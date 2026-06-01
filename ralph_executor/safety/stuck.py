"""Layer 1 safety: detect ``STUCK.md`` and relocate the PBI to ``blocked/``.

This is per-PBI self-halt -- the loop keeps running; only the offending
PBI is moved out of ``current/`` and into ``blocked/`` with a HISTORY.md
audit entry recording the reason. The cycle detector observes the
resulting ``pbi.blocked`` event through the shared event log.

The folder relocation is routed through ``queue.movements.move_current_to_blocked``
so the ``git mv`` + commit + push all happen atomically in a single
queue-branch commit. Without that, ``shutil.move`` would leave the move
stranded in the queue clone's working tree: ``current/<id>/*`` would
show as ``D`` (deleted) and ``blocked/<id>/`` as ``??`` (untracked),
origin/<queue-branch> would still show the PBI in ``current/``, and the
loop would be unable to make forward progress on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ralph_executor import git_ops
from ralph_executor.config import ExecutorConfig
from ralph_executor.safety.events import Event, EventLog, EventType, signature_from_text
from ralph_executor.types import PBI

# ``queue.movements`` is imported lazily inside ``move_to_blocked`` to break
# the import cycle: ``movements`` imports from ``safety.events``, and the
# ``safety`` package's ``__init__`` re-exports from this module. A top-level
# ``from ralph_executor.queue.movements import …`` would race the
# half-initialised ``movements`` module during package load.

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


def move_to_blocked(*, cfg: ExecutorConfig, pbi: PBI, reason: str) -> Path:
    """Move the PBI from ``.ralph/current/<id>/`` to ``.ralph/blocked/<id>/``.

    Appends a HISTORY.md line recording the reason, stages all working-tree
    changes inside the PBI dir (so untracked files like ``STUCK.md`` and
    any frontmatter edits the attempt counter wrote are included), then
    delegates to :func:`queue.movements.move_current_to_blocked` for the
    actual ``git mv`` + frontmatter status rewrite + commit + push.

    Append-before-stage mirrors ``loop._move_to_blocked_with_reason`` so
    the reason lands in the same commit as the move rather than as a
    follow-up edit at the new path (which would re-introduce the same
    uncommitted-edit drift the routing through ``move_current_to_blocked``
    exists to prevent).

    Raises:
        ValueError: PBI dir is not under ``.ralph/current/<id>/``.
        QueueMovementError: precondition violation inside the movement
            helper (e.g. blocked target already occupied; wrong status).
        PushRebaseConflict: queue branch raced; caller treats as recoverable.
    """
    # Lazy import to break the safety <-> queue.movements import cycle
    # (see the module-level note above the import block).
    from ralph_executor.queue.movements import move_current_to_blocked

    queue_repo = cfg.queue_clone_path
    pbi_dir = queue_repo / ".ralph" / "current" / pbi.id
    if not pbi_dir.is_dir():
        raise ValueError(f"PBI {pbi.id} is not in .ralph/current/ (looked at {pbi_dir})")
    _append_history_line(pbi_dir, reason)
    # Without this stage step, ``git mv`` of the directory would leave
    # untracked files (notably the ``STUCK.md`` Claude just wrote, plus
    # any new PLAN.md/HISTORY.md entries) stranded at the source path —
    # git mv only moves files that are in the index.
    git_ops.add_all_changes(queue_repo, pbi_dir)
    move_current_to_blocked(cfg, pbi)
    return queue_repo / ".ralph" / "blocked" / pbi.id


def handle_stuck(
    *,
    cfg: ExecutorConfig,
    pbi: PBI,
    now: datetime,
    event_log: EventLog | None = None,
) -> StuckOutcome | None:
    """Top-level Layer 1 hook called by the executor's loop driver.

    If ``STUCK.md`` is present, moves the PBI to ``blocked/``, appends
    a HISTORY entry, and returns a :class:`StuckOutcome` carrying the
    blocked path, the truncated reason, and the synthetic
    ``pbi.blocked`` event the caller should append to the event log.
    Returns ``None`` if there is no STUCK.md (the iteration's normal
    outcome).

    When ``event_log`` is provided, a ``signature.observed`` event is
    appended carrying the hashed reason text so the cycle detector's
    ``signature_recurrence`` rule can spot the same blocker recurring
    across PBIs. Emission happens before ``move_to_blocked`` so the
    signature is logged even if the move fails.

    The move itself is committed + pushed on the queue branch by
    ``move_current_to_blocked`` (via ``movements._move``); callers that
    catch ``PushRebaseConflict`` get a recoverable signal when the queue
    branch raced.
    """
    queue_repo = cfg.queue_clone_path
    pbi_dir = queue_repo / ".ralph" / "current" / pbi.id
    if not detect_stuck(pbi_dir):
        return None
    reason = read_stuck_reason(pbi_dir)
    if event_log is not None:
        event_log.append(
            Event(
                kind=EventType.SIGNATURE_OBSERVED,
                recorded_at=now,
                pbi_id=pbi.id,
                payload={"signature": signature_from_text(reason)},
            )
        )
    target = move_to_blocked(cfg=cfg, pbi=pbi, reason=reason)
    event = Event(
        kind=EventType.PBI_BLOCKED,
        recorded_at=now,
        pbi_id=pbi.id,
        payload={"reason": reason, "source": "stuck-self-halt"},
    )
    return StuckOutcome(blocked_path=target, reason=reason, event=event)


def _append_history_line(pbi_dir: Path, reason: str) -> None:
    history = pbi_dir / _HISTORY_FILE
    existing = history.read_text(encoding="utf-8") if history.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    stamp = f"\n---\nSTUCK -- moved to blocked/\nreason: {reason}\n"
    history.write_text(existing + stamp, encoding="utf-8")
