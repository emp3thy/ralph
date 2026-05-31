"""Command-line entry point for ``ralph-executor``.

Usage::

    ralph-executor [--watch] [--once] [--iterations N] [--log-level LEVEL]
                   [--queue-repo URL] [--queue-branch BRANCH]
    ralph-executor health --ready
    ralph-executor health --live
    ralph-executor doctor [--json]

* ``--watch``          -- daemon mode: run forever, sleep on idle. Without
                          this flag the loop drains to idle and exits 0
                          (the intended default for unattended pod /
                          container deployments). Mutually exclusive with
                          ``--once`` / ``--iterations``.
* ``--once``           -- run a single iteration and exit. Alias for
                          ``--iterations 1``. Kept for backward compatibility.
* ``--iterations N``   -- run exactly N iterations and exit.
* ``--queue-repo URL`` -- override the queue_repo TOML value for this run.
* ``--queue-branch B`` -- override the queue_branch TOML value for this run.
* ``--log-level``      -- override ``RALPH_LOG_LEVEL`` for this run.

The executor is queue-driven: each iteration reads the next PBI's
``target_repo`` from frontmatter and clones (or reuses) it under
``<workspace_root>/clones/<owner>/<name>/``. There is no operator-facing
flag for picking the per-iteration target; the queue PBI is the single
source of truth.

Startup sequence (BEFORE the iteration loop):

  1. Parse argv.
  2. ``load_config()`` -- reads RALPH_* env vars + ``~/.ralph/config.toml``.
  3. Apply CLI overrides to the loaded config.
  4. Configure logging.
  5. ``prepare_host_environment()`` -- reads RALPH_GIT_HOST, verifies
     auth env vars, stages the chosen skill directories. Fails fast
     (exit 2) with a clear error if the environment is malformed.
  6. Enter the iteration loop.

``RALPH_RUN_ONCE`` environment variable: if set to a truthy value
(``1``, ``true``, ``yes``) and ``--iterations`` is not supplied,
behaves as though ``--once`` was passed (single-iteration mode).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from ralph_executor.config import (
    ConfigError,
    ExecutorConfig,
    load_config,
)
from ralph_executor.host_select import (
    HostSelectionError,
    prepare_host_environment,
)
from ralph_executor.loop import _pr_skill_scripts_path, iterate_once, run_loop
from ralph_executor.sweep.reconcile import reconcile_all, reconcile_stale_current_all
from ralph_executor.sweep.runner import SweepConfig, SweepContext
from ralph_executor.sweep.types import CurrentReconcileReport, ReconcileReport

_VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

log = logging.getLogger(__name__)


def _truthy(value: str) -> bool:
    """Return True if the string value looks like a boolean true."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ralph-executor",
        description=(
            "Run the Ralph per-repo autonomous coding loop. By default the "
            "loop drains to idle and exits 0 (intended for unattended pod / "
            "container runs); pass --watch for the legacy daemon behaviour."
        ),
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Daemon mode: run forever, sleep on idle. Without this flag the "
            "loop exits after idle_exit_threshold consecutive idle iterations."
            " Mutually exclusive with --once / --iterations."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single iteration and exit. Alias for --iterations 1.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        metavar="N",
        help="Run exactly N iterations and exit.",
    )
    # --repo and --workspace removed: the queue PBI's target_repo
    # frontmatter is the only input that decides which repo the executor
    # works on per iteration. The operator does not pre-clone target
    # repos; the loop materialises every target under workspace_root.
    parser.add_argument(
        "--log-level",
        choices=_VALID_LOG_LEVELS,
        help="Override RALPH_LOG_LEVEL for this run.",
    )
    parser.add_argument(
        "--queue-repo",
        metavar="URL",
        help=(
            "Override the queue_repo TOML value for this run (HTTPS URL of the queue repository)."
        ),
    )
    parser.add_argument(
        "--queue-branch",
        metavar="BRANCH",
        help=(
            "Override the queue_branch TOML value for this run "
            "(branch name on the queue repo; default: ralph-queue)."
        ),
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    # ``ralph-executor health``
    health_parser = subparsers.add_parser(
        "health",
        help="Liveness / readiness probe (returns 0 if healthy).",
    )
    health_group = health_parser.add_mutually_exclusive_group(required=True)
    health_group.add_argument(
        "--ready",
        action="store_true",
        help="Readiness probe: return 0 if executor is ready.",
    )
    health_group.add_argument(
        "--live",
        action="store_true",
        help="Liveness probe: return 0 if executor process is alive.",
    )

    # ``ralph-executor doctor``
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run environment diagnostics.",
    )
    doctor_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit diagnostics as JSON.",
    )

    # ``ralph-executor init``
    init_parser = subparsers.add_parser(
        "init",
        help="Per-machine setup: pick workspace_root, write ~/.ralph/config.toml.",
    )
    init_parser.add_argument(
        "--yes",
        action="store_true",
        help="Non-interactive: accept the OS default for workspace_root.",
    )

    # ``ralph-executor scaffold`` removed (KILL-RALPH-HOME T10). The
    # queue is now bootstrapped externally via
    # ``scripts/setup_ralph_queue_github.py``; the executor no longer
    # offers a per-repo subcommand.

    # ``ralph-executor migrate-queue``
    migrate_parser = subparsers.add_parser(
        "migrate-queue",
        help=(
            "One-shot: bootstrap a new queue repo from an existing .ralph/ "
            "tree. Source must contain .ralph/inbox/; target must be empty."
        ),
    )
    migrate_parser.add_argument(
        "--source",
        required=True,
        type=Path,
        help="Path to the existing queue worktree (parent of .ralph/).",
    )
    migrate_parser.add_argument(
        "--target",
        required=True,
        help="HTTPS (or file://) URL of the empty new queue repo.",
    )

    # ``ralph-executor reconcile``
    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help=(
            "Reconcile orphan pending-pr/ directories (those without "
            "PR-LINK.md) by looking up the PR via the host API and moving "
            "them to done/blocked/inbox based on PR state."
        ),
    )
    # ``--repo`` / ``--workspace`` removed from reconcile (T4 of
    # KILL-RALPH-HOME). The reconcile path reads ``.ralph/`` from
    # ``<workspace_root>/queue/``; the queue-clone root is fixed by
    # ``workspace_root`` in ``~/.ralph/config.toml`` and there is no
    # operator-facing knob for overriding the target repo per run.
    reconcile_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the actions that would be taken without moving any files.",
    )

    return parser


def _configure_logging(level: int) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        force=True,
    )
    logging.getLogger("ralph_executor").setLevel(level)


def _apply_overrides(cfg: ExecutorConfig, args: argparse.Namespace) -> ExecutorConfig:
    log_level: int = cfg.log_level
    queue_repo: str = cfg.queue_repo
    queue_branch: str = cfg.queue_branch
    watch_mode: bool = cfg.watch_mode
    changed = False
    if args.log_level:
        log_level = int(logging.getLevelName(args.log_level))
        changed = True
    if getattr(args, "queue_repo", None):
        # Mirror load_config's parse_target_repo validation so the CLI
        # override surfaces a clean ConfigError on a malformed URL
        # rather than crashing inside ensure_queue_clone with an
        # unhandled QueueCloneError later in iterate_once.
        from ralph_executor.url_utils import parse_target_repo

        try:
            parse_target_repo(args.queue_repo)
        except ValueError as exc:
            raise ConfigError(f"--queue-repo: {exc}") from exc
        queue_repo = args.queue_repo
        changed = True
    if getattr(args, "queue_branch", None) is not None:
        # `is not None` (not truthiness): `--queue-branch ""` must reach the
        # validator below so the empty-string ConfigError surfaces instead of
        # silently no-op'ing.
        stripped = args.queue_branch.strip()
        if not stripped:
            raise ConfigError("--queue-branch must be a non-empty branch name")
        if stripped == "HEAD" or stripped.startswith("refs/heads/"):
            raise ConfigError(
                f"--queue-branch must be a plain branch name (got {args.queue_branch!r})"
            )
        queue_branch = stripped
        changed = True
    # --watch overrides any TOML / env value; absence of the flag does NOT
    # disable a TOML watch_mode=true (operators who pinned daemon mode in
    # config keep it).
    if getattr(args, "watch", False):
        watch_mode = True
        changed = True
    if not changed:
        return cfg
    return dataclasses.replace(
        cfg,
        log_level=log_level,
        queue_repo=queue_repo,
        queue_branch=queue_branch,
        watch_mode=watch_mode,
    )


def _resolve_iteration_count(args: argparse.Namespace) -> int | None:
    """Return the number of iterations to run, or None for unlimited.

    Precedence (highest to lowest):
      1. ``--iterations N`` explicit CLI flag.
      2. ``--once`` CLI flag (maps to 1).
      3. ``RALPH_RUN_ONCE`` env var (maps to 1 when truthy).
      4. None (run until interrupted).
    """
    if args.iterations is not None:
        return int(args.iterations)
    if args.once:
        return 1
    run_once_env = os.environ.get("RALPH_RUN_ONCE", "")
    if run_once_env and _truthy(run_once_env):
        return 1
    return None


def _cmd_health(args: argparse.Namespace) -> int:
    """Handle ``ralph-executor health --ready`` / ``--live``.

    v1 stub: both probes return 0. Plan 11 (ralph-doctor) may replace
    the ready probe with a deeper environment check.
    """
    if args.ready:
        log.debug("readiness probe: ok")
    elif args.live:
        log.debug("liveness probe: ok")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Handle ``ralph-executor doctor [--json]``.

    Shells out to the ralph-doctor skill if installed. If the skill is
    not yet installed (Plan 11 not landed), emits a stub JSON response
    and exits 0 so callers don't break.
    """
    try:
        from ralph_executor.subprocess_utils import run_text

        result = run_text(
            ["ralph-doctor", "--json"] if args.json_output else ["ralph-doctor"],
            capture_output=True,
        )
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        return result.returncode
    except FileNotFoundError:
        # ralph-doctor not yet installed (Plan 11 not landed).
        if args.json_output:
            print(json.dumps({"status": "doctor skill not yet installed"}))
        else:
            print("doctor: ralph-doctor skill not yet installed (Plan 11)")
        return 0


def _cmd_reconcile(args: argparse.Namespace) -> int:
    """Handler for ``ralph-executor reconcile [--dry-run]``.

    Loads the executor config, resolves the PR-skill scripts directory
    for the configured git host, builds a ``SweepContext`` rooted at
    ``<workspace_root>/queue/.ralph`` and delegates to ``reconcile_all``.
    Prints a one-line-per-orphan summary table.

    Returns 0 on success, 2 on config or environment errors.
    """
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    ralph_email = os.environ.get("RALPH_ADO_AUTHOR_EMAIL", "").strip() or "reconcile@ralph.local"
    scripts_path = _pr_skill_scripts_path(cfg)
    if not scripts_path.is_dir():
        print(
            f"error: PR-skill scripts directory not found at {scripts_path}",
            file=sys.stderr,
        )
        return 2

    from datetime import UTC, datetime, timedelta

    from ralph_executor.loop import _queue_repo_root

    ctx = SweepContext(
        # Worktree-mode awareness: ``.ralph/`` lives in the queue clone at
        # ``<workspace_root>/queue/`` (not in any per-target checkout).
        queue_root=_queue_repo_root(cfg) / ".ralph",
        ado_pr_scripts_path=scripts_path,
        config=SweepConfig(
            ralph_author_email=ralph_email,
            max_attempts=cfg.max_attempts,
            stale_threshold=timedelta(days=3),
            now=datetime.now(tz=UTC),
        ),
        # Sweep is queue-only: label the context with the queue clone's
        # directory name (always ``queue`` under ``workspace_root``).
        repo_name=_queue_repo_root(cfg).name,
    )

    report = reconcile_all(ctx, dry_run=args.dry_run)
    _print_reconcile_report(report, dry_run=args.dry_run)

    current_report = reconcile_stale_current_all(ctx, dry_run=args.dry_run)
    _print_current_reconcile_report(current_report, dry_run=args.dry_run)
    return 0


def _print_reconcile_report(report: ReconcileReport, *, dry_run: bool) -> None:
    """Print a one-line-per-orphan summary table to stdout."""
    prefix = "would: " if dry_run else ""
    if not report.actions and not report.errors:
        print("reconcile: no orphans found in pending-pr/")
        return
    print(f"{'PBI-ID':<40} {'Action':<20}")
    print("-" * 60)
    for pbi_id, action in sorted(report.actions.items()):
        print(f"{pbi_id:<40} {prefix}{action.value}")
    for pbi_id, err in sorted(report.errors.items()):
        print(f"{pbi_id:<40} ERROR: {err}")
    n_moves = sum(1 for a in report.actions.values() if a.name.startswith("MOVED_"))
    n_stays = sum(1 for a in report.actions.values() if a.name.startswith("KEEP_"))
    n_err = len(report.errors)
    total = len(report.actions) + n_err
    print(f"\n{total} orphans attempted: {n_moves} moved, {n_stays} stays, {n_err} errors.")


def _print_current_reconcile_report(
    report: CurrentReconcileReport,
    *,
    dry_run: bool,
) -> None:
    """Print a one-line-per-entry summary for the current/ reconciliation pass."""
    prefix = "would: " if dry_run else ""
    if not report.actions and not report.errors:
        print("\nreconcile-current: no current/ entries found")
        return
    print()
    print(f"{'PBI-ID':<40} {'Current/ action':<28}")
    print("-" * 70)
    for pbi_id, action in sorted(report.actions.items()):
        print(f"{pbi_id:<40} {prefix}{action.value}")
    for pbi_id, err in sorted(report.errors.items()):
        print(f"{pbi_id:<40} ERROR: {err}")
    n_del = sum(1 for a in report.actions.values() if a.name.startswith("DELETED_"))
    n_keep = sum(1 for a in report.actions.values() if a.name.startswith("KEEP_"))
    n_err = len(report.errors)
    total = len(report.actions) + n_err
    print(f"\n{total} current/ entries inspected: {n_del} deleted, {n_keep} kept, {n_err} errors.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    # --- dispatch subcommands that don't need load_config ---
    if args.subcommand == "health":
        return _cmd_health(args)
    if args.subcommand == "doctor":
        return _cmd_doctor(args)
    if args.subcommand == "init":
        from ralph_executor.setup_cmds import cmd_init

        try:
            return cmd_init(assume_yes=args.yes)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.subcommand == "reconcile":
        return _cmd_reconcile(args)
    if args.subcommand == "migrate-queue":
        from ralph_executor.migrate_queue import MigrateQueueError
        from ralph_executor.migrate_queue import main as migrate_main

        try:
            return migrate_main(["--source", str(args.source), "--target", args.target])
        except MigrateQueueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    # --- default command: run the executor loop ---
    # --watch is mutually exclusive with the explicit-iteration-count flags;
    # the two surface contradictory intents (drain-forever vs run-N-then-exit).
    # Argparse doesn't enforce the mutex (the flags live outside any mutex
    # group so the existing subcommands stay unchanged); check here so the
    # error fires before any config load.
    if getattr(args, "watch", False) and (args.once or args.iterations is not None):
        print(
            "error: --watch is mutually exclusive with --once / --iterations",
            file=sys.stderr,
        )
        return 2
    try:
        cfg = load_config()
        cfg = _apply_overrides(cfg, args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _configure_logging(cfg.log_level)

    log.info(
        "ralph-executor starting (workspace_root=%s queue_repo=%s queue_branch=%s main=%s)",
        cfg.workspace_root,
        cfg.queue_repo,
        cfg.queue_branch,
        cfg.main_branch,
    )

    # Sync TOML-sourced project identifiers / alerting into the process
    # env BEFORE host_select runs. host_select.verify_auth_env and the
    # pr-<host> skill read these by name from the environment; this
    # single bridge lets operators write them once in
    # `.ralph/config.toml` instead of exporting per shell.
    # Skips empty values so existing env values aren't clobbered with "".
    for name, value in (
        ("GH_OWNER", cfg.gh_owner),
        ("ADO_ORG_URL", cfg.ado_org_url),
        ("ADO_PROJECT", cfg.ado_project),
        ("RALPH_HALT_WEBHOOK", cfg.halt_webhook),
    ):
        if value:
            os.environ[name] = value

    # Stage host-specific skills BEFORE any iteration. If this fails,
    # Ralph can't operate against the chosen host -- abort immediately
    # so the operator fixes their env rather than silently running
    # against missing or stale skill directories.
    try:
        host = prepare_host_environment(host_override=cfg.git_host or None)
        log.info("host environment ready: host=%s", host)
    except HostSelectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    iteration_count = _resolve_iteration_count(args)

    try:
        if iteration_count is not None:
            # Run exactly N iterations.
            for i in range(iteration_count):
                result = iterate_once(cfg)
                log.info(
                    "iteration %d/%d outcome=%s pbi=%s",
                    i + 1,
                    iteration_count,
                    result.outcome,
                    result.pbi_id,
                )
                if result.outcome == "halted":
                    log.warning("loop halted -- exiting")
                    return 0
            return 0
        # Run until interrupted.
        for result in run_loop(cfg):
            log.info("iteration outcome=%s pbi=%s", result.outcome, result.pbi_id)
            if result.outcome == "halted":
                log.warning("loop halted -- exiting")
                return 0
        return 0
    except KeyboardInterrupt:
        log.info("interrupted; exiting cleanly")
        return 0
    except Exception:  # noqa: BLE001 -- top-level safety net
        log.exception("unhandled exception in ralph-executor")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
