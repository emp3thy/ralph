---
id: STAGE-B-PLAN-18-verifier
type: feature
status: inbox
severity: high
attempts: 0
created_at: 2026-05-26T07:30:00+00:00
updated_at: 2026-05-26T07:30:00+00:00
depends_on: []
---

# Real CI-green verifier before classifying pr_created

Today `classify_outcome` returns `pr_created` whenever `gh pr list --head ralph/<PBI>` returns a URL. That is not a verifier — it trusts Claude's self-report. Claude can delete failing tests, hardcode values to make CI green, push, and the executor moves the PBI to `pending-pr` as if it succeeded.

This PBI gates `pr_created` on **all required CI checks being green** for the PR. Otherwise the iteration stays `partial` (PBI stays in `current/`), Claude sees the failure summary on stderr, and the next iteration tries again.

See `PLAN.md` for the full implementation plan.

## Acceptance criteria

- `classify_outcome` returns `pr_created` ONLY when `gh pr checks <num> --required --json bucket,name` reports every required check has `bucket=="pass"`
- Pending checks → return `partial` (re-poll next iteration); never return `pr_created` optimistically
- Failed checks → return `partial` with the failed check names visible in `outcome.stderr` so Claude reads them on next iteration
- Polling: 6 attempts × 30 s = 3 min max wait per iteration; longer waits → next iteration
- New regression tests cover: all-pass, one-pending, one-failed, no-required-checks, gh-API-error paths
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/STAGE-B-PLAN-18-verifier`
