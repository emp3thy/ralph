"""HISTORY.md primitives for the sweep: append an entry, move a PBI directory.

Both helpers take exactly what they consume (the sweep's ``now``
timestamp) instead of the runner's ``SweepContext`` so this module stays
leaf-level within ``sweep/`` and never imports the runner.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from ralph_executor.sweep.types import SweepPbiError


def move_with_history(src: Path, dst: Path, reason: str, now: datetime) -> None:
    """Move a PBI directory and append the reason to HISTORY.md at the destination.

    The move happens before the history write so a failed move never
    leaves a "moved" entry contradicting what is on disk; every I/O
    failure is wrapped in :class:`SweepPbiError` for per-PBI isolation.
    """
    # Stage the move FIRST so a failure (EXDEV cross-device, EACCES
    # permission denied, ENOSPC disk full, …) doesn't leave a spurious
    # "moved" entry in HISTORY.md that contradicts what's on disk.
    # Wrap mkdir + move in SweepPbiError so the per-PBI isolation in
    # run() catches OSError at every IO step.
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SweepPbiError(f"failed to create {dst.parent}: {exc}") from exc
    if dst.exists():
        raise SweepPbiError(f"destination {dst} already exists")
    try:
        shutil.move(str(src), str(dst))
    except OSError as exc:
        raise SweepPbiError(f"failed to move {src} to {dst}: {exc}") from exc
    # Append history to the NEW location — src no longer exists after
    # a successful move. Original behaviour wrote to src BEFORE move,
    # so on success the entry travelled along; preserve that semantic
    # by writing to dst after the move completes.
    append_history(dst, reason, now)


def append_history(pbi_dir: Path, reason: str, now: datetime) -> None:
    """Append a timestamped sweep entry to the PBI's HISTORY.md.

    OSError is converted to :class:`SweepPbiError` here so every call
    site uniformly stays inside run()'s per-PBI isolation.
    """
    # Wrap IO at the source so EVERY call site (PING_REVIEWER dispatch,
    # move_with_history, emit_feedback_pbi) gets OSError → SweepPbiError
    # conversion uniformly. Without this, a disk-full / EACCES /
    # EROFS error from read_text or write_text escapes run()'s
    # per-PBI isolation and aborts the remaining sweep.
    history = pbi_dir / "HISTORY.md"
    line = f"- {now.isoformat()} sweep: {reason}\n"
    try:
        prior = history.read_text(encoding="utf-8") if history.exists() else ""
        history.write_text(prior + line, encoding="utf-8")
    except OSError as exc:
        raise SweepPbiError(f"failed to append HISTORY.md in {pbi_dir}: {exc}") from exc
