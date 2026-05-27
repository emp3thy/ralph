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
