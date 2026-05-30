#!/usr/bin/env bash
# Stop the running ralph-report server identified by .ralph-work/report/server-info.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: stop-server.sh <repo-path>" >&2
  exit 2
fi

REPO="$1"
INFO="$REPO/.ralph-work/report/server-info"
STOPPED="$REPO/.ralph-work/report/server-stopped"

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
