---
id: STAGE-B-PLAN-20a-worktree
type: feature
status: current
severity: normal
attempts: 0
created_at: 2026-05-26T07:30:00+00:00
updated_at: 2026-05-27T01:37:09+00:00
depends_on: []
---

# Refactor executor from single-checkout branch-dance to two git worktrees

Current code in `loop.py` is full of `_ensure_on_queue_branch` and `_persist_iteration_writes` calls that swap branches mid-iteration just so `.ralph/current/<PBI>/HISTORY.md` is visible while the feature branch is checked out for code edits. The KNOWN ISSUE comment at `loop.py:247` flags this as deferred future work.

Two git worktrees from one clone eliminate the branch-dance:
- **Queue worktree** always on `ralph-queue`, executor reads/writes `.ralph/` here
- **Code worktree** always on the active `ralph/<PBI-ID>`, Claude reads/writes code here
- Shared object store — no duplication, no syncing cost

This refactor is Phase 1 of the larger KEDA pod work tracked in Issue #20. It is independently useful for the local runner — the branch-dance complexity goes away. The KEDA pod (Phase 2) stays as Issue #20 until below-90% unknowns (Bedrock IRSA, ROSA cluster reach) are resolved separately.

See `PLAN.md` for the full implementation plan.

## Acceptance criteria

- New `ralph_executor/worktree.py` with `ensure_worktree`, `list_worktrees`, `remove_worktree` helpers
- `_claim_pbi` in `loop.py` creates the feature branch as a worktree at `<repo>/.ralph-work/<PBI-ID>/` instead of `git checkout -b` in the same checkout
- `PROMPT.md` reads from `$RALPH_PBI_DIR/HISTORY.md` (absolute) instead of `.ralph/current/<id>/HISTORY.md` (cwd-relative); env var fallback keeps Stage A behaviour for any consumer that still relies on relative paths
- `claude_spawn` sets `cwd=<work_worktree>` and `RALPH_PBI_DIR=<queue_worktree>/.ralph/current/<id>/`
- `_persist_iteration_writes` simplifies — no more branch switching; just commit + push from the queue worktree
- `_ensure_on_queue_branch` becomes dead code and is removed
- Worktree cleanup on PBI completion (move to pending-pr / blocked / done) — orphan worktrees pruned
- Backwards compat for local-runner with no worktree set up: helpers detect single-checkout mode and behave as today; gated by config flag `use_worktrees` (default false in Stage A, switch to true here)
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/STAGE-B-PLAN-20a-worktree`
