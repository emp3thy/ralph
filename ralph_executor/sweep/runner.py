"""Sweep runner: orchestrate the per-iteration sweep over pending-pr/.

The pure ``decide_action`` function encodes the spec's sweep table and is
exhaustively unit-tested. ``run`` wraps it with I/O: fetching PR state via
the PR skill, moving folders, and writing feedback PBIs.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ralph_executor.sweep import feedback_pbi as feedback_module
from ralph_executor.sweep import pr_state
from ralph_executor.sweep import state as sidecar_state
from ralph_executor.sweep.pr_state import AdoSkillError
from ralph_executor.sweep.types import (
    Action,
    CommentSnapshot,
    Decision,
    PrSnapshot,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepConfig:
    """All non-default parameters the sweep needs.

    ``ralph_author_email`` MUST be non-empty for the sweep to run in
    production; the constructor raises ``ValueError`` if it isn't. Tests
    that don't care about Ralph-authored filtering should pass a clearly
    fictitious value (e.g. ``"ralph-bot@example.com"``).
    """

    ralph_author_email: str
    max_attempts: int
    stale_threshold: timedelta
    now: datetime

    def __post_init__(self) -> None:
        if not self.ralph_author_email:
            raise ValueError("ralph_author_email is required (set RALPH_ADO_AUTHOR_EMAIL)")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.stale_threshold.total_seconds() <= 0:
            raise ValueError("stale_threshold must be a positive timedelta")
        if self.now.tzinfo is None:
            raise ValueError("now must be timezone-aware")


@dataclass(frozen=True)
class SweepContext:
    """I/O context for one sweep invocation.

    The loop driver (Plan 7) constructs this from its own ``LoopContext``
    and passes it in. The split keeps ``run`` testable in isolation.
    """

    queue_root: Path  # the ``.ralph/`` directory in the project repo
    ado_pr_scripts_path: Path  # the staged PR-skill ``scripts/`` directory
    config: SweepConfig


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


class _SweepPbiError(RuntimeError):
    """Raised to skip one PBI without aborting the whole sweep."""


def run(*, ctx: SweepContext) -> SweepResult:
    """Walk pending-pr/ once. Return a structured summary."""
    pending_dir = ctx.queue_root / "pending-pr"
    pbis = _list_pbi_directories(pending_dir)
    actions: list[PbiActionRecord] = []
    errors: list[str] = []

    for pbi_dir in pbis:
        try:
            actions.append(_process_pbi(pbi_dir=pbi_dir, ctx=ctx))
        except _SweepPbiError as err:
            errors.append(f"{pbi_dir.name}: {err}")
            log.warning("sweep error for %s: %s", pbi_dir.name, err)

    return SweepResult(
        pbis_scanned=len(pbis),
        actions=tuple(actions),
        errors=tuple(errors),
    )


def _list_pbi_directories(pending_dir: Path) -> list[Path]:
    if not pending_dir.is_dir():
        return []
    return sorted(p for p in pending_dir.iterdir() if p.is_dir())


def _process_pbi(*, pbi_dir: Path, ctx: SweepContext) -> PbiActionRecord:
    pr_id = _read_pr_id(pbi_dir)
    attempts = _read_attempts(pbi_dir)
    sidecar = sidecar_state.load_sidecar(pbi_dir)
    try:
        snapshot = pr_state.fetch(pr_id=pr_id, skill_scripts_path=ctx.ado_pr_scripts_path)
    except AdoSkillError as err:
        raise _SweepPbiError(f"PR skill failure: {err}") from err

    decision = decide_action(
        pr=snapshot,
        attempts=attempts,
        last_seen_comment_ids=sidecar.last_seen_comment_ids,
        last_feedback_round=sidecar.last_feedback_round,
        config=ctx.config,
    )
    _dispatch(
        pbi_dir=pbi_dir,
        decision=decision,
        snapshot=snapshot,
        sidecar=sidecar,
        ctx=ctx,
    )
    return PbiActionRecord(
        pbi_id=pbi_dir.name,
        pr_id=pr_id,
        action=decision.action,
        reason=decision.reason,
    )


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


def _dispatch(
    *,
    pbi_dir: Path,
    decision: Decision,
    snapshot: PrSnapshot,
    sidecar: sidecar_state.SweepSidecar,
    ctx: SweepContext,
) -> None:
    qr = ctx.queue_root
    a = decision.action
    if a is Action.MOVE_TO_DONE:
        _move_with_history(pbi_dir, qr / "done" / pbi_dir.name, decision.reason, ctx)
    elif a in (Action.MOVE_TO_BLOCKED_ABANDONED, Action.MOVE_TO_BLOCKED_MAX_ATTEMPTS):
        _move_with_history(pbi_dir, qr / "blocked" / pbi_dir.name, decision.reason, ctx)
    elif a is Action.MOVE_TO_INBOX_RETRY:
        _move_with_history(pbi_dir, qr / "inbox" / pbi_dir.name, decision.reason, ctx)
    elif a is Action.CREATE_FEEDBACK_PBI:
        _emit_feedback_pbi(
            pbi_dir=pbi_dir,
            decision=decision,
            snapshot=snapshot,
            sidecar=sidecar,
            ctx=ctx,
        )
    elif a is Action.PING_REVIEWER:
        _append_history(pbi_dir, decision.reason, ctx)
    elif a is Action.NOOP:
        return
    else:  # pragma: no cover — defensive
        raise _SweepPbiError(f"unhandled action: {a}")


def _move_with_history(src: Path, dst: Path, reason: str, ctx: SweepContext) -> None:
    # Stage the move FIRST so a failure (EXDEV cross-device, EACCES
    # permission denied, ENOSPC disk full, …) doesn't leave a spurious
    # "moved" entry in HISTORY.md that contradicts what's on disk.
    # Wrap mkdir + move in _SweepPbiError so the per-PBI isolation in
    # run() catches OSError at every IO step.
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _SweepPbiError(f"failed to create {dst.parent}: {exc}") from exc
    if dst.exists():
        raise _SweepPbiError(f"destination {dst} already exists")
    try:
        shutil.move(str(src), str(dst))
    except OSError as exc:
        raise _SweepPbiError(f"failed to move {src} to {dst}: {exc}") from exc
    # Append history to the NEW location — src no longer exists after
    # a successful move. Original behaviour wrote to src BEFORE move,
    # so on success the entry travelled along; preserve that semantic
    # by writing to dst after the move completes.
    _append_history(dst, reason, ctx)


def _append_history(pbi_dir: Path, reason: str, ctx: SweepContext) -> None:
    # Wrap IO at the source so EVERY call site (PING_REVIEWER dispatch,
    # _move_with_history, _emit_feedback_pbi) gets OSError → _SweepPbiError
    # conversion uniformly. Without this, a disk-full / EACCES /
    # EROFS error from read_text or write_text escapes run()'s
    # per-PBI isolation and aborts the remaining sweep.
    history = pbi_dir / "HISTORY.md"
    line = f"- {ctx.config.now.isoformat()} sweep: {reason}\n"
    try:
        prior = history.read_text(encoding="utf-8") if history.exists() else ""
        history.write_text(prior + line, encoding="utf-8")
    except OSError as exc:
        raise _SweepPbiError(f"failed to append HISTORY.md in {pbi_dir}: {exc}") from exc


def _emit_feedback_pbi(
    *,
    pbi_dir: Path,
    decision: Decision,
    snapshot: PrSnapshot,
    sidecar: sidecar_state.SweepSidecar,
    ctx: SweepContext,
) -> None:
    next_round = sidecar.last_feedback_round + 1
    bundle = feedback_module.render(
        pr=snapshot,
        originating_pbi_id=pbi_dir.name,
        round_number=next_round,
        new_comments=decision.new_comments,
        original_pbi_summary=_read_original_summary(pbi_dir),
        generated_at=ctx.config.now,
    )
    target_dir = ctx.queue_root / "inbox" / bundle.directory_name
    if target_dir.exists():
        raise _SweepPbiError(f"feedback PBI {target_dir} already exists; refusing to overwrite")
    # Wrap the file IO so EXDEV / EACCES / ENOSPC become _SweepPbiError
    # rather than escaping run()'s per-PBI isolation.
    try:
        target_dir.mkdir(parents=True)
        (target_dir / "FEEDBACK.md").write_text(bundle.feedback_md, encoding="utf-8")
        (target_dir / "PR-LINK.md").write_text(bundle.pr_link_md, encoding="utf-8")
        (target_dir / "ORIGINAL.md").write_text(bundle.original_md, encoding="utf-8")
        (target_dir / "HISTORY.md").write_text(bundle.history_md, encoding="utf-8")
    except OSError as exc:
        raise _SweepPbiError(f"failed to write feedback PBI {target_dir}: {exc}") from exc

    new_ids = {f"{c.thread_id}:{c.comment_id}" for c in decision.new_comments}
    # write_sidecar calls tmp.write_text + tmp.replace; both can raise
    # OSError (ENOSPC etc.). Wrap so it's caught per-PBI rather than
    # escaping run() and leaving an inconsistent half-state where the
    # feedback PBI dir was written but the sidecar wasn't (which would
    # make every subsequent sweep try to re-create the same feedback
    # PBI and hit "already exists; refusing to overwrite").
    try:
        sidecar_state.write_sidecar(
            pbi_dir,
            sidecar_state.SweepSidecar(
                last_feedback_sweep=ctx.config.now,
                last_feedback_round=next_round,
                last_seen_comment_ids=sidecar_state.merge_seen_comment_ids(
                    sidecar.last_seen_comment_ids, new_ids
                ),
            ),
        )
    except OSError as exc:
        raise _SweepPbiError(f"failed to write sidecar for {pbi_dir}: {exc}") from exc
    _append_history(pbi_dir, decision.reason, ctx)


def _read_original_summary(pbi_dir: Path) -> str:
    for candidate in ("PBI.md", "BUG.md", "FEEDBACK.md"):
        path = pbi_dir / candidate
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise _SweepPbiError(f"failed to read {candidate} in {pbi_dir}: {exc}") from exc
            # First 40 lines; enough for context, bounded for file size.
            return "\n".join(text.splitlines()[:40])
    return "(no original PBI body found)"
