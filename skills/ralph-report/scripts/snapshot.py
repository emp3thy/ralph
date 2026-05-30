"""Read the snapshot panels (current/inbox/pending-pr/blocked/done) for ralph-report.

This module reuses ``scripts.pbi_reader.enumerate_state`` for PBI directory
parsing. It does NOT read the timeline / done-24h commit data — those come
from ``git_walker.py`` because they are commit-history-derived.

The reader prefers the queue worktree at ``<repo>/.ralph-work/queue``. When
that worktree is absent it falls back to materialising a temporary mirror
of ``origin/ralph-queue:.ralph/`` via ``git ls-tree`` + ``git show``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Make ``scripts.pbi_reader`` importable when this module is loaded from
# the ralph repo root (whether via importlib in tests or as a flat import
# from ``report.py`` at runtime).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.pbi_reader import PBIRow, PBIRowError, enumerate_state  # noqa: E402

_QUEUE_REF = "origin/ralph-queue"
_STATES: tuple[str, ...] = ("current", "inbox", "pending-pr", "blocked", "done")
_ENTRY_FILES: tuple[str, ...] = (
    "PBI.md",
    "BUG.md",
    "FEEDBACK.md",
    "HISTORY.md",
    "PR-LINK.md",
    "ORIGINAL.md",
    "PLAN.md",
)


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
    """Read snapshot panels from ``repo_path``.

    Prefers the queue worktree at ``<repo>/.ralph-work/queue``. Falls back
    to ``git -C <repo> show origin/ralph-queue:<path>`` when the worktree
    is absent. Returns an empty snapshot if neither surface yields any
    data (e.g. ``repo_path`` is not a git repo at all).
    """
    queue_root = _queue_worktree(repo_path)
    if queue_root is not None:
        return _load_via_worktree(repo_path, queue_root)
    return _load_via_git_show(repo_path)


def _load_via_worktree(repo_path: Path, queue_root: Path) -> Snapshot:
    return Snapshot(
        current=enumerate_state(queue_root, "current", repo_name=repo_path.name),
        inbox=enumerate_state(queue_root, "inbox", repo_name=repo_path.name),
        pending_pr=enumerate_state(queue_root, "pending-pr", repo_name=repo_path.name),
        blocked=enumerate_state(queue_root, "blocked", repo_name=repo_path.name),
        done=enumerate_state(queue_root, "done", repo_name=repo_path.name),
        meta_cycle_sentinels=_collect_meta_cycle_sentinels(queue_root / ".ralph" / "blocked"),
    )


def _load_via_git_show(repo_path: Path) -> Snapshot:
    """Materialise a temporary mirror of ``origin/ralph-queue:.ralph/`` and read it.

    Rather than re-implementing the PBI parser on top of ``git show``, we
    create a short-lived staging dir, copy each entry file out of the
    queue ref via ``git show``, then call ``enumerate_state`` against the
    staging tree. The staging dir is discarded when the function returns.
    """
    snap = Snapshot()
    staging = Path(tempfile.mkdtemp(prefix="ralph-report-"))
    try:
        for state in _STATES:
            target_dir = staging / ".ralph" / state
            target_dir.mkdir(parents=True)
            for entry_name in _ls_tree_dirs(repo_path, f"{_QUEUE_REF}:.ralph/{state}"):
                if state == "blocked" and _is_meta_cycle_sentinel(entry_name):
                    continue
                _materialise_pbi(repo_path, state, entry_name, target_dir)
            _set_state(
                snap,
                state,
                enumerate_state(staging, state, repo_name=repo_path.name),
            )
        snap.meta_cycle_sentinels = _materialise_meta_sentinels(
            repo_path, staging / ".ralph" / "blocked"
        )
    finally:
        # Best-effort cleanup; ignore Windows-handle quirks on rmtree.
        shutil.rmtree(staging, ignore_errors=True)
    return snap


def _set_state(snap: Snapshot, state: str, rows: list[PBIRow | PBIRowError]) -> None:
    if state == "current":
        snap.current = rows
    elif state == "inbox":
        snap.inbox = rows
    elif state == "pending-pr":
        snap.pending_pr = rows
    elif state == "blocked":
        snap.blocked = rows
    elif state == "done":
        snap.done = rows
    else:
        raise ValueError(f"unknown state {state!r}")


def _is_meta_cycle_sentinel(name: str) -> bool:
    return name.startswith("META-cycle-") and name.endswith(".md")


def _ls_tree_dirs(repo_path: Path, ref_with_path: str) -> list[str]:
    """Return entry names under ``ref_with_path``.

    Returns ``[]`` when the path is absent on the queue branch (legitimate
    for an empty queue) or when ``repo_path`` is not a git repo at all.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-tree", "--name-only", ref_with_path],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _materialise_pbi(repo_path: Path, state: str, pbi_id: str, target_dir: Path) -> None:
    pbi_dir = target_dir / pbi_id
    pbi_dir.mkdir()
    for entry_name in _ENTRY_FILES:
        ref_path = f"{_QUEUE_REF}:.ralph/{state}/{pbi_id}/{entry_name}"
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "show", ref_path],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            (pbi_dir / entry_name).write_text(proc.stdout, encoding="utf-8")


def _materialise_meta_sentinels(repo_path: Path, target_dir: Path) -> list[MetaCycleSentinel]:
    target_dir.mkdir(parents=True, exist_ok=True)
    sentinels: list[MetaCycleSentinel] = []
    for name in _ls_tree_dirs(repo_path, f"{_QUEUE_REF}:.ralph/blocked"):
        if not _is_meta_cycle_sentinel(name):
            continue
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "show", f"{_QUEUE_REF}:.ralph/blocked/{name}"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            continue
        path = target_dir / name
        path.write_text(proc.stdout, encoding="utf-8")
        sentinels.append(MetaCycleSentinel(filename=name, path=path))
    return sentinels
