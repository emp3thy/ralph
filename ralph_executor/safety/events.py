"""Append-only event log backing the cycle detector.

Events are persisted to a SQLite database under
``<repo>/.ralph/state/events.db``. The schema is intentionally small
(one ``events`` table) because every detector rule is a pure function
over events -- the log only has to write fast and read fast.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

_SCHEMA_VERSION: Final[int] = 1
_DB_RELATIVE: Final[str] = ".ralph/state/events.db"


class EventType(StrEnum):
    """The categories of events the cycle detector cares about.

    Names are deliberately verbose so that detector code reads
    naturally. Values are stable strings persisted in the DB; renaming
    would require a migration.
    """

    PBI_OPENED = "pbi.opened"
    PBI_CLOSED = "pbi.closed"
    PBI_BLOCKED = "pbi.blocked"
    PBI_MERGED = "pbi.merged"
    PR_CREATED = "pr.created"
    PR_MERGED = "pr.merged"
    PR_RED = "pr.red"
    PR_GREEN_THEN_RED = "pr.green_then_red"
    FILE_TOUCHED = "file.touched"
    SIGNATURE_OBSERVED = "signature.observed"
    ATTEMPT_INCREMENTED = "attempt.incremented"
    ITERATION_COMPLETED = "iteration.completed"


@dataclass(frozen=True)
class Event:
    """A single row in the event log."""

    kind: EventType
    recorded_at: datetime
    pbi_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.recorded_at.tzinfo is None:
            raise ValueError("Event.recorded_at must be timezone-aware")


class EventLog:
    """Wraps a SQLite connection. Thread-safe via a single lock."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        self._ensure_schema()

    # -- public API ---------------------------------------------------

    def append(self, event: Event) -> None:
        """Persist ``event``. Raises ``ValueError`` for unknown kinds."""
        if not isinstance(event.kind, EventType):
            # Defensive: bare strings can sneak in from JSON-decoded test
            # fixtures.  Reject them explicitly so the failure is local.
            try:
                EventType(event.kind)
            except ValueError as exc:
                raise ValueError(f"unknown EventType: {event.kind!r}") from exc
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (kind, recorded_at, pbi_id, payload) VALUES (?, ?, ?, ?)",
                (
                    event.kind.value if isinstance(event.kind, EventType) else str(event.kind),
                    _utc_isoformat(event.recorded_at),
                    event.pbi_id,
                    json.dumps(event.payload, sort_keys=True),
                ),
            )
            self._conn.commit()

    def recent(self, *, window: timedelta, now: datetime) -> list[Event]:
        """Return events recorded in the half-open interval ``[now-window, now]``.

        Ordered by ``recorded_at ASC`` (oldest first). A zero-width
        window returns only events with ``recorded_at == now``.
        """
        if now.tzinfo is None:
            raise ValueError("recent(now=...) must be timezone-aware")
        since = now - window
        with self._lock:
            cursor = self._conn.execute(
                "SELECT kind, recorded_at, pbi_id, payload"
                " FROM events"
                " WHERE recorded_at >= ? AND recorded_at <= ?"
                " ORDER BY recorded_at ASC, rowid ASC",
                (_utc_isoformat(since), _utc_isoformat(now)),
            )
            rows = cursor.fetchall()
        return [
            Event(
                kind=EventType(row[0]),
                recorded_at=_parse_iso(row[1]),
                pbi_id=row[2],
                payload=json.loads(row[3]) if row[3] else {},
            )
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- internals ----------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "  rowid INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  kind TEXT NOT NULL,"
                "  recorded_at TEXT NOT NULL,"
                "  pbi_id TEXT NOT NULL,"
                "  payload TEXT NOT NULL"
                ")"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_recorded_at ON events (recorded_at)"
            )
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()


def open_log(repo: Path) -> EventLog:
    """Open (or create) the event log under ``<repo>/.ralph/state/events.db``."""
    db_path = repo / _DB_RELATIVE
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")
    return EventLog(conn)


def _utc_isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
