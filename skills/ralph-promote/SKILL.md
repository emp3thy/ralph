---
name: ralph-promote
description: Move a PBI between state folders in the queue clone. Locates the PBI under `.ralph/<from>/<id>/`, `git mv`s it to `.ralph/<to>/<id>/`, rewrites the entry file's `status` and `updated_at` frontmatter, commits, and pushes `main` to the queue remote. This is the operator's manual override for the executor's automatic state transitions — e.g. nudging an `inbox/` PBI into `current/` so the next iteration claims it.
---

# ralph-promote

## What this skill does

`ralph-promote` moves a PBI directory between state folders in the
queue clone (`<workspace_root>/queue/` on `main`). It updates the
entry file's `status` and `updated_at` frontmatter to match the new
state, appends a single line to `HISTORY.md`, commits the move, and
pushes `main` to `origin`.

The skill does NOT modify the PBI's body, type, severity, or any other
field. It is purely a state-folder move.

## When to use it

- Manually queue an `inbox/` PBI into `current/` ahead of normal
  ordering.
- Demote a `current/` PBI back to `inbox/` so the executor releases
  it without cancelling.
- Re-open a `blocked/` PBI by moving it back to `inbox/` after the
  blocker is resolved (`ralph-triage --to inbox` is the more common
  knob for this, and resets `attempts:` — use `ralph-promote` only
  when you want the attempt count preserved).
- Send a `done/` or `pending-pr/` PBI to `archive/` when the work no
  longer belongs in the active board.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--pbi-id <id>` | yes | PBI identifier matching the directory name under `.ralph/<from>/`. |
| `--from <state>` | yes | Source state folder. One of `current`, `inbox`, `pending-pr`, `blocked`, `done`, `archive`. |
| `--to <state>` | yes | Destination state folder. Must differ from `--from`. |
| `--workspace <path>` | no | Override `workspace_root` from `~/.ralph/config.toml`. The queue clone lives at `<workspace_root>/queue/`. |
| `--queue-repo <url>` | no | Override `queue_repo` from `~/.ralph/config.toml`. HTTPS URL of the queue repo. |
| `--instance-id <name>` | no | Operator instance_id used by the CLAIM.json ownership guard when moving a PBI out of `current/`. Resolution order: `--instance-id` flag, `RALPH_INSTANCE_ID` env, `instance_id` in `~/.ralph/config.toml`, sanitised hostname. |
| `--no-push` | no | Commit the move locally but do not push. |
| `--dry-run` | no | Compute and log without writing, committing, or pushing. Prints the JSON summary describing what would have happened. Does NOT clone the queue. |

The skill resolves `workspace_root` and `queue_repo` from
`~/.ralph/config.toml` (created via `ralph-executor init`).
`--workspace` and `--queue-repo` override the TOML values. There is no
env-var fallback on the skill surface — operator paths stay on the
TOML / CLI rails.

## Output

Prints a JSON summary to stdout on success. Example:

```json
{
  "pbi_id": "WI-1234",
  "from_state": "inbox",
  "to_state": "current",
  "entry_file": ".ralph/current/WI-1234/PBI.md",
  "queue_clone": "/home/dev/ralph-workspaces/queue",
  "commit_sha": "abcdef0123456789",
  "pushed": true,
  "dry_run": false,
  "already_promoted": false
}
```

Progress messages go to stderr.

## How it is invoked

```bash
uv run python skills/ralph-promote/scripts/promote.py \
    --pbi-id WI-1234 \
    --from inbox \
    --to current
```

## CLAIM.json ownership guard (multi-ralph)

Under multi-ralph (Scope 1) every PBI in `.ralph/current/` carries a
`CLAIM.json` recording which ralph instance owns the claim. When
`ralph-promote` moves a PBI **out** of `current/`, it compares the claim's
`instance_id` against the operator's resolved identity:

- **Own claim** → promote proceeds as documented above.
- **Foreign claim** → exits with code `3` and the message
  `ralph-promote: cannot promote PBI claimed by '<other>'; use ralph-recover`.
  Route through `ralph-recover` to take over before promoting.
- **Missing or malformed `CLAIM.json`** → exits with code `3` and a
  message describing the inconsistency. Every claimed PBI must carry a
  valid claim under Scope 1; this state indicates a queue bug or
  half-rolled-back claim.

The guard is scoped to moves out of `current/` only — every other source
folder (`inbox/`, `blocked/`, `pending-pr/`, `done/`, `archive/`) is
CLAIM-less by design, so moves from those folders skip the guard. Moves
**into** `current/` are not blocked by `ralph-promote` (the executor's
claim path is what writes CLAIM.json on its own iterations).

Exit-code summary: `0` success, `2` config / queue error, `3` claim guard
refusal (route to `ralph-recover`).

## What this skill does NOT do

- It does not change `severity`, `type`, `attempts`, or any other
  frontmatter field. Only `status` (matching the destination folder)
  and `updated_at` are rewritten.
- It does not edit the PBI body. The entry file's content past the
  frontmatter is preserved verbatim.
- It does not touch the target service repo. All mutation is against
  the queue clone.
- It does not coordinate with the executor's claim machinery — if you
  promote a PBI into `current/` while another is already there, you
  will create the two-PBIs-in-current state that the executor refuses
  on its next iteration. Use this skill deliberately.
