# Promote Claude Code bash-tool timeout to TOML config

**Date:** 2026-05-27
**Scope:** Promote `BASH_MAX_TIMEOUT_MS` (currently inherited from operator shell env, defaulting to 600_000 = 10 min in Claude Code) to a first-class `ExecutorConfig` field so ralph can pin a 15-minute (or operator-chosen) per-bash-tool ceiling for the Claude session it spawns.
**Status:** Design — pending review.

## Background

`claude_spawn.spawn_claude_p` builds the env for the `claude -p` subprocess by copying `os.environ` (line 434) and adding `RALPH_PBI_DIR` + optionally `ANTHROPIC_API_KEY`. It does NOT explicitly set `BASH_MAX_TIMEOUT_MS`, so the spawned claude inherits whatever the operator shell has — which is typically nothing, meaning Claude Code's default of 600_000 ms (10 minutes) per bash tool call kicks in.

Operator wants 15 min (900_000 ms). Hardcoding works, but the same logic that drove the sweep-knobs PBI applies: this is project state, not secret, not per-shell, and benefits from being checked in alongside the repo.

## Decision

Promote as `bash_max_timeout_ms: int = 900_000` on `ExecutorConfig`, layered defaults < TOML < env. `spawn_claude_p` sets the resulting value on the subprocess env before launching `claude`.

This is an **env-bridge** case: the consumer is the Claude Code subprocess, which reads `BASH_MAX_TIMEOUT_MS` from `os.environ`. The bridge entry runs from `cfg.bash_max_timeout_ms` → subprocess env. Same shape as PR #21's `gh_owner` / `halt_webhook` knobs.

## Naming

TOML key: `bash_max_timeout_ms`.

Env var: `BASH_MAX_TIMEOUT_MS` (the Claude Code env-var the subprocess will read — no new ralph-prefixed env name, because the variable already has a canonical name in the Claude Code ecosystem).

Default: `900_000` (15 minutes). Chosen because the operator explicitly wants 15 min; 10 min was Claude Code's default and proved insufficient for some PBI tasks.

## Implementation

### File: `ralph_executor/config.py`

1. Add a module-level default:
   ```python
   DEFAULT_BASH_MAX_TIMEOUT_MS = 900_000  # 15 minutes
   ```

2. Add `"bash_max_timeout_ms"` to `_TOML_KNOWN_KEYS`.

3. Add `bash_max_timeout_ms: int = DEFAULT_BASH_MAX_TIMEOUT_MS` to `ExecutorConfig`, at the end of the dataclass (after the sweep-knobs PBI's fields, since this new field has a default).

4. Add resolver call in `load_config()` after the sweep-knobs `stale_days` block:
   ```python
   bash_max_timeout_ms = _resolve_int(
       name="bash_max_timeout_ms",
       env_name="BASH_MAX_TIMEOUT_MS",
       toml_value=toml_overrides.get("bash_max_timeout_ms"),
       default=DEFAULT_BASH_MAX_TIMEOUT_MS,
       source_label=source_label,
   )
   if bash_max_timeout_ms <= 0:
       raise ConfigError(
           f"{source_label}: bash_max_timeout_ms must be positive (got {bash_max_timeout_ms})"
       )
   ```

5. Pass to `ExecutorConfig(...)` constructor.

### File: `ralph_executor/claude_spawn.py`

In `spawn_claude_p`, between the `env = os.environ.copy()` and the `subprocess.Popen` call, add:

```python
# Tell the Claude Code subprocess to use ralph's chosen bash-tool
# ceiling. The default in Claude Code itself is 600_000 (10 min);
# we promote ralph's 15-min default via cfg.bash_max_timeout_ms,
# overridable per repo via TOML and per shell via env.
env["BASH_MAX_TIMEOUT_MS"] = str(cfg.bash_max_timeout_ms)
```

Place AFTER the `ANTHROPIC_API_KEY` block so the bridge entries cluster together.

### File: `ralph_executor/setup_cmds.py`

Append to `CONFIG_TOML_STUB`:

```
#
# Claude Code bash-tool ceiling in milliseconds. Default 900000 (15 min).
# Ralph propagates this to the spawned claude subprocess via the
# BASH_MAX_TIMEOUT_MS env var. Must be positive.
# bash_max_timeout_ms = 900000
```

### Tests

`tests/executor/test_config_toml.py`:
- TOML-loads `bash_max_timeout_ms`
- Env `BASH_MAX_TIMEOUT_MS` wins over TOML
- Default is `900_000` when neither set
- Validation: `bash_max_timeout_ms = 0` raises `ConfigError`

`tests/executor/test_claude_spawn.py` (or wherever spawn is tested):
- Assert `spawn_claude_p` sets `BASH_MAX_TIMEOUT_MS` on the subprocess env to `str(cfg.bash_max_timeout_ms)`
- A `cfg` built with `bash_max_timeout_ms=42_000` flows through to the subprocess env

### Backwards compatibility

Operators with `BASH_MAX_TIMEOUT_MS` set in their shell: continue working (env wins over TOML via `_resolve_int`).

Operators with nothing set: ralph now applies the 900_000 default to the Claude subprocess, replacing the 600_000 inherited from Claude Code itself.

The new default raises the ceiling, so existing scripts that ran under 10 min still work; scripts that previously timed out at 10 min now have more headroom.

### Out of scope

- Other Claude Code tool timeouts (no operator demand)
- Renaming `BASH_MAX_TIMEOUT_MS` (it's Claude Code's canonical name)

## Acceptance

- `ExecutorConfig` exposes `bash_max_timeout_ms: int`
- `load_config()` resolves via defaults < TOML < env; rejects non-positive
- `spawn_claude_p` sets `BASH_MAX_TIMEOUT_MS` on the subprocess env from cfg
- `CONFIG_TOML_STUB` lists the key as a commented example
- Tests cover layering + validation + bridge behaviour
- pytest / ruff / mypy strict all green
