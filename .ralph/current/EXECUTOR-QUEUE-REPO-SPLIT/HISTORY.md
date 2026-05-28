<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-28T10:15:00+00:00

- Task 0 spike: grep enumerated 28 files referencing `queue_branch`; categorised CONFIG/LOOP/CLI/TEST/OUT-OF-SCOPE (skills + docs deferred to PBI 2).
- Task 1: replaced `queue_branch` with required `queue_repo` field on `ExecutorConfig`. Validated via `parse_target_repo`. Updated `tests/executor/conftest.py` fixture, `test_config.py`, `test_config_toml.py`.
- Tests: `uv run pytest tests/executor/test_config.py tests/executor/test_config_toml.py` → 83 passed.
- Lint: `uv run ruff check` + `ruff format` clean on touched files.
- mypy on `config.py` clean; loop.py/cli.py still reference `cfg.queue_branch` — that's intentional, handled by Tasks 3–7.
- Commit: `668ccef config(executor): EXECUTOR-QUEUE-REPO-SPLIT — replace queue_branch with queue_repo`
- Next iteration: Task 2 — create `ralph_executor/queue_clone.py` with `ensure_queue_clone` + tests.
