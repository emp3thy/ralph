"""AUTOBUG_EMITTED EventType variant round-trips through the event log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ralph_executor.safety.events import Event, EventType, open_log


def test_autobug_emitted_event_round_trips(tmp_path: Path) -> None:
    log = open_log(tmp_path)
    recorded_at = datetime(2026, 5, 31, tzinfo=UTC)
    event = Event(
        kind=EventType.AUTOBUG_EMITTED,
        recorded_at=recorded_at,
        pbi_id="autobug-abc-001",
        payload={"signature": "abc" + "0" * 61, "trigger_kind": "python_crash"},
    )
    log.append(event)
    events = log.recent(
        window=timedelta(hours=1),
        now=datetime(2026, 5, 31, 1, tzinfo=UTC),
    )
    log.close()
    assert events
    assert events[0].kind == EventType.AUTOBUG_EMITTED
    assert events[0].payload["signature"].startswith("abc")
    assert events[0].payload["trigger_kind"] == "python_crash"
