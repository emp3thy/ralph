"""Tests for the ``ralph-promote`` skill's entry script.

``ralph-promote`` bumps a PBI's ``severity`` frontmatter field. The PBI
may live in any state folder under ``.ralph/`` -- promote is purely a
metadata update, not a state change. The skill commits and pushes to
``ralph-queue``.

Tests use a local bare repo + working clone in ``tmp_path`` and never
touch the network.
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
SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-promote" / "scripts" / "promote.py"


@pytest.fixture(scope="module")
def promote_module() -> ModuleType:
    assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("ralph_promote_script", SCRIPT_PATH)
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


def _seed_pbi(
    repo: Path,
    state_folder: str,
    pbi_id: str,
    pbi_type: str = "feature",
    severity: str = "normal",
) -> Path:
    _git(repo, "checkout", "ralph-queue")
    pbi_dir = repo / ".ralph" / state_folder / pbi_id
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
        f"status: {state_folder}\n"
        f"severity: {severity}\n"
        "attempts: 2\n"
        'created_at: "2026-05-20T08:00:00+00:00"\n'
        'updated_at: "2026-05-22T09:30:00+00:00"\n'
        "---\n"
        "\n"
        "# body text\n"
        "\n"
        "paragraph two\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _git(repo, "add", f".ralph/{state_folder}/{pbi_id}")
    _git(repo, "commit", "-m", f"chore(test): seed {pbi_id}")
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


def test_promote_feature_pbi_in_inbox(
    git_repo: Path,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pbi(git_repo, "inbox", "WI-100", pbi_type="feature", severity="normal")

    exit_code = promote_module.main(
        [
            "--pbi-id",
            "WI-100",
            "--severity",
            "high",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-100"
    assert payload["previous_severity"] == "normal"
    assert payload["new_severity"] == "high"
    assert payload["state_folder"] == "inbox"
    assert payload["pushed"] is True
    assert payload["commit_sha"]

    _git(git_repo, "checkout", "ralph-queue")
    entry = git_repo / ".ralph" / "inbox" / "WI-100" / "PBI.md"
    fm = _read_frontmatter(entry)
    assert fm["severity"] == "high"
    assert fm["id"] == "WI-100"
    assert fm["type"] == "feature"
    assert fm["attempts"] == 2
    assert fm["created_at"] == "2026-05-20T08:00:00+00:00"
    assert fm["updated_at"] != "2026-05-22T09:30:00+00:00"

    latest = _git(git_repo, "log", "-1", "--pretty=%s").strip()
    assert latest == "chore(queue): promote WI-100 (normal -> high)"


def test_promote_bug_pbi_uses_bug_md(
    git_repo: Path,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pbi(git_repo, "blocked", "BUG-X", pbi_type="bug", severity="high")

    exit_code = promote_module.main(
        [
            "--pbi-id",
            "BUG-X",
            "--severity",
            "critical",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 0

    _git(git_repo, "checkout", "ralph-queue")
    entry = git_repo / ".ralph" / "blocked" / "BUG-X" / "BUG.md"
    fm = _read_frontmatter(entry)
    assert fm["severity"] == "critical"
    assert fm["type"] == "bug"


def test_promote_rejects_unknown_severity(
    git_repo: Path,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pbi(git_repo, "inbox", "WI-200", severity="normal")

    with pytest.raises(SystemExit) as exc:
        promote_module.main(
            [
                "--pbi-id",
                "WI-200",
                "--severity",
                "urgent",
                "--repo",
                str(git_repo),
            ]
        )
    assert exc.value.code == 2


def test_promote_errors_on_missing_pbi(
    git_repo: Path,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = promote_module.main(
        [
            "--pbi-id",
            "WI-NOPE",
            "--severity",
            "high",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "not found" in stderr or "no such" in stderr or "missing" in stderr


def test_promote_preserves_body_content(
    git_repo: Path,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pbi(git_repo, "inbox", "WI-300", severity="low")

    exit_code = promote_module.main(
        [
            "--pbi-id",
            "WI-300",
            "--severity",
            "normal",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 0

    _git(git_repo, "checkout", "ralph-queue")
    text = (git_repo / ".ralph" / "inbox" / "WI-300" / "PBI.md").read_text(encoding="utf-8")
    assert "# body text" in text
    assert "paragraph two" in text
    assert text.count("---") == 2


def test_promote_no_push_leaves_remote_unchanged(
    git_repo: Path,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pbi(git_repo, "inbox", "WI-400", severity="normal")
    before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

    exit_code = promote_module.main(
        [
            "--pbi-id",
            "WI-400",
            "--severity",
            "high",
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


def test_promote_dry_run_writes_nothing(
    git_repo: Path,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pbi(git_repo, "inbox", "WI-500", severity="normal")
    before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

    exit_code = promote_module.main(
        [
            "--pbi-id",
            "WI-500",
            "--severity",
            "critical",
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
    fm = _read_frontmatter(git_repo / ".ralph" / "inbox" / "WI-500" / "PBI.md")
    assert fm["severity"] == "normal"
    after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
    assert before == after


def test_promote_records_history_entry(
    git_repo: Path,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pbi(git_repo, "inbox", "WI-600", severity="normal")

    exit_code = promote_module.main(
        [
            "--pbi-id",
            "WI-600",
            "--severity",
            "critical",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 0

    _git(git_repo, "checkout", "ralph-queue")
    history = (git_repo / ".ralph" / "inbox" / "WI-600" / "HISTORY.md").read_text(encoding="utf-8")
    assert "ralph-promote" in history
    assert "normal -> critical" in history


def test_promote_runs_full_path_when_severity_on_disk_but_not_committed(
    git_repo: Path,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: BugBot PR #35 flagged that the idempotency check
    compared the working-tree severity, so a previously-failed promote
    whose ``write_frontmatter`` succeeded but whose ``git commit`` failed
    left the new severity on disk while HEAD still had the old one. The
    next invocation read ``previous_severity == args.severity`` and
    exited 0 with ``previous_severity == new_severity`` — never landing
    the change on the queue branch. Fix reads HEAD's frontmatter via
    ``read_path_from_head``; this test seeds the bad state and asserts
    the next invocation commits + pushes."""
    pbi_dir = _seed_pbi(git_repo, "inbox", "WI-650", pbi_type="feature", severity="normal")

    _git(git_repo, "checkout", "ralph-queue")
    # Simulate previously-failed promote: on-disk frontmatter AND
    # HISTORY.md have been updated as the prior attempt got that far,
    # but the commit step never ran. Leave the working tree on
    # ralph-queue with the dirty files in place — the script's
    # checkout_queue_branch is a no-op when already there and then
    # re-runs the commit + push path.
    entry = pbi_dir / "PBI.md"
    text = entry.read_text(encoding="utf-8")
    text = text.replace("severity: normal", "severity: high")
    entry.write_text(text, encoding="utf-8")
    # Pre-existing HISTORY entry from the failed attempt — round-3 fix
    # must not append a duplicate.
    history_path = pbi_dir / "HISTORY.md"
    history_path.write_text(
        "## 2026-05-27T10:00:00+00:00 ralph-promote: promote\nseverity: normal -> high\n",
        encoding="utf-8",
    )
    head_before = _git(git_repo, "rev-parse", "ralph-queue")

    exit_code = promote_module.main(
        [
            "--pbi-id",
            "WI-650",
            "--severity",
            "high",
            "--repo",
            str(git_repo),
            "--no-push",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["new_severity"] == "high"
    assert payload["commit_sha"] != "", (
        "must re-run the full commit path when HEAD's severity is "
        "unchanged, even if the working tree already has the new value"
    )
    head_after = _git(git_repo, "rev-parse", "ralph-queue")
    assert head_after != head_before, "a new commit must land on ralph-queue"
    # BugBot round-2 (PR #35): previous_severity for audit must come
    # from HEAD, not from the dirty working tree. The retry case would
    # otherwise log "high -> high" in HISTORY + the commit subject.
    assert payload["previous_severity"] == "normal", (
        "previous_severity must reflect HEAD's committed value, not the "
        "working tree value (which was already 'high' from the failed run)"
    )
    history = (git_repo / ".ralph" / "inbox" / "WI-650" / "HISTORY.md").read_text(encoding="utf-8")
    assert "normal -> high" in history
    assert "high -> high" not in history
    # BugBot round-3 (PR #35): must NOT duplicate the HISTORY entry on
    # retry. The seed wrote one entry; the script must skip the second
    # append since the working tree already had the new severity.
    assert history.count("severity: normal -> high") == 1, (
        f"HISTORY must not gain a duplicate entry on retry; got:\n{history}"
    )
    subject = _git(git_repo, "log", "-1", "--pretty=%s", "ralph-queue").strip()
    assert "(normal -> high)" in subject, f"commit subject wrong: {subject}"
