#!/usr/bin/env bash
# Background launcher for ralph-report.
#
# Usage:
#   start-server.sh [--workspace PATH] [--queue-repo URL] [--queue-branch NAME] \
#                   [--port N] [--bind HOST] [--idle-seconds N]
#
# Resolves the operator workspace via the same chain as `ralph-status`
# (`--workspace` flag → `workspace_root` in `~/.ralph/config.toml` →
# default `~/ralph-workspaces`), writes connection info to
# `<workspace_root>/report/server-info`, then exits. The server keeps
# running until idle for 30 minutes OR until stop-server.sh is invoked.

set -euo pipefail

WORKSPACE=""
QUEUE_REPO=""
QUEUE_BRANCH=""
PORT=0
BIND="127.0.0.1"
IDLE=1800

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace) WORKSPACE="$2"; shift 2;;
    --queue-repo) QUEUE_REPO="$2"; shift 2;;
    --queue-branch) QUEUE_BRANCH="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --bind) BIND="$2"; shift 2;;
    --idle-seconds) IDLE="$2"; shift 2;;
    *) echo "unknown flag: $1" >&2; exit 2;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Resolve workspace_root via the same chain report.py uses, so we know
# where the child will land server-info before we have to poll for it.
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
    print(f'start-server.sh: {exc}', file=sys.stderr)
    raise SystemExit(2)
"
)"

REPORT_DIR="$WORKSPACE_ROOT/report"
mkdir -p "$REPORT_DIR"
LOG_FILE="$REPORT_DIR/server.log"
rm -f "$REPORT_DIR/server-stopped"
# Force the wait loop below to block until the freshly-launched child
# writes new connection info — otherwise a stale server-info from a
# previous run is returned and callers get a dead PID/port.
rm -f "$REPORT_DIR/server-info"

CHILD_ARGS=(--port "$PORT" --bind "$BIND" --idle-seconds "$IDLE")
if [[ -n "$WORKSPACE" ]]; then
  CHILD_ARGS+=(--workspace "$WORKSPACE")
fi
if [[ -n "$QUEUE_REPO" ]]; then
  CHILD_ARGS+=(--queue-repo "$QUEUE_REPO")
fi
if [[ -n "$QUEUE_BRANCH" ]]; then
  CHILD_ARGS+=(--queue-branch "$QUEUE_BRANCH")
fi

nohup uv run python "$SCRIPT_DIR/report.py" "${CHILD_ARGS[@]}" \
  > "$LOG_FILE" 2>&1 &

# Wait briefly for server-info to be written by the child.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if [[ -f "$REPORT_DIR/server-info" ]]; then
    break
  fi
  sleep 0.3
done

if [[ ! -f "$REPORT_DIR/server-info" ]]; then
  echo "start-server.sh: server failed to start; see $LOG_FILE" >&2
  exit 1
fi

cat "$REPORT_DIR/server-info"
