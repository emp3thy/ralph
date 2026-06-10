"""Work-worktree lifecycle.

Two operations: materialise the per-PBI work worktree inside the target
clone on its feature branch, and tear it down after the PBI reaches a
terminal state. The worktree lives at
``<clone-root>/.ralph-work/<PBI-ID>/`` on branch ``ralph/<PBI-ID>``;
cleanup always preserves the feature branch (pending-pr PBIs need it to
keep the PR alive) and tolerates removal failures — an orphan worktree
is operator-recoverable via ``git worktree prune``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ralph_executor.config import ExecutorConfig
from ralph_executor.types import PBI
from ralph_executor.worktree import (
    ensure_worktree,
    remove_worktree,
    work_worktree_path,
)

log = logging.getLogger(__name__)


def materialise_worktree(
    cfg: ExecutorConfig,
    *,
    clone_root: Path,
    pbi_id: str,
    branch: str,
) -> Path:
    """Create (idempotently) the per-PBI work worktree and return its path.

    The worktree is rooted at ``<clone_root>/.ralph-work/<pbi_id>/`` on
    ``branch``, forked from ``origin/<cfg.main_branch>`` when the branch
    is new. ``ensure_worktree`` is a no-op when the worktree already
    exists on the right branch. Raises ``git_ops.GitCommandError`` when
    the base ref is missing — callers pre-flight or catch accordingly.
    """
    work_wt = work_worktree_path(clone_root, pbi_id)
    ensure_worktree(
        clone_root,
        worktree_path=work_wt,
        branch=branch,
        create_branch_from=f"origin/{cfg.main_branch}",
    )
    return work_wt


def cleanup_work_worktree(cfg: ExecutorConfig, pbi: PBI) -> None:
    """Remove the per-PBI work worktree on a terminal iteration outcome.

    Called when the PBI leaves ``current/`` (pr_created → pending-pr,
    handle_stuck → blocked, max-attempts → blocked). The feature branch
    ``ralph/<PBI-ID>`` is preserved so pending-pr PBIs keep a branch to
    point the PR at; only the working directory at
    ``<clone-root>/.ralph-work/<PBI-id>/`` is torn down.

    Locates the owning git repo via ``ensure_clone`` against the PBI's
    ``target_repo`` URL — this is idempotent (no clone if already present,
    just a fetch) and returns the same ``clone_root`` the claim used.
    Calling ``ensure_clone`` again avoids relying on ``pbi.work_worktree``
    (which is runtime-only and absent after a queue re-read between
    iterations); there is no process-wide ``repo_path`` to fall back
    onto after KILL-RALPH-HOME, so the deterministic clone root from
    ``ensure_clone`` is the only honest source.

    Defensive no-op when target_repo / ensure_clone are unavailable.
    Tolerant of removal failures — an orphan worktree is recoverable
    (operator can ``git worktree prune``), but raising here would obscure
    the real terminal outcome the iteration is reporting.
    """
    if pbi.work_worktree is not None:
        work_wt = pbi.work_worktree
        # Work worktrees live at <clone-root>/.ralph-work/<pbi-id>/, so
        # the owning git repo is the worktree's grandparent.
        owning_repo = work_wt.parent.parent
    else:
        # Re-derive clone_root from the PBI's target_repo field
        # (populated by ``iterate_once`` immediately after the queue
        # read so it survives the move_*_to_* operations that invalidate
        # ``pbi.path``). Idempotent in production (fetch-only when clone
        # already exists); honours the tests' monkeypatched
        # ``ensure_clone`` so fixture clone_roots match what the claim
        # used.
        if not pbi.target_repo:
            log.warning(
                "PBI %s missing target_repo on terminal outcome; cannot "
                "resolve work worktree path — orphan may remain",
                pbi.id,
            )
            return
        try:
            from ralph_executor.url_utils import parse_target_repo

            info = parse_target_repo(pbi.target_repo)
        except ValueError:
            log.warning(
                "failed to parse target_repo for PBI %s; orphan work worktree may remain",
                pbi.id,
                exc_info=True,
            )
            return
        # Compute owning_repo deterministically (workspace_root + owner +
        # name) rather than via a fresh ``ensure_clone`` — a transient
        # fetch failure shouldn't leave the worktree behind, and the path
        # itself is fully determined by the URL components. Tests
        # monkeypatch ``ensure_clone`` to return a divergent clone_root
        # (fake_repo), so honour that override when present by calling
        # ensure_clone and using its return value if it succeeds.
        owning_repo = cfg.workspace_root / "clones" / info.owner / info.name
        try:
            from ralph_executor.target_clone import ensure_clone

            clone = ensure_clone(info, workspace_root=cfg.workspace_root)
            owning_repo = clone.clone_root
        except Exception:
            log.warning(
                "ensure_clone failed for PBI %s; using deterministic clone path",
                pbi.id,
                exc_info=True,
            )
        if not (owning_repo / ".git").exists():
            log.warning(
                "owning repo %s has no .git; cannot remove worktree for PBI %s",
                owning_repo,
                pbi.id,
            )
            return
        work_wt = work_worktree_path(owning_repo, pbi.id)
    try:
        remove_worktree(owning_repo, work_wt)
    except Exception:
        log.warning(
            "failed to remove work worktree at %s; orphan left for manual prune",
            work_wt,
            exc_info=True,
        )
