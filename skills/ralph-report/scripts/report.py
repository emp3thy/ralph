"""Entry point for the ``ralph-report`` skill.

Parses CLI args, resolves the operator queue clone via the standard
``scripts.queue_writer`` chain (``--workspace`` / ``--queue-repo`` /
``--queue-branch`` with ``~/.ralph/config.toml`` fallbacks), starts the
local HTTP server in a daemon thread, writes
``<workspace_root>/report/server-info`` (a JSON file holding the URL,
host, port, pid, queue_clone, and start timestamp), and blocks until the
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
_REPO_ROOT = _SCRIPTS_DIR.resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.queue_writer import (  # noqa: E402
    QueueWriterError,
    acquire_queue_clone,
    resolve_queue_branch,
    resolve_queue_repo,
    resolve_workspace_root,
)


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


def report_dir(workspace_root: Path) -> Path:
    """Return the directory where ralph-report writes its sentinel files.

    Kept out of the queue clone so the clone's working tree stays clean
    (the queue clone is on the ``ralph-queue`` branch and is mutated by
    other skills; dropping server-info inside it would surface as an
    untracked file on every ``git status``).
    """
    return workspace_root / "report"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — returns a Unix exit code (0 on clean exit)."""
    parser = argparse.ArgumentParser(
        description="ralph-report — local HTML dashboard for ralph-queue activity.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Override workspace_root from ~/.ralph/config.toml.",
    )
    parser.add_argument(
        "--queue-repo",
        dest="queue_repo",
        help="Override queue_repo from ~/.ralph/config.toml.",
    )
    parser.add_argument(
        "--queue-branch",
        dest="queue_branch",
        metavar="BRANCH",
        help="Override queue_branch from ~/.ralph/config.toml (default: ralph-queue).",
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

    try:
        workspace_root = resolve_workspace_root(args.workspace)
        queue_repo = resolve_queue_repo(args.queue_repo)
        queue_branch = resolve_queue_branch(args.queue_branch)
        queue_clone = acquire_queue_clone(workspace_root, queue_repo, queue_branch)
    except QueueWriterError as exc:
        print(f"ralph-report: {exc}", file=sys.stderr)
        return 2

    handle = _server.start_server(
        queue_clone=queue_clone,
        port=args.port,
        idle_seconds=args.idle_seconds,
        bind_host=args.bind,
    )

    info_dir = report_dir(workspace_root)
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
                "queue_clone": str(queue_clone),
                "workspace_root": str(workspace_root),
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
