# Promote Sweep Knobs to TOML Config — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote `RALPH_ADO_AUTHOR_EMAIL` and `RALPH_STALE_DAYS` from env-only reads to first-class `ExecutorConfig` fields (`bot_author_email` + `stale_days`), with env vars surviving as overrides via the existing defaults < TOML < env resolver chain. `loop._run_sweep` reads directly from `cfg`; no env-bridge added.

**Architecture:** Same shape as PRs #16 (`git_host`) and #21 (`gh_owner` / `ado_org_url` / `ado_project` / `halt_webhook`). New entries in `_TOML_KNOWN_KEYS`, two new dataclass fields with defaults (so positional `ExecutorConfig(...)` test constructions keep compiling), two `_resolve_*` calls in `load_config()`, a validation guard for non-positive `stale_days`. `loop._run_sweep` already accepts `cfg: ExecutorConfig` — swap `os.environ.get(...)` reads for `cfg.bot_author_email` / `cfg.stale_days`. No bridge entries: both knobs have zero subprocess consumers (verified via `grep -rE 'RALPH_ADO_AUTHOR_EMAIL|RALPH_STALE_DAYS' --include='*.py'`).

**Tech Stack:** Python 3.12, dataclasses, `tomllib`, pytest, ruff, mypy strict.

**Confidence (per task):**

| Task | % | Why |
|---|---|---|
| 1. Failing config tests | 98% | Pattern exists at `test_config_toml.py:157+` (gh_owner/halt_webhook promotion). Direct replication. |
| 2. Promote knobs in config | 95% | Mechanical add to known patterns. Dataclass field-order trap is explicit in Step 2.3 (defaults must come last because `pr_check_poll_*` fields have none). |
| 3. Failing sweep tests | 95% | Fixtures + imports pre-flighted against `tests/executor/test_loop.py:17` (`from ralph_executor.queue.filesystem import FilesystemQueueSource`) and `loop.py:492` (`FilesystemQueueSource(cfg)` — not `cfg.repo_path`). Monkeypatch targets on `ralph_executor.sweep.runner.SweepConfig` and `ralph_executor.sweep.run` work because `_run_sweep` does lazy `from X import Y` inside the function body, which re-resolves attributes on the source module at call time. |
| 4. Refactor _run_sweep | 95% | Lines + replacement code explicit. Only risk: existing tests elsewhere that `setenv` these names — Step 4.3 includes a grep to find them. |
| 5. Stub doc update | 96% | Append text. Conditional test branch (skip if no existing snapshot test) avoids fabricating new test surface. |
| 6. Full-suite gate | 92% | Could surface a latent test that monkeypatched the env vars and is no longer wired to the new path. Step 6.1's "Common failure modes" lists the two known shapes + fixes. |

All tasks ≥ 90%. Pre-flight checks (line numbers, fixture shape, `_TOML_KNOWN_KEYS` membership, existing test patterns) verified against the current tree before writing the plan.

**Spec:** `docs/superpowers/specs/2026-05-27-promote-sweep-knobs-to-config-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `ralph_executor/config.py` | Modify | Add `bot_author_email` + `stale_days` to `_TOML_KNOWN_KEYS`, `ExecutorConfig`, and `load_config()` with positivity guard for `stale_days`. |
| `ralph_executor/loop.py` | Modify | Replace `os.environ.get(...)` reads in `_run_sweep` with `cfg.bot_author_email` / `cfg.stale_days`. Update the WARNING string and docstring. |
| `ralph_executor/setup_cmds.py` | Modify | Add two commented entries to `CONFIG_TOML_STUB`. |
| `tests/executor/conftest.py` | Modify | Add `bot_author_email=""` + `stale_days=3` to the `cfg_for_repo` fixture constructor. Add new env vars to relevant cleanup lists. |
| `tests/executor/test_config_toml.py` | Modify | Add layering tests (TOML wins, env wins over TOML, defaults) + validation test for non-positive `stale_days`. Add new env vars to `clean_env` cleanup. |
| `tests/executor/test_loop.py` | Modify | Add tests for `_run_sweep` skipping when `cfg.bot_author_email == ""` and passing `cfg.stale_days` through to `SweepConfig`. Remove or update any test that exercises the env-var path through `_run_sweep`. |

---

## Task 1: Add config layering + validation tests (failing)

**Why first:** TDD. Lock in the public-API contract (TOML key names, env name pairing, validation message) before touching the implementation.

**Files:**
- Modify: `tests/executor/test_config_toml.py`

- [ ] **Step 1.1: Add new env vars to the `clean_env` fixture cleanup list**

Open `tests/executor/test_config_toml.py`. Find the `clean_env` fixture (around line 22). Inside the `for var in (...)` tuple (around line 27), add two entries so the tests start from a known-empty env:

```python
@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> Path:
    """RALPH_REPO_PATH set, every overridable env var removed."""
    monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for var in (
        "RALPH_QUEUE_BRANCH",
        "RALPH_MAIN_BRANCH",
        "RALPH_MAX_ATTEMPTS",
        "RALPH_LOG_LEVEL",
        "RALPH_ITERATION_SLEEP_SECONDS",
        "RALPH_CLAUDE_BINARY",
        "RALPH_GIT_HOST",
        "GH_OWNER",
        "ADO_ORG_URL",
        "ADO_PROJECT",
        "RALPH_HALT_WEBHOOK",
        "RALPH_PR_CHECK_POLL_MAX_ATTEMPTS",
        "RALPH_PR_CHECK_POLL_INTERVAL_SECONDS",
        "RALPH_ADO_AUTHOR_EMAIL",
        "RALPH_STALE_DAYS",
    ):
        monkeypatch.delenv(var, raising=False)
    return git_repo
```

- [ ] **Step 1.2: Add layering tests for `bot_author_email` and `stale_days`**

Append the following tests to the end of `tests/executor/test_config_toml.py`:

```python
def test_sweep_knobs_picked_up_from_toml(clean_env: Path) -> None:
    """bot_author_email + stale_days flow from TOML into ExecutorConfig
    (the env-var read in loop._run_sweep is being retired)."""
    _write_toml(
        clean_env,
        """
        bot_author_email = "ralph-bot@example.com"
        stale_days = 7
        """,
    )
    cfg = load_config()
    assert cfg.bot_author_email == "ralph-bot@example.com"
    assert cfg.stale_days == 7


def test_env_wins_over_toml_for_sweep_knobs(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env names keep the historical ``RALPH_ADO_AUTHOR_EMAIL`` /
    ``RALPH_STALE_DAYS`` spelling for backwards compatibility — operators
    with these set today see no change."""
    _write_toml(
        clean_env,
        """
        bot_author_email = "from-toml@example.com"
        stale_days = 3
        """,
    )
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "from-env@example.com")
    monkeypatch.setenv("RALPH_STALE_DAYS", "10")
    cfg = load_config()
    assert cfg.bot_author_email == "from-env@example.com"
    assert cfg.stale_days == 10


def test_sweep_knobs_defaults_when_neither_set(clean_env: Path) -> None:
    cfg = load_config()
    assert cfg.bot_author_email == ""
    assert cfg.stale_days == 3


def test_stale_days_rejected_when_zero_in_toml(clean_env: Path) -> None:
    _write_toml(clean_env, "stale_days = 0\n")
    with pytest.raises(ConfigError, match="stale_days must be positive"):
        load_config()


def test_stale_days_rejected_when_negative_in_env(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RALPH_STALE_DAYS", "-1")
    with pytest.raises(ConfigError, match="stale_days must be positive"):
        load_config()
```

- [ ] **Step 1.3: Run the new tests and verify they fail**

Run:

```bash
uv run pytest tests/executor/test_config_toml.py -k "sweep_knobs or stale_days_rejected" -v
```

Expected: **5 FAILs**, all complaining about `AttributeError: 'ExecutorConfig' object has no attribute 'bot_author_email'` (or the same for `stale_days`).

- [ ] **Step 1.4: Commit**

```bash
git -C /c/Users/gethi/source/ralph add tests/executor/test_config_toml.py
git -C /c/Users/gethi/source/ralph commit -m "test(config): failing tests for bot_author_email + stale_days TOML promotion"
```

---

## Task 2: Promote the knobs in `ExecutorConfig`

**Why:** Make the tests from Task 1 pass by introducing the fields, resolver calls, and validation.

**Files:**
- Modify: `ralph_executor/config.py`
- Modify: `tests/executor/conftest.py`

- [ ] **Step 2.1: Add a default constant for `stale_days`**

Open `ralph_executor/config.py`. Below the existing `DEFAULT_*` constants block (around line 53, just before `_VALID_LOG_LEVEL_NAMES`), insert:

```python
# Sweep stale-PR threshold in days. PRs older than this in pending-pr/
# get moved to blocked/. Promoted from RALPH_STALE_DAYS env-only.
DEFAULT_STALE_DAYS = 3
```

- [ ] **Step 2.2: Add the new keys to `_TOML_KNOWN_KEYS`**

Find `_TOML_KNOWN_KEYS = frozenset({...})` (around line 59). Add the two new keys, grouped with their neighbours, with a short reason comment:

```python
_TOML_KNOWN_KEYS = frozenset(
    {
        "queue_branch",
        "main_branch",
        "max_attempts",
        "log_level",
        "iteration_sleep_seconds",
        "claude_binary",
        "git_host",
        # Per-host project identifiers + alerting. Promoted from env-only
        # in this layer because they are project state, not secrets.
        "gh_owner",
        "ado_org_url",
        "ado_project",
        "halt_webhook",
        # Sweep tuning. Promoted from env-only so operators can pin them
        # in .ralph/config.toml instead of exporting RALPH_* every shell.
        "bot_author_email",
        "stale_days",
        # CI-green verifier budget — see DEFAULT_PR_CHECK_POLL_* above.
        "pr_check_poll_max_attempts",
        "pr_check_poll_interval_seconds",
    }
)
```

- [ ] **Step 2.3: Add the new fields to `ExecutorConfig`**

Python forbids non-default fields after default fields in a dataclass. The existing `pr_check_poll_max_attempts: int` and `pr_check_poll_interval_seconds: float` have no defaults, so the new fields (which need defaults so positional `ExecutorConfig(...)` calls in conftest fixtures keep compiling) must go at the **end** of the dataclass — after `pr_check_poll_interval_seconds`.

Find the `@dataclass(frozen=True) class ExecutorConfig:` block (around line 87). After the existing `pr_check_poll_interval_seconds: float` line (around line 121), append:

```python
    pr_check_poll_max_attempts: int
    pr_check_poll_interval_seconds: float
    # Sweep tuning — promoted from env-only. ``bot_author_email`` is the
    # commit/PR author email ralph uses; sweep skips comments by this
    # author so the loop doesn't feed back into itself. Env name keeps
    # the historical ``RALPH_ADO_AUTHOR_EMAIL`` spelling for
    # backwards-compat — sweep is host-agnostic, so the TOML key drops
    # the misleading ADO prefix. ``stale_days`` is the pending-PR
    # staleness threshold (days), strictly positive (validated in
    # ``load_config``).
    bot_author_email: str = ""
    stale_days: int = DEFAULT_STALE_DAYS
```

The class now ends with these two fields at the bottom.

- [ ] **Step 2.4: Add resolver calls + validation in `load_config()`**

Find `load_config()` (around line 259). Locate the existing `halt_webhook = _resolve_str(...)` block (around line 343). Insert the two new resolver calls below it, before the `pr_check_poll_max_attempts = _resolve_int(...)` block:

```python
    halt_webhook = _resolve_str(
        name="halt_webhook",
        env_name="RALPH_HALT_WEBHOOK",
        toml_value=toml_overrides.get("halt_webhook"),
        default="",
        source_label=source_label,
    )
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
    pr_check_poll_max_attempts = _resolve_int(
        ...
```

- [ ] **Step 2.5: Pass new fields to the `ExecutorConfig(...)` constructor**

Find the `return ExecutorConfig(...)` block at the bottom of `load_config()` (around line 365). Append the two new keyword args after `pr_check_poll_interval_seconds`:

```python
    return ExecutorConfig(
        repo_path=repo_path,
        queue_branch=queue_branch,
        main_branch=main_branch,
        max_attempts=max_attempts,
        log_level=log_level,
        iteration_sleep_seconds=sleep_seconds,
        claude_binary=claude_binary,
        anthropic_api_key=anthropic_key,
        git_host=git_host,
        gh_owner=gh_owner,
        ado_org_url=ado_org_url,
        ado_project=ado_project,
        halt_webhook=halt_webhook,
        pr_check_poll_max_attempts=pr_check_poll_max_attempts,
        pr_check_poll_interval_seconds=pr_check_poll_interval_seconds,
        bot_author_email=bot_author_email,
        stale_days=stale_days,
    )
```

- [ ] **Step 2.6: Update the `cfg_for_repo` fixture to set explicit values**

Open `tests/executor/conftest.py`. Find the `cfg_for_repo` fixture (around line 187). The new fields have defaults so existing tests keep working without changes, but making the values explicit in the shared fixture prevents accidental drift if defaults change later. Add the two new keyword args at the end of the constructor call:

```python
@pytest.fixture
def cfg_for_repo(fake_repo: Path, fake_claude_binary: Path) -> ExecutorConfig:
    """Build an ExecutorConfig pointing at the fake repo + fake claude."""
    return ExecutorConfig(
        repo_path=fake_repo,
        queue_branch="ralph-queue",
        main_branch="main",
        max_attempts=3,
        log_level=20,  # logging.INFO
        iteration_sleep_seconds=0.0,
        claude_binary=str(fake_claude_binary),
        anthropic_api_key="fake-key",
        git_host="github",
        gh_owner="",
        ado_org_url="",
        ado_project="",
        halt_webhook="",
        pr_check_poll_max_attempts=6,
        pr_check_poll_interval_seconds=30.0,
        bot_author_email="",
        stale_days=3,
    )
```

- [ ] **Step 2.7: Run the failing tests and verify they now pass**

```bash
uv run pytest tests/executor/test_config_toml.py -k "sweep_knobs or stale_days_rejected" -v
```

Expected: **5 PASS**.

- [ ] **Step 2.8: Run the full config test module and mypy/ruff**

```bash
uv run pytest tests/executor/test_config_toml.py tests/executor/test_config.py -v
uv run ruff check ralph_executor/config.py tests/executor/conftest.py tests/executor/test_config_toml.py
uv run mypy --strict ralph_executor/config.py
```

Expected: all green. If `mypy --strict` flags the new fields, double-check the dataclass declaration matches the spec (`str` and `int`, defaults `""` and `DEFAULT_STALE_DAYS`).

- [ ] **Step 2.9: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/config.py tests/executor/conftest.py
git -C /c/Users/gethi/source/ralph commit -m "feat(config): promote bot_author_email + stale_days to ExecutorConfig"
```

---

## Task 3: Add `_run_sweep` cfg-driven tests (failing)

**Why:** TDD again for the loop-side change. Lock in the new WARNING shape and the `cfg.stale_days` passthrough before touching `loop.py`.

**Files:**
- Modify: `tests/executor/test_loop.py`

- [ ] **Step 3.1: Add an import for `dataclasses.replace` if not already present**

Open `tests/executor/test_loop.py`. At the top, confirm `dataclasses.replace` is importable. If `from dataclasses import replace` is missing, add it. (Used to derive variants of `cfg_for_repo` per test.)

- [ ] **Step 3.2: Add three new tests for the cfg-driven sweep behaviour**

Append these tests to the end of `tests/executor/test_loop.py`. Imports for `SweepConfig`, `_run_sweep`, and `FilesystemQueueSource` should match what the rest of the file uses — copy the existing pattern.

```python
def test_run_sweep_skips_when_bot_author_email_empty(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without bot_author_email set, sweep must skip with a WARNING that
    still mentions the legacy env-var name so operators grepping logs see
    the same anchor."""
    from dataclasses import replace
    from ralph_executor.queue.filesystem import FilesystemQueueSource
    from ralph_executor.loop import _run_sweep

    # Ensure no inherited env can satisfy the sweep — only cfg matters.
    monkeypatch.delenv("RALPH_ADO_AUTHOR_EMAIL", raising=False)

    cfg = replace(cfg_for_repo, bot_author_email="")
    source = FilesystemQueueSource(cfg)
    with caplog.at_level("WARNING", logger="ralph_executor.loop"):
        _run_sweep(cfg, source)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("bot_author_email" in m for m in msgs), msgs
    assert any("RALPH_ADO_AUTHOR_EMAIL" in m for m in msgs), msgs


def test_run_sweep_passes_cfg_values_to_sweep_config(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SweepConfig must be constructed from cfg fields, not from os.environ."""
    from dataclasses import replace
    from datetime import timedelta

    from ralph_executor.queue.filesystem import FilesystemQueueSource
    from ralph_executor.loop import _run_sweep

    # If _run_sweep regressed to reading env, this poisoned env would
    # cause SweepConfig to be built with the env value rather than cfg's.
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "WRONG@example.com")
    monkeypatch.setenv("RALPH_STALE_DAYS", "999")

    captured: dict[str, object] = {}

    class _SpySweepConfig:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "ralph_executor.sweep.runner.SweepConfig",
        _SpySweepConfig,
    )

    # Stub out the actual sweep run so we don't need a real PR skill on disk.
    monkeypatch.setattr(
        "ralph_executor.sweep.run",
        lambda ctx: None,  # type: ignore[misc]
    )
    # Bypass the scripts-path check.
    monkeypatch.setattr(
        "ralph_executor.loop._pr_skill_scripts_path",
        lambda cfg: fake_repo,
    )

    cfg = replace(cfg_for_repo, bot_author_email="ralph@x.test", stale_days=5)
    _run_sweep(cfg, FilesystemQueueSource(cfg))

    assert captured["ralph_author_email"] == "ralph@x.test"
    assert captured["stale_threshold"] == timedelta(days=5)
    assert captured["max_attempts"] == cfg.max_attempts


def test_run_sweep_does_not_read_env_for_promoted_knobs(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a regression where someone re-adds os.environ.get for these
    two names inside _run_sweep."""
    import ralph_executor.loop as loop_mod

    src = Path(loop_mod.__file__).read_text(encoding="utf-8")
    assert 'os.environ.get("RALPH_ADO_AUTHOR_EMAIL"' not in src
    assert "os.environ.get(\"RALPH_STALE_DAYS\"" not in src
```

- [ ] **Step 3.3: Run the new tests and verify they fail (or document why one already passes)**

```bash
uv run pytest tests/executor/test_loop.py -k "run_sweep_" -v
```

Expected: the first two FAIL because `_run_sweep` still reads env vars. The third (source grep) may already FAIL because the env reads are still present. All three should pass once Task 4 lands.

- [ ] **Step 3.4: Commit**

```bash
git -C /c/Users/gethi/source/ralph add tests/executor/test_loop.py
git -C /c/Users/gethi/source/ralph commit -m "test(loop): failing tests for cfg-driven _run_sweep"
```

---

## Task 4: Refactor `_run_sweep` to read from `cfg`

**Why:** Make the Task 3 tests pass by removing the env reads.

**Files:**
- Modify: `ralph_executor/loop.py`

- [ ] **Step 4.1: Replace the env-driven body with cfg reads**

Open `ralph_executor/loop.py`. Find `_run_sweep` (around line 90). Replace lines 99 through 137 (from the docstring `Production-safety:` paragraph through the `if stale_days <= 0:` block, and the immediately-following `SweepConfig(...)` constructor at line 142) with the version below.

The replacement covers two contiguous edits:

**a) Docstring (lines 99–105)** — replace the `Production-safety:` paragraph with:

```python
    """Drive one sweep over ``.ralph/pending-pr/`` (Plan 8).

    Builds a ``SweepContext`` from the executor config and current
    environment, then delegates to ``ralph_executor.sweep.run``. The
    ``source`` argument is unused — the sweep reads ``.ralph/pending-pr/``
    directly from the filesystem so it can stay isolated from the queue
    abstraction.

    Production-safety: the sweep needs ``cfg.bot_author_email`` (used to
    skip ralph-authored PR comments so the loop doesn't feed back into
    itself) and a PR-skill scripts directory matching the configured git
    host. If either is missing the sweep is skipped with a WARNING — the
    loop must keep running rather than abort, since pre-Plan-8 deployments
    and the bulk of the executor test suite don't set the author email.
    Validation of ``cfg.stale_days`` (must be positive) lives in
    ``config.load_config``; this function trusts the value.
    """
```

**b) Body (lines 106–147)** — replace the env reads, int-parse, positivity guard, and the `SweepConfig(...)` constructor's two relevant fields:

```python
    del source  # sweep walks the filesystem directly
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

    from ralph_executor.sweep import run as run_sweep
    from ralph_executor.sweep.runner import SweepConfig, SweepContext

    sweep_cfg = SweepConfig(
        ralph_author_email=cfg.bot_author_email,
        max_attempts=cfg.max_attempts,
        stale_threshold=timedelta(days=cfg.stale_days),
        now=datetime.now(tz=UTC),
    )
    sweep_ctx = SweepContext(
        queue_root=cfg.repo_path / ".ralph",
        ado_pr_scripts_path=scripts_path,
        config=sweep_cfg,
    )
    result = run_sweep(ctx=sweep_ctx)
    log.info(
        "sweep: scanned %d PBIs (actions=%d, errors=%d)",
        result.pbis_scanned,
        len(result.actions),
        len(result.errors),
    )
```

What was removed:

- `ralph_email = os.environ.get("RALPH_ADO_AUTHOR_EMAIL", "").strip()`
- `raw_days = os.environ.get("RALPH_STALE_DAYS", "3").strip() or "3"`
- The `try / except ValueError` around int parsing
- The `if stale_days <= 0:` fallback block (replaced by the `ConfigError` raise added to `load_config` in Task 2)

- [ ] **Step 4.2: Run the loop tests and verify they pass**

```bash
uv run pytest tests/executor/test_loop.py -k "run_sweep_" -v
```

Expected: all three PASS.

- [ ] **Step 4.3: Run the full loop test suite + mypy/ruff**

```bash
uv run pytest tests/executor/test_loop.py tests/executor/test_loop_integration.py -v
uv run ruff check ralph_executor/loop.py tests/executor/test_loop.py
uv run mypy --strict ralph_executor/loop.py
```

Expected: all green. If any existing test in `test_loop.py` previously relied on `monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", ...)` to make `_run_sweep` run, it must now either set `bot_author_email` on the `cfg` via `dataclasses.replace` or be removed because the env-only path no longer exists. Hunt with: `grep -n "RALPH_ADO_AUTHOR_EMAIL\\|RALPH_STALE_DAYS" tests/executor/test_loop.py` and update any callers.

- [ ] **Step 4.4: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/loop.py
git -C /c/Users/gethi/source/ralph commit -m "refactor(loop): _run_sweep reads cfg.bot_author_email + cfg.stale_days directly"
```

---

## Task 5: Update `CONFIG_TOML_STUB`

**Why:** Operators running `ralph scaffold` (or reading the stub by hand) need to see the new keys as commented examples. Without this, the spec's "operator surface" surface area is incomplete.

**Files:**
- Modify: `ralph_executor/setup_cmds.py`
- Modify: `tests/executor/test_setup_cmds.py` (only if there are existing snapshot tests for the stub)

- [ ] **Step 5.1: Check whether existing tests assert stub content**

```bash
grep -n "CONFIG_TOML_STUB\|gh_owner\|halt_webhook" tests/executor/test_setup_cmds.py
```

If no matches: no test edit needed; proceed to Step 5.2 only.
If matches: existing tests assert the stub's content. Add assertions for the new keys alongside them in Step 5.3.

- [ ] **Step 5.2: Extend `CONFIG_TOML_STUB`**

Open `ralph_executor/setup_cmds.py`. Find `CONFIG_TOML_STUB = """\` (around line 30). After the `# halt_webhook = "https://outlook.office.com/webhook/..."` line and before the closing `"""`, append:

```
#
# Sweep tuning (promoted from env-only). Required for sweep to run; if
# bot_author_email is unset, sweep is skipped each iteration with a
# WARNING. Env overrides keep the historical RALPH_ADO_AUTHOR_EMAIL /
# RALPH_STALE_DAYS spelling for backwards compatibility.
# bot_author_email = "ralph-bot@example.com"
# stale_days = 3
```

The full block looks like:

```python
CONFIG_TOML_STUB = """\
# ralph-executor per-repo configuration.
#
# All keys are optional. Defaults are baked into ralph_executor.config.
# Environment variables (RALPH_*) override anything set here; CLI flags
# override env. See the load_config docstring for the precedence chain.
#
# queue_branch = "ralph-queue"
# main_branch = "main"
# max_attempts = 20
# iteration_sleep_seconds = 30
# claude_binary = "claude"
# log_level = "INFO"
# git_host = "github"   # or "ado"
#
# Project identifiers + alerting (promoted from env-only). Set these
# instead of exporting $GH_OWNER / $ADO_ORG_URL / $ADO_PROJECT /
# $RALPH_HALT_WEBHOOK every shell. Secrets (GH_TOKEN, ADO_PAT) stay
# env-only by policy.
# gh_owner = "your-github-org-or-user"
# ado_org_url = "https://dev.azure.com/your-org"
# ado_project = "your-ado-project"
# halt_webhook = "https://outlook.office.com/webhook/..."
#
# Sweep tuning (promoted from env-only). Required for sweep to run; if
# bot_author_email is unset, sweep is skipped each iteration with a
# WARNING. Env overrides keep the historical RALPH_ADO_AUTHOR_EMAIL /
# RALPH_STALE_DAYS spelling for backwards compatibility.
# bot_author_email = "ralph-bot@example.com"
# stale_days = 3
"""
```

- [ ] **Step 5.3: Update the snapshot test if it exists**

If Step 5.1 found matches, add the new key assertions next to the existing ones. Pattern (copy the existing assertion style — don't invent a new one):

```python
def test_config_toml_stub_lists_new_keys() -> None:
    from ralph_executor.setup_cmds import CONFIG_TOML_STUB

    assert "bot_author_email" in CONFIG_TOML_STUB
    assert "stale_days" in CONFIG_TOML_STUB
    # Backwards-compat env names should be referenced so operators with
    # them set today can find their way to the new TOML keys.
    assert "RALPH_ADO_AUTHOR_EMAIL" in CONFIG_TOML_STUB
    assert "RALPH_STALE_DAYS" in CONFIG_TOML_STUB
```

If `test_setup_cmds.py` doesn't already assert stub content, **do not add a new test file or new assertions**. The stub is documentation; the layering tests in Task 1 are the contract. Skip Step 5.3 in that case.

- [ ] **Step 5.4: Run any setup-cmds tests + ruff**

```bash
uv run pytest tests/executor/test_setup_cmds.py -v
uv run ruff check ralph_executor/setup_cmds.py
```

Expected: all green.

- [ ] **Step 5.5: Commit**

```bash
git -C /c/Users/gethi/source/ralph add ralph_executor/setup_cmds.py tests/executor/test_setup_cmds.py
git -C /c/Users/gethi/source/ralph commit -m "docs(setup): document bot_author_email + stale_days in CONFIG_TOML_STUB"
```

(If `test_setup_cmds.py` was not touched, drop it from the `git add` list — git will reject staging an unchanged path silently, but better to keep the command honest.)

---

## Task 6: Full-suite green + final commit

**Why:** Sanity check that no other module broke (test_loop_integration, sweep/runner imports, etc.).

- [ ] **Step 6.1: Run the full ralph_executor test suite**

```bash
uv run pytest tests/ -v
```

Expected: all green. Common failure modes:

- A test elsewhere did `monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", ...)` and called `_run_sweep` expecting it to run. Fix by switching to `dataclasses.replace(cfg, bot_author_email=...)`.
- `ExecutorConfig(...)` positional call somewhere outside `conftest.py` broke because the new fields aren't keyword-only — should not happen because new fields have defaults, but worth confirming.
- `mypy --strict` complains about a missing field in a hand-rolled `ExecutorConfig(...)` construction elsewhere.

- [ ] **Step 6.2: Run ruff + mypy across the whole package**

```bash
uv run ruff check ralph_executor/ tests/
uv run mypy --strict ralph_executor/
```

Expected: all green.

- [ ] **Step 6.3: Confirm no env-var leakage remains**

```bash
grep -rEn 'os\.environ.*RALPH_ADO_AUTHOR_EMAIL|os\.environ.*RALPH_STALE_DAYS' ralph_executor/
```

Expected: **no output** outside `config.py` (where `_resolve_str` / `_resolve_int` legitimately read these names as part of the layering chain). The grep should return zero lines.

- [ ] **Step 6.4: Smoke-check the WARNING shape**

```bash
uv run python -c "from dataclasses import replace; from tests.executor.conftest import _ ; pass"
```

Optional. If you want a runtime smoke instead, build a fake repo with `.ralph/` and an empty `.ralph/config.toml`, run `ralph` against it, and confirm the WARNING line contains both `bot_author_email` and `RALPH_ADO_AUTHOR_EMAIL`. Reference example log line:

```
WARNING ralph_executor.loop: sweep: bot_author_email is not set (TOML key 'bot_author_email' or env RALPH_ADO_AUTHOR_EMAIL); skipping sweep this iteration
```

---

## Acceptance (matches the spec)

- [x] `ExecutorConfig` exposes `bot_author_email: str` + `stale_days: int` — Task 2
- [x] `load_config()` resolves both via defaults < TOML < env — Task 2
- [x] `load_config()` rejects non-positive `stale_days` — Task 2 (test in Task 1)
- [x] `loop._run_sweep` reads from `cfg`; zero `os.environ.get` calls for these two names — Task 4 (regression-test in Task 3)
- [x] `CONFIG_TOML_STUB` includes both as commented examples — Task 5
- [x] Layering tests + validation tests in `test_config_toml.py` — Tasks 1 + 2
- [x] `_run_sweep` tests cover cfg-driven skip + passthrough — Tasks 3 + 4
- [x] pytest / ruff / mypy strict all green — Task 6

## Out of scope (per spec)

- Renaming the `RALPH_ADO_AUTHOR_EMAIL` env var
- Adding bridge entries in `cli._export_cfg_to_env` for these knobs
- Promoting any other env-only knob (full audit in spec § 5)

## Operational notes

- **Branch:** This work lands on `ralph-queue` (or a feature branch off `main`, depending on the operator's flow). Tasks are written as direct commits; if you'd rather one PR-per-task or one PR for the whole plan, squash at the end.
- **Ralph running:** If ralph is running against this checkout, halt it before Task 4 because `loop._run_sweep` is touched. Resume after Task 6.
