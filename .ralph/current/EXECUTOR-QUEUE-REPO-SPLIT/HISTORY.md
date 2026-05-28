<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-28T10:15:00+00:00

- Task 0 spike: grep enumerated 28 files referencing `queue_branch`; categorised CONFIG/LOOP/CLI/TEST/OUT-OF-SCOPE (skills + docs deferred to PBI 2).
- Task 1: replaced `queue_branch` with required `queue_repo` field on `ExecutorConfig`. Validated via `parse_target_repo`. Updated `tests/executor/conftest.py` fixture, `test_config.py`, `test_config_toml.py`.
- Tests: `uv run pytest tests/executor/test_config.py tests/executor/test_config_toml.py` → 83 passed.
- Lint: `uv run ruff check` + `ruff format` clean on touched files.
- mypy on `config.py` clean; loop.py/cli.py still reference `cfg.queue_branch` — that's intentional, handled by Tasks 3–7.
- Commit: `668ccef config(executor): EXECUTOR-QUEUE-REPO-SPLIT — replace queue_branch with queue_repo`
- Next iteration: Task 2 — create `ralph_executor/queue_clone.py` with `ensure_queue_clone` + tests.

## Iteration 2 — 2026-05-28T11:00:00+00:00

- Task 2: created `ralph_executor/queue_clone.py` mirroring `target_clone.ensure_clone`. `ensure_queue_clone(workspace_root, queue_repo)` clones to `<workspace_root>/queue/` on first call; fetches + ff-only pulls `main` on subsequent calls. Raises `QueueCloneError` on any git failure (clone, fetch, or non-ff pull) with operator hint pointing at `gh auth login`.
- Tests: added `tests/executor/test_queue_clone.py` with 3 cases (first-call clones, second-call fetches + pulls new commit, bad-URL → QueueCloneError). Uses local bare git repos under tmp_path — no network required.
- `uv run pytest tests/executor/test_queue_clone.py` → 3 passed.
- Lint: `uv run ruff check` clean; `ruff format` applied.
- mypy: clean on `queue_clone.py`. Pre-existing `cfg.queue_branch` attr-defined errors in `loop.py`/`movements.py`/`cli.py` remain (intentional — Tasks 3–7 fix).
- Commit: forthcoming.
- Next iteration: Task 3 — wire `loop._pull_queue` to call `ensure_queue_clone` and remove `_ensure_on_queue_branch`.

## Iteration 3 — 2026-05-28T11:45:00+00:00

- Task 3: imported `ensure_queue_clone` into `loop.py`; replaced `_pull_queue` body with a single call to `ensure_queue_clone(cfg.workspace_root, cfg.queue_repo)`; collapsed `_queue_repo_root` to always return `cfg.workspace_root / "queue"`; deleted the `_ensure_on_queue_branch` function.
- Lifted three orphaned `_ensure_on_queue_branch(cfg)` callers (formerly in `_persist_iteration_writes`, `_run_ralph` after the push-conflict catch, and `iterate_once` after the claim cycle-detector branch) in the same commit — keeping the function would have required either a no-op stub or letting ruff fail `F821`. Plan flagged these for Task 4; pulling forward 3 line-deletions preserves a green lint gate at every commit.
- Added `test_pull_queue_calls_ensure_queue_clone` in `tests/executor/test_loop.py` (monkeypatches `loop.ensure_queue_clone`, asserts `_pull_queue(cfg)` calls it with `(workspace_root, queue_repo)`).
- Tests: `uv run pytest tests/executor/test_loop.py::test_pull_queue_calls_ensure_queue_clone tests/executor/test_queue_clone.py tests/executor/test_config.py tests/executor/test_config_toml.py` → 87 passed.
- Lint: `uv run ruff check ralph_executor/loop.py tests/executor/test_loop.py` → All checks passed. `ruff format` → no changes.
- mypy / pyright: many existing `cfg.queue_branch` attr-defined errors persist in `loop.py` — intentional; Tasks 4–7 sweep them.
- Next iteration: Task 4 — replace `cfg.queue_branch` references with `"main"` in `_persist_iteration_writes` push site (line ~305) plus the remaining `_pull_queue`-legacy lines and `queue/movements.py`.

## Iteration 4 — 2026-05-28T12:30:00+00:00

- Task 4: stripped every `cfg.queue_branch` reference inside the `_persist_iteration_writes` / `_run_ralph` push paths and the `queue/movements.py` mover.
  - `loop.py:_persist_iteration_writes` — docstring rewritten (no more worktree-vs-legacy fork), `queue_repo = _queue_repo_root(cfg)`, push to `branch="main"`, comment refreshed to point at `origin/main`.
  - `loop.py:iterate_once` — push-conflict catch block comment + log.warning purged of `cfg.queue_branch`, now references the queue repo's `main`.
  - `queue/movements.py` — module docstring rewritten (no branch-switch step), `_queue_repo` simplified to `cfg.workspace_root / "queue"` (dropped `queue_worktree_path` import), legacy `if not cfg.use_worktrees: git_ops.checkout(...)` block deleted, push to `branch="main"`, comments updated.
- Lines 565 + 588 in `_claim_pbi` / `_claim_pbi_worktree` still hold `cfg.queue_branch` — intentional, Task 5 owns those (legacy claim path deletion + worktree-branch arg removal).
- Tests: `uv run pytest tests/executor/test_loop.py::test_pull_queue_calls_ensure_queue_clone tests/executor/test_queue_clone.py tests/executor/test_config.py tests/executor/test_config_toml.py -q` → 87 passed. `tests/executor/test_movements.py` fails because `cfg_for_repo` fixture still points `workspace_root` at the real `~/ralph-workspaces` instead of a tmp dir (and the legacy `cfg.repo_path` queue-on-branch model). Per plan Task 4 Step 2, those failures are explicitly deferred to Task 9 (conftest fixture sweep) — not a regression of this commit.
- Lint: `uv run ruff check ralph_executor/loop.py ralph_executor/queue/movements.py` → clean. `ruff format` → no changes.
- mypy / pyright on touched files: only the Task-5-scoped `cfg.queue_branch` attr-defined errors at L565/L588 remain; all `_persist`/`_move`/`iterate_once` references are gone.
- Next iteration: Task 5 — drop the legacy `use_worktrees=False` branch out of `_claim_pbi` and the queue-worktree `ensure_worktree` call in `_claim_pbi_worktree`; add the `use_worktrees=False` rejection to `load_config`.
