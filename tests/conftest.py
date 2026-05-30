"""Project-wide pytest fixtures."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str) -> str:
    """Run git with stable Windows-safe encoding; return stdout."""
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
def queue_env(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """Bare queue remote on ``ralph-queue`` + empty workspace dir.

    Returns ``(workspace_root, queue_repo_url)`` for the test to pass into
    ralph-new via ``--workspace`` / ``--queue-repo``.
    """
    bare = tmp_path / "queue.git"
    seed = tmp_path / "queue-seed"
    workspace = tmp_path / "ws"
    subprocess.run(["git", "init", "--bare", "-b", "ralph-queue", str(bare)], check=True)
    subprocess.run(["git", "init", "-b", "ralph-queue", str(seed)], check=True)
    _git(seed, "config", "user.email", "seed@example.com")
    _git(seed, "config", "user.name", "Seed")
    _git(seed, "commit", "--allow-empty", "-m", "chore: initial ralph-queue")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-u", "origin", "ralph-queue")
    yield workspace, str(bare)
