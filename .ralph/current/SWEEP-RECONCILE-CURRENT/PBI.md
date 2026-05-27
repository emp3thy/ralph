---
id: SWEEP-RECONCILE-CURRENT
type: feature
status: current
severity: normal
attempts: 0
created_at: 2026-05-27T18:00:00+00:00
updated_at: 2026-05-27T21:50:22+00:00
depends_on: []
---

# Sweep reconciles stale .ralph/current/ entries

Extend `ralph_executor/sweep/reconcile.py` with a filesystem-only janitor pass that deletes `.ralph/current/<PBI-ID>/` directories that lack `PBI.md` and have a sibling in `done/`, `blocked/`, or `pending-pr/`.

Closes the orphan-leak introduced by feature-branch HISTORY.md commits being replayed onto `ralph-queue` via `merge main`. After this PBI lands, the first sweep iteration clears the three current orphans (CONFIG-PROMOTE-SWEEP-KNOBS, STAGE-B-PLAN-08, SWEEP-RECONCILE-ORPHANS).

Pure filesystem — no host API calls. Safety gate: `PBI.md` presence wins, so an active claim is never deleted.

Spec: `docs/superpowers/specs/2026-05-27-sweep-reconcile-current-design.md`
Plan: `docs/superpowers/plans/2026-05-27-sweep-reconcile-current-plan.md`

See `PLAN.md` for the implementation plan pointer.

## Acceptance criteria

- `sweep/types.py` exports `CurrentReconcileAction` + `CurrentReconcileReport`
- `sweep/reconcile.py` exposes `reconcile_stale_current_one` + `reconcile_stale_current_all`
- `sweep/runner.py::run()` calls the iterator after the existing pending-pr loop
- `ralph-executor reconcile [--dry-run]` prints two sections
- After one sweep iteration following merge: `.ralph/current/` on `ralph-queue` contains only `STAGE-B-PLAN-10/` + `.gitkeep`
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/SWEEP-RECONCILE-CURRENT`
