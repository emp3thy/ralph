"""Tests for the append-only event log."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.safety.events import (
    Event,
    EventLog,
    EventType,
    open_log,
)
from tests.safety.conftest import make_event, offset


def test_open_log_creates_state_dir_and_db(tmp_path: Path) -> None:
    # Note: no ``.ralph/state`` directory pre-created here; ``open_log``
    # must create it idempotently.
    (tmp_path / ".ralph").mkdir()
    log = open_log(tmp_path)
    assert (tmp_path / ".ralph" / "state" / "events.db").is_file()
    log.close()


def test_schema_initialisation_is_idempotent(repo_dir: Path) -> None:
    open_log(repo_dir).close()
    open_log(repo_dir).close()  # second open must not raise
    open_log(repo_dir).close()


def test_append_and_retrieve_single_event(event_log: EventLog, now: datetime) -> None:
    event = make_event(
        kind=EventType.PBI_OPENED,
        recorded_at=now,
        pbi_id="WI-42",
        payload={"severity": "high"},
    )
    event_log.append(event)
    events = event_log.recent(window=timedelta(hours=1), now=now)
    assert len(events) == 1
    assert events[0].kind == EventType.PBI_OPENED
    assert events[0].pbi_id == "WI-42"
    assert events[0].payload == {"severity": "high"}


def test_recent_filters_by_window(event_log: EventLog, now: datetime) -> None:
    event_log.append(make_event(kind=EventType.PBI_OPENED, recorded_at=offset(now, hours=-2)))
    event_log.append(make_event(kind=EventType.PBI_CLOSED, recorded_at=offset(now, minutes=-30)))
    recent_hour = event_log.recent(window=timedelta(hours=1), now=now)
    assert {ev.kind for ev in recent_hour} == {EventType.PBI_CLOSED}
    recent_three_hours = event_log.recent(window=timedelta(hours=3), now=now)
    assert {ev.kind for ev in recent_three_hours} == {
        EventType.PBI_OPENED,
        EventType.PBI_CLOSED,
    }


def test_recent_orders_by_recorded_at_ascending(event_log: EventLog, now: datetime) -> None:
    event_log.append(
        make_event(
            kind=EventType.PBI_CLOSED,
            recorded_at=offset(now, minutes=-10),
            pbi_id="WI-2",
        )
    )
    event_log.append(
        make_event(
            kind=EventType.PBI_OPENED,
            recorded_at=offset(now, minutes=-30),
            pbi_id="WI-1",
        )
    )
    events = event_log.recent(window=timedelta(hours=1), now=now)
    assert [ev.pbi_id for ev in events] == ["WI-1", "WI-2"]


def test_log_is_durable_across_reopen(repo_dir: Path, now: datetime) -> None:
    log = open_log(repo_dir)
    log.append(make_event(kind=EventType.PBI_OPENED, recorded_at=now))
    log.close()

    log2 = open_log(repo_dir)
    try:
        events = log2.recent(window=timedelta(hours=1), now=now)
        assert len(events) == 1
    finally:
        log2.close()


def test_payload_round_trips_complex_json(event_log: EventLog, now: datetime) -> None:
    payload = {
        "signature": "TypeError at handler.py:42",
        "files": ["a.py", "b.py"],
        "attempts": 3,
        "nested": {"k": "v"},
    }
    event_log.append(
        make_event(kind=EventType.SIGNATURE_OBSERVED, recorded_at=now, payload=payload)
    )
    events = event_log.recent(window=timedelta(hours=1), now=now)
    assert events[0].payload == payload


def test_event_kind_is_validated_at_append(event_log: EventLog, now: datetime) -> None:
    # mypy strict won't allow a non-EventType here, but at runtime the
    # log must still reject obviously malformed events. We construct a
    # synthetic Event with a string kind to assert the runtime guard.
    bogus = Event(
        kind="not-a-real-kind",  # type: ignore[arg-type]
        recorded_at=now,
        pbi_id="WI-1",
        payload={},
    )
    with pytest.raises(ValueError, match="unknown EventType"):
        event_log.append(bogus)


def test_recent_with_zero_window_returns_only_strict_present(
    event_log: EventLog, now: datetime
) -> None:
    event_log.append(make_event(kind=EventType.PBI_OPENED, recorded_at=now))
    event_log.append(
        make_event(kind=EventType.PBI_CLOSED, recorded_at=offset(now, microseconds=-1))
    )
    events = event_log.recent(window=timedelta(0), now=now)
    assert [ev.kind for ev in events] == [EventType.PBI_OPENED]
