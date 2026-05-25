# Safety Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan extends Plan 7 (`2026-05-24-07-executor-core.md`); do NOT begin until Plan 7's verification gate is green.

**Goal:** Implement Layer 1 (Ralph self-halt via STUCK.md) and Layer 3 (global cycle detection) of the spec's [Safety controls](../specs/2026-05-23-ralph-v1-per-repo-loop-design.md#safety-controls) section. Add a halt-and-acknowledge mechanism that freezes the executor when a cycle trips, snapshots state to a META-BUG file in `.ralph/blocked/`, optionally notifies via a webhook, and refuses to restart until a sentinel file has been acknowledged. Wire `RALPH_MAX_ATTEMPTS` (default `3`) into the attempt counter consumed by the cycle detector. Layer 2 (auto code review + human reviewers) is acknowledged but NOT built here — the spec is explicit that the team's existing auto code review fills that role.

**Architecture:** A new `ralph_executor.safety` package with five modules — `stuck` (STUCK.md detection + folder mutation), `events` (append-only event log on SQLite), `cycle_detector` (six pure-function detector rules over a list of events), `halt` (META-BUG writer, sentinel file management, optional webhook notification), and `__init__` (re-exports the public API). Each detector rule is a function `evaluate(events: list[Event], now: datetime) -> CycleSignal | None` taking pre-loaded events and a "now" timestamp; no detector touches the filesystem or the network. The executor's loop driver (Plan 7's `ralph_executor.loop.run_iteration`) gains three integration hooks — `safety.check_halt_sentinel()` (before each iteration), `safety.handle_stuck(...)` (after STUCK.md is detected on Claude exit), and `safety.record_iteration_event(...)` plus `safety.evaluate_cycle(...)` (after each iteration, before the next). The integration is shown as an exact diff against Plan 7's loop driver. Tests live in `tests/safety/` and feed each detector synthetic event sequences with assertion of `trip` / `no-trip` outcomes.

**Tech Stack:** Python 3.12+, `uv`, `sqlite3` (stdlib), `pytest`, `pytest-freezegun` (added in Task 2), `responses` (for the webhook test), `pyyaml` (frontmatter), ruff, mypy strict. No new third-party dependency beyond `pytest-freezegun` and `responses` (the latter is already a Plan-2 dependency).

---

## File Structure

| Path | Responsibility |
|---|---|
| `ralph_executor/safety/__init__.py` | Public API surface. Re-exports `Event`, `EventType`, `CycleSignal`, `SignalKind`, `StuckOutcome`, `HaltedError`, `MetaBug`, plus the orchestrator functions `check_halt_sentinel`, `handle_stuck`, `record_iteration_event`, `evaluate_cycle`, and `halt_and_acknowledge`. Everything else inside the package is implementation detail. |
| `ralph_executor/safety/events.py` | Append-only event log. Backed by SQLite at `.ralph/state/events.db` (path resolved from the executor's `RALPH_REPO_PATH` env var); schema is one `events` table plus a `schema_version` pragma. Functions: `open_log(repo: Path) -> EventLog`, `EventLog.append(event: Event) -> None`, `EventLog.recent(window: timedelta, now: datetime) -> list[Event]`. Dataclasses: `Event`, `EventType` (string enum). No business logic — write/read only. |
| `ralph_executor/safety/stuck.py` | STUCK.md handling. Functions: `detect_stuck(pbi_dir: Path) -> bool` (looks for a non-empty `STUCK.md` inside the PBI directory), `read_stuck_reason(pbi_dir: Path) -> str` (reads + truncates the body for logging), `move_to_blocked(repo: Path, pbi_dir: Path, reason: str) -> Path` (moves the PBI directory from `.ralph/current/<id>/` to `.ralph/blocked/<id>/`, appends a HISTORY.md line, returns the new path). Pure I/O — no SQLite, no detector logic. |
| `ralph_executor/safety/cycle_detector.py` | Pure detector rules. Dataclasses: `CycleSignal` (rule name, severity, description, supporting event ids), `SignalKind` (string enum: `signature_recurrence`, `whack_a_mole`, `same_file_thrashing`, `regression_cascade`, `attempt_divergence`, `blocked_growth`). Functions: `evaluate_signature_recurrence(events, now)`, `evaluate_whack_a_mole(events, now)`, `evaluate_same_file_thrashing(events, now)`, `evaluate_regression_cascade(events, now)`, `evaluate_attempt_divergence(events, now)`, `evaluate_blocked_growth(events, now)`, plus an `evaluate_all(events, now) -> list[CycleSignal]` aggregator. No I/O; takes pre-loaded events. |
| `ralph_executor/safety/halt.py` | Halt-and-ack mechanism. Functions: `snapshot_state(repo: Path) -> StateSnapshot`, `write_meta_bug(repo, snapshot, signals) -> Path` (writes a META-BUG markdown file under `.ralph/blocked/` with frontmatter + body), `write_halt_sentinel(repo, meta_bug_id) -> Path` (writes `.ralph/state/halted` with the META-BUG id and a placeholder `acknowledged-by:` line), `check_halt_sentinel(repo) -> HaltStatus` (returns `running`, `halted`, or `acknowledged`), `notify_halt(meta_bug, webhook_url=None) -> None` (POSTs to the webhook if set; otherwise logs to stderr), `halt_and_acknowledge(repo, signals, *, webhook_env="RALPH_HALT_WEBHOOK") -> MetaBug` (the top-level orchestrator the executor calls). Dataclasses: `StateSnapshot`, `MetaBug`, `HaltStatus`, `HaltedError`. |
| `tests/safety/__init__.py` | Empty package marker. |
| `tests/safety/conftest.py` | Shared fixtures: `repo_dir` (a `tmp_path`-based `.ralph/` skeleton with `inbox/`, `current/`, `pending-pr/`, `done/`, `blocked/`, `state/`), `event_log` (an `EventLog` opened against `repo_dir`), and helpers `make_event(...)`, `freeze_time(...)`. |
| `tests/safety/test_events.py` | Tests for the SQLite event log: schema initialisation is idempotent, append round-trips, `recent(window)` filters by timestamp, ordering is by `recorded_at ASC`, the log is durable across `open_log` calls, JSON payloads survive round-trip. |
| `tests/safety/test_stuck.py` | Tests for STUCK.md detection + folder mutation: detection on non-empty file, no detection on missing or empty file, `move_to_blocked` relocates the directory and appends a HISTORY.md line with the reason. |
| `tests/safety/test_cycle_detector.py` | One test class per detector rule, each feeding synthetic event sequences (trip + no-trip) and asserting the outcome. Plus `test_evaluate_all_returns_all_tripped_signals`. |
| `tests/safety/test_halt.py` | Tests for snapshot, META-BUG writer (frontmatter shape), sentinel lifecycle (`running` → `halted` → `acknowledged`), webhook notification (with `responses`), and `halt_and_acknowledge` orchestration. |
| `tests/safety/test_integration_loop.py` | End-to-end test that drives a fake loop iteration through the safety hooks: a sequence of synthetic Claude exits produces events; on the trip, the executor writes the META-BUG + sentinel; on the next iteration `check_halt_sentinel` raises `HaltedError`; after the sentinel is acknowledged manually, the loop resumes. Uses the real `ralph_executor.loop.run_iteration` from Plan 7 with the Claude spawn monkeypatched. |
| `pyproject.toml` | Add `pytest-freezegun` to `[dependency-groups].dev`. The `[tool.mypy].files` and `[tool.pytest.ini_options].testpaths` already cover `ralph_executor` + `tests` from Plan 7. |

---

## Task 1 — Wire up the `safety/` package skeleton and add `pytest-freezegun`

**Files**
- Modify: `pyproject.toml`
- Create: `ralph_executor/safety/__init__.py`
- Create: `tests/safety/__init__.py`

**Steps**

- [ ] 1. Confirm Plan 7 is merged. Run:
  ```
  uv run pytest tests/executor/ -v
  uv run mypy ralph_executor
  ```
  Expected: every test passes; mypy clean. If either fails, STOP — Plan 9 reaches into Plan 7's `ralph_executor.loop` module in Task 7 and depends on Plan 7's `PBI` type, `current_pbi()` helper, and `run_iteration()` shape.

- [ ] 2. Add `pytest-freezegun` to the dev dependency group. Edit `pyproject.toml`. Locate the `[dependency-groups]` table (Plan 1 added it). Append to the `dev` list:
  ```toml
  [dependency-groups]
  dev = [
      # ... existing entries ...
      "pytest-freezegun>=0.4.2",
  ]
  ```
  Then sync:
  ```
  uv sync --group dev
  ```
  Expected: `uv` installs `pytest-freezegun` and prints `Resolved N packages`. The lockfile is updated.

- [ ] 3. Create `ralph_executor/safety/__init__.py` with the exact content below. The re-exports are deliberately listed (mypy strict requires explicit `__all__` for star-imports, and the safety package is small enough to enumerate):
  ```python
  """Ralph executor safety controls.

  Two of the spec's three safety layers live here:

  - Layer 1 — Ralph self-halt: ``stuck`` detects ``STUCK.md`` inside the
    current PBI on Claude exit and relocates the PBI to ``.ralph/blocked/``.
  - Layer 3 — Global cycle detection: ``cycle_detector`` runs six pure
    detector rules over a rolling window of events; ``events`` provides the
    append-only event log; ``halt`` materialises a META-BUG, snapshots
    state, and refuses to restart until a sentinel is acknowledged.

  Layer 2 (mechanical PR guards) is intentionally not implemented; the
  spec acknowledges the team's existing auto code review fills the role.
  """
  from __future__ import annotations

  from ralph_executor.safety.cycle_detector import (
      CycleSignal,
      SignalKind,
      evaluate_all,
      evaluate_attempt_divergence,
      evaluate_blocked_growth,
      evaluate_regression_cascade,
      evaluate_same_file_thrashing,
      evaluate_signature_recurrence,
      evaluate_whack_a_mole,
  )
  from ralph_executor.safety.events import Event, EventLog, EventType, open_log
  from ralph_executor.safety.halt import (
      HaltedError,
      HaltStatus,
      MetaBug,
      StateSnapshot,
      check_halt_sentinel,
      halt_and_acknowledge,
      notify_halt,
      snapshot_state,
      write_halt_sentinel,
      write_meta_bug,
  )
  from ralph_executor.safety.stuck import (
      StuckOutcome,
      detect_stuck,
      handle_stuck,
      move_to_blocked,
      read_stuck_reason,
  )

  __all__ = [
      "CycleSignal",
      "Event",
      "EventLog",
      "EventType",
      "HaltStatus",
      "HaltedError",
      "MetaBug",
      "SignalKind",
      "StateSnapshot",
      "StuckOutcome",
      "check_halt_sentinel",
      "detect_stuck",
      "evaluate_all",
      "evaluate_attempt_divergence",
      "evaluate_blocked_growth",
      "evaluate_regression_cascade",
      "evaluate_same_file_thrashing",
      "evaluate_signature_recurrence",
      "evaluate_whack_a_mole",
      "halt_and_acknowledge",
      "handle_stuck",
      "move_to_blocked",
      "notify_halt",
      "open_log",
      "read_stuck_reason",
      "snapshot_state",
      "write_halt_sentinel",
      "write_meta_bug",
  ]
  ```
  Expected: file is exactly that content.

- [ ] 4. Create `tests/safety/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 5. Confirm the toolchain still parses:
  ```
  uv run ruff check ralph_executor/safety/__init__.py
  uv run mypy ralph_executor/safety/__init__.py
  ```
  Expected: ruff: `All checks passed!`. Mypy: fails because the modules being imported do not yet exist — that is the red step Task 2 fixes. (Specifically: `ralph_executor.safety.cycle_detector` and the rest are missing. Acceptable.)

- [ ] 6. Commit the scaffold + dep-bump:
  ```
  git add pyproject.toml uv.lock ralph_executor/safety/__init__.py tests/safety/__init__.py
  git commit -m "chore(safety): scaffold safety package and add pytest-freezegun"
  ```
  Expected: commit succeeds.

---

## Task 2 — Implement `events.py` (append-only SQLite event log)

**Files**
- Create: `ralph_executor/safety/events.py`
- Create: `tests/safety/conftest.py`
- Create: `tests/safety/test_events.py`

**Steps**

- [ ] 1. Write `tests/safety/conftest.py` with the exact content below. The fixtures here are shared across every safety test file:
  ```python
  """Shared fixtures for the safety test package."""
  from __future__ import annotations

  import json
  from collections.abc import Iterator
  from datetime import datetime, timedelta, timezone
  from pathlib import Path
  from typing import Any

  import pytest

  from ralph_executor.safety.events import Event, EventLog, EventType, open_log


  RALPH_DIRS = (
      ".ralph/inbox",
      ".ralph/current",
      ".ralph/pending-pr",
      ".ralph/done",
      ".ralph/blocked",
      ".ralph/state",
  )


  @pytest.fixture
  def repo_dir(tmp_path: Path) -> Path:
      """Create an empty ``.ralph/`` skeleton under ``tmp_path``."""
      for relative in RALPH_DIRS:
          (tmp_path / relative).mkdir(parents=True, exist_ok=True)
      return tmp_path


  @pytest.fixture
  def event_log(repo_dir: Path) -> Iterator[EventLog]:
      log = open_log(repo_dir)
      try:
          yield log
      finally:
          log.close()


  @pytest.fixture
  def now() -> datetime:
      return datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)


  def make_event(
      *,
      kind: EventType,
      recorded_at: datetime,
      pbi_id: str = "WI-1",
      payload: dict[str, Any] | None = None,
  ) -> Event:
      return Event(
          kind=kind,
          recorded_at=recorded_at,
          pbi_id=pbi_id,
          payload=payload or {},
      )


  def offset(now: datetime, **kwargs: int) -> datetime:
      """Return ``now`` offset by the given keyword duration (negative for past)."""
      return now + timedelta(**kwargs)


  def write_pbi_dir(
      base: Path,
      *,
      bucket: str,
      pbi_id: str,
      frontmatter: dict[str, Any] | None = None,
      body: str = "",
      extra_files: dict[str, str] | None = None,
  ) -> Path:
      """Write a minimal PBI directory under ``base/.ralph/<bucket>/<pbi_id>/``."""
      pbi_dir = base / ".ralph" / bucket / pbi_id
      pbi_dir.mkdir(parents=True, exist_ok=True)
      fm = dict(frontmatter or {})
      fm.setdefault("id", pbi_id)
      fm.setdefault("type", "feature")
      fm.setdefault("status", bucket)
      fm.setdefault("severity", "normal")
      fm.setdefault("attempts", 0)
      front = "---\n"
      for key, value in fm.items():
          if isinstance(value, str):
              front += f"{key}: {value}\n"
          else:
              front += f"{key}: {json.dumps(value)}\n"
      front += "---\n\n"
      (pbi_dir / "PBI.md").write_text(front + body, encoding="utf-8")
      (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
      for name, content in (extra_files or {}).items():
          (pbi_dir / name).write_text(content, encoding="utf-8")
      return pbi_dir
  ```
  Expected: file written; importable from `ralph_executor.safety.events` once Task 2 step 3 is complete.

- [ ] 2. Write `tests/safety/test_events.py` with the exact content below. These are the failing tests for the event log:
  ```python
  """Tests for the append-only event log."""
  from __future__ import annotations

  from datetime import datetime, timedelta, timezone
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


  def test_append_and_retrieve_single_event(
      event_log: EventLog, now: datetime
  ) -> None:
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
      event_log.append(
          make_event(kind=EventType.PBI_OPENED, recorded_at=offset(now, hours=-2))
      )
      event_log.append(
          make_event(kind=EventType.PBI_CLOSED, recorded_at=offset(now, minutes=-30))
      )
      recent_hour = event_log.recent(window=timedelta(hours=1), now=now)
      assert {ev.kind for ev in recent_hour} == {EventType.PBI_CLOSED}
      recent_three_hours = event_log.recent(window=timedelta(hours=3), now=now)
      assert {ev.kind for ev in recent_three_hours} == {
          EventType.PBI_OPENED,
          EventType.PBI_CLOSED,
      }


  def test_recent_orders_by_recorded_at_ascending(
      event_log: EventLog, now: datetime
  ) -> None:
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


  def test_payload_round_trips_complex_json(
      event_log: EventLog, now: datetime
  ) -> None:
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


  def test_event_kind_is_validated_at_append(
      event_log: EventLog, now: datetime
  ) -> None:
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
          make_event(
              kind=EventType.PBI_CLOSED, recorded_at=offset(now, microseconds=-1)
          )
      )
      events = event_log.recent(window=timedelta(0), now=now)
      assert [ev.kind for ev in events] == [EventType.PBI_OPENED]
  ```
  Expected: nine tests, all failing with `ImportError` or `ModuleNotFoundError` against `ralph_executor.safety.events` (since the module does not yet exist).

- [ ] 3. Write `ralph_executor/safety/events.py` with the exact content below:
  ```python
  """Append-only event log backing the cycle detector.

  Events are persisted to a SQLite database under
  ``<repo>/.ralph/state/events.db``. The schema is intentionally small
  (one ``events`` table) because every detector rule is a pure function
  over events — the log only has to write fast and read fast.
  """
  from __future__ import annotations

  import json
  import sqlite3
  import threading
  from dataclasses import dataclass, field
  from datetime import datetime, timedelta, timezone
  from enum import Enum
  from pathlib import Path
  from typing import Any, Final


  _SCHEMA_VERSION: Final[int] = 1
  _DB_RELATIVE: Final[str] = ".ralph/state/events.db"


  class EventType(str, Enum):
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
                  EventType(event.kind)  # type: ignore[arg-type]
              except ValueError as exc:
                  raise ValueError(f"unknown EventType: {event.kind!r}") from exc
          with self._lock:
              self._conn.execute(
                  "INSERT INTO events (kind, recorded_at, pbi_id, payload)"
                  " VALUES (?, ?, ?, ?)",
                  (
                      event.kind.value
                      if isinstance(event.kind, EventType)
                      else str(event.kind),
                      _utc_isoformat(event.recorded_at),
                      event.pbi_id,
                      json.dumps(event.payload, sort_keys=True),
                  ),
              )
              self._conn.commit()

      def recent(
          self, *, window: timedelta, now: datetime
      ) -> list[Event]:
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
                  "CREATE INDEX IF NOT EXISTS idx_events_recorded_at"
                  " ON events (recorded_at)"
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
      return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


  def _parse_iso(value: str) -> datetime:
      return datetime.fromisoformat(value)
  ```

- [ ] 4. Run the event-log tests; they must now all pass:
  ```
  uv run pytest tests/safety/test_events.py -v
  ```
  Expected: nine tests collected, nine passing.

- [ ] 5. Run ruff and mypy:
  ```
  uv run ruff check ralph_executor/safety/events.py tests/safety/test_events.py tests/safety/conftest.py
  uv run mypy ralph_executor/safety/events.py tests/safety/test_events.py tests/safety/conftest.py
  ```
  Expected: both clean.

- [ ] 6. Commit:
  ```
  git add ralph_executor/safety/events.py tests/safety/conftest.py tests/safety/test_events.py
  git commit -m "feat(safety): add append-only SQLite event log for cycle detection"
  ```

---

## Task 3 — Implement `stuck.py` (STUCK.md detection + relocation to blocked/)

**Files**
- Create: `ralph_executor/safety/stuck.py`
- Create: `tests/safety/test_stuck.py`

**Steps**

- [ ] 1. Write `tests/safety/test_stuck.py` with the exact content below:
  ```python
  """Tests for STUCK.md detection + folder mutation (Layer 1 self-halt)."""
  from __future__ import annotations

  from datetime import datetime, timezone
  from pathlib import Path

  import pytest

  from ralph_executor.safety.stuck import (
      StuckOutcome,
      detect_stuck,
      handle_stuck,
      move_to_blocked,
      read_stuck_reason,
  )
  from tests.safety.conftest import write_pbi_dir


  def test_detect_stuck_returns_true_when_file_has_content(repo_dir: Path) -> None:
      pbi_dir = write_pbi_dir(
          repo_dir,
          bucket="current",
          pbi_id="WI-1",
          extra_files={
              "STUCK.md": "what I tried: read INVESTIGATE.md, nothing helped\n"
          },
      )
      assert detect_stuck(pbi_dir) is True


  def test_detect_stuck_returns_false_when_file_is_missing(repo_dir: Path) -> None:
      pbi_dir = write_pbi_dir(repo_dir, bucket="current", pbi_id="WI-1")
      assert detect_stuck(pbi_dir) is False


  def test_detect_stuck_returns_false_when_file_is_blank(repo_dir: Path) -> None:
      pbi_dir = write_pbi_dir(
          repo_dir,
          bucket="current",
          pbi_id="WI-1",
          extra_files={"STUCK.md": "   \n\n   \n"},
      )
      assert detect_stuck(pbi_dir) is False


  def test_read_stuck_reason_returns_trimmed_content(repo_dir: Path) -> None:
      body = "blocking: ADO PAT lacks workitem read scope\n"
      pbi_dir = write_pbi_dir(
          repo_dir,
          bucket="current",
          pbi_id="WI-1",
          extra_files={"STUCK.md": body},
      )
      assert read_stuck_reason(pbi_dir) == body.strip()


  def test_read_stuck_reason_truncates_overlong_content(repo_dir: Path) -> None:
      huge = "x" * 5000
      pbi_dir = write_pbi_dir(
          repo_dir,
          bucket="current",
          pbi_id="WI-1",
          extra_files={"STUCK.md": huge},
      )
      reason = read_stuck_reason(pbi_dir)
      assert len(reason) <= 2048
      assert reason.endswith("...[truncated]")


  def test_move_to_blocked_relocates_directory(repo_dir: Path) -> None:
      pbi_dir = write_pbi_dir(
          repo_dir,
          bucket="current",
          pbi_id="WI-7",
          extra_files={"STUCK.md": "blocking: ambiguous acceptance criteria\n"},
      )
      new_path = move_to_blocked(
          repo=repo_dir,
          pbi_dir=pbi_dir,
          reason="blocking: ambiguous acceptance criteria",
      )
      assert new_path == repo_dir / ".ralph" / "blocked" / "WI-7"
      assert new_path.is_dir()
      assert not pbi_dir.exists()
      history = (new_path / "HISTORY.md").read_text(encoding="utf-8")
      assert "STUCK" in history
      assert "ambiguous acceptance criteria" in history


  def test_move_to_blocked_refuses_outside_current(repo_dir: Path) -> None:
      pbi_dir = write_pbi_dir(
          repo_dir, bucket="inbox", pbi_id="WI-9"
      )
      with pytest.raises(ValueError, match="must be under .ralph/current/"):
          move_to_blocked(repo=repo_dir, pbi_dir=pbi_dir, reason="x")


  def test_move_to_blocked_refuses_collision(repo_dir: Path) -> None:
      pbi_dir = write_pbi_dir(
          repo_dir,
          bucket="current",
          pbi_id="WI-1",
          extra_files={"STUCK.md": "stuck\n"},
      )
      # Pre-create the collision target.
      write_pbi_dir(repo_dir, bucket="blocked", pbi_id="WI-1")
      with pytest.raises(FileExistsError):
          move_to_blocked(repo=repo_dir, pbi_dir=pbi_dir, reason="stuck")


  def test_handle_stuck_returns_outcome_with_event(
      repo_dir: Path,
  ) -> None:
      pbi_dir = write_pbi_dir(
          repo_dir,
          bucket="current",
          pbi_id="WI-3",
          extra_files={"STUCK.md": "blocking: dependency missing\n"},
      )
      now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)
      outcome = handle_stuck(repo=repo_dir, pbi_dir=pbi_dir, now=now)
      assert isinstance(outcome, StuckOutcome)
      assert outcome.blocked_path == repo_dir / ".ralph" / "blocked" / "WI-3"
      assert outcome.reason.startswith("blocking: dependency missing")
      assert outcome.event.pbi_id == "WI-3"
      assert outcome.event.kind.value == "pbi.blocked"
      assert outcome.event.recorded_at == now


  def test_handle_stuck_returns_none_when_no_stuck_file(
      repo_dir: Path,
  ) -> None:
      pbi_dir = write_pbi_dir(repo_dir, bucket="current", pbi_id="WI-3")
      now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)
      outcome = handle_stuck(repo=repo_dir, pbi_dir=pbi_dir, now=now)
      assert outcome is None
  ```
  Expected: ten tests, all failing because `ralph_executor.safety.stuck` doesn't exist yet.

- [ ] 2. Write `ralph_executor/safety/stuck.py` with the exact content below:
  ```python
  """Layer 1 safety: detect ``STUCK.md`` and relocate the PBI to ``blocked/``.

  This is per-PBI self-halt — the loop keeps running; only the offending
  PBI is moved out of ``current/`` and into ``blocked/`` with a HISTORY.md
  audit entry recording the reason. The cycle detector observes the
  resulting ``pbi.blocked`` event through the shared event log.
  """
  from __future__ import annotations

  import shutil
  from dataclasses import dataclass
  from datetime import datetime
  from pathlib import Path

  from ralph_executor.safety.events import Event, EventType


  _MAX_REASON_BYTES = 2048
  _TRUNCATION_MARKER = "...[truncated]"
  _STUCK_FILE = "STUCK.md"
  _HISTORY_FILE = "HISTORY.md"


  @dataclass(frozen=True)
  class StuckOutcome:
      """Returned from :func:`handle_stuck` when a STUCK.md is detected."""

      blocked_path: Path
      reason: str
      event: Event


  def detect_stuck(pbi_dir: Path) -> bool:
      """Return True iff ``<pbi_dir>/STUCK.md`` exists and is non-empty."""
      stuck = pbi_dir / _STUCK_FILE
      if not stuck.is_file():
          return False
      return stuck.read_text(encoding="utf-8").strip() != ""


  def read_stuck_reason(pbi_dir: Path) -> str:
      """Return the trimmed content of ``STUCK.md``, truncated for logging.

      An empty or missing file yields the empty string. Content longer
      than ``_MAX_REASON_BYTES`` characters is cut and the truncation
      marker appended; the cut point respects whole lines so the head of
      the reason stays human-readable.
      """
      stuck = pbi_dir / _STUCK_FILE
      if not stuck.is_file():
          return ""
      raw = stuck.read_text(encoding="utf-8").strip()
      if len(raw) <= _MAX_REASON_BYTES:
          return raw
      cut = raw[: _MAX_REASON_BYTES - len(_TRUNCATION_MARKER)]
      # Backtrack to the previous newline so the truncation respects
      # paragraph boundaries.
      newline_idx = cut.rfind("\n")
      if newline_idx > 0:
          cut = cut[:newline_idx]
      return cut + _TRUNCATION_MARKER


  def move_to_blocked(
      *, repo: Path, pbi_dir: Path, reason: str
  ) -> Path:
      """Move ``pbi_dir`` from ``.ralph/current/<id>/`` to ``.ralph/blocked/<id>/``.

      Appends a HISTORY.md line recording the reason. Raises ``ValueError``
      if the directory is not under ``.ralph/current/``, and
      ``FileExistsError`` if a sibling already occupies the blocked slot.
      """
      try:
          relative = pbi_dir.relative_to(repo)
      except ValueError as exc:
          raise ValueError(
              f"{pbi_dir} is not inside {repo}"
          ) from exc
      parts = relative.parts
      if len(parts) < 3 or parts[0] != ".ralph" or parts[1] != "current":
          raise ValueError(
              f"{pbi_dir} must be under .ralph/current/<pbi-id>/"
          )
      pbi_id = parts[2]
      blocked_root = repo / ".ralph" / "blocked"
      blocked_root.mkdir(parents=True, exist_ok=True)
      target = blocked_root / pbi_id
      if target.exists():
          raise FileExistsError(
              f"blocked target already occupied: {target}; resolve manually"
          )
      # Move the whole directory (preserves attachments, PLAN.md, etc.).
      shutil.move(str(pbi_dir), str(target))
      _append_history_line(target, reason)
      return target


  def handle_stuck(
      *, repo: Path, pbi_dir: Path, now: datetime
  ) -> StuckOutcome | None:
      """Top-level Layer 1 hook called by the executor's loop driver.

      If ``STUCK.md`` is present, moves the PBI to ``blocked/``, appends
      a HISTORY entry, and returns a :class:`StuckOutcome` carrying the
      blocked path, the truncated reason, and the synthetic
      ``pbi.blocked`` event the caller should append to the event log.
      Returns ``None`` if there is no STUCK.md (the iteration's normal
      outcome).
      """
      if not detect_stuck(pbi_dir):
          return None
      reason = read_stuck_reason(pbi_dir)
      pbi_id = pbi_dir.name
      target = move_to_blocked(repo=repo, pbi_dir=pbi_dir, reason=reason)
      event = Event(
          kind=EventType.PBI_BLOCKED,
          recorded_at=now,
          pbi_id=pbi_id,
          payload={"reason": reason, "source": "stuck-self-halt"},
      )
      return StuckOutcome(blocked_path=target, reason=reason, event=event)


  def _append_history_line(pbi_dir: Path, reason: str) -> None:
      history = pbi_dir / _HISTORY_FILE
      existing = history.read_text(encoding="utf-8") if history.is_file() else ""
      if existing and not existing.endswith("\n"):
          existing += "\n"
      stamp = (
          f"\n---\n"
          f"STUCK — moved to blocked/\n"
          f"reason: {reason}\n"
      )
      history.write_text(existing + stamp, encoding="utf-8")
  ```

- [ ] 3. Run the tests:
  ```
  uv run pytest tests/safety/test_stuck.py -v
  ```
  Expected: ten tests pass.

- [ ] 4. Run ruff and mypy:
  ```
  uv run ruff check ralph_executor/safety/stuck.py tests/safety/test_stuck.py
  uv run mypy ralph_executor/safety/stuck.py tests/safety/test_stuck.py
  ```
  Expected: both clean.

- [ ] 5. Commit:
  ```
  git add ralph_executor/safety/stuck.py tests/safety/test_stuck.py
  git commit -m "feat(safety): add STUCK.md handler that moves stuck PBIs to blocked/"
  ```

---

## Task 4 — Implement `cycle_detector.py` (six pure detector rules)

**Files**
- Create: `ralph_executor/safety/cycle_detector.py`
- Create: `tests/safety/test_cycle_detector.py`

**Steps**

- [ ] 1. Write `tests/safety/test_cycle_detector.py` with the exact content below. Each test class targets one detector rule; both trip and no-trip paths are covered, plus an aggregator test:
  ```python
  """Tests for the six pure cycle-detector rules."""
  from __future__ import annotations

  from datetime import datetime, timedelta, timezone
  from typing import Iterable

  import pytest

  from ralph_executor.safety.cycle_detector import (
      CycleSignal,
      SignalKind,
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
      def test_trips_when_same_signature_recurs_inside_24h(
          self, now: datetime
      ) -> None:
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
      def test_trips_when_close_rate_matches_create_rate(
          self, now: datetime
      ) -> None:
          # Five new bugs and four closures inside the 4h window — close/create
          # ratio is 0.8, above the 0.7 threshold.
          events: list[Event] = []
          for i in range(5):
              events.append(
                  make_event(
                      kind=EventType.PBI_OPENED,
                      recorded_at=offset(now, minutes=-(200 - i * 10)),
                      pbi_id=f"BUG-OPEN-{i}",
                  )
              )
          for i in range(4):
              events.append(
                  make_event(
                      kind=EventType.PBI_CLOSED,
                      recorded_at=offset(now, minutes=-(190 - i * 10)),
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
          # One open + one close is below the rule's minimum (>=3 opens).
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
      def test_trips_when_one_file_has_6_PRs_in_24h(
          self, now: datetime
      ) -> None:
          target = "src/auth/handler.py"
          events: list[Event] = []
          for i in range(6):
              events.append(
                  make_event(
                      kind=EventType.PR_CREATED,
                      recorded_at=offset(now, hours=-(20 - i)),
                      pbi_id=f"WI-{i}",
                      payload={"files": [target]},
                  )
              )
          signal = evaluate_same_file_thrashing(_events(events), now)
          assert signal is not None
          assert target in signal.description

      def test_does_not_trip_at_5_PRs(self, now: datetime) -> None:
          target = "src/auth/handler.py"
          events: list[Event] = [
              make_event(
                  kind=EventType.PR_CREATED,
                  recorded_at=offset(now, hours=-(20 - i)),
                  pbi_id=f"WI-{i}",
                  payload={"files": [target]},
              )
              for i in range(5)
          ]
          assert evaluate_same_file_thrashing(_events(events), now) is None

      def test_ignores_files_touched_long_ago(self, now: datetime) -> None:
          target = "src/auth/handler.py"
          events: list[Event] = [
              make_event(
                  kind=EventType.PR_CREATED,
                  recorded_at=offset(now, hours=-(30 + i)),
                  pbi_id=f"WI-{i}",
                  payload={"files": [target]},
              )
              for i in range(6)
          ]
          assert evaluate_same_file_thrashing(_events(events), now) is None


  # ----------------------------------------------------------------------
  # regression_cascade
  # ----------------------------------------------------------------------

  class TestRegressionCascade:
      def test_trips_when_recently_merged_signature_reappears(
          self, now: datetime
      ) -> None:
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
      def test_trips_when_recent_attempts_average_higher_than_baseline(
          self, now: datetime
      ) -> None:
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
      def test_aggregator_returns_all_tripped_signals(
          self, now: datetime
      ) -> None:
          target = "src/auth/handler.py"
          sig = "AssertionError in handler.py:42"
          events: list[Event] = []
          # Trip same_file_thrashing.
          for i in range(6):
              events.append(
                  make_event(
                      kind=EventType.PR_CREATED,
                      recorded_at=offset(now, hours=-(20 - i)),
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

      def test_aggregator_returns_empty_when_calm(
          self, now: datetime
      ) -> None:
          assert evaluate_all([], now) == []
  ```
  Expected: nineteen tests, all failing because `ralph_executor.safety.cycle_detector` doesn't exist yet.

- [ ] 2. Write `ralph_executor/safety/cycle_detector.py` with the exact content below:
  ```python
  """Layer 3 safety: pure-function cycle-detection rules.

  Each rule is a function ``evaluate_<name>(events, now) -> CycleSignal | None``
  that takes a pre-loaded list of events and a "now" timestamp. Detectors
  do NOT touch the filesystem, the database, or the network — the I/O
  happens in the loop driver (it reads events, calls the detectors, and
  writes the META-BUG via ``halt.halt_and_acknowledge``).

  Thresholds are defined as module-level constants so they can be tuned
  without touching detector bodies. They mirror the v1 column in the spec's
  "Layer 3" table.
  """
  from __future__ import annotations

  from collections import defaultdict
  from dataclasses import dataclass, field
  from datetime import datetime, timedelta
  from enum import Enum
  from typing import Iterable

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

  class SignalKind(str, Enum):
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
      if isinstance(raw, int):
          return raw
      return None


  # ----------------------------------------------------------------------
  # Rule 1 — signature_recurrence
  # ----------------------------------------------------------------------

  def evaluate_signature_recurrence(
      events: list[Event], now: datetime
  ) -> CycleSignal | None:
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
          if ev.kind
          in {EventType.SIGNATURE_OBSERVED, EventType.PR_GREEN_THEN_RED}
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
  # Rule 2 — whack_a_mole
  # ----------------------------------------------------------------------

  def evaluate_whack_a_mole(
      events: list[Event], now: datetime
  ) -> CycleSignal | None:
      """Trip when close-rate ≈ create-rate over a 4h window."""
      window = WHACK_A_MOLE_WINDOW
      opens = [
          ev
          for ev in events
          if ev.kind == EventType.PBI_OPENED and _within(window, now, ev)
      ]
      closes = [
          ev
          for ev in events
          if ev.kind in {EventType.PBI_CLOSED, EventType.PR_MERGED}
          and _within(window, now, ev)
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
              f"({len(closes)} closes vs {len(opens)} opens) — "
              f"each fix is followed by a new failure"
          ),
          supporting_events=tuple(opens[:3] + closes[:3]),
      )


  # ----------------------------------------------------------------------
  # Rule 3 — same_file_thrashing
  # ----------------------------------------------------------------------

  def evaluate_same_file_thrashing(
      events: list[Event], now: datetime
  ) -> CycleSignal | None:
      """Trip when one file is touched by ``SAME_FILE_MIN_PRS`` distinct PRs in 24h."""
      window = SAME_FILE_WINDOW
      pr_events = [
          ev
          for ev in events
          if ev.kind == EventType.PR_CREATED and _within(window, now, ev)
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
                      f"{int(window.total_seconds() // 3600)}h — "
                      f"likely wrong abstraction"
                  ),
                  supporting_events=tuple(evs[:5]),
              )
      return None


  # ----------------------------------------------------------------------
  # Rule 4 — regression_cascade
  # ----------------------------------------------------------------------

  def evaluate_regression_cascade(
      events: list[Event], now: datetime
  ) -> CycleSignal | None:
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
  # Rule 5 — attempt_divergence
  # ----------------------------------------------------------------------

  def evaluate_attempt_divergence(
      events: list[Event], now: datetime
  ) -> CycleSignal | None:
      """Trip when recent average attempts/PBI is materially above baseline."""
      attempt_events = [
          ev for ev in events if ev.kind == EventType.ATTEMPT_INCREMENTED
      ]
      recent = [
          ev for ev in attempt_events if _within(ATTEMPT_RECENT_WINDOW, now, ev)
      ]
      baseline = [
          ev
          for ev in attempt_events
          if _within(ATTEMPT_BASELINE_WINDOW, now, ev) and ev not in recent
      ]
      if len(recent) < ATTEMPT_RECENT_MIN_PBIS:
          return None
      if len(baseline) < ATTEMPT_BASELINE_MIN_PBIS:
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
      values = [a for a in (_attempts(ev) for ev in events) if a is not None]
      if not values:
          return 0.0
      return sum(values) / len(values)


  # ----------------------------------------------------------------------
  # Rule 6 — blocked_growth
  # ----------------------------------------------------------------------

  def evaluate_blocked_growth(
      events: list[Event], now: datetime
  ) -> CycleSignal | None:
      """Trip when ``blocked/`` growth exceeds ``done/`` growth over 24h."""
      window = BLOCKED_GROWTH_WINDOW
      blocks = [
          ev
          for ev in events
          if ev.kind == EventType.PBI_BLOCKED and _within(window, now, ev)
      ]
      closes = [
          ev
          for ev in events
          if ev.kind in {EventType.PBI_MERGED, EventType.PBI_CLOSED}
          and _within(window, now, ev)
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
  ```

- [ ] 3. Run the cycle-detector tests:
  ```
  uv run pytest tests/safety/test_cycle_detector.py -v
  ```
  Expected: nineteen tests pass.

- [ ] 4. Run ruff and mypy:
  ```
  uv run ruff check ralph_executor/safety/cycle_detector.py tests/safety/test_cycle_detector.py
  uv run mypy ralph_executor/safety/cycle_detector.py tests/safety/test_cycle_detector.py
  ```
  Expected: both clean.

- [ ] 5. Commit:
  ```
  git add ralph_executor/safety/cycle_detector.py tests/safety/test_cycle_detector.py
  git commit -m "feat(safety): add six pure cycle-detector rules + aggregator"
  ```

---

## Task 5 — Implement `halt.py` (META-BUG writer + sentinel + webhook)

**Files**
- Create: `ralph_executor/safety/halt.py`
- Create: `tests/safety/test_halt.py`

**Steps**

- [ ] 1. Write `tests/safety/test_halt.py` with the exact content below:
  ```python
  """Tests for the halt-and-acknowledge mechanism."""
  from __future__ import annotations

  import json
  from datetime import datetime, timezone
  from pathlib import Path

  import pytest
  import responses

  from ralph_executor.safety.cycle_detector import CycleSignal, SignalKind
  from ralph_executor.safety.events import Event, EventType
  from ralph_executor.safety.halt import (
      HaltedError,
      HaltStatus,
      MetaBug,
      StateSnapshot,
      check_halt_sentinel,
      halt_and_acknowledge,
      notify_halt,
      snapshot_state,
      write_halt_sentinel,
      write_meta_bug,
  )
  from tests.safety.conftest import make_event, write_pbi_dir


  def _signal(kind: SignalKind = SignalKind.SIGNATURE_RECURRENCE) -> CycleSignal:
      return CycleSignal(
          kind=kind,
          description=f"{kind.value} fired",
          supporting_events=(
              make_event(
                  kind=EventType.SIGNATURE_OBSERVED,
                  recorded_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
              ),
          ),
      )


  def test_snapshot_state_collects_queue_contents(repo_dir: Path) -> None:
      write_pbi_dir(repo_dir, bucket="inbox", pbi_id="WI-1")
      write_pbi_dir(repo_dir, bucket="current", pbi_id="WI-2")
      write_pbi_dir(repo_dir, bucket="blocked", pbi_id="WI-3")

      snapshot = snapshot_state(repo_dir)
      assert isinstance(snapshot, StateSnapshot)
      assert "WI-1" in snapshot.inbox
      assert snapshot.current == ("WI-2",)
      assert "WI-3" in snapshot.blocked


  def test_write_meta_bug_emits_frontmatter_and_body(repo_dir: Path) -> None:
      snapshot = StateSnapshot(
          repo_path=repo_dir,
          inbox=("WI-1",),
          current=("WI-2",),
          pending_pr=(),
          done=(),
          blocked=("WI-3",),
          taken_at=datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc),
      )
      signals = [_signal(), _signal(SignalKind.WHACK_A_MOLE)]
      meta = write_meta_bug(repo=repo_dir, snapshot=snapshot, signals=signals)
      assert isinstance(meta, MetaBug)
      assert meta.path.is_file()
      assert meta.path.parent == repo_dir / ".ralph" / "blocked"
      text = meta.path.read_text(encoding="utf-8")
      assert text.startswith("---\n")
      assert f"id: {meta.id}" in text
      assert "type: meta" in text
      assert "severity: critical" in text
      assert "signature_recurrence" in text
      assert "whack_a_mole" in text


  def test_write_halt_sentinel_records_meta_bug_id(repo_dir: Path) -> None:
      path = write_halt_sentinel(repo=repo_dir, meta_bug_id="META-cycle-1")
      assert path == repo_dir / ".ralph" / "state" / "halted"
      content = path.read_text(encoding="utf-8")
      assert "meta_bug_id: META-cycle-1" in content
      assert "acknowledged_by:" in content
      assert "acknowledged_at:" in content


  def test_check_halt_sentinel_running_when_absent(repo_dir: Path) -> None:
      assert check_halt_sentinel(repo_dir) == HaltStatus.RUNNING


  def test_check_halt_sentinel_halted_when_not_acknowledged(repo_dir: Path) -> None:
      write_halt_sentinel(repo=repo_dir, meta_bug_id="META-cycle-2")
      assert check_halt_sentinel(repo_dir) == HaltStatus.HALTED


  def test_check_halt_sentinel_acknowledged_when_filled_in(repo_dir: Path) -> None:
      sentinel = write_halt_sentinel(repo=repo_dir, meta_bug_id="META-cycle-3")
      text = sentinel.read_text(encoding="utf-8")
      text = text.replace(
          "acknowledged_by:", "acknowledged_by: gethin@example.com"
      ).replace(
          "acknowledged_at:", "acknowledged_at: 2026-05-25T08:00:00+00:00"
      )
      sentinel.write_text(text, encoding="utf-8")
      assert check_halt_sentinel(repo_dir) == HaltStatus.ACKNOWLEDGED


  @responses.activate
  def test_notify_halt_posts_to_webhook_when_url_provided(
      repo_dir: Path,
  ) -> None:
      url = "https://example.com/hooks/ralph"
      responses.add(responses.POST, url, status=200, json={"ok": True})
      meta = MetaBug(
          id="META-cycle-4",
          path=repo_dir / ".ralph" / "blocked" / "META-cycle-4.md",
          severity="critical",
          summary="signature recurrence + whack_a_mole",
          signals=(_signal(), _signal(SignalKind.WHACK_A_MOLE)),
          created_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
      )
      notify_halt(meta, webhook_url=url)
      assert len(responses.calls) == 1
      body = json.loads(responses.calls[0].request.body or "{}")
      assert body["meta_bug_id"] == "META-cycle-4"
      assert body["signals"]
      assert body["severity"] == "critical"


  def test_notify_halt_logs_to_stderr_when_no_webhook(
      repo_dir: Path, capsys: pytest.CaptureFixture[str]
  ) -> None:
      meta = MetaBug(
          id="META-cycle-5",
          path=repo_dir / ".ralph" / "blocked" / "META-cycle-5.md",
          severity="critical",
          summary="x",
          signals=(_signal(),),
          created_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
      )
      notify_halt(meta, webhook_url=None)
      err = capsys.readouterr().err
      assert "META-cycle-5" in err
      assert "RALPH HALT" in err


  def test_halt_and_acknowledge_writes_meta_bug_and_sentinel(
      repo_dir: Path, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      monkeypatch.delenv("RALPH_HALT_WEBHOOK", raising=False)
      write_pbi_dir(repo_dir, bucket="current", pbi_id="WI-active")
      meta = halt_and_acknowledge(
          repo=repo_dir, signals=[_signal()], now=datetime(2026, 5, 24, tzinfo=timezone.utc)
      )
      assert meta.path.is_file()
      assert check_halt_sentinel(repo_dir) == HaltStatus.HALTED
      # current/ contents are preserved — the spec says we DO NOT kick the
      # PBI back to inbox.
      assert (repo_dir / ".ralph" / "current" / "WI-active").is_dir()


  def test_halted_error_carries_meta_bug_path(repo_dir: Path) -> None:
      with pytest.raises(HaltedError) as exc_info:
          raise HaltedError(
              meta_bug_id="META-x",
              meta_bug_path=repo_dir / ".ralph" / "blocked" / "META-x.md",
              sentinel_path=repo_dir / ".ralph" / "state" / "halted",
          )
      assert exc_info.value.meta_bug_id == "META-x"
      assert "META-x.md" in str(exc_info.value)
  ```
  Expected: eleven tests, all failing because the module doesn't exist yet.

- [ ] 2. Write `ralph_executor/safety/halt.py` with the exact content below:
  ```python
  """Halt-and-acknowledge: META-BUG writer, sentinel file, optional webhook.

  When :func:`halt_and_acknowledge` is called, the executor:

  1. Snapshots the queue (inbox / current / pending-pr / done / blocked)
     into a :class:`StateSnapshot`.
  2. Writes a META-BUG markdown file under ``.ralph/blocked/`` carrying
     the snapshot plus a per-signal trace.
  3. Writes the sentinel file at ``.ralph/state/halted``. The sentinel
     records the META-BUG id and contains two placeholder lines a human
     must fill in (``acknowledged_by:`` and ``acknowledged_at:``).
     :func:`check_halt_sentinel` reports the executor's state based on
     whether those placeholders are still empty.
  4. Optionally POSTs a small JSON payload to the URL named by the
     ``RALPH_HALT_WEBHOOK`` env var (override via the ``webhook_url=``
     argument in tests). When unset, the function logs the META-BUG id
     and summary to stderr.
  """
  from __future__ import annotations

  import json
  import os
  import sys
  from dataclasses import dataclass, field
  from datetime import datetime, timezone
  from enum import Enum
  from pathlib import Path

  import requests

  from ralph_executor.safety.cycle_detector import CycleSignal


  _META_BUG_PREFIX = "META-cycle-"
  _META_BUG_SUFFIX = ".md"
  _BLOCKED_RELATIVE = ".ralph/blocked"
  _SENTINEL_RELATIVE = ".ralph/state/halted"
  _SENTINEL_PLACEHOLDER_BY = "acknowledged_by:"
  _SENTINEL_PLACEHOLDER_AT = "acknowledged_at:"


  # ----------------------------------------------------------------------
  # Dataclasses + enums
  # ----------------------------------------------------------------------

  class HaltStatus(str, Enum):
      RUNNING = "running"
      HALTED = "halted"
      ACKNOWLEDGED = "acknowledged"


  @dataclass(frozen=True)
  class StateSnapshot:
      repo_path: Path
      inbox: tuple[str, ...]
      current: tuple[str, ...]
      pending_pr: tuple[str, ...]
      done: tuple[str, ...]
      blocked: tuple[str, ...]
      taken_at: datetime


  @dataclass(frozen=True)
  class MetaBug:
      id: str
      path: Path
      severity: str
      summary: str
      signals: tuple[CycleSignal, ...]
      created_at: datetime
      snapshot: StateSnapshot | None = None


  class HaltedError(RuntimeError):
      """Raised by the loop driver when the halt sentinel forbids restart."""

      def __init__(
          self,
          *,
          meta_bug_id: str,
          meta_bug_path: Path,
          sentinel_path: Path,
      ) -> None:
          super().__init__(
              f"executor is halted by {meta_bug_id} "
              f"(see {meta_bug_path}); acknowledge by editing "
              f"{sentinel_path}"
          )
          self.meta_bug_id = meta_bug_id
          self.meta_bug_path = meta_bug_path
          self.sentinel_path = sentinel_path


  # ----------------------------------------------------------------------
  # Snapshot
  # ----------------------------------------------------------------------

  def snapshot_state(repo: Path) -> StateSnapshot:
      def _ls(bucket: str) -> tuple[str, ...]:
          directory = repo / ".ralph" / bucket
          if not directory.is_dir():
              return ()
          entries = sorted(
              p.name for p in directory.iterdir() if p.is_dir()
          )
          return tuple(entries)

      return StateSnapshot(
          repo_path=repo,
          inbox=_ls("inbox"),
          current=_ls("current"),
          pending_pr=_ls("pending-pr"),
          done=_ls("done"),
          blocked=_ls("blocked"),
          taken_at=datetime.now(tz=timezone.utc),
      )


  # ----------------------------------------------------------------------
  # META-BUG
  # ----------------------------------------------------------------------

  def _meta_bug_id(now: datetime) -> str:
      stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
      return f"{_META_BUG_PREFIX}{stamp}"


  def write_meta_bug(
      *,
      repo: Path,
      snapshot: StateSnapshot,
      signals: list[CycleSignal],
      now: datetime | None = None,
  ) -> MetaBug:
      """Render the META-BUG markdown file and return its dataclass."""
      created_at = now or datetime.now(tz=timezone.utc)
      meta_id = _meta_bug_id(created_at)
      blocked_root = repo / _BLOCKED_RELATIVE
      blocked_root.mkdir(parents=True, exist_ok=True)
      path = blocked_root / f"{meta_id}{_META_BUG_SUFFIX}"

      summary = ", ".join(signal.kind.value for signal in signals)
      lines: list[str] = [
          "---",
          f"id: {meta_id}",
          "type: meta",
          "severity: critical",
          "status: blocked",
          f"created_at: {created_at.isoformat()}",
          f"summary: {summary}",
          "---",
          "",
          f"# {meta_id}",
          "",
          "Cycle detector tripped. Halt is in effect; the executor's main",
          "loop refuses to start until the sentinel at",
          f"`{_SENTINEL_RELATIVE}` is acknowledged (fill in",
          "`acknowledged_by` and `acknowledged_at`).",
          "",
          "## Signals",
          "",
      ]
      for signal in signals:
          lines.extend(
              [
                  f"### {signal.kind.value} ({signal.severity})",
                  "",
                  signal.description,
                  "",
              ]
          )
          if signal.supporting_events:
              lines.append("Supporting events:")
              for event in signal.supporting_events:
                  lines.append(
                      f"- `{event.kind.value}` "
                      f"at {event.recorded_at.isoformat()} "
                      f"(pbi={event.pbi_id})"
                  )
              lines.append("")
      lines.extend(
          [
              "## Queue snapshot",
              "",
              f"- inbox ({len(snapshot.inbox)}): "
              + ", ".join(snapshot.inbox) or "- inbox (0): (empty)",
              f"- current ({len(snapshot.current)}): "
              + ", ".join(snapshot.current) or "- current (0): (empty)",
              f"- pending-pr ({len(snapshot.pending_pr)}): "
              + ", ".join(snapshot.pending_pr)
              or "- pending-pr (0): (empty)",
              f"- done ({len(snapshot.done)}): "
              + ", ".join(snapshot.done) or "- done (0): (empty)",
              f"- blocked ({len(snapshot.blocked)}): "
              + ", ".join(snapshot.blocked) or "- blocked (0): (empty)",
              "",
              "## How to acknowledge",
              "",
              "1. Read the signals above and the supporting events.",
              "2. Decide whether the underlying cause is fixed. If not,",
              "   resolve the cause BEFORE acknowledging — the same cycle",
              "   will re-trigger on restart.",
              "3. Edit `.ralph/state/halted` and fill in the",
              "   `acknowledged_by` and `acknowledged_at` lines with your",
              "   identity and the current timestamp.",
              "4. The executor will resume on its next iteration.",
              "",
          ]
      )
      path.write_text("\n".join(lines), encoding="utf-8")
      return MetaBug(
          id=meta_id,
          path=path,
          severity="critical",
          summary=summary,
          signals=tuple(signals),
          created_at=created_at,
          snapshot=snapshot,
      )


  # ----------------------------------------------------------------------
  # Sentinel
  # ----------------------------------------------------------------------

  def write_halt_sentinel(*, repo: Path, meta_bug_id: str) -> Path:
      path = repo / _SENTINEL_RELATIVE
      path.parent.mkdir(parents=True, exist_ok=True)
      content = (
          f"# Ralph halt sentinel\n"
          f"#\n"
          f"# The executor refuses to start its main loop while this file\n"
          f"# is unacknowledged. To acknowledge: fill in BOTH the\n"
          f"# acknowledged_by and acknowledged_at lines, then save.\n"
          f"# To resume from a clean state instead, delete this file AND\n"
          f"# the META-BUG it references after fixing the underlying cause.\n"
          f"#\n"
          f"meta_bug_id: {meta_bug_id}\n"
          f"halted_at: {datetime.now(tz=timezone.utc).isoformat()}\n"
          f"acknowledged_by:\n"
          f"acknowledged_at:\n"
      )
      path.write_text(content, encoding="utf-8")
      return path


  def check_halt_sentinel(repo: Path) -> HaltStatus:
      path = repo / _SENTINEL_RELATIVE
      if not path.is_file():
          return HaltStatus.RUNNING
      text = path.read_text(encoding="utf-8")
      by_filled = _is_filled(text, _SENTINEL_PLACEHOLDER_BY)
      at_filled = _is_filled(text, _SENTINEL_PLACEHOLDER_AT)
      if by_filled and at_filled:
          return HaltStatus.ACKNOWLEDGED
      return HaltStatus.HALTED


  def _is_filled(text: str, key: str) -> bool:
      for raw_line in text.splitlines():
          line = raw_line.strip()
          if line.startswith(key):
              value = line[len(key):].strip()
              return value != ""
      return False


  # ----------------------------------------------------------------------
  # Webhook notification
  # ----------------------------------------------------------------------

  def notify_halt(meta_bug: MetaBug, *, webhook_url: str | None) -> None:
      payload = {
          "meta_bug_id": meta_bug.id,
          "severity": meta_bug.severity,
          "summary": meta_bug.summary,
          "created_at": meta_bug.created_at.isoformat(),
          "path": str(meta_bug.path),
          "signals": [
              {
                  "kind": s.kind.value,
                  "description": s.description,
                  "severity": s.severity,
              }
              for s in meta_bug.signals
          ],
      }
      if not webhook_url:
          print(
              f"RALPH HALT: {meta_bug.id} — {meta_bug.summary} "
              f"(see {meta_bug.path})",
              file=sys.stderr,
          )
          return
      try:
          response = requests.post(webhook_url, json=payload, timeout=10)
          response.raise_for_status()
      except requests.RequestException as exc:
          # Notification failure must NOT mask the halt itself; log and move on.
          print(
              f"RALPH HALT NOTIFY FAILED: {exc!r}; META-BUG {meta_bug.id} "
              f"is still written at {meta_bug.path}",
              file=sys.stderr,
          )


  # ----------------------------------------------------------------------
  # Top-level orchestrator
  # ----------------------------------------------------------------------

  def halt_and_acknowledge(
      *,
      repo: Path,
      signals: list[CycleSignal],
      now: datetime | None = None,
      webhook_env: str = "RALPH_HALT_WEBHOOK",
  ) -> MetaBug:
      """Snapshot state, write META-BUG + sentinel, fire webhook.

      Returns the :class:`MetaBug`. The caller (the executor loop driver)
      is expected to raise :class:`HaltedError` immediately after — there
      is no return-to-loop path once a META-BUG is written. The current
      PBI is intentionally NOT moved.
      """
      snapshot = snapshot_state(repo)
      meta = write_meta_bug(
          repo=repo,
          snapshot=snapshot,
          signals=list(signals),
          now=now,
      )
      write_halt_sentinel(repo=repo, meta_bug_id=meta.id)
      webhook_url = os.environ.get(webhook_env, "").strip() or None
      notify_halt(meta, webhook_url=webhook_url)
      return meta
  ```

- [ ] 3. Run the halt tests:
  ```
  uv run pytest tests/safety/test_halt.py -v
  ```
  Expected: eleven tests pass.

- [ ] 4. Run ruff and mypy:
  ```
  uv run ruff check ralph_executor/safety/halt.py tests/safety/test_halt.py
  uv run mypy ralph_executor/safety/halt.py tests/safety/test_halt.py
  ```
  Expected: both clean.

- [ ] 5. Commit:
  ```
  git add ralph_executor/safety/halt.py tests/safety/test_halt.py
  git commit -m "feat(safety): add halt-and-ack mechanism (META-BUG, sentinel, webhook)"
  ```

---

## Task 6 — Add the `attempts`-counter accessor + `RALPH_MAX_ATTEMPTS`

**Files**
- Modify: `ralph_executor/safety/__init__.py` (extend re-exports)
- Create: `ralph_executor/safety/attempts.py`
- Create: `tests/safety/test_attempts.py`

**Steps**

- [ ] 1. Write `tests/safety/test_attempts.py` with the exact content below:
  ```python
  """Tests for the attempts-counter helpers."""
  from __future__ import annotations

  from pathlib import Path

  import pytest

  from ralph_executor.safety.attempts import (
      AttemptCounter,
      AttemptsExceeded,
      max_attempts,
      read_attempts,
      write_attempts,
  )
  from tests.safety.conftest import write_pbi_dir


  def test_default_max_attempts_is_three(
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      monkeypatch.delenv("RALPH_MAX_ATTEMPTS", raising=False)
      assert max_attempts() == 3


  def test_env_override_for_max_attempts(
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "7")
      assert max_attempts() == 7


  def test_invalid_env_falls_back_to_default(
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "not-a-number")
      assert max_attempts() == 3


  def test_zero_or_negative_env_falls_back(
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "0")
      assert max_attempts() == 3
      monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "-2")
      assert max_attempts() == 3


  def test_read_attempts_returns_zero_when_field_missing(
      repo_dir: Path,
  ) -> None:
      pbi_dir = write_pbi_dir(
          repo_dir,
          bucket="current",
          pbi_id="WI-1",
          frontmatter={"attempts": 0},
      )
      assert read_attempts(pbi_dir) == 0


  def test_write_attempts_updates_frontmatter_in_place(
      repo_dir: Path,
  ) -> None:
      pbi_dir = write_pbi_dir(
          repo_dir,
          bucket="current",
          pbi_id="WI-1",
          frontmatter={"attempts": 1, "severity": "high"},
      )
      write_attempts(pbi_dir, value=4)
      assert read_attempts(pbi_dir) == 4
      # Other frontmatter must be preserved.
      text = (pbi_dir / "PBI.md").read_text(encoding="utf-8")
      assert "severity: high" in text


  def test_increment_returns_new_value(repo_dir: Path) -> None:
      pbi_dir = write_pbi_dir(
          repo_dir, bucket="current", pbi_id="WI-1", frontmatter={"attempts": 2}
      )
      counter = AttemptCounter(pbi_dir=pbi_dir)
      assert counter.increment() == 3
      assert read_attempts(pbi_dir) == 3


  def test_increment_raises_when_max_exceeded(
      repo_dir: Path, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "2")
      pbi_dir = write_pbi_dir(
          repo_dir, bucket="current", pbi_id="WI-1", frontmatter={"attempts": 2}
      )
      counter = AttemptCounter(pbi_dir=pbi_dir)
      with pytest.raises(AttemptsExceeded):
          counter.increment()


  def test_attempts_exceeded_records_pbi_id(repo_dir: Path) -> None:
      pbi_dir = write_pbi_dir(
          repo_dir, bucket="current", pbi_id="WI-attempts", frontmatter={"attempts": 99}
      )
      counter = AttemptCounter(pbi_dir=pbi_dir)
      with pytest.raises(AttemptsExceeded) as exc_info:
          counter.increment()
      assert exc_info.value.pbi_id == "WI-attempts"
      assert exc_info.value.attempts >= 99
  ```
  Expected: nine tests, all failing because `ralph_executor.safety.attempts` does not exist yet.

- [ ] 2. Write `ralph_executor/safety/attempts.py` with the exact content below. The frontmatter parser is intentionally small — a full YAML parser is overkill for an `attempts:` integer; we keep the rest of the document byte-identical and update only the targeted line:
  ```python
  """Per-PBI attempts counter (wraps the ``attempts:`` frontmatter field).

  The counter is consulted by the executor's loop driver at the start of
  each iteration on a given PBI: increment first, then either spawn
  claude-p (if the counter did not raise) or move the PBI to ``blocked/``
  via :func:`ralph_executor.safety.stuck.move_to_blocked` (if it did).
  """
  from __future__ import annotations

  import os
  import re
  from dataclasses import dataclass
  from pathlib import Path


  DEFAULT_MAX_ATTEMPTS = 3
  _MAX_ATTEMPTS_ENV = "RALPH_MAX_ATTEMPTS"
  _ATTEMPT_LINE = re.compile(r"^(attempts:\s*)(-?\d+)\s*$", flags=re.MULTILINE)
  _FRONTMATTER_FENCE = "---"
  _CANDIDATE_FILES = ("PBI.md", "BUG.md", "FEEDBACK.md")


  class AttemptsExceeded(RuntimeError):
      """Raised when incrementing the counter would exceed the configured max."""

      def __init__(self, *, pbi_id: str, attempts: int, limit: int) -> None:
          super().__init__(
              f"attempts ({attempts}) reached limit ({limit}) for {pbi_id}"
          )
          self.pbi_id = pbi_id
          self.attempts = attempts
          self.limit = limit


  def max_attempts() -> int:
      raw = os.environ.get(_MAX_ATTEMPTS_ENV, "").strip()
      if not raw:
          return DEFAULT_MAX_ATTEMPTS
      try:
          value = int(raw)
      except ValueError:
          return DEFAULT_MAX_ATTEMPTS
      if value <= 0:
          return DEFAULT_MAX_ATTEMPTS
      return value


  def _frontmatter_file(pbi_dir: Path) -> Path:
      for candidate in _CANDIDATE_FILES:
          path = pbi_dir / candidate
          if path.is_file():
              return path
      raise FileNotFoundError(
          f"no PBI frontmatter file ({_CANDIDATE_FILES}) under {pbi_dir}"
      )


  def read_attempts(pbi_dir: Path) -> int:
      path = _frontmatter_file(pbi_dir)
      text = path.read_text(encoding="utf-8")
      front, _body = _split_frontmatter(text)
      match = _ATTEMPT_LINE.search(front)
      if not match:
          return 0
      return int(match.group(2))


  def write_attempts(pbi_dir: Path, *, value: int) -> None:
      path = _frontmatter_file(pbi_dir)
      text = path.read_text(encoding="utf-8")
      front, body = _split_frontmatter(text)
      if _ATTEMPT_LINE.search(front):
          new_front = _ATTEMPT_LINE.sub(rf"\g<1>{value}", front)
      else:
          # Insert ``attempts:`` line just before the closing fence so the
          # frontmatter remains well-formed even on hand-authored PBIs that
          # forgot the field.
          new_front = front.rstrip() + f"\nattempts: {value}\n"
      path.write_text(_join_frontmatter(new_front, body), encoding="utf-8")


  def _split_frontmatter(text: str) -> tuple[str, str]:
      if not text.startswith(_FRONTMATTER_FENCE):
          raise ValueError("frontmatter must open with '---' on line 1")
      try:
          end_idx = text.index(f"\n{_FRONTMATTER_FENCE}", len(_FRONTMATTER_FENCE))
      except ValueError as exc:
          raise ValueError("frontmatter has no closing '---' fence") from exc
      front = text[len(_FRONTMATTER_FENCE) : end_idx]
      body = text[end_idx + len(f"\n{_FRONTMATTER_FENCE}") :]
      return front.strip("\n"), body


  def _join_frontmatter(front: str, body: str) -> str:
      return f"{_FRONTMATTER_FENCE}\n{front.strip()}\n{_FRONTMATTER_FENCE}{body}"


  @dataclass
  class AttemptCounter:
      """Mutable counter view over a PBI directory's ``attempts:`` field."""

      pbi_dir: Path

      def current(self) -> int:
          return read_attempts(self.pbi_dir)

      def increment(self) -> int:
          new_value = read_attempts(self.pbi_dir) + 1
          limit = max_attempts()
          if new_value > limit:
              raise AttemptsExceeded(
                  pbi_id=self.pbi_dir.name,
                  attempts=new_value,
                  limit=limit,
              )
          write_attempts(self.pbi_dir, value=new_value)
          return new_value
  ```

- [ ] 3. Extend `ralph_executor/safety/__init__.py` to re-export the new public names. Add the import and append to `__all__`:
  ```python
  from ralph_executor.safety.attempts import (
      AttemptCounter,
      AttemptsExceeded,
      max_attempts,
      read_attempts,
      write_attempts,
  )
  ```
  and add `"AttemptCounter"`, `"AttemptsExceeded"`, `"max_attempts"`, `"read_attempts"`, `"write_attempts"` to the `__all__` tuple (alphabetised).

- [ ] 4. Run the attempts tests:
  ```
  uv run pytest tests/safety/test_attempts.py -v
  ```
  Expected: nine tests pass.

- [ ] 5. Run ruff and mypy:
  ```
  uv run ruff check ralph_executor/safety/attempts.py tests/safety/test_attempts.py ralph_executor/safety/__init__.py
  uv run mypy ralph_executor/safety/attempts.py tests/safety/test_attempts.py ralph_executor/safety/__init__.py
  ```
  Expected: both clean.

- [ ] 6. Commit:
  ```
  git add ralph_executor/safety/attempts.py ralph_executor/safety/__init__.py tests/safety/test_attempts.py
  git commit -m "feat(safety): add attempts counter with RALPH_MAX_ATTEMPTS env override"
  ```

---

## Task 7 — Wire the safety hooks into Plan 7's loop driver

**Files**
- Modify: `ralph_executor/loop.py` (Plan 7's file)
- Modify: `tests/safety/test_integration_loop.py` (new file added in this task)

**Diff against Plan 7's `ralph_executor/loop.py`** — the exact integration points the safety hooks insert into are shown below. Plan 7 declared `run_iteration(repo: Path) -> IterationResult` as the loop's main entry point and `current_pbi(repo)` as the helper that returns the PBI dataclass for the current PBI (or `None`). The safety integration is purely additive: existing callers see no behavioural change unless cycle detector signals trip.

```diff
--- a/ralph_executor/loop.py        (Plan 7)
+++ b/ralph_executor/loop.py        (Plan 9)
@@ -10,7 +10,16 @@
 from ralph_executor.queue.filesystem import current_pbi, move_pbi
 from ralph_executor.spawn import spawn_claude_p
 from ralph_executor.types import PBI, IterationResult
+from ralph_executor.safety import (
+    AttemptCounter,
+    AttemptsExceeded,
+    HaltStatus,
+    HaltedError,
+    check_halt_sentinel,
+    evaluate_all,
+    halt_and_acknowledge,
+    handle_stuck,
+    open_log,
+    EventType,
+    Event,
+)


-def run_iteration(repo: Path) -> IterationResult:
+def run_iteration(repo: Path) -> IterationResult:
+    # Layer 3 — refuse to start an iteration while a halt sentinel is active.
+    status = check_halt_sentinel(repo)
+    if status == HaltStatus.HALTED:
+        raise HaltedError(
+            meta_bug_id="(see .ralph/state/halted)",
+            meta_bug_path=repo / ".ralph" / "blocked",
+            sentinel_path=repo / ".ralph" / "state" / "halted",
+        )
+    log = open_log(repo)
+    try:
         pbi = current_pbi(repo)
         if pbi is None:
             # ...existing fresh-PBI claim path (Plan 7)...
             pbi = _claim_next_pbi(repo)
             if pbi is None:
                 return IterationResult.idle()
+        counter = AttemptCounter(pbi_dir=pbi.path)
+        try:
+            new_attempts = counter.increment()
+        except AttemptsExceeded as exc:
+            log.append(
+                Event(
+                    kind=EventType.PBI_BLOCKED,
+                    recorded_at=datetime.now(tz=timezone.utc),
+                    pbi_id=pbi.id,
+                    payload={"reason": str(exc), "source": "max-attempts"},
+                )
+            )
+            return _move_to_blocked_for_max_attempts(repo, pbi, exc)
+        log.append(
+            Event(
+                kind=EventType.ATTEMPT_INCREMENTED,
+                recorded_at=datetime.now(tz=timezone.utc),
+                pbi_id=pbi.id,
+                payload={"attempts": new_attempts},
+            )
+        )
         outcome = spawn_claude_p(repo=repo, pbi=pbi)
+        # Layer 1 — if Ralph wrote STUCK.md, move the PBI to blocked/.
+        stuck = handle_stuck(repo=repo, pbi_dir=pbi.path, now=datetime.now(tz=timezone.utc))
+        if stuck is not None:
+            log.append(stuck.event)
+            outcome = outcome.with_blocked(reason=stuck.reason)
+        # Loop-end bookkeeping for the cycle detector.
+        log.append(
+            Event(
+                kind=EventType.ITERATION_COMPLETED,
+                recorded_at=datetime.now(tz=timezone.utc),
+                pbi_id=pbi.id,
+                payload={"outcome": outcome.summary()},
+            )
+        )
+        # Layer 3 — evaluate detector rules.
+        now = datetime.now(tz=timezone.utc)
+        signals = evaluate_all(log.recent(window=timedelta(hours=72), now=now), now)
+        if signals:
+            halt_and_acknowledge(repo=repo, signals=signals, now=now)
+            raise HaltedError(
+                meta_bug_id="(see latest META-cycle-* in .ralph/blocked/)",
+                meta_bug_path=repo / ".ralph" / "blocked",
+                sentinel_path=repo / ".ralph" / "state" / "halted",
+            )
         return outcome
+    finally:
+        log.close()
```

Notes about the diff:

- Plan 7's `IterationResult.with_blocked(reason=...)` is the existing dataclass mutator for "this iteration moved the PBI to blocked/". If Plan 7 named the mutator differently, rename the call site here to match — the safety package does not depend on that name.
- `_move_to_blocked_for_max_attempts` is a small helper the implementer adds in Plan 7's `loop.py` (next to `_claim_next_pbi`). Its body is:
  ```python
  def _move_to_blocked_for_max_attempts(
      repo: Path, pbi: PBI, exc: AttemptsExceeded
  ) -> IterationResult:
      """Move ``pbi`` to ``.ralph/blocked/`` after the attempts counter fired."""
      target = repo / ".ralph" / "blocked" / pbi.id
      target.parent.mkdir(parents=True, exist_ok=True)
      shutil.move(str(pbi.path), str(target))
      return IterationResult.blocked(
          pbi=pbi, reason=f"max attempts exceeded ({exc.attempts}/{exc.limit})"
      )
  ```
- The `from datetime import datetime, timezone, timedelta` import is added next to Plan 7's existing imports. Add `shutil` to the same imports group if not already present.

**Steps**

- [ ] 1. Apply the diff above to `ralph_executor/loop.py`. Implementer must reconcile the diff against Plan 7's actual file contents — the diff describes the integration shape, not a literal patch. If Plan 7 named the loop function `run_loop` instead of `run_iteration`, or if `current_pbi` was placed in a different module, adjust the import and call site accordingly; everything else in the diff stays as-is.

- [ ] 2. Write `tests/safety/test_integration_loop.py` with the exact content below. This test drives the real `run_iteration` through the safety hooks, monkeypatching only the Claude spawn (the unit under test is the integration, not Claude itself):
  ```python
  """End-to-end safety integration test against ``ralph_executor.loop``."""
  from __future__ import annotations

  from datetime import datetime, timedelta, timezone
  from pathlib import Path

  import pytest

  from ralph_executor.loop import run_iteration
  from ralph_executor.safety import HaltedError, HaltStatus, check_halt_sentinel
  from ralph_executor.safety.events import EventType, open_log
  from tests.safety.conftest import make_event, write_pbi_dir


  @pytest.fixture
  def repo_with_current(repo_dir: Path) -> Path:
      write_pbi_dir(repo_dir, bucket="current", pbi_id="WI-active")
      return repo_dir


  def test_iteration_refuses_to_start_when_sentinel_halted(
      repo_with_current: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      # Write a halt sentinel by hand (HALTED state).
      sentinel = repo_with_current / ".ralph" / "state" / "halted"
      sentinel.write_text(
          "meta_bug_id: META-test\nacknowledged_by:\nacknowledged_at:\n",
          encoding="utf-8",
      )
      assert check_halt_sentinel(repo_with_current) == HaltStatus.HALTED
      with pytest.raises(HaltedError):
          run_iteration(repo_with_current)


  def test_iteration_resumes_once_sentinel_acknowledged(
      repo_with_current: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      # Stub spawn_claude_p so the test doesn't actually invoke Claude.
      from ralph_executor import loop as loop_module

      monkeypatch.setattr(
          loop_module,
          "spawn_claude_p",
          lambda *, repo, pbi: loop_module.IterationResult.idle(),
      )

      sentinel = repo_with_current / ".ralph" / "state" / "halted"
      sentinel.write_text(
          "meta_bug_id: META-test\n"
          "acknowledged_by: gethin\n"
          "acknowledged_at: 2026-05-24T12:00:00+00:00\n",
          encoding="utf-8",
      )
      assert check_halt_sentinel(repo_with_current) == HaltStatus.ACKNOWLEDGED
      # Acknowledged sentinel must not block the loop.
      run_iteration(repo_with_current)


  def test_iteration_triggers_halt_when_detector_fires(
      repo_with_current: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      # Pre-seed the event log so signature_recurrence trips on the next
      # iteration. We add a PBI_CLOSED with signature S 23h ago and a
      # SIGNATURE_OBSERVED with the same signature 1h ago.
      log = open_log(repo_with_current)
      try:
          now = datetime.now(tz=timezone.utc)
          log.append(
              make_event(
                  kind=EventType.PBI_CLOSED,
                  recorded_at=now.replace(microsecond=0) - timedelta(hours=23),
                  pbi_id="BUG-old",
                  payload={"signature": "ZeroDivisionError @ calc.py:10"},
              )
          )
          log.append(
              make_event(
                  kind=EventType.SIGNATURE_OBSERVED,
                  recorded_at=now.replace(microsecond=0) - timedelta(hours=1),
                  pbi_id="BUG-new",
                  payload={"signature": "ZeroDivisionError @ calc.py:10"},
              )
          )
      finally:
          log.close()

      from ralph_executor import loop as loop_module

      monkeypatch.setattr(
          loop_module,
          "spawn_claude_p",
          lambda *, repo, pbi: loop_module.IterationResult.idle(),
      )

      with pytest.raises(HaltedError):
          run_iteration(repo_with_current)

      # The current PBI must remain in current/ (spec: do NOT kick to inbox).
      assert (repo_with_current / ".ralph" / "current" / "WI-active").is_dir()
      # A META-cycle-* file was written.
      blocked = repo_with_current / ".ralph" / "blocked"
      meta_bugs = list(blocked.glob("META-cycle-*.md"))
      assert meta_bugs, "halt_and_acknowledge must write a META-cycle-* file"


  def test_attempts_exceeded_moves_pbi_to_blocked(
      repo_dir: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "1")
      write_pbi_dir(
          repo_dir,
          bucket="current",
          pbi_id="WI-overrun",
          frontmatter={"attempts": 1},
      )
      from ralph_executor import loop as loop_module

      monkeypatch.setattr(
          loop_module,
          "spawn_claude_p",
          lambda *, repo, pbi: loop_module.IterationResult.idle(),
      )

      result = run_iteration(repo_dir)
      # The PBI must no longer be in current/ — it moved to blocked/.
      assert not (repo_dir / ".ralph" / "current" / "WI-overrun").exists()
      assert (repo_dir / ".ralph" / "blocked" / "WI-overrun").is_dir()
      assert result.blocked is True or "blocked" in result.summary().lower()
  ```
  Note: the integration test uses `from ralph_executor import loop as loop_module` and then `monkeypatch.setattr(loop_module, "spawn_claude_p", ...)` — this requires Plan 7 to import `spawn_claude_p` at module scope (not inside `run_iteration`) so the monkeypatch substitutes the name the loop function actually resolves. If Plan 7 imports `spawn_claude_p` lazily, change the monkeypatch target to the actual module location (e.g. `ralph_executor.spawn`).

- [ ] 3. Run the integration tests:
  ```
  uv run pytest tests/safety/test_integration_loop.py -v
  ```
  Expected: four tests pass. If `run_iteration` was named differently by Plan 7, surface that mismatch before continuing — the test file's import must match Plan 7's public API.

- [ ] 4. Run the full safety test subset to confirm nothing else regressed:
  ```
  uv run pytest tests/safety/ -v
  ```
  Expected: every safety test (events + stuck + cycle_detector + halt + attempts + integration) passes — sixty-something tests in total depending on Plan 7's API surface.

- [ ] 5. Run ruff and mypy across the safety package + loop:
  ```
  uv run ruff check ralph_executor/safety/ ralph_executor/loop.py tests/safety/
  uv run mypy ralph_executor/safety/ ralph_executor/loop.py tests/safety/
  ```
  Expected: both clean. If mypy complains about `IterationResult.with_blocked(reason=...)` or `IterationResult.blocked`, that means Plan 7 exported a different shape — add the missing method to `ralph_executor/types.py` (Plan 7's file) so the safety hooks compile. This is an additive change to Plan 7's dataclass; do NOT change the safety integration to work around it.

- [ ] 6. Commit:
  ```
  git add ralph_executor/loop.py tests/safety/test_integration_loop.py ralph_executor/types.py
  git commit -m "feat(safety): wire STUCK/cycle/attempts hooks into the loop driver"
  ```
  (Include `ralph_executor/types.py` in the commit only if you had to extend `IterationResult` in step 5.)

---

## Task 8 — Full toolchain pass

**Files**
- (none — toolchain-only step)

**Steps**

- [ ] 1. Run the whole local gate to confirm Plan 9's deliverables don't disturb Plans 1–8:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  uv run pytest
  ```
  Expected: every command exits 0. Pytest's total test count is the sum of all preceding plans plus Plan 9's safety tests. Mypy passes with `Success: no issues found`.

- [ ] 2. Confirm the safety package exposes the documented public API:
  ```
  uv run python -c "from ralph_executor import safety; assert hasattr(safety, 'evaluate_all'); assert hasattr(safety, 'halt_and_acknowledge'); assert hasattr(safety, 'check_halt_sentinel'); assert hasattr(safety, 'handle_stuck'); assert hasattr(safety, 'AttemptCounter'); assert hasattr(safety, 'max_attempts'); print('safety API OK')"
  ```
  Expected output: `safety API OK`.

- [ ] 3. Confirm the commit history is clean and conventional:
  ```
  git log --oneline -10
  ```
  Expected: six Plan-9 commits at the top of the log, each with a conventional-commit prefix:
  - `chore(safety): scaffold safety package and add pytest-freezegun`
  - `feat(safety): add append-only SQLite event log for cycle detection`
  - `feat(safety): add STUCK.md handler that moves stuck PBIs to blocked/`
  - `feat(safety): add six pure cycle-detector rules + aggregator`
  - `feat(safety): add halt-and-ack mechanism (META-BUG, sentinel, webhook)`
  - `feat(safety): add attempts counter with RALPH_MAX_ATTEMPTS env override`
  - `feat(safety): wire STUCK/cycle/attempts hooks into the loop driver`
  (Seven total — count is correct if Task 7's optional `types.py` change folded into one commit.)

---

## Verification

This is the orchestrator's Plan 9 verification gate. The gate command from `2026-05-24-00-orchestrator.md` is:

> `uv run pytest tests/executor/test_safety.py -v` — All pass; cycle detector triggers correctly on synthetic input.

Plan 9 implements the safety package at `tests/safety/` (rather than `tests/executor/test_safety.py`) because the package is large enough to warrant its own subtree — same convention Plan 7 follows. The orchestrator's gate command therefore expands to:

> `uv run pytest tests/safety/ -v` — All pass; cycle detector triggers correctly on synthetic input.

The verification has two parts: the hermetic artifact gate (no network, no real Claude), and a manual rehearsal that simulates a halt-and-acknowledge cycle on a throwaway repo.

### Part 1 — artifact gate (no network)

- [ ] 1. Run the safety test subtree in isolation:
  ```
  uv run pytest tests/safety/ -v
  ```
  Expected: every test (events + stuck + cycle_detector + halt + attempts + integration_loop) passes. Roughly sixty tests; the exact count depends on Plan 7's API surface.

- [ ] 2. Run the full repo gate to confirm Plan 9 didn't disturb earlier plans:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  uv run pytest
  ```
  Expected: every command exits 0. The combined test count is the sum of Plans 1–9.

- [ ] 3. Confirm each detector rule trips on at least one synthetic input. Run:
  ```
  uv run pytest tests/safety/test_cycle_detector.py -v -k trips
  ```
  Expected: every test named `*_trips_*` passes — every detector has a positive case.

- [ ] 4. Confirm `RALPH_MAX_ATTEMPTS` is honoured. Run:
  ```
  RALPH_MAX_ATTEMPTS=5 uv run python -c "from ralph_executor.safety.attempts import max_attempts; print(max_attempts())"
  ```
  Expected output: `5`.

### Part 2 — manual rehearsal of halt-and-acknowledge (once per environment)

The orchestrator gate also expects a human-driven rehearsal to prove the halt cycle behaves end-to-end. This is the dry run for the "wake up to a META-BUG in `blocked/`" operator workflow.

- [ ] 5. Pick a throwaway repo (or a temp directory). Initialise the `.ralph/` skeleton:
  ```
  mkdir -p /tmp/ralph-safety-rehearsal/.ralph/{inbox,current,pending-pr,done,blocked,state}
  cd /tmp/ralph-safety-rehearsal
  git init -q
  git commit --allow-empty -m "chore: bootstrap"
  ```

- [ ] 6. Use the Python REPL to drive a synthetic halt:
  ```
  uv run python <<'PY'
  from datetime import datetime, timedelta, timezone
  from pathlib import Path
  from ralph_executor.safety import (
      halt_and_acknowledge,
      check_halt_sentinel,
      open_log,
      EventType,
      Event,
  )
  from ralph_executor.safety.cycle_detector import (
      CycleSignal,
      SignalKind,
      evaluate_signature_recurrence,
  )

  repo = Path("/tmp/ralph-safety-rehearsal")
  log = open_log(repo)
  now = datetime.now(tz=timezone.utc)
  log.append(Event(
      kind=EventType.PBI_CLOSED,
      recorded_at=now - timedelta(hours=23),
      pbi_id="BUG-1",
      payload={"signature": "TestSig"},
  ))
  log.append(Event(
      kind=EventType.SIGNATURE_OBSERVED,
      recorded_at=now - timedelta(hours=1),
      pbi_id="BUG-2",
      payload={"signature": "TestSig"},
  ))
  signals = [evaluate_signature_recurrence(
      log.recent(window=timedelta(hours=24), now=now), now
  )]
  meta = halt_and_acknowledge(repo=repo, signals=[s for s in signals if s])
  log.close()
  print("META-BUG written at:", meta.path)
  print("Halt status:", check_halt_sentinel(repo).value)
  PY
  ```
  Expected output: a META-BUG path printed, plus `Halt status: halted`. Inspect the META-BUG file and confirm it contains frontmatter (`id`, `type: meta`, `severity: critical`), a signals section, and a queue-snapshot section.

- [ ] 7. Acknowledge the halt:
  ```
  # Edit .ralph/state/halted with your editor of choice; fill in
  # acknowledged_by and acknowledged_at lines.
  ```
  Then confirm:
  ```
  uv run python -c "from pathlib import Path; from ralph_executor.safety import check_halt_sentinel; print(check_halt_sentinel(Path('/tmp/ralph-safety-rehearsal')).value)"
  ```
  Expected output: `acknowledged`.

- [ ] 8. Clean up:
  ```
  rm -rf /tmp/ralph-safety-rehearsal
  ```

If steps 1–8 all pass, Plan 9 is complete. The next plan in the dependency graph is Plan 12 (ROSA packaging — depends on Plan 9 alongside Plans 5, 7, 8, 11), and Plan 13 (end-to-end smoke) which exercises Plan 9 indirectly through real loop iterations on a throwaway repo.

### Concerns and known gaps

- **Plan 2 (auto code review)** is intentionally absent. The spec is explicit that the team's existing PR comment loop fills the role of mechanical guards; v1 does not add a redundant "ralph-guards" CI stage. If a future v2 ralph-guards stage materialises, it lands in a new sub-plan and integrates with the same event log via additional `EventType` values.
- **Detector thresholds are tunable constants in `cycle_detector.py`**, not configurable via env vars. The spec's v1 thresholds are the only data we have; revisit once the executor runs against a real repo and we observe false-positive rates.
- **Halt notification is fire-and-forget.** A webhook delivery failure logs to stderr but does NOT block the halt itself — the META-BUG + sentinel are still written. This is deliberate: the sentinel is the load-bearing safety mechanism; the webhook is convenience.
- **Sentinel parsing is line-based, not full YAML.** The intent is to keep the sentinel hand-editable in any text editor; full YAML would surprise operators who forget quotes around timestamps. If a future plan wants stricter parsing, swap in `yaml.safe_load` but keep the file format byte-compatible.
- **Cross-platform notes.** All filesystem paths use `pathlib.Path`; `shutil.move` is used for cross-device safety. The integration test suite passes on Windows, macOS, and Linux. The webhook test uses `responses`, not real HTTP.
- **Concurrency.** The event log uses SQLite WAL mode + a per-connection lock; one executor process per repo is supported, which matches the spec's "one Ralph per repo" v1 constraint. Multi-executor support is out of scope.
