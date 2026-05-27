<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-27T18:00:00+00:00

- Task 1: added `git_ops.push_with_rebase` + `PushRebaseConflict`; new
  `tests/executor/test_git_ops_push_rebase.py` (6 tests, all green:
  no-advance, non-conflicting advance, conflict-raises +
  pre-rebase-HEAD restored, network failure, no-local-commits no-op,
  one-retry-on-race via monkeypatched `_run_git`).
- Task 2: wired `push_with_rebase` into the two real production push
  sites — `queue/movements._move` (covers `move_inbox_to_current`,
  `move_current_to_pending_pr`, `move_current_to_blocked`) and
  `loop._persist_iteration_writes`. Sweep runner has no `git_ops.push`
  call (sweep only writes filesystem; commits flow through the loop's
  persist path), so the spec's "5 call sites" count resolves to 2
  production sites in the current codebase — recorded in the commit
  message. Added race-survival integration test:
  `test_move_inbox_to_current_survives_concurrent_remote_advance`.
- Task 3: extended `IterationOutcome` Literal with `"push_conflict"`;
  `iterate_once` catches `PushRebaseConflict` from the persist path,
  logs a `WARNING` with the conflict paths, returns
  `IterationResult(outcome="push_conflict", pbi_id=...)` instead of
  letting the exception kill `run_loop`. Test
  `test_iterate_once_recovers_from_push_conflict` monkeypatches
  `_persist_iteration_writes` to raise and asserts the recovered
  outcome.
- Task 4: runbook
  `docs/superpowers/runbooks/2026-05-27-push-race-repro.md`; full
  `uv run pytest` (623 passed, 2 skipped); `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy ralph_executor scripts
  skills tests` all green.
- Root cause: `loop.py::_persist_iteration_writes` and
  `queue/movements.py::_move` called `git_ops.push(queue_repo,
  queue_branch)` blind; a concurrent writer to `origin/ralph-queue`
  triggered `! [rejected] (fetch first)` and `GitCommandError`
  propagated through `iterate_once → run_loop → cli.main`, exiting
  the executor.
- Fix: new `push_with_rebase` helper (fetch → rebase iff behind > 0 →
  push, with one retry; `rebase --abort` + `PushRebaseConflict` on
  conflict), wired into the two push sites, and
  `iterate_once` catches `PushRebaseConflict` to return
  `outcome="push_conflict"` instead of crashing.
- Tests: green (623 passed). Ruff/format/mypy: clean.
- Notes for next iteration: PR-ready.

## Iteration 1 — 2026-05-27T18:00:00+00:00 — PR created

- PR: #36 — https://github.com/emp3thy/ralph/pull/36
- Branch: ralph/LOOP-PERSIST-PUSH-RACE
- Title: LOOP-PERSIST-PUSH-RACE: loop survives concurrent ralph-queue writers
