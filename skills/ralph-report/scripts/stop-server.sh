#!/usr/bin/env bash
# Stop the running ralph-report server identified by
# <workspace_root>/report/server-info.
#
# Usage:
#   stop-server.sh [--workspace PATH]
#
# Resolves workspace_root the same way report.py does, then signals the
# pid recorded in server-info.

set -euo pipefail

WORKSPACE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace) WORKSPACE="$2"; shift 2;;
    -h|--help) echo "usage: stop-server.sh [--workspace PATH]"; exit 0;;
    *) echo "stop-server.sh: unknown flag: $1" >&2; exit 2;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

WORKSPACE_ROOT="$(
  WORKSPACE_INPUT="$WORKSPACE" REPO_ROOT_INPUT="$REPO_ROOT" uv run python -c "
import os
import sys
from pathlib import Path
sys.path.insert(0, os.environ['REPO_ROOT_INPUT'])
from scripts.queue_writer import QueueWriterError, resolve_workspace_root
try:
    ws_in = os.environ.get('WORKSPACE_INPUT', '')
    cli = Path(ws_in) if ws_in else None
    print(resolve_workspace_root(cli))
except QueueWriterError as exc:
    print(f'stop-server.sh: {exc}', file=sys.stderr)
    raise SystemExit(2)
"
)"

INFO="$WORKSPACE_ROOT/report/server-info"
STOPPED="$WORKSPACE_ROOT/report/server-stopped"

if [[ -f "$STOPPED" ]]; then
  # Server exited naturally (idle timeout). server-info still on disk but
  # the PID may now belong to an unrelated process the OS reused — don't
  # signal it.
  echo "stop-server.sh: already stopped (server-stopped present)"
  exit 0
fi

if [[ ! -f "$INFO" ]]; then
  echo "stop-server.sh: $INFO not found; server may already be stopped" >&2
  exit 0
fi

PID="$(python3 -c "import json, sys; print(json.load(open(sys.argv[1]))['pid'])" "$INFO")"

if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  # Python's default SIGTERM disposition terminates the process before
  # it can run report.py's cleanup, so we write the sentinel from here.
  # Without this, a second stop-server.sh invocation would skip the
  # server-stopped guard and could signal a reused PID.
  date -u +%Y-%m-%dT%H:%M:%SZ > "$STOPPED"
  echo "stop-server.sh: sent SIGTERM to pid $PID"
else
  # PID is gone — write the sentinel so a future invocation doesn't
  # re-attempt the kill against whatever now owns that PID.
  date -u +%Y-%m-%dT%H:%M:%SZ > "$STOPPED"
  echo "stop-server.sh: pid $PID not running"
fi
