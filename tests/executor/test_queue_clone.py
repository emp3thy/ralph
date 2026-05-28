"""Tests for queue_clone.ensure_queue_clone using local bare git repos."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph_executor.queue_clone import QueueCloneError, ensure_queue_clone


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_bare_remote(tmp_path: Path) -> Path:
    """Build a bare repo with an initial commit on main, return its path."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "--initial-branch=main")

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    (seed / "README.md").write_text("queue\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "origin", "main")
    return bare


def test_first_call_clones(tmp_path: Path) -> None:
    remote = _make_bare_remote(tmp_path)
    workspace = tmp_path / "ws"

    path = ensure_queue_clone(workspace, f"file://{remote.as_posix()}")

    assert path == workspace / "queue"
    assert (path / ".git").exists()
    assert (path / "README.md").read_text(encoding="utf-8") == "queue\n"


def test_second_call_fetches_and_pulls(tmp_path: Path) -> None:
    remote = _make_bare_remote(tmp_path)
    workspace = tmp_path / "ws"

    path = ensure_queue_clone(workspace, f"file://{remote.as_posix()}")

    push_src = tmp_path / "push_src"
    subprocess.run(
        ["git", "clone", str(remote), str(push_src)],
        check=True,
        capture_output=True,
    )
    (push_src / "new.md").write_text("new\n", encoding="utf-8")
    _git(push_src, "add", ".")
    _git(push_src, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "second")
    _git(push_src, "push", "origin", "main")

    ensure_queue_clone(workspace, f"file://{remote.as_posix()}")
    assert (path / "new.md").exists()


def test_bad_url_raises_queue_clone_error(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    with pytest.raises(QueueCloneError) as exc:
        ensure_queue_clone(workspace, "file:///definitely/not/a/repo")
    assert "queue" in str(exc.value).lower()
