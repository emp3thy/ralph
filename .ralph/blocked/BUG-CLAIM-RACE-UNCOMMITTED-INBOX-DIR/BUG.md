---
id: BUG-CLAIM-RACE-UNCOMMITTED-INBOX-DIR
type: bug
status: blocked
severity: high
attempts: 0
created_at: 2026-05-28T08:30:00+00:00
updated_at: 2026-05-28T11:46:29+00:00
depends_on: []
---

# Claim crashes when inbox PBI dir exists on disk but is not yet committed

`ralph_executor/loop.py::_claim_pbi_worktree` calls `move_inbox_to_current` → `movements._move` → `git_ops.mv`. The source path is selected by `FilesystemQueueSource._list_pbis`, which walks `.ralph/inbox/` with `state_dir.iterdir()` and has no git-tracked filter. If an external writer (operator adding a PBI in another shell / another Claude session) has written the entry file to disk but not yet committed it on `ralph-queue`, the executor selects the dir, then `git mv` fails with `fatal: source directory is empty` (no tracked files under the source), exit 128. `GitCommandError` propagates out of `iterate_once` and **kills the ralph process**.

Observed crash 2026-05-28 08:14:31: another Claude session was mid-add of `EXECUTOR-EXIT-WHEN-IDLE-DEFAULT`; the executor scanned inbox at 08:14:30, attempted `git mv inbox/.../ current/.../` at 08:14:31, failed because files were still uncommitted; the other session committed at 08:14:37 (6s later). The race window is the gap between filesystem write and `git commit` in the external writer.

Same pattern as `LOOP-PERSIST-PUSH-RACE`: a concurrent writer on `ralph-queue` causes an unhandled `GitCommandError` that should be a recoverable transient instead.

## Fix

Add an `UncommittedSource` exception to `ralph_executor/queue/movements.py` (sibling of `QueueMovementError`). In `_move`, before calling `git_ops.mv`, run `git ls-files <src>` against the queue worktree; if the output is empty, raise `UncommittedSource(pbi_id)`. In `loop.iterate_once`, catch `UncommittedSource` around `_claim_pbi` the same way `PushRebaseConflict` is caught — log a WARNING and return `IterationResult(outcome="uncommitted_source", pbi_id=picked.id)`. The next iteration re-scans inbox and will succeed once the external writer's commit lands.

Add a `git_ops.ls_files(repo, path) -> list[str]` helper so movements doesn't reach into `_run_git` directly.

## Reproduction

1. In one shell, start `ralph-executor` with worktrees enabled and inbox empty.
2. In a second shell, create the dir + entry file on `ralph-queue` but DO NOT commit yet:
   ```
   mkdir .ralph-work/queue/.ralph/inbox/TEST-RACE
   # write PBI.md / BUG.md with valid frontmatter
   ```
3. Wait for the executor's next sweep tick. It selects `TEST-RACE`, calls `git mv`, the loop crashes with:
   ```
   git command ['git', 'mv', '.ralph\inbox\TEST-RACE', '.ralph\current\TEST-RACE'] exited 128: fatal: source directory is empty
   ```

## Acceptance criteria

- `ralph_executor/queue/movements.py::UncommittedSource` exists; `_move` raises it when `git ls-files <src>` is empty
- `git_ops.ls_files` helper exists and is the only git surface used by `movements`
- `loop.iterate_once` catches `UncommittedSource` around `_claim_pbi`, logs WARNING, returns `IterationResult(outcome="uncommitted_source", pbi_id=...)`
- New unit test in `tests/queue/test_movements.py` covers: write a dir with entry file on disk without staging → `_move` raises `UncommittedSource`
- New integration test in `tests/test_loop.py` covers: external writer creates inbox dir without committing → `iterate_once` returns `outcome="uncommitted_source"`, loop does not crash, next iteration (after commit lands) claims cleanly
- Repro steps 1-3 above produce a WARNING (not a crash); next iteration completes cleanly once the writer commits
- `uv run pytest` passes
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/BUG-CLAIM-RACE-UNCOMMITTED-INBOX-DIR`

## Out of scope

- Filtering `_list_pbis` itself by git-tracked status (layering — keeps filesystem.py git-free). The `_move` guard is sufficient: even if `_list_pbis` returns an uncommitted dir, the claim fails gracefully and retries.
- Cross-state races (sweep, pending-pr → done). `move_current_to_pending_pr` and `move_current_to_blocked` operate on dirs the executor itself just wrote and committed, so the race surface does not exist there in practice; the `_move` guard still covers them defensively.
