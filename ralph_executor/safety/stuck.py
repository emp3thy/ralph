"""Layer 1 safety: detect ``STUCK.md`` and relocate the PBI to ``blocked/``.

This is per-PBI self-halt -- the loop keeps running; only the offending
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


def move_to_blocked(*, repo: Path, pbi_dir: Path, reason: str) -> Path:
    """Move ``pbi_dir`` from ``.ralph/current/<id>/`` to ``.ralph/blocked/<id>/``.

    Appends a HISTORY.md line recording the reason. Raises ``ValueError``
    if the directory is not under ``.ralph/current/``, and
    ``FileExistsError`` if a sibling already occupies the blocked slot.
    """
    try:
        relative = pbi_dir.relative_to(repo)
    except ValueError as exc:
        raise ValueError(f"{pbi_dir} is not inside {repo}") from exc
    parts = relative.parts
    if len(parts) < 3 or parts[0] != ".ralph" or parts[1] != "current":
        raise ValueError(f"{pbi_dir} must be under .ralph/current/<pbi-id>/")
    pbi_id = parts[2]
    blocked_root = repo / ".ralph" / "blocked"
    blocked_root.mkdir(parents=True, exist_ok=True)
    target = blocked_root / pbi_id
    if target.exists():
        raise FileExistsError(f"blocked target already occupied: {target}; resolve manually")
    # Move the whole directory (preserves attachments, PLAN.md, etc.).
    shutil.move(str(pbi_dir), str(target))
    _append_history_line(target, reason)
    return target


def handle_stuck(*, repo: Path, pbi_dir: Path, now: datetime) -> StuckOutcome | None:
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
    stamp = f"\n---\nSTUCK -- moved to blocked/\nreason: {reason}\n"
    history.write_text(existing + stamp, encoding="utf-8")
