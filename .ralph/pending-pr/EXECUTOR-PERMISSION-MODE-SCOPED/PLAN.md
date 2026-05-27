# PLAN — EXECUTOR-PERMISSION-MODE-SCOPED

Scope `claude -p` permission mode to the executor subprocess instead of
inheriting the host's `~/.claude/settings.json` `defaultMode`. The
executor passes `--permission-mode <mode>` explicitly. Default
`"bypassPermissions"`; operator can override via TOML or env.

Confidence: 95% — `claude --help` confirms `--permission-mode <mode>`
with valid value `bypassPermissions`; flag is positional-safe placed
before `-p`.

## Steps

- [x] 1. Add `claude_permission_mode` field to `ExecutorConfig` with
      `DEFAULT_CLAUDE_PERMISSION_MODE = "bypassPermissions"`, layered via
      `_resolve_str` (TOML key `claude_permission_mode`, env
      `RALPH_CLAUDE_PERMISSION_MODE`). Validate value against the
      claude CLI's allowed set
      (`acceptEdits`, `auto`, `bypassPermissions`, `default`, `dontAsk`,
      `plan`) at load time; raise `ConfigError` on a bad value.
- [x] 2. Wire the flag into `claude_spawn._build_argv` — emit
      `--permission-mode <cfg.claude_permission_mode>` BEFORE `-p`
      (commander/yargs treats the next token after `-p` as the prompt
      value, so all flags must precede `-p`).
- [x] 3. Update existing fixtures (`cfg_for_repo` in
      `tests/executor/conftest.py`) to set
      `claude_permission_mode="bypassPermissions"` so other tests keep
      passing.
- [x] 4. Add unit tests:
      - `test_spawn_passes_permission_mode_flag` — asserts argv contains
        `--permission-mode` followed by `bypassPermissions`, in that
        order, and that the pair sits before `-p`.
      - `test_config_claude_permission_mode_default` — default load
        yields `"bypassPermissions"`.
      - `test_config_claude_permission_mode_env_overrides` — env wins
        over default.
      - `test_config_claude_permission_mode_toml_overrides` — TOML key
        wins over default.
      - `test_config_invalid_permission_mode_raises` — bogus value
        raises `ConfigError`.
- [x] 5. Update operator docs: `README.md` config-keys section gets a
      `claude_permission_mode` entry; note that the executor now sets
      its own permission mode so the host's global `~/.claude/settings.json`
      `defaultMode` does not need to be relaxed for ralph to run.
- [x] 6. Run gates: `uv run pytest`, `uv run ruff check .`,
      `uv run ruff format --check .`,
      `uv run mypy ralph_executor scripts skills tests`.
- [x] 7. Append iteration record to `HISTORY.md`, commit on
      `ralph/EXECUTOR-PERMISSION-MODE-SCOPED`, push, open PR via `gh`.
