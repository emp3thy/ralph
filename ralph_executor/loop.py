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
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml

if TYPE_CHECKING:
    from ralph_executor.url_utils import TargetRepoInfo

from ralph_executor import git_ops
from ralph_executor.claude_spawn import ClaudeOutcome, spawn_claude_p
from ralph_executor.config import ExecutorConfig
from ralph_executor.git_ops import PushRebaseConflict
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue.movements import (
    move_current_to_pending_pr,
    move_inbox_to_blocked,
    move_inbox_to_current,
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
    evaluate_all,
    halt_and_acknowledge,
    handle_stuck,
    open_log,
)
from ralph_executor.types import PBI
from ralph_executor.worktree import (
    ensure_worktree,
    queue_worktree_path,
    remove_worktree,
    work_worktree_path,
)

log = logging.getLogger(__name__)


def _queue_repo_root(cfg: ExecutorConfig) -> Path:
    """Repo root that owns ``.ralph/`` (events.db, sentinel, blocked/, …).

    In worktree mode ``.ralph/`` lives in the queue worktree, not the
    primary checkout (which is typically on ``main`` and has no
    ``.ralph/`` directory). Every operation that reads or writes under
    ``.ralph/`` — opening the event log, moving PBIs to ``.ralph/blocked/``,
    handling STUCK.md, checking/writing the halt sentinel — must route
    through this helper. Otherwise the side-effects silently land in the
    primary checkout's working tree, where they are never committed or
    pushed and become invisible after a process restart.
    """
    if cfg.use_worktrees:
        return queue_worktree_path(cfg.repo_path)
    return cfg.repo_path


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
    """Drive one sweep over ``.ralph/pending-pr/`` (Plan 8).

    Builds a ``SweepContext`` from the executor config and current
    environment, then delegates to ``ralph_executor.sweep.run``. The
    ``source`` argument is unused — the sweep reads ``.ralph/pending-pr/``
    directly from the filesystem so it can stay isolated from the queue
    abstraction.

    Production-safety: the sweep needs ``cfg.bot_author_email`` (used to
    skip ralph-authored PR comments so the loop doesn't feed back into
    itself) and a PR-skill scripts directory matching the configured git
    host. If either is missing the sweep is skipped with a WARNING — the
    loop must keep running rather than abort, since pre-Plan-8 deployments
    and the bulk of the executor test suite don't set the author email.
    Validation of ``cfg.stale_days`` (must be positive) lives in
    ``config.load_config``; this function trusts the value.
    """
    del source  # sweep walks the filesystem directly
    if not cfg.bot_author_email:
        log.warning(
            "sweep: bot_author_email is not set (TOML key 'bot_author_email' "
            "or env RALPH_ADO_AUTHOR_EMAIL); skipping sweep this iteration"
        )
        return

    scripts_path = _pr_skill_scripts_path(cfg)
    if not scripts_path.is_dir():
        log.warning(
            "sweep: PR-skill scripts directory not found at %s; skipping",
            scripts_path,
        )
        return

    from ralph_executor.sweep import run as run_sweep
    from ralph_executor.sweep.runner import SweepConfig, SweepContext

    sweep_cfg = SweepConfig(
        ralph_author_email=cfg.bot_author_email,
        max_attempts=cfg.max_attempts,
        stale_threshold=timedelta(days=cfg.stale_days),
        now=datetime.now(tz=UTC),
        auto_merge_clean_prs=cfg.auto_merge_clean_prs,
    )
    # Open the event log so the sweep can emit cycle-detector events
    # (Plan 19b: PR_MERGED + PBI_CLOSED on pending-pr → done,
    # PR_GREEN_THEN_RED on green→red CI transitions). Close in a finally
    # so a sweep-side crash never leaks the SQLite handle.
    event_log = open_log(_queue_repo_root(cfg))
    try:
        sweep_ctx = SweepContext(
            # `.ralph/` lives in the queue worktree under cfg.use_worktrees,
            # not the primary checkout (which is typically on `main` and has
            # no `.ralph/`). Same reasoning as `_queue_repo_root` for all
            # other `.ralph/` accesses in this module.
            queue_root=_queue_repo_root(cfg) / ".ralph",
            ado_pr_scripts_path=scripts_path,
            config=sweep_cfg,
            # Always derive the repo name from the primary checkout, NOT
            # from queue_root.parent.name — the latter would be "queue" in
            # worktree mode (the .ralph-work/queue/ dir).
            repo_name=cfg.repo_path.name,
            event_log=event_log,
        )
        result = run_sweep(ctx=sweep_ctx)
    finally:
        # Wrap close() so a failure here (e.g. sqlite flush error) does
        # not mask an exception from run_sweep — losing the real cause
        # makes post-mortem debugging much harder. Log close() failures
        # at WARNING and let the original (if any) propagate unchanged.
        try:
            event_log.close()
        except Exception as exc:
            log.warning("sweep: event_log.close() failed: %s", exc)
    log.info(
        "sweep: scanned %d PBIs (actions=%d, errors=%d)",
        result.pbis_scanned,
        len(result.actions),
        len(result.errors),
    )


def _pr_skill_scripts_path(cfg: ExecutorConfig) -> Path:
    """Return the on-disk scripts directory for the configured PR skill.

    ``cfg.git_host == "github"`` → ``skills/pr-github/scripts/``.
    ``cfg.git_host == "ado"``    → ``skills/ado-pr/scripts/``.
    Empty / unknown host: prefer ``pr-github`` if it exists, else fall
    back to ``ado-pr`` (existence is verified by the caller).
    """
    host = (cfg.git_host or "").strip().lower()
    if host == "github":
        return cfg.repo_path / "skills" / "pr-github" / "scripts"
    if host == "ado":
        return cfg.repo_path / "skills" / "ado-pr" / "scripts"
    pr_github = cfg.repo_path / "skills" / "pr-github" / "scripts"
    if pr_github.is_dir():
        return pr_github
    return cfg.repo_path / "skills" / "ado-pr" / "scripts"


def _check_cycle_detector(cfg: ExecutorConfig, source: FilesystemQueueSource) -> bool:
    """Evaluate all cycle-detector rules against the recent event log.

    Returns ``True`` if any signal tripped (and the loop should halt after
    this call completes the META-BUG + sentinel write). Returns ``False``
    when no signals fire.

    The function is kept as a module-level callable so tests can monkeypatch
    it without dependency-injection (reconciliation #9).
    """
    now = datetime.now(tz=UTC)
    event_log = open_log(_queue_repo_root(cfg))
    try:
        events = event_log.recent(window=timedelta(hours=72), now=now)
    finally:
        event_log.close()
    signals = evaluate_all(events, now, cfg)
    if not signals:
        return False
    log.warning(
        "cycle detector tripped (%d signal(s)); writing META-BUG + sentinel",
        len(signals),
    )
    halt_and_acknowledge(repo=_queue_repo_root(cfg), signals=signals, now=now)
    return True


# ----------------------------------------------------------------------
# One iteration
# ----------------------------------------------------------------------


def _ensure_on_queue_branch(cfg: ExecutorConfig) -> None:
    """Make the primary checkout's HEAD match ``cfg.queue_branch``.

    In worktree mode the queue worktree owns ``cfg.queue_branch`` and
    git refuses to check the same branch out in two worktrees, so this
    is a no-op — callers that need to read ``.ralph/`` use the queue
    worktree path instead.
    """
    if cfg.use_worktrees:
        return
    if git_ops.current_branch(cfg.repo_path) != cfg.queue_branch:
        git_ops.checkout(cfg.repo_path, cfg.queue_branch)


def _persist_iteration_writes(
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

    In worktree mode (``cfg.use_worktrees=True``) all git operations run
    against the long-lived queue worktree at
    ``<repo>/.ralph-work/queue/`` — no branch switching on the primary
    checkout. Legacy single-checkout mode keeps the original behaviour
    (``_ensure_on_queue_branch`` swaps the primary to ``cfg.queue_branch``
    before staging).

    Emits ``FILE_TOUCHED`` to ``event_log`` when a new commit lands and
    the diff is non-empty. The cycle detector reserves the event for
    future per-iteration rules (no current rule reads it; emit for
    forward compatibility).
    """
    _ensure_on_queue_branch(cfg)
    queue_repo = queue_worktree_path(cfg.repo_path) if cfg.use_worktrees else cfg.repo_path
    pbi_dir = queue_repo / ".ralph" / "current" / pbi_id
    if not pbi_dir.is_dir():
        # PBI was moved out of current/ by handle_stuck or
        # move_current_to_pending_pr — nothing to persist here.
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
        # origin/<queue_branch> instead of failing the push outright. The
        # caller (iterate_once) catches PushRebaseConflict and converts
        # it to a recoverable LoopResult so the loop keeps running.
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


def _pull_queue(cfg: ExecutorConfig) -> None:
    log.debug("pulling %s", cfg.queue_branch)
    if cfg.use_worktrees:
        # Materialise (or reuse) the queue worktree before pulling so the
        # very first iteration on a fresh repo still has a path to update.
        queue_wt = queue_worktree_path(cfg.repo_path)
        ensure_worktree(cfg.repo_path, worktree_path=queue_wt, branch=cfg.queue_branch)
        git_ops.pull(queue_wt, cfg.queue_branch)
        return
    _ensure_on_queue_branch(cfg)
    git_ops.pull(cfg.repo_path, cfg.queue_branch)


def _pull_main(cfg: ExecutorConfig) -> None:
    log.debug("pulling %s", cfg.main_branch)
    git_ops.checkout(cfg.repo_path, cfg.main_branch)
    git_ops.pull(cfg.repo_path, cfg.main_branch)


def _feature_branch_name(pbi: PBI) -> str:
    return f"ralph/{pbi.id}"


class _ClaimError(RuntimeError):
    """Raised when claim fails for a reason warranting blocked/ + HISTORY entry.

    Caught by ``iterate_once``, which moves the PBI to ``blocked/<id>/`` and
    appends the error message to HISTORY.md. Used by the multi-target claim
    path (target_repo parse, host check, ensure_clone) where failures are
    not retryable inside the loop itself.
    """


_ENTRY_FILENAMES: tuple[str, ...] = ("PBI.md", "BUG.md", "FEEDBACK.md")


def _read_target_repo_from_pbi(pbi: PBI) -> str:
    """Read the ``target_repo`` field from the PBI's entry-file frontmatter.

    Probes ``PBI.md``, ``BUG.md``, ``FEEDBACK.md`` in order — matches
    pbi_reader's entry-file discovery. Raises ``_ClaimError`` when the
    entry file is missing, the YAML frontmatter cannot be parsed, or the
    ``target_repo`` field is absent / empty.
    """
    entry: Path | None = None
    for name in _ENTRY_FILENAMES:
        candidate = pbi.path / name
        if candidate.is_file():
            entry = candidate
            break
    if entry is None:
        raise _ClaimError(
            f"PBI {pbi.id} has no entry file (looked for {', '.join(_ENTRY_FILENAMES)})"
        )

    text = entry.read_text(encoding="utf-8")
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        raise _ClaimError(f"PBI {pbi.id} entry file {entry.name} has no frontmatter fence")
    lines = text.splitlines()
    close_idx = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close_idx is None:
        raise _ClaimError(f"PBI {pbi.id} entry file {entry.name} has no closing fence")
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:close_idx]))
    except yaml.YAMLError as exc:
        raise _ClaimError(f"PBI {pbi.id} YAML parse error: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise _ClaimError(f"PBI {pbi.id} frontmatter is not a mapping")
    target_repo = frontmatter.get("target_repo")
    if not target_repo:
        raise _ClaimError(f"PBI {pbi.id} missing target_repo field")
    return str(target_repo)


def _cleanup_work_worktree(cfg: ExecutorConfig, pbi: PBI) -> None:
    """Remove the per-PBI work worktree on a terminal iteration outcome.

    Called when the PBI leaves ``current/`` (pr_created → pending-pr,
    handle_stuck → blocked, max-attempts → blocked). The feature branch
    ``ralph/<PBI-ID>`` is preserved so pending-pr PBIs keep a branch to
    point the PR at; only the working directory at
    ``<clone-root>/.ralph-work/<PBI-id>/`` is torn down.

    Locates the owning git repo via ``ensure_clone`` against the PBI's
    ``target_repo`` URL — this is idempotent (no clone if already present,
    just a fetch) and returns the same ``clone_root`` the claim used.
    Calling ``ensure_clone`` again avoids relying on ``pbi.work_worktree``
    (which is runtime-only and absent after a queue re-read between
    iterations); falling back to ``cfg.repo_path`` would look in the
    wrong directory entirely and leak orphan worktrees per PBI.

    No-op in legacy single-checkout mode or when target_repo / ensure_clone
    are unavailable (defensive). Tolerant of removal failures — an orphan
    worktree is recoverable (operator can ``git worktree prune``), but
    raising here would obscure the real terminal outcome the iteration is
    reporting.
    """
    if not cfg.use_worktrees:
        return
    if pbi.work_worktree is not None:
        work_wt = pbi.work_worktree
        # Work worktrees live at <clone-root>/.ralph-work/<pbi-id>/, so
        # the owning git repo is the worktree's grandparent.
        owning_repo = work_wt.parent.parent
    else:
        # Re-derive clone_root from the PBI's target_repo field
        # (populated by ``iterate_once`` immediately after the queue
        # read so it survives the move_*_to_* operations that invalidate
        # ``pbi.path``). Idempotent in production (fetch-only when clone
        # already exists); honours the tests' monkeypatched
        # ``ensure_clone`` so fixture clone_roots match what the claim
        # used.
        if not pbi.target_repo:
            log.warning(
                "PBI %s missing target_repo on terminal outcome; cannot "
                "resolve work worktree path — orphan may remain",
                pbi.id,
            )
            return
        try:
            from ralph_executor.target_clone import ensure_clone
            from ralph_executor.url_utils import parse_target_repo

            info = parse_target_repo(pbi.target_repo)
            clone = ensure_clone(info, workspace_root=cfg.workspace_root)
        except Exception:
            log.warning(
                "failed to resolve clone root for PBI %s; orphan work worktree may remain",
                pbi.id,
                exc_info=True,
            )
            return
        owning_repo = clone.clone_root
        work_wt = work_worktree_path(owning_repo, pbi.id)
    try:
        remove_worktree(owning_repo, work_wt)
    except Exception:
        log.warning(
            "failed to remove work worktree at %s; orphan left for manual prune",
            work_wt,
            exc_info=True,
        )


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


def _claim_pbi(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Move PBI into current/ and create the per-PBI feature branch.

    Multi-target prelude (runs before legacy/worktree fork):
      0. ``_read_target_repo_from_pbi`` — read ``target_repo`` from the
         PBI entry-file frontmatter.
      0b. ``parse_target_repo`` — split into host/owner/name; ValueError
          surfaces as ``_ClaimError('invalid target_repo URL: …')``.
      0c. Host gate — only ``github.com`` is supported on this PBI;
          other hosts raise ``_ClaimError('unsupported host …')``.

    Worktree mode (``cfg.use_worktrees=True``):
      1. Ensure the long-lived queue worktree at
         ``<repo>/.ralph-work/queue/`` is on ``cfg.queue_branch``.
      2. ``target_clone.ensure_clone`` — clone-once / fetch-each-iter the
         target repo into ``<workspace_root>/clones/<owner>-<name>/``;
         ``TargetUnreachable`` maps to ``_ClaimError('target unreachable: …')``.
      3. ``move_inbox_to_current`` operates against the queue worktree
         (movements._move resolves the path via ``cfg.use_worktrees``).
      4. Materialise the per-PBI work worktree INSIDE the target clone at
         ``<clone_root>/.ralph-work/<PBI-ID>/`` on ``ralph/<PBI-ID>``,
         forked from the clone's ``origin/<main_branch>`` when the branch
         is new.

    Legacy single-checkout mode (``cfg.use_worktrees=False``):
      1. ``move_inbox_to_current`` (commits + pushes on ralph-queue,
         emits ``PBI_OPENED`` to the event log for the cycle detector).
      2. ``git pull main``.
      3. ``git checkout -b ralph/<PBI-ID>`` off main.
      4. Switch back to ``ralph-queue`` so the caller sees the updated
         ``.ralph/current/`` on disk immediately after claim.  The
         feature branch is checked out again in ``_run_ralph`` just
         before spawning Claude.
    """
    from dataclasses import replace

    from ralph_executor.url_utils import parse_target_repo

    target_url = _read_target_repo_from_pbi(pbi)
    try:
        info = parse_target_repo(target_url)
    except ValueError as exc:
        raise _ClaimError(f"invalid target_repo URL: {exc}") from exc
    if info.host != "github.com":
        raise _ClaimError(f"unsupported host {info.host!r} (only github.com is supported)")

    if cfg.use_worktrees:
        return _claim_pbi_worktree(cfg, pbi, target_url=target_url, info=info)

    event_log = open_log(_queue_repo_root(cfg))
    try:
        moved = move_inbox_to_current(
            cfg,
            pbi,
            event_log=event_log,
            now=datetime.now(tz=UTC),
        )
    finally:
        event_log.close()
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
    return replace(moved, target_repo=target_url, target_info=info)


def _claim_pbi_worktree(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    target_url: str,
    info: TargetRepoInfo,
) -> PBI:
    """Worktree-mode implementation of ``_claim_pbi``.

    The primary checkout's branch is never touched. ``.ralph/`` reads
    and writes go through the queue worktree; code edits go through the
    per-PBI work worktree, which now lives INSIDE the target's clone
    (``<workspace_root>/clones/<owner>-<name>/.ralph-work/<PBI-ID>/``).
    """
    from dataclasses import replace

    from ralph_executor import target_clone as tc_mod

    queue_wt = queue_worktree_path(cfg.repo_path)
    ensure_worktree(cfg.repo_path, worktree_path=queue_wt, branch=cfg.queue_branch)
    try:
        clone = tc_mod.ensure_clone(info, workspace_root=cfg.workspace_root)
    except tc_mod.TargetUnreachable as exc:
        raise _ClaimError(f"target unreachable: {exc}") from exc
    event_log = open_log(_queue_repo_root(cfg))
    try:
        moved = move_inbox_to_current(
            cfg,
            pbi,
            event_log=event_log,
            now=datetime.now(tz=UTC),
        )
    finally:
        event_log.close()
    branch = _feature_branch_name(moved)
    work_wt = work_worktree_path(clone.clone_root, moved.id)
    ensure_worktree(
        clone.clone_root,
        worktree_path=work_wt,
        branch=branch,
        create_branch_from=f"origin/{cfg.main_branch}",
    )
    return replace(
        moved,
        target_repo=target_url,
        target_info=info,
        work_worktree=work_wt,
    )


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

    Increments the attempt counter ONLY when the outcome is ``stuck`` or
    ``error`` (i.e. a genuine failed iteration). ``partial`` outcomes
    represent legitimate multi-step progress and do NOT count against
    the max-attempts budget — otherwise long plans (many sub-tasks
    spread across iterations) would always hit the wall. If the
    increment pushes the counter past the configured maximum, the PBI
    is moved to ``blocked/`` and a synthetic ``error`` outcome is
    returned to mirror the AttemptsExceeded path.

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
        # --- Spawn Claude ------------------------------------------------
        if cfg.use_worktrees:
            pbi_dir_in_queue = queue_worktree_path(cfg.repo_path) / ".ralph" / "current" / pbi.id
            # ``cwd`` falls back to ``pbi.work_worktree`` inside
            # ``spawn_claude_p`` (populated by ``_claim_pbi`` from the
            # target clone), so no explicit cwd kwarg is needed here.
            outcome = spawn_claude_p(
                cfg,
                pbi,
                pbi_dir=pbi_dir_in_queue,
            )
        else:
            outcome = spawn_claude_p(cfg, pbi)
        log.info("PBI %s outcome=%s exit=%d", pbi.id, outcome.kind, outcome.exit_code)

        # --- Plan 9: bump attempt counter ONLY on failure outcomes -------
        # `partial` outcomes are legitimate multi-step progress and don't
        # count toward the failure budget. Only stuck / error do.
        if outcome.kind in ("stuck", "error"):
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
                # equivalent comment in the pr_created path below.
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

        if outcome.kind == "pr_created":
            touched = git_ops.diff_names(cfg.repo_path, cfg.main_branch, _feature_branch_name(pbi))
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
            return outcome, IterationResult(
                outcome="ran_pr_created", pbi_id=pbi.id, pr_url=outcome.pr_url
            )

        if outcome.kind == "stuck":
            # --- Plan 9 Layer 1: STUCK.md detection ----------------------
            stuck_outcome = handle_stuck(
                repo=_queue_repo_root(cfg),
                pbi_dir=pbi.path,
                now=datetime.now(tz=UTC),
                event_log=event_log,
            )
            if stuck_outcome is not None:
                event_log.append(stuck_outcome.event)
                log.info("PBI %s stuck: %s", pbi.id, stuck_outcome.reason)
                _cleanup_work_worktree(cfg, pbi)
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
        # FilesystemQueueSource doesn't populate ``target_repo`` /
        # ``work_worktree`` on the PBI dataclass (it consumes the on-disk
        # schema directly without the multi-target runtime fields).
        # Populate both here so:
        #   * ``_run_ralph``'s terminal-outcome cleanup can re-derive the
        #     target clone_root without depending on ``pbi.path`` (which
        #     the move_*_to_* operations invalidate before cleanup runs);
        #   * ``spawn_claude_p`` uses the per-PBI work worktree inside the
        #     target clone as cwd for resumed PBIs, NOT ``cfg.repo_path``
        #     (which is ralph's own repo and would corrupt the wrong
        #     repository for every iteration after the first).
        from dataclasses import replace as _replace

        from ralph_executor.url_utils import parse_target_repo

        try:
            target_url = _read_target_repo_from_pbi(current)
        except _ClaimError:
            target_url = ""
        # Parse the URL outside the worktree-mode guard so non-worktree
        # mode resumed PBIs also get target_info populated — without it
        # ``spawn_claude_p``'s ``GH_OWNER`` injection (guarded by
        # ``pbi.target_info is not None``) silently no-ops for every
        # iteration after the first, and any ``gh`` / ``pr-github`` call
        # the spawned Claude makes uses the wrong (or absent) owner.
        # TargetRepoInfo is imported into this module's TYPE_CHECKING
        # block; ``None`` is annotation enough at runtime.
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
        if cfg.use_worktrees and info is not None:
            try:
                from ralph_executor.target_clone import ensure_clone

                clone = ensure_clone(info, workspace_root=cfg.workspace_root)
                work_wt = work_worktree_path(clone.clone_root, current.id)
            except Exception:
                log.warning(
                    "iterate_once: could not resolve work worktree for resumed PBI %s; "
                    "spawn_claude_p will fall back to cfg.repo_path",
                    current.id,
                    exc_info=True,
                )
        current = _replace(
            current,
            target_repo=target_url,
            target_info=info,
            work_worktree=work_wt,
        )
        # Current occupied → run Ralph on it (attempt counter + spawn).
        # ``_run_ralph`` reaches into ``move_current_to_pending_pr`` /
        # ``handle_stuck`` on terminal outcomes, both of which call
        # ``push_with_rebase`` via ``movements._move``. A concurrent
        # writer on ``cfg.queue_branch`` can make that push raise
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
        # Restore queue branch so .ralph/ is visible on disk after the call.
        _ensure_on_queue_branch(cfg)
        # Persist any HISTORY.md / STUCK.md / PLAN.md edits Claude wrote
        # inside the PBI dir. The move_current_to_* paths handle their
        # own commits via git mv, but partial/error outcomes leave the
        # PBI in current/ with dirty files that would otherwise be lost.
        event_log = open_log(_queue_repo_root(cfg))
        try:
            _persist_iteration_writes(
                cfg,
                current.id,
                event_log=event_log,
                now=datetime.now(tz=UTC),
            )
        except PushRebaseConflict as exc:
            # Concurrent writer advanced the queue branch in a way that
            # conflicts with the iteration's persist commit. The local
            # commit was abandoned (rebase --abort), the on-disk writes
            # remain in the queue worktree, and the next iteration will
            # re-stage them. Log a WARNING and surface the outcome so
            # operator dashboards see it — the loop must NOT crash.
            log.warning(
                "iterate_once: push conflict on %s (paths: %s); "
                "skipping this iteration's persist, loop will retry next round",
                cfg.queue_branch,
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
                meta_bug_path=_queue_repo_root(cfg) / ".ralph" / "blocked",
                sentinel_path=_queue_repo_root(cfg) / ".ralph" / "state" / "halted",
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
                meta_bug_path=_queue_repo_root(cfg) / ".ralph" / "blocked",
                sentinel_path=_queue_repo_root(cfg) / ".ralph" / "state" / "halted",
            )
        return IterationResult(outcome="idle", pbi_id=None)

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
    except _ClaimError as exc:
        # Multi-target prelude failure (missing/invalid target_repo,
        # unsupported host, ``TargetUnreachable``). The PBI is still in
        # inbox/; demote it to blocked/ with the reason in HISTORY.md so
        # the operator can triage. Skip the cycle detector — the move is
        # final and the failure is not a retry signal.
        log.warning("claim failed for PBI %s: %s; moving to blocked/", picked.id, exc)
        _move_to_blocked_with_reason(cfg, picked, reason=str(exc))
        return IterationResult(outcome="claim_failed", pbi_id=picked.id)
    # _claim_pbi already returns to queue branch; re-assert for clarity.
    _ensure_on_queue_branch(cfg)
    if _check_cycle_detector(cfg, source):
        raise HaltedError(
            meta_bug_id="(see latest META-cycle-* in .ralph/blocked/)",
            meta_bug_path=_queue_repo_root(cfg) / ".ralph" / "blocked",
            sentinel_path=_queue_repo_root(cfg) / ".ralph" / "state" / "halted",
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
