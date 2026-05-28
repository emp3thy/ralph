<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-28T08:55:00+00:00 — PR created

- Added `watch_mode: bool = False` + `idle_exit_threshold: int = 2` to `ExecutorConfig`; TOML keys `watch_mode` / `idle_exit_threshold` + env vars `RALPH_WATCH_MODE` / `RALPH_IDLE_EXIT_THRESHOLD`; positive-validation on `idle_exit_threshold`.
- `run_loop` (ralph_executor/loop.py) now tracks consecutive idle outcomes, resets on any non-idle, and exits cleanly once `consecutive_idle >= cfg.idle_exit_threshold` unless `cfg.watch_mode` is True. Drain log line emitted via the `ralph_executor.cli` logger so supervisors get a single grep target.
- CLI gains `--watch` (manual mutex with `--once` / `--iterations` because the existing layout puts those args outside any argparse mutex group). `_apply_overrides` flips `cfg.watch_mode=True` when `--watch` is passed; absence preserves a TOML / env value.
- TOML stub (ralph_executor/setup_cmds.py) documents the new keys under a "Drain-on-idle" comment block.
- README + docs/bootstrap-operator-runbook.md lead with the new drain default and document `--watch` as the workstation opt-in; pod / container runs explicitly called out as the intended default.
- Tests: 4 new in tests/executor/test_loop.py (two-idle drain, watch-mode no-drain, non-idle resets counter, integration against empty fixture); 3 new in tests/executor/test_cli.py (mutex rejection x2, --watch flips cfg.watch_mode).
- Verification: `uv run pytest` -> 852 passed / 4 pre-existing skips. `uv run ruff check .` clean. `uv run ruff format --check .` clean. `uv run mypy ralph_executor scripts skills tests` -> 1 pre-existing error in tests/executor/test_claude_spawn.py:856 (`comparison-overlap`), confirmed unchanged by stashing this PR's diff; not addressed here.
- PR: #47
- Branch: ralph/EXECUTOR-EXIT-WHEN-IDLE-DEFAULT
- Title: `EXECUTOR-EXIT-WHEN-IDLE-DEFAULT: drain to idle by default; --watch opts in to daemon mode`
- URL: https://github.com/emp3thy/ralph/pull/47
- 2026-05-28T11:46:25.248916+00:00 sweep: PR merged (completed)
