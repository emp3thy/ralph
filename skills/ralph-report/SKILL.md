---
name: ralph-report
description: Local read-only HTML dashboard of ralph-queue activity. Renders Current / Blocked / Inbox / Pending PR / Done-24h panels + a 24h activity timeline. Starts a local HTTP server on 127.0.0.1, auto-exits after 30 minutes idle. Source: filesystem reads on the operator queue clone at `<workspace_root>/queue-<instance_id>/` + `git log` against `ralph-queue`. Sibling of `ralph-status` (one-shot CLI table) — both skills read the SAME tree.
---

# ralph-report

## What this skill does

A long-running local HTTP server that renders ralph-queue activity as
an HTML dashboard. Read-only. Manual refresh (browser F5).

Five rows, top to bottom:

1. **Current** — the single PBI being worked on (1 max)
2. **Blocked** — all blocked PBIs and META-cycle sentinels
3. **Inbox + Pending PR** — side-by-side: queued PBIs and PRs in review
4. **Done (last 24h)** — PBIs whose done-move commit landed in the last 24h
5. **Activity timeline (last 24h)** — state-changes only (adds, claims, PR opens, ships, blocks, cycle-trips). Persist-iteration commits filtered out.

## When to use it

When you want a glanceable view of "what is ralph doing and what
happened recently" without re-running `ralph-status` every few minutes.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--workspace <path>` | no | Override `workspace_root` from `~/.ralph/config.toml`. |
| `--queue-repo <url>` | no | Override `queue_repo` from `~/.ralph/config.toml`. |
| `--queue-branch <name>` | no | Override `queue_branch` from `~/.ralph/config.toml` (default: `ralph-queue`). |
| `--instance-id <name>` | no | Operator instance_id used to land on the executor's namespaced queue clone (`queue-<instance-id>/`). Resolution order: `--instance-id` flag, `RALPH_INSTANCE_ID` env, `instance_id` in `~/.ralph/config.toml`, sanitised hostname. |
| `--port <int>` | no | Bind port. `0` (default) = OS picks. |
| `--bind <host>` | no | Bind host. Default: `127.0.0.1`. |
| `--idle-seconds <int>` | no | Auto-exit after N seconds of no requests. Default: 1800. |

## Configuration

`ralph-report` reads `workspace_root`, `queue_repo`, and `queue_branch`
from `~/.ralph/config.toml` (populated by `ralph-executor init`) — the
SAME chain as `ralph-status`, `ralph-cancel`, `ralph-promote`. The CLI
flags above override the TOML values one-by-one. If `queue_repo` is
neither set in TOML nor passed via `--queue-repo`, the skill exits 2
with a clear error.

## How it is invoked

```bash
# foreground
uv run python skills/ralph-report/scripts/report.py

# background (writes URL/pid to <workspace_root>/report/server-info)
bash skills/ralph-report/scripts/start-server.sh

# stop
bash skills/ralph-report/scripts/stop-server.sh
```

All three forms accept `--workspace` / `--queue-repo` / `--queue-branch`
overrides matching the TOML knobs.

## Data sources

- **Snapshot panels (Current / Inbox / Pending PR / Blocked):** filesystem reads of `.ralph/<state>/` on the operator queue clone at `<workspace_root>/queue-<instance_id>/`, resolved by `scripts.queue_writer.acquire_queue_clone(workspace_root, queue_repo, queue_branch, instance_id=...)`. This is the SAME tree `ralph-status` reads — the two surfaces are guaranteed to agree (modulo ordering / pretty wrapping).
- **Done (last 24h) and Activity timeline:** `git log origin/ralph-queue --since=24.hours.ago` against the operator clone, parsed against the executor's deterministic commit-message grammar.

## What this skill does NOT do

- It does not write to `ralph-queue` or any other branch — server is read-only. Server-info / server-stopped sentinel files land at `<workspace_root>/report/`, not inside the queue clone.
- It does not push live updates (no SSE / WebSocket / fetch polling) — refresh the browser with F5.
- It does not call GitHub / ADO. The queue folder is the truth surface.
- It does not aggregate PR check / CI status.
- It does not aggregate across multiple repos — one server, one workspace.
- It does not authenticate — server binds `127.0.0.1` only by default; `--bind 0.0.0.0` is documented but unsafe outside trusted networks.
- It does not persist state — every request recomputes from disk + `git log`.
