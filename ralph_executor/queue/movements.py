"""Atomic folder moves between ``.ralph/`` states.

Each helper:
  1. Switches the working tree to the queue branch.
  2. Validates the PBI is currently in the expected source state.
  3. ``git mv``s the directory to the destination state folder.
  4. Rewrites the entry file's frontmatter (``status:``, ``updated_at:``).
  5. Commits + pushes the queue branch.

The result is a new ``PBI`` dataclass reflecting the new on-disk state.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from ralph_executor import git_ops
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import (
    ENTRY_FILE_BY_TYPE,
    parse_pbi_directory,
)
from ralph_executor.types import PBI, PBIStatus

log = logging.getLogger(__name__)


class QueueMovementError(RuntimeError):
    """Raised when a folder move violates a precondition."""


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _rewrite_status(entry_file: Path, new_status: PBIStatus) -> None:
    text = entry_file.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise QueueMovementError(f"{entry_file}: no opening '---' fence to rewrite")
    end = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end < 0:
        raise QueueMovementError(f"{entry_file}: no closing '---' fence to rewrite")
    rewrote_status = False
    rewrote_updated = False
    now = _now_iso()
    for idx in range(1, end):
        stripped = lines[idx].lstrip()
        if stripped.startswith("status:"):
            lines[idx] = f"status: {new_status}\n"
            rewrote_status = True
        elif stripped.startswith("updated_at:"):
            lines[idx] = f"updated_at: {now}\n"
            rewrote_updated = True
    if not rewrote_status:
        # Insert before the closing fence.
        lines.insert(end, f"status: {new_status}\n")
        end += 1
    if not rewrote_updated:
        lines.insert(end, f"updated_at: {now}\n")
    entry_file.write_text("".join(lines), encoding="utf-8")


def _move(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    expected_state: PBIStatus,
    target_state: PBIStatus,
    commit_prefix: str,
) -> PBI:
    if pbi.status != expected_state:
        raise QueueMovementError(f"PBI {pbi.id} must be in {expected_state}, found in {pbi.status}")
    git_ops.checkout(cfg.repo_path, cfg.queue_branch)

    src = cfg.repo_path / ".ralph" / expected_state / pbi.id
    dst = cfg.repo_path / ".ralph" / target_state / pbi.id
    if not src.is_dir():
        raise QueueMovementError(f"source path {src} does not exist on the queue branch")
    if dst.exists():
        raise QueueMovementError(f"destination {dst} already exists; refusing to overwrite")

    git_ops.mv(cfg.repo_path, src, dst)

    entry_name = ENTRY_FILE_BY_TYPE[pbi.type]
    _rewrite_status(dst / entry_name, target_state)

    git_ops.commit_all(
        cfg.repo_path,
        f"{commit_prefix}: move {pbi.id} from {expected_state} to {target_state}",
    )
    git_ops.push(cfg.repo_path, cfg.queue_branch)
    return parse_pbi_directory(dst, status=target_state)


def move_inbox_to_current(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Claim a PBI from inbox into the single-focus current folder."""
    return _move(
        cfg,
        pbi,
        expected_state="inbox",
        target_state="current",
        commit_prefix="chore(ralph-queue)",
    )


def move_current_to_pending_pr(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Promote a PBI whose PR was created from current to pending-pr."""
    return _move(
        cfg,
        pbi,
        expected_state="current",
        target_state="pending-pr",
        commit_prefix="feat(ralph-queue)",
    )


def move_current_to_blocked(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Demote a stuck PBI from current to blocked."""
    return _move(
        cfg,
        pbi,
        expected_state="current",
        target_state="blocked",
        commit_prefix="chore(ralph-queue)",
    )
