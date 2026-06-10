"""Sweep runner: orchestrate the per-iteration sweep over pending-pr/.

The pure ``decide_action`` function encodes the spec's sweep table and is
exhaustively unit-tested. ``run`` wraps it with I/O: fetching PR state via
the PR skill, moving folders, and writing feedback PBIs.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import Path

from ralph_executor.sweep import pr_state
from ralph_executor.sweep import state as sidecar_state
from ralph_executor.sweep.actions import dispatch as _dispatch
from ralph_executor.sweep.events import (
    emit_pr_green_then_red as _emit_pr_green_then_red,
)
from ralph_executor.sweep.pr_state import AdoSkillError
from ralph_executor.sweep.target import (
    per_pbi_subprocess_overrides as _per_pbi_subprocess_overrides,
)
from ralph_executor.sweep.target import read_target_info
from ralph_executor.sweep.types import (
    Action,
    CommentSnapshot,
    Decision,
    PrSnapshot,
    SweepConfig,
    SweepContext,
)
from ralph_executor.sweep.types import (
    SweepPbiError as _SweepPbiError,
)
from ralph_executor.url_utils import TargetRepoInfo

log = logging.getLogger(__name__)

# Explicit re-export surface (mypy ``no_implicit_reexport``): SweepConfig /
# SweepContext moved to types.py and _per_pbi_subprocess_overrides to
# target.py, but cli.py, iteration_safety.py, reconcile.py and the tests
# import them from here.
__all__ = [
    "PbiActionRecord",
    "SweepConfig",
    "SweepContext",
    "SweepResult",
    "_per_pbi_subprocess_overrides",
    "decide_action",
    "run",
]


@dataclass(frozen=True)
class PbiActionRecord:
    pbi_id: str
    pr_id: int | None
    action: Action
    reason: str


@dataclass(frozen=True)
class SweepResult:
    """Summary returned by ``run`` for logging and tests."""

    pbis_scanned: int
    actions: tuple[PbiActionRecord, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


# ----------------------------------------------------------------------
# Pure-function core
# ----------------------------------------------------------------------


def decide_action(
    *,
    pr: PrSnapshot,
    attempts: int,
    last_seen_comment_ids: AbstractSet[str],
    last_feedback_round: int,
    config: SweepConfig,
) -> Decision:
    """Decide what to do with one pending PBI given its current PR snapshot.

    The ordering of the checks matches the spec's "Sweep logic per pending
    PR" table, except that "new active human comments" is evaluated BEFORE
    "stale" — when comments have arrived, addressing them is strictly more
    useful than pinging the reviewer (and the comment itself counts as
    activity, so the staleness check would no longer fire on the next sweep
    anyway).
    """
    del last_feedback_round  # reserved for future heuristics; unused in v1
    if pr.pr_status == "completed":
        return Decision(action=Action.MOVE_TO_DONE, reason="PR merged (completed)")
    if pr.pr_status == "abandoned":
        return Decision(action=Action.MOVE_TO_BLOCKED_ABANDONED, reason="PR abandoned")
    if pr.pr_status == "unknown":
        return Decision(
            action=Action.NOOP,
            reason="PR status unknown; will retry on next sweep",
        )

    # Active branch.
    if pr.ci_status == "failed":
        if attempts >= config.max_attempts:
            return Decision(
                action=Action.MOVE_TO_BLOCKED_MAX_ATTEMPTS,
                reason=(
                    f"CI red and attempts ({attempts}) >= max "
                    f"({config.max_attempts}); moving to blocked"
                ),
            )
        return Decision(
            action=Action.MOVE_TO_INBOX_RETRY,
            reason=f"CI red (attempt {attempts}); retry",
        )

    new_comments = _new_active_human_comments(
        pr=pr,
        last_seen_comment_ids=last_seen_comment_ids,
        ralph_author_email=config.ralph_author_email,
    )
    if new_comments:
        return Decision(
            action=Action.CREATE_FEEDBACK_PBI,
            reason=f"{len(new_comments)} new active comment(s) since last sweep",
            new_comments=new_comments,
        )

    if config.auto_merge_clean_prs and pr.merge_state == "clean":
        return Decision(action=Action.MERGE_PR, reason="auto-merging clean PR")

    if config.now - pr.last_activity_at >= config.stale_threshold:
        return Decision(
            action=Action.PING_REVIEWER,
            reason=(
                f"PR stale: no activity for >= "
                f"{config.stale_threshold} (last activity {pr.last_activity_at.isoformat()})"
            ),
        )

    return Decision(action=Action.NOOP, reason="PR open, CI green, awaiting review")


def _new_active_human_comments(
    *,
    pr: PrSnapshot,
    last_seen_comment_ids: AbstractSet[str],
    ralph_author_email: str,
) -> tuple[CommentSnapshot, ...]:
    """Filter the PR's threads to comments that warrant a new FEEDBACK PBI."""
    ralph_email = ralph_author_email.lower()
    collected: list[CommentSnapshot] = []
    for thread in pr.threads:
        if thread.status != "active":
            continue
        for comment in thread.comments:
            if comment.author_email.lower() == ralph_email:
                continue
            key = f"{comment.thread_id}:{comment.comment_id}"
            if key in last_seen_comment_ids:
                continue
            collected.append(comment)
    return tuple(collected)


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------


_PR_ID_RE = re.compile(r"!?(\d+)")


def run(*, ctx: SweepContext) -> SweepResult:
    """Walk pending-pr/ once. Return a structured summary."""
    pending_dir = ctx.queue_root / "pending-pr"
    pbis = _list_pbi_directories(pending_dir)
    actions, errors = _process_pending_pass(pbis=pbis, ctx=ctx)
    _reconcile_stale_current_pass(ctx=ctx, errors=errors)
    return SweepResult(
        pbis_scanned=len(pbis),
        actions=tuple(actions),
        errors=tuple(errors),
    )


def _list_pbi_directories(pending_dir: Path) -> list[Path]:
    if not pending_dir.is_dir():
        return []
    return sorted(p for p in pending_dir.iterdir() if p.is_dir())


def _process_pending_pass(
    *, pbis: list[Path], ctx: SweepContext
) -> tuple[list[PbiActionRecord], list[str]]:
    """Walk pending-pr/ entries: reconcile orphans, process PR-linked PBIs.

    One directory-sorted walk handles both arms (orphan reconciliation stays
    interleaved with PR-linked processing) so the observable ordering of
    moves, log lines and ``errors`` entries matches the pre-split single
    loop. The ``_SweepPbiError`` except sits directly around the per-PBI
    work — one bad PBI never aborts the sweep.
    """
    actions: list[PbiActionRecord] = []
    errors: list[str] = []
    for pbi_dir in pbis:
        if not (pbi_dir / "PR-LINK.md").is_file():
            _reconcile_orphans_pass(pbi_dir=pbi_dir, ctx=ctx, errors=errors)
            continue

        try:
            actions.append(_process_pbi(pbi_dir=pbi_dir, ctx=ctx))
        except _SweepPbiError as err:
            errors.append(f"{pbi_dir.name}: {err}")
            log.warning("sweep error for %s: %s", pbi_dir.name, err)
    return actions, errors


def _reconcile_orphans_pass(*, pbi_dir: Path, ctx: SweepContext, errors: list[str]) -> None:
    """Reconcile one pending-pr/ entry that has no PR-LINK.md.

    Invoked from inside ``_process_pending_pass``'s walk (not as a separate
    sweep over the queue) so orphans are handled in the same sorted order
    as their PR-linked siblings.
    """
    from ralph_executor.sweep.reconcile import (  # local import avoids cycle
        ReconcileError,
        reconcile_orphan,
    )
    from ralph_executor.sweep.types import ReconcileAction

    try:
        action = reconcile_orphan(pbi_dir, ctx)
        if action == ReconcileAction.KEEP_API_ERROR:
            log.warning(
                "sweep: reconcile API error for %s; will retry next iteration",
                pbi_dir.name,
            )
        else:
            log.info(
                "sweep: reconciled %s -> %s",
                pbi_dir.name,
                action.value,
            )
    except ReconcileError as err:
        errors.append(f"{pbi_dir.name}: reconcile error: {err}")
        log.warning("sweep: reconcile failed for %s: %s", pbi_dir.name, err)


def _reconcile_stale_current_pass(*, ctx: SweepContext, errors: list[str]) -> None:
    """Reconcile stale .ralph/current/ entries (filesystem-only janitor pass).

    Runs AFTER the pending-pr loop so an iteration which promotes
    pending-pr/<id>/ to done/<id>/ in that loop can also delete the
    leftover current/<id>/ shadow in the same pass.
    """
    from ralph_executor.sweep.reconcile import reconcile_stale_current_all
    from ralph_executor.sweep.types import CurrentReconcileAction

    current_report = reconcile_stale_current_all(ctx)
    for pbi_id, current_action in current_report.actions.items():
        if current_action == CurrentReconcileAction.KEEP_NO_SIBLING:
            log.warning(
                "sweep: current/%s has no sibling in done/blocked/pending-pr "
                "and no PBI.md; leaving for operator review",
                pbi_id,
            )
    for pbi_id, current_err in current_report.errors.items():
        errors.append(f"current/{pbi_id}: reconcile error: {current_err}")


def _process_pbi(*, pbi_dir: Path, ctx: SweepContext) -> PbiActionRecord:
    """Pipeline for one PR-linked PBI: fetch state, decide, track CI, dispatch."""
    pr_id, attempts, sidecar, target_info, snapshot = _fetch_pbi_state(pbi_dir=pbi_dir, ctx=ctx)
    decision = decide_action(
        pr=snapshot,
        attempts=attempts,
        last_seen_comment_ids=sidecar.last_seen_comment_ids,
        last_feedback_round=sidecar.last_feedback_round,
        config=ctx.config,
    )
    sidecar = _track_ci_transition(pbi_dir=pbi_dir, snapshot=snapshot, sidecar=sidecar, ctx=ctx)
    _dispatch(
        pbi_dir=pbi_dir,
        decision=decision,
        snapshot=snapshot,
        sidecar=sidecar,
        ctx=ctx,
        target_info=target_info,
    )
    return PbiActionRecord(
        pbi_id=pbi_dir.name,
        pr_id=pr_id,
        action=decision.action,
        reason=decision.reason,
    )


def _fetch_pbi_state(
    *, pbi_dir: Path, ctx: SweepContext
) -> tuple[int, int, sidecar_state.SweepSidecar, TargetRepoInfo | None, PrSnapshot]:
    """Read the PBI's on-disk state and fetch its live PR snapshot.

    Returns ``(pr_id, attempts, sidecar, target_info, snapshot)``. Raises
    ``_SweepPbiError`` on unreadable/invalid inputs or PR-skill failure so
    the caller's per-PBI isolation handles it.
    """
    pr_id = _read_pr_id(pbi_dir)
    attempts = _read_attempts(pbi_dir)
    sidecar = sidecar_state.load_sidecar(pbi_dir)
    try:
        target_info = read_target_info(pbi_dir)
    except ValueError as exc:
        raise _SweepPbiError(f"invalid target_repo: {exc}") from exc
    sub_env, sub_repo = _per_pbi_subprocess_overrides(target_info, ctx)
    try:
        snapshot = pr_state.fetch(
            pr_id=pr_id,
            skill_scripts_path=ctx.ado_pr_scripts_path,
            repo_name=sub_repo,
            env=sub_env,
        )
    except AdoSkillError as err:
        raise _SweepPbiError(f"PR skill failure: {err}") from err
    return pr_id, attempts, sidecar, target_info, snapshot


def _track_ci_transition(
    *,
    pbi_dir: Path,
    snapshot: PrSnapshot,
    sidecar: sidecar_state.SweepSidecar,
    ctx: SweepContext,
) -> sidecar_state.SweepSidecar:
    """Emit PR_GREEN_THEN_RED and persist terminal CI state into the sidecar.

    Returns the (possibly replaced) sidecar so the caller dispatches with
    the persisted state.
    """
    # Plan 19b Task 3: detect succeeded → failed CI transition and persist
    # the current terminal CI state into the sidecar before dispatch.
    # Persisting only "succeeded" / "failed" (NOT "running" / "none" /
    # "unknown") preserves the last-known terminal state across
    # intermediate ticks — succeeded → running → failed must still emit.
    # The pre-dispatch write means the updated sidecar travels with any
    # subsequent move (MOVE_TO_INBOX_RETRY etc.).
    if sidecar.last_ci_status == "succeeded" and snapshot.ci_status == "failed":
        _emit_pr_green_then_red(
            pbi_id=pbi_dir.name,
            snapshot=snapshot,
            event_log=ctx.event_log,
            now=ctx.config.now,
        )
    if (
        snapshot.ci_status in {"succeeded", "failed"}
        and snapshot.ci_status != sidecar.last_ci_status
    ):
        sidecar = sidecar_state.SweepSidecar(
            last_feedback_sweep=sidecar.last_feedback_sweep,
            last_feedback_round=sidecar.last_feedback_round,
            last_seen_comment_ids=sidecar.last_seen_comment_ids,
            last_ci_status=snapshot.ci_status,
        )
        try:
            sidecar_state.write_sidecar(pbi_dir, sidecar)
        except OSError as exc:
            raise _SweepPbiError(f"failed to write sidecar for {pbi_dir}: {exc}") from exc
    return sidecar


def _read_pr_id(pbi_dir: Path) -> int:
    pr_link = pbi_dir / "PR-LINK.md"
    if not pr_link.is_file():
        raise _SweepPbiError("PR-LINK.md is missing; cannot determine PR id")
    try:
        text = pr_link.read_text(encoding="utf-8")
    except OSError as exc:
        raise _SweepPbiError(f"failed to read PR-LINK.md in {pbi_dir}: {exc}") from exc
    for line in text.splitlines():
        if "PR ID" in line or "PR:" in line:
            match = _PR_ID_RE.search(line)
            if match:
                return int(match.group(1))
    raise _SweepPbiError("PR-LINK.md does not contain a parseable PR id")


def _read_attempts(pbi_dir: Path) -> int:
    for candidate in ("PBI.md", "BUG.md", "FEEDBACK.md"):
        path = pbi_dir / candidate
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise _SweepPbiError(f"failed to read {candidate} in {pbi_dir}: {exc}") from exc
        in_frontmatter = False
        for line in text.splitlines():
            if line.strip() == "---":
                if in_frontmatter:
                    break
                in_frontmatter = True
                continue
            if in_frontmatter and line.startswith("attempts:"):
                try:
                    return int(line.split(":", 1)[1].strip())
                except ValueError:
                    return 0
    return 0
