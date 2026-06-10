"""Atomic folder moves between ``.ralph/`` states.

Each helper:
  1. Validates the PBI is currently in the expected source state.
  2. ``git mv``s the directory to the destination state folder.
  3. Rewrites the entry file's frontmatter (``status:``, ``updated_at:``).
  4. Commits + pushes ``main`` on the queue clone.

The result is a new ``PBI`` dataclass reflecting the new on-disk state.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ralph_executor import git_ops
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.claim import CLAIM_FILENAME, Claim, write_claim
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

log = logging.getLogger(__name__)


def _queue_repo(cfg: ExecutorConfig) -> Path:
    """Filesystem path of the queue clone for this run.

    Scope 1 multi-ralph: the queue clone is namespaced per-instance at
    ``<workspace_root>/queue-<instance_id>/``. Delegates to
    :attr:`ExecutorConfig.queue_clone_path`.
    """
    return cfg.queue_clone_path


class QueueMovementError(RuntimeError):
    """Raised when a folder move violates a precondition."""


class UncommittedSource(RuntimeError):
    """Raised when ``_move``'s source dir has no files tracked by git.

    The dir is present on disk — an external writer (operator adding a
    PBI in another shell, a second ralph session mid-add) has created the
    directory and entry file but has not yet run ``git commit`` on the
    queue branch. ``git mv`` would fail with ``fatal: source directory is
    empty`` (exit 128) and a raw ``GitCommandError`` would propagate up
    through ``_claim_pbi`` and kill the loop. ``iterate_once`` catches
    this exception instead, logs a WARNING, and returns a recoverable
    ``IterationResult(outcome="uncommitted_source", ...)`` so the next
    iteration re-scans inbox once the writer's commit lands.
    """

    def __init__(self, pbi_id: str) -> None:
        super().__init__(f"PBI {pbi_id} source dir has no files tracked by git yet")
        self.pbi_id = pbi_id


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _rewrite_status(
    entry_file: Path,
    new_status: PBIStatus,
    *,
    stamp_closed_at: bool = False,
) -> None:
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
    rewrote_closed = False
    now = _now_iso()
    for idx in range(1, end):
        stripped = lines[idx].lstrip()
        if stripped.startswith("status:"):
            lines[idx] = f"status: {new_status}\n"
            rewrote_status = True
        elif stripped.startswith("updated_at:"):
            lines[idx] = f"updated_at: {now}\n"
            rewrote_updated = True
        elif stripped.startswith("closed_at:") and stamp_closed_at:
            lines[idx] = f"closed_at: {now}\n"
            rewrote_closed = True
    if not rewrote_status:
        # Insert before the closing fence.
        lines.insert(end, f"status: {new_status}\n")
        end += 1
    if not rewrote_updated:
        lines.insert(end, f"updated_at: {now}\n")
        end += 1
    if stamp_closed_at and not rewrote_closed:
        lines.insert(end, f"closed_at: {now}\n")
    entry_file.write_text("".join(lines), encoding="utf-8")


def _move(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    expected_state: PBIStatus,
    target_state: PBIStatus,
    commit_prefix: str,
    extra_files_writer: Callable[[Path], None] | None = None,
    commit_subject_override: str | None = None,
) -> PBI:
    if pbi.status != expected_state:
        raise QueueMovementError(f"PBI {pbi.id} must be in {expected_state}, found in {pbi.status}")
    queue_repo = _queue_repo(cfg)

    src = queue_repo / ".ralph" / expected_state / pbi.id
    dst = queue_repo / ".ralph" / target_state / pbi.id
    if not src.is_dir():
        raise QueueMovementError(f"source path {src} does not exist in the queue clone")
    if dst.exists():
        raise QueueMovementError(f"destination {dst} already exists; refusing to overwrite")

    # Race guard: an external writer (operator, second ralph session)
    # can create the source dir on disk before committing it. ``git mv``
    # would then exit 128 with ``fatal: source directory is empty`` and
    # crash the loop. Detect via ``git ls-files`` and raise a recoverable
    # ``UncommittedSource`` instead; ``iterate_once`` catches it and the
    # next iteration retries once the writer's commit lands.
    if not git_ops.ls_files(queue_repo, src):
        raise UncommittedSource(pbi.id)

    git_ops.mv(queue_repo, src, dst)

    entry_name = ENTRY_FILE_BY_TYPE[pbi.type]
    _rewrite_status(
        dst / entry_name,
        target_state,
        stamp_closed_at=(target_state == "done"),
    )

    if extra_files_writer is not None:
        extra_files_writer(dst)

    message = (
        commit_subject_override
        if commit_subject_override is not None
        else f"{commit_prefix}: move {pbi.id} from {expected_state} to {target_state}"
    )
    # ``commit_paths(..., [dst])`` stages everything under ``dst`` (the
    # frontmatter rewrite to the entry file + any new files dropped in by
    # ``extra_files_writer``, e.g. CLAIM.json in the claim path) and
    # commits them atomically with the ``git mv`` rename already in the
    # index. Per [[63c4e75a]] a brand-new file inside a ``git mv``-d
    # directory is NOT auto-staged by ``git mv`` itself; the explicit
    # ``git add -A -- dst`` inside ``commit_paths`` is what picks it up.
    # ``commit_all`` (``git add -A`` over the whole worktree) is unsafe
    # here on Windows: ralph holds an exclusive ``msvcrt.locking`` lock
    # on ``.ralph.lock`` at the queue-clone root, ``git add`` cannot
    # read the locked file, and the move crashes with
    # ``Permission denied`` (BUG-COMMIT-ALL-RALPH-LOCK-WINDOWS).
    git_ops.commit_paths(queue_repo, message, [dst])
    # push_with_rebase tolerates concurrent writers (operator commits, a
    # second ralph instance, web commits) racing the queue repo's
    # cfg.queue_branch between this iteration's start and the move's push.
    # PushRebaseConflict is the conflict case; iterate_once treats it as
    # a recoverable warning.
    git_ops.push_with_rebase(queue_repo, remote="origin", branch=cfg.queue_branch)
    return parse_pbi_directory(dst, status=target_state)


def move_inbox_to_current(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    event_log: EventLog | None = None,
    now: datetime | None = None,
    instance_id: str | None = None,
    hostname: str | None = None,
    claimed_at: datetime | None = None,
) -> PBI:
    """Claim a PBI from inbox into the single-focus current folder.

    Emits ``PBI_OPENED`` to ``event_log`` when ``event_log`` is provided.
    The cycle detector's ``whack_a_mole`` rule consumes opens vs closes
    over a rolling window.

    Scope 1 multi-ralph (Task 9): when ``instance_id`` is provided the
    claim atomically writes ``CLAIM.json`` into ``current/<id>/`` as part
    of the same commit as the ``git mv`` rename + frontmatter rewrite,
    and pins the commit subject to
    ``chore(queue): claim <id> for <instance_id>``. ``hostname`` is
    required when ``instance_id`` is set (the claim marker carries
    both). ``claimed_at`` defaults to ``datetime.now(UTC)`` for callers
    that don't pin it.

    On ``PushRebaseConflict`` from the claim path's
    ``push_with_rebase``: the local clone is reset to
    ``origin/<queue_branch>`` so the next iteration sees no leftover
    ``current/<id>/`` and ``_pull_queue``'s ff-only pull succeeds.
    Without the reset the loser would diverge from origin permanently.
    """
    extra_writer: Callable[[Path], None] | None
    commit_subject_override: str | None
    if instance_id is not None:
        if hostname is None:
            raise QueueMovementError("hostname is required when instance_id is provided")
        when = claimed_at if claimed_at is not None else datetime.now(tz=UTC)
        claim = Claim(
            instance_id=instance_id,
            claimed_at=when.isoformat(),
            hostname=hostname,
        )

        def _write_claim_file(dst: Path) -> None:
            write_claim(dst / CLAIM_FILENAME, claim)

        extra_writer = _write_claim_file
        commit_subject_override = f"chore(queue): claim {pbi.id} for {instance_id}"
    else:
        extra_writer = None
        commit_subject_override = None
    try:
        moved = _move(
            cfg,
            pbi,
            expected_state="inbox",
            target_state="current",
            commit_prefix="chore(ralph-queue)",
            extra_files_writer=extra_writer,
            commit_subject_override=commit_subject_override,
        )
    except git_ops.PushRebaseConflict:
        if instance_id is not None:
            # Roll back the local claim commit + working tree so the
            # next iteration's ff-only pull succeeds and no stale
            # ``current/<id>/`` lingers on the losing clone.
            git_ops.reset_hard(_queue_repo(cfg), f"origin/{cfg.queue_branch}")
        raise
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


def move_pending_pr_to_done(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Promote a merged-PR PBI from pending-pr to done. Stamps closed_at."""
    return _move(
        cfg,
        pbi,
        expected_state="pending-pr",
        target_state="done",
        commit_prefix="chore(ralph-queue)",
    )


def move_inbox_to_blocked(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Demote an unclaimable inbox PBI directly to blocked.

    Used by ``iterate_once`` when ``_claim_pbi`` raises ``_ClaimError``
    before the PBI has made it into ``current/`` (target_repo missing,
    parse failure, unsupported host, ``TargetUnreachable``). The PBI
    never holds the single-focus slot, so the move is inbox -> blocked.
    """
    return _move(
        cfg,
        pbi,
        expected_state="inbox",
        target_state="blocked",
        commit_prefix="chore(ralph-queue)",
    )
