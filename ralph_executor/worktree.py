"""Thin wrappers around ``git worktree`` for the two-worktree executor.

The Stage-B executor keeps two worktrees per repo:

* a long-lived **queue worktree** pinned to ``ralph-queue``, where the
  executor reads/writes ``.ralph/``;
* a per-PBI **work worktree** pinned to ``ralph/<PBI-ID>``, where Claude
  edits code.

These helpers create, inspect, and remove those worktrees. Everything
goes through ``git_ops._run_git`` so subprocess invocation matches the
rest of ``ralph_executor`` (single point to intercept in tests).
"""

from __future__ import annotations

import logging
from pathlib import Path

from .git_ops import GitCommandError, _run_git

log = logging.getLogger(__name__)


WORKTREE_ROOT_DIR = ".ralph-work"


def queue_worktree_path(repo_root: Path) -> Path:
    """Canonical filesystem path of the long-lived queue worktree.

    All ``.ralph/`` reads and writes route through this path when
    ``cfg.use_worktrees`` is True, so the executor never has to swap the
    primary checkout's branch to see the queue tree.
    """
    return Path(repo_root) / WORKTREE_ROOT_DIR / "queue"


def work_worktree_path(clone_root: Path, pbi_id: str) -> Path:
    """Canonical filesystem path of the per-PBI work worktree.

    Lives INSIDE the target's clone (not inside ralph's checkout). The
    queue worktree (``queue_worktree_path``) stays in ralph's checkout.
    Returns ``<clone_root>/.ralph-work/<pbi_id>``.
    """
    return Path(clone_root) / WORKTREE_ROOT_DIR / pbi_id


def _local_branch_exists(git_root: Path, branch: str) -> bool:
    """Return True if ``refs/heads/<branch>`` exists in ``git_root``.

    Unlike ``git_ops.branch_exists`` (which also looks at ``origin/``),
    worktree creation needs to know whether the branch is materialised
    locally — ``git worktree add <path> <branch>`` requires a local ref.
    """
    result = _run_git(
        git_root,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/heads/{branch}",
        check=False,
    )
    return result.returncode == 0


def _existing_worktree_paths(git_root: Path) -> set[Path]:
    """Resolved paths of every worktree currently registered for ``git_root``."""
    paths: set[Path] = set()
    for entry in list_worktrees(git_root):
        path_value = entry.get("path")
        if isinstance(path_value, str):
            paths.add(Path(path_value).resolve())
    return paths


def list_worktrees(git_root: Path) -> list[dict[str, str | bool]]:
    """Parse ``git worktree list --porcelain`` into structured dicts.

    Each entry has at least a ``path`` key. ``branch`` (with the
    ``refs/heads/`` prefix stripped) is present for attached worktrees;
    detached HEADs get a ``detached: True`` flag instead. ``bare``,
    ``locked``, and ``prunable`` markers pass through as booleans.
    """
    out = _run_git(git_root, "worktree", "list", "--porcelain").stdout
    entries: list[dict[str, str | bool]] = []
    current: dict[str, str | bool] = {}
    for line in out.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        key, sep, value = line.partition(" ")
        if sep:
            current[key] = value
        else:
            current[key] = True
    if current:
        entries.append(current)
    for entry in entries:
        # ``git worktree list --porcelain`` emits each block keyed by the
        # literal token ``worktree <path>``; normalise to ``path`` so the
        # rest of the module (and consumers) can rely on a stable key.
        worktree_value = entry.pop("worktree", None)
        if isinstance(worktree_value, str):
            entry["path"] = worktree_value
        branch = entry.get("branch")
        if isinstance(branch, str):
            entry["branch"] = branch.removeprefix("refs/heads/")
    return entries


def worktree_branch(worktree_path: Path) -> str:
    """Return the branch currently checked out in ``worktree_path``.

    Used to verify state before / after ``ensure_worktree`` flips a
    worktree's branch.
    """
    return _run_git(worktree_path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def ensure_worktree(
    git_root: Path,
    *,
    worktree_path: Path,
    branch: str,
    create_branch_from: str | None = None,
) -> None:
    """Idempotently materialise ``worktree_path`` on ``branch``.

    If the worktree already exists, switch its checked-out branch to
    ``branch`` (no-op when already there). If it does not exist, create
    it via ``git worktree add``.

    When ``branch`` does not exist locally, ``create_branch_from`` must
    be provided so the helper can ``git worktree add -b <branch> <path>
    <base>`` (or ``git checkout -b`` in the existing-worktree case).
    Raises ``GitCommandError`` when git rejects the operation;
    ``ValueError`` when the branch is missing and no base was provided.

    Callers are expected to ensure ``worktree_path``'s working tree is
    clean before switching branches — this helper does not stash or
    discard local changes.
    """
    worktree_path = Path(worktree_path)
    git_root = Path(git_root)

    existing = _existing_worktree_paths(git_root)
    target = worktree_path.resolve() if worktree_path.exists() else worktree_path

    if target in existing or worktree_path.resolve() in existing:
        current = worktree_branch(worktree_path)
        if current == branch:
            return
        if _local_branch_exists(git_root, branch):
            _run_git(worktree_path, "checkout", branch)
            return
        if create_branch_from is None:
            raise ValueError(
                f"branch {branch!r} does not exist locally and create_branch_from is not set"
            )
        _run_git(worktree_path, "checkout", "-b", branch, create_branch_from)
        return

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if _local_branch_exists(git_root, branch):
        _run_git(git_root, "worktree", "add", str(worktree_path), branch)
        return
    if create_branch_from is None:
        raise ValueError(
            f"branch {branch!r} does not exist locally and create_branch_from is not set"
        )
    _run_git(
        git_root,
        "worktree",
        "add",
        "-b",
        branch,
        str(worktree_path),
        create_branch_from,
    )


def remove_worktree(git_root: Path, worktree_path: Path) -> None:
    """Remove ``worktree_path`` and prune git's metadata for it.

    Tolerant of an already-gone worktree: when the path is not in
    ``git worktree list``, run ``git worktree prune`` to clean up any
    lingering metadata and return without error. On Windows, callers
    must not invoke this from a shell whose CWD is inside the worktree
    being removed — Windows pins a directory handle on the CWD and the
    remove will fail with "Permission denied" (see memory id
    0dda832cbec346c29e5e81e3cb2113f9). ``--force`` is always passed so
    incomplete state from a previous crash does not block cleanup.
    """
    worktree_path = Path(worktree_path)
    git_root = Path(git_root)

    paths = _existing_worktree_paths(git_root)
    if worktree_path.resolve() not in paths:
        _run_git(git_root, "worktree", "prune", check=False)
        return

    try:
        _run_git(git_root, "worktree", "remove", "--force", str(worktree_path))
    except GitCommandError as exc:
        log.warning("git worktree remove failed for %s: %s", worktree_path, exc)
        raise
    finally:
        _run_git(git_root, "worktree", "prune", check=False)


__all__ = [
    "GitCommandError",
    "WORKTREE_ROOT_DIR",
    "ensure_worktree",
    "list_worktrees",
    "queue_worktree_path",
    "remove_worktree",
    "work_worktree_path",
    "worktree_branch",
]
