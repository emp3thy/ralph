# Promote sweep knobs to TOML config

**Date:** 2026-05-27
**Scope:** Promote `RALPH_ADO_AUTHOR_EMAIL` and `RALPH_STALE_DAYS` from env-only reads to first-class `ExecutorConfig` fields, layered defaults < TOML < env.
**Status:** Design — pending user review.

## Background

`ExecutorConfig` (`ralph_executor/config.py`) is the single source of runtime configuration. PR #16 promoted `git_host`. PR #21 promoted `gh_owner`, `ado_org_url`, `ado_project`, and `halt_webhook` and added an env-bridge in `cli._export_cfg_to_env` so subprocess skills (`pr-github`, `host_select.verify_auth_env`, halt webhook poster) still see the values by env-var name.

Two operationally-significant knobs remain env-only:

- `RALPH_ADO_AUTHOR_EMAIL` — sweep author email. Used to skip Ralph-authored PR comments in the sweep loop so the loop doesn't feed back into itself.
- `RALPH_STALE_DAYS` — sweep stale threshold (days). Defaults to 3 if unset/invalid.

Both are read by `ralph_executor/loop.py:_run_sweep` (lines 107 and 120). Both feed into `SweepConfig` constructed inline in that same function. Neither value is consumed by any subprocess: `grep -rE 'RALPH_ADO_AUTHOR_EMAIL|RALPH_STALE_DAYS' --include='*.py'` returns only `ralph_executor/{loop,sweep/runner,sweep/types}.py`.

## Decision

Promote both knobs directly. **No env-bridge.** `_run_sweep` already accepts `cfg: ExecutorConfig`; it should read `cfg.bot_author_email` and `cfg.stale_days` instead of calling `os.environ.get()`.

Env-var support survives via the existing `_resolve_str` / `_resolve_int` resolver chain (defaults < TOML < env), so operators with `RALPH_ADO_AUTHOR_EMAIL` set today see no change.

### Why no env-bridge

`cli._export_cfg_to_env` exists because PR #21's knobs are read by subprocess skills that inherit `os.environ` from the parent. The bridge translates `cfg.gh_owner` back to `os.environ["GH_OWNER"]` so the subprocess shell can read it. For `bot_author_email` and `stale_days`, that justification doesn't hold:

| Field | In-process consumer | Subprocess consumer | Bridge needed? |
|---|---|---|---|
| `gh_owner` | `host_select.verify_auth_env` | `pr-github/_common.py` | yes |
| `halt_webhook` | `safety/halt.py` (in-process poster) | none today, but designed to be portable | yes (defensive) |
| `bot_author_email` | `loop._run_sweep` → `SweepConfig` | none | **no** |
| `stale_days` | `loop._run_sweep` → `SweepConfig` | none | **no** |

Adding bridge entries for these two would create two sources of truth (`cfg.bot_author_email` and `os.environ["RALPH_ADO_AUTHOR_EMAIL"]`) for zero benefit.

## Naming

TOML key: `bot_author_email`.

Rationale: sweep is host-agnostic (works for both GitHub and Azure DevOps PRs). The `ADO` prefix in the env-var name is historical and misleading. The TOML surface is the place to fix it.

Env-var name stays `RALPH_ADO_AUTHOR_EMAIL`. Operators already have it set; no rename, no deprecation cycle. The mismatch between TOML name and env name is documented in `CONFIG_TOML_STUB` and `config.py` docstring.

TOML key: `stale_days`.

Env-var name stays `RALPH_STALE_DAYS`.

## Implementation

### File: `ralph_executor/config.py`

1. Add to `_TOML_KNOWN_KEYS` (alphabetical with neighbours):

   ```python
   "bot_author_email",
   "stale_days",
   ```

2. Add module-level default:

   ```python
   DEFAULT_STALE_DAYS = 3
   ```

3. Add fields to `ExecutorConfig` (with defaults so existing test constructions keep compiling):

   ```python
   # Sweep tuning. bot_author_email is the commit/PR author email Ralph
   # uses — sweep skips comments by this author so the loop doesn't feed
   # back into itself. Env name keeps the ``ADO`` prefix for backwards
   # compatibility; sweep is host-agnostic and the value is the same
   # regardless of host.
   bot_author_email: str = ""
   stale_days: int = DEFAULT_STALE_DAYS
   ```

4. Add resolver calls in `load_config()` (alphabetical with neighbours):

   ```python
   bot_author_email = _resolve_str(
       name="bot_author_email",
       env_name="RALPH_ADO_AUTHOR_EMAIL",
       toml_value=toml_overrides.get("bot_author_email"),
       default="",
       source_label=source_label,
   )
   stale_days = _resolve_int(
       name="stale_days",
       env_name="RALPH_STALE_DAYS",
       toml_value=toml_overrides.get("stale_days"),
       default=DEFAULT_STALE_DAYS,
       source_label=source_label,
   )
   if stale_days <= 0:
       raise ConfigError(
           f"{source_label}: stale_days must be positive (got {stale_days})"
       )
   ```

   Positivity check belongs here (config layer), not in `_run_sweep`. `SweepConfig.__post_init__` will still validate as a defence-in-depth check.

5. Pass new fields to the `ExecutorConfig(...)` constructor at the bottom of `load_config()`.

### File: `ralph_executor/loop.py`

Replace the body of `_run_sweep` from line 107 through line 137 with:

```python
if not cfg.bot_author_email:
    log.warning(
        "sweep: bot_author_email is not set (TOML key 'bot_author_email' "
        "or env RALPH_ADO_AUTHOR_EMAIL); skipping sweep this iteration"
    )
    return

scripts_path = _pr_skill_scripts_path(cfg)
if not scripts_path.is_dir():
    log.warning(
        "sweep: PR-skill scripts directory not found at %s; skipping",
        scripts_path,
    )
    return
```

And in the `SweepConfig(...)` constructor below:

```python
sweep_cfg = SweepConfig(
    ralph_author_email=cfg.bot_author_email,
    max_attempts=cfg.max_attempts,
    stale_threshold=timedelta(days=cfg.stale_days),
    now=datetime.now(tz=UTC),
)
```

Drop:

- `os.environ.get("RALPH_ADO_AUTHOR_EMAIL", ...)` and the `ralph_email` local
- `os.environ.get("RALPH_STALE_DAYS", "3")` and the `raw_days` / `stale_days` int-parse + positivity guard
- The `try / except ValueError` around int parsing (config layer catches that and raises `ConfigError` at startup, which is the right place)

Update the docstring at lines 99–104 to point at config fields rather than env vars.

### File: `ralph_executor/setup_cmds.py`

Add to `CONFIG_TOML_STUB` (the commented TOML scaffold written by `ralph init`):

```toml
# Sweep author email. Commits/PR comments authored by this address are
# skipped by the sweep loop so Ralph doesn't react to itself. Required
# for sweep to run; if unset, sweep is skipped each iteration with a
# WARNING. Env override: RALPH_ADO_AUTHOR_EMAIL (legacy name kept for
# backwards compatibility; sweep is host-agnostic).
# bot_author_email = "ralph-bot@example.com"

# Sweep stale-PR threshold in days. PRs older than this are moved to
# blocked/. Must be positive. Env override: RALPH_STALE_DAYS.
# stale_days = 3
```

### Tests

`tests/test_config.py`:

- TOML-only load: `bot_author_email = "x@y"` → `cfg.bot_author_email == "x@y"`
- Env overrides TOML: `RALPH_ADO_AUTHOR_EMAIL=env@y` with TOML `bot_author_email = "toml@y"` → `cfg.bot_author_email == "env@y"`
- Default: neither set → `cfg.bot_author_email == ""`
- Same three cases for `stale_days` (with int default `3`)
- Validation: `stale_days = 0` in TOML raises `ConfigError("must be positive")`
- Validation: `RALPH_STALE_DAYS=-1` raises `ConfigError`

`tests/test_loop_sweep.py` (or wherever `_run_sweep` is tested):

- Build a test `ExecutorConfig` with `bot_author_email=""` → sweep skipped, WARNING logged about `bot_author_email is not set`
- Build with `bot_author_email="ralph@x"`, `stale_days=5` → `SweepConfig` constructed with `ralph_author_email="ralph@x"` and `stale_threshold=timedelta(days=5)`
- Remove any existing test that exercises the env-var path through `_run_sweep` (the env-var resolution is now exclusively a `load_config` concern, covered by `test_config.py`)

### Backwards compatibility

- Operators with `RALPH_ADO_AUTHOR_EMAIL=…` set: continue working (env still wins via `_resolve_str`).
- Operators with `RALPH_STALE_DAYS=…` set: continue working.
- Operators with neither set: sweep was already skipped with a WARNING; behaviour unchanged.
- New TOML key in `.ralph/config.toml`: unknown-key warnings are suppressed by adding to `_TOML_KNOWN_KEYS` before deploying.

### Out of scope

- Promoting `RALPH_LOG_LEVEL`, `RALPH_RUN_ONCE`, `RALPH_HOME`, `RALPH_SKILLS_ROOT`, `RALPH_CLAUDE_SKILLS_DIR`, `RALPH_WORKITEM_FETCH_SCRIPT`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `ADO_PAT` — each is either operational/per-shell, a secret, or already promoted (see audit table in this design's brainstorm).
- Renaming `RALPH_ADO_AUTHOR_EMAIL` env var. Deprecation cycle isn't worth the operator churn for a single-operator deployment today.
- Bridge entries for the two new fields. Not needed (no subprocess consumer).

## Risks

- **Existing tests construct `ExecutorConfig(...)` positionally.** Mitigation: the two new fields take defaults (`bot_author_email = ""`, `stale_days = 3`) so existing constructions don't need to add positional args. New keyword args at end of dataclass.
- **`SweepConfig.__post_init__` rejects `stale_threshold <= 0`.** With the config-layer guard added, this can no longer fire from a TOML/env path, but stays as defence-in-depth for direct test construction.
- **WARNING message change.** Operators grepping logs for `RALPH_ADO_AUTHOR_EMAIL is not set` won't match the new message `bot_author_email is not set`. New message mentions both the TOML key and the env var, so a grep for either substring still matches.

## Acceptance

- `ralph_executor/config.py` exposes `bot_author_email: str` and `stale_days: int` on `ExecutorConfig`
- `load_config()` resolves both via defaults < TOML < env
- `_resolve_int` rejects non-positive `stale_days` at load time
- `loop._run_sweep` reads from `cfg`, no `os.environ.get` calls for these two names
- `CONFIG_TOML_STUB` includes both as commented examples
- `tests/test_config.py` covers defaults, env override, validation
- `tests/test_loop_sweep.py` covers the cfg-driven skip and pass-through paths
- All existing tests pass (mypy strict, ruff, pytest)
