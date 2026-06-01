---
name: ralph-recover
description: Manually recover a stuck claim under multi-ralph. Moves a PBI out of .ralph/current/ back to inbox/ or to blocked/ in the queue clone, deletes its CLAIM.json, appends a HISTORY.md entry naming the previous owner, and (for --to inbox) resets the attempt counter. Operator-driven; no --force flag — invocation itself is the deliberate action. Refuses to run when the halt sentinel is active.
---

# ralph-recover

## What this skill does

`ralph-recover` is how a human takes over a PBI that some ralph
instance claimed but is no longer working on (host crashed, workspace
lost, fleet rebalance). It moves the PBI directory out of
`.ralph/current/<id>/` back to either `.ralph/inbox/<id>/` (so the
queue can re-dispatch it) or `.ralph/blocked/<id>/` (so a human
triages it), deletes the orphan `CLAIM.json`, appends a `HISTORY.md`
audit entry naming the previous owner, and (when the destination is
`inbox`) resets the PBI's `attempts:` counter to `0` — an orphaned
claim is not a failed attempt.

Every recover lands as ONE commit pinned to the subject
`chore(queue): recover <pbi-id> from <previous-instance-id>`. The
existing `CLAIM.json` payload is printed to stderr as an audit trail
BEFORE the destructive move so the operator has a copy of what was
overwritten.

## When to use it

- A ralph instance crashed mid-iteration leaving a claim no other
  instance can take.
- A workspace was deleted but the queue's `current/<id>/CLAIM.json`
  still points at that instance.
- An operator wants to reassign a stuck PBI to a different fleet
  member.

Do NOT use `ralph-recover` to abort a PBI that the owning instance is
still actively working on — that is what `ralph-cancel` is for. The
ownership-guard refusals from `ralph-cancel` / `ralph-promote` (exit
3) explicitly point the operator at `ralph-recover` for the
foreign-claim case.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--pbi-id <id>` | yes | PBI identifier matching the directory name under `.ralph/current/`. |
| `--to <state>` | yes | Destination state folder. Must be `inbox` (re-dispatchable, attempts counter reset) or `blocked` (human-triage, attempts preserved). |
| `--workspace <path>` | no | Override `workspace_root` from `~/.ralph/config.toml`. The queue clone lives at `<workspace_root>/queue/`. |
| `--queue-repo <url>` | no | Override `queue_repo` from `~/.ralph/config.toml`. HTTPS URL of the queue repo. |
| `--queue-branch <name>` | no | Override `queue_branch` from `~/.ralph/config.toml` (default `ralph-queue`). |
| `--no-push` | no | Commit the recover locally but do not push. Useful for inspecting the commit before it lands. |

There is intentionally no `--force` flag. The skill is operator-only;
invocation IS the deliberate action.

## Halt sentinel guard

`ralph-recover` refuses to run when `.ralph/state/halted` is present
and unacknowledged. The executor halts when its safety net trips; a
queue mutation during a halt could mask or interact with the
unresolved root cause. The operator must acknowledge (or delete) the
halt sentinel before recovering claims. The refusal prints
`ralph-recover: halt sentinel active` to stderr and exits with code
`4`.

Exit-code summary: `0` success, `2` config / queue error, `4` halt
sentinel active.

## Output

Prints a JSON summary to stdout on success. Example:

```json
{
  "attempts_reset": true,
  "commit_sha": "abcdef0123456789",
  "dry_run_skipped": false,
  "entry_file": ".ralph/inbox/WI-1234/PBI.md",
  "from_state": "current",
  "pbi_id": "WI-1234",
  "pushed": true,
  "queue_clone": "/home/dev/ralph-workspaces/queue",
  "recovered_from_instance": "ralph-a",
  "to_state": "inbox"
}
```

Progress messages and the audit dump of the previous `CLAIM.json`
payload go to stderr.

## How it is invoked

```bash
# Recover to inbox so the queue can re-dispatch the PBI; attempts -> 0.
uv run python skills/ralph-recover/scripts/recover.py \
  --pbi-id WI-1234 --to inbox

# Recover to blocked so a human triages it; attempts preserved.
uv run python skills/ralph-recover/scripts/recover.py \
  --pbi-id WI-1234 --to blocked
```

## What this skill does NOT do

- It does not delete the PBI. The recover preserves every file under
  the PBI directory except `CLAIM.json`.
- It does not touch the target service repo. All mutation is against
  the queue clone.
- It does not promote a PBI between any other state pair — use
  `ralph-promote` for those moves. `ralph-recover` is specifically
  for `current/` → `inbox/|blocked/` with claim deletion.
- It does not bypass the halt sentinel. Acknowledge the halt before
  running recover.
