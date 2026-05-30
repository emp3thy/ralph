---
name: ralph-new
description: Author a new PBI directly into the ralph-queue. Interactive prompts (or flags) gather title, type (bug/feature), severity, target_repo, depends_on, and per-type body content. Writes the canonical PBI directory shape (BUG.md+REPRODUCE.md+HISTORY.md for bugs; PBI.md+PLAN.md+HISTORY.md for features), commits with `chore(queue): add <id>`, and pushes to the queue branch. Sole submission surface — supersedes ralph-add. Spec: docs/superpowers/specs/2026-05-30-ralph-new-design.md.
---

# ralph-new

`ralph-new` is the single operator-facing submission surface for Ralph PBIs.

## When to use it

Use `ralph-new` whenever a human (BA, PM, triager, dev) wants to file a bug or feature against any service repo. No GitHub issue required.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--title TEXT` | yes (via prompt or flag) | PBI title; slugified to PBI id. |
| `--type {bug,feature}` | yes | PBI type. `pr-feedback` is sweep-only and rejected. |
| `--severity {critical,high,normal,low}` | no (default `normal`) | Triage lane. |
| `--target-repo URL` | yes | HTTPS owner/name URL of the target service repo. |
| `--depends-on ID` | no (repeatable) | Each id is validated for syntax AND for existence in the queue. |
| `--parent-id ID` | no | Epic link. |
| `--id SLUG` | no | Override auto-generated slug. |
| `--spec-path PATH` | no | Feature only: docs/superpowers/specs path. |
| `--plan-path PATH` | no | Feature only: docs/superpowers/plans path; blank renders a TODO stub. |
| `--body-file PATH` | no | Read entire entry-file body from file; skips per-section prompts. |
| `--reproduce-file PATH` | no | Bug only: read REPRODUCE.md body from file. |
| `--non-interactive` | no | Refuse to prompt; missing required field exits 2. |
| `--no-push` | no | Commit but do not push. |
| `--dry-run` | no | Print envelope; no clone / write / commit. |
| `--check-depends-on` | no | Under `--dry-run`, force clone so existence check runs. |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |

## Output

JSON envelope on stdout describing the written PBI. Exit 0 on success, 2 on validation/queue errors, 130 on Ctrl+C.

## Files written per type

- **Bug:** `BUG.md`, `REPRODUCE.md`, `HISTORY.md`
- **Feature:** `PBI.md`, `PLAN.md`, `HISTORY.md`

See the spec for the full body templates.
