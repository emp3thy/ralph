<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Claim failed — 2026-05-28T11:46:29+00:00

PBI BUG-CLAIM-RACE-UNCOMMITTED-INBOX-DIR missing target_repo field

## Iteration 1 — 2026-05-28T15:00:00+00:00

Hypothesis: `movements._move` calls `git_ops.mv` against the queue clone with a source dir that an external writer staged on disk but did not yet `git commit`. `git mv` then errors `fatal: source directory is empty` (exit 128) because no tracked files live under the path. The `GitCommandError` propagates out of `_claim_pbi` → `iterate_once` and kills the executor.

Plan:
- Add `git_ops.ls_files(repo, path) -> list[str]` (tracked files under `path`).
- Add `movements.UncommittedSource(RuntimeError)` (sibling of `QueueMovementError`).
- In `movements._move`, before `git_ops.mv`, call `ls_files(queue_repo, src)`; if empty, raise `UncommittedSource(pbi.id)`.
- Extend `IterationOutcome` with `"uncommitted_source"`.
- In `loop.iterate_once`, catch `UncommittedSource` around `_claim_pbi`, log WARNING, return `IterationResult(outcome="uncommitted_source", pbi_id=picked.id)`.
- Unit test in `tests/executor/test_movements.py`: write dir + entry on disk without staging → `_move` raises `UncommittedSource`.
- Integration test in `tests/executor/test_loop.py`: external writer creates inbox dir without committing → first `iterate_once` returns `uncommitted_source`; after writer commits + pushes, next `iterate_once` claims cleanly.

Outcome:
- All steps applied.
- Tests: 989 passed, 4 skipped.
- Lint/format/mypy: clean.

Root cause: `FilesystemQueueSource._list_pbis` walks `.ralph/inbox/` by filesystem `iterdir()` with no git-tracked filter, so an external writer's mid-add (dir on disk, no `git commit` yet) is selected and `git mv` exits 128 with `fatal: source directory is empty`, propagating `GitCommandError` out of `_claim_pbi` and killing the loop.
Fix: `git_ops.ls_files` helper + `movements._move` raises `UncommittedSource` when the source has no tracked files; `loop.iterate_once` catches it around `_claim_pbi`, logs a WARNING, and returns `IterationResult(outcome="uncommitted_source", pbi_id=picked.id)` so the next iteration retries once the writer's commit lands.

## Iteration 1 — 2026-05-28T15:30:00+00:00 — PR created

- PR: !51 (https://github.com/emp3thy/ralph/pull/51)
- Branch: ralph/BUG-CLAIM-RACE-UNCOMMITTED-INBOX-DIR
- Title: BUG-CLAIM-RACE-UNCOMMITTED-INBOX-DIR: skip uncommitted inbox dir instead of crashing

