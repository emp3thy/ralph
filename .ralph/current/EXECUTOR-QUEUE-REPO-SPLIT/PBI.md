---
id: EXECUTOR-QUEUE-REPO-SPLIT
type: feature
status: current
severity: high
attempts: 0
created_at: 2026-05-28T01:00:00+00:00
updated_at: 2026-05-28T09:32:59+00:00
depends_on: []
target_repo: https://github.com/emp3thy/ralph
---

# Executor reads queue from a separate repo (queue_repo)

PBI 1 of 2 in the queue-repo-split architecture change. Replace the executor's queue-as-a-branch model (`ralph-queue` branch on the executor source repo) with a queue-as-its-own-repo model. Executor clones `queue_repo` into `$RALPH_WORKSPACE/queue/` and operates on it directly.

Adds a `migrate-queue` CLI subcommand for the one-shot operator bootstrap of `emp3thy/ralph-queue` from the current queue state.

**Important operator gate after this PBI merges:**

1. Merge this PR.
2. Create empty `emp3thy/ralph-queue` repo on GitHub.
3. Run `uv run ralph-executor migrate-queue --source <old-queue-worktree> --target https://github.com/emp3thy/ralph-queue`.
4. Add `queue_repo = "https://github.com/emp3thy/ralph-queue"` to `~/.ralph/config.toml`.
5. Restart ralph-executor.

Without those steps, the executor will crash on next iteration (`queue_repo not configured` error). That's intentional — it forces the operator to complete the migration before any further iteration.

Spec: `docs/superpowers/specs/2026-05-28-queue-repo-split-design.md`
Plan: `docs/superpowers/plans/2026-05-28-executor-queue-repo-split-plan.md`

See `PLAN.md` for the implementation plan pointer.

## Acceptance criteria

- `ExecutorConfig.queue_repo` exists (required HTTPS URL via TOML or CLI; no env var).
- `ExecutorConfig.queue_branch` removed.
- `ralph_executor/queue_clone.py` exposes `ensure_queue_clone(workspace_root, queue_repo) -> Path`.
- `loop._pull_queue` calls `ensure_queue_clone`. `_ensure_on_queue_branch` deleted.
- Every `git_ops.push(..., cfg.queue_branch)` pushes to `"main"` instead.
- `cli.py` adds `migrate-queue` subcommand and a `--queue-repo` flag.
- `setup_cmds.init` prompts for `queue_repo` with a smoke clone.
- `use_worktrees=False` no longer supported; `load_config` raises a clear error if set.
- All existing tests pass; new tests cover `ensure_queue_clone`, `migrate-queue` subcommand (filter helper + push smoke), and the config validation.
- Plan-defined gates pass: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy ralph_executor scripts skills tests`.
- PR opened against `main` from `ralph/EXECUTOR-QUEUE-REPO-SPLIT`.
