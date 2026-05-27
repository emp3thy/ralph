---
id: CONFIG-PROMOTE-BASH-TIMEOUT
type: feature
status: inbox
severity: normal
attempts: 0
created_at: 2026-05-27T00:01:00+00:00
updated_at: 2026-05-27T00:01:00+00:00
depends_on: ["CONFIG-PROMOTE-SWEEP-KNOBS"]
---

# Promote Claude Code bash-tool timeout (BASH_MAX_TIMEOUT_MS) to TOML config

Promote `BASH_MAX_TIMEOUT_MS` (Claude Code's per-bash-call ceiling, currently inherited from the operator shell with a Claude-Code-side default of 600_000 ms = 10 min) to a first-class `ExecutorConfig` field `bash_max_timeout_ms`, defaulting to 900_000 (15 min). Set explicitly on the Claude subprocess env in `spawn_claude_p`.

Uses a local subprocess-scoped bridge (set in `spawn_claude_p`), NOT `cli._export_cfg_to_env`. The latter would pollute ralph's parent env globally; the former touches only the claude subprocess env.

Depends on `CONFIG-PROMOTE-SWEEP-KNOBS`: both PBIs touch `ralph_executor/config.py` and `tests/executor/conftest.py`. Sequencing prevents merge conflicts.

Spec: `docs/superpowers/specs/2026-05-27-promote-bash-timeout-to-config-design.md`
Plan: `docs/superpowers/plans/2026-05-27-promote-bash-timeout-to-config-plan.md`

See `PLAN.md` for the implementation plan pointer.

## Acceptance criteria

- `ExecutorConfig` exposes `bash_max_timeout_ms: int` with default 900_000
- `load_config()` resolves via defaults < TOML < env; rejects non-positive with `ConfigError`
- `spawn_claude_p` sets `env["BASH_MAX_TIMEOUT_MS"] = str(cfg.bash_max_timeout_ms)` on the subprocess env
- `CONFIG_TOML_STUB` lists the key as a commented example
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/CONFIG-PROMOTE-BASH-TIMEOUT`
