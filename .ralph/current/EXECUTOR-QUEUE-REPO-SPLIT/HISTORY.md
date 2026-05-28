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
