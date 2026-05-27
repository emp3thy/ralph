<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-27T19:45:00+00:00

- Task 1: failing tests added to `tests/executor/test_config_toml.py` (5 cases: TOML pickup, env-wins-over-TOML, default, zero-rejected, negative-env-rejected) and `tests/executor/test_claude_spawn.py` (1 case asserting `BASH_MAX_TIMEOUT_MS` flows from `cfg` through to subprocess env). Spawn-bridge test uses the existing fake-claude-script pattern (echo env to stdout) rather than mocking `subprocess.Popen` — simpler and consistent with `test_spawn_claude_p_respects_cwd_and_pbi_dir_overrides`. Commit `7863cc8`.
- Task 2: added `DEFAULT_BASH_MAX_TIMEOUT_MS = 900_000`, `"bash_max_timeout_ms"` in `_TOML_KNOWN_KEYS`, dataclass field at end of `ExecutorConfig` (default-field ordering — must follow `stale_days`), `_resolve_int` call with positivity guard in `load_config`, conftest `cfg_for_repo` updated. Commit `a1bf434`.
- Task 3: env-bridge line in `spawn_claude_p` between the `ANTHROPIC_API_KEY` block and the `log.info("spawning ...")` call — sets `env["BASH_MAX_TIMEOUT_MS"] = str(cfg.bash_max_timeout_ms)` on the Popen env dict (subprocess-scoped, NOT `os.environ`). Commit `d1c610b`.
- Task 4: appended commented `bash_max_timeout_ms` example to `CONFIG_TOML_STUB` in `setup_cmds.py`. Commit `7af8ba5`.
- Task 5: full suite green — `631 passed, 2 skipped` (opt-in prompt-smoke); `uv run ruff check .` clean; `uv run mypy ralph_executor scripts skills tests` clean; one `ruff format` fixup on test file in commit `c95e153`.
- Tests: green (`uv run pytest tests/`).
- Notes: spec's suggested Popen-mock approach for the spawn-bridge test was replaced with the existing fake-claude-binary echo pattern — simpler and matches the test style for the other two env-passing tests in `test_claude_spawn.py`.

## Iteration 2 — 2026-05-27T19:50:00+00:00 — PR created

- PR: https://github.com/emp3thy/ralph/pull/38
- Branch: ralph/CONFIG-PROMOTE-BASH-TIMEOUT
- Title: CONFIG-PROMOTE-BASH-TIMEOUT: promote bash_max_timeout_ms to TOML

