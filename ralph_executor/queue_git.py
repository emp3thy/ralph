"""Queue-clone git operations used by the iteration loop.

Three helpers: resolving the per-instance queue-clone path, refreshing
the queue clone before an iteration, and persisting any PBI-directory
edits Claude made during it. All run against the queue clone at
``<workspace_root>/queue-<instance_id>/`` (materialised by
``ensure_queue_clone``). ``queue_repo_root`` is a pure path
computation; ``pull_queue`` and ``persist_iteration_writes`` shell out
to git and touch the filesystem.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from ralph_executor import git_ops
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue_clone import ensure_queue_clone
from ralph_executor.safety import Event, EventLog, EventType

log = logging.getLogger(__name__)


def queue_repo_root(cfg: ExecutorConfig) -> Path:
    """Filesystem path of the queue clone for this instance.

    Scope 1 multi-ralph: the queue clone is namespaced per-instance at
    ``<workspace_root>/queue-<instance_id>/``. Delegates to
    :attr:`ExecutorConfig.queue_clone_path` so every module that needs
    the path agrees with the executor's view.

    The queue repo is cloned by ``ensure_queue_clone`` into this path
    and owns ``.ralph/`` (events.db, sentinel, blocked/, …). Every
    operation that reads or writes under ``.ralph/`` — opening the
    event log, moving PBIs to ``.ralph/blocked/``, handling STUCK.md,
    checking/writing the halt sentinel — routes through this helper so
    the side-effects land in the queue clone that gets pushed to
    ``origin/<queue_branch>`` of ``queue_repo``.
    """
    return cfg.queue_clone_path


def pull_queue(cfg: ExecutorConfig) -> None:
    """Refresh the queue clone before an iteration. Cheap; runs every iteration."""
    log.debug(
        "refreshing queue clone for %s (branch=%s)",
        cfg.queue_repo,
        cfg.queue_branch,
    )
    ensure_queue_clone(
        cfg.workspace_root,
        cfg.queue_repo,
        cfg.queue_branch,
        instance_id=cfg.instance_id,
    )


def persist_iteration_writes(
    cfg: ExecutorConfig,
    pbi_id: str,
    *,
    event_log: EventLog | None = None,
    now: datetime | None = None,
) -> None:
    """Commit + push any HISTORY.md/STUCK.md/PLAN.md edits Claude wrote
    inside the current PBI dir during this iteration.

    When the iteration outcome leaves the PBI in current/ (partial /
    error), nothing else moves the directory, so those edits would sit
    uncommitted in the working tree and be lost on the next iteration's
    checkout.

    Stages ONLY the PBI's directory under .ralph/current/<id>/ — not the
    whole .ralph/ tree — so local-state artefacts (e.g.
    .ralph/state/events.db) aren't accidentally committed every
    iteration. No-ops cleanly when the index ends up empty and when the
    PBI was already moved out of current/ by a sibling code path.

    All git operations run against the queue clone (materialised by
    ``pull_queue`` earlier in the iteration). The clone is its own
    working tree on the queue branch; no branch switching is required.

    Emits ``FILE_TOUCHED`` to ``event_log`` when a new commit lands and
    the diff is non-empty. The cycle detector reserves the event for
    future per-iteration rules (no current rule reads it; emit for
    forward compatibility).
    """
    queue_repo = queue_repo_root(cfg)
    pbi_dir = queue_repo / ".ralph" / "current" / pbi_id
    if not pbi_dir.is_dir():
        # PBI was already moved + committed + pushed by handle_stuck or
        # move_current_to_pending_pr — both route through
        # ``movements._move`` which runs ``git_ops.mv`` + ``commit_paths``
        # + ``push_with_rebase`` inside the same call. Nothing remains
        # in current/ for this helper to stage.
        return
    # Use add_all_changes so deletions of tracked files (e.g. Claude
    # removing a resolved STUCK.md) are staged too — bare `git add <dir>`
    # would skip them and leave index + working tree divergent.
    git_ops.add_all_changes(queue_repo, pbi_dir)
    head_before = git_ops.rev_parse_head(queue_repo)
    message = f"chore(queue): persist iteration writes for {pbi_id}"
    head_after = git_ops.commit_index(queue_repo, message)
    if head_after != head_before:
        log.info("persisted iteration writes for %s as %s", pbi_id, head_after[:7])
        # push_with_rebase rebases the local persist commit onto a raced
        # origin/main instead of failing the push outright. The caller
        # (iterate_once) catches PushRebaseConflict and converts it to a
        # recoverable IterationResult so the loop keeps running.
        git_ops.push_with_rebase(queue_repo, remote="origin", branch=cfg.queue_branch)
        if event_log is not None:
            files = git_ops.diff_names(queue_repo, head_before, head_after)
            if files:
                recorded_at = now if now is not None else datetime.now(tz=UTC)
                event_log.append(
                    Event(
                        kind=EventType.FILE_TOUCHED,
                        recorded_at=recorded_at,
                        pbi_id=pbi_id,
                        payload={"files": files},
                    )
                )
