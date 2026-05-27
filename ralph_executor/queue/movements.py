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
from ralph_executor.safety.events import (
    Event,
    EventLog,
    EventType,
    signature_from_text,
)
from ralph_executor.types import PBI, PBIStatus
from ralph_executor.worktree import queue_worktree_path

log = logging.getLogger(__name__)


def _queue_repo(cfg: ExecutorConfig) -> Path:
    """Filesystem path of the checkout that owns ``.ralph/`` for this run.

    In worktree mode this is the long-lived queue worktree; in legacy
    single-checkout mode it is the primary checkout itself. Both are
    real working trees backed by the same ``.git/`` object store.
    """
    if cfg.use_worktrees:
        return queue_worktree_path(cfg.repo_path)
    return cfg.repo_path


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
    queue_repo = _queue_repo(cfg)
    if not cfg.use_worktrees:
        # Legacy single-checkout: primary holds queue_branch, swap to it
        # before touching .ralph/. In worktree mode the queue worktree is
        # already pinned to queue_branch — no checkout needed (and a
        # checkout would actually fail because the branch is owned by the
        # queue worktree, not the primary).
        git_ops.checkout(queue_repo, cfg.queue_branch)

    src = queue_repo / ".ralph" / expected_state / pbi.id
    dst = queue_repo / ".ralph" / target_state / pbi.id
    if not src.is_dir():
        raise QueueMovementError(f"source path {src} does not exist on the queue branch")
    if dst.exists():
        raise QueueMovementError(f"destination {dst} already exists; refusing to overwrite")

    git_ops.mv(queue_repo, src, dst)

    entry_name = ENTRY_FILE_BY_TYPE[pbi.type]
    _rewrite_status(dst / entry_name, target_state)

    git_ops.commit_all(
        queue_repo,
        f"{commit_prefix}: move {pbi.id} from {expected_state} to {target_state}",
    )
    # push_with_rebase tolerates concurrent writers (operator commits, a
    # second ralph instance, web commits) racing the queue branch between
    # this iteration's start and the move's push. PushRebaseConflict is
    # the conflict case; iterate_once treats it as a recoverable warning.
    git_ops.push_with_rebase(queue_repo, remote="origin", branch=cfg.queue_branch)
    return parse_pbi_directory(dst, status=target_state)


def move_inbox_to_current(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    event_log: EventLog | None = None,
    now: datetime | None = None,
) -> PBI:
    """Claim a PBI from inbox into the single-focus current folder.

    Emits ``PBI_OPENED`` to ``event_log`` when ``event_log`` is provided.
    The cycle detector's ``whack_a_mole`` rule consumes opens vs closes
    over a rolling window.
    """
    moved = _move(
        cfg,
        pbi,
        expected_state="inbox",
        target_state="current",
        commit_prefix="chore(ralph-queue)",
    )
    if event_log is not None:
        recorded_at = now if now is not None else datetime.now(tz=UTC)
        event_log.append(
            Event(
                kind=EventType.PBI_OPENED,
                recorded_at=recorded_at,
                pbi_id=moved.id,
                payload={},
            )
        )
    return moved


def move_current_to_pending_pr(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    event_log: EventLog | None = None,
    pr_url: str | None = None,
    touched_files: list[str] | None = None,
    now: datetime | None = None,
) -> PBI:
    """Promote a PBI whose PR was created from current to pending-pr.

    Emits ``PR_CREATED`` to ``event_log`` when ``event_log`` and ``pr_url``
    are provided. The cycle detector consumes the event's ``files`` payload
    to spot same-file thrashing across PBIs. ``touched_files`` is the
    cumulative diff of the feature branch against main; an empty list is
    fine if the diff cannot be computed.
    """
    moved = _move(
        cfg,
        pbi,
        expected_state="current",
        target_state="pending-pr",
        commit_prefix="feat(ralph-queue)",
    )
    if event_log is not None and pr_url is not None:
        recorded_at = now if now is not None else datetime.now(tz=UTC)
        event_log.append(
            Event(
                kind=EventType.PR_CREATED,
                recorded_at=recorded_at,
                pbi_id=moved.id,
                payload={
                    "pr_url": pr_url,
                    "signature": signature_from_text(pr_url),
                    "files": list(touched_files or []),
                },
            )
        )
    return moved


def move_current_to_blocked(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Demote a stuck PBI from current to blocked."""
    return _move(
        cfg,
        pbi,
        expected_state="current",
        target_state="blocked",
        commit_prefix="chore(ralph-queue)",
    )
