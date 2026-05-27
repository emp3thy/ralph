"""Tests for ``ralph_executor.sweep.runner.decide_action``.

Every row of the spec's "Sweep logic per pending PR" table maps to one or
more tests below. The function under test is pure: it takes a
``PrSnapshot``, the originating PBI's attempt count, and configuration,
and returns a ``Decision``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ralph_executor.sweep.runner import SweepConfig, decide_action
from ralph_executor.sweep.types import (
    Action,
    CommentSnapshot,
    Decision,
    PrSnapshot,
    ThreadSnapshot,
)

RALPH_EMAIL = "ralph-bot@example.com"
HUMAN_EMAIL = "reviewer@example.com"
NOW = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)


def _config(**overrides: object) -> SweepConfig:
    defaults: dict[str, object] = {
        "ralph_author_email": RALPH_EMAIL,
        "max_attempts": 3,
        "stale_threshold": timedelta(days=3),
        "now": NOW,
    }
    defaults.update(overrides)
    return SweepConfig(**defaults)  # type: ignore[arg-type]


def _snapshot(
    *,
    pr_status: str = "active",
    ci_status: str = "succeeded",
    threads: tuple[ThreadSnapshot, ...] = (),
    last_activity_at: datetime | None = None,
) -> PrSnapshot:
    return PrSnapshot(
        pr_id=100,
        title="WI-1234 — Add /healthz endpoint",
        pr_status=pr_status,  # type: ignore[arg-type]
        ci_status=ci_status,  # type: ignore[arg-type]
        source_branch="ralph/WI-1234",
        target_branch="main",
        reviewers=(HUMAN_EMAIL,),
        threads=threads,
        last_activity_at=last_activity_at or NOW - timedelta(hours=1),
        url="https://dev.azure.com/example-org/_git/svc/pullrequest/100",
    )


def _comment(
    *,
    author: str = HUMAN_EMAIL,
    thread_id: int = 1,
    comment_id: int = 1,
    posted_at: datetime | None = None,
    text: str = "Please rename this variable.",
) -> CommentSnapshot:
    return CommentSnapshot(
        thread_id=thread_id,
        comment_id=comment_id,
        author_email=author,
        posted_at=posted_at or NOW - timedelta(hours=2),
        text=text,
        file_path="src/app.py" if comment_id else None,
        line=42 if comment_id else None,
    )


def _thread(
    *,
    thread_id: int = 1,
    status: str = "active",
    comments: tuple[CommentSnapshot, ...] | None = None,
) -> ThreadSnapshot:
    return ThreadSnapshot(
        thread_id=thread_id,
        status=status,  # type: ignore[arg-type]
        file_path="src/app.py",
        line=42,
        comments=comments or (_comment(thread_id=thread_id),),
    )


def _decide(
    pr: PrSnapshot,
    *,
    attempts: int = 1,
    last_seen_comment_ids: set[str] | None = None,
    config: SweepConfig | None = None,
) -> Decision:
    return decide_action(
        pr=pr,
        attempts=attempts,
        last_seen_comment_ids=last_seen_comment_ids or set(),
        last_feedback_round=0,
        config=config or _config(),
    )


# Rows 1-4 of the spec table: pure pr_status / ci_status / attempts dispatch.
@pytest.mark.parametrize(
    "pr_status,ci_status,attempts,expected_action,reason_substr",
    [
        ("completed", "succeeded", 1, Action.MOVE_TO_DONE, "merged"),
        ("abandoned", "succeeded", 1, Action.MOVE_TO_BLOCKED_ABANDONED, "abandon"),
        ("active", "failed", 1, Action.MOVE_TO_INBOX_RETRY, "ci"),
        ("active", "failed", 3, Action.MOVE_TO_BLOCKED_MAX_ATTEMPTS, "max"),
        ("active", "succeeded", 1, Action.NOOP, "awaiting review"),
        ("unknown", "unknown", 1, Action.NOOP, "unknown"),
    ],
)
def test_terminal_dispatch_table(
    pr_status: str,
    ci_status: str,
    attempts: int,
    expected_action: Action,
    reason_substr: str,
) -> None:
    pr = _snapshot(pr_status=pr_status, ci_status=ci_status, threads=())
    decision = _decide(pr, attempts=attempts)
    assert decision.action is expected_action
    assert reason_substr in decision.reason.lower()


# Row 5: open + new active human comments → create feedback PBI.
def test_new_active_human_comments_yield_feedback_pbi() -> None:
    human = _comment(author=HUMAN_EMAIL, thread_id=11, comment_id=101)
    pr = _snapshot(
        pr_status="active",
        ci_status="succeeded",
        threads=(_thread(thread_id=11, status="active", comments=(human,)),),
    )
    decision = _decide(pr)
    assert decision.action is Action.CREATE_FEEDBACK_PBI
    assert decision.new_comments == (human,)


def test_ralph_authored_and_non_active_threads_are_filtered() -> None:
    ralph = _comment(author=RALPH_EMAIL, thread_id=12, comment_id=201)
    fixed = _comment(author=HUMAN_EMAIL, thread_id=30, comment_id=401)
    pr = _snapshot(
        pr_status="active",
        ci_status="succeeded",
        threads=(
            _thread(thread_id=12, status="active", comments=(ralph,)),
            _thread(thread_id=30, status="fixed", comments=(fixed,)),
        ),
    )
    assert _decide(pr).action is Action.NOOP


def test_already_seen_comments_are_excluded_from_new_set() -> None:
    seen = _comment(author=HUMAN_EMAIL, thread_id=20, comment_id=301)
    fresh = _comment(author=HUMAN_EMAIL, thread_id=21, comment_id=302)
    pr = _snapshot(
        pr_status="active",
        ci_status="succeeded",
        threads=(
            _thread(thread_id=20, status="active", comments=(seen,)),
            _thread(thread_id=21, status="active", comments=(fresh,)),
        ),
    )
    decision = _decide(pr, last_seen_comment_ids={"20:301"})
    assert decision.action is Action.CREATE_FEEDBACK_PBI
    assert decision.new_comments == (fresh,)


# Row 6: stale → ping reviewer (unless new comments take priority).
def test_stale_yields_ping_reviewer_when_no_new_comments() -> None:
    pr = _snapshot(
        pr_status="active",
        ci_status="succeeded",
        threads=(),
        last_activity_at=NOW - timedelta(days=5),
    )
    decision = _decide(pr, config=_config(stale_threshold=timedelta(days=3)))
    assert decision.action is Action.PING_REVIEWER
    assert "stale" in decision.reason.lower()


def test_stale_with_new_comments_prefers_feedback_pbi() -> None:
    # New comments are why the PR has movement to address.
    fresh = _comment(author=HUMAN_EMAIL, thread_id=40, comment_id=501)
    pr = _snapshot(
        pr_status="active",
        ci_status="succeeded",
        threads=(_thread(thread_id=40, status="active", comments=(fresh,)),),
        last_activity_at=NOW - timedelta(days=5),
    )
    decision = _decide(pr, config=_config(stale_threshold=timedelta(days=3)))
    assert decision.action is Action.CREATE_FEEDBACK_PBI


# SweepConfig validation.
def test_missing_ralph_author_email_raises() -> None:
    with pytest.raises(ValueError, match="ralph_author_email"):
        SweepConfig(
            ralph_author_email="",
            max_attempts=3,
            stale_threshold=timedelta(days=3),
            now=NOW,
        )
