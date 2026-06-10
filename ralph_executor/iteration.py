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
     b. If empty: run the sweep, then pick the highest-priority inbox
        PBI. If picked, ``git pull main``, claim the PBI into current/,
        and create the per-PBI feature branch ``ralph/<PBI-ID>`` off
        main.
  4. Evaluate cycle-detector rules (Plan 9 Layer 3). If any trip, write
     the META-BUG + sentinel and raise ``HaltedError``.

This module owns orchestration only; the helpers it drives live in four
sibling modules: ``pbi_claim`` (inbox -> current claim), ``queue_git``
(queue-clone git operations), ``worktree_manager`` (work-worktree
lifecycle), and ``iteration_safety`` (sweep + cycle detector). The moved
helpers are re-imported here under their old private names
(``_claim_pbi``, ``_pull_queue``, ``_run_sweep``, ...) so internal call
sites and iteration-level monkeypatch targets keep resolving.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import shutil
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ralph_executor import git_ops
from ralph_executor.claude_spawn import ClaudeOutcome, spawn_claude_p
from ralph_executor.config import ExecutorConfig
from ralph_executor.git_ops import PushRebaseConflict

# Re-imported under the old private names so internal callers, cli's
# import, and loop-level monkeypatches keep working unchanged.
from ralph_executor.iteration_safety import (
    check_cycle_detector as _check_cycle_detector,
)
from ralph_executor.iteration_safety import (
    pr_skill_scripts_path as _pr_skill_scripts_path,
)
from ralph_executor.iteration_safety import run_sweep
from ralph_executor.lockfile import WorkspaceLockfile

# Re-imported under the old private names so internal callers, the
# resume path, except clauses, and loop-level monkeypatch / import
# paths keep working unchanged.
from ralph_executor.pbi_claim import (
    ClaimError,
)
from ralph_executor.pbi_claim import (
    claim_pbi as _claim_pbi,
)
from ralph_executor.pbi_claim import (
    feature_branch_name as _feature_branch_name,
)
from ralph_executor.pbi_claim import (
    read_target_repo_from_pbi as _read_target_repo_from_pbi,
)
from ralph_executor.pbi_claim import (
    warn_project_toml_in_target_clone as _warn_project_toml_in_target_clone,
)
from ralph_executor.prompt_composer import PromptComposeError
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue.movements import (
    UncommittedSource,
    move_current_to_blocked,
    move_current_to_pending_pr,
    move_inbox_to_blocked,
)

# Re-imported under the old private names so internal callers, cli's
# deferred import, and loop-level monkeypatches keep working unchanged.
from ralph_executor.queue_git import (
    persist_iteration_writes as _persist_iteration_writes,
)
from ralph_executor.queue_git import (
    pull_queue as _pull_queue,
)
from ralph_executor.queue_git import (
    queue_repo_root as _queue_repo_root,
)
from ralph_executor.safety import (
    AttemptCounter,
    AttemptsExceeded,
    Event,
    EventLog,
    EventType,
    HaltedError,
    HaltStatus,
    check_halt_sentinel,
    handle_stuck,
    open_log,
)
from ralph_executor.types import PBI
from ralph_executor.worktree import (
    ensure_worktree,
    work_worktree_path,
)

# Re-imported under the old private name so internal callers and
# loop-level monkeypatches keep working unchanged.
from ralph_executor.worktree_manager import (
    cleanup_work_worktree as _cleanup_work_worktree,
)

# Backward-compat alias so older except clauses / monkeypatch / import
# paths keep resolving (exception classes alias cleanly).
_ClaimError = ClaimError

# Explicit re-exports (mypy strict no_implicit_reexport): cli.py and
# the satellite test files import these old private names from loop
# even though the functions now live in iteration_safety / pbi_claim.
__all__ = [
    "_pr_skill_scripts_path",
    "_warn_project_toml_in_target_clone",
]

log = logging.getLogger(__name__)


IterationOutcome = Literal[
    "idle",
    "claimed",
    "claim_failed",
    "ran_partial",
    "ran_error",
    "ran_pr_created",
    "ran_stuck",
    "halted",
    "push_conflict",
    "uncommitted_source",
]


@dataclass(frozen=True)
class IterationResult:
    """What happened during a single ``iterate_once`` call."""

    outcome: IterationOutcome
    pbi_id: str | None
    pr_url: str | None = None


def _run_sweep(cfg: ExecutorConfig, source: FilesystemQueueSource) -> None:
    """Delegate to ``iteration_safety.run_sweep``, threading this module's
    ``_pr_skill_scripts_path`` global through the resolver seam.

    Kept as a real ``def`` (not an aliased import) for two reasons:
    tests patch ``ralph_executor.iteration._run_sweep`` directly
    (orchestration seam), and tests patch
    ``ralph_executor.iteration._pr_skill_scripts_path`` expecting the sweep
    to see the stub — the call-time global lookup here is what makes
    that interception work after the function moved modules.
    """
    run_sweep(cfg, source, pr_skill_scripts_path=_pr_skill_scripts_path)


# ----------------------------------------------------------------------
# One iteration
# ----------------------------------------------------------------------


def _append_compose_error_to_history(
    pbi_dir_in_queue: Path, exc: PromptComposeError, now: datetime
) -> None:
    """Append a single iteration entry to HISTORY.md noting a compose failure.

    Runs before Claude is spawned, so the standard "Claude wrote files
    in the PBI dir" path never fires. Without this note the operator
    would see an ``error`` iteration in the event log with no PBI-side
    breadcrumb explaining why. ``_persist_iteration_writes`` picks the
    append up on the same iteration's commit.
    """
    if not pbi_dir_in_queue.is_dir():
        # Defensive: claim_pbi materialises this directory before
        # _run_ralph is called. Skip rather than crash the recovery path.
        return
    history = pbi_dir_in_queue / "HISTORY.md"
    entry = (
        f"\n## Iteration — {now.isoformat()} — prompt compose error\n"
        f"- error: {exc}\n"
        f"- outcome: error (loop did not crash)\n"
    )
    with history.open("a", encoding="utf-8") as handle:
        handle.write(entry)


def _move_to_blocked_with_reason(cfg: ExecutorConfig, pbi: PBI, *, reason: str) -> None:
    """Append the reason to the PBI's HISTORY.md, then move it inbox -> blocked.

    Used by ``iterate_once`` when ``_claim_pbi`` raises ``_ClaimError``.
    The HISTORY.md append happens BEFORE ``git mv`` so the move's single
    commit captures both the relocation and the failure record. HISTORY.md
    is created when missing — the PBI directory schema requires it, but
    being defensive here keeps a malformed inbox PBI from masking the
    real claim failure with a write error.
    """
    queue_repo = _queue_repo_root(cfg)
    inbox_dir = queue_repo / ".ralph" / "inbox" / pbi.id
    history = inbox_dir / "HISTORY.md"
    now = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    entry = f"\n## Claim failed — {now}\n\n{reason}\n"
    existing = history.read_text(encoding="utf-8") if history.is_file() else ""
    history.write_text(existing + entry, encoding="utf-8")
    move_inbox_to_blocked(cfg, pbi)


def _move_current_to_blocked_with_reason(cfg: ExecutorConfig, pbi: PBI, *, reason: str) -> None:
    """Append the reason to the PBI's HISTORY.md, then move it current -> blocked.

    Sibling of ``_move_to_blocked_with_reason`` for the resume-path
    self-heal: a PBI in ``current/`` whose work worktree cannot be
    (re)materialised — e.g. an earlier iteration crashed mid-claim before
    the worktree existed and the target repo's ``origin/<main_branch>``
    is still missing — cannot make progress. Demote it to ``blocked/``
    with the diagnostic in HISTORY.md so an operator can triage.
    """
    queue_repo = _queue_repo_root(cfg)
    current_dir = queue_repo / ".ralph" / "current" / pbi.id
    history = current_dir / "HISTORY.md"
    now = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    entry = f"\n## Claim failed — {now}\n\n{reason}\n"
    existing = history.read_text(encoding="utf-8") if history.is_file() else ""
    history.write_text(existing + entry, encoding="utf-8")
    move_current_to_blocked(cfg, pbi)


def _spawn_and_classify(
    cfg: ExecutorConfig, pbi: PBI, pbi_dir_in_queue: Path, now: datetime
) -> ClaudeOutcome:
    """Spawn ``claude -p`` against the PBI and classify the result.

    Spawn cwd: Claude runs against the per-PBI work worktree inside the
    target clone (populated by ``_claim_pbi`` and threaded through on
    ``pbi.work_worktree``). ``pbi_dir_in_queue`` points at the PBI's
    directory inside the queue clone (``<workspace_root>/queue-<instance_id>/
    .ralph/current/<PBI-ID>/``) so Claude can read PROMPT.md / PBI.md /
    HISTORY.md and write STUCK.md / HISTORY.md without leaving the
    target checkout.

    ``PromptComposeError`` is converted into a synthetic classified
    ``error`` outcome (with a HISTORY.md breadcrumb) so the loop
    survives a missing/malformed prompt tree instead of crashing.
    """
    # ``cwd`` falls back to ``pbi.work_worktree`` inside
    # ``spawn_claude_p`` (populated by ``_claim_pbi`` from the target
    # clone), so no explicit cwd kwarg is needed here. ``pbi_dir``
    # points at the PBI's directory inside the queue clone so Claude
    # can read PROMPT.md / HISTORY.md / PBI.md and write STUCK.md /
    # HISTORY.md without leaving its target-clone working tree.
    try:
        outcome = spawn_claude_p(
            cfg,
            pbi,
            pbi_dir=pbi_dir_in_queue,
        )
    except PromptComposeError as exc:
        # PromptComposeError fires when the queue clone is missing
        # the prompt/ topic-folder tree (or it's malformed). The
        # composer's own docstring promises this is surfaced as a
        # classified ``error`` iteration so the loop survives —
        # before this catch, the exception propagated unhandled
        # through ``run_loop`` and felled the whole executor process
        # on the first PBI claim of a brand-new queue repo. Record
        # the reason in HISTORY.md and synthesise an error outcome
        # so the existing attempt-counter / max-attempts machinery
        # routes the PBI through the normal failure path.
        log.error("PBI %s prompt-compose failed: %s", pbi.id, exc)
        _append_compose_error_to_history(pbi_dir_in_queue, exc, now)
        outcome = ClaudeOutcome(
            kind="error",
            pr_url=None,
            stdout="",
            stderr=f"prompt-compose error: {exc}",
            exit_code=1,
            duration_seconds=0.0,
        )
    log.info("PBI %s outcome=%s exit=%d", pbi.id, outcome.kind, outcome.exit_code)
    return outcome


def _bump_attempts_on_failure(
    cfg: ExecutorConfig,
    pbi: PBI,
    outcome: ClaudeOutcome,
    now: datetime,
    event_log: EventLog,
) -> tuple[ClaudeOutcome, IterationResult] | None:
    """Bump the attempt counter on failure outcomes; block the PBI on overflow.

    Increments the attempt counter ONLY when the outcome is ``stuck`` or
    ``error`` (i.e. a genuine failed iteration). ``partial`` outcomes
    represent legitimate multi-step progress and do NOT count against
    the max-attempts budget — otherwise long plans (many sub-tasks
    spread across iterations) would always hit the wall.

    Returns ``None`` when the caller should fall through to the
    outcome-specific handling (non-failure outcome, or a successful
    increment — the ``attempt.incremented`` event is appended here). If
    the increment pushes the counter past the configured maximum, the
    PBI is moved to ``blocked/`` and a synthetic ``error`` outcome plus
    the ``ran_stuck`` result are returned for the caller to early-return
    — mirroring the AttemptsExceeded path.
    """
    # --- Plan 9: bump attempt counter ONLY on failure outcomes -------
    # `partial` outcomes are legitimate multi-step progress and don't
    # count toward the failure budget. Only stuck / error do.
    if outcome.kind not in ("stuck", "error"):
        return None
    counter = AttemptCounter(pbi_dir=pbi.path)
    try:
        new_attempts = counter.increment()
    except AttemptsExceeded as exc:
        log.warning(
            "PBI %s exceeded max failed attempts (%d/%d); moving to blocked/",
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
        target = _queue_repo_root(cfg) / ".ralph" / "blocked" / pbi.id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # shutil.move would silently move pbi.path INSIDE the existing
            # target dir, producing .ralph/blocked/<id>/<id>/ — invisible to
            # the queue scanner. Mirrors the same guard in
            # ralph_executor/safety/stuck.py::move_to_blocked.
            raise FileExistsError(
                f"cannot move {pbi.path} to {target}: target already exists"
            ) from exc
        # Clean up the work worktree BEFORE the move — see the
        # equivalent comment in ``_handle_pr_created``.
        _cleanup_work_worktree(cfg, pbi)
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
    return None


def _handle_pr_created(
    cfg: ExecutorConfig,
    pbi: PBI,
    outcome: ClaudeOutcome,
    now: datetime,
    event_log: EventLog,
) -> IterationResult:
    """Promote a ``pr_created`` PBI from ``current/`` to ``pending-pr/``."""
    # The diff must run against the TARGET clone (which holds the
    # feature branch the PR was opened from), NOT ralph's own
    # checkout. Derive the clone root from ``pbi.target_info``
    # populated by ``_claim_pbi`` / ``iterate_once``'s resume
    # path. Defensive empties (symmetric to the resume path's
    # tolerance for a missing clone): ``target_info=None`` from
    # malformed frontmatter, or the deterministic clone_root
    # not on disk (transient fetch failure earlier in the
    # iteration) — log + surface an empty touched-files list
    # rather than crash; ``pr_created`` itself is still valid.
    touched: list[str] = []
    if pbi.target_info is None:
        log.warning(
            "PBI %s pr_created but target_info missing; touched_files=[]",
            pbi.id,
        )
    else:
        clone_root = cfg.workspace_root / "clones" / pbi.target_info.owner / pbi.target_info.name
        if not clone_root.is_dir():
            log.warning(
                "PBI %s pr_created but target clone %s is missing; touched_files=[]",
                pbi.id,
                clone_root,
            )
        else:
            touched = git_ops.diff_names(clone_root, cfg.main_branch, _feature_branch_name(pbi))
    # Clean up the work worktree BEFORE the queue move — the move
    # invalidates ``pbi.path`` (used by ``_read_target_repo_from_pbi``
    # when ``pbi.work_worktree`` was not threaded through).
    _cleanup_work_worktree(cfg, pbi)
    move_current_to_pending_pr(
        cfg,
        pbi,
        event_log=event_log,
        pr_url=outcome.pr_url,
        touched_files=touched,
        now=now,
    )
    return IterationResult(outcome="ran_pr_created", pbi_id=pbi.id, pr_url=outcome.pr_url)


def _handle_stuck_outcome(
    cfg: ExecutorConfig, pbi: PBI, event_log: EventLog
) -> IterationResult | None:
    """Handle a ``stuck`` outcome via Plan 9 Layer 1 STUCK.md detection.

    ``handle_stuck`` moves the PBI to ``blocked/`` and returns a
    ``StuckOutcome`` carrying a ``pbi.blocked`` event that is appended
    to the event log here. Returns ``None`` when Claude reported stuck
    but no STUCK.md is present — the caller falls through to ``partial``
    and the PBI stays in ``current/`` for the next iteration.
    """
    # --- Plan 9 Layer 1: STUCK.md detection ----------------------
    stuck_outcome = handle_stuck(
        cfg=cfg,
        pbi=pbi,
        now=datetime.now(tz=UTC),
        event_log=event_log,
    )
    if stuck_outcome is not None:
        event_log.append(stuck_outcome.event)
        log.info("PBI %s stuck: %s", pbi.id, stuck_outcome.reason)
        _cleanup_work_worktree(cfg, pbi)
        return IterationResult(outcome="ran_stuck", pbi_id=pbi.id)
    return None


def _run_ralph(cfg: ExecutorConfig, pbi: PBI) -> tuple[ClaudeOutcome, IterationResult]:
    """Spawn ``claude -p`` against the current PBI and classify the result.

    Composer over the outcome-phase helpers: ``_spawn_and_classify`` →
    ``_bump_attempts_on_failure`` (early-returns ``ran_stuck`` on attempt
    overflow) → ``_handle_pr_created`` → ``_handle_stuck_outcome``
    (``None`` falls through) → error → partial.

    Multi-step PBI discipline: ``partial`` and ``error`` outcomes leave
    the PBI in ``current/``; ``pr_created`` promotes to ``pending-pr/``;
    ``stuck`` triggers ``handle_stuck`` (Layer 1) which moves the PBI to
    ``blocked/``.

    Event emission scope: this function only consumes the classified
    ``ClaudeOutcome`` and emits ``pbi.*`` / ``attempt.incremented`` /
    ``pbi.blocked`` events. ``pr.green_then_red`` (post-merge regression
    detection) is NOT emitted here — that observation requires polling
    PR check state AFTER pr_created has promoted the PBI to
    ``pending-pr/``, and is the sweep observer's job per Plan 19b. The
    CI-green verifier wired into ``classify_outcome`` by Plan 18 only
    decides classification at spawn-time (gating ``pr_created`` on
    required-check pass); it does not itself produce a
    ``PR_GREEN_THEN_RED`` event.
    """
    now = datetime.now(tz=UTC)
    event_log = open_log(_queue_repo_root(cfg))
    try:
        pbi_dir_in_queue = _queue_repo_root(cfg) / ".ralph" / "current" / pbi.id
        outcome = _spawn_and_classify(cfg, pbi, pbi_dir_in_queue, now)
        attempt_overflow = _bump_attempts_on_failure(cfg, pbi, outcome, now, event_log)
        if attempt_overflow is not None:
            return attempt_overflow
        if outcome.kind == "pr_created":
            return outcome, _handle_pr_created(cfg, pbi, outcome, now, event_log)
        if outcome.kind == "stuck":
            stuck_result = _handle_stuck_outcome(cfg, pbi, event_log)
            if stuck_result is not None:
                return outcome, stuck_result
            # Claude reported stuck but no STUCK.md present -- fall through
            # to partial (the PBI stays in current/ for the next iteration).
        if outcome.kind == "error":
            return outcome, IterationResult(outcome="ran_error", pbi_id=pbi.id)
        return outcome, IterationResult(outcome="ran_partial", pbi_id=pbi.id)
    finally:
        event_log.close()


def _current_pbi_id_or_none(cfg: ExecutorConfig) -> str | None:
    try:
        cur = FilesystemQueueSource(cfg).current_pbi()
        return cur.id if cur else None
    except Exception:  # noqa: BLE001 — best-effort context for autobug
        return None


def iterate_once(cfg: ExecutorConfig) -> IterationResult:
    """Run one iteration; wrap the body in a defensive autobug fuse.

    Delegates to :func:`_iterate_once_inner` for the actual work. The
    control-flow exits (``KeyboardInterrupt`` / ``HaltedError`` /
    ``PushRebaseConflict`` / ``UncommittedSource``) propagate unchanged —
    they are handled inline by callers. Any OTHER ``BaseException`` is
    reported to :func:`autobug.detect_python_crash` BEFORE being
    re-raised, so an inner-loop crash is captured as a bug PBI without
    waiting for the top-level ``cli.run_loop_with_autobug`` wrapper to
    catch it. Carries ``triggering_pbi_id`` from the queue's current/
    state so the emitted bug has direct context.
    """
    try:
        return _iterate_once_inner(cfg)
    except (KeyboardInterrupt, HaltedError, PushRebaseConflict, UncommittedSource):
        raise
    except BaseException as exc:
        if getattr(cfg, "autobug_enabled", True):
            try:
                from ralph_executor import autobug as _autobug

                _ctx = _autobug.Context(
                    queue_root=cfg.queue_clone_path,
                    state_dir=cfg.queue_clone_path / ".ralph" / "state",
                    env=dict(os.environ),
                    now=datetime.now(tz=UTC),
                    ralph_sha=os.environ.get("RALPH_SHA", "<unknown>"),
                    bot_author_email=cfg.bot_author_email,
                    triggering_pbi_id=_current_pbi_id_or_none(cfg),
                    queue_branch=cfg.queue_branch,
                )
                from datetime import timedelta as _td

                from ralph_executor.autobug.fuses import RateLimitConfig

                _autobug.detect_python_crash(
                    exc,
                    _ctx,
                    target_repo=cfg.queue_repo,
                    severity=cfg.autobug_severity_python_crash,
                    rate_cfg=RateLimitConfig(
                        max_writes=cfg.autobug_rate_max,
                        window=_td(minutes=cfg.autobug_rate_window_minutes),
                    ),
                    dedup_window_days=cfg.autobug_dedup_done_window_days,
                )
                # Mark the exception so cli.run_loop_with_autobug's outer
                # handler does not re-emit (which would dedup-bump
                # occurrences to 2 for a single crash).
                with contextlib.suppress(AttributeError, TypeError):
                    exc.__autobug_emitted__ = True  # type: ignore[attr-defined]
            except BaseException as inner:  # noqa: BLE001 — never mask the original
                log.warning("autobug loop wire failed: %s", inner)
        raise


def _resume_current(cfg: ExecutorConfig, current: PBI) -> tuple[PBI, IterationResult | None]:
    """Rehydrate a resumed PBI's runtime fields and self-heal its worktree.

    ``FilesystemQueueSource`` doesn't populate ``target_repo`` /
    ``target_info`` / ``work_worktree``; this helper re-derives them and
    self-heals a missing work worktree (a prior claim may have crashed
    after ``move_inbox_to_current`` but before ``ensure_worktree``).

    Returns ``(pbi, early_result)``: on success ``pbi`` carries the
    populated runtime fields and ``early_result`` is ``None``. When the
    self-heal fails (``ensure_worktree`` raises ``GitCommandError``) the
    PBI is demoted current → blocked and ``early_result`` is the
    ``claim_failed`` result the iteration must return.
    """
    # FilesystemQueueSource doesn't populate ``target_repo`` /
    # ``work_worktree`` on the PBI dataclass (it consumes the on-disk
    # schema directly without the multi-target runtime fields).
    # Populate both here so:
    #   * ``_run_ralph``'s terminal-outcome cleanup can re-derive the
    #     target clone_root without depending on ``pbi.path`` (which
    #     the move_*_to_* operations invalidate before cleanup runs);
    #   * ``spawn_claude_p`` uses the per-PBI work worktree inside the
    #     target clone as cwd for resumed PBIs. There is no
    #     process-wide ``repo_path`` to fall back onto after
    #     KILL-RALPH-HOME; absence of ``pbi.work_worktree`` here
    #     would raise ``ConfigError`` inside spawn_claude_p, which is
    #     the correct fail-fast rather than picking a wrong cwd.
    from dataclasses import replace as _replace

    from ralph_executor.url_utils import parse_target_repo

    try:
        target_url = _read_target_repo_from_pbi(current)
    except _ClaimError:
        target_url = ""
    # Parse the URL outside the info guard so resumed PBIs get
    # target_info populated — without it
    # ``spawn_claude_p``'s ``GH_OWNER`` injection (guarded by
    # ``pbi.target_info is not None``) silently no-ops for every
    # iteration after the first, and any ``gh`` / ``pr-github`` call
    # the spawned Claude makes uses the wrong (or absent) owner.
    # No ``TargetRepoInfo`` annotation needed — ``None`` plus the
    # ``parse_target_repo`` return type infer it.
    info = None
    if target_url:
        try:
            info = parse_target_repo(target_url)
        except ValueError:
            # Malformed target_repo on disk shouldn't crash the resume
            # path — let spawn_claude_p fall back to its env-default
            # owner, same as legacy behaviour before this PR.
            info = None
    work_wt: Path | None = None
    if info is not None:
        # ``clone_root`` is fully determined by workspace_root + owner +
        # name; compute deterministically so a transient fetch failure
        # (network blip, auth expired, etc.) does NOT leave
        # ``pbi.work_worktree`` unset — spawn_claude_p raises
        # ConfigError on an unset worktree (the post-KILL-RALPH-HOME
        # contract), and we want the resume path to proceed against
        # the cached deterministic clone instead.
        clone_root = cfg.workspace_root / "clones" / info.owner / info.name
        try:
            from ralph_executor.target_clone import ensure_clone

            clone = ensure_clone(info, workspace_root=cfg.workspace_root)
            # Honour the returned clone_root rather than the
            # deterministic compute. Production ensure_clone always
            # returns the deterministic path — the override matters
            # only for tests that monkeypatch ensure_clone to alias
            # the target clone elsewhere (e.g. to the fake queue
            # clone). Without this, the resume self-heal below skips
            # itself whenever ensure_clone is stubbed to a divergent
            # root.
            clone_root = clone.clone_root
        except Exception:
            log.warning(
                "iterate_once: ensure_clone failed for resumed PBI %s; "
                "using deterministic clone path (may be stale)",
                current.id,
                exc_info=True,
            )
        if clone_root.is_dir():
            _warn_project_toml_in_target_clone(clone_root)
            work_wt = work_worktree_path(clone_root, current.id)
            # Self-heal a missing work worktree. ``ensure_worktree``
            # is idempotent — a no-op when the worktree already
            # exists on the right branch — so the cost on the happy
            # path is one ``git worktree list`` probe. The case it
            # rescues: a prior iteration's claim crashed AFTER
            # ``move_inbox_to_current`` succeeded but BEFORE
            # ``ensure_worktree`` finished (e.g. ``origin/<main>``
            # missing at that moment), leaving the PBI stranded in
            # ``current/`` with no worktree. Without this call the
            # resume path would hand a non-existent ``cwd`` to
            # ``spawn_claude_p`` and the PBI could never recover.
            #
            # If ensure_worktree itself fails (still no
            # ``origin/<main>``), demote the PBI current -> blocked
            # with the reason in HISTORY.md and skip this iteration
            # — the loop must not crash on a malformed target.
            try:
                ensure_worktree(
                    clone_root,
                    worktree_path=work_wt,
                    branch=_feature_branch_name(current),
                    create_branch_from=f"origin/{cfg.main_branch}",
                )
            except git_ops.GitCommandError as exc:
                log.warning(
                    "iterate_once: cannot materialize work worktree for "
                    "resumed PBI %s (%s); moving to blocked/",
                    current.id,
                    exc,
                )
                _move_current_to_blocked_with_reason(cfg, current, reason=str(exc))
                return current, IterationResult(outcome="claim_failed", pbi_id=current.id)
    current = _replace(
        current,
        target_repo=target_url,
        target_info=info,
        work_worktree=work_wt,
    )
    return current, None


def _execute_current(
    cfg: ExecutorConfig,
    current: PBI,
    queue_repo: Path,
    source: FilesystemQueueSource,
) -> IterationResult:
    """Run Ralph on the PBI occupying ``current/`` and persist its writes.

    ``PushRebaseConflict`` from ``_run_ralph``'s terminal-outcome moves
    or from the persist commit is downgraded to a ``push_conflict``
    result so the loop retries next round instead of crashing. Raises
    ``HaltedError`` when the cycle detector trips.
    """
    # Current occupied → run Ralph on it (attempt counter + spawn).
    # ``_run_ralph`` reaches into ``move_current_to_pending_pr`` /
    # ``handle_stuck`` on terminal outcomes, both of which call
    # ``push_with_rebase`` via ``movements._move``. A concurrent
    # writer on the queue repo's ``main`` can make that push raise
    # ``PushRebaseConflict`` — without this catch the executor
    # process would crash, exactly the failure mode this code
    # path exists to prevent.
    try:
        _outcome, result = _run_ralph(cfg, current)
    except PushRebaseConflict as exc:
        log.warning(
            "iterate_once: push conflict during _run_ralph for %s (paths: %s); "
            "loop will retry next round",
            current.id,
            ", ".join(exc.conflict_paths) or "<unknown>",
        )
        return IterationResult(outcome="push_conflict", pbi_id=current.id)
    # Persist any HISTORY.md / STUCK.md / PLAN.md edits Claude wrote
    # inside the PBI dir. The move_current_to_* paths handle their
    # own commits via git mv, but partial/error outcomes leave the
    # PBI in current/ with dirty files that would otherwise be lost.
    event_log = open_log(queue_repo)
    try:
        _persist_iteration_writes(
            cfg,
            current.id,
            event_log=event_log,
            now=datetime.now(tz=UTC),
        )
    except PushRebaseConflict as exc:
        # Concurrent writer advanced the queue repo's main in a way
        # that conflicts with the iteration's persist commit. The
        # local commit was abandoned (rebase --abort), the on-disk
        # writes remain in the queue clone, and the next iteration
        # will re-stage them. Log a WARNING and surface the outcome
        # so operator dashboards see it — the loop must NOT crash.
        log.warning(
            "iterate_once: push conflict on queue main (paths: %s); "
            "skipping this iteration's persist, loop will retry next round",
            ", ".join(exc.conflict_paths) or "<unknown>",
        )
        return IterationResult(outcome="push_conflict", pbi_id=current.id)
    finally:
        event_log.close()
    if _check_cycle_detector(cfg, source):
        # META-BUG + sentinel already written by _check_cycle_detector;
        # raise HaltedError so the caller knows the loop is frozen.
        raise HaltedError(
            meta_bug_id="(see latest META-cycle-* in .ralph/blocked/)",
            meta_bug_path=queue_repo / ".ralph" / "blocked",
            sentinel_path=queue_repo / ".ralph" / "state" / "halted",
        )
    return result


def _sweep_and_pick(
    cfg: ExecutorConfig, source: FilesystemQueueSource, queue_repo: Path
) -> tuple[PBI | None, IterationResult | None]:
    """Run the sweep and pick the next inbox PBI when ``current/`` is empty.

    Returns ``(picked, idle_result)``: exactly one of the pair is
    non-``None``. When the inbox is empty the cycle detector still runs
    (a globally-tripped cycle raises ``HaltedError``) before the ``idle``
    result is returned.
    """
    # Current empty → run the sweep, pick next, claim if any.
    _run_sweep(cfg, source)

    picked = source.pick_next()
    if picked is None:
        # Nothing to do; run the cycle-detector check anyway so a
        # globally-tripped cycle can halt the loop.
        if _check_cycle_detector(cfg, source):
            raise HaltedError(
                meta_bug_id="(see latest META-cycle-* in .ralph/blocked/)",
                meta_bug_path=queue_repo / ".ralph" / "blocked",
                sentinel_path=queue_repo / ".ralph" / "state" / "halted",
            )
        return None, IterationResult(outcome="idle", pbi_id=None)
    return picked, None


def _claim_picked(
    cfg: ExecutorConfig,
    picked: PBI,
    queue_repo: Path,
    source: FilesystemQueueSource,
) -> IterationResult:
    """Claim the picked inbox PBI into ``current/``.

    Transient failures (``PushRebaseConflict``, ``UncommittedSource``)
    are downgraded to retryable results; ``_ClaimError`` demotes the PBI
    inbox → blocked. Raises ``HaltedError`` when the post-claim cycle
    detector trips.
    """
    log.info("claiming PBI %s", picked.id)
    # ``_claim_pbi`` invokes ``move_inbox_to_current`` → ``push_with_rebase``.
    # A concurrent writer on the queue branch can make that push raise
    # ``PushRebaseConflict``; treat it like the run-Ralph case above
    # so the loop continues on the next iteration.
    try:
        claimed = _claim_pbi(cfg, picked)
    except PushRebaseConflict as exc:
        log.warning(
            "iterate_once: push conflict during _claim_pbi for %s (paths: %s); "
            "loop will retry next round",
            picked.id,
            ", ".join(exc.conflict_paths) or "<unknown>",
        )
        return IterationResult(outcome="push_conflict", pbi_id=picked.id)
    except UncommittedSource:
        # External writer (operator, second ralph session) wrote the
        # inbox PBI dir on disk but has not yet committed it on the
        # queue branch. ``_list_pbis`` selects the dir from the
        # filesystem, ``git mv`` then errors ``fatal: source directory
        # is empty``. Treat it as a recoverable transient: next
        # iteration re-scans inbox and succeeds once the writer commits.
        log.warning(
            "iterate_once: inbox dir for %s not yet committed by external "
            "writer; skipping claim, loop will retry next round",
            picked.id,
        )
        return IterationResult(outcome="uncommitted_source", pbi_id=picked.id)
    except _ClaimError as exc:
        # Multi-target prelude failure (missing/invalid target_repo,
        # unsupported host, ``TargetUnreachable``). The PBI is still in
        # inbox/; demote it to blocked/ with the reason in HISTORY.md so
        # the operator can triage. Skip the cycle detector — the move is
        # final and the failure is not a retry signal.
        log.warning("claim failed for PBI %s: %s; moving to blocked/", picked.id, exc)
        _move_to_blocked_with_reason(cfg, picked, reason=str(exc))
        return IterationResult(outcome="claim_failed", pbi_id=picked.id)
    if _check_cycle_detector(cfg, source):
        raise HaltedError(
            meta_bug_id="(see latest META-cycle-* in .ralph/blocked/)",
            meta_bug_path=queue_repo / ".ralph" / "blocked",
            sentinel_path=queue_repo / ".ralph" / "state" / "halted",
        )
    return IterationResult(outcome="claimed", pbi_id=claimed.id)


def _iterate_once_inner(cfg: ExecutorConfig) -> IterationResult:
    """Run a single iteration of the loop; composer over the phase helpers.

    Halt check → queue pull → (``_resume_current`` → ``_execute_current``)
    when ``current/`` is occupied, else ``_sweep_and_pick`` →
    ``_claim_picked``. The no-work case is an idempotent no-op (``"idle"``).

    Raises ``HaltedError`` if the halt sentinel is active (Plan 9 Layer 3).
    """
    # --- Plan 9 Layer 3: refuse to start while sentinel is active --------
    queue_repo = _queue_repo_root(cfg)
    status = check_halt_sentinel(queue_repo)
    if status == HaltStatus.HALTED:
        raise HaltedError(
            meta_bug_id="(see .ralph/state/halted)",
            meta_bug_path=queue_repo / ".ralph" / "blocked",
            sentinel_path=queue_repo / ".ralph" / "state" / "halted",
        )

    _pull_queue(cfg)
    source = FilesystemQueueSource(cfg)

    current = source.current_pbi()
    if current is not None:
        current, early = _resume_current(cfg, current)
        if early is not None:
            return early
        return _execute_current(cfg, current, queue_repo, source)

    picked, idle = _sweep_and_pick(cfg, source, queue_repo)
    if picked is None:
        assert idle is not None  # _sweep_and_pick: exactly one of the pair
        return idle
    return _claim_picked(cfg, picked, queue_repo, source)


# ----------------------------------------------------------------------
# Run forever (with an optional iteration cap for tests)
# ----------------------------------------------------------------------


def run_loop(
    cfg: ExecutorConfig, *, max_iterations: int | None = None
) -> Iterator[IterationResult]:
    """Run iterations until interrupted, drained, or ``max_iterations`` reached.

    Yields each ``IterationResult`` so callers (and tests) can observe
    progress. ``max_iterations`` is primarily for tests; in production
    callers pass ``None`` and the loop terminates either on KeyboardInterrupt
    or — under the default ``cfg.watch_mode=False`` — once
    ``cfg.idle_exit_threshold`` consecutive ``idle`` outcomes have stacked
    up. Any non-idle outcome (claimed / ran_partial / ran_pr_created / …)
    resets the consecutive-idle counter so a long PBI can interleave with
    quiet ticks without false-tripping the drain.

    ``cfg.watch_mode=True`` preserves the legacy daemon behaviour: idle
    iterations sleep for ``cfg.iteration_sleep_seconds`` and the loop
    keeps polling forever (still bounded by ``max_iterations`` when set).

    Raises ``HaltedError`` if the halt sentinel blocks the loop on entry
    (Plan 9 Layer 3). Callers that want a gentle drain should catch
    ``HaltedError`` and surface it to the operator.

    Scope 1 multi-ralph: acquires an OS-level exclusive lock on
    ``<workspace>/queue-<instance_id>/.ralph.lock`` before the first
    iteration and releases it on clean exit. The OS releases the lock on
    crash exit too, so no stale-lock recovery is needed.
    ``LockfileError`` propagates unwrapped — a same-workspace contender
    must surface to the operator rather than silently no-op the loop.
    """
    # Drain-on-idle log goes through the cli logger so operators grep one
    # consistent ``ralph_executor.cli: queue drained …`` line regardless of
    # whether the iteration loop runs from cli.main or a programmatic caller.
    cli_log = logging.getLogger("ralph_executor.cli")

    # Materialise the queue clone BEFORE acquiring the lockfile — the
    # lockfile lives at ``<queue-clone>/.ralph.lock``, so a cold-start
    # where the queue dir does not yet exist would otherwise leave the
    # lockfile inside an empty directory that subsequent ``git clone``
    # (inside ``ensure_queue_clone``) refuses to overwrite. After this
    # call, iterate_once's own ``_pull_queue`` becomes a cheap fetch+pull.
    _pull_queue(cfg)

    lock_path = cfg.queue_clone_path / ".ralph.lock"
    lock = WorkspaceLockfile(
        lock_path,
        instance_id=cfg.instance_id,
        hostname=socket.gethostname(),
    )
    lock.acquire()
    atexit.register(lock.release)
    try:
        count = 0
        consecutive_idle = 0
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
            if result.outcome == "idle":
                consecutive_idle += 1
            else:
                consecutive_idle = 0
            count += 1
            if max_iterations is not None and count >= max_iterations:
                return
            if not cfg.watch_mode and consecutive_idle >= cfg.idle_exit_threshold:
                cli_log.info(
                    "queue drained -- exiting after %d consecutive idle iterations",
                    consecutive_idle,
                )
                return
            # Sleep only between iterations that found nothing to do.
            if result.outcome == "idle":
                time.sleep(cfg.iteration_sleep_seconds)
    finally:
        lock.release()
