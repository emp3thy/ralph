---
name: ralph-doctor
description: Verify the host environment (laptop or pod) is ralph-safe BEFORE the executor starts. Runs seven preflight checks — permissions.allow coverage; hooks free of interactive-prompt calls / stdin reads; skills free of interactive-prompt calls in their main path; MCP servers configured with non-interactive auth; Anthropic (or Bedrock) auth resolves on cold start; staged `pr/` + `workitem-fetch/` skills match RALPH_GIT_HOST; host-specific auth check (GitHub PAT for `github`, ADO PAT for `ado`) — and refuses to let Ralph start if any error-severity check fails. Reads ~/.claude/settings.json by default; the path is configurable via --settings for tests and alternative install layouts.
---

# ralph-doctor

## What this skill does

`ralph-doctor` is the preflight gate for `ralph-executor`. It catches the
one class of failure that silently kills unattended pods: a hook or skill
that expects a human (an <!-- ralph-doctor: ignore -->`AskUserQuestion`<!-- /ralph-doctor: ignore --> call, a `read -p` prompt, an
OAuth refresh) — and it now also catches the host-staging class of
failure: a pod built for one git host that was started with the wrong
`RALPH_GIT_HOST` value, or a pod whose `host_select.py` never ran. The
spec is explicit about this — see
`docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md`
section "ralph-doctor checks". If `ralph-doctor` cannot pass on the
target container image, the pod does not start.

## When to use it

- Manually on a developer laptop before pushing a container image:
  `python skills/ralph-doctor/scripts/check.py`
- Automatically as the container's entrypoint's first step:
  `python /opt/ralph/skills/ralph-doctor/scripts/check.py && exec
  ralph-executor`. Non-zero exit terminates the entrypoint before the
  executor spawns.
- In CI as a gating job on the ralph repo and on consumer service repos.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--settings <path>` | no | Path to settings.json. Default `~/.claude/settings.json`. |
| `--skills-dir <path>` | no | Path to installed skills. Default `~/.claude/skills`. |
| `--skip <name[,name...]>` | no | Check names to skip (e.g. `--skip auth,github_auth` for offline runs). |
| `--only <name[,name...]>` | no | Check names to run; everything else is skipped. Mutually exclusive with `--skip`. |
| `--json` | no | Suppress the human summary on stderr; emit JSON only. |
| `--strict` | no | Treat warn-severity failures as errors. |

## Environment variables

| Variable | Used by | Purpose |
|---|---|---|
| `RALPH_GIT_HOST` | runner | **Required.** `github` or `ado`. Selects the host-auth check. Unset → exit 2 with pointer to the orchestrator env-var table. |
| `ANTHROPIC_API_KEY` | `auth` | Anthropic Messages API key. Required unless `RALPH_USE_BEDROCK=1`. |
| `RALPH_USE_BEDROCK` | `auth` | Set to `1` to probe AWS Bedrock instead of Anthropic. |
| `GH_TOKEN` | `github_auth` | GitHub PAT. Required when `RALPH_GIT_HOST=github`. |
| `GH_OWNER` | `github_auth` | GitHub org or user. Required when `RALPH_GIT_HOST=github`. |
| `ADO_PAT` | `ado_auth` | ADO Personal Access Token. Required when `RALPH_GIT_HOST=ado`. |
| `ADO_ORG_URL` | `ado_auth` | ADO org URL. Required when `RALPH_GIT_HOST=ado`. |
| `ADO_PROJECT` | `ado_auth` | ADO project name. Required when `RALPH_GIT_HOST=ado`. |
| `ADO_REPOSITORY` | `ado_auth` | ADO repository name. Required when `RALPH_GIT_HOST=ado`. |
| `RALPH_LOG_LEVEL` | runner | `INFO` (default), `DEBUG`, `WARNING`. Controls stderr verbosity. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Every error-severity check passed. |
| `1` | At least one error-severity check failed. Pod must NOT start. |
| `2` | Internal failure (missing/malformed settings.json, mutually-exclusive CLI flags, unknown check name, `RALPH_GIT_HOST` unset or unknown). |

## Output

Stdout: single JSON document. Stderr: human summary (suppressed by
`--json`). Example:

```json
{
  "ok": true,
  "exit_code": 0,
  "summary": {"errors": 0, "warns": 0, "passes": 7, "skips": 1},
  "checks": [
    {"name": "permissions", "severity": "error", "status": "pass",
     "message": "permissions.allow covers all 7 required tools and 2 required skills."}
  ]
}
```

The `skips: 1` line reflects the non-dispatched host-auth check (e.g.
`ado_auth` when `RALPH_GIT_HOST=github`).

## How it is invoked

```bash
RALPH_GIT_HOST=github uv run python skills/ralph-doctor/scripts/check.py
RALPH_GIT_HOST=github uv run python skills/ralph-doctor/scripts/check.py --skip auth --json
RALPH_GIT_HOST=ado uv run python skills/ralph-doctor/scripts/check.py --strict --only permissions,hooks
```

Tests live at `tests/skills/test_ralph_doctor.py`.

## The seven checks

| Check | Severity | What it asserts | Runs when |
|---|---|---|---|
| `permissions` | error | `permissions.allow` covers Bash, Edit, Write, Read, Grep, Glob, Skill, and skills `pr`, `workitem-fetch` (wildcards honoured). | always |
<!-- ralph-doctor: ignore -->
| `hooks` | error | No active hook contains `AskUserQuestion`, `input(`, `read -p`, or `Read-Host`. `async: true` matches → warn. | always |
| `skills` | error | No installed skill's `SKILL.md` or `scripts/*.py` calls `AskUserQuestion` (heuristic substring scan). | always |
<!-- /ralph-doctor: ignore -->
| `mcp` | error | No MCP server requires OAuth / browser redirect (`oauth`, `--auth`, `--login`, `BROWSER`). | always |
| `auth` | error | Anthropic (or Bedrock if `RALPH_USE_BEDROCK=1`) auth resolves on cold start via a no-op API call. | always |
| `host_staging` | error | Staged `pr/SKILL.md` and `workitem-fetch/SKILL.md` have frontmatter `name:` equal to `pr-<RALPH_GIT_HOST>` and `workitem-fetch-<RALPH_GIT_HOST>`. | always |
| `github_auth` | error | `GET /user` returns 2xx (PAT works); `GET /repos/{GH_OWNER}/test-permissions` returns 404 (fine) or 2xx (also fine); 403 → fail (scopes). | when `RALPH_GIT_HOST=github` |
| `ado_auth` | error | `pullrequests/999999999` returns HTTP 404 (proves PAT auth + project routing). | when `RALPH_GIT_HOST=ado` |

## What this skill does NOT do

- It does not modify settings.json. Findings are read-only.
- It does not check whether `ralph-executor` itself is installed (Plan 12).
- It does not check git remote access (Plan 12 covers `git ls-remote`).
- It does not exercise `claude -p` itself — the auth probe is a cheap proxy.
- It does not stage skills itself — that's `host_select.py` in Plan 7. `host_staging` only verifies the result.

## Trade-offs

The `skills` check is heuristic. A skill that hides <!-- ralph-doctor: ignore -->`AskUserQuestion`<!-- /ralph-doctor: ignore -->
behind a dynamic call (`getattr(self, 'Ask' + 'UserQuestion')()`)
evades the substring scan. v2 may add AST-based scanning; v1
optimises for false-positive resistance by honouring
`<!-- ralph-doctor: ignore -->` markers in markdown and
`# noqa: ralph-doctor` in Python lines.
