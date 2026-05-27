"""Tests for ``ralph_executor.worktree`` helpers.

The helpers wrap ``git worktree`` for the two-worktree executor: queue
worktree pinned to ``ralph-queue`` and a per-PBI work worktree on
``ralph/<PBI-ID>``. These tests exercise the helpers directly against a
real local git repo (no mocks of subprocess) so the porcelain parsing and
edge cases (already-exists, branch-from-base, missing-on-remove) are
covered against the actual ``git`` binary.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from ralph_executor.worktree import (
    ensure_worktree,
    list_worktrees,
    remove_worktree,
    worktree_branch,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def worktree_repo(tmp_path: Path) -> Iterator[Path]:
    """Minimal local git repo with ``main`` and an extra ``feature`` branch.

    No bare remote and no ``ralph-queue`` — the helper tests do not need
    them. The repo's primary HEAD is left on ``main``; tests add or remove
    worktrees against the other refs.
    """
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "commit", "--allow-empty", "-m", "init")
    _git(repo, "branch", "-M", "main")
    _git(repo, "branch", "feature")
    yield repo
    # Best-effort teardown: pytest's tmp_path cleanup can fail on Windows
    # if worktrees are still registered. Force-remove anything left.
    for entry in list_worktrees(repo):
        path = entry.get("path")
        if isinstance(path, str) and Path(path) != repo:
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", path],
                check=False,
                capture_output=True,
            )
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "prune"],
        check=False,
        capture_output=True,
    )


def _registered_worktree_paths(repo: Path) -> set[Path]:
    """Resolve every registered worktree to a ``Path`` for comparison.

    Resolving via ``Path`` normalises slash style so the assertion holds
    on Windows where porcelain emits forward slashes but ``resolve()``
    returns backslashes.
    """
    return {
        Path(str(entry.get("path"))).resolve()
        for entry in list_worktrees(repo)
        if isinstance(entry.get("path"), str)
    }


def test_ensure_worktree_creates_new_when_absent(worktree_repo: Path) -> None:
    """``ensure_worktree`` creates a fresh worktree for an existing branch."""
    target = worktree_repo / ".ralph-work" / "feature-wt"
    assert not target.exists()

    ensure_worktree(worktree_repo, worktree_path=target, branch="feature")

    assert target.is_dir(), "worktree directory was not created"
    assert worktree_branch(target) == "feature"
    assert target.resolve() in _registered_worktree_paths(worktree_repo)


def test_ensure_worktree_idempotent_when_already_exists(worktree_repo: Path) -> None:
    """Calling ``ensure_worktree`` twice with the same branch is a no-op
    on the second call — no error, no duplicate registration."""
    target = worktree_repo / ".ralph-work" / "feature-wt"
    ensure_worktree(worktree_repo, worktree_path=target, branch="feature")
    before = list_worktrees(worktree_repo)

    ensure_worktree(worktree_repo, worktree_path=target, branch="feature")
    after = list_worktrees(worktree_repo)

    assert len(after) == len(before)
    assert worktree_branch(target) == "feature"


def test_ensure_worktree_creates_branch_from_base(worktree_repo: Path) -> None:
    """When the requested branch does not exist locally, ``ensure_worktree``
    creates it from ``create_branch_from`` rather than failing."""
    target = worktree_repo / ".ralph-work" / "new-wt"

    ensure_worktree(
        worktree_repo,
        worktree_path=target,
        branch="ralph/WI-42",
        create_branch_from="main",
    )

    assert worktree_branch(target) == "ralph/WI-42"
    # The new branch is now a local ref, not just a worktree HEAD.
    refs = _git(worktree_repo, "branch", "--list", "ralph/WI-42").strip()
    assert "ralph/WI-42" in refs


def test_remove_worktree_cleans_up_and_prunes(worktree_repo: Path) -> None:
    """``remove_worktree`` deletes the working tree and drops the git
    metadata so subsequent ``list_worktrees`` no longer reports it."""
    target = worktree_repo / ".ralph-work" / "feature-wt"
    ensure_worktree(worktree_repo, worktree_path=target, branch="feature")
    assert target.is_dir()

    remove_worktree(worktree_repo, target)

    assert not target.exists(), "worktree directory still present after remove"
    assert target.resolve() not in _registered_worktree_paths(worktree_repo)


def test_remove_worktree_tolerates_missing(worktree_repo: Path) -> None:
    """An already-gone worktree is not an error — ``remove_worktree``
    prunes any stale metadata and returns cleanly so cleanup paths are
    safe to call unconditionally on crash recovery."""
    target = worktree_repo / ".ralph-work" / "never-created"
    assert not target.exists()

    # Must not raise.
    remove_worktree(worktree_repo, target)

    assert target.resolve() not in _registered_worktree_paths(worktree_repo)
