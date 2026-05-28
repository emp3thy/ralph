# STUCK — STAGE-B-PLAN-13

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
5. **PBI frontmatter has `target_repo: https://github.com/...`** —
   STAGE-B-PLAN-19 (commit e20d80a) added a GitHub repo URL field, and
   commit 8e68e5d (STAGE-B-PLAN-12) explicitly delivered "ROSA Docker
   image (Phase 1: github variant)". The repo is being shipped GitHub-only
   for v1; ADO is a deferred host (referenced in `host_select.py` but
   without backing skill/script artifacts).

Task 1 Step 1 (preflight) lists test files that mostly do exist
(`test_ralph_cancel.py`, `test_ralph_promote.py`, `test_ralph_triage.py`,
`tests/executor/`, `test_ralph_doctor.py`), so the preconditions check
itself would pass — but every subsequent task (fixtures, claude stub,
conftest, all five scenarios, README, gate) is wired through ADO
constructs that have no implementation to bind to.

Mechanically adapting the plan in-flight — rewriting every ADO reference
to its GitHub equivalent, swapping `AdoClient` for `GhClient`, recasting
the work-item fixtures from ADO `System.*` fields to GitHub issue JSON,
replacing the `threads`/`comments` REST mocks with GitHub review-thread
GraphQL mocks, redoing the PR-feedback scenario around `pr-github`'s
status verbs (`fixed`/`closed` vs ADO's `fixed`/`active`/`wontFix`/
`byDesign`), and propagating those changes through all eleven fixture
files plus the conftest and five scenarios — is a substantial design
exercise, not a "task per iteration" execution. It crosses the
plan-vs-execute boundary described in standing instructions and is
outside the per-iteration "small reviewable step" contract.

## What would unblock me

One of the following human decisions:

- **Rewrite the canonical plan for the GitHub host** at
  `docs/superpowers/plans/2026-05-24-13-end-to-end-smoke.md` so every
  task names extant artifacts (`gh_client.GhClient`, `pr-github`,
  `workitem-fetch-github`, GitHub issue JSON shapes, GitHub REST/GraphQL
  endpoints, `GH_TOKEN`/`GH_OWNER` env vars, GitHub-style PBI IDs and
  thread status verbs). The replanned tasks should mirror the existing
  GitHub test patterns already in `tests/skills/test_pr_github.py` and
  `tests/skills/test_workitem_fetch_github.py`.
- **OR** materialise the missing ADO host first
  (`scripts/ado_client.py`, `skills/pr-ado/`, `skills/workitem-fetch-ado/`,
  ADO support in `ralph-add` and the executor's sweep) as a prerequisite
  plan, then run this plan against the new ADO surface unchanged.
- Either way: confirm whether the smoke harness should target one host
  (GitHub), both hosts (parameterised by `RALPH_GIT_HOST`), or be deferred
  until ADO ships.

## My confidence with help

85% — if the plan is rewritten in GitHub terms (or, alternatively, if a
parameter-driven version targets both hosts with GitHub as the default
mocked path), the per-task scaffolding (throwaway repo fixture, mock HTTP
server, canned claude binary, five scenarios, README, gate) is a
mechanical translation of the existing structure and well within the
per-iteration budget. The remaining 15% covers integration friction with
the executor's `--iterations` flag (called out as the most likely friction
point in the plan's own self-review, item 3) and the cycle-detector event-
log shape (Plan 9 details that I have not yet read).
