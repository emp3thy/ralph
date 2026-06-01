"""Tests for the six pure cycle-detector rules."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from ralph_executor.safety.cycle_detector import (
    SignalKind,
    _mean_attempts,
    evaluate_all,
    evaluate_attempt_divergence,
    evaluate_blocked_growth,
    evaluate_regression_cascade,
    evaluate_same_file_thrashing,
    evaluate_signature_recurrence,
    evaluate_whack_a_mole,
)
from ralph_executor.safety.events import Event, EventType
from tests.safety.conftest import make_event, offset


def _events(items: Iterable[Event]) -> list[Event]:
    return sorted(items, key=lambda ev: ev.recorded_at)


# ----------------------------------------------------------------------
# signature_recurrence
# ----------------------------------------------------------------------


class TestSignatureRecurrence:
    def test_trips_when_same_signature_recurs_inside_24h(self, now: datetime) -> None:
        sig = "NullPointerException at handler.py:42"
        events = _events(
            [
                make_event(
                    kind=EventType.SIGNATURE_OBSERVED,
                    recorded_at=offset(now, hours=-23),
                    pbi_id="BUG-1",
                    payload={"signature": sig},
                ),
                make_event(
                    kind=EventType.PBI_CLOSED,
                    recorded_at=offset(now, hours=-22),
                    pbi_id="BUG-1",
                    payload={"signature": sig},
                ),
                make_event(
                    kind=EventType.SIGNATURE_OBSERVED,
                    recorded_at=offset(now, hours=-1),
                    pbi_id="BUG-2",
                    payload={"signature": sig},
                ),
            ]
        )
        signal = evaluate_signature_recurrence(events, now)
        assert signal is not None
        assert signal.kind == SignalKind.SIGNATURE_RECURRENCE
        assert sig in signal.description

    def test_does_not_trip_outside_24h(self, now: datetime) -> None:
        sig = "TimeoutError at client.py:88"
        events = _events(
            [
                make_event(
                    kind=EventType.PBI_CLOSED,
                    recorded_at=offset(now, hours=-30),
                    payload={"signature": sig},
                ),
                make_event(
                    kind=EventType.SIGNATURE_OBSERVED,
                    recorded_at=offset(now, hours=-1),
                    payload={"signature": sig},
                ),
            ]
        )
        assert evaluate_signature_recurrence(events, now) is None

    def test_does_not_trip_when_signatures_differ(self, now: datetime) -> None:
        events = _events(
            [
                make_event(
                    kind=EventType.PBI_CLOSED,
                    recorded_at=offset(now, hours=-2),
                    payload={"signature": "A"},
                ),
                make_event(
                    kind=EventType.SIGNATURE_OBSERVED,
                    recorded_at=offset(now, minutes=-30),
                    payload={"signature": "B"},
                ),
            ]
        )
        assert evaluate_signature_recurrence(events, now) is None


# ----------------------------------------------------------------------
# whack_a_mole
# ----------------------------------------------------------------------


class TestWhackAMole:
    def test_trips_when_close_rate_matches_create_rate(self, now: datetime) -> None:
        # Twelve new bugs and ten closures inside the 4h window -- close/create
        # ratio is 0.83, above the 0.7 threshold; opens hit the 12-open minimum.
        events: list[Event] = []
        for i in range(12):
            events.append(
                make_event(
                    kind=EventType.PBI_OPENED,
                    recorded_at=offset(now, minutes=-(230 - i * 10)),
                    pbi_id=f"BUG-OPEN-{i}",
                )
            )
        for i in range(10):
            events.append(
                make_event(
                    kind=EventType.PBI_CLOSED,
                    recorded_at=offset(now, minutes=-(220 - i * 10)),
                    pbi_id=f"BUG-CLOSE-{i}",
                )
            )
        signal = evaluate_whack_a_mole(_events(events), now)
        assert signal is not None
        assert signal.kind == SignalKind.WHACK_A_MOLE

    def test_does_not_trip_when_only_closes_happen(self, now: datetime) -> None:
        events = _events(
            [
                make_event(
                    kind=EventType.PBI_CLOSED,
                    recorded_at=offset(now, minutes=-60),
                    pbi_id="BUG-1",
                ),
            ]
        )
        assert evaluate_whack_a_mole(events, now) is None

    def test_does_not_trip_below_minimum_volume(self, now: datetime) -> None:
        # One open + one close is below the rule's minimum (>=12 opens).
        events = _events(
            [
                make_event(
                    kind=EventType.PBI_OPENED,
                    recorded_at=offset(now, minutes=-100),
                    pbi_id="BUG-1",
                ),
                make_event(
                    kind=EventType.PBI_CLOSED,
                    recorded_at=offset(now, minutes=-50),
                    pbi_id="BUG-1",
                ),
            ]
        )
        assert evaluate_whack_a_mole(events, now) is None


# ----------------------------------------------------------------------
# same_file_thrashing
# ----------------------------------------------------------------------


class TestSameFileThrashing:
    def test_trips_when_one_file_has_10_PRs_in_24h(self, now: datetime) -> None:
        target = "src/auth/handler.py"
        events: list[Event] = []
        for i in range(10):
            events.append(
                make_event(
                    kind=EventType.PR_CREATED,
                    recorded_at=offset(now, hours=-(20 - i * 2)),
                    pbi_id=f"WI-{i}",
                    payload={"files": [target]},
                )
            )
        signal = evaluate_same_file_thrashing(_events(events), now)
        assert signal is not None
        assert target in signal.description

    def test_does_not_trip_at_9_PRs(self, now: datetime) -> None:
        target = "src/auth/handler.py"
        events: list[Event] = [
            make_event(
                kind=EventType.PR_CREATED,
                recorded_at=offset(now, hours=-(20 - i * 2)),
                pbi_id=f"WI-{i}",
                payload={"files": [target]},
            )
            for i in range(9)
        ]
        assert evaluate_same_file_thrashing(_events(events), now) is None  # default 10 / 24h

    def test_ignores_files_touched_long_ago(self, now: datetime) -> None:
        target = "src/auth/handler.py"
        events: list[Event] = [
            make_event(
                kind=EventType.PR_CREATED,
                recorded_at=offset(now, hours=-(30 + i)),
                pbi_id=f"WI-{i}",
                payload={"files": [target]},
            )
            for i in range(10)
        ]
        assert evaluate_same_file_thrashing(_events(events), now) is None

    def test_threshold_override_lowers_trip_point(self, now: datetime) -> None:
        """A 3-PR set does not trip the default (10) but trips when the
        operator lowers ``min_prs`` via config — the path the loop driver
        takes when it passes ``cfg.same_file_min_prs`` through
        ``evaluate_all``."""
        target = "src/auth/handler.py"
        events = _events(
            [
                make_event(
                    kind=EventType.PR_CREATED,
                    recorded_at=offset(now, hours=-(20 - i * 2)),
                    pbi_id=f"WI-{i}",
                    payload={"files": [target]},
                )
                for i in range(3)
            ]
        )
        # Default threshold (10): no signal.
        assert evaluate_same_file_thrashing(events, now) is None
        # Lowered threshold: trips at 3.
        signal = evaluate_same_file_thrashing(events, now, min_prs=3, window_hours=24.0)
        assert signal is not None
        assert signal.kind == SignalKind.SAME_FILE_THRASHING

    def test_threshold_override_raises_trip_point(self, now: datetime) -> None:
        """The escape hatch the PBI was filed for: a busy 24h with 10
        distinct PRs no longer trips once the operator raises
        ``min_prs`` to a sprint-appropriate ceiling."""
        target = "ralph_executor/loop.py"
        events = _events(
            [
                make_event(
                    kind=EventType.PR_CREATED,
                    recorded_at=offset(now, hours=-(20 - i * 2)),
                    pbi_id=f"WI-{i}",
                    payload={"files": [target]},
                )
                for i in range(10)
            ]
        )
        # Default trips.
        assert evaluate_same_file_thrashing(events, now) is not None
        # Raised threshold: no signal.
        assert evaluate_same_file_thrashing(events, now, min_prs=20, window_hours=24.0) is None

    def test_window_override_narrows_lookback(self, now: datetime) -> None:
        """Shrinking ``window_hours`` to 6 drops the 8 events older than
        6h, so the remaining 2 events are below ``min_prs=10`` and the
        rule does not trip."""
        target = "ralph_executor/loop.py"
        events = _events(
            [
                make_event(
                    kind=EventType.PR_CREATED,
                    recorded_at=offset(now, hours=-(20 - i * 2)),
                    pbi_id=f"WI-{i}",
                    payload={"files": [target]},
                )
                for i in range(10)
            ]
        )
        assert evaluate_same_file_thrashing(events, now) is not None  # 24h default
        assert evaluate_same_file_thrashing(events, now, window_hours=6.0) is None


# ----------------------------------------------------------------------
# regression_cascade
# ----------------------------------------------------------------------


class TestRegressionCascade:
    def test_trips_when_recently_merged_signature_reappears(self, now: datetime) -> None:
        sig = "AssertionError in test_login.py::test_redirect"
        events = _events(
            [
                make_event(
                    kind=EventType.PR_MERGED,
                    recorded_at=offset(now, hours=-12),
                    pbi_id="BUG-old",
                    payload={"signature": sig},
                ),
                make_event(
                    kind=EventType.PR_GREEN_THEN_RED,
                    recorded_at=offset(now, hours=-1),
                    pbi_id="WI-new",
                    payload={"signature": sig},
                ),
            ]
        )
        signal = evaluate_regression_cascade(events, now)
        assert signal is not None
        assert signal.kind == SignalKind.REGRESSION_CASCADE

    def test_no_trip_when_signature_does_not_match(self, now: datetime) -> None:
        events = _events(
            [
                make_event(
                    kind=EventType.PR_MERGED,
                    recorded_at=offset(now, hours=-12),
                    payload={"signature": "A"},
                ),
                make_event(
                    kind=EventType.PR_GREEN_THEN_RED,
                    recorded_at=offset(now, hours=-1),
                    payload={"signature": "B"},
                ),
            ]
        )
        assert evaluate_regression_cascade(events, now) is None


# ----------------------------------------------------------------------
# attempt_divergence
# ----------------------------------------------------------------------


class TestAttemptDivergence:
    def test_trips_when_recent_attempts_average_higher_than_baseline(self, now: datetime) -> None:
        events: list[Event] = []
        # Older window: 10 PBIs averaging ~1 attempt.
        for i in range(10):
            events.append(
                make_event(
                    kind=EventType.ATTEMPT_INCREMENTED,
                    recorded_at=offset(now, hours=-(60 - i)),
                    pbi_id=f"OLD-{i}",
                    payload={"attempts": 1},
                )
            )
        # Recent window: 6 PBIs averaging ~3 attempts.
        for i in range(6):
            events.append(
                make_event(
                    kind=EventType.ATTEMPT_INCREMENTED,
                    recorded_at=offset(now, hours=-(5 - i)),
                    pbi_id=f"NEW-{i}",
                    payload={"attempts": 3},
                )
            )
        signal = evaluate_attempt_divergence(_events(events), now)
        assert signal is not None
        assert signal.kind == SignalKind.ATTEMPT_DIVERGENCE

    def test_no_trip_when_baseline_unknown(self, now: datetime) -> None:
        # Without enough historical events the detector must abstain.
        events = _events(
            [
                make_event(
                    kind=EventType.ATTEMPT_INCREMENTED,
                    recorded_at=offset(now, minutes=-30),
                    payload={"attempts": 5},
                ),
            ]
        )
        assert evaluate_attempt_divergence(events, now) is None

    # ------------------------------------------------------------------
    # Fix A regression: _mean_attempts must use per-PBI peak, not raw
    # ------------------------------------------------------------------

    def test_mean_attempts_uses_per_pbi_peak_not_intermediates(self, now: datetime) -> None:
        # PBI "a" emits cumulative events [1, 2, 3, 4] — peak is 4.
        # PBI "b" emits a single event [1] — peak is 1.
        # Correct mean = (4 + 1) / 2 = 2.5.
        # Old code averaged all 5 raw values → (1+2+3+4+1)/5 = 2.2 (wrong).
        events = [
            make_event(
                kind=EventType.ATTEMPT_INCREMENTED,
                recorded_at=offset(now, hours=-5),
                pbi_id="a",
                payload={"attempts": 1},
            ),
            make_event(
                kind=EventType.ATTEMPT_INCREMENTED,
                recorded_at=offset(now, hours=-4),
                pbi_id="a",
                payload={"attempts": 2},
            ),
            make_event(
                kind=EventType.ATTEMPT_INCREMENTED,
                recorded_at=offset(now, hours=-3),
                pbi_id="a",
                payload={"attempts": 3},
            ),
            make_event(
                kind=EventType.ATTEMPT_INCREMENTED,
                recorded_at=offset(now, hours=-2),
                pbi_id="a",
                payload={"attempts": 4},
            ),
            make_event(
                kind=EventType.ATTEMPT_INCREMENTED,
                recorded_at=offset(now, hours=-1),
                pbi_id="b",
                payload={"attempts": 1},
            ),
        ]
        assert _mean_attempts(events) == 2.5

    # ------------------------------------------------------------------
    # Fix B regression: gates must count distinct PBIs, not events
    # ------------------------------------------------------------------

    def test_single_pbi_with_many_attempts_does_not_trip(self, now: datetime) -> None:
        # One PBI retried 5 times in the recent window — only 1 distinct PBI,
        # below ATTEMPT_RECENT_MIN_PBIS (3). The detector must return None.
        recent_events = [
            make_event(
                kind=EventType.ATTEMPT_INCREMENTED,
                recorded_at=offset(now, hours=-i - 1),
                pbi_id="SINGLE",
                payload={"attempts": i + 1},
            )
            for i in range(5)
        ]
        # Provide enough baseline events (5 distinct PBIs) so only the
        # recent gate is responsible for the None result.
        baseline_events = [
            make_event(
                kind=EventType.ATTEMPT_INCREMENTED,
                recorded_at=offset(now, hours=-(50 + i)),
                pbi_id=f"OLD-{i}",
                payload={"attempts": 1},
            )
            for i in range(5)
        ]
        signal = evaluate_attempt_divergence(_events(recent_events + baseline_events), now)
        assert signal is None


# ----------------------------------------------------------------------
# blocked_growth
# ----------------------------------------------------------------------


class TestBlockedGrowth:
    def test_trips_when_blocks_exceed_closes(self, now: datetime) -> None:
        events: list[Event] = []
        for i in range(6):
            events.append(
                make_event(
                    kind=EventType.PBI_BLOCKED,
                    recorded_at=offset(now, hours=-(20 - i)),
                    pbi_id=f"WI-B{i}",
                )
            )
        for i in range(2):
            events.append(
                make_event(
                    kind=EventType.PBI_MERGED,
                    recorded_at=offset(now, hours=-(20 - i)),
                    pbi_id=f"WI-D{i}",
                )
            )
        signal = evaluate_blocked_growth(_events(events), now)
        assert signal is not None
        assert signal.kind == SignalKind.BLOCKED_GROWTH

    def test_no_trip_when_closes_outpace_blocks(self, now: datetime) -> None:
        events: list[Event] = []
        for i in range(2):
            events.append(
                make_event(
                    kind=EventType.PBI_BLOCKED,
                    recorded_at=offset(now, hours=-(20 - i)),
                    pbi_id=f"WI-B{i}",
                )
            )
        for i in range(8):
            events.append(
                make_event(
                    kind=EventType.PBI_MERGED,
                    recorded_at=offset(now, hours=-(20 - i)),
                    pbi_id=f"WI-D{i}",
                )
            )
        assert evaluate_blocked_growth(_events(events), now) is None


# ----------------------------------------------------------------------
# aggregator
# ----------------------------------------------------------------------


class TestEvaluateAll:
    def test_aggregator_returns_all_tripped_signals(self, now: datetime) -> None:
        target = "src/auth/handler.py"
        sig = "AssertionError in handler.py:42"
        events: list[Event] = []
        # Trip same_file_thrashing.
        for i in range(10):
            events.append(
                make_event(
                    kind=EventType.PR_CREATED,
                    recorded_at=offset(now, hours=-(20 - i * 2)),
                    pbi_id=f"WI-{i}",
                    payload={"files": [target]},
                )
            )
        # Trip signature_recurrence.
        events.append(
            make_event(
                kind=EventType.PBI_CLOSED,
                recorded_at=offset(now, hours=-22),
                pbi_id="BUG-1",
                payload={"signature": sig},
            )
        )
        events.append(
            make_event(
                kind=EventType.SIGNATURE_OBSERVED,
                recorded_at=offset(now, hours=-1),
                pbi_id="BUG-2",
                payload={"signature": sig},
            )
        )
        signals = evaluate_all(_events(events), now)
        kinds = {s.kind for s in signals}
        assert SignalKind.SAME_FILE_THRASHING in kinds
        assert SignalKind.SIGNATURE_RECURRENCE in kinds

    def test_aggregator_returns_empty_when_calm(self, now: datetime) -> None:
        assert evaluate_all([], now) == []

    def test_aggregator_forwards_same_file_cfg_thresholds(self, now: datetime) -> None:
        """``evaluate_all`` reads ``cfg.same_file_min_prs`` and
        ``cfg.same_file_window_hours`` and passes them through to the
        same_file_thrashing detector. With a lowered ``min_prs=3``, three
        PRs are enough to trip; with ``min_prs=20`` the same events stay
        below the floor."""
        # Build a minimal ExecutorConfig — only the same_file fields matter
        # here, the rest are hand-rolled defaults so the dataclass instantiates.
        from ralph_executor.config import ExecutorConfig

        def _cfg(min_prs: int) -> ExecutorConfig:
            return ExecutorConfig(
                queue_repo="https://github.com/example/queue",
                queue_branch="ralph-queue",
                main_branch="main",
                max_attempts=3,
                log_level=20,
                iteration_sleep_seconds=0.0,
                claude_binary="claude",
                claude_permission_mode="bypassPermissions",
                anthropic_api_key="",
                git_host="github",
                gh_owner="",
                ado_org_url="",
                ado_project="",
                halt_webhook="",
                pr_check_poll_max_attempts=1,
                pr_check_poll_interval_seconds=0.1,
                instance_id="test-ralph",
                use_worktrees=True,
                bot_author_email="",
                stale_days=3,
                bash_max_timeout_ms=900_000,
                same_file_min_prs=min_prs,
                same_file_window_hours=24.0,
            )

        target = "ralph_executor/loop.py"
        events = _events(
            [
                make_event(
                    kind=EventType.PR_CREATED,
                    recorded_at=offset(now, hours=-(20 - i * 2)),
                    pbi_id=f"WI-{i}",
                    payload={"files": [target]},
                )
                for i in range(3)
            ]
        )
        # Lowered cfg threshold trips.
        signals_low = evaluate_all(events, now, _cfg(min_prs=3))
        assert any(s.kind == SignalKind.SAME_FILE_THRASHING for s in signals_low)
        # Raised cfg threshold suppresses.
        signals_high = evaluate_all(events, now, _cfg(min_prs=20))
        assert not any(s.kind == SignalKind.SAME_FILE_THRASHING for s in signals_high)


# UTC import used indirectly via conftest fixtures -- suppress unused warning
_ = UTC
