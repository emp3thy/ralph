---
name: workitem-fetch-github
description: Fetch a GitHub Issue and emit a normalised work-item JSON document on stdout. Used by ralph-add as the GitHub-specific fetcher. Reads GH_TOKEN and GH_OWNER from the environment; the repo name is passed as a flag or derived from --repo. Downloads inline images and attachments as base64. Reads issue labels to derive PBI type (bug / feature) and severity (critical / high / normal / low).
---

# workitem-fetch-github

## What this skill does

Fetches one GitHub Issue and emits the **normalised work-item JSON** schema documented in `2026-05-24-03-ralph-add-skill.md` (and replicated in `skills/workitem-fetch-github/scripts/schema.py`). This is the Phase 1 GitHub-specific fetcher used by the host-agnostic `ralph-add` orchestrator.

The fetcher is host-pure: it knows about GitHub and only GitHub. The sibling `workitem-fetch-ado/` skill (Phase 2) provides the same surface for Azure DevOps.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--work-item <id>` | yes | Issue number (bare integer `42` or `#42`). |
| `--repo <owner/name>` | no | `owner/name` slug. If omitted, derives from `GH_OWNER` env + the issue's containing repo from `--repo-name`. |
| `--repo-name <name>` | no | Repo name only (combined with `GH_OWNER` env if `--repo` not given). |
| `--include-children` | no | Walk linked sub-issues (referenced as `#N` in the issue body or as GitHub task-list items) and emit their ids in `child_ids`. Does NOT recursively fetch them — `ralph-add` does that one level at a time. |

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `GH_TOKEN` | yes | GitHub PAT (or fine-grained token) with `repo` (private) or `public_repo` (public) scope. |
| `GH_OWNER` | when `--repo` not given | GitHub org or user (e.g. `example-org`). |

## Output

A single JSON document on stdout matching the normalised schema. Progress on stderr. Exit 0 on success, 2 on validation/IO error.

## GitHub REST endpoints used

| Operation | Endpoint |
|---|---|
| Fetch issue | `GET /repos/{owner}/{repo}/issues/{number}` |
| Download attachment | `GET <asset-url>` (the URL appears inline in the issue body's `![](...)` markdown image tags or in the issue's `body` field as plain URLs). The fetcher resolves each, downloads the bytes, and base64-encodes them. |

## Label-to-PBI-type / severity mapping

See the table in `2026-05-24-03-ralph-add-skill.md`. The fetcher applies it in order; first match wins; default `feature` / `normal`.

## How it is invoked

```bash
GH_TOKEN=ghp_... GH_OWNER=example-org \
  uv run python skills/workitem-fetch-github/scripts/fetch.py \
    --work-item 42 \
    --repo example-org/service-auth
```
