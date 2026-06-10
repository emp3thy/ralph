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

import atexit
import contextlib
import logging
import os
import shutil
import socket
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
from ralph_executor.lockfile import WorkspaceLockfile
from ralph_executor.prompt_composer import PromptComposeError
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue.movements import (
    UncommittedSource,
    move_current_to_blocked,
    move_current_to_pending_pr,
    move_inbox_to_blocked,
    move_inbox_to_current,
)
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
    remove_worktree,
    work_worktree_path,
)

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
            # `.ralph/` lives in the queue clone at
            # ``<workspace_root>/queue/`` — same path every read/write in
            # this module routes through ``_queue_repo_root``.
            queue_root=_queue_repo_root(cfg) / ".ralph",
            ado_pr_scripts_path=scripts_path,
            config=sweep_cfg,
            # The queue clone is the single repo the sweep reads/writes
            # (every PR scanned belongs to a target reachable from the
            # queue's pending-pr index); label the sweep context with
            # its directory name — by convention ``queue`` under
            # ``workspace_root``.
            repo_name=_queue_repo_root(cfg).name,
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


def _warn_project_toml_in_target_clone(clone_root: Path) -> None:
    """Emit a WARNING if ``<clone>/.ralph/config.toml`` exists.

    Called after every successful ``ensure_clone`` so a freshly cloned
    target gets exactly one WARNING per iteration that touches it. The
    file is NOT loaded — project TOML is gone after this refactor. The
    WARNING names the path so the operator can move the settings to
    ``~/.ralph/config.toml`` and delete the file.

    Detection failure (permission denied, transient I/O error reading
    the clone) is suppressed at DEBUG so a flaky filesystem never
    crashes the iteration.
    """
    cfg_file = clone_root / ".ralph" / "config.toml"
    try:
        present = cfg_file.is_file()
    except OSError as exc:
        log.debug("project-TOML check failed for %s: %s", cfg_file, exc)
        return
    if present:
        log.warning(
            "project TOML at %s is not supported -- ignored. "
            "Move settings to ~/.ralph/config.toml.",
            cfg_file,
        )


def _pr_skill_scripts_path(cfg: ExecutorConfig) -> Path:
    """Return the on-disk scripts directory for the configured PR skill.

    The scripts live in the ralph executor source tree (``skills/pr-github/``
    or ``skills/ado-pr/``), NOT in any target / queue clone. We resolve
    relative to the ``ralph_executor`` package location so the lookup is
    independent of CWD and of any operator-supplied repo path.

    ``cfg.git_host == "github"`` → ``<ralph-src>/skills/pr-github/scripts/``.
    ``cfg.git_host == "ado"``    → ``<ralph-src>/skills/ado-pr/scripts/``.
    Empty / unknown host: prefer ``pr-github`` if it exists, else fall
    back to ``ado-pr`` (existence is verified by the caller).
    """
    import ralph_executor

    ralph_src = Path(ralph_executor.__file__).resolve().parent.parent
    host = (cfg.git_host or "").strip().lower()
    if host == "github":
        return ralph_src / "skills" / "pr-github" / "scripts"
    if host == "ado":
        return ralph_src / "skills" / "ado-pr" / "scripts"
    pr_github = ralph_src / "skills" / "pr-github" / "scripts"
    if pr_github.is_dir():
        return pr_github
    return ralph_src / "skills" / "ado-pr" / "scripts"


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
    halt_and_acknowledge(
        repo=_queue_repo_root(cfg),
        signals=signals,
        now=now,
        tripped_by_instance=cfg.instance_id,
    )
    return True


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
    iterations); there is no process-wide ``repo_path`` to fall back
    onto after KILL-RALPH-HOME, so the deterministic clone root from
    ``ensure_clone`` is the only honest source.

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
            from ralph_executor.url_utils import parse_target_repo

            info = parse_target_repo(pbi.target_repo)
        except ValueError:
            log.warning(
                "failed to parse target_repo for PBI %s; orphan work worktree may remain",
                pbi.id,
                exc_info=True,
            )
            return
        # Compute owning_repo deterministically (workspace_root + owner +
        # name) rather than via a fresh ``ensure_clone`` — a transient
        # fetch failure shouldn't leave the worktree behind, and the path
        # itself is fully determined by the URL components. Tests
        # monkeypatch ``ensure_clone`` to return a divergent clone_root
        # (fake_repo), so honour that override when present by calling
        # ensure_clone and using its return value if it succeeds.
        owning_repo = cfg.workspace_root / "clones" / info.owner / info.name
        try:
            from ralph_executor.target_clone import ensure_clone

            clone = ensure_clone(info, workspace_root=cfg.workspace_root)
            owning_repo = clone.clone_root
        except Exception:
            log.warning(
                "ensure_clone failed for PBI %s; using deterministic clone path",
                pbi.id,
                exc_info=True,
            )
        if not (owning_repo / ".git").exists():
            log.warning(
                "owning repo %s has no .git; cannot remove worktree for PBI %s",
                owning_repo,
                pbi.id,
            )
            return
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


def _claim_pbi(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Move PBI into current/ and create the per-PBI feature branch.

    Multi-target prelude:
      0. ``_read_target_repo_from_pbi`` — read ``target_repo`` from the
         PBI entry-file frontmatter.
      0b. ``parse_target_repo`` — split into host/owner/name; ValueError
          surfaces as ``_ClaimError('invalid target_repo URL: …')``.
      0c. Host gate — only ``github.com`` is supported on this PBI;
          other hosts raise ``_ClaimError('unsupported host …')``.

    Claim body (always runs ``_claim_pbi_worktree``):
      1. ``target_clone.ensure_clone`` — clone-once / fetch-each-iter the
         target repo into ``<workspace_root>/clones/<owner>-<name>/``;
         ``TargetUnreachable`` maps to ``_ClaimError('target unreachable: …')``.
      2. ``move_inbox_to_current`` operates against the queue clone at
         ``<workspace_root>/queue/`` (movements._move resolves the path
         via ``_queue_repo_root``).
      3. Materialise the per-PBI work worktree INSIDE the target clone at
         ``<clone_root>/.ralph-work/<PBI-ID>/`` on ``ralph/<PBI-ID>``,
         forked from the clone's ``origin/<main_branch>`` when the branch
         is new.

    The Stage-A single-checkout legacy branch-dance is gone — the queue
    is its own clone now, not a branch on the primary checkout, and
    ``load_config`` rejects ``use_worktrees=False`` outright.
    """
    from ralph_executor.url_utils import parse_target_repo

    target_url = _read_target_repo_from_pbi(pbi)
    try:
        info = parse_target_repo(target_url)
    except ValueError as exc:
        raise _ClaimError(f"invalid target_repo URL: {exc}") from exc
    if info.host != "github.com":
        raise _ClaimError(f"unsupported host {info.host!r} (only github.com is supported)")

    return _claim_pbi_worktree(cfg, pbi, target_url=target_url, info=info)


def _claim_pbi_worktree(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    target_url: str,
    info: TargetRepoInfo,
) -> PBI:
    """Worktree-mode implementation of ``_claim_pbi``.

    The primary checkout's branch is never touched. ``.ralph/`` reads
    and writes go through the queue clone at ``<workspace_root>/queue/``
    (materialised earlier by ``_pull_queue`` → ``ensure_queue_clone``);
    code edits go through the per-PBI work worktree, which lives INSIDE
    the target's clone (``<workspace_root>/clones/<owner>/<name>/.ralph-work/<PBI-ID>/``).
    """
    from dataclasses import replace

    from ralph_executor import target_clone as tc_mod

    try:
        clone = tc_mod.ensure_clone(info, workspace_root=cfg.workspace_root)
    except tc_mod.TargetUnreachable as exc:
        raise _ClaimError(f"target unreachable: {exc}") from exc
    _warn_project_toml_in_target_clone(clone.clone_root)
    # Pre-flight the base ref BEFORE the inbox -> current move. An empty
    # target repo (no commits, no main branch) would otherwise crash
    # ``ensure_worktree`` below with a raw ``GitCommandError`` after the
    # PBI has already been promoted to ``current/`` — taking the loop
    # down AND stranding the PBI with no worktree. Routing this through
    # ``_ClaimError`` here keeps the claim atomic and lets ``iterate_once``
    # demote the PBI inbox -> blocked with the reason in HISTORY.md.
    if not git_ops.is_branch_remote(clone.clone_root, cfg.main_branch):
        raise _ClaimError(
            f"target repo {target_url} has no origin/{cfg.main_branch} "
            f"(target may be empty or the main branch is misnamed)"
        )
    event_log = open_log(_queue_repo_root(cfg))
    try:
        moved = move_inbox_to_current(
            cfg,
            pbi,
            event_log=event_log,
            now=datetime.now(tz=UTC),
            instance_id=cfg.instance_id,
            hostname=socket.gethostname(),
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

    Spawn cwd: Claude runs against the per-PBI work worktree inside the
    target clone (populated by ``_claim_pbi`` and threaded through on
    ``pbi.work_worktree``). The ``pbi_dir`` argument points at the PBI's
    directory inside the queue clone (``<workspace_root>/queue/.ralph/
    current/<PBI-ID>/``) so Claude can read PROMPT.md / PBI.md /
    HISTORY.md and write STUCK.md / HISTORY.md without leaving the
    target checkout.

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
        # ``cwd`` falls back to ``pbi.work_worktree`` inside
        # ``spawn_claude_p`` (populated by ``_claim_pbi`` from the target
        # clone), so no explicit cwd kwarg is needed here. ``pbi_dir``
        # points at the PBI's directory inside the queue clone so Claude
        # can read PROMPT.md / HISTORY.md / PBI.md and write STUCK.md /
        # HISTORY.md without leaving its target-clone working tree.
        pbi_dir_in_queue = _queue_repo_root(cfg) / ".ralph" / "current" / pbi.id
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
                clone_root = (
                    cfg.workspace_root / "clones" / pbi.target_info.owner / pbi.target_info.name
                )
                if not clone_root.is_dir():
                    log.warning(
                        "PBI %s pr_created but target clone %s is missing; touched_files=[]",
                        pbi.id,
                        clone_root,
                    )
                else:
                    touched = git_ops.diff_names(
                        clone_root, cfg.main_branch, _feature_branch_name(pbi)
                    )
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
                cfg=cfg,
                pbi=pbi,
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


def _iterate_once_inner(cfg: ExecutorConfig) -> IterationResult:
    """Run a single iteration of the loop and return the outcome.

    Idempotent in the no-work case: if current/ is empty and the inbox
    is empty, the iteration is a no-op and returns ``IterationResult("idle")``.

    ``.ralph/`` lives in the queue clone at ``<workspace_root>/queue/``;
    callers inspect it via ``_queue_repo_root(cfg)`` (the primary
    checkout is never branch-swapped — that single-checkout model is
    gone).

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
                    return IterationResult(outcome="claim_failed", pbi_id=current.id)
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
        event_log = open_log(_queue_repo_root(cfg))
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
