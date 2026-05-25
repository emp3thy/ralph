"""Loop driver — the heart of the executor.

Algorithm (matches the spec's "Iteration model"):

  1. Check the halt sentinel — refuse to iterate while the executor is
     halted (Plan 9 Layer 3).
  2. ``git pull ralph-queue`` (every iteration, cheap, keeps the queue
     in sync).
  3. Check ``current/``.
     a. If occupied: increment the attempt counter (Plan 9), then spawn
        ``claude -p`` against that PBI.
        * pr_created → move PBI to pending-pr/.
        * stuck      → handle_stuck (Plan 9 Layer 1) → blocked/.
        * partial / error → PBI stays in current/ (multi-step).
     b. If empty: run the sweep stub (Plan 8 fills in), then pick the
        highest-priority inbox PBI. If picked, ``git pull main``, claim
        the PBI into current/, and create the per-PBI feature branch
        ``ralph/<PBI-ID>`` off main.
  4. Evaluate cycle-detector rules (Plan 9 Layer 3). If any trip, write
     the META-BUG + sentinel and raise ``HaltedError``.

Plan 8 will replace ``_run_sweep`` with the real sweep implementation.
Both replacements happen via ``monkeypatch`` in tests and via plain
import overrides in production; the loop itself stays untouched.
"""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from ralph_executor import git_ops
from ralph_executor.claude_spawn import ClaudeOutcome, spawn_claude_p
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue.movements import (
    move_current_to_pending_pr,
    move_inbox_to_current,
)
from ralph_executor.safety import (
    AttemptCounter,
    AttemptsExceeded,
    Event,
    EventType,
    HaltedError,
    HaltStatus,
    check_halt_sentinel,
    evaluate_all,
    halt_and_acknowledge,
    handle_stuck,
    open_log,
)
from ralph_executor.types import PBI

log = logging.getLogger(__name__)

IterationOutcome = Literal[
    "idle",
    "claimed",
    "ran_partial",
    "ran_error",
    "ran_pr_created",
    "ran_stuck",
    "halted",
]


@dataclass(frozen=True)
class IterationResult:
    """What happened during a single ``iterate_once`` call."""

    outcome: IterationOutcome
    pbi_id: str | None
    pr_url: str | None = None


# ----------------------------------------------------------------------
# Stubs for Plans 8 and 9
# ----------------------------------------------------------------------


def _run_sweep(cfg: ExecutorConfig, source: FilesystemQueueSource) -> None:
    """Stub — Plan 8 fills this in.

    In Plan 8 this will iterate ``source.pending_pr_pbis()`` and call
    ``ado-pr show``/``ado-pr read-threads`` to detect PR state changes.
    In v1 (this plan) it is intentionally a no-op so the loop's
    single-PBI focus discipline is testable without Plan 8.
    """
    log.debug("sweep stub invoked (Plan 8 will replace this)")


def _check_cycle_detector(cfg: ExecutorConfig, source: FilesystemQueueSource) -> bool:
    """Evaluate all cycle-detector rules against the recent event log.

    Returns ``True`` if any signal tripped (and the loop should halt after
    this call completes the META-BUG + sentinel write). Returns ``False``
    when no signals fire.

    The function is kept as a module-level callable so tests can monkeypatch
    it without dependency-injection (reconciliation #9).
    """
    now = datetime.now(tz=UTC)
    event_log = open_log(cfg.repo_path)
    try:
        events = event_log.recent(window=timedelta(hours=72), now=now)
    finally:
        event_log.close()
    signals = evaluate_all(events, now)
    if not signals:
        return False
    log.warning(
        "cycle detector tripped (%d signal(s)); writing META-BUG + sentinel",
        len(signals),
    )
    halt_and_acknowledge(repo=cfg.repo_path, signals=signals, now=now)
    return True


# ----------------------------------------------------------------------
# One iteration
# ----------------------------------------------------------------------


def _ensure_on_queue_branch(cfg: ExecutorConfig) -> None:
    if git_ops.current_branch(cfg.repo_path) != cfg.queue_branch:
        git_ops.checkout(cfg.repo_path, cfg.queue_branch)


def _pull_queue(cfg: ExecutorConfig) -> None:
    log.debug("pulling %s", cfg.queue_branch)
    _ensure_on_queue_branch(cfg)
    git_ops.pull(cfg.repo_path, cfg.queue_branch)


def _pull_main(cfg: ExecutorConfig) -> None:
    log.debug("pulling %s", cfg.main_branch)
    git_ops.checkout(cfg.repo_path, cfg.main_branch)
    git_ops.pull(cfg.repo_path, cfg.main_branch)


def _feature_branch_name(pbi: PBI) -> str:
    return f"ralph/{pbi.id}"


def _claim_pbi(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Move PBI into current/ and create the per-PBI feature branch.

    Sequence (matches the spec's "Branch dance"):
      1. ``move_inbox_to_current`` (commits + pushes on ralph-queue).
      2. ``git pull main``.
      3. ``git checkout -b ralph/<PBI-ID>`` off main.
      4. Switch back to ``ralph-queue`` so the caller sees the updated
         ``.ralph/current/`` on disk immediately after claim.  The
         feature branch is checked out again in ``_run_ralph`` just
         before spawning Claude.
    """
    moved = move_inbox_to_current(cfg, pbi)
    _pull_main(cfg)
    branch = _feature_branch_name(moved)
    if git_ops.branch_exists(cfg.repo_path, branch):
        # Multi-step PBI re-enters after a previous claim was rolled
        # back — checkout the existing branch rather than create.
        git_ops.checkout(cfg.repo_path, branch)
    else:
        git_ops.checkout_new(cfg.repo_path, branch)
    # Return to the queue branch so .ralph/ is visible on disk.
    git_ops.checkout(cfg.repo_path, cfg.queue_branch)
    return moved


def _switch_to_feature_branch(cfg: ExecutorConfig, pbi: PBI) -> None:
    """Check out the feature branch for ``pbi`` before spawning Claude."""
    branch = _feature_branch_name(pbi)
    git_ops.checkout(cfg.repo_path, branch)


def _run_ralph(cfg: ExecutorConfig, pbi: PBI) -> tuple[ClaudeOutcome, IterationResult]:
    """Spawn ``claude -p`` against the current PBI and classify the result.

    Multi-step PBI discipline: ``partial`` and ``error`` outcomes leave
    the PBI in ``current/``; ``pr_created`` promotes to ``pending-pr/``;
    ``stuck`` triggers ``handle_stuck`` (Layer 1) which moves the PBI to
    ``blocked/`` and returns a ``StuckOutcome`` carrying a ``pbi.blocked``
    event the caller appends to the event log.

    Also increments the attempt counter before spawning (Plan 9 Layer 1).
    If the counter exceeds the configured maximum, the PBI is moved to
    ``blocked/`` without spawning Claude.

    KNOWN ISSUE: the working tree is currently left on ``cfg.queue_branch``
    when spawning so that ``.ralph/current/<PBI-ID>/`` is visible on disk
    for Claude to read PROMPT.md / HISTORY.md / PBI.md and write
    STUCK.md / HISTORY.md per its instructions. The spawned Claude session
    is expected to ``git checkout ralph/<PBI-ID>`` itself before making
    code edits (PROMPT.md instructs this explicitly), then return to
    ``ralph-queue`` for queue-state writes. The ``_switch_to_feature_branch``
    helper is present as the executor-driven alternative.

    The trade-off: the spec's "Branch dance" expected the EXECUTOR to do
    the feature-branch checkout before spawning, but ``.ralph/`` only
    lives on ``ralph-queue``. Plan 7 defers the full reconciliation --
    likely needing a git-worktree or .ralph-merge-into-feature scheme --
    to Plan 9 / a follow-up. See PR #5 review thread for context.
    """
    now = datetime.now(tz=UTC)
    event_log = open_log(cfg.repo_path)
    try:
        # --- Plan 9: increment attempt counter before spawning -----------
        counter = AttemptCounter(pbi_dir=pbi.path)
        try:
            new_attempts = counter.increment()
        except AttemptsExceeded as exc:
            log.warning(
                "PBI %s exceeded max attempts (%d/%d); moving to blocked/",
                pbi.id,
                exc.attempts,
                exc.limit,
            )
            event_log.append(
                Event(
                    kind=EventType.PBI_BLOCKED,
                    recorded_at=now,
                    pbi_id=pbi.id,
                    payload={"reason": str(exc), "source": "max-attempts"},
                )
            )
            target = cfg.repo_path / ".ralph" / "blocked" / pbi.id
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pbi.path), str(target))
            dummy = ClaudeOutcome(
                kind="error",
                pr_url=None,
                stdout="",
                stderr=str(exc),
                exit_code=1,
                duration_seconds=0.0,
            )
            return dummy, IterationResult(outcome="ran_stuck", pbi_id=pbi.id)

        event_log.append(
            Event(
                kind=EventType.ATTEMPT_INCREMENTED,
                recorded_at=now,
                pbi_id=pbi.id,
                payload={"attempts": new_attempts},
            )
        )

        # --- Spawn Claude ------------------------------------------------
        outcome = spawn_claude_p(cfg, pbi)
        log.info("PBI %s outcome=%s exit=%d", pbi.id, outcome.kind, outcome.exit_code)

        if outcome.kind == "pr_created":
            move_current_to_pending_pr(cfg, pbi)
            return outcome, IterationResult(
                outcome="ran_pr_created", pbi_id=pbi.id, pr_url=outcome.pr_url
            )

        if outcome.kind == "stuck":
            # --- Plan 9 Layer 1: STUCK.md detection ----------------------
            stuck_outcome = handle_stuck(
                repo=cfg.repo_path,
                pbi_dir=pbi.path,
                now=datetime.now(tz=UTC),
            )
            if stuck_outcome is not None:
                event_log.append(stuck_outcome.event)
                log.info("PBI %s stuck: %s", pbi.id, stuck_outcome.reason)
                return outcome, IterationResult(outcome="ran_stuck", pbi_id=pbi.id)
            # Claude reported stuck but no STUCK.md present -- fall through
            # to partial (the PBI stays in current/ for the next iteration).

        if outcome.kind == "error":
            return outcome, IterationResult(outcome="ran_error", pbi_id=pbi.id)
        return outcome, IterationResult(outcome="ran_partial", pbi_id=pbi.id)
    finally:
        event_log.close()


def iterate_once(cfg: ExecutorConfig) -> IterationResult:
    """Run a single iteration of the loop and return the outcome.

    Idempotent in the no-work case: if current/ is empty and the inbox
    is empty, the iteration is a no-op and returns ``IterationResult("idle")``.

    The working tree is guaranteed to be on ``cfg.queue_branch`` when
    this function returns, regardless of the outcome, so that callers
    can inspect ``.ralph/`` on disk immediately after the call.

    Raises ``HaltedError`` if the halt sentinel is active (Plan 9 Layer 3).
    """
    # --- Plan 9 Layer 3: refuse to start while sentinel is active --------
    status = check_halt_sentinel(cfg.repo_path)
    if status == HaltStatus.HALTED:
        raise HaltedError(
            meta_bug_id="(see .ralph/state/halted)",
            meta_bug_path=cfg.repo_path / ".ralph" / "blocked",
            sentinel_path=cfg.repo_path / ".ralph" / "state" / "halted",
        )

    _pull_queue(cfg)
    source = FilesystemQueueSource(cfg)

    current = source.current_pbi()
    if current is not None:
        # Current occupied → run Ralph on it (attempt counter + spawn).
        _outcome, result = _run_ralph(cfg, current)
        # Restore queue branch so .ralph/ is visible on disk after the call.
        _ensure_on_queue_branch(cfg)
        if _check_cycle_detector(cfg, source):
            # META-BUG + sentinel already written by _check_cycle_detector;
            # raise HaltedError so the caller knows the loop is frozen.
            raise HaltedError(
                meta_bug_id="(see latest META-cycle-* in .ralph/blocked/)",
                meta_bug_path=cfg.repo_path / ".ralph" / "blocked",
                sentinel_path=cfg.repo_path / ".ralph" / "state" / "halted",
            )
        return result

    # Current empty → sweep (Plan 8 stub), pick next, claim if any.
    _run_sweep(cfg, source)

    picked = source.pick_next()
    if picked is None:
        # Nothing to do; run the cycle-detector check anyway so a
        # globally-tripped cycle can halt the loop.
        if _check_cycle_detector(cfg, source):
            raise HaltedError(
                meta_bug_id="(see latest META-cycle-* in .ralph/blocked/)",
                meta_bug_path=cfg.repo_path / ".ralph" / "blocked",
                sentinel_path=cfg.repo_path / ".ralph" / "state" / "halted",
            )
        return IterationResult(outcome="idle", pbi_id=None)

    log.info("claiming PBI %s", picked.id)
    claimed = _claim_pbi(cfg, picked)
    # _claim_pbi already returns to queue branch; re-assert for clarity.
    _ensure_on_queue_branch(cfg)
    if _check_cycle_detector(cfg, source):
        raise HaltedError(
            meta_bug_id="(see latest META-cycle-* in .ralph/blocked/)",
            meta_bug_path=cfg.repo_path / ".ralph" / "blocked",
            sentinel_path=cfg.repo_path / ".ralph" / "state" / "halted",
        )
    return IterationResult(outcome="claimed", pbi_id=claimed.id)


# ----------------------------------------------------------------------
# Run forever (with an optional iteration cap for tests)
# ----------------------------------------------------------------------


def run_loop(
    cfg: ExecutorConfig, *, max_iterations: int | None = None
) -> Iterator[IterationResult]:
    """Run iterations until interrupted or ``max_iterations`` reached.

    Yields each ``IterationResult`` so callers (and tests) can observe
    progress. ``max_iterations`` is primarily for tests; in production
    callers pass ``None`` and the loop runs until KeyboardInterrupt.

    Raises ``HaltedError`` if the halt sentinel blocks the loop on entry
    (Plan 9 Layer 3). Callers that want a gentle drain should catch
    ``HaltedError`` and surface it to the operator.
    """
    count = 0
    while True:
        try:
            result = iterate_once(cfg)
        except KeyboardInterrupt:
            log.info("interrupted")
            return
        except HaltedError:
            log.warning("halt sentinel is active -- exiting run_loop")
            raise
        yield result
        if result.outcome == "halted":
            log.warning("halt signalled -- exiting run_loop")
            return
        count += 1
        if max_iterations is not None and count >= max_iterations:
            return
        # Sleep only between iterations that found nothing to do.
        if result.outcome == "idle":
            time.sleep(cfg.iteration_sleep_seconds)
