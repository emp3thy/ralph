"""Tests for the ``ralph-cancel`` skill's entry script.

``ralph-cancel`` is the only mutation we allow on ``.ralph/current/``:
it drops an empty ``CANCEL`` sentinel file inside the directory of the
PBI Ralph is actively working on, then commits + pushes to
``ralph-queue``. Ralph notices the sentinel on its next iteration and
abandons the PBI.

The script lives at ``skills/ralph-cancel/scripts/cancel.py``. Tests
build a fake ``ralph-queue`` clone in ``tmp_path`` and verify each
behaviour without any network or real ADO.
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

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-cancel" / "scripts" / "cancel.py"


@pytest.fixture(scope="module")
def cancel_module() -> ModuleType:
    assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("ralph_cancel_script", SCRIPT_PATH)
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


def _seed_pbi(
    repo: Path,
    state_folder: str,
    pbi_id: str,
    entry_file: str = "PBI.md",
) -> Path:
    """Create a PBI directory on the ralph-queue branch and commit it."""
    _git(repo, "checkout", "ralph-queue")
    pbi_dir = repo / ".ralph" / state_folder / pbi_id
    pbi_dir.mkdir(parents=True)
    (pbi_dir / entry_file).write_text(
        "---\n"
        f"id: {pbi_id}\n"
        "type: feature\n"
        f"status: {state_folder}\n"
        "severity: normal\n"
        "attempts: 0\n"
        "created_at: 2026-05-24T10:00:00+00:00\n"
        "updated_at: 2026-05-24T10:00:00+00:00\n"
        "---\n"
        "\n"
        "# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _git(repo, "add", f".ralph/{state_folder}/{pbi_id}")
    _git(repo, "commit", "-m", f"chore(test): seed {pbi_id} in {state_folder}")
    _git(repo, "push", "origin", "ralph-queue")
    _git(repo, "checkout", "main")
    return pbi_dir


def test_cancel_drops_sentinel_in_current(
    git_repo: Path,
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pbi(git_repo, "current", "WI-1234")

    exit_code = cancel_module.main(
        [
            "--pbi-id",
            "WI-1234",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-1234"
    assert payload["sentinel_path"].endswith("WI-1234/CANCEL")
    assert payload["pushed"] is True
    assert payload["commit_sha"]

    _git(git_repo, "checkout", "ralph-queue")
    cancel_file = git_repo / ".ralph" / "current" / "WI-1234" / "CANCEL"
    assert cancel_file.is_file()
    assert cancel_file.read_bytes() == b""

    latest = _git(git_repo, "log", "-1", "--pretty=%s").strip()
    assert latest == "chore(queue): cancel WI-1234"


def test_cancel_refuses_pbi_outside_current(
    git_repo: Path,
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pbi(git_repo, "inbox", "WI-2000")

    exit_code = cancel_module.main(
        [
            "--pbi-id",
            "WI-2000",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "current" in stderr


def test_cancel_errors_on_missing_pbi(
    git_repo: Path,
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cancel_module.main(
        [
            "--pbi-id",
            "WI-DOES-NOT-EXIST",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "not found" in stderr or "no such" in stderr or "missing" in stderr


def test_cancel_no_push_keeps_remote_at_previous_sha(
    git_repo: Path,
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pbi(git_repo, "current", "WI-3000")
    before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

    exit_code = cancel_module.main(
        [
            "--pbi-id",
            "WI-3000",
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


def test_cancel_dry_run_writes_nothing(
    git_repo: Path,
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pbi(git_repo, "current", "WI-4000")
    before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

    exit_code = cancel_module.main(
        [
            "--pbi-id",
            "WI-4000",
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
    cancel_file = git_repo / ".ralph" / "current" / "WI-4000" / "CANCEL"
    assert not cancel_file.exists()
    after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
    assert before == after


def test_cancel_idempotent_when_sentinel_already_present(
    git_repo: Path,
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pbi_dir = _seed_pbi(git_repo, "current", "WI-5000")
    _git(git_repo, "checkout", "ralph-queue")
    (pbi_dir / "CANCEL").write_text("", encoding="utf-8")
    _git(git_repo, "add", ".ralph/current/WI-5000/CANCEL")
    _git(git_repo, "commit", "-m", "chore(test): pre-existing cancel")
    _git(git_repo, "push", "origin", "ralph-queue")
    _git(git_repo, "checkout", "main")

    exit_code = cancel_module.main(
        [
            "--pbi-id",
            "WI-5000",
            "--repo",
            str(git_repo),
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["already_cancelled"] is True
    assert payload["commit_sha"] == ""
    assert payload["pushed"] is False


def test_cancel_runs_full_path_when_sentinel_on_disk_but_not_committed(
    git_repo: Path,
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: BugBot PR #35 flagged that the idempotency check used
    ``Path.exists()`` which fires for a sentinel that was staged but
    never committed (pre-commit hook reject, missing user.email, etc.).
    The script then exited 0 with ``already_cancelled=True`` even though
    HEAD was unchanged — ralph would never see the cancel. Fix routes
    the check through ``git cat-file -e HEAD:<path>``; this test seeds
    the bad-state (sentinel on disk + staged but NOT committed) and
    asserts the next invocation re-runs the full commit + push path."""
    pbi_dir = _seed_pbi(git_repo, "current", "WI-5050")
    _git(git_repo, "checkout", "ralph-queue")
    # Stage but do NOT commit — simulates a previously-failed cancel
    # whose `git add` succeeded but whose `git commit` hook rejected
    # the change.
    (pbi_dir / "CANCEL").write_text("", encoding="utf-8")
    _git(git_repo, "add", ".ralph/current/WI-5050/CANCEL")
    _git(git_repo, "checkout", "main")

    head_before = _git(git_repo, "rev-parse", "ralph-queue")

    exit_code = cancel_module.main(
        [
            "--pbi-id",
            "WI-5050",
            "--repo",
            str(git_repo),
            "--no-push",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["already_cancelled"] is False, (
        "must re-run the full path when the sentinel is not in HEAD, "
        "even if it exists on the filesystem"
    )
    assert payload["commit_sha"] != ""
    head_after = _git(git_repo, "rev-parse", "ralph-queue")
    assert head_after != head_before, "a new commit must land on ralph-queue"


def test_cancel_repo_not_a_repo_errors(
    tmp_path: Path,
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    exit_code = cancel_module.main(
        [
            "--pbi-id",
            "WI-1",
            "--repo",
            str(not_a_repo),
        ]
    )
    assert exit_code == 2
    assert "not a git repository" in capsys.readouterr().err.lower()
