# Loop's persist-iteration push must fetch+rebase first

**Date:** 2026-05-27
**Scope:** `ralph_executor/loop.py::_persist_iteration_writes` (and the queue-movement push paths that share the same shape). The push to `origin/ralph-queue` must tolerate concurrent writers by fetching the remote tip and rebasing the local commit on top before pushing. Today it pushes blind; on divergence the push is rejected and `GitCommandError` bubbles out of `run_loop` as an unhandled exception, killing the executor process.
**Status:** Design — pending review.

## Background

Observed incident, 2026-05-27 17:40:29, on the ralph self-host loop:

```
ralph_executor.git_ops.GitCommandError: git command ['git', 'push', 'origin', 'ralph-queue'] exited 1: To https://github.com/emp3thy/ralph.git
 ! [rejected]        ralph-queue -> ralph-queue (fetch first)
error: failed to push some refs to 'https://github.com/emp3thy/ralph.git'
hint: Updates were rejected because the remote contains work that you do not have locally.
```

The crashing call site is `loop.py:292`, inside `_persist_iteration_writes`. Sequence reconstructed from the run log:

1. 17:34:09 — iteration spawns claude for STAGE-B-PLAN-12.
2. (concurrent) — operator commits `6997b70 docs(spec): add target_repo field to PBI frontmatter` directly to `origin/ralph-queue` at 17:37:27.
3. 17:40:27 — claude finishes; loop assembles persistence commit `aff0f08` on the local queue worktree (touching `.ralph/current/STAGE-B-PLAN-12/HISTORY.md`).
4. 17:40:29 — loop calls `git push origin ralph-queue`; remote rejects because local hasn't seen `6997b70`.
5. `GitCommandError` propagates through `iterate_once` → `run_loop` → `cli.main`; CLI logs "unhandled exception" and the process exits.

Resolution required an operator to manually `git fetch origin ralph-queue && git rebase origin/ralph-queue && git push origin ralph-queue` inside `.ralph-work/queue`. Then restart ralph.

The same exposure exists in every queue movement that ends with `git_ops.push(queue_repo, cfg.queue_branch)`:

- `move_inbox_to_current`
- `move_current_to_pending_pr`
- `move_current_to_blocked`
- (in `sweep/runner.py`) the sweep's per-iteration commit + push.

Any concurrent writer (a human queueing a PBI via `ralph-add`, a second ralph instance on a different repo, a manual commit to `ralph-queue` via the github UI) can race against the loop and trigger the same crash.

## Decision

Replace the bare push in queue-mutation paths with a fetch-rebase-push helper. The helper:

1. `git fetch origin <branch>` — refresh the remote-tracking ref without merging.
2. If local is already an ancestor of `origin/<branch>` (no local commits ahead), exit early — nothing to push.
3. If `origin/<branch>` is ahead and the local commits don't conflict with the new remote commits, `git rebase origin/<branch>` — replay local commits on top.
4. `git push origin <branch>` — now fast-forward, should succeed.
5. If the rebase has conflicts (file-level overlap between local persist commit and remote commits), abort the rebase and raise a typed error that `iterate_once` catches and treats as a recoverable hiccup (skip the iteration's push, log a warning, continue the loop).

Chosen over alternatives:

- **`git push --force-with-lease`**: safer than `--force` but still asymmetric — wins races by overwriting concurrent writers, which is exactly the wrong direction (the loop's commit is the side that should yield, not the side that just got `target_repo` added to a spec). Rejected.
- **Locking via a sentinel file on the branch**: introduces its own race + GC problem; doesn't fit the ralph model where multiple ralph instances on different repos all push to the same shared queue branch concept.
- **One queue branch per repo**: bigger refactor, separate PBI (`STAGE-B-PLAN-10` is in flight on exactly this direction). Out of scope here; this PBI is a defensive fix that benefits all future architectures.

## Architecture

### New helper in `git_ops.py`

```python
def push_with_rebase(
    repo: Path,
    *,
    remote: str,
    branch: str,
) -> None:
    """Fetch ``remote/branch``, rebase the current branch onto it if needed,
    then push. Raises ``PushRebaseConflict`` on rebase conflicts (caller
    decides whether to abort the iteration or surface to the operator)."""
```

Internally:

- `_run_git(repo, "fetch", remote, branch)` — non-FF safe, always succeeds unless network/auth dies.
- `_run_git(repo, "rev-list", "--count", "--left-right", f"HEAD...{remote}/{branch}")` — split into `(ahead, behind)`.
- If `behind == 0`: skip rebase. If `behind > 0`: `_run_git(repo, "rebase", f"{remote}/{branch}")`.
  - On non-zero exit from rebase: `_run_git(repo, "rebase", "--abort")` then raise `PushRebaseConflict`.
- `_run_git(repo, "push", remote, branch)`.

`PushRebaseConflict` is a new exception class in `git_ops.py`, sibling of `GitCommandError`. It carries the list of conflicted paths (parsed from `git diff --name-only --diff-filter=U` between the fetch and the abort) so the caller can log them.

### Call-site updates

Each queue movement and the sweep persistence path swap `git_ops.push(...)` for `git_ops.push_with_rebase(...)`:

- `ralph_executor/queue/movements.py::_move` (covers all three move helpers).
- `ralph_executor/loop.py::_persist_iteration_writes`.
- `ralph_executor/sweep/runner.py` — wherever it currently pushes after a queue update.

### `iterate_once` error handling

`iterate_once` (in `loop.py`) catches `PushRebaseConflict` specifically and:

1. Logs a `WARNING` with the conflict path list.
2. Does NOT mutate the loop's PBI state or sidecar (the persistence commit was abandoned; the work itself is still in the work worktree and will be re-tried next iteration).
3. Returns a `LoopResult` with `outcome="push_conflict"` so the operator's structured logs surface it.
4. Continues the loop — does not bubble to `run_loop`, does not exit.

`GitCommandError` from `push_with_rebase` for non-conflict reasons (network, auth) keeps its existing semantics: propagate, crash, operator intervenes. The new behaviour is ONLY for the divergence-race case.

## Error handling

| Failure | Behaviour |
|---|---|
| `origin/ralph-queue` ref unreachable (network) | `GitCommandError` from the inner fetch — same as today, propagates. |
| Auth failure pushing | `GitCommandError` from push — same as today, propagates. |
| Rebase non-zero with conflict markers | `PushRebaseConflict`; `iterate_once` logs + skips; loop continues. |
| Rebase non-zero without conflicts (corrupted repo state) | The rebase abort itself fails; outer `GitCommandError` propagates as a hard crash (operator intervention required — repo state is unrecoverable from inside the loop). |
| Push fast-forward rejected even after rebase (somebody pushed between rebase + push) | Retry the helper once, then propagate on second failure. |

The one-retry is bounded: if the second push also races we crash on purpose — that's evidence of >2 writers in <5 seconds, which is a real human problem worth interrupting on.

## Testing

`tests/executor/test_git_ops_push_rebase.py` — new file:

- Two local git repos: a bare "remote" and a working "local". Helper points local at the bare remote as `origin`.
- `push_with_rebase` when local is N commits ahead, remote is 0 ahead → push succeeds, no rebase.
- `push_with_rebase` when local is 1 ahead, remote is 1 ahead on a non-overlapping path → rebase runs, push succeeds, local HEAD's commit is now on top of the remote commit.
- `push_with_rebase` when local is 1 ahead, remote is 1 ahead on an overlapping path → rebase fails, `PushRebaseConflict` raised, local HEAD restored to pre-rebase state via `rebase --abort`.
- `push_with_rebase` when network fails (point `origin` URL at a non-existent file path) → `GitCommandError`.
- `push_with_rebase` with one race during the helper (monkeypatch `_run_git` to inject a fake conflict on first push, succeed on retry) → succeeds on second try.

`tests/executor/test_loop_iteration.py` (modify existing iteration tests):

- Integration: inject a `PushRebaseConflict` from the monkeypatched helper at `_persist_iteration_writes` → `iterate_once` returns `LoopResult(outcome="push_conflict")`; loop does not crash; next iteration re-runs cleanly.

`tests/executor/test_movements.py`:

- Each `move_*` helper, when push races once, completes successfully on the rebase path.

## Acceptance

- `git_ops.push_with_rebase` exists with the signature above.
- `PushRebaseConflict` exists in `git_ops.py`.
- `_persist_iteration_writes` and every `move_*` helper use `push_with_rebase` instead of bare `push`.
- Sweep's per-iteration push uses `push_with_rebase`.
- `iterate_once` catches `PushRebaseConflict` and returns `outcome="push_conflict"` without crashing.
- pytest, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy ralph_executor scripts skills tests` all green.
- Reproduction recipe (manual ack in PR description): in one terminal start the loop on a feature PBI; in another terminal `git commit --allow-empty + git push` to `origin/ralph-queue`; loop survives, does not crash, picks up the remote commit on next iteration.

## Out of scope

- Concurrent claude spawns on the same PBI — not the bug we're fixing.
- Replacing the single shared queue branch with one-per-repo (architectural; separate PBI track).
- Detecting two-ralph-on-one-queue race (legitimate use case under STAGE-B-PLAN-10's design; this fix just makes it safe).
- Rebasing the feature branch in `.ralph-work/repo-<id>/` — different worktree, different ref, no race exposure.

## Risks

| Risk | Mitigation |
|---|---|
| Rebase reorders local commit, breaks bisect-ability of "persist iteration N" semantics | Persist commit is independent (single-file HISTORY.md append); rebase doesn't change its content, only its parent. Bisect history of HISTORY.md is preserved. |
| Helper hides a real divergence problem by silently rebasing | Helper logs INFO with the count of fast-forwarded remote commits per call; operator's structured logs show "rebased N commits before push" if it ever happens. |
| Two ralph instances on the same repo race indefinitely (push-conflict-skip → re-iterate → push-conflict-skip ...) | Future PBI: per-repo loop lock on a sidecar file. Out of scope here; the one-retry above bounds the per-iteration cost. |
| `--force-with-lease` accidentally introduced by a future PR | Add a ruff custom rule or a CI grep step that forbids `--force` / `--force-with-lease` in `git_ops.py`. (Optional follow-up tracker; not blocking this PBI.) |

## depends_on

None. Touches `git_ops.py`, `loop.py`, `queue/movements.py`, `sweep/runner.py` — does not overlap with `STAGE-B-PLAN-10` (which is on a separate branch and reshapes the orchestrator) or `SWEEP-RECONCILE-CURRENT` (touches `sweep/reconcile.py` + types).
