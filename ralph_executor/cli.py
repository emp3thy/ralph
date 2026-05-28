"""Command-line entry point for ``ralph-executor``.

Usage::

    ralph-executor [--watch] [--once] [--iterations N]
                   [--repo PATH | --workspace NAME] [--log-level LEVEL]
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
* ``--repo PATH``      -- explicit path to the repo Ralph operates on.
* ``--workspace NAME`` -- resolve repo path against ``$RALPH_HOME/NAME``.
                          Mutually exclusive with ``--repo``. Requires
                          ``RALPH_HOME`` to be set. Convention for running
                          multiple ralphs on one machine: keep every
                          ralph's checkout under ``$RALPH_HOME/<name>/``.
* ``--log-level``      -- override ``RALPH_LOG_LEVEL`` for this run.

Repo path resolution (highest → lowest):

  1. ``--repo PATH``
  2. ``--workspace NAME``  →  ``$RALPH_HOME/NAME``
  3. ``RALPH_REPO_PATH`` env var
  4. Current working directory

Startup sequence (BEFORE the iteration loop):

  1. Parse argv.
  2. ``load_config()`` -- reads RALPH_* env vars, validates the repo.
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
    validate_repo_path,
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
    repo_group = parser.add_mutually_exclusive_group()
    repo_group.add_argument(
        "--repo",
        help=("Explicit path to the repo Ralph operates on. Overrides RALPH_REPO_PATH and cwd."),
    )
    repo_group.add_argument(
        "--workspace",
        metavar="NAME",
        help=(
            "Resolve repo path against $RALPH_HOME/NAME. Requires RALPH_HOME "
            "to be set. Convention for running multiple ralphs on one host."
        ),
    )
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
        help="Per-machine setup: pick ralph_home, write ~/.ralph/config.toml.",
    )
    init_parser.add_argument(
        "--ralph-home",
        type=Path,
        metavar="PATH",
        help="Skip the prompt and set ralph_home to PATH.",
    )
    init_parser.add_argument(
        "--yes",
        action="store_true",
        help="Non-interactive: accept the OS default for ralph_home.",
    )

    # ``ralph-executor scaffold``
    scaffold_parser = subparsers.add_parser(
        "scaffold",
        help=(
            "Per-repo setup: create ralph-queue branch with .ralph/ skeleton "
            "(inbox, current, pending-pr, done, blocked) + commented config.toml stub. "
            "Resolves target via the same --repo / --workspace / cwd chain as the loop."
        ),
    )
    # Re-register --repo / --workspace on the subparser so they can appear
    # AFTER the `scaffold` subcommand name (which matches the natural CLI
    # shape `ralph-executor scaffold --repo PATH`). argparse-level mutual
    # exclusion is preserved by the inner group.
    scaffold_repo_group = scaffold_parser.add_mutually_exclusive_group()
    scaffold_repo_group.add_argument(
        "--repo",
        help="Explicit path to the repo to scaffold.",
    )
    scaffold_repo_group.add_argument(
        "--workspace",
        metavar="NAME",
        help="Resolve target against $RALPH_HOME/NAME (or ~/.ralph/config.toml).",
    )
    scaffold_parser.add_argument(
        "--force",
        action="store_true",
        help="Scaffold even if ralph-queue branch already exists.",
    )
    scaffold_parser.add_argument(
        "--no-config-toml",
        dest="with_config_toml",
        action="store_false",
        help="Skip writing the .ralph/config.toml stub.",
    )

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
    reconcile_repo_group = reconcile_parser.add_mutually_exclusive_group()
    reconcile_repo_group.add_argument(
        "--repo",
        help="Explicit path to the repo Ralph operates on.",
    )
    reconcile_repo_group.add_argument(
        "--workspace",
        metavar="NAME",
        help="Resolve repo path against $RALPH_HOME/NAME.",
    )
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


def _scaffold_resolve_target(args: argparse.Namespace) -> Path:
    """Resolve the scaffold target via the same chain the loop uses:
    ``--repo`` > ``--workspace`` > ``$RALPH_REPO_PATH`` > cwd.

    Unlike ``load_config``, the scaffold path is NOT required to be a
    valid git repo yet — ``cmd_scaffold`` will call ``validate_repo_path``
    itself and surface the error consistently with its other errors.
    """
    if args.repo:
        return Path(args.repo).resolve()
    if args.workspace:
        return _resolve_workspace(args.workspace)
    env_value = os.environ.get("RALPH_REPO_PATH", "").strip()
    if env_value:
        return Path(env_value).resolve()
    return Path.cwd().resolve()


def _resolve_workspace(name: str) -> Path:
    """Resolve ``--workspace NAME`` against the ralph_home root.

    Root resolution: ``$RALPH_HOME`` env var if set, else ``ralph_home``
    from ``~/.ralph/config.toml`` (written by ``ralph-executor init``).

    ``NAME`` must be a plain directory name (no path separators, no
    parent-traversal, not absolute). Otherwise ``Path(home) / name``
    would silently escape the root — Python's ``Path / abs`` discards
    the LHS, and ``..`` traversal resolves outside the root.

    Raises ``ConfigError`` if no root can be resolved or if ``name``
    violates the plain-directory-name invariant.
    """
    from ralph_executor.user_config import read_ralph_home, user_config_path

    env_value = os.environ.get("RALPH_HOME", "").strip()
    if env_value:
        home_path = Path(env_value)
    else:
        home_path_or_none = read_ralph_home()
        if home_path_or_none is None:
            raise ConfigError(
                "--workspace needs a ralph_home root. Set $RALPH_HOME, or run "
                f"`ralph-executor init` to write one to {user_config_path()}."
            )
        home_path = home_path_or_none
    name_path = Path(name)
    if name_path.is_absolute() or len(name_path.parts) != 1 or name_path.parts[0] in ("..", "."):
        raise ConfigError(
            f"--workspace name must be a plain directory name "
            f"(no separators, no '.' or '..', not absolute); got: {name!r}"
        )
    return (home_path / name).resolve()


def _apply_overrides(cfg: ExecutorConfig, args: argparse.Namespace) -> ExecutorConfig:
    repo_path: Path = cfg.repo_path
    log_level: int = cfg.log_level
    queue_repo: str = cfg.queue_repo
    watch_mode: bool = cfg.watch_mode
    changed = False
    # argparse already enforces mutual exclusion between --repo and --workspace.
    if args.repo:
        repo_path = validate_repo_path(Path(args.repo).resolve(), source="--repo")
        changed = True
    elif args.workspace:
        repo_path = validate_repo_path(_resolve_workspace(args.workspace), source="--workspace")
        changed = True
    if args.log_level:
        log_level = int(logging.getLevelName(args.log_level))
        changed = True
    if getattr(args, "queue_repo", None):
        queue_repo = args.queue_repo
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
        repo_path=repo_path,
        log_level=log_level,
        queue_repo=queue_repo,
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
        import subprocess

        result = subprocess.run(
            ["ralph-doctor", "--json"] if args.json_output else ["ralph-doctor"],
            capture_output=True,
            text=True,
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

    Loads the executor config (with ``--repo`` / ``--workspace`` overrides
    via ``_apply_overrides``), resolves the PR-skill scripts directory for
    the configured git host, builds a ``SweepContext`` and delegates to
    ``reconcile_all``. Prints a one-line-per-orphan summary table.

    Returns 0 on success, 2 on config or environment errors.
    """
    try:
        cfg = load_config()
        cfg = _apply_overrides(cfg, args)
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
        # Worktree-mode awareness: `.ralph/` lives in the queue worktree
        # (not the primary checkout, which has no `.ralph/`).
        queue_root=_queue_repo_root(cfg) / ".ralph",
        ado_pr_scripts_path=scripts_path,
        config=SweepConfig(
            ralph_author_email=ralph_email,
            max_attempts=cfg.max_attempts,
            stale_threshold=timedelta(days=3),
            now=datetime.now(tz=UTC),
        ),
        # repo_name must come from the primary checkout name, not from
        # queue_root.parent.name (which is "queue" in worktree mode).
        repo_name=cfg.repo_path.name,
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
            return cmd_init(ralph_home=args.ralph_home, assume_yes=args.yes)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.subcommand == "scaffold":
        from ralph_executor.setup_cmds import cmd_scaffold

        # Reuse the --repo / --workspace / cwd resolution chain rather
        # than reimplement: synthesise a path the same way the loop does.
        try:
            scaffold_target = _scaffold_resolve_target(args)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return cmd_scaffold(
            repo_path=scaffold_target,
            force=args.force,
            with_config_toml=args.with_config_toml,
        )
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
        "ralph-executor starting (repo=%s queue_repo=%s main=%s)",
        cfg.repo_path,
        cfg.queue_repo,
        cfg.main_branch,
    )

    # Sync TOML-sourced project identifiers / alerting into the process
    # env BEFORE host_select runs. host_select.verify_auth_env and the
    # pr-<host> + workitem-fetch-<host> skills all read these by name
    # from the environment; this single bridge lets operators write
    # them once in `.ralph/config.toml` instead of exporting per shell.
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
