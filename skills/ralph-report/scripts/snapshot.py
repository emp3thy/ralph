"""Read the snapshot panels (current/inbox/pending-pr/blocked/done) for ralph-report.

This module reuses ``scripts.pbi_reader.enumerate_state`` for PBI directory
parsing. It does NOT read the timeline / done-24h commit data — those come
from ``git_walker.py`` because they are commit-history-derived.

The reader takes the operator queue clone (``<workspace_root>/queue/``)
directly, the SAME tree ``ralph-status``, ``ralph-cancel``, and
``ralph-promote`` read. The previous source-repo worktree lookup and
``git show origin/ralph-queue:`` temp-mirror fallback have been removed —
the operator clone is the single source of truth (see
``BUG-RALPH-REPORT-UNIFY-DATA-SOURCE``).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# Make ``scripts.pbi_reader`` importable when this module is loaded from
# the ralph repo root (whether via importlib in tests or as a flat import
# from ``report.py`` at runtime).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.pbi_reader import PBIRow, PBIRowError, enumerate_state  # noqa: E402


@dataclass(frozen=True)
class MetaCycleSentinel:
    """A ``META-cycle-*.md`` sentinel file under ``.ralph/blocked/``."""

    filename: str


@dataclass
class Snapshot:
    current: list[PBIRow | PBIRowError] = field(default_factory=list)
    inbox: list[PBIRow | PBIRowError] = field(default_factory=list)
    pending_pr: list[PBIRow | PBIRowError] = field(default_factory=list)
    blocked: list[PBIRow | PBIRowError] = field(default_factory=list)
    done: list[PBIRow | PBIRowError] = field(default_factory=list)
    meta_cycle_sentinels: list[MetaCycleSentinel] = field(default_factory=list)


def _collect_meta_cycle_sentinels(blocked_dir: Path) -> list[MetaCycleSentinel]:
    if not blocked_dir.is_dir():
        return []
    sentinels: list[MetaCycleSentinel] = []
    for child in sorted(blocked_dir.iterdir()):
        if child.is_file() and child.name.startswith("META-cycle-") and child.suffix == ".md":
            sentinels.append(MetaCycleSentinel(filename=child.name))
    return sentinels


def load_snapshot(*, queue_clone: Path) -> Snapshot:
    """Read snapshot panels from the operator queue clone.

    ``queue_clone`` is the root of the queue clone (the directory that
    holds ``.ralph/`` directly). Returns an empty snapshot if
    ``.ralph/`` is missing (e.g. a freshly-initialised clone).
    """
    ralph_dir = queue_clone / ".ralph"
    return Snapshot(
        current=enumerate_state(queue_clone, "current"),
        inbox=enumerate_state(queue_clone, "inbox"),
        pending_pr=enumerate_state(queue_clone, "pending-pr"),
        blocked=enumerate_state(queue_clone, "blocked"),
        done=enumerate_state(queue_clone, "done"),
        meta_cycle_sentinels=_collect_meta_cycle_sentinels(ralph_dir / "blocked"),
    )
