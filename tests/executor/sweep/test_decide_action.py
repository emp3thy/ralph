"""Tests for ``ralph_executor.sweep.runner.decide_action``.

Every row of the spec's "Sweep logic per pending PR" table maps to one or
more tests below. The function under test is pure: it takes a
``PrSnapshot``, the originating PBI's attempt count, and configuration,
and returns a ``Decision``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ralph_executor.sweep.runner import (
    SweepConfig,
    _new_active_human_comments,
    decide_action,
)
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
    merge_state: str = "",
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
        merge_state=merge_state,
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


# Precedence interactions: terminal statuses and CI win over later checks.
def test_abandoned_with_new_comments_still_blocked_abandoned() -> None:
    human = _comment(author=HUMAN_EMAIL, thread_id=60, comment_id=701)
    pr = _snapshot(
        pr_status="abandoned",
        ci_status="succeeded",
        threads=(_thread(thread_id=60, status="active", comments=(human,)),),
    )
    assert _decide(pr).action is Action.MOVE_TO_BLOCKED_ABANDONED


def test_unknown_status_with_new_comments_is_noop() -> None:
    human = _comment(author=HUMAN_EMAIL, thread_id=61, comment_id=702)
    pr = _snapshot(
        pr_status="unknown",
        ci_status="succeeded",
        threads=(_thread(thread_id=61, status="active", comments=(human,)),),
    )
    assert _decide(pr).action is Action.NOOP


def test_ci_failed_preempts_auto_merge() -> None:
    pr = _snapshot(ci_status="failed", merge_state="clean")
    decision = _decide(pr, config=_config(auto_merge_clean_prs=True))
    assert decision.action is Action.MOVE_TO_INBOX_RETRY


def test_ci_failed_preempts_new_comments() -> None:
    human = _comment(author=HUMAN_EMAIL, thread_id=62, comment_id=703)
    pr = _snapshot(
        ci_status="failed",
        threads=(_thread(thread_id=62, status="active", comments=(human,)),),
    )
    assert _decide(pr, attempts=1).action is Action.MOVE_TO_INBOX_RETRY


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


def test_feedback_decision_carries_exact_new_comments() -> None:
    first = _comment(author=HUMAN_EMAIL, thread_id=63, comment_id=704)
    second = _comment(author=HUMAN_EMAIL, thread_id=64, comment_id=705)
    pr = _snapshot(
        pr_status="active",
        ci_status="succeeded",
        threads=(
            _thread(thread_id=63, status="active", comments=(first,)),
            _thread(thread_id=64, status="active", comments=(second,)),
        ),
    )
    decision = _decide(pr)
    assert decision.action is Action.CREATE_FEEDBACK_PBI
    assert decision.new_comments == (first, second)


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


def test_stale_threshold_boundary_is_inclusive() -> None:
    threshold = timedelta(days=3)
    config = _config(stale_threshold=threshold)

    exactly_at = _snapshot(threads=(), last_activity_at=NOW - threshold)
    assert _decide(exactly_at, config=config).action is Action.PING_REVIEWER

    one_second_under = _snapshot(
        threads=(),
        last_activity_at=NOW - (threshold - timedelta(seconds=1)),
    )
    assert _decide(one_second_under, config=config).action is Action.NOOP


# SweepConfig validation.
def test_missing_ralph_author_email_raises() -> None:
    with pytest.raises(ValueError, match="ralph_author_email"):
        SweepConfig(
            ralph_author_email="",
            max_attempts=3,
            stale_threshold=timedelta(days=3),
            now=NOW,
        )


# Auto-merge clean PRs: gated by SweepConfig.auto_merge_clean_prs.
def test_flag_off_clean_is_noop() -> None:
    pr = _snapshot(merge_state="clean")
    decision = _decide(pr, config=_config(auto_merge_clean_prs=False))
    assert decision.action is Action.NOOP


def test_flag_on_clean_yields_merge_pr() -> None:
    pr = _snapshot(merge_state="clean")
    decision = _decide(pr, config=_config(auto_merge_clean_prs=True))
    assert decision.action is Action.MERGE_PR
    assert "auto-merging" in decision.reason.lower()


def test_flag_on_dirty_is_not_merge_pr() -> None:
    pr = _snapshot(merge_state="dirty")
    decision = _decide(pr, config=_config(auto_merge_clean_prs=True))
    assert decision.action is not Action.MERGE_PR


@pytest.mark.parametrize("merge_state", ["unstable", "CLEAN"])
def test_flag_on_non_clean_merge_state_is_not_merge_pr(merge_state: str) -> None:
    # merge_state is matched exactly against lowercase "clean".
    pr = _snapshot(merge_state=merge_state)
    decision = _decide(pr, config=_config(auto_merge_clean_prs=True))
    assert decision.action is not Action.MERGE_PR


def test_new_comments_preempt_merge_pr() -> None:
    human = _comment(author=HUMAN_EMAIL, thread_id=50, comment_id=601)
    pr = _snapshot(
        merge_state="clean",
        threads=(_thread(thread_id=50, status="active", comments=(human,)),),
    )
    decision = _decide(pr, config=_config(auto_merge_clean_prs=True))
    assert decision.action is Action.CREATE_FEEDBACK_PBI


# Direct tests for the _new_active_human_comments filter.
@pytest.mark.parametrize(
    "config_email,comment_email",
    [
        ("RALPH@X.COM", "ralph@x.com"),
        ("ralph@x.com", "RALPH@X.COM"),
    ],
)
def test_filter_author_email_match_is_case_insensitive(
    config_email: str,
    comment_email: str,
) -> None:
    ralph = _comment(author=comment_email, thread_id=70, comment_id=801)
    pr = _snapshot(
        threads=(_thread(thread_id=70, status="active", comments=(ralph,)),),
    )
    result = _new_active_human_comments(
        pr=pr,
        last_seen_comment_ids=set(),
        ralph_author_email=config_email,
    )
    assert result == ()


@pytest.mark.parametrize(
    "status",
    ["pending", "fixed", "closed", "wontFix", "byDesign", "unknown"],
)
def test_filter_excludes_every_non_active_thread_status(status: str) -> None:
    human = _comment(author=HUMAN_EMAIL, thread_id=71, comment_id=802)
    pr = _snapshot(
        threads=(_thread(thread_id=71, status=status, comments=(human,)),),
    )
    result = _new_active_human_comments(
        pr=pr,
        last_seen_comment_ids=set(),
        ralph_author_email=RALPH_EMAIL,
    )
    assert result == ()


def test_filter_mixed_seen_unseen_returns_only_unseen_in_order() -> None:
    first = _comment(author=HUMAN_EMAIL, thread_id=72, comment_id=803)
    seen = _comment(author=HUMAN_EMAIL, thread_id=72, comment_id=804)
    last = _comment(author=HUMAN_EMAIL, thread_id=72, comment_id=805)
    pr = _snapshot(
        threads=(_thread(thread_id=72, status="active", comments=(first, seen, last)),),
    )
    result = _new_active_human_comments(
        pr=pr,
        last_seen_comment_ids={"72:804"},
        ralph_author_email=RALPH_EMAIL,
    )
    assert result == (first, last)
