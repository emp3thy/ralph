"""Per-PBI sidecar persisted at ``<pbi-dir>/.ralph-state.json``.

The sidecar lets the sweep distinguish "PR has new active comments" from
"PR has comments we already turned into a feedback PBI last round". Without
this, the sweep would loop forever generating duplicate feedback PBIs.

``last_ci_status`` tracks the prior tick's CI bucket so the runner can
detect green→red transitions (Plan 19b PR_GREEN_THEN_RED emission). The
empty string is the "never observed" sentinel for backward compatibility
with sidecars written before Plan 19b.

Storage layout (per-PBI, NOT global):

    {
      "last_feedback_sweep": "2026-05-24T12:00:00+00:00",
      "last_feedback_round": 2,
      "last_seen_comment_ids": ["10:1", "10:2", "11:5"],
      "last_ci_status": "succeeded"
    }

Comment ids are stored as ``"<thread_id>:<comment_id>"`` so they remain
unique across threads.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SIDECAR_FILENAME = ".ralph-state.json"


@dataclass(frozen=True)
class SweepSidecar:
    last_feedback_sweep: datetime | None
    last_feedback_round: int
    # frozenset, not set — @dataclass(frozen=True) auto-generates __hash__
    # which calls hash() on every field. A plain set is unhashable, so a
    # SweepSidecar used as a dict key / set member would raise TypeError.
    # No call site does that today, but the frozen=True annotation
    # implies hashability — keep the contract honest.
    last_seen_comment_ids: frozenset[str] = field(default_factory=frozenset)
    last_ci_status: str = ""


def load_sidecar(pbi_dir: Path) -> SweepSidecar:
    """Return the sidecar for ``pbi_dir``. Missing or corrupt → empty default."""
    path = pbi_dir / SIDECAR_FILENAME
    if not path.is_file():
        return SweepSidecar(
            last_feedback_sweep=None,
            last_feedback_round=0,
            last_seen_comment_ids=frozenset(),
            last_ci_status="",
        )
    # Catch OSError alongside JSONDecodeError: an EACCES / ESTALE /
    # ENOSPC from read_text would otherwise propagate through
    # _process_pbi and run()'s except _SweepPbiError, aborting all
    # remaining PBIs. Returning the default sidecar is the right
    # behaviour — same as the existing JSONDecodeError + non-dict
    # branches: missing/unreadable file = "never swept", let the sweep
    # proceed.
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return SweepSidecar(
            last_feedback_sweep=None,
            last_feedback_round=0,
            last_seen_comment_ids=frozenset(),
            last_ci_status="",
        )
    if not isinstance(raw, dict):
        return SweepSidecar(
            last_feedback_sweep=None,
            last_feedback_round=0,
            last_seen_comment_ids=frozenset(),
            last_ci_status="",
        )
    sweep_raw = raw.get("last_feedback_sweep")
    sweep_dt: datetime | None
    if isinstance(sweep_raw, str) and sweep_raw:
        try:
            sweep_dt = datetime.fromisoformat(sweep_raw)
        except ValueError:
            sweep_dt = None
    else:
        sweep_dt = None
    # A manually-edited sidecar with wrong-type values (e.g.
    # last_feedback_round="abc", last_seen_comment_ids=42) would
    # otherwise raise ValueError / TypeError from int() / iteration
    # and escape every per-PBI guard. Treat as corrupt → default.
    try:
        ci_raw = raw.get("last_ci_status")
        return SweepSidecar(
            last_feedback_sweep=sweep_dt,
            last_feedback_round=int(raw.get("last_feedback_round", 0) or 0),
            last_seen_comment_ids=frozenset(
                str(x) for x in (raw.get("last_seen_comment_ids") or [])
            ),
            last_ci_status=str(ci_raw) if isinstance(ci_raw, str) else "",
        )
    except (ValueError, TypeError):
        return SweepSidecar(
            last_feedback_sweep=None,
            last_feedback_round=0,
            last_seen_comment_ids=frozenset(),
            last_ci_status="",
        )


def write_sidecar(pbi_dir: Path, sidecar: SweepSidecar) -> None:
    """Atomically write ``sidecar`` to ``pbi_dir/.ralph-state.json``."""
    pbi_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_feedback_sweep": (
            sidecar.last_feedback_sweep.isoformat()
            if sidecar.last_feedback_sweep is not None
            else None
        ),
        "last_feedback_round": sidecar.last_feedback_round,
        "last_seen_comment_ids": sorted(sidecar.last_seen_comment_ids),
        "last_ci_status": sidecar.last_ci_status,
    }
    tmp = pbi_dir / (SIDECAR_FILENAME + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(pbi_dir / SIDECAR_FILENAME)


def merge_seen_comment_ids(existing: frozenset[str], new: Iterable[str]) -> frozenset[str]:
    """Return the union of ``existing`` and ``new`` (never shrinks)."""
    return existing | frozenset(str(x) for x in new)
