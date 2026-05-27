<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

---
STUCK -- moved to blocked/
reason: # STUCK — STAGE-B-PLAN-13

## What I tried

- Iteration 1: Read `prompt/PROMPT.md`, `HISTORY.md` (empty), `PBI.md`, `PLAN.md`, and the canonical plan at `docs/superpowers/plans/2026-05-24-13-end-to-end-smoke.md` (2130 lines). Inventoried the current repo state under `scripts/`, `skills/`, and `tests/` to confirm what backing infrastructure exists.

## What is blocking me

The canonical plan is built end-to-end on Azure DevOps (ADO) infrastructure
that does not exist in this repository. Concrete mismatches found in the
plan that block execution of essentially every task from Task 2 onward:

1. **`scripts.ado_client.AdoClient` does not exist.** The plan's
   `e2e_claude_stub.py` (Task 4) imports it directly
   (`from scripts.ado_client import AdoClient`). The repo provides
   `scripts/gh_client.py` only — there is no `scripts/ado_client.py`.
2. **`ado-pr` skill does not exist.** The plan repeatedly references the
   `ado-pr` skill and its `create-pr` operation (PROMPT.md guidance, Task
   6 commentary, README runbook). The repo has `skills/pr-github/` only;
   there is no `skills/pr-ado/`.
3. **`workitem-fetch-ado` skill does not exist.** The plan assumes the
   ADO work-item GET shape (e.g. `_apis/wit/workitems/{id}` returning
   `fields.System.WorkItemType`, `Microsoft.VSTS.TCM.ReproSteps`, etc.).
   `ralph-add` in this repo dispatches to `workitem-fetch-github` or
   `workitem-fetch-mock`. There is no ADO variant.
4. **All five mock-ADO REST endpoints are ADO-only** (`pullRequests`,
   `threads`, `threads/{id}/comments`, work-item GET, PATCH thread
   status). The fixtures (`feature_healthz.json`, `bug_irsa.json`, etc.)
   are in ADO JSON shape, the env vars are `ADO_PAT` / `ADO_ORG_URL` /
   `ADO_PROJECT`, and the PBI IDs are `WI-####`. The repo's PBI IDs are
   GitHub issue numbers, the env vars are `GH_TOKEN` / `GH_OWNER`, and
   the API surface is the GitHub REST + GraphQL API used by
   `gh_client.py`.
5. **PBI frontmatter has `target_repo: https://github.com/...`** —...[truncated]
