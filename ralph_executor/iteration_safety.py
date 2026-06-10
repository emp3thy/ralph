"""Per-iteration safety wiring: sweep + cycle detector.

Both entry points read from the queue clone (the canonical ``.ralph/``
tree, resolved via ``queue_git.queue_repo_root``) and emit events to
the cycle-detector log. They are module-level callables so tests can
monkeypatch them without dependency injection; the loop module keeps
aliased re-exports (``_run_sweep`` / ``_check_cycle_detector`` /
``_pr_skill_scripts_path``) so loop-level patch targets stay valid.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue_git import queue_repo_root
from ralph_executor.safety import evaluate_all, halt_and_acknowledge, open_log

log = logging.getLogger(__name__)


def pr_skill_scripts_path(cfg: ExecutorConfig) -> Path:
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


def run_sweep(
    cfg: ExecutorConfig,
    source: FilesystemQueueSource,
    *,
    pr_skill_scripts_path: Callable[[ExecutorConfig], Path] | None = None,
) -> None:
    """Drive one sweep over ``.ralph/pending-pr/``.

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

    ``pr_skill_scripts_path`` is the resolver for the PR-skill scripts
    directory. It is an injectable parameter so the loop module can pass
    its own re-exported global — preserving monkeypatches at
    ``ralph_executor.iteration._pr_skill_scripts_path`` — and tests can pass
    a stub directly. When ``None`` it late-binds to this module's
    ``pr_skill_scripts_path`` at call time (a def-time default would
    freeze the original function object and defeat monkeypatching).
    """
    del source  # sweep walks the filesystem directly
    if pr_skill_scripts_path is None:
        # Late-bind through sys.modules so monkeypatches at
        # ``ralph_executor.iteration_safety.pr_skill_scripts_path``
        # are honoured.
        from ralph_executor import iteration_safety as _self

        pr_skill_scripts_path = _self.pr_skill_scripts_path
    if not cfg.bot_author_email:
        log.warning(
            "sweep: bot_author_email is not set (TOML key 'bot_author_email' "
            "or env RALPH_ADO_AUTHOR_EMAIL); skipping sweep this iteration"
        )
        return

    scripts_path = pr_skill_scripts_path(cfg)
    if not scripts_path.is_dir():
        log.warning(
            "sweep: PR-skill scripts directory not found at %s; skipping",
            scripts_path,
        )
        return

    from ralph_executor.sweep import run as sweep_run
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
    event_log = open_log(queue_repo_root(cfg))
    try:
        sweep_ctx = SweepContext(
            # ``.ralph/`` lives in the queue clone — same path every
            # read/write in this module routes through ``queue_repo_root``.
            queue_root=queue_repo_root(cfg) / ".ralph",
            ado_pr_scripts_path=scripts_path,
            config=sweep_cfg,
            # The queue clone is the single repo the sweep reads/writes
            # (every PR scanned belongs to a target reachable from the
            # queue's pending-pr index); label the sweep context with
            # its directory name.
            repo_name=queue_repo_root(cfg).name,
            event_log=event_log,
        )
        result = sweep_run(ctx=sweep_ctx)
    finally:
        # Wrap close() so a failure here (e.g. sqlite flush error) does
        # not mask an exception from sweep_run — losing the real cause
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


def check_cycle_detector(cfg: ExecutorConfig, source: FilesystemQueueSource) -> bool:
    """Evaluate all cycle-detector rules against the recent event log.

    Returns ``True`` if any signal tripped (and the loop should halt after
    this call completes the META-BUG + sentinel write). Returns ``False``
    when no signals fire.

    The function is kept as a module-level callable so tests can monkeypatch
    it without dependency-injection (reconciliation #9).
    """
    now = datetime.now(tz=UTC)
    event_log = open_log(queue_repo_root(cfg))
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
        repo=queue_repo_root(cfg),
        signals=signals,
        now=now,
        tripped_by_instance=cfg.instance_id,
    )
    return True
