<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-27T22:53:40Z

- Step 1: Added `claude_permission_mode` to `ExecutorConfig` (default `"bypassPermissions"`, TOML key `claude_permission_mode`, env `RALPH_CLAUDE_PERMISSION_MODE`). Validates against the claude CLI's `--permission-mode` enum (`acceptEdits`, `auto`, `bypassPermissions`, `default`, `dontAsk`, `plan`); bogus value raises `ConfigError`.
- Step 2: `claude_spawn._build_argv` now emits `--permission-mode <cfg.claude_permission_mode>` BEFORE `-p` so the subprocess does not inherit the host's `~/.claude/settings.json` `defaultMode`.
- Step 3: Updated `cfg_for_repo` fixture (`tests/executor/conftest.py`) and the safety-integration `_init_repo` helper (`tests/safety/test_integration_loop.py`) with `claude_permission_mode="bypassPermissions"`.
- Step 4: Added 6 new tests (`test_spawn_passes_permission_mode_flag_to_claude`, `test_spawn_passes_overridden_permission_mode`, `test_load_config_invalid_permission_mode`, `test_claude_permission_mode_default`, `test_claude_permission_mode_picked_up_from_toml`, `test_env_claude_permission_mode_wins_over_toml`, `test_invalid_claude_permission_mode_in_toml_raises`).
- Step 5: README config-keys section gained a `Claude subprocess permission mode` block describing the TOML/env override and the host-global-defaultMode-can-stay-auto consequence.
- Tests: green — `uv run pytest` 772 passed / 4 skipped; `uv run ruff check .` clean; `uv run ruff format --check .` clean; `uv run mypy ralph_executor scripts skills tests` clean.
- Notes: All `PLAN.md` boxes ticked. Proceeding to commit + push + open PR.

## Iteration 1 — 2026-05-27T22:53:40Z — PR created

- PR: #43 (https://github.com/emp3thy/ralph/pull/43)
- Branch: ralph/EXECUTOR-PERMISSION-MODE-SCOPED
- Title: EXECUTOR-PERMISSION-MODE-SCOPED: scope claude permission-mode to executor subprocess
