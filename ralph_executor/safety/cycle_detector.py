"""Layer 3 safety: pure-function cycle-detection rules.

Each rule is a function ``evaluate_<name>(events, now) -> CycleSignal | None``
that takes a pre-loaded list of events and a "now" timestamp. Detectors
do NOT touch the filesystem, the database, or the network -- the I/O
happens in the loop driver (it reads events, calls the detectors, and
writes the META-BUG via ``halt.halt_and_acknowledge``).

Thresholds are defined as module-level constants so they can be tuned
without touching detector bodies. They mirror the v1 column in the spec's
"Layer 3" table.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from ralph_executor.safety.events import Event, EventType

# ----------------------------------------------------------------------
# Thresholds (tunable; mirror the spec's "Layer 3" table)
# ----------------------------------------------------------------------

SIGNATURE_RECURRENCE_WINDOW = timedelta(hours=24)
WHACK_A_MOLE_WINDOW = timedelta(hours=4)
WHACK_A_MOLE_MIN_OPENS = 3
WHACK_A_MOLE_RATIO_THRESHOLD = 0.7
SAME_FILE_WINDOW = timedelta(hours=24)
SAME_FILE_MIN_PRS = 6
REGRESSION_WINDOW = timedelta(hours=72)
ATTEMPT_BASELINE_WINDOW = timedelta(hours=72)
ATTEMPT_RECENT_WINDOW = timedelta(hours=12)
ATTEMPT_RECENT_MIN_PBIS = 3
ATTEMPT_BASELINE_MIN_PBIS = 5
ATTEMPT_DIVERGENCE_DELTA = 1.5
BLOCKED_GROWTH_WINDOW = timedelta(hours=24)
BLOCKED_GROWTH_MIN_BLOCKS = 5
BLOCKED_GROWTH_RATIO_THRESHOLD = 1.5


# ----------------------------------------------------------------------
# Public dataclasses + enums
# ----------------------------------------------------------------------


class SignalKind(StrEnum):
    SIGNATURE_RECURRENCE = "signature_recurrence"
    WHACK_A_MOLE = "whack_a_mole"
    SAME_FILE_THRASHING = "same_file_thrashing"
    REGRESSION_CASCADE = "regression_cascade"
    ATTEMPT_DIVERGENCE = "attempt_divergence"
    BLOCKED_GROWTH = "blocked_growth"


@dataclass(frozen=True)
class CycleSignal:
    kind: SignalKind
    description: str
    severity: str = "critical"
    supporting_events: tuple[Event, ...] = field(default_factory=tuple)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _within(window: timedelta, now: datetime, ev: Event) -> bool:
    return (now - ev.recorded_at) <= window and ev.recorded_at <= now


def _filter(events: Iterable[Event], *, kinds: set[EventType]) -> list[Event]:
    return [ev for ev in events if ev.kind in kinds]


def _signature(ev: Event) -> str | None:
    sig = ev.payload.get("signature")
    return str(sig) if isinstance(sig, str) and sig else None


def _files(ev: Event) -> list[str]:
    raw = ev.payload.get("files", [])
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str)]


def _attempts(ev: Event) -> int | None:
    raw = ev.payload.get("attempts")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return None


# ----------------------------------------------------------------------
# Rule 1 -- signature_recurrence
# ----------------------------------------------------------------------


def evaluate_signature_recurrence(events: list[Event], now: datetime) -> CycleSignal | None:
    """Trip when a closed PBI's signature reappears inside 24h.

    "Closed" here is approximated as ``PBI_CLOSED`` or ``PR_MERGED``;
    "reappears" is a later ``SIGNATURE_OBSERVED`` or
    ``PR_GREEN_THEN_RED`` carrying the same ``payload['signature']``.
    """
    window = SIGNATURE_RECURRENCE_WINDOW
    closed = [
        ev
        for ev in events
        if ev.kind in {EventType.PBI_CLOSED, EventType.PR_MERGED}
        and _within(window, now, ev)
        and _signature(ev) is not None
    ]
    observed_after = [
        ev
        for ev in events
        if ev.kind in {EventType.SIGNATURE_OBSERVED, EventType.PR_GREEN_THEN_RED}
        and _within(window, now, ev)
        and _signature(ev) is not None
    ]
    for close_ev in closed:
        sig = _signature(close_ev)
        for obs_ev in observed_after:
            if obs_ev.recorded_at <= close_ev.recorded_at:
                continue
            if _signature(obs_ev) == sig:
                return CycleSignal(
                    kind=SignalKind.SIGNATURE_RECURRENCE,
                    description=(
                        f"signature reappeared within 24h: {sig!r} "
                        f"(closed {close_ev.recorded_at.isoformat()}, "
                        f"reobserved {obs_ev.recorded_at.isoformat()})"
                    ),
                    supporting_events=(close_ev, obs_ev),
                )
    return None


# ----------------------------------------------------------------------
# Rule 2 -- whack_a_mole
# ----------------------------------------------------------------------


def evaluate_whack_a_mole(events: list[Event], now: datetime) -> CycleSignal | None:
    """Trip when close-rate is close to create-rate over a 4h window."""
    window = WHACK_A_MOLE_WINDOW
    opens = [ev for ev in events if ev.kind == EventType.PBI_OPENED and _within(window, now, ev)]
    closes = [
        ev
        for ev in events
        if ev.kind in {EventType.PBI_CLOSED, EventType.PR_MERGED} and _within(window, now, ev)
    ]
    if len(opens) < WHACK_A_MOLE_MIN_OPENS:
        return None
    ratio = len(closes) / max(len(opens), 1)
    if ratio < WHACK_A_MOLE_RATIO_THRESHOLD:
        return None
    return CycleSignal(
        kind=SignalKind.WHACK_A_MOLE,
        description=(
            f"close/create ratio is {ratio:.2f} over the last "
            f"{int(window.total_seconds() // 3600)}h "
            f"({len(closes)} closes vs {len(opens)} opens) -- "
            f"each fix is followed by a new failure"
        ),
        supporting_events=tuple(opens[:3] + closes[:3]),
    )


# ----------------------------------------------------------------------
# Rule 3 -- same_file_thrashing
# ----------------------------------------------------------------------


def evaluate_same_file_thrashing(events: list[Event], now: datetime) -> CycleSignal | None:
    """Trip when one file is touched by ``SAME_FILE_MIN_PRS`` distinct PRs in 24h."""
    window = SAME_FILE_WINDOW
    pr_events = [
        ev for ev in events if ev.kind == EventType.PR_CREATED and _within(window, now, ev)
    ]
    hits: dict[str, list[Event]] = defaultdict(list)
    for ev in pr_events:
        for path in _files(ev):
            hits[path].append(ev)
    for path, evs in hits.items():
        # Distinct PBIs (a single PBI might re-push the same file).
        distinct_pbis = {ev.pbi_id for ev in evs}
        if len(distinct_pbis) >= SAME_FILE_MIN_PRS:
            return CycleSignal(
                kind=SignalKind.SAME_FILE_THRASHING,
                description=(
                    f"{path!r} modified via {len(distinct_pbis)} distinct "
                    f"PBIs in the last "
                    f"{int(window.total_seconds() // 3600)}h -- "
                    f"likely wrong abstraction"
                ),
                supporting_events=tuple(evs[:5]),
            )
    return None


# ----------------------------------------------------------------------
# Rule 4 -- regression_cascade
# ----------------------------------------------------------------------


def evaluate_regression_cascade(events: list[Event], now: datetime) -> CycleSignal | None:
    """Trip when a previously-green-then-red failure matches a recent merge's signature."""
    window = REGRESSION_WINDOW
    merges = [
        ev
        for ev in events
        if ev.kind == EventType.PR_MERGED
        and _within(window, now, ev)
        and _signature(ev) is not None
    ]
    regressions = [
        ev
        for ev in events
        if ev.kind == EventType.PR_GREEN_THEN_RED
        and _within(window, now, ev)
        and _signature(ev) is not None
    ]
    for merge_ev in merges:
        merge_sig = _signature(merge_ev)
        for reg_ev in regressions:
            if reg_ev.recorded_at <= merge_ev.recorded_at:
                continue
            if _signature(reg_ev) == merge_sig:
                return CycleSignal(
                    kind=SignalKind.REGRESSION_CASCADE,
                    description=(
                        f"regression matches recently-merged fix: {merge_sig!r} "
                        f"(merged {merge_ev.recorded_at.isoformat()}, "
                        f"re-failed {reg_ev.recorded_at.isoformat()})"
                    ),
                    supporting_events=(merge_ev, reg_ev),
                )
    return None


# ----------------------------------------------------------------------
# Rule 5 -- attempt_divergence
# ----------------------------------------------------------------------


def evaluate_attempt_divergence(events: list[Event], now: datetime) -> CycleSignal | None:
    """Trip when recent average attempts/PBI is materially above baseline."""
    attempt_events = [ev for ev in events if ev.kind == EventType.ATTEMPT_INCREMENTED]
    recent = [ev for ev in attempt_events if _within(ATTEMPT_RECENT_WINDOW, now, ev)]
    baseline = [
        ev
        for ev in attempt_events
        if _within(ATTEMPT_BASELINE_WINDOW, now, ev) and ev not in recent
    ]
    if len({ev.pbi_id for ev in recent if ev.pbi_id}) < ATTEMPT_RECENT_MIN_PBIS:
        return None
    if len({ev.pbi_id for ev in baseline if ev.pbi_id}) < ATTEMPT_BASELINE_MIN_PBIS:
        return None
    recent_avg = _mean_attempts(recent)
    baseline_avg = _mean_attempts(baseline)
    delta = recent_avg - baseline_avg
    if delta < ATTEMPT_DIVERGENCE_DELTA:
        return None
    return CycleSignal(
        kind=SignalKind.ATTEMPT_DIVERGENCE,
        description=(
            f"average attempts/PBI rising: "
            f"recent {recent_avg:.2f} vs baseline {baseline_avg:.2f} "
            f"(delta {delta:.2f}, threshold {ATTEMPT_DIVERGENCE_DELTA})"
        ),
        supporting_events=tuple(recent[:5]),
    )


def _mean_attempts(events: list[Event]) -> float:
    """Mean of each PBI's PEAK attempt count.

    Each ATTEMPT_INCREMENTED event records the cumulative count at attempt
    time, so events for one PBI look like [1, 2, ..., N]. Averaging those
    raw values systematically deflates the per-PBI mean (it becomes (N+1)/2
    instead of N). Reduce to one value per PBI (its peak) before averaging.
    """
    if not events:
        return 0.0
    peaks: dict[str, int] = {}
    for ev in events:
        pid = ev.pbi_id or ""
        if not pid:
            continue
        a = _attempts(ev)
        if a is not None and a > peaks.get(pid, 0):
            peaks[pid] = a
    if not peaks:
        return 0.0
    return sum(peaks.values()) / len(peaks)


# ----------------------------------------------------------------------
# Rule 6 -- blocked_growth
# ----------------------------------------------------------------------


def evaluate_blocked_growth(events: list[Event], now: datetime) -> CycleSignal | None:
    """Trip when ``blocked/`` growth exceeds ``done/`` growth over 24h."""
    window = BLOCKED_GROWTH_WINDOW
    blocks = [ev for ev in events if ev.kind == EventType.PBI_BLOCKED and _within(window, now, ev)]
    closes = [
        ev
        for ev in events
        if ev.kind in {EventType.PBI_MERGED, EventType.PBI_CLOSED} and _within(window, now, ev)
    ]
    if len(blocks) < BLOCKED_GROWTH_MIN_BLOCKS:
        return None
    ratio = len(blocks) / max(len(closes), 1)
    if ratio < BLOCKED_GROWTH_RATIO_THRESHOLD:
        return None
    return CycleSignal(
        kind=SignalKind.BLOCKED_GROWTH,
        description=(
            f"blocked/ growing faster than done/: "
            f"{len(blocks)} blocks vs {len(closes)} closes over the last "
            f"{int(window.total_seconds() // 3600)}h "
            f"(ratio {ratio:.2f})"
        ),
        supporting_events=tuple(blocks[:5]),
    )


# ----------------------------------------------------------------------
# Aggregator
# ----------------------------------------------------------------------

_ALL_DETECTORS = (
    evaluate_signature_recurrence,
    evaluate_whack_a_mole,
    evaluate_same_file_thrashing,
    evaluate_regression_cascade,
    evaluate_attempt_divergence,
    evaluate_blocked_growth,
)


def evaluate_all(events: list[Event], now: datetime) -> list[CycleSignal]:
    """Run every detector and return every tripped signal.

    The order of returned signals mirrors the order of detectors above.
    """
    results: list[CycleSignal] = []
    for detector in _ALL_DETECTORS:
        signal = detector(events, now)
        if signal is not None:
            results.append(signal)
    return results
