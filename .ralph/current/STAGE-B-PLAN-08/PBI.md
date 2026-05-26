---
id: STAGE-B-PLAN-08
type: feature
status: current
severity: normal
attempts: 0
created_at: 2026-05-25T19:46:00+00:00
updated_at: 2026-05-26T23:48:55+00:00
depends_on: []
---

# Implement Plan: sweep logic (pending-pr observer + PR-feedback generation)

Execute the canonical implementation plan at:
`docs/superpowers/plans/2026-05-24-08-sweep-logic.md`

Read the plan in full. Follow its tasks in order. Each task ends with a commit. When all tasks complete, push the feature branch and create a PR via the staged `pr` skill.

## Acceptance criteria

- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/STAGE-B-PLAN-08`
