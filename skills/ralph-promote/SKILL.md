---
name: ralph-promote
description: Bump a PBI's severity. Locates the PBI in any state folder under .ralph/ (inbox, current, pending-pr, blocked, done, archive), updates the severity field in its frontmatter (and refreshes updated_at), commits to ralph-queue, and pushes. Use when "normal" became "urgent" without changing the work item itself. Severity must be one of critical, high, normal, low.
---

# ralph-promote

## What this skill does

`ralph-promote` is the human knob for severity. It does not change the
PBI's body, type, or state folder — it only edits the `severity` field
on the PBI's entry file frontmatter (`PBI.md` for features and PR
feedback, `BUG.md` for bugs) and refreshes `updated_at`. It also
appends a single line to `HISTORY.md` so the change is auditable. The
result is committed and pushed to `ralph-queue`.

## When to use it

- A normal-priority bug has been open long enough to need escalation.
- A feature originally tagged `low` has become a blocker for another
  team's work.
- A critical bug was mis-classified as high during submission.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--pbi-id <id>` | yes | PBI identifier matching the directory name under any `.ralph/<state>/` folder. |
| `--severity <level>` | yes | New severity. One of `critical`, `high`, `normal`, `low`. |
| `--repo <path>` | yes | Absolute path to an existing checkout of the target service repo. |
| `--branch <name>` | no | Queue branch name. Default: `ralph-queue` (overridable via `RALPH_QUEUE_BRANCH`). |
| `--no-push` | no | Commit locally but do not push. |
| `--dry-run` | no | Compute and log without writing, committing, or pushing. |

## Environment variables

| Variable | Purpose |
|---|---|
| `RALPH_QUEUE_BRANCH` | Default queue branch name; overridden by `--branch`. Optional; defaults to `ralph-queue`. |

## Output

Prints a JSON summary to stdout on success. Example:

```json
{
  "pbi_id": "WI-1234",
  "previous_severity": "normal",
  "new_severity": "high",
  "state_folder": "inbox",
  "entry_file": ".ralph/inbox/WI-1234/PBI.md",
  "repo_path": "/home/dev/service-auth",
  "branch": "ralph-queue",
  "commit_sha": "abcdef0123456789",
  "pushed": true,
  "dry_run": false
}
```

## How it is invoked

```bash
uv run python skills/ralph-promote/scripts/promote.py \
    --pbi-id WI-1234 \
    --severity high \
    --repo /path/to/service-auth
```

## What this skill does NOT do

- It does not move the PBI between state folders. A PBI stays where it
  was; only the severity changes.
- It does not change other frontmatter fields (`status`, `attempts`,
  `type`, `id`, `created_at`). Those are owned by the executor or are
  set once at submission.
- It does not require ADO credentials — this is purely a git mutation.
