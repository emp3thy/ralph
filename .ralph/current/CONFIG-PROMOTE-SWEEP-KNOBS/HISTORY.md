<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-27T08:55:00+00:00

- Task 1: failing `test_config_toml.py` tests for `bot_author_email` + `stale_days` TOML promotion (layering, defaults, positivity validation). Commit `3d77481`.
- Task 2: added `DEFAULT_STALE_DAYS`, new `_TOML_KNOWN_KEYS`, `ExecutorConfig` fields (`bot_author_email: str = ""`, `stale_days: int = DEFAULT_STALE_DAYS` at the end of the dataclass to satisfy default-field ordering), two `_resolve_*` calls in `load_config()`, positivity guard. Updated `cfg_for_repo` fixture. Commit `c4bfa66`.
- Task 3: failing `test_loop.py` tests — `_run_sweep` must skip when `cfg.bot_author_email` empty; must pass `cfg` values (not env) to `SweepConfig`; must not contain `os.environ.get` reads for the two names. Commit `1cfa949`.
- Task 4: replaced `_run_sweep` body — read `cfg.bot_author_email` / `cfg.stale_days` directly; removed `os` import; updated docstring. Commit `57ba89b`.
- Task 5: appended `bot_author_email` + `stale_days` commented examples to `CONFIG_TOML_STUB` in `setup_cmds.py`. No existing snapshot test to update. Commit `ae5a7f9`. Format fix on `config.py` in `2114cdd`.
- Task 6: full suite green — `497 passed, 2 skipped`; `ruff check` + `ruff format --check` + `mypy` all clean; integration tests in `test_loop_integration.py` rewritten to use `dataclasses.replace(cfg, bot_author_email=..., stale_days=...)` instead of the retired env-only path. Commit `86041c3`.
- Tests: green (`uv run pytest tests/`).
- Notes: SimpleNamespace stand-in needed in `test_run_sweep_passes_cfg_values_to_sweep_config` because the lambda from the plan returned `None` and `_run_sweep` accesses `.pbis_scanned` on the result.

## Iteration 2 — 2026-05-27T09:00:00+00:00 — PR created

- PR: https://github.com/emp3thy/ralph/pull/30
- Branch: ralph/CONFIG-PROMOTE-SWEEP-KNOBS
- Title: CONFIG-PROMOTE-SWEEP-KNOBS: promote bot_author_email + stale_days to TOML

