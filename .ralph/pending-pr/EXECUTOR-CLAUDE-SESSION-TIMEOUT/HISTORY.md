<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-28T01:40:18Z

- Plan steps 1-5: completed in one iteration. Scope is small (one TOML
  knob + one Popen wait flag + tests) and matches the
  `CONFIG-PROMOTE-CYCLE-DETECTOR-THRESHOLDS` template.
- Code:
  - `ralph_executor/config.py`: added
    `DEFAULT_CLAUDE_SESSION_TIMEOUT_SECONDS = 1200`, new
    `claude_session_timeout_seconds` field on `ExecutorConfig`, TOML
    key, env override (`RALPH_CLAUDE_SESSION_TIMEOUT_SECONDS`),
    positive-int validation.
  - `ralph_executor/claude_spawn.py`: switched `proc.wait()` to
    `proc.wait(timeout=cfg.claude_session_timeout_seconds)`; added
    `_kill_process_tree` helper (Windows `taskkill /T /F` + POSIX
    `killpg`), started the child in its own process group / new
    Windows process group so the kill walks the tree; replaced the
    KeyboardInterrupt-path `proc.kill()` with the same tree-killer
    (the existing single-`proc.kill` was a Windows orphan-grandchild
    hazard for the `.cmd`-wrapped real claude binary too).
  - `ralph_executor/setup_cmds.py`: documented the knob in the
    sample `config.toml` block.
  - `tests/executor/conftest.py`: added the new field to
    `cfg_for_repo`.
  - `tests/executor/test_config_toml.py`: 5 new tests covering
    default, TOML override, env-wins, and zero/negative rejection.
  - `tests/executor/test_claude_spawn.py`: 2 new tests — a unit test
    monkeypatching `Popen` + `_kill_process_tree`, and an integration
    test driving a real fake-claude that sleeps 60 s with a 2-second
    budget.
- Tests: green — `uv run pytest` 798 passed, 4 skipped. `uv run ruff
  check .`, `uv run ruff format --check .`, and
  `uv run mypy ralph_executor scripts skills tests` all clean.
- Notes: the `_kill_process_tree` change is technically a bonus over
  the PBI sketch — on Windows the real `claude` binary is a `.cmd`
  shim that exec's the Node entrypoint, so a plain `proc.kill()` would
  only terminate the shim and leave the Node child holding the pipes
  open (the integration test surfaced this immediately). The tree
  kill is the right behaviour for the existing KeyboardInterrupt path
  too.

## Iteration 2 — 2026-05-28T01:42:00Z — PR created

- PR: !46 — https://github.com/emp3thy/ralph/pull/46
- Branch: ralph/EXECUTOR-CLAUDE-SESSION-TIMEOUT
- Title: EXECUTOR-CLAUDE-SESSION-TIMEOUT: per-iteration deadline on claude subprocess
