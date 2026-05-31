---
name: ralph-recover
description: Manual escape hatch for foreign claims on .ralph/current/<pbi-id>/. Forces the PBI out of current/ to inbox/ (resets attempts) or blocked/, strips CLAIM.json, commits, and pushes. Used when the operator must take over a PBI claimed by another ralph instance (crashed, retired, or otherwise unreachable).
---

# ralph-recover

## What this skill does

`ralph-recover` is the manual override for the multi-ralph claim
protocol. When a PBI in `.ralph/current/<pbi-id>/` carries a
`CLAIM.json` naming a different instance that the operator has decided
is unreachable, `ralph-recover` forces the PBI out of `current/` back
to `inbox/` (so the next available instance can re-claim it) or
`blocked/` (so a human can triage it). It removes the foreign
`CLAIM.json`, rewrites the entry file's `status` (and resets
`attempts` when destination is `inbox/`), appends a `recover` entry to
`HISTORY.md`, commits, and pushes.

Other operator skills (`ralph-cancel`, `ralph-promote`) refuse to act
on a foreign claim and direct the operator here. This skill is the
sole tool that bypasses the ownership check.

## When to use it

- Another ralph instance has crashed mid-iteration and left a
  `CLAIM.json` behind. The PBI is stranded — no live owner will ever
  release it.
- A retired or decommissioned ralph still owns a `CLAIM.json` and you
  need to redistribute its work.
- An operator on instance `ralph-a` needs to act on a PBI claimed by
  `ralph-b` and cannot reach `ralph-b` directly.

Do NOT use `ralph-recover` to cancel a PBI you own — use
`ralph-cancel`. Do NOT use it to move a PBI between other state
folders — use `ralph-promote`.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--pbi-id <id>` | yes | The PBI identifier under `.ralph/current/`. |
| `--to <inbox\|blocked>` | yes | Destination state. `inbox` resets `attempts` to 0 and clears the claim; `blocked` preserves `attempts` and only clears the claim. |
| `--workspace <path>` | no | Override `workspace_root` from `~/.ralph/config.toml`. |
| `--queue-repo <url>` | no | Override `queue_repo` from `~/.ralph/config.toml`. |
| `--queue-branch <branch>` | no | Override `queue_branch` from `~/.ralph/config.toml`. |
| `--instance-id <id>` | no | Override `instance_id` from `~/.ralph/config.toml`. Determines the namespaced clone path `<workspace_root>/queue-<instance-id>/`. |
| `--no-push` | no | Commit the recovery locally but do not push. |

The skill refuses to operate when the queue's halt sentinel
(`.ralph/state/halted`) is set — a halt is a global stop and overrides
manual recovery.

## Output

Prints a JSON summary to stdout on success:

```json
{
  "pbi_id": "WI-1234",
  "from_state": "current",
  "to_state": "inbox",
  "previous_owner": "ralph-b",
  "queue_clone": "/home/dev/ralph-workspaces/queue-ralph-a",
  "commit_sha": "abcdef0123456789",
  "pushed": true
}
```

`previous_owner` is `null` if the PBI had no `CLAIM.json` (legacy or
mid-migration state).

Progress messages go to stderr.

## How it is invoked

```bash
uv run python skills/ralph-recover/scripts/recover.py \
  --pbi-id WI-1234 --to inbox
```

## What this skill does NOT do

- It does not touch the target service repo or the feature branch
  `ralph/<PBI-id>`. Only the queue clone is mutated.
- It does not delete PBI files — only the foreign `CLAIM.json` is
  removed.
- It does not operate on PBIs outside `.ralph/current/`. Use
  `ralph-promote` for inter-state moves of unclaimed PBIs.
- It does not bypass the halt sentinel. A halted queue refuses
  recovery just like every other operator skill.
