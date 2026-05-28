---
id: STAGE-B-PLAN-13
type: feature
status: archive
severity: normal
attempts: 1
created_at: 2026-05-25T19:46:00+00:00
updated_at: 2026-05-28T14:40:00+00:00
depends_on: ["STAGE-B-PLAN-10", "STAGE-B-PLAN-12"]
target_repo: https://github.com/emp3thy/ralph
---

# Implement Plan: end-to-end smoke harness + 5 scenarios

> **CANCELLED 2026-05-28.** The canonical plan
> (`docs/superpowers/plans/2026-05-24-13-end-to-end-smoke.md`) was written
> assuming ADO infrastructure (`scripts.ado_client`, `skills/pr-ado`,
> `skills/workitem-fetch-ado`, `ADO_PAT`/`ADO_ORG_URL` env vars, `WI-####`
> PBI IDs, ADO REST fixtures). The repo built `pr-github` only; pr-ado
> is deferred. The plan is unimplementable as-written. Operator decision:
> cancel rather than rewrite. See STUCK.md for the full mismatch list.
> If smoke coverage is wanted later, brainstorm a fresh github-only
> smoke harness PBI from scratch.

Execute the canonical implementation plan at:
`docs/superpowers/plans/2026-05-24-13-end-to-end-smoke.md`

Read the plan in full. Follow its tasks in order. Each task ends with a commit. When all tasks complete, push the feature branch and create a PR via the staged `pr` skill.

## Acceptance criteria

- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/STAGE-B-PLAN-13`
