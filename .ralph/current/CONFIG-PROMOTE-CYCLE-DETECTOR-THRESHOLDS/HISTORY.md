<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-28T01:21:37Z

- Promoted `same_file_min_prs` (default 10) and `same_file_window_hours` (default 24.0) to `ExecutorConfig` and TOML (`.ralph/config.toml`), with `RALPH_SAME_FILE_MIN_PRS` / `RALPH_SAME_FILE_WINDOW_HOURS` env overrides. Both validated > 0.
- Removed module-level `SAME_FILE_MIN_PRS` / `SAME_FILE_WINDOW` constants from `ralph_executor/safety/cycle_detector.py`; `DEFAULT_SAME_FILE_MIN_PRS` / `DEFAULT_SAME_FILE_WINDOW_HOURS` now live in `ralph_executor/config.py` (same pattern as `bash_max_timeout_ms` / `stale_days`).
- `evaluate_same_file_thrashing(events, now, *, min_prs, window_hours)` — kwargs default to the module-level config defaults so standalone callers still work; `evaluate_all(events, now, cfg=None)` reads `cfg.same_file_min_prs` / `cfg.same_file_window_hours` and forwards them through. Loop driver passes `cfg` in `_check_cycle_detector`.
- Documented new TOML keys in `setup_cmds.CONFIG_TOML_STUB`.
- Tests: `uv run pytest tests/safety/test_cycle_detector.py tests/executor/test_config_toml.py tests/executor/test_movements.py` green (82 passed). Full `uv run pytest` green (800 passed, 4 skipped). `uv run ruff check .` clean. `uv run ruff format --check .` clean. `uv run mypy ralph_executor scripts skills tests` clean.
- Notes: added 3 same_file override tests (lower / raise / window narrow) + 1 aggregator test asserting `evaluate_all` forwards `cfg.same_file_min_prs` through; 5 new `test_config_toml.py` cases cover defaults, TOML pickup, env override, and positive-validation rejection.
