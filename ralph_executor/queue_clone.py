"""Idempotent clone of the queue repo into the workspace.

Mirrors ``target_clone.ensure_clone`` — clone on first call, fetch +
ff-only pull on subsequent calls. The queue repo's default branch is
``main`` (not ``ralph-queue``); branch-swapping is irrelevant because
the queue clone never leaves ``main``.
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
    argv = ["git", *(["-C", str(repo)] if repo is not None else []), *args]
    return run_text(argv, capture_output=True, timeout=timeout)


def ensure_queue_clone(workspace_root: Path, queue_repo: str, *, timeout: float = 120.0) -> Path:
    """Ensure ``<workspace_root>/queue`` is a clone of ``queue_repo`` on ``main``.

    On first call: ``git clone <queue_repo> <workspace_root>/queue``.
    On subsequent calls: ``git fetch origin`` then ``git pull --ff-only origin main``.

    Returns the path to the clone. Raises ``QueueCloneError`` with a message
    pointing at ``gh auth login`` on auth-related failures.
    """
    workspace_root.mkdir(parents=True, exist_ok=True)
    dest = workspace_root / "queue"

    if not (dest / ".git").exists():
        log.info("cloning queue %s -> %s", queue_repo, dest)
        result = _run_git(None, "clone", queue_repo, str(dest), timeout=timeout)
        if result.returncode != 0:
            raise QueueCloneError(
                f"git clone of queue repo {queue_repo!r} failed "
                f"(exit {result.returncode}): {result.stderr.strip()}\n"
                f"If this is an auth problem, run `gh auth login`."
            )
        return dest

    log.info("refreshing queue clone at %s", dest)
    fetch = _run_git(dest, "fetch", "origin", timeout=timeout)
    if fetch.returncode != 0:
        raise QueueCloneError(
            f"git fetch in queue clone {dest} failed "
            f"(exit {fetch.returncode}): {fetch.stderr.strip()}"
        )
    pull = _run_git(dest, "pull", "--ff-only", "origin", "main", timeout=timeout)
    if pull.returncode != 0:
        raise QueueCloneError(
            f"git pull --ff-only in queue clone {dest} failed "
            f"(exit {pull.returncode}): {pull.stderr.strip()}\n"
            f"If main was force-pushed remotely, resolve manually."
        )
    return dest
