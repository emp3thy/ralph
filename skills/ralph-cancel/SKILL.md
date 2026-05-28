---
name: ralph-cancel
description: Cancel a PBI Ralph is actively working on. Drops an empty CANCEL sentinel file into the PBI directory under .ralph/current/ in the queue clone (<workspace_root>/queue on main), commits, and pushes. Ralph notices the sentinel on its next iteration and abandons the PBI. The sentinel is the only allowed mutation on .ralph/current/; PBIs in other state folders (inbox, blocked, pending-pr, done, archive) cannot be cancelled this way.
---

# ralph-cancel

## What this skill does

`ralph-cancel` is how a human aborts a PBI Ralph is in the middle of
working on. It writes an empty file named `CANCEL` inside the PBI's
directory under `.ralph/current/` in the queue clone, commits, and
pushes `main` to `origin`. Ralph's loop reads `CANCEL` at the top of
every iteration and, if it finds one, stops work on the PBI and moves
it to `.ralph/blocked/` with a cancellation note in `HISTORY.md`.

## When to use it

- The PBI's scope changed and Ralph should stop immediately.
- Ralph is thrashing on the PBI and a human wants to pull it back into
  the human triage loop.
- The PBI was submitted by mistake and the BA wants to clear the slot.

Do NOT use `ralph-cancel` for PBIs that aren't in `current/`. Use
`ralph-triage` (for blocked PBIs) or `ralph-promote` (to demote an
inbox/pending-pr PBI back out) instead.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--pbi-id <id>` | yes | The PBI identifier (e.g. `WI-1234` or `BUG-deploy-rosa-irsa-2026-05-23`). Matches the directory name under `.ralph/current/`. |
| `--workspace <path>` | no | Override `workspace_root` from `~/.ralph/config.toml`. The queue clone lives at `<workspace_root>/queue/`. |
| `--queue-repo <url>` | no | Override `queue_repo` from `~/.ralph/config.toml`. HTTPS URL of the queue repo. |
| `--no-push` | no | Commit the sentinel locally but do not push. Useful for inspecting the commit before it lands. |
| `--dry-run` | no | Compute and log without writing the sentinel, committing, or pushing. Prints the JSON summary describing what would have happened. Does NOT clone the queue. |

The skill resolves `workspace_root` and `queue_repo` from
`~/.ralph/config.toml` (created via `ralph-executor init`). `--workspace`
and `--queue-repo` override the TOML values. There is no env-var fallback
on the skill surface — operator paths stay on the TOML / CLI rails.

## Output

Prints a JSON summary to stdout on success. Example:

```json
{
  "pbi_id": "WI-1234",
  "sentinel_path": ".ralph/current/WI-1234/CANCEL",
  "queue_clone": "/home/dev/ralph-workspaces/queue",
  "commit_sha": "abcdef0123456789",
  "pushed": true,
  "dry_run": false,
  "already_cancelled": false
}
```

Progress messages go to stderr.

## How it is invoked

```bash
uv run python skills/ralph-cancel/scripts/cancel.py --pbi-id WI-1234
```

## What this skill does NOT do

- It does not move the PBI directory out of `.ralph/current/` — that
  is the executor's job. The sentinel is a signal; the move is the
  response.
- It does not delete any PBI files. The cancellation is recorded via
  the empty `CANCEL` file plus a commit on `main` in the queue repo.
- It does not affect PBIs in `.ralph/inbox/`, `.ralph/blocked/`,
  `.ralph/pending-pr/`, `.ralph/done/`, or `.ralph/archive/`. Cancellation
  is current-state-only.
- It does not touch the target service repo. All mutation is against
  the queue clone.
