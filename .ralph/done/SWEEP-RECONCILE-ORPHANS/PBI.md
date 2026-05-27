---
id: SWEEP-RECONCILE-ORPHANS
type: feature
status: pending-pr
severity: normal
attempts: 0
created_at: 2026-05-27T00:02:00+00:00
updated_at: 2026-05-27T11:37:50+00:00
depends_on: []
---

# Sweep auto-reconciles orphan pending-pr/ entries

Sweep today reports `"PR-LINK.md is missing; cannot determine PR id"` for every `pending-pr/<PBI-ID>/` directory without a `PR-LINK.md` file. 8 such orphans exist as of 2026-05-27 (manual gh squash-merges during babysit + older PBIs predating PR-LINK.md). Sweep gives up instead of reconciling via the host API.

This PBI adds:
- `lookup_by_branch.py` sub-op to `skills/pr-github/scripts/` (6th op, sibling of `show.py` etc.) — looks up a PR by source branch
- `ralph_executor/sweep/reconcile.py` module — `reconcile_orphan` + `reconcile_all`, mapping lookup result to `done/`/`blocked/`/`inbox/` per a 5-state table
- `ralph-executor reconcile [--dry-run]` CLI subcommand for one-shot manual cleanup
- Sweep auto-calls `reconcile_orphan` when it finds an orphan (in `sweep/runner.py:run()` at the missing-PR-LINK branch)

PR-LINK.md is written retroactively on terminal moves (done/blocked) and on stay-open cases (so the next sweep iteration uses the normal path).

ADO version of `lookup_by_branch` is deferred — separate follow-up tracker issue on `emp3thy/ralph` blocks on Phase 2 of `docs/superpowers/plans/2026-05-24-05-ado-pr-skill.md`.

Spec: `docs/superpowers/specs/2026-05-27-sweep-reconcile-orphans-design.md`
Plan: `docs/superpowers/plans/2026-05-27-sweep-reconcile-orphans-plan.md`

See `PLAN.md` for the implementation plan pointer.

## Acceptance criteria

- `skills/pr-github/scripts/lookup_by_branch.py` exists with JSON contract from spec, exit codes 0/2/3
- `ralph_executor/sweep/reconcile.py` exposes `reconcile_orphan` + `reconcile_all`
- Sweep auto-reconciles orphans; the "PR-LINK.md is missing" sweep error no longer fires
- `ralph-executor reconcile [--dry-run]` exists
- PR-LINK.md written retroactively on terminal moves (done/blocked) and on stay-open cases
- The 8 current orphans clear on first reconcile run (verified via `ralph-executor reconcile --dry-run` smoke at end of Task 7)
- `responses` library used for skill HTTP mocking (no network in CI)
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/SWEEP-RECONCILE-ORPHANS`
- Follow-up GitHub issue filed on `emp3thy/ralph` tracking ADO `lookup_by_branch` (blocked on Phase 2 pr-ado plan)
