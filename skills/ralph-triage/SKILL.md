---
name: ralph-triage
description: Walk the .ralph/blocked/ queue in the queue clone and route a stuck PBI to its next state. Two destinations are allowed - inbox (return for retry, with attempts reset to 0) or archive (close out, creating .ralph/archive/ on demand). Every triage decision requires a --note explaining the operator's reasoning; the note is appended to the PBI's HISTORY.md so the audit trail is preserved.
---

# ralph-triage

## What this skill does

`ralph-triage` is the on-call / tech-lead surface for the blocked queue
inside the queue clone (`<workspace_root>/queue-<instance_id>/` on
`cfg.queue_branch`, default: `ralph-queue`). When
Ralph has marked a PBI blocked (because it self-halted via STUCK.md or
because the executor's cycle detector tripped on it), the PBI sits in
`.ralph/blocked/<pbi-id>/`. A human reads the STUCK.md and HISTORY.md
and chooses one of two paths:

- **`--to inbox`** — return the PBI to the inbox queue. The skill
  resets `attempts` to 0, updates `status` to `inbox`, refreshes
  `updated_at`, `git mv`s the directory to `.ralph/inbox/<pbi-id>/`,
  and appends a structured HISTORY.md entry containing the human's
  `--note`. The PBI re-enters the priority lanes on the next executor
  iteration.
- **`--to archive`** — close the PBI out. The skill updates `status`
  to `archive`, refreshes `updated_at`, `git mv`s the directory to
  `.ralph/archive/<pbi-id>/` (creating the archive folder on demand),
  and appends a final HISTORY.md entry. `attempts` is preserved on
  archive — the record of how hard Ralph tried is part of the audit
  trail.

After the move the skill commits and pushes the queue branch
(`cfg.queue_branch`, default: `ralph-queue`) to the queue remote.

## When to use it

- Daily blocked-queue review: walk every PBI in `blocked/`, decide
  inbox vs archive.
- Reactive triage: a STUCK.md from Ralph mentions an ambiguity that
  the on-call engineer can resolve; return to inbox after adding
  clarifying context to the PBI itself (separately).
- Cleanup: a PBI is obsolete (the feature was descoped, the bug was
  fixed elsewhere). Archive it with a note.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--pbi-id <id>` | yes | PBI identifier matching the directory name under `.ralph/blocked/`. |
| `--to <destination>` | yes | Where to route the PBI. One of `inbox`, `archive`. |
| `--note <text>` | yes | Operator's reasoning for the decision. Appended verbatim to HISTORY.md. |
| `--workspace <path>` | no | Override `workspace_root` from `~/.ralph/config.toml`. The queue clone lives at `<workspace_root>/queue-<instance_id>/`. |
| `--queue-repo <url>` | no | Override `queue_repo` from `~/.ralph/config.toml`. HTTPS URL of the queue repo. |
| `--queue-branch <name>` | no | Override `queue_branch` from `~/.ralph/config.toml` (default: `ralph-queue`). |
| `--no-push` | no | Commit locally but do not push. |
| `--dry-run` | no | Compute and log without moving, committing, or pushing. Prints the JSON summary describing what would have happened. Does NOT clone the queue. |

The skill resolves `workspace_root` and `queue_repo` from
`~/.ralph/config.toml` (created via `ralph-executor init`).
`--workspace` and `--queue-repo` override the TOML values. There is no
env-var fallback on the skill surface — operator paths stay on the
TOML / CLI rails.

## Output

Prints a JSON summary to stdout on success. Example for `--to inbox`:

```json
{
  "pbi_id": "WI-1234",
  "destination": "inbox",
  "previous_state_folder": "blocked",
  "old_path": ".ralph/blocked/WI-1234",
  "new_path": ".ralph/inbox/WI-1234",
  "attempts_reset_to_zero": true,
  "archive_created": false,
  "queue_clone": "/home/dev/ralph-workspaces/queue-<instance-id>",
  "commit_sha": "abcdef0123456789",
  "pushed": true,
  "dry_run": false,
  "already_triaged": false
}
```

Progress messages go to stderr.

## How it is invoked

```bash
uv run python skills/ralph-triage/scripts/triage.py \
    --pbi-id WI-1234 \
    --to inbox \
    --note "QA produced a working repro; resetting for retry"
```

## What this skill does NOT do

- It does not touch any PBI outside `.ralph/blocked/`. PBIs in
  `inbox/`, `current/`, `pending-pr/`, `done/`, or `archive/` cannot
  be triaged — only the blocked queue is the human-triage surface.
  Use `ralph-promote --from <state> --to <state>` for arbitrary
  state-folder moves.
- It does not edit the PBI's body content. The only mutations are to
  the frontmatter (`status`, `attempts`, `updated_at`) and HISTORY.md.
- It does not delete the STUCK.md file when returning to inbox. The
  STUCK.md remains as historical context for the next attempt; Ralph's
  PROMPT.md tells it to read HISTORY.md and treat any present
  STUCK.md as prior diagnosis, not a current halt.
- It does not touch the target service repo. All mutation is against
  the queue clone.
