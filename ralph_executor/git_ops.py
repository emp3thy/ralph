"""Thin subprocess wrappers around the ``git`` binary.

Every helper goes through ``_run_git`` so tests have a single point to
intercept (the conftest uses real subprocess against a local fake repo,
but Plans 8 and 9 may want to spy on calls via monkeypatch).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ralph_executor.subprocess_utils import run_text

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
    result = run_text(
        argv,
        cwd=str(repo),
        check=False,
        capture_output=capture,
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


def clone(url: str, dest: Path, *, timeout: float = 120.0) -> None:
    """Run ``git clone <url> <dest>`` with a wall-clock timeout.

    Full (non-shallow) clone — ralph needs full history for
    ``git diff main..`` and sweep-side investigation. Raises
    :class:`GitCommandError` on non-zero exit; ``subprocess.TimeoutExpired``
    propagates unchanged if the clone exceeds ``timeout`` seconds.
    """
    argv = ["git", "clone", url, str(dest)]
    log.debug("git: argv=%s", argv)
    result = run_text(
        argv,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise GitCommandError(
            argv,
            result.returncode,
            result.stderr.strip() or result.stdout.strip(),
        )


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
    callers stage the exact paths they want via ``add()`` /
    ``add_all_changes()``. No-ops (returns current HEAD) when the index
    is empty.

    ``git diff --cached --quiet`` exits 0 (no diff), 1 (diff found),
    or >=2 (git itself errored — bad ref, repo corruption, etc.).
    Treat anything outside {0, 1} as a real failure and raise rather
    than letting it fall through to ``git commit`` (which would then
    error with a confusing 'nothing to commit' message).
    """
    diff = _run_git(repo, "diff", "--cached", "--quiet", check=False)
    if diff.returncode == 0:
        return rev_parse_head(repo)
    if diff.returncode != 1:
        raise GitCommandError(
            ["git", "diff", "--cached", "--quiet"],
            diff.returncode,
            diff.stderr,
        )
    _run_git(repo, "commit", "-m", message)
    return rev_parse_head(repo)


def add_all_changes(repo: Path, path: Path) -> None:
    """Run ``git add -A <path>`` — stages new, modified, AND deleted
    files inside ``<path>``.

    Bare ``git add <path>`` skips deletions of tracked files, which
    leaves the index inconsistent with the working tree when a caller
    (e.g. Claude removing a stale STUCK.md) deletes a tracked file.
    """
    _run_git(repo, "add", "-A", "--", str(path.relative_to(repo)))


def push(repo: Path, branch: str, remote: str = "origin") -> None:
    """Run ``git push <remote> <branch>``."""
    _run_git(repo, "push", remote, branch)


class PushRebaseConflict(RuntimeError):
    """Raised when :func:`push_with_rebase` aborted a rebase due to conflicts.

    ``conflict_paths`` lists the files git reported as conflicted before the
    rebase was aborted. Callers (``iterate_once``) use this for the log
    payload; this helper does NOT attempt to auto-resolve conflicts — the
    local branch is restored to its pre-rebase HEAD via ``rebase --abort``
    and the caller decides whether to skip the iteration or surface it to
    the operator.
    """

    def __init__(self, conflict_paths: tuple[str, ...]) -> None:
        super().__init__(f"rebase conflict on: {', '.join(conflict_paths) or '<unknown>'}")
        self.conflict_paths = conflict_paths


def push_with_rebase(repo: Path, *, remote: str, branch: str) -> None:
    """Fetch ``remote/branch``, rebase local commits onto it if needed, push.

    Safe against the common race where a concurrent writer advances
    ``remote/branch`` between iteration start and push: instead of failing
    with a non-fast-forward rejection, the local commits are replayed on
    top of the new remote tip and pushed.

    Raises:
        PushRebaseConflict: the rebase aborted because of conflict markers
            (file-level overlap between local commits and the new remote
            commits). The local branch is restored to its pre-rebase HEAD.
        GitCommandError: any other git failure (network, auth, corrupt
            repo state). Existing semantics — propagate and let the
            operator intervene.

    One retry is attempted on a second-attempt race (the rebase window
    itself got raced again); a second push failure propagates.
    """
    _run_git(repo, "fetch", remote, branch)
    counts = _run_git(
        repo,
        "rev-list",
        "--count",
        "--left-right",
        f"HEAD...{remote}/{branch}",
    ).stdout
    _ahead, behind = counts.strip().split()
    del _ahead  # only the "behind" side decides whether to rebase
    if int(behind) > 0:
        _rebase_onto_remote(repo, remote=remote, branch=branch)
    try:
        _run_git(repo, "push", remote, branch)
    except GitCommandError:
        # Window between rebase and push was raced again — fetch, rebase
        # one more time, then retry the push. A second failure propagates
        # to the caller: >2 writers racing in <5 seconds is a real human
        # problem worth interrupting on.
        _run_git(repo, "fetch", remote, branch)
        _rebase_onto_remote(repo, remote=remote, branch=branch)
        _run_git(repo, "push", remote, branch)


def _rebase_onto_remote(repo: Path, *, remote: str, branch: str) -> None:
    """Rebase HEAD onto ``remote/branch``; raise on conflicts after abort.

    Helper for :func:`push_with_rebase`. The double-rebase shape (initial
    + post-race retry) is identical, so the conflict-detection + abort
    logic lives here.
    """
    rebase = _run_git(repo, "rebase", f"{remote}/{branch}", check=False)
    if rebase.returncode == 0:
        return
    conflict_paths: tuple[str, ...] = ()
    try:
        diff = _run_git(repo, "diff", "--name-only", "--diff-filter=U", check=False)
        conflict_paths = tuple(line for line in diff.stdout.splitlines() if line.strip())
    finally:
        _run_git(repo, "rebase", "--abort", check=False)
    raise PushRebaseConflict(conflict_paths)


def mv(repo: Path, src: Path, dst: Path) -> None:
    """Run ``git mv <src> <dst>``, creating ``dst``'s parent dirs if needed."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run_git(
        repo,
        "mv",
        str(src.relative_to(repo)),
        str(dst.relative_to(repo)),
    )


def reset_hard(repo: Path, target: str) -> None:
    """Run ``git reset --hard <target>``.

    Drops local commits and resets the working tree + index to ``target``.
    Used by the claim path to roll back a local claim commit when
    ``push_with_rebase`` raises ``PushRebaseConflict`` — without the
    reset, the local clone would diverge from origin and the next
    iteration's ff-only pull would fail.
    """
    _run_git(repo, "reset", "--hard", target)


def ls_files(repo: Path, path: Path) -> list[str]:
    """Return paths tracked by git under ``path`` (one entry per line).

    Empty list = ``path`` exists on disk but no files under it are
    tracked yet. Used by ``movements._move`` to detect the
    external-writer-mid-add race (operator or another ralph session
    wrote a new inbox PBI dir to the queue clone but has not yet run
    ``git commit``); ``git mv`` would otherwise fail with
    ``fatal: source directory is empty`` and crash the executor.
    """
    result = _run_git(repo, "ls-files", "--", str(path.relative_to(repo)))
    return [line for line in result.stdout.splitlines() if line.strip()]


def rev_parse_head(repo: Path) -> str:
    """Return the 40-char sha of the current HEAD."""
    return _run_git(repo, "rev-parse", "HEAD").stdout.strip()


def diff_names(repo: Path, base: str, head: str) -> list[str]:
    """Return the list of paths the feature branch introduced.

    Runs ``git diff --name-only <base>...<head>`` (THREE dots).
    Three-dot is the diff from the merge-base of base+head to head — i.e.
    only what the feature branch actually changed. Two-dot would include
    files main changed after the feature branch was created, polluting
    the cycle-detector ``files`` payload and risking false
    same_file_thrashing trips. For two sequential commit SHAs (the
    ``_persist_iteration_writes`` call site) the merge-base of a parent
    and its child is the parent itself, so two-dot and three-dot produce
    identical results — the change is safe at both call sites.

    Returns an empty list if either ref is missing or the diff command
    fails (the cycle detector's payload contract tolerates an empty
    ``files`` list, so a partial failure is preferable to crashing
    the move).
    """
    result = _run_git(repo, "diff", "--name-only", f"{base}...{head}", check=False)
    if result.returncode != 0:
        log.warning(
            "diff_names %s...%s failed (%d): %s",
            base,
            head,
            result.returncode,
            result.stderr.strip(),
        )
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]
