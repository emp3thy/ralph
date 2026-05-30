---
name: ralph-report
description: Local read-only HTML dashboard of ralph-queue activity in a single repo. Renders Current / Blocked / Inbox / Pending PR / Done-24h panels + a 24h activity timeline. Starts a local HTTP server on 127.0.0.1, auto-exits after 30 minutes idle. Source: filesystem reads on the queue worktree + `git log` against `ralph-queue`. Sibling of `ralph-status` (one-shot CLI table).
---

# ralph-report

## What this skill does

A long-running local HTTP server that renders the ralph-queue activity
of a single repo as an HTML dashboard. Read-only. Manual refresh (browser F5).

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
| `--repo <path>` | yes | Path to the ralph repo checkout. |
| `--port <int>` | no | Bind port. `0` (default) = OS picks. |
| `--bind <host>` | no | Bind host. Default: `127.0.0.1`. |
| `--idle-seconds <int>` | no | Auto-exit after N seconds of no requests. Default: 1800. |

## How it is invoked

```bash
# foreground
uv run python skills/ralph-report/scripts/report.py --repo /path/to/repo

# background (writes URL/pid to <repo>/.ralph-work/report/server-info)
bash skills/ralph-report/scripts/start-server.sh --repo /path/to/repo

# stop
bash skills/ralph-report/scripts/stop-server.sh /path/to/repo
```

## Data sources

- **Snapshot panels (Current / Inbox / Pending PR / Blocked):** filesystem reads of `.ralph/<state>/` on the queue worktree at `<repo>/.ralph-work/queue`. Falls back to `git -C <repo> show origin/ralph-queue:<path>` when the worktree is absent.
- **Done (last 24h) and Activity timeline:** `git log ralph-queue --since=24.hours.ago` parsed against the executor's deterministic commit-message grammar.

## What this skill does NOT do

- It does not write to `ralph-queue` or any other branch — server is read-only.
- It does not push live updates (no SSE / WebSocket / fetch polling) — refresh the browser with F5.
- It does not call GitHub / ADO. The queue folder is the truth surface.
- It does not aggregate PR check / CI status.
- It does not aggregate across multiple repos — one server, one repo.
- It does not authenticate — server binds `127.0.0.1` only by default; `--bind 0.0.0.0` is documented but unsafe outside trusted networks.
- It does not persist state — every request recomputes from disk + `git log`.
