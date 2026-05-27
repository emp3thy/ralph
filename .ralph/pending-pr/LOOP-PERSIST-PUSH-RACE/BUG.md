---
id: LOOP-PERSIST-PUSH-RACE
type: bug
status: pending-pr
severity: high
attempts: 0
created_at: 2026-05-27T18:00:00+00:00
updated_at: 2026-05-27T17:29:26+00:00
depends_on: []
---

# Loop's persist push must fetch+rebase first

`ralph_executor/loop.py::_persist_iteration_writes` pushes `ralph-queue` blind. When `origin/ralph-queue` advances between the iteration's start and its persist time (concurrent operator commit, second ralph instance, or a queued out-of-band commit), the push is rejected as non-fast-forward and `GitCommandError` propagates as an unhandled exception, **killing the ralph process**.

Observed crash 2026-05-27 17:40:29: a docs(spec) commit landed on `origin/ralph-queue` at 17:37:27 while STAGE-B-PLAN-12's iteration was running; the post-iteration push failed; the loop crashed.

Same exposure exists in every queue movement (`move_inbox_to_current`, `move_current_to_pending_pr`, `move_current_to_blocked`) and in sweep's per-iteration push.

Add a `push_with_rebase(repo, remote, branch)` helper to `git_ops.py` that fetches, rebases the local commit onto the remote if needed, then pushes. Add a `PushRebaseConflict` exception for the conflict case. `iterate_once` catches `PushRebaseConflict` → returns `LoopResult(outcome="push_conflict")` instead of crashing.

Spec: `docs/superpowers/specs/2026-05-27-loop-persist-push-race-design.md`
Plan: `docs/superpowers/plans/2026-05-27-loop-persist-push-race-plan.md`

See `PLAN.md` for the implementation plan pointer.

## Reproduction

1. Start ralph loop on a real PBI.
2. While iterating, in a second terminal: `git -C .ralph-work/queue commit --allow-empty -m "race" && git -C .ralph-work/queue push origin ralph-queue`.
3. The loop crashes with `git command ['git', 'push', 'origin', 'ralph-queue'] exited 1: ! [rejected] (fetch first)`.

## Acceptance criteria

- `git_ops.push_with_rebase` + `PushRebaseConflict` exist
- All 5 queue-push call sites use `push_with_rebase`
- `iterate_once` catches `PushRebaseConflict` → `outcome="push_conflict"`, loop continues
- Repro recipe in step 1-3 above produces a WARNING (not a crash); next iteration completes cleanly
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/LOOP-PERSIST-PUSH-RACE`
