"""Command-line entry point for ``ralph-executor``.

Usage::

    ralph-executor [--once] [--iterations N]
                   [--repo PATH | --workspace NAME] [--log-level LEVEL]
    ralph-executor health --ready
    ralph-executor health --live
    ralph-executor doctor [--json]

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
from ralph_executor.loop import iterate_once, run_loop

_VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

log = logging.getLogger(__name__)


def _truthy(value: str) -> bool:
    """Return True if the string value looks like a boolean true."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ralph-executor",
        description=(
            "Run the Ralph per-repo autonomous coding loop. By default "
            "iterates until interrupted; use --once for a single iteration."
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

    return parser


def _configure_logging(level: int) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        force=True,
    )
    logging.getLogger("ralph_executor").setLevel(level)


def _resolve_workspace(name: str) -> Path:
    """Resolve ``--workspace NAME`` against ``$RALPH_HOME``.

    Raises ``ConfigError`` if RALPH_HOME is unset or empty so the operator
    gets a clear error rather than a silently-wrong path.
    """
    home_raw = os.environ.get("RALPH_HOME", "").strip()
    if not home_raw:
        raise ConfigError(
            "--workspace requires RALPH_HOME to be set (e.g. "
            "RALPH_HOME=C:\\dev\\ralph; ralph-executor --workspace my-repo "
            "then resolves to C:\\dev\\ralph\\my-repo)"
        )
    return (Path(home_raw) / name).resolve()


def _apply_overrides(cfg: ExecutorConfig, args: argparse.Namespace) -> ExecutorConfig:
    repo_path: Path = cfg.repo_path
    log_level: int = cfg.log_level
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
    if not changed:
        return cfg
    return dataclasses.replace(cfg, repo_path=repo_path, log_level=log_level)


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    # --- dispatch subcommands that don't need config ---
    if args.subcommand == "health":
        return _cmd_health(args)
    if args.subcommand == "doctor":
        return _cmd_doctor(args)

    # --- default command: run the executor loop ---
    try:
        cfg = load_config()
        cfg = _apply_overrides(cfg, args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _configure_logging(cfg.log_level)

    log.info(
        "ralph-executor starting (repo=%s queue=%s main=%s)",
        cfg.repo_path,
        cfg.queue_branch,
        cfg.main_branch,
    )

    # Stage host-specific skills BEFORE any iteration. If this fails,
    # Ralph can't operate against the chosen host -- abort immediately
    # so the operator fixes their env rather than silently running
    # against missing or stale skill directories.
    try:
        host = prepare_host_environment()
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
