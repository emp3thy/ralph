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
- 2026-05-28T01:42:18.233580+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:42:53.857295+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:43:29.219930+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:44:04.537117+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:44:40.107242+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:45:15.666192+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:45:51.153857+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:46:26.416423+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:47:01.912505+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:47:38.119712+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:48:13.957391+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:48:50.344938+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:49:26.280031+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:50:02.210001+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:50:38.370718+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:51:14.246954+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:51:49.599345+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:52:25.108646+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:53:01.478334+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:53:37.640518+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:54:13.434877+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:54:49.196038+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:55:25.110565+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:56:00.851103+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:56:36.647694+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:57:12.142699+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:57:48.103393+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:58:24.026765+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:59:00.839065+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T01:59:37.050758+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T02:00:13.055679+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T02:00:51.372840+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T02:01:34.399060+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T02:02:17.793755+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T02:02:57.447951+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T02:03:33.559077+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T02:04:09.948300+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T02:04:47.092119+00:00 sweep: PR merged (completed)
