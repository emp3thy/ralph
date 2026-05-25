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
