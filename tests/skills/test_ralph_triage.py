"""Tests for the ``ralph-triage`` skill's entry script.

``ralph-triage`` walks the ``.ralph/blocked/`` queue. For each PBI the
human decides whether to:

- return it to ``.ralph/inbox/`` (with ``attempts`` reset to 0 and a
  triage note appended to HISTORY.md), or
- close it out by moving it to ``.ralph/archive/`` (the archive folder
  is created on demand) with a final HISTORY.md entry.

Tests cover both destinations, the validation of ``--to``, and the
resilience to missing inputs.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-triage" / "scripts" / "triage.py"


@pytest.fixture(scope="module")
def triage_module() -> ModuleType:
    assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("ralph_triage_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Iterator[Path]:
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", str(work)], check=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test User")
    _git(work, "commit", "--allow-empty", "-m", "chore: initial main commit")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")
    _git(work, "checkout", "-b", "ralph-queue")
    _git(work, "commit", "--allow-empty", "-m", "chore(queue): bootstrap")
    _git(work, "push", "-u", "origin", "ralph-queue")
    _git(work, "checkout", "main")
    yield work


def _seed_blocked_pbi(
    repo: Path,
    pbi_id: str,
    pbi_type: str = "feature",
    attempts: int = 3,
    with_stuck: bool = True,
) -> Path:
    _git(repo, "checkout", "ralph-queue")
    pbi_dir = repo / ".ralph" / "blocked" / pbi_id
    pbi_dir.mkdir(parents=True)
    entry_name = {
        "feature": "PBI.md",
        "bug": "BUG.md",
        "pr-feedback": "FEEDBACK.md",
    }[pbi_type]
    (pbi_dir / entry_name).write_text(
        "---\n"
        f"id: {pbi_id}\n"
        f"type: {pbi_type}\n"
        "status: blocked\n"
        "severity: normal\n"
        f"attempts: {attempts}\n"
        'created_at: "2026-05-18T08:00:00+00:00"\n'
        'updated_at: "2026-05-22T09:30:00+00:00"\n'
        "---\n"
        "\n"
        "# body text\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text(
        "- timestamp: 2026-05-22T09:30:00+00:00\n"
        "- actor: ralph-executor\n"
        "- action: blocked\n"
        "- detail: attempt 3 exhausted\n",
        encoding="utf-8",
    )
    if with_stuck:
        (pbi_dir / "STUCK.md").write_text(
            "# Stuck\n\n"
            "what I tried: rebuilt the index three times.\n"
            "what would unblock me: a working repro of the failure.\n",
            encoding="utf-8",
        )
    _git(repo, "add", f".ralph/blocked/{pbi_id}")
    _git(repo, "commit", "-m", f"chore(test): seed blocked {pbi_id}")
    _git(repo, "push", "origin", "ralph-queue")
    _git(repo, "checkout", "main")
    return pbi_dir


def _read_frontmatter(entry_file: Path) -> dict[str, object]:
    text = entry_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "---"
    end = next(i for i, line in enumerate(lines[1:], start=1) if line == "---")
    result = yaml.safe_load("\n".join(lines[1:end]))
    assert isinstance(result, dict)
    return result


def test_triage_to_inbox_resets_attempts_and_moves(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_blocked_pbi(git_repo, "WI-700", attempts=3)

    exit_code = triage_module.main(
        [
            "--pbi-id",
            "WI-700",
            "--to",
            "inbox",
            "--note",
            "tried a fresh REPRODUCE.md from QA; reset and retry",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-700"
    assert payload["destination"] == "inbox"
    assert payload["previous_state_folder"] == "blocked"
    assert payload["pushed"] is True
    assert payload["commit_sha"]

    _git(git_repo, "checkout", "ralph-queue")
    old_path = git_repo / ".ralph" / "blocked" / "WI-700"
    new_path = git_repo / ".ralph" / "inbox" / "WI-700"
    assert not old_path.exists(), "blocked/ directory should be gone"
    assert new_path.is_dir()

    fm = _read_frontmatter(new_path / "PBI.md")
    assert fm["attempts"] == 0
    assert fm["status"] == "inbox"
    assert fm["updated_at"] != "2026-05-22T09:30:00+00:00"

    history = (new_path / "HISTORY.md").read_text(encoding="utf-8")
    assert "ralph-triage" in history
    assert "return-to-inbox" in history
    assert "tried a fresh REPRODUCE.md from QA" in history

    latest = _git(git_repo, "log", "-1", "--pretty=%s").strip()
    assert latest == "chore(queue): triage WI-700 (blocked -> inbox)"


def test_triage_to_archive_creates_archive_folder(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_blocked_pbi(git_repo, "WI-800", attempts=4)

    _git(git_repo, "checkout", "ralph-queue")
    assert not (git_repo / ".ralph" / "archive").exists()
    _git(git_repo, "checkout", "main")

    exit_code = triage_module.main(
        [
            "--pbi-id",
            "WI-800",
            "--to",
            "archive",
            "--note",
            "ambiguous scope; closing out, BA will re-submit if needed",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["destination"] == "archive"
    assert payload["archive_created"] is True

    _git(git_repo, "checkout", "ralph-queue")
    assert (git_repo / ".ralph" / "archive").is_dir()
    old_path = git_repo / ".ralph" / "blocked" / "WI-800"
    new_path = git_repo / ".ralph" / "archive" / "WI-800"
    assert not old_path.exists()
    assert new_path.is_dir()

    fm = _read_frontmatter(new_path / "PBI.md")
    assert fm["status"] == "archive"
    assert fm["attempts"] == 4

    history = (new_path / "HISTORY.md").read_text(encoding="utf-8")
    assert "ralph-triage" in history
    assert "archive" in history
    assert "ambiguous scope" in history


def test_triage_rejects_unknown_destination(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_blocked_pbi(git_repo, "WI-900")

    with pytest.raises(SystemExit) as exc:
        triage_module.main(
            [
                "--pbi-id",
                "WI-900",
                "--to",
                "trash",
                "--note",
                "no",
                "--repo",
                str(git_repo),
            ]
        )
    assert exc.value.code == 2


def test_triage_errors_on_missing_pbi(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = triage_module.main(
        [
            "--pbi-id",
            "WI-NOPE",
            "--to",
            "inbox",
            "--note",
            "missing",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "not found" in stderr or "no such" in stderr or "blocked" in stderr


def test_triage_rejects_pbi_outside_blocked(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _git(git_repo, "checkout", "ralph-queue")
    pbi_dir = git_repo / ".ralph" / "inbox" / "WI-1000"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        "id: WI-1000\n"
        "type: feature\n"
        "status: inbox\n"
        "severity: normal\n"
        "attempts: 0\n"
        'created_at: "2026-05-24T10:00:00+00:00"\n'
        'updated_at: "2026-05-24T10:00:00+00:00"\n'
        "---\n"
        "\n"
        "# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _git(git_repo, "add", ".ralph/inbox/WI-1000")
    _git(git_repo, "commit", "-m", "chore(test): seed inbox WI-1000")
    _git(git_repo, "push", "origin", "ralph-queue")
    _git(git_repo, "checkout", "main")

    exit_code = triage_module.main(
        [
            "--pbi-id",
            "WI-1000",
            "--to",
            "inbox",
            "--note",
            "won't work",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "blocked" in stderr


def test_triage_requires_note(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_blocked_pbi(git_repo, "WI-1100")

    with pytest.raises(SystemExit) as exc:
        triage_module.main(
            [
                "--pbi-id",
                "WI-1100",
                "--to",
                "inbox",
                "--repo",
                str(git_repo),
            ]
        )
    assert exc.value.code == 2


def test_triage_dry_run_leaves_files_in_place(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_blocked_pbi(git_repo, "WI-1200")
    before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

    exit_code = triage_module.main(
        [
            "--pbi-id",
            "WI-1200",
            "--to",
            "inbox",
            "--note",
            "would-be note",
            "--repo",
            str(git_repo),
            "--dry-run",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["pushed"] is False
    assert payload["commit_sha"] == ""

    _git(git_repo, "checkout", "ralph-queue")
    assert (git_repo / ".ralph" / "blocked" / "WI-1200").is_dir()
    assert not (git_repo / ".ralph" / "inbox" / "WI-1200").exists()
    after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
    assert before == after


def test_triage_no_push_keeps_remote_unchanged(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_blocked_pbi(git_repo, "WI-1300")
    before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

    exit_code = triage_module.main(
        [
            "--pbi-id",
            "WI-1300",
            "--to",
            "archive",
            "--note",
            "closing out",
            "--repo",
            str(git_repo),
            "--no-push",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pushed"] is False
    assert payload["commit_sha"]
    after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
    assert before == after


def test_triage_runs_full_path_when_moved_on_disk_but_not_committed(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: BugBot PR #35 flagged that triage had no idempotency
    for the move + commit pair. If ``_move_directory`` succeeded but
    ``commit_paths`` failed, retry could not find the PBI in
    ``blocked/`` (moved to ``inbox/``) and raised "PBI is in
    .ralph/inbox/, not in .ralph/blocked/". Fix detects the
    HEAD-still-blocked + working-tree-already-moved state and short-
    circuits to ``commit_paths`` only. Also asserts HISTORY.md gains
    no duplicate entry."""
    _seed_blocked_pbi(git_repo, "WI-1400")

    # Simulate previously-failed triage: working tree already shows the
    # PBI in inbox/ with updated frontmatter + a single HISTORY entry,
    # but HEAD still has it in blocked/.
    _git(git_repo, "checkout", "ralph-queue")
    src = git_repo / ".ralph" / "blocked" / "WI-1400"
    dst = git_repo / ".ralph" / "inbox" / "WI-1400"
    dst.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.move(str(src), str(dst))
    pbi_md = dst / "PBI.md"
    text = pbi_md.read_text(encoding="utf-8")
    text = text.replace("status: blocked", "status: inbox")
    text = text.replace("attempts: 3", "attempts: 0")
    pbi_md.write_text(text, encoding="utf-8")
    history_path = dst / "HISTORY.md"
    history_path.write_text(
        history_path.read_text(encoding="utf-8")
        + "## 2026-05-27T10:00:00+00:00 ralph-triage: return-to-inbox\n"
        "reseed for retry\n",
        encoding="utf-8",
    )
    head_before = _git(git_repo, "rev-parse", "ralph-queue")

    exit_code = triage_module.main(
        [
            "--pbi-id",
            "WI-1400",
            "--to",
            "inbox",
            "--note",
            "reseed for retry",
            "--repo",
            str(git_repo),
            "--no-push",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["commit_sha"] != "", (
        "must re-run commit_paths when the move already landed on disk "
        "but HEAD still shows the PBI under blocked/"
    )
    head_after = _git(git_repo, "rev-parse", "ralph-queue")
    assert head_after != head_before, "a new commit must land on ralph-queue"
    history = (git_repo / ".ralph" / "inbox" / "WI-1400" / "HISTORY.md").read_text(encoding="utf-8")
    assert history.count("return-to-inbox") == 1, (
        f"HISTORY must not gain a duplicate entry on retry; got:\n{history}"
    )


def test_triage_records_audit_when_note_text_was_used_in_earlier_cycle(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: BugBot PR #35 round-5 flagged that scoping the HISTORY
    dedup to the entire file suppresses legitimate audit entries when the
    operator reuses note text in a later blocked→inbox cycle. The dedup
    must only fire during the partial-failure retry window."""
    note = "reset and retry the build"
    _seed_blocked_pbi(git_repo, "WI-1500")

    # Cycle 1: blocked → inbox with the note.
    rc = triage_module.main(
        [
            "--pbi-id",
            "WI-1500",
            "--to",
            "inbox",
            "--note",
            note,
            "--repo",
            str(git_repo),
            "--no-push",
        ]
    )
    assert rc == 0
    capsys.readouterr()

    # Re-block the PBI on ralph-queue (simulate a later failure that
    # moved it back to blocked/) so we can triage it again with the same
    # note.
    _git(git_repo, "checkout", "ralph-queue")
    import shutil

    src = git_repo / ".ralph" / "inbox" / "WI-1500"
    dst = git_repo / ".ralph" / "blocked" / "WI-1500"
    shutil.move(str(src), str(dst))
    # Update frontmatter back to blocked status.
    entry = dst / "PBI.md"
    text = entry.read_text(encoding="utf-8")
    text = text.replace("status: inbox", "status: blocked")
    text = text.replace("attempts: 0", "attempts: 3")
    entry.write_text(text, encoding="utf-8")
    _git(git_repo, "add", ".ralph")
    _git(git_repo, "commit", "-m", "chore(test): re-block WI-1500")
    _git(git_repo, "push", "origin", "ralph-queue")
    _git(git_repo, "checkout", "main")

    # Cycle 2: blocked → inbox with the SAME note. Must produce a fresh
    # HISTORY entry even though the note text matches the cycle-1 entry.
    rc = triage_module.main(
        [
            "--pbi-id",
            "WI-1500",
            "--to",
            "inbox",
            "--note",
            note,
            "--repo",
            str(git_repo),
            "--no-push",
        ]
    )
    assert rc == 0

    history = (git_repo / ".ralph" / "inbox" / "WI-1500" / "HISTORY.md").read_text(encoding="utf-8")
    assert history.count(note) == 2, (
        f"cycle 2 must record a fresh audit entry even though the note "
        f"text was reused from cycle 1; got:\n{history}"
    )


def test_triage_archive_created_true_on_partial_failure_retry(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: BugBot PR #35 round-7 (LOW). Previous round used
    ``(repo / '.ralph/archive').is_dir()`` for ``archive_existed_before``.
    In the partial-failure retry case the previous incomplete run
    already created ``.ralph/archive/`` on disk via ``_move_directory``
    but never committed it. The retry then reported
    ``archive_created=False`` even though THIS commit is the first time
    the archive dir lands on HEAD. Fix uses ``is_path_in_head`` instead."""
    _seed_blocked_pbi(git_repo, "WI-1600")

    # Simulate the partial-failure retry shape: working tree already has
    # the PBI in .ralph/archive/<id>/ (move happened) and the new
    # archive directory exists on disk; HEAD still has it in blocked/.
    _git(git_repo, "checkout", "ralph-queue")
    import shutil

    src = git_repo / ".ralph" / "blocked" / "WI-1600"
    dst = git_repo / ".ralph" / "archive" / "WI-1600"
    shutil.move(str(src), str(dst))
    entry = dst / "PBI.md"
    text = entry.read_text(encoding="utf-8")
    text = text.replace("status: blocked", "status: archive")
    entry.write_text(text, encoding="utf-8")
    history_path = dst / "HISTORY.md"
    history_path.write_text(
        history_path.read_text(encoding="utf-8")
        + "## ralph-triage: archive\n"
        + "ambiguous scope, archiving\n",
        encoding="utf-8",
    )

    exit_code = triage_module.main(
        [
            "--pbi-id",
            "WI-1600",
            "--to",
            "archive",
            "--note",
            "ambiguous scope, archiving",
            "--repo",
            str(git_repo),
            "--no-push",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["archive_created"] is True, (
        "archive_created must be True when THIS commit first lands "
        ".ralph/archive/ in HEAD, even if the dir already exists on "
        "disk from a prior incomplete attempt"
    )


def test_triage_dedup_handles_multi_line_note(
    git_repo: Path,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: BugBot PR #35 round-7. ``append_history`` flattens
    embedded newlines via ``flatten_history_field``, but the dedup
    check compared the raw ``args.note`` against the flattened stored
    text — counts always 0, dedup never fires, partial-failure retry
    duplicated the entry. Fix flattens both sides."""
    note = "first line\nsecond line"
    flat = "first line second line"
    _seed_blocked_pbi(git_repo, "WI-1700")

    # Simulate partial-failure retry pre-move: working tree shows the
    # PBI moved + frontmatter updated + HISTORY already carrying the
    # FLATTENED entry from the prior attempt, HEAD still in blocked.
    _git(git_repo, "checkout", "ralph-queue")
    import shutil

    src = git_repo / ".ralph" / "blocked" / "WI-1700"
    dst = git_repo / ".ralph" / "inbox" / "WI-1700"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    pbi_md = dst / "PBI.md"
    text = pbi_md.read_text(encoding="utf-8")
    text = text.replace("status: blocked", "status: inbox")
    text = text.replace("attempts: 3", "attempts: 0")
    pbi_md.write_text(text, encoding="utf-8")
    history_path = dst / "HISTORY.md"
    history_path.write_text(
        history_path.read_text(encoding="utf-8") + f"## ralph-triage: return-to-inbox\n{flat}\n",
        encoding="utf-8",
    )

    rc = triage_module.main(
        [
            "--pbi-id",
            "WI-1700",
            "--to",
            "inbox",
            "--note",
            note,
            "--repo",
            str(git_repo),
            "--no-push",
        ]
    )
    assert rc == 0
    history = (git_repo / ".ralph" / "inbox" / "WI-1700" / "HISTORY.md").read_text(encoding="utf-8")
    assert history.count(flat) == 1, f"dedup must compare flattened forms; got:\n{history}"
