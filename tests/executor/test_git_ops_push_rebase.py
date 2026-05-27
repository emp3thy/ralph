"""Tests for ``git_ops.push_with_rebase`` and ``PushRebaseConflict``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph_executor import git_ops
from ralph_executor.git_ops import (
    GitCommandError,
    PushRebaseConflict,
    push_with_rebase,
)


@pytest.fixture
def two_repos(tmp_path: Path) -> tuple[Path, Path]:
    """Return (local, remote_bare) — both initialised, remote tracked by local."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    local = tmp_path / "local"
    subprocess.run(["git", "clone", str(remote), str(local)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(local), "config", "user.email", "t@x"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    (local / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(local), "add", "README"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "commit", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "push", "-u", "origin", "HEAD:main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "checkout", "-B", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "branch", "--set-upstream-to=origin/main"],
        check=True,
        capture_output=True,
    )
    return local, remote


def _second_clone(tmp_path: Path, remote: Path, name: str = "second") -> Path:
    """Clone ``remote`` into a second working tree configured for commits."""
    second = tmp_path / name
    subprocess.run(["git", "clone", str(remote), str(second)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(second), "config", "user.email", "t2@x"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(second), "config", "user.name", "t2"],
        check=True,
        capture_output=True,
    )
    return second


def _add_commit(repo: Path, path: str, content: str, msg: str) -> None:
    (repo / path).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", path], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", msg], check=True, capture_output=True)


def _remote_head(remote: Path, branch: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", f"refs/heads/{branch}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def test_push_with_rebase_no_remote_advance(two_repos: tuple[Path, Path]) -> None:
    local, remote = two_repos
    _add_commit(local, "a.txt", "a", "add a")
    local_head = subprocess.run(
        ["git", "-C", str(local), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    push_with_rebase(local, remote="origin", branch="main")
    assert _remote_head(remote, "main") == local_head


def test_push_with_rebase_remote_advanced_non_conflicting(
    two_repos: tuple[Path, Path], tmp_path: Path
) -> None:
    local, remote = two_repos
    second = _second_clone(tmp_path, remote)
    _add_commit(second, "remote.txt", "remote", "remote commit")
    subprocess.run(
        ["git", "-C", str(second), "push", "origin", "main"],
        check=True,
        capture_output=True,
    )
    _add_commit(local, "local.txt", "local", "local commit")
    push_with_rebase(local, remote="origin", branch="main")
    # The local commit was rebased on top of the remote one and pushed.
    log = subprocess.run(
        ["git", "-C", str(local), "log", "--format=%s", "-3"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert log[0] == "local commit"
    assert log[1] == "remote commit"
    local_head = subprocess.run(
        ["git", "-C", str(local), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert _remote_head(remote, "main") == local_head


def test_push_with_rebase_conflict_raises(two_repos: tuple[Path, Path], tmp_path: Path) -> None:
    local, remote = two_repos
    second = _second_clone(tmp_path, remote)
    _add_commit(second, "conflict.txt", "from-remote\n", "remote conflict")
    subprocess.run(
        ["git", "-C", str(second), "push", "origin", "main"],
        check=True,
        capture_output=True,
    )
    _add_commit(local, "conflict.txt", "from-local\n", "local conflict")
    pre_head = subprocess.run(
        ["git", "-C", str(local), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with pytest.raises(PushRebaseConflict) as exc:
        push_with_rebase(local, remote="origin", branch="main")
    assert "conflict.txt" in str(exc.value)
    assert "conflict.txt" in exc.value.conflict_paths
    # Rebase was aborted: HEAD restored, no in-progress rebase directory.
    post_head = subprocess.run(
        ["git", "-C", str(local), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert post_head == pre_head
    assert not (local / ".git" / "rebase-merge").exists()
    assert not (local / ".git" / "rebase-apply").exists()


def test_push_with_rebase_network_failure(tmp_path: Path) -> None:
    local = tmp_path / "lonely"
    subprocess.run(["git", "init", str(local)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(local), "config", "user.email", "t@x"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    (local / "x").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(local), "add", "x"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(local), "commit", "-m", "x"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "remote", "add", "origin", str(tmp_path / "nonexistent")],
        check=True,
        capture_output=True,
    )
    with pytest.raises(GitCommandError):
        push_with_rebase(local, remote="origin", branch="main")


def test_push_with_rebase_no_local_commits_is_noop(two_repos: tuple[Path, Path]) -> None:
    local, remote = two_repos
    before_remote = _remote_head(remote, "main")
    push_with_rebase(local, remote="origin", branch="main")
    assert _remote_head(remote, "main") == before_remote


def test_push_with_rebase_one_retry_on_race(
    two_repos: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the push fast-forward races between rebase and push, retry once.

    Simulate by intercepting the first ``push`` call: advance the remote
    via a sibling clone, then propagate the original push (which now
    fails), so the helper takes its one retry path and succeeds.
    """
    local, remote = two_repos
    _add_commit(local, "local.txt", "local", "local commit")

    real_run = git_ops._run_git
    state = {"push_attempt": 0}

    def racing_run(
        repo: Path,
        *args: str,
        check: bool = True,
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "push" and repo == local:
            state["push_attempt"] += 1
            if state["push_attempt"] == 1:
                # Advance the remote BEFORE the first push runs so the
                # push is rejected as non-FF, exercising the retry path.
                second = _second_clone(tmp_path, remote, name="racer")
                _add_commit(second, "racer.txt", "racer", "racer commit")
                subprocess.run(
                    ["git", "-C", str(second), "push", "origin", "main"],
                    check=True,
                    capture_output=True,
                )
        return real_run(repo, *args, check=check, capture=capture)

    monkeypatch.setattr(git_ops, "_run_git", racing_run)
    push_with_rebase(local, remote="origin", branch="main")
    assert state["push_attempt"] == 2
    # Both racer + local commits are on remote.
    log_remote = subprocess.run(
        ["git", "-C", str(local), "log", "--format=%s", "origin/main", "-3"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert "local commit" in log_remote
    assert "racer commit" in log_remote
