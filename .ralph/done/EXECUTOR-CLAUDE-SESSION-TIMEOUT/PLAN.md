# PLAN — EXECUTOR-CLAUDE-SESSION-TIMEOUT

Per-iteration deadline on the spawned Claude subprocess. Scope is
intentionally small (one TOML knob, one default, one new test plus an
integration test), following the `CONFIG-PROMOTE-CYCLE-DETECTOR-THRESHOLDS`
template.

- [x] 1. Add `DEFAULT_CLAUDE_SESSION_TIMEOUT_SECONDS = 1200` and a
      `claude_session_timeout_seconds: int` field on `ExecutorConfig`;
      wire it through `_TOML_KNOWN_KEYS`, `load_config` (env
      `RALPH_CLAUDE_SESSION_TIMEOUT_SECONDS`), and the positive-int
      validation guard.
- [x] 2. Update `claude_spawn.spawn_claude_p` to call
      `proc.wait(timeout=cfg.claude_session_timeout_seconds)`. On
      `subprocess.TimeoutExpired`, log a warning, kill the process tree
      (Windows: `taskkill /T /F`; POSIX: `os.killpg`), join the tee
      threads, and append a synthetic stderr line so the classifier
      surfaces an `error` outcome to the loop.
- [x] 3. Tests:
      - Config: TOML default, TOML override, env-wins-over-TOML,
        zero/negative rejection (in `tests/executor/test_config_toml.py`).
      - Unit: monkeypatched `Popen` whose `wait` raises
        `TimeoutExpired`; assert `_kill_process_tree` was called and
        outcome is `error` with the synthetic stderr.
      - Integration: real `fake_claude` script that sleeps 60 s with a
        `claude_session_timeout_seconds=2` cfg; assert the spawn
        returns near the budget with an error outcome.
- [x] 4. Update the `setup_cmds` sample `config.toml` block to document
      the new knob.
- [x] 5. Lint, format, mypy, full pytest — all clean.
