---
name: ralph-add
description: Submit a work item to a service repo's Ralph queue. Host-agnostic orchestrator. Calls the staged workitem-fetch skill (GitHub or ADO) to fetch the work item title, body, acceptance criteria, and attachments, packages them into a PBI directory using the canonical Ralph conventions, then commits and pushes the PBI to the ralph-queue branch of the target service repo. Optional --expand-children expands a parent work item into one child PBI per linked child work item.
---

# ralph-add

## What this skill does

`ralph-add` is the BA / PM submission surface for Ralph. It is **host-agnostic** — it does not call GitHub or Azure DevOps directly. Instead, it shells out to a sibling **`workitem-fetch/`** skill that the executor (Plan 7) has staged from one of `workitem-fetch-github/` or `workitem-fetch-ado/` based on `RALPH_GIT_HOST`. The fetcher returns a normalised JSON document; `ralph-add` packages that into a PBI directory and pushes it to `ralph-queue`.

Given a work item id (e.g. `WI-1234` for ADO, `42` or `#42` for GitHub) and a target service repo, `ralph-add`:

1. Locates the staged `workitem-fetch/` skill (see [How it finds the fetcher](#how-it-finds-the-fetcher) below).
2. Invokes the fetcher via `subprocess.run`, passing the work item id.
3. Parses the fetcher's stdout as JSON, validates the shape against the normalised schema.
4. Classifies the PBI type and severity from the normalised `type` / `severity` fields (the fetcher has already done the host-specific derivation).
5. Writes a PBI directory under `.ralph/inbox/<WI-id>/` on the `ralph-queue` branch of the target repo, following the conventions in `docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md`.
6. Commits the new directory with a conventional-commit message and pushes the `ralph-queue` branch.

## When to use it

Use `ralph-add` when a human (BA, PM, triager) wants to route a work item to a service's Ralph queue. This is the canonical entry point; never hand-edit PBI directories on `ralph-queue` directly.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--work-item <id>` | yes | Work item id. ADO form: bare integer (`1234`) or `WI-` prefixed (`WI-1234`). GitHub form: bare integer (`42`) or `#`-prefixed (`#42`). |
| `--repo <path>` | one of `--repo` / `--repo-url` | Path to an existing checkout of the target service repo. The skill switches that checkout onto `ralph-queue`, mutates it, and pushes. |
| `--repo-url <url>` | one of `--repo` / `--repo-url` | Git clone URL for the target service repo. Reserved — not implemented in v1 (the orchestrator exits with code 2 and a clear message). |
| `--severity <level>` | no | Override the severity returned by the fetcher (`critical`, `high`, `normal`, `low`). |
| `--expand-children` | no | If the work item has child links (issue references on GitHub; Hierarchy-Forward relations on ADO), write one PBI per child instead of one PBI for the parent. Each child PBI carries `parent_id: <parent-WI-id>` in its frontmatter. |
| `--via-mcp` | no | Reserved for v2. Raises `NotImplementedError` (exit 2) in v1. |
| `--branch <name>` | no | Queue branch name. Default: `ralph-queue` (overridable via `RALPH_QUEUE_BRANCH` env var). |
| `--no-push` | no | Commit the PBI directory but do not push to the remote. |
| `--dry-run` | no | Compute everything but do not write files, commit, or push. Prints the JSON summary describing what would have happened. |

## Environment variables

`ralph-add` reads only one env var directly:

| Variable | Purpose |
|---|---|
| `RALPH_QUEUE_BRANCH` | Default queue branch name; overridden by `--branch`. Optional; defaults to `ralph-queue`. |
| `RALPH_WORKITEM_FETCH_SCRIPT` | Optional override pointing at the fetcher script (used by tests). |
| `RALPH_GIT_HOST` | Used only as a fallback hint when no staged fetcher is found (see "How it finds the fetcher"). |

All host-specific env vars (`GH_TOKEN`, `GH_OWNER`, `ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT`) are read by the **fetcher**, not by `ralph-add`. `ralph-add` does not validate them; it inherits them into the fetcher subprocess's environment.

## How it finds the fetcher

`ralph-add` resolves the fetcher script in this order, taking the first that exists:

1. `RALPH_WORKITEM_FETCH_SCRIPT` env var (used by tests and advanced overrides).
2. `~/.claude/skills/workitem-fetch/scripts/fetch.py` — the install location the executor's host-select (Plan 7) stages from one of `workitem-fetch-github/` or `workitem-fetch-ado/`.
3. If `RALPH_GIT_HOST=github`: `<ralph-repo-root>/skills/workitem-fetch-github/scripts/fetch.py` (dev fallback).
4. If `RALPH_GIT_HOST=ado`: `<ralph-repo-root>/skills/workitem-fetch-ado/scripts/fetch.py` (dev fallback, Phase 2).
5. Otherwise: exit 2 with the message `could not locate workitem-fetch fetcher; set RALPH_GIT_HOST or stage the skill first`.

## Output

The skill prints a JSON summary to stdout on success. Example:

```json
{
  "work_item_id": "1234",
  "pbi_id": "WI-1234",
  "pbi_type": "feature",
  "pbi_path": ".ralph/inbox/WI-1234",
  "repo_path": "/home/dev/service-auth",
  "branch": "ralph-queue",
  "attachments_downloaded": 2,
  "children_expanded": 0,
  "commit_sha": "abcdef0123456789",
  "pushed": true,
  "dry_run": false,
  "source_host": "github"
}
```

Progress messages go to stderr; the JSON on stdout is machine-readable and is the source of truth for downstream automation.

## How it is invoked

```bash
uv run python skills/ralph-add/scripts/add.py \
    --work-item 1234 \
    --repo /path/to/service-auth
```

Tests for the script live at `tests/skills/test_ralph_add.py` and use a mock fetcher script written into the test's `tmp_path` so the orchestrator is exercised without touching any real host.

## What this skill does NOT do

- It does not call any git host's REST API directly. The fetcher does that.
- It does not create or modify the `ralph-queue` branch's policy (Plan 2 — see `docs/runbooks/ralph-queue-setup.md`).
- It does not invoke Ralph or `claude -p`; it only writes to the queue.
- It does not handle PR-feedback PBIs — those are generated by the executor's sweep (Plan 8).
- It does not edit the executor-managed frontmatter fields (`status`, `attempts`) beyond setting their initial values (`status: inbox`, `attempts: 0`).
