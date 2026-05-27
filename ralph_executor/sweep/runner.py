"""Sweep runner: orchestrate the per-iteration sweep over pending-pr/.

The pure ``decide_action`` function encodes the spec's sweep table and is
exhaustively unit-tested. ``run`` (Task 6) wraps it with I/O: fetching
PR state via the PR skill, moving folders, and writing feedback PBIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ralph_executor.sweep.types import (
    Action,
    CommentSnapshot,
    Decision,
    PrSnapshot,
)


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
    last_seen_comment_ids: set[str],
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
    last_seen_comment_ids: set[str],
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
# Orchestration (filled in Task 6)
# ----------------------------------------------------------------------


def run(*, ctx: object) -> SweepResult:  # noqa: ARG001 — populated in Task 6
    """Top-level sweep entry point. Implementation lands in Task 6."""
    raise NotImplementedError("sweep.run is implemented in Task 6")


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _pbi_dir_iter(pending_dir: Path) -> list[Path]:
    """Sorted list of immediate child directories of ``pending_dir``."""
    if not pending_dir.is_dir():
        return []
    return sorted(p for p in pending_dir.iterdir() if p.is_dir())
