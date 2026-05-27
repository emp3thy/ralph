---
id: CONFIG-PROMOTE-SWEEP-KNOBS
type: feature
status: current
severity: normal
attempts: 0
created_at: 2026-05-27T00:00:00+00:00
updated_at: 2026-05-27T06:58:47+00:00
depends_on: []
---

# Promote sweep knobs (bot_author_email + stale_days) to TOML config

Promote `RALPH_ADO_AUTHOR_EMAIL` and `RALPH_STALE_DAYS` from env-only reads to first-class `ExecutorConfig` fields (`bot_author_email` + `stale_days`), layered defaults < TOML < env. `loop._run_sweep` reads `cfg` directly; no env-bridge added (neither value has a subprocess consumer).

TOML key `bot_author_email` drops the misleading `ADO` prefix because sweep is host-agnostic. Env name `RALPH_ADO_AUTHOR_EMAIL` stays unchanged for backwards compatibility — operators with it set today see no change.

Spec: `docs/superpowers/specs/2026-05-27-promote-sweep-knobs-to-config-design.md`
Plan: `docs/superpowers/plans/2026-05-27-promote-sweep-knobs-to-config-plan.md`

See `PLAN.md` for the implementation plan pointer.

## Acceptance criteria

- `ExecutorConfig` exposes `bot_author_email: str` and `stale_days: int`
- `load_config()` resolves both via defaults < TOML < env
- `load_config()` rejects non-positive `stale_days` with `ConfigError`
- `loop._run_sweep` reads from `cfg`; zero `os.environ.get` calls for these two names (regression-tested in `test_loop.py`)
- `CONFIG_TOML_STUB` lists both as commented examples
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/CONFIG-PROMOTE-SWEEP-KNOBS`
