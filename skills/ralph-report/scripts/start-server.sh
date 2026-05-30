#!/usr/bin/env bash
# Background launcher for ralph-report.
#
# Usage:
#   start-server.sh --repo /path/to/ralph
#
# Writes connection info to <repo>/.ralph-work/report/server-info,
# then exits. The server keeps running until idle for 30 minutes
# OR until stop-server.sh is invoked.

set -euo pipefail

REPO=""
PORT=0
BIND="127.0.0.1"
IDLE=1800

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --bind) BIND="$2"; shift 2;;
    --idle-seconds) IDLE="$2"; shift 2;;
    *) echo "unknown flag: $1" >&2; exit 2;;
  esac
done

if [[ -z "$REPO" ]]; then
  echo "start-server.sh: --repo <path> is required" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPORT_DIR="$REPO/.ralph-work/report"
mkdir -p "$REPORT_DIR"
LOG_FILE="$REPORT_DIR/server.log"
rm -f "$REPORT_DIR/server-stopped"
# Force the wait loop below to block until the freshly-launched child
# writes new connection info — otherwise a stale server-info from a
# previous run is returned and callers get a dead PID/port.
rm -f "$REPORT_DIR/server-info"

nohup uv run python "$SCRIPT_DIR/report.py" \
  --repo "$REPO" \
  --port "$PORT" \
  --bind "$BIND" \
  --idle-seconds "$IDLE" \
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
