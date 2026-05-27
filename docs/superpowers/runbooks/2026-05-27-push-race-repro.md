# Manual repro: ralph loop survives concurrent writers

This runbook reproduces the LOOP-PERSIST-PUSH-RACE bug (loop crash on a
non-fast-forward push of `ralph-queue`) and confirms the fix
(`git_ops.push_with_rebase` + `iterate_once`'s `PushRebaseConflict`
handler).

## Steps

1. Start the ralph loop on a real PBI:
   ```
   uv run python -m ralph_executor
   ```
2. While the loop is mid-iteration, in a second terminal advance
   `origin/ralph-queue` out of band:
   ```
   git -C .ralph-work/queue commit --allow-empty -m "race"
   git -C .ralph-work/queue push origin ralph-queue
   ```
3. Watch the loop log.

## Expected (post-fix)

A single `WARNING iterate_once: push conflict on ralph-queue
(paths: <files>); skipping this iteration's persist, loop will retry
next round` line is emitted **only** when the racing commit conflicts
with the iteration's persist. In the more common non-conflicting case
the helper silently rebases the local persist commit onto the racing
remote tip and pushes; no WARNING fires.

The loop continues. The next iteration completes normally and the local
persist commit lands on top of the racing commit.

## Pre-fix behaviour (regression guard)

Before the fix the second push was rejected with
`! [rejected] ralph-queue -> ralph-queue (fetch first)`, the
`GitCommandError` propagated through `iterate_once` → `run_loop` →
`cli.main`, and the executor process exited. Resolution required an
operator to manually `git fetch origin ralph-queue && git rebase
origin/ralph-queue && git push origin ralph-queue` inside
`.ralph-work/queue` and restart ralph.
