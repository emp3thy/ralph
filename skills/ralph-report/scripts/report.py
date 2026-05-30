"""Entry point for the ``ralph-report`` skill.

Parses CLI args, starts the local HTTP server in a daemon thread,
writes ``<repo>/.ralph-work/report/server-info`` (a JSON file holding
the URL, host, port, pid, and start timestamp), and blocks until the
server's idle timer fires or the user sends SIGINT. On exit, drops a
``server-stopped`` marker so ``stop-server.sh`` (and any other client)
can tell the server is no longer running.

The sibling ``server.py`` lives in the same kebab-case directory, which
is not a valid Python package, so it is loaded via
:func:`importlib.util.spec_from_file_location` rather than
``from .server import start_server`` (the plan's pseudocode would raise
``ImportError`` against the kebab dir, same blocker iter 2-6 hit).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

_SCRIPTS_DIR = Path(__file__).resolve().parent


def _load_sibling(name: str, file_name: str) -> ModuleType:
    """Load a sibling kebab-dir script as a top-level module."""
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = _SCRIPTS_DIR / file_name
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_server = _load_sibling("ralph_report_server", "server.py")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — returns a Unix exit code (0 on clean exit)."""
    parser = argparse.ArgumentParser(
        description="ralph-report — local HTML dashboard for ralph-queue activity.",
    )
    parser.add_argument(
        "--repo",
        required=True,
        type=Path,
        help="Path to the ralph repo checkout.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Bind port (0 = OS picks). Default: 0.",
    )
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="Bind host. Default: 127.0.0.1.",
    )
    parser.add_argument(
        "--idle-seconds",
        type=int,
        default=1800,
        help="Auto-exit after N seconds of no requests. Default: 1800.",
    )
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    if not repo.is_dir():
        print(
            f"ralph-report: --repo {args.repo} is not a directory",
            file=sys.stderr,
        )
        return 2
    if not (repo / ".git").exists():
        print(
            f"ralph-report: --repo {args.repo} is not a git repo (no .git)",
            file=sys.stderr,
        )
        return 2

    handle = _server.start_server(
        repo_path=repo,
        port=args.port,
        idle_seconds=args.idle_seconds,
        bind_host=args.bind,
    )

    info_dir = repo / ".ralph-work" / "report"
    info_dir.mkdir(parents=True, exist_ok=True)
    info_path = info_dir / "server-info"
    stopped_path = info_dir / "server-stopped"
    if stopped_path.exists():
        stopped_path.unlink()
    info_path.write_text(
        json.dumps(
            {
                "url": f"http://{args.bind}:{handle.port}",
                "host": args.bind,
                "port": handle.port,
                "pid": os.getpid(),
                "started_at": datetime.now(tz=UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"ralph-report listening on http://{args.bind}:{handle.port}")
    print(f"server-info: {info_path}")

    try:
        handle.thread.join()
    except KeyboardInterrupt:
        handle.shutdown()
        handle.thread.join(timeout=5)

    stopped_path.write_text(
        datetime.now(tz=UTC).isoformat(),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
