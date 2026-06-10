"""Idempotent clone of the queue repo into the workspace.

Mirrors ``target_clone.ensure_clone`` — clone on first call, fetch +
ff-only pull on subsequent calls. The branch is configurable per
deployment via ``cfg.queue_branch`` (default ``"ralph-queue"``); the
queue clone never leaves that branch.

The destination is namespaced per-instance at
``<workspace_root>/queue-<instance_id>/``. The ``instance_id`` kwarg is
required so every call-site spells it out — there is no fallback to a
shared ``queue/`` directory that two ralph instances could collide on.
A pre-multi-ralph legacy ``<workspace_root>/queue/`` directory is never
migrated automatically: its presence raises ``QueueCloneError`` with
instructions to rename it to the namespaced path (or delete it to
re-clone); if BOTH the legacy and namespaced paths exist,
``QueueCloneError`` is raised because the executor cannot pick a winner
without operator input.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ralph_executor.subprocess_utils import run_text

log = logging.getLogger(__name__)


class QueueCloneError(RuntimeError):
    """Raised when the queue clone cannot be created or refreshed."""


def _run_git(
    repo: Path | None, *args: str, timeout: float = 120.0
) -> subprocess.CompletedProcess[str]:
    """``run_text`` wrapper that converts subprocess failures to
    :class:`QueueCloneError`.

    ``subprocess.run`` raises ``TimeoutExpired`` before returning a
    result object when the operation exceeds the wall-clock budget; it
    raises ``FileNotFoundError`` (an ``OSError`` subclass) when ``git``
    is absent from ``PATH``. Either would escape ``ensure_queue_clone``
    unwrapped and crash the executor process in the caller
    (``queue_git.pull_queue`` only knows ``QueueCloneError``).
    """
    argv = ["git", *(["-C", str(repo)] if repo is not None else []), *args]
    try:
        return run_text(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise QueueCloneError(
            f"git {' '.join(args)} exceeded {timeout}s in {repo or '<no repo>'}"
        ) from exc
    except OSError as exc:
        raise QueueCloneError(
            f"git {' '.join(args)} failed in {repo or '<no repo>'}: {exc}"
        ) from exc


def ensure_queue_clone(
    workspace_root: Path,
    queue_repo: str,
    queue_branch: str,
    *,
    instance_id: str,
    timeout: float = 120.0,
) -> Path:
    """Ensure ``<workspace_root>/queue-<instance_id>/`` is a clone of ``queue_repo``.

    On first call: ``git clone -b <queue_branch> <queue_repo>
    <workspace_root>/queue-<instance_id>``. On subsequent calls:
    ``git fetch origin`` then ``git pull --ff-only origin <queue_branch>``.

    ``instance_id`` is required: the clone always lands at the
    namespaced path so multiple ralphs on the same workspace can
    co-exist. A legacy ``<workspace_root>/queue/`` directory is never
    migrated automatically — if one exists, ``QueueCloneError`` is
    raised with instructions to rename it to the namespaced path (or
    delete it to re-clone); if BOTH the legacy and namespaced paths
    exist, ``QueueCloneError`` is raised so the operator picks the
    winner.

    Returns the path to the clone. Raises ``QueueCloneError`` with a message
    pointing at ``gh auth login`` on auth-related failures.
    """
    workspace_root.mkdir(parents=True, exist_ok=True)
    legacy = workspace_root / "queue"
    dest = workspace_root / f"queue-{instance_id}"

    if legacy.exists() and dest.exists():
        raise QueueCloneError(
            f"both legacy queue/ and queue-{instance_id}/ exist in "
            f"{workspace_root}; remove or merge one before continuing "
            "(the executor refuses to pick a winner so no committed "
            "work is silently abandoned)"
        )
    if legacy.exists():
        raise QueueCloneError(
            f"legacy queue clone found at {legacy}. Multi-ralph requires the "
            f"namespaced path: rename it to {dest} (git-aware: plain mv/rename "
            "is fine) or delete it to re-clone"
        )

    if not (dest / ".git").exists():
        log.info("cloning queue %s (branch=%s) -> %s", queue_repo, queue_branch, dest)
        result = _run_git(
            None,
            "clone",
            "-b",
            queue_branch,
            queue_repo,
            str(dest),
            timeout=timeout,
        )
        if result.returncode != 0:
            raise QueueCloneError(
                f"git clone of queue repo {queue_repo!r} (branch {queue_branch!r}) failed "
                f"(exit {result.returncode}): {result.stderr.strip()}\n"
                f"If this is an auth problem, run `gh auth login`. If the branch is "
                f"missing on the remote, run scripts/setup_ralph_queue_github.py first."
            )
        return dest

    log.info("refreshing queue clone at %s (branch=%s)", dest, queue_branch)
    fetch = _run_git(dest, "fetch", "origin", timeout=timeout)
    if fetch.returncode != 0:
        raise QueueCloneError(
            f"git fetch in queue clone {dest} failed "
            f"(exit {fetch.returncode}): {fetch.stderr.strip()}"
        )
    pull = _run_git(dest, "pull", "--ff-only", "origin", queue_branch, timeout=timeout)
    if pull.returncode != 0:
        raise QueueCloneError(
            f"git pull --ff-only origin {queue_branch} in queue clone {dest} failed "
            f"(exit {pull.returncode}): {pull.stderr.strip()}\n"
            f"If {queue_branch} was force-pushed remotely, resolve manually."
        )
    return dest
