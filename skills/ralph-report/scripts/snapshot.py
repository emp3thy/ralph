"""Read the snapshot panels (current/inbox/pending-pr/blocked/done) from the ralph-queue worktree.

This module reuses ``scripts.pbi_reader.enumerate_state`` for PBI directory
parsing. It does NOT read the timeline / done-24h commit data — those come
from ``git_walker.py`` because they are commit-history-derived.

The reader prefers the queue worktree at ``<repo>/.ralph-work/queue``.
A ``ls-tree`` / ``git show`` fallback for the no-worktree case is
implemented in Task 1b (function ``_load_via_git_show``).
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
    path: Path


@dataclass
class Snapshot:
    current: list[PBIRow | PBIRowError] = field(default_factory=list)
    inbox: list[PBIRow | PBIRowError] = field(default_factory=list)
    pending_pr: list[PBIRow | PBIRowError] = field(default_factory=list)
    blocked: list[PBIRow | PBIRowError] = field(default_factory=list)
    done: list[PBIRow | PBIRowError] = field(default_factory=list)
    meta_cycle_sentinels: list[MetaCycleSentinel] = field(default_factory=list)


class SnapshotError(RuntimeError):
    """Raised when the snapshot cannot be loaded at all."""


def _queue_worktree(repo_path: Path) -> Path | None:
    candidate = repo_path / ".ralph-work" / "queue" / ".ralph"
    if candidate.is_dir():
        return repo_path / ".ralph-work" / "queue"
    return None


def _collect_meta_cycle_sentinels(blocked_dir: Path) -> list[MetaCycleSentinel]:
    if not blocked_dir.is_dir():
        return []
    sentinels: list[MetaCycleSentinel] = []
    for child in sorted(blocked_dir.iterdir()):
        if child.is_file() and child.name.startswith("META-cycle-") and child.suffix == ".md":
            sentinels.append(MetaCycleSentinel(filename=child.name, path=child))
    return sentinels


def load_snapshot(*, repo_path: Path) -> Snapshot:
    """Read snapshot panels from the ralph-queue worktree of ``repo_path``."""
    queue_root = _queue_worktree(repo_path)
    if queue_root is None:
        raise SnapshotError(
            f"queue worktree not found at {repo_path / '.ralph-work' / 'queue'}; "
            "git-show fallback not implemented yet"
        )
    return Snapshot(
        current=enumerate_state(queue_root, "current", repo_name=repo_path.name),
        inbox=enumerate_state(queue_root, "inbox", repo_name=repo_path.name),
        pending_pr=enumerate_state(queue_root, "pending-pr", repo_name=repo_path.name),
        blocked=enumerate_state(queue_root, "blocked", repo_name=repo_path.name),
        done=enumerate_state(queue_root, "done", repo_name=repo_path.name),
        meta_cycle_sentinels=_collect_meta_cycle_sentinels(queue_root / ".ralph" / "blocked"),
    )
