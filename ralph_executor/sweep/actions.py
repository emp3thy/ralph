"""Executes sweep decisions: folder moves, reviewer pings, feedback emission, and PR merge routing.

``dispatch`` routes a ``Decision`` to the matching side effect; the merge
branch fans out through ``dispatch_merge_pr`` → ``_invoke_merge_pr``. The
module takes a ``SweepContext`` (defined in ``types.py``) so it stays
leaf-level within ``sweep/`` and never imports the runner.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from ralph_executor.subprocess_utils import run_text
from ralph_executor.sweep import state as sidecar_state
from ralph_executor.sweep.events import emit_pr_merged_and_pbi_closed
from ralph_executor.sweep.feedback_emit import emit_feedback_pbi
from ralph_executor.sweep.history import append_history, move_with_history
from ralph_executor.sweep.target import per_pbi_subprocess_overrides
from ralph_executor.sweep.types import (
    Action,
    Decision,
    PrSnapshot,
    SweepContext,
    SweepPbiError,
)
from ralph_executor.url_utils import TargetRepoInfo

log = logging.getLogger(__name__)


def dispatch(
    *,
    pbi_dir: Path,
    decision: Decision,
    snapshot: PrSnapshot,
    sidecar: sidecar_state.SweepSidecar,
    ctx: SweepContext,
    target_info: TargetRepoInfo | None = None,
) -> None:
    """Execute the decided action for one pending PBI.

    Each ``Action`` maps to exactly one side effect: a folder move (with
    HISTORY.md append), a HISTORY.md ping entry, feedback-PBI emission,
    or the merge_pr routing in :func:`dispatch_merge_pr`. ``NOOP`` does
    nothing; an unhandled action raises :class:`SweepPbiError` so the
    per-PBI guard in ``run`` records it without aborting the sweep.
    """
    qr = ctx.queue_root
    a = decision.action
    if a is Action.MOVE_TO_DONE:
        move_with_history(pbi_dir, qr / "done" / pbi_dir.name, decision.reason, ctx.config.now)
        emit_pr_merged_and_pbi_closed(
            pbi_id=pbi_dir.name,
            snapshot=snapshot,
            event_log=ctx.event_log,
            now=ctx.config.now,
        )
    elif a in (Action.MOVE_TO_BLOCKED_ABANDONED, Action.MOVE_TO_BLOCKED_MAX_ATTEMPTS):
        move_with_history(pbi_dir, qr / "blocked" / pbi_dir.name, decision.reason, ctx.config.now)
    elif a is Action.MOVE_TO_INBOX_RETRY:
        move_with_history(pbi_dir, qr / "inbox" / pbi_dir.name, decision.reason, ctx.config.now)
    elif a is Action.CREATE_FEEDBACK_PBI:
        emit_feedback_pbi(
            pbi_dir=pbi_dir,
            decision=decision,
            snapshot=snapshot,
            sidecar=sidecar,
            queue_root=ctx.queue_root,
            now=ctx.config.now,
        )
    elif a is Action.PING_REVIEWER:
        append_history(pbi_dir, decision.reason, ctx.config.now)
    elif a is Action.MERGE_PR:
        dispatch_merge_pr(
            pbi_dir=pbi_dir,
            snapshot=snapshot,
            ctx=ctx,
            target_info=target_info,
        )
    elif a is Action.NOOP:
        return
    else:  # pragma: no cover — defensive
        raise SweepPbiError(f"unhandled action: {a}")


def _invoke_merge_pr(
    *,
    pr_id: int,
    ctx: SweepContext,
    repo_name: str | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Subprocess-invoke the ``pr-github`` ``merge_pr.py`` skill.

    Returns the raw subprocess exit code (0 = merged, 3 = HTTP error,
    4 = race / refused-by-host). Exit 2 (argparse / validation) is
    converted to ``SweepPbiError`` so the per-PBI guard in ``run`` catches
    it — exit 2 indicates the caller passed garbage and is never a
    "retry next sweep" condition. Any other unexpected exit also raises,
    so the dispatch branch never silently swallows a new exit code added
    to the skill in the future.

    ``repo_name`` and ``env`` (when provided) override the legacy
    ``ctx.repo_name`` + inherited-env behaviour with per-PBI values
    derived from the PBI's ``target_repo``.
    """
    script = ctx.ado_pr_scripts_path / "merge_pr.py"
    if not script.is_file():
        raise SweepPbiError(f"merge_pr.py not found at {script}")
    cmd = [
        sys.executable,
        str(script),
        "--repo",
        repo_name or ctx.repo_name or ctx.queue_root.parent.name,
        "--pr-id",
        str(pr_id),
    ]
    result = run_text(
        cmd,
        check=False,
        capture_output=True,
        env=env,
    )
    if result.returncode == 2:
        raise SweepPbiError(
            f"merge_pr.py exited 2 (validation): {result.stderr.strip() or '<no stderr>'}"
        )
    return result.returncode


def dispatch_merge_pr(
    *,
    pbi_dir: Path,
    snapshot: PrSnapshot,
    ctx: SweepContext,
    target_info: TargetRepoInfo | None = None,
) -> None:
    """Run merge_pr.py for this PBI and route its exit code.

    Exit 0 → move PBI to ``done/`` with the marker reason and emit
    ``PR_MERGED`` + ``PBI_CLOSED``. Exit 4 → log INFO and leave the PBI
    in ``pending-pr/`` for the next sweep (race / refused-by-host).
    Exit 3 → log WARNING and leave the PBI in ``pending-pr/`` (transient
    GitHub error). Anything else → raise ``SweepPbiError`` so the
    per-PBI guard records it and continues with the remaining PBIs.
    """
    sub_env, sub_repo = per_pbi_subprocess_overrides(target_info, ctx)
    rc = _invoke_merge_pr(pr_id=snapshot.pr_id, ctx=ctx, repo_name=sub_repo, env=sub_env)
    if rc == 0:
        move_with_history(
            pbi_dir,
            ctx.queue_root / "done" / pbi_dir.name,
            "PR auto-merged by sweep",
            ctx.config.now,
        )
        emit_pr_merged_and_pbi_closed(
            pbi_id=pbi_dir.name,
            snapshot=snapshot,
            event_log=ctx.event_log,
            now=ctx.config.now,
        )
    elif rc == 4:
        log.info(
            "sweep: merge_pr refused for %s (race / not-ready); retry next iter",
            pbi_dir.name,
        )
    elif rc == 3:
        log.warning(
            "sweep: merge_pr GitHub error for %s; retry next iter",
            pbi_dir.name,
        )
    else:
        raise SweepPbiError(f"merge_pr returned unexpected exit {rc}")
