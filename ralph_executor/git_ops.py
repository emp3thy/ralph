"""Thin subprocess wrappers around the ``git`` binary.

Every helper goes through ``_run_git`` so tests have a single point to
intercept (the conftest uses real subprocess against a local fake repo,
but Plans 8 and 9 may want to spy on calls via monkeypatch).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class GitCommandError(RuntimeError):
    """Raised when a git invocation exits non-zero."""

    def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
        super().__init__(f"git command {argv!r} exited {returncode}: {stderr.strip()}")
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr


def _run_git(
    repo: Path,
    *args: str,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    argv = ["git", *args]
    log.debug("git: cwd=%s argv=%s", repo, argv)
    result = subprocess.run(
        argv,
        cwd=str(repo),
        check=False,
        capture_output=capture,
        text=True,
    )
    if check and result.returncode != 0:
        raise GitCommandError(argv, result.returncode, result.stderr)
    return result


def current_branch(repo: Path) -> str:
    """Return the name of the currently checked-out branch."""
    return _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def fetch(repo: Path, remote: str = "origin") -> None:
    """Run ``git fetch <remote>``."""
    _run_git(repo, "fetch", remote)


def pull(repo: Path, branch: str, remote: str = "origin") -> None:
    """Run ``git pull --ff-only <remote> <branch>`` against the current checkout."""
    _run_git(repo, "pull", "--ff-only", remote, branch)


def checkout(repo: Path, branch: str) -> None:
    """Run ``git checkout <branch>`` (must already exist)."""
    _run_git(repo, "checkout", branch)


def checkout_new(repo: Path, branch: str) -> None:
    """Run ``git checkout -b <branch>`` off the current HEAD."""
    _run_git(repo, "checkout", "-b", branch)


def branch_exists(repo: Path, branch: str) -> bool:
    """Return True if ``branch`` exists either locally or on ``origin``."""
    local = _run_git(repo, "branch", "--list", branch, check=False).stdout.strip()
    if local:
        return True
    remote = _run_git(
        repo, "branch", "-r", "--list", f"origin/{branch}", check=False
    ).stdout.strip()
    return bool(remote)


def is_branch_remote(repo: Path, branch: str) -> bool:
    """Return True if ``origin/<branch>`` exists."""
    remote = _run_git(
        repo, "branch", "-r", "--list", f"origin/{branch}", check=False
    ).stdout.strip()
    return bool(remote)


def add(repo: Path, path: Path) -> None:
    """Run ``git add <path>``."""
    _run_git(repo, "add", str(path.relative_to(repo)))


def commit_all(repo: Path, message: str) -> str:
    """Stage tracked changes and commit. Returns the new HEAD sha.

    If there is nothing to commit, returns the current HEAD sha unchanged.
    """
    _run_git(repo, "add", "-A")
    status = _run_git(repo, "status", "--porcelain").stdout.strip()
    if not status:
        return rev_parse_head(repo)
    _run_git(repo, "commit", "-m", message)
    return rev_parse_head(repo)


def commit_index(repo: Path, message: str) -> str:
    """Commit whatever is currently staged. Returns new HEAD sha.

    Unlike ``commit_all``, this does NOT run ``git add -A`` first —
    callers stage the exact paths they want via ``add()``. No-ops
    (returns current HEAD) when the index is empty.
    """
    diff = _run_git(repo, "diff", "--cached", "--quiet", check=False)
    if diff.returncode == 0:
        return rev_parse_head(repo)
    _run_git(repo, "commit", "-m", message)
    return rev_parse_head(repo)


def push(repo: Path, branch: str, remote: str = "origin") -> None:
    """Run ``git push <remote> <branch>``."""
    _run_git(repo, "push", remote, branch)


def mv(repo: Path, src: Path, dst: Path) -> None:
    """Run ``git mv <src> <dst>``, creating ``dst``'s parent dirs if needed."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run_git(
        repo,
        "mv",
        str(src.relative_to(repo)),
        str(dst.relative_to(repo)),
    )


def rev_parse_head(repo: Path) -> str:
    """Return the 40-char sha of the current HEAD."""
    return _run_git(repo, "rev-parse", "HEAD").stdout.strip()
