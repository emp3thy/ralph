# Promote Bash Timeout to TOML Config — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote `BASH_MAX_TIMEOUT_MS` from inherited-env-only to first-class `ExecutorConfig` field `bash_max_timeout_ms` (default 900_000 = 15 min), explicitly set on the Claude subprocess env in `spawn_claude_p`.

**Architecture:** Same shape as PR #21's bridge knobs (`gh_owner`, `halt_webhook`). Default constant, dataclass field with default, resolver call with positivity guard, env-bridge entry in `spawn_claude_p` setting `os.environ`-bound subprocess env.

**Tech Stack:** Python 3.12, dataclasses, `tomllib`, pytest, ruff, mypy strict.

**Confidence (per task):**

| Task | % | Why |
|---|---|---|
| 1. Failing config + spawn tests | 95% | Pattern from sweep-knobs PBI; spawn-env test is new but `subprocess.Popen` mocking has prior art in `tests/executor/test_claude_spawn.py`. |
| 2. Promote knob in config | 97% | Mechanical addition to known patterns. Field-order trap noted: must go after sweep-knobs fields (sister PBI). |
| 3. Wire env-bridge in spawn_claude_p | 96% | One-line env assignment, between two existing blocks. |
| 4. Stub doc update | 96% | Append comment block. |
| 5. Full-suite gate | 93% | Could surface a test that fakes `subprocess.Popen` and checks env contents — minor risk of needing assertion update. |

**Spec:** `docs/superpowers/specs/2026-05-27-promote-bash-timeout-to-config-design.md`

**Depends on:** `CONFIG-PROMOTE-SWEEP-KNOBS` — both PBIs touch `ralph_executor/config.py` and `tests/executor/conftest.py`. Sequencing prevents merge conflicts: sweep-knobs lands first, this PBI rebases onto its main-merge.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `ralph_executor/config.py` | Modify | Add `DEFAULT_BASH_MAX_TIMEOUT_MS`, add to `_TOML_KNOWN_KEYS`, add `bash_max_timeout_ms: int` field with default, resolver call + positivity guard. |
| `ralph_executor/claude_spawn.py` | Modify | In `spawn_claude_p`, set `env["BASH_MAX_TIMEOUT_MS"]` from `cfg.bash_max_timeout_ms`. |
| `ralph_executor/setup_cmds.py` | Modify | Add commented entry to `CONFIG_TOML_STUB`. |
| `tests/executor/conftest.py` | Modify | Add `bash_max_timeout_ms=900_000` to `cfg_for_repo` fixture. |
| `tests/executor/test_config_toml.py` | Modify | Add layering + validation tests; add `BASH_MAX_TIMEOUT_MS` to `clean_env` cleanup. |
| `tests/executor/test_claude_spawn.py` | Modify | Add test that `spawn_claude_p` populates subprocess env with `BASH_MAX_TIMEOUT_MS` from cfg. |

---

## Task 1: Failing tests (config + spawn bridge)

- [ ] **Step 1.1: Add `BASH_MAX_TIMEOUT_MS` to `clean_env` cleanup**

In `tests/executor/test_config_toml.py`, append `"BASH_MAX_TIMEOUT_MS"` to the `for var in (...)` tuple in the `clean_env` fixture.

- [ ] **Step 1.2: Append config layering + validation tests**

```python
def test_bash_max_timeout_ms_from_toml(clean_env: Path) -> None:
    _write_toml(clean_env, "bash_max_timeout_ms = 1200000\n")
    cfg = load_config()
    assert cfg.bash_max_timeout_ms == 1_200_000


def test_bash_max_timeout_ms_env_wins(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_toml(clean_env, "bash_max_timeout_ms = 1200000\n")
    monkeypatch.setenv("BASH_MAX_TIMEOUT_MS", "600000")
    cfg = load_config()
    assert cfg.bash_max_timeout_ms == 600_000


def test_bash_max_timeout_ms_default(clean_env: Path) -> None:
    cfg = load_config()
    assert cfg.bash_max_timeout_ms == 900_000


def test_bash_max_timeout_ms_rejected_when_zero(clean_env: Path) -> None:
    _write_toml(clean_env, "bash_max_timeout_ms = 0\n")
    with pytest.raises(ConfigError, match="bash_max_timeout_ms must be positive"):
        load_config()
```

- [ ] **Step 1.3: Add spawn-bridge test**

In `tests/executor/test_claude_spawn.py`, copy the existing fake-popen pattern (look for any test that already monkeypatches `subprocess.Popen` — there should be one) and add:

```python
def test_spawn_claude_p_sets_bash_max_timeout_ms_from_cfg(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spawn_claude_p must export cfg.bash_max_timeout_ms to the Claude
    subprocess env so the bash-tool ceiling matches what TOML/env said."""
    from dataclasses import replace

    captured_env: dict[str, str] = {}

    class _FakePopen:
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            captured_env.update(kwargs.get("env") or {})
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self.returncode = 0

        def wait(self) -> int:
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr("ralph_executor.claude_spawn.subprocess.Popen", _FakePopen)
    # Stub the gh PR query so spawn_claude_p completes its post-exit logic.
    monkeypatch.setattr(
        "ralph_executor.claude_spawn._query_open_pr_via_gh",
        lambda repo, branch: None,
    )

    cfg = replace(cfg_for_repo, bash_max_timeout_ms=420_000)
    # Build a minimal PBI stand-in matching whatever the existing tests do.
    pbi = _make_test_pbi(fake_repo)  # use the helper the existing tests use

    spawn_claude_p(cfg, pbi)
    assert captured_env.get("BASH_MAX_TIMEOUT_MS") == "420000"
```

(Copy `_make_test_pbi` from the existing test pattern; don't invent a new one.)

- [ ] **Step 1.4: Run failing tests**

```bash
uv run pytest tests/executor/test_config_toml.py -k "bash_max_timeout" -v
uv run pytest tests/executor/test_claude_spawn.py -k "bash_max_timeout" -v
```

Expected: all FAIL.

- [ ] **Step 1.5: Commit**

```bash
git -C /c/Users/gethi/source/ralph add tests/executor/test_config_toml.py tests/executor/test_claude_spawn.py
git -C /c/Users/gethi/source/ralph commit -m "test(config): failing tests for bash_max_timeout_ms TOML promotion + spawn bridge"
```

---

## Task 2: Promote `bash_max_timeout_ms` in `ExecutorConfig`

- [ ] **Step 2.1: Add default constant**

In `ralph_executor/config.py`, near the other `DEFAULT_*` constants:

```python
DEFAULT_BASH_MAX_TIMEOUT_MS = 900_000  # 15 minutes; Claude Code's own default is 600_000.
```

- [ ] **Step 2.2: Add to `_TOML_KNOWN_KEYS`**

Add `"bash_max_timeout_ms"` near the sweep-knobs entries.

- [ ] **Step 2.3: Add dataclass field with default**

At the end of `ExecutorConfig` (after the sweep-knobs PBI's `stale_days` field):

```python
    # Claude Code bash-tool ceiling in milliseconds. Set on the Claude
    # subprocess env in spawn_claude_p. Default 900_000 (15 min);
    # Claude Code's own default is 600_000 (10 min). Strictly positive.
    bash_max_timeout_ms: int = DEFAULT_BASH_MAX_TIMEOUT_MS
```

- [ ] **Step 2.4: Add resolver call in `load_config()`**

After the `stale_days` resolver block:

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

- [ ] **Step 2.5: Pass to `ExecutorConfig(...)` constructor**

Append `bash_max_timeout_ms=bash_max_timeout_ms` to the `return ExecutorConfig(...)` keyword args.

- [ ] **Step 2.6: Update `cfg_for_repo` fixture**

In `tests/executor/conftest.py`, add `bash_max_timeout_ms=900_000` to the `cfg_for_repo` constructor.

- [ ] **Step 2.7: Run config tests + mypy + ruff**

```bash
uv run pytest tests/executor/test_config_toml.py -k "bash_max_timeout" -v
uv run ruff check ralph_executor/config.py tests/executor/conftest.py
uv run mypy --strict ralph_executor/config.py
```

Expected: 4 PASS, ruff/mypy green.

- [ ] **Step 2.8: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/config.py tests/executor/conftest.py
git -C /c/Users/gethi/source/ralph commit -m "feat(config): promote bash_max_timeout_ms to ExecutorConfig"
```

---

## Task 3: Wire env-bridge in `spawn_claude_p`

- [ ] **Step 3.1: Add env-bridge line**

In `ralph_executor/claude_spawn.py`, find `spawn_claude_p` (around line 419). After the `ANTHROPIC_API_KEY` block (around line 440) and before `log.info("spawning %s for PBI %s", ...)`, insert:

```python
    # Tell the Claude Code subprocess to use ralph's chosen bash-tool
    # ceiling. Claude Code's default is 600_000 (10 min); ralph's default
    # is 900_000 (15 min). Operator can override via TOML or env.
    env["BASH_MAX_TIMEOUT_MS"] = str(cfg.bash_max_timeout_ms)
```

- [ ] **Step 3.2: Run the spawn-bridge test**

```bash
uv run pytest tests/executor/test_claude_spawn.py -k "bash_max_timeout" -v
```

Expected: PASS.

- [ ] **Step 3.3: Run full claude_spawn tests + mypy/ruff**

```bash
uv run pytest tests/executor/test_claude_spawn.py -v
uv run ruff check ralph_executor/claude_spawn.py
uv run mypy --strict ralph_executor/claude_spawn.py
```

- [ ] **Step 3.4: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/claude_spawn.py
git -C /c/Users/gethi/source/ralph commit -m "feat(spawn): bridge cfg.bash_max_timeout_ms to claude subprocess env"
```

---

## Task 4: Update `CONFIG_TOML_STUB`

- [ ] **Step 4.1: Append commented entry**

In `ralph_executor/setup_cmds.py`, append to `CONFIG_TOML_STUB` (after the sweep-knobs block from the sister PBI):

```
#
# Claude Code bash-tool ceiling in milliseconds. Default 900000 (15 min);
# Claude Code's own default is 600000 (10 min). Propagated to the spawned
# claude subprocess via BASH_MAX_TIMEOUT_MS. Must be positive.
# bash_max_timeout_ms = 900000
```

- [ ] **Step 4.2: Run + commit**

```bash
uv run pytest tests/executor/test_setup_cmds.py -v
uv run ruff check ralph_executor/setup_cmds.py
git -C /c/Users/gethi/source/ralph add ralph_executor/setup_cmds.py
git -C /c/Users/gethi/source/ralph commit -m "docs(setup): document bash_max_timeout_ms in CONFIG_TOML_STUB"
```

---

## Task 5: Full-suite green

- [ ] **Step 5.1: Run full suite + ruff + mypy**

```bash
uv run pytest tests/ -v
uv run ruff check ralph_executor/ tests/
uv run mypy --strict ralph_executor/
```

Expected: all green. If a test elsewhere checks subprocess env contents without expecting `BASH_MAX_TIMEOUT_MS`, update it to allow the new key.

## Acceptance

- [x] `ExecutorConfig.bash_max_timeout_ms: int` with default 900_000 — Task 2
- [x] `load_config()` defaults < TOML < env; rejects non-positive — Task 2
- [x] `spawn_claude_p` sets `BASH_MAX_TIMEOUT_MS` on subprocess env from cfg — Task 3
- [x] `CONFIG_TOML_STUB` lists the key — Task 4
- [x] Tests cover layering + validation + bridge — Tasks 1 + 2 + 3
- [x] pytest / ruff / mypy strict all green — Task 5

## Out of scope

- Other Claude Code tool timeouts
- Renaming `BASH_MAX_TIMEOUT_MS` env var
