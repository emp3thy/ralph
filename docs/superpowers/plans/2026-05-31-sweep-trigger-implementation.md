# Sweep Trigger + Idle Backoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current "sweep only when `current/` is empty at iter entry" trigger with three explicit events (startup, post-move-out, on-current-empty) and add adaptive idle sleep (5 min on idle, 30 s baseline on busy/non-idle).

**Architecture:** Inside `iterate_once`, add a conditional `_run_sweep` call after the cycle-detector check when the iteration outcome was a move-out (`ran_pr_created` or `ran_stuck`). In `run_loop`, fire a one-shot startup sweep before the loop and pick the per-iteration sleep duration from a new `iteration_idle_sleep_seconds` config field. The existing "sweep when current empty before pick_next" call stays untouched.

**Tech Stack:** Python 3.12 stdlib only. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-05-31-sweep-trigger-design.md`
**Standards consulted:** `standards/ralph-runtime.md` — confidence scoring + 3-bucket assumptions applied below.

---

## Forward-reference audit

Task ordering does not introduce import-time forward references:
- T2 (TOML stub) edits the `CONFIG_TOML_STUB` constant in `setup_cmds.py`; no dependency on T1's field.
- T3 (startup sweep) imports `_run_sweep` from `loop.py` itself — same module — no new imports.
- T4 (post-move-out sweep) is in-place edit of `iterate_once` — no new imports.
- T5 (adaptive sleep) reads `cfg.iteration_idle_sleep_seconds` added in T1.

T5 strictly depends on T1; T3, T4 are independent. Sequencing T1 → T2 → T3 → T4 → T5 → T6 is the chosen safe order.

## Lint-cleanliness conventions

- `from datetime import UTC` — UP017.
- Drop unused `import pytest`; import only what test bodies use — F401.
- Type hints on test helpers; mypy clean.

---

## File Structure

### Modified files

- `ralph_executor/config.py` — `iteration_idle_sleep_seconds` field + default constant + `_TOML_KNOWN_KEYS` entry + resolution in `load_config`.
- `ralph_executor/setup_cmds.py` — extend `CONFIG_TOML_STUB` with the commented new knob.
- `ralph_executor/loop.py` — `run_loop` startup catch-up sweep + adaptive idle sleep; `iterate_once` post-cycle move-out sweep.
- `tests/executor/test_config.py` — extend.
- `tests/executor/test_loop.py` — extend.
- `tests/executor/test_loop_integration.py` — extend (one new integration test).
- `README.md` — document the new knob alongside the existing idle/watch documentation.
- `docs/runbooks/ralph-setup.md` — add the new knob to the config-knob table.

### No new files

This is a surgical change — six tasks, no new modules.

---

## Task T1: `iteration_idle_sleep_seconds` config field — **confidence: 97%**

**Files:** `ralph_executor/config.py` (modify), `tests/executor/test_config.py` (extend)

- [ ] **Step 1: Failing tests**

Append to `tests/executor/test_config.py`:

```python
def test_iteration_idle_sleep_seconds_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("RALPH_ITERATION_IDLE_SLEEP_SECONDS", raising=False)
    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue")
    _prepare_project_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "project")
    cfg = load_config()
    assert cfg.iteration_idle_sleep_seconds == 300.0


def test_iteration_idle_sleep_seconds_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("RALPH_ITERATION_IDLE_SLEEP_SECONDS", "120")
    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue")
    _prepare_project_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "project")
    cfg = load_config()
    assert cfg.iteration_idle_sleep_seconds == 120.0


def test_iteration_idle_sleep_seconds_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("RALPH_ITERATION_IDLE_SLEEP_SECONDS", raising=False)
    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue")
    _prepare_project_repo(tmp_path)
    project_toml = tmp_path / "project" / ".ralph" / "config.toml"
    project_toml.parent.mkdir(parents=True, exist_ok=True)
    project_toml.write_text("iteration_idle_sleep_seconds = 600\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "project")
    cfg = load_config()
    assert cfg.iteration_idle_sleep_seconds == 600.0


def test_iteration_idle_sleep_seconds_negative_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("RALPH_ITERATION_IDLE_SLEEP_SECONDS", "-1")
    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue")
    _prepare_project_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "project")
    with pytest.raises(ConfigError, match="iteration_idle_sleep_seconds"):
        load_config()


def test_iteration_idle_sleep_seconds_zero_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("RALPH_ITERATION_IDLE_SLEEP_SECONDS", "0")
    _prepare_user_config(tmp_path, queue_repo="https://github.com/owner/queue")
    _prepare_project_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "project")
    with pytest.raises(ConfigError, match="iteration_idle_sleep_seconds"):
        load_config()
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/executor/test_config.py -v -k iteration_idle_sleep_seconds
```

Expected: FAIL — `AttributeError: 'ExecutorConfig' object has no attribute 'iteration_idle_sleep_seconds'`.

- [ ] **Step 3: Implementation**

In `ralph_executor/config.py`:

a. Add the constant near `DEFAULT_ITERATION_SLEEP_SECONDS`:

```python
# Adaptive idle backoff: when an iteration outcome is ``idle`` the loop
# sleeps this many seconds instead of ``DEFAULT_ITERATION_SLEEP_SECONDS``.
# A quiet ralph stops burning cycles polling its inbox. The first non-idle
# outcome implicitly resets cadence to the busy baseline because
# ``run_loop`` re-evaluates the sleep duration each iteration.
DEFAULT_ITERATION_IDLE_SLEEP_SECONDS = 300.0
```

b. Extend `_TOML_KNOWN_KEYS` with `"iteration_idle_sleep_seconds"`.

c. Add the field to `ExecutorConfig` (alongside the other timing knobs):

```python
    # Adaptive idle backoff — see DEFAULT_ITERATION_IDLE_SLEEP_SECONDS.
    iteration_idle_sleep_seconds: float = DEFAULT_ITERATION_IDLE_SLEEP_SECONDS
```

d. Add resolution in `load_config` (next to `sleep_seconds` resolution):

```python
    idle_sleep_seconds = _resolve_float(
        name="iteration_idle_sleep_seconds",
        env_name="RALPH_ITERATION_IDLE_SLEEP_SECONDS",
        toml_value=toml_overrides.get("iteration_idle_sleep_seconds"),
        default=DEFAULT_ITERATION_IDLE_SLEEP_SECONDS,
        source_label=source_label,
    )
    if idle_sleep_seconds <= 0:
        raise ConfigError(
            f"{source_label}: iteration_idle_sleep_seconds must be positive "
            f"(got {idle_sleep_seconds})"
        )
```

e. Pass `iteration_idle_sleep_seconds=idle_sleep_seconds` into the `ExecutorConfig(...)` constructor.

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/executor/test_config.py -v -k iteration_idle_sleep_seconds
```

Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/config.py tests/executor/test_config.py
git commit -m "sweep-trigger: iteration_idle_sleep_seconds config field"
```

---

## Task T2: TOML stub documentation — **confidence: 99%**

**Files:** `ralph_executor/setup_cmds.py` (modify)

- [ ] **Step 1: Implementation**

Append to the existing `CONFIG_TOML_STUB` constant in `ralph_executor/setup_cmds.py`, after the existing `iteration_sleep_seconds` reference (line ~49):

```python
# Adaptive idle backoff. When an iteration's outcome is ``idle`` the loop
# sleeps this many seconds instead of ``iteration_sleep_seconds``. The
# first non-idle outcome implicitly resets cadence. Default 300 (5 min).
# Must be positive.
# iteration_idle_sleep_seconds = 300
```

- [ ] **Step 2: Smoke test**

```
uv run python -c "from ralph_executor.setup_cmds import CONFIG_TOML_STUB; assert 'iteration_idle_sleep_seconds' in CONFIG_TOML_STUB"
```

Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```
git add ralph_executor/setup_cmds.py
git commit -m "sweep-trigger: document iteration_idle_sleep_seconds in TOML stub"
```

---

## Task T3: Startup catch-up sweep in `run_loop` — **confidence: 94%**

**Files:** `ralph_executor/loop.py` (modify), `tests/executor/test_loop.py` (extend)

**Step 0 mitigation (verified):** `run_loop` is at `ralph_executor/loop.py:946-1003`. The iteration body starts at the `while True:` block (~line 974). Insert the startup sweep BEFORE the `while`. The existing `iterate_once` calls `_pull_queue` first, so the startup sweep needs its own `_pull_queue` to ensure the queue clone exists before `_run_sweep` runs.

- [ ] **Step 1: Failing tests**

```python
def test_run_loop_startup_sweep_fires_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_loop calls _run_sweep exactly once before entering the loop."""
    from ralph_executor.loop import run_loop
    from ralph_executor.queue.filesystem import FilesystemQueueSource

    cfg = _build_minimal_cfg_for_loop(tmp_path, iterations=1)
    sweep_calls: list[FilesystemQueueSource] = []

    def fake_sweep(cfg_arg, source_arg):
        sweep_calls.append(source_arg)

    monkeypatch.setattr("ralph_executor.loop._run_sweep", fake_sweep)

    # iterate_once is unstubbed; runs against fake_repo with empty inbox.
    list(run_loop(cfg, max_iterations=1))
    # Startup sweep + at least one in-loop sweep on empty current/.
    # The startup count is at least 1.
    assert len(sweep_calls) >= 1


def test_run_loop_startup_sweep_failure_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """If startup _run_sweep raises, run_loop logs WARNING and proceeds."""
    import logging
    from ralph_executor.loop import run_loop

    cfg = _build_minimal_cfg_for_loop(tmp_path, iterations=1)
    call_log: list[str] = []

    def boom_then_ok(cfg_arg, source_arg):
        if not call_log:
            call_log.append("startup")
            raise RuntimeError("simulated startup sweep failure")
        call_log.append("in-loop")

    monkeypatch.setattr("ralph_executor.loop._run_sweep", boom_then_ok)

    with caplog.at_level(logging.WARNING):
        list(run_loop(cfg, max_iterations=1))
    assert "startup sweep" in caplog.text.lower()
    assert call_log == ["startup", "in-loop"]
```

Helper `_build_minimal_cfg_for_loop` reuses the existing `cfg_for_repo` fixture pattern; create or extend it as needed.

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/executor/test_loop.py -v -k startup_sweep
```

Expected: FAIL — `run_loop` does not yet call `_run_sweep` at startup.

- [ ] **Step 3: Implementation**

In `ralph_executor/loop.py::run_loop`, AFTER the `cli_log = logging.getLogger("ralph_executor.cli")` line and BEFORE the `while True:` loop body:

```python
    # Startup catch-up: one sweep before the iteration loop begins so a
    # ralph that just restarted reads PR state up-to-date before the
    # first claim. Sweep failure is logged at WARNING and does NOT block
    # the loop — the iteration body's own sweep paths will retry later.
    try:
        _pull_queue(cfg)
        source = FilesystemQueueSource(cfg)
        _run_sweep(cfg, source)
    except Exception:
        log.warning("startup sweep failed; proceeding to iteration loop", exc_info=True)
```

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/executor/test_loop.py -v -k startup_sweep
```

Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/loop.py tests/executor/test_loop.py
git commit -m "sweep-trigger: run_loop startup catch-up sweep"
```

---

## Task T4: Post-move-out sweep in `iterate_once` — **confidence: 93%**

**Files:** `ralph_executor/loop.py` (modify), `tests/executor/test_loop.py` (extend)

**Step 0 mitigation (verified):** The `iterate_once` current-occupied branch is at `ralph_executor/loop.py:825-879` per re-read. Insertion point: AFTER the `_check_cycle_detector(cfg, source)` call (around line 870) and BEFORE the `return result` statement. The outcome strings `"ran_pr_created"` and `"ran_stuck"` are existing literals in the `IterationOutcome` enum at `loop.py:93-105`.

- [ ] **Step 1: Failing tests**

```python
def test_iterate_once_sweeps_after_pr_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After ran_pr_created, _run_sweep is called once before iterate_once returns."""
    from ralph_executor.loop import iterate_once

    cfg = _build_two_repo_fixture_with_current(tmp_path, outcome="pr_created")
    sweep_call_count = 0

    def fake_sweep(cfg_arg, source_arg):
        nonlocal sweep_call_count
        sweep_call_count += 1

    monkeypatch.setattr("ralph_executor.loop._run_sweep", fake_sweep)
    result = iterate_once(cfg)
    assert result.outcome == "ran_pr_created"
    assert sweep_call_count == 1


def test_iterate_once_sweeps_after_stuck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After ran_stuck, _run_sweep is called once before iterate_once returns."""
    from ralph_executor.loop import iterate_once

    cfg = _build_two_repo_fixture_with_current(tmp_path, outcome="stuck")
    sweep_call_count = 0

    def fake_sweep(cfg_arg, source_arg):
        nonlocal sweep_call_count
        sweep_call_count += 1

    monkeypatch.setattr("ralph_executor.loop._run_sweep", fake_sweep)
    result = iterate_once(cfg)
    assert result.outcome == "ran_stuck"
    assert sweep_call_count == 1


def test_iterate_once_no_sweep_after_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After ran_partial, _run_sweep is NOT called."""
    from ralph_executor.loop import iterate_once

    cfg = _build_two_repo_fixture_with_current(tmp_path, outcome="partial")
    sweep_call_count = 0
    monkeypatch.setattr(
        "ralph_executor.loop._run_sweep",
        lambda cfg_arg, source_arg: (lambda: None)(),
    )

    def counting(cfg_arg, source_arg):
        nonlocal sweep_call_count
        sweep_call_count += 1

    monkeypatch.setattr("ralph_executor.loop._run_sweep", counting)
    result = iterate_once(cfg)
    assert result.outcome == "ran_partial"
    assert sweep_call_count == 0


def test_iterate_once_no_sweep_after_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After ran_error, _run_sweep is NOT called."""
    from ralph_executor.loop import iterate_once

    cfg = _build_two_repo_fixture_with_current(tmp_path, outcome="error")
    sweep_call_count = 0

    def counting(cfg_arg, source_arg):
        nonlocal sweep_call_count
        sweep_call_count += 1

    monkeypatch.setattr("ralph_executor.loop._run_sweep", counting)
    result = iterate_once(cfg)
    assert result.outcome == "ran_error"
    assert sweep_call_count == 0


def test_iterate_once_sweep_failure_after_move_out_does_not_poison_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """If post-cycle sweep raises, iterate_once still returns the original outcome."""
    import logging
    from ralph_executor.loop import iterate_once

    cfg = _build_two_repo_fixture_with_current(tmp_path, outcome="pr_created")

    def boom(cfg_arg, source_arg):
        raise RuntimeError("simulated post-cycle sweep failure")

    monkeypatch.setattr("ralph_executor.loop._run_sweep", boom)
    with caplog.at_level(logging.WARNING):
        result = iterate_once(cfg)
    assert result.outcome == "ran_pr_created"
    assert "post-move-out sweep" in caplog.text.lower()
```

Helper `_build_two_repo_fixture_with_current` reuses the canonical conftest pattern, parameterised by the claude-spawn stub outcome.

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/executor/test_loop.py -v -k iterate_once and (sweep or move_out)
```

Expected: FAIL — sweep count is 0 because the new call site does not exist yet.

- [ ] **Step 3: Implementation**

In `ralph_executor/loop.py::iterate_once`, immediately AFTER the existing `if _check_cycle_detector(cfg, source): ...` block (and its `HaltedError` raise) and BEFORE the existing `return result` statement at the end of the current-occupied branch:

```python
        # Post-move-out sweep (trigger 2). Fires AFTER persist + cycle so
        # the cycle detector windows on the iteration's own events without
        # noise from sweep emissions. Failure is logged at WARNING and the
        # iteration still returns its original outcome — sweep is a
        # nice-to-have at this hook, not load-bearing.
        if result.outcome in ("ran_pr_created", "ran_stuck"):
            try:
                _run_sweep(cfg, source)
            except Exception:
                log.warning(
                    "post-move-out sweep failed; iteration %s outcome preserved",
                    result.outcome,
                    exc_info=True,
                )
```

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/executor/test_loop.py -v -k iterate_once and (sweep or move_out)
```

Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/loop.py tests/executor/test_loop.py
git commit -m "sweep-trigger: iterate_once sweep after persist+cycle on move-out"
```

---

## Task T5: Adaptive idle sleep in `run_loop` — **confidence: 95%**

**Files:** `ralph_executor/loop.py` (modify), `tests/executor/test_loop.py` (extend)

- [ ] **Step 1: Failing tests**

```python
def test_run_loop_idle_uses_idle_sleep_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idle outcomes sleep iteration_idle_sleep_seconds, not the busy baseline."""
    import time
    from ralph_executor.loop import run_loop

    cfg = _build_minimal_cfg_for_loop(
        tmp_path, iterations=1,
        iteration_sleep_seconds=30.0,
        iteration_idle_sleep_seconds=300.0,
    )
    # Force iterate_once to return idle.
    monkeypatch.setattr(
        "ralph_executor.loop.iterate_once",
        lambda cfg_arg: IterationResult(outcome="idle", pbi_id=None),
    )
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: recorded.append(s))

    # watch_mode=True so the loop sleeps on idle instead of exiting on
    # idle_exit_threshold.
    cfg = dataclasses.replace(cfg, watch_mode=True)
    list(run_loop(cfg, max_iterations=1))
    assert recorded == [300.0]


def test_run_loop_non_idle_does_not_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-idle outcomes (claimed / ran_*) do not sleep."""
    import time
    from ralph_executor.loop import run_loop

    cfg = _build_minimal_cfg_for_loop(tmp_path, iterations=1)
    monkeypatch.setattr(
        "ralph_executor.loop.iterate_once",
        lambda cfg_arg: IterationResult(outcome="claimed", pbi_id="WI-1"),
    )
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: recorded.append(s))
    list(run_loop(cfg, max_iterations=1))
    assert recorded == []


def test_run_loop_idle_then_claimed_resets_cadence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idle (5min sleep) → claimed (no sleep) → idle (5min sleep again).

    The reset is implicit (each iteration recomputes the sleep duration).
    """
    import time
    from ralph_executor.loop import run_loop

    cfg = _build_minimal_cfg_for_loop(
        tmp_path, iterations=3,
        iteration_idle_sleep_seconds=300.0,
    )
    cfg = dataclasses.replace(cfg, watch_mode=True)
    outcomes = iter([
        IterationResult(outcome="idle", pbi_id=None),
        IterationResult(outcome="claimed", pbi_id="WI-1"),
        IterationResult(outcome="idle", pbi_id=None),
    ])
    monkeypatch.setattr(
        "ralph_executor.loop.iterate_once",
        lambda cfg_arg: next(outcomes),
    )
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: recorded.append(s))
    list(run_loop(cfg, max_iterations=3))
    assert recorded == [300.0, 300.0]  # two idle sleeps, claimed in between
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/executor/test_loop.py -v -k idle_sleep or non_idle_does_not_sleep or resets_cadence
```

Expected: FAIL — current code sleeps for `cfg.iteration_sleep_seconds` (30.0), not `cfg.iteration_idle_sleep_seconds` (300.0).

- [ ] **Step 3: Implementation**

In `ralph_executor/loop.py::run_loop`, replace the existing trailing block:

```python
        # Sleep only between iterations that found nothing to do.
        if result.outcome == "idle":
            time.sleep(cfg.iteration_sleep_seconds)
```

with:

```python
        # Sleep only between iterations that found nothing to do.
        # Adaptive backoff: idle outcomes sleep iteration_idle_sleep_seconds
        # (default 300 s). The first non-idle outcome implicitly resets
        # cadence because this branch is re-evaluated each iteration.
        if result.outcome == "idle":
            time.sleep(cfg.iteration_idle_sleep_seconds)
```

- [ ] **Step 4: Run tests to verify pass**

```
uv run pytest tests/executor/test_loop.py -v -k idle_sleep or non_idle_does_not_sleep or resets_cadence
```

Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```
git add ralph_executor/loop.py tests/executor/test_loop.py
git commit -m "sweep-trigger: adaptive idle sleep in run_loop"
```

---

## Task T6: End-to-end integration test — **confidence: 92%**

**Files:** `tests/executor/test_loop_integration.py` (extend)

**Step 0 mitigation:** Reuse the existing `cfg_for_repo` + `sample_pbi` fixtures from `tests/executor/conftest.py`. Stub claude to return `pr_created`; assert sweep fires AFTER `pending-pr/` has the PBI and AFTER any cycle-detector event the iteration produced.

- [ ] **Step 1: Failing test**

```python
def test_iterate_once_sweep_fires_after_pr_created_against_real_fixture(
    cfg_for_repo, sample_pbi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration: end-to-end iterate_once with pr_created outcome shows
    sweep firing AFTER the move-out has landed in pending-pr/."""
    from ralph_executor.loop import iterate_once, _queue_repo_root

    # Stub spawn_claude_p to return pr_created with a fake PR URL.
    from ralph_executor.claude_spawn import ClaudeOutcome

    def fake_spawn(*args, **kwargs):
        return ClaudeOutcome(
            kind="pr_created",
            pr_url="https://github.com/test/repo/pull/1",
            stdout="", stderr="",
            exit_code=0, duration_seconds=1.0,
        )

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", fake_spawn)

    # Pre-claim the PBI into current/ via _claim_pbi.
    # (See conftest write_sample_pbi for the inbox-initial position.)
    # Drive one iteration; assert sweep ran AFTER the move.
    sweep_observed_state: list[dict[str, int]] = []

    def observing_sweep(cfg_arg, source_arg):
        queue = _queue_repo_root(cfg_arg)
        sweep_observed_state.append({
            "current": len(list((queue / ".ralph" / "current").iterdir())),
            "pending_pr": len(list((queue / ".ralph" / "pending-pr").iterdir())),
        })

    monkeypatch.setattr("ralph_executor.loop._run_sweep", observing_sweep)

    # First call: claim from inbox.
    iterate_once(cfg_for_repo)
    # Second call: spawn returns pr_created; sweep should observe
    # pending-pr/ depth >= 1 (PBI just moved there).
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "ran_pr_created"
    assert any(s["pending_pr"] >= 1 for s in sweep_observed_state)
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/executor/test_loop_integration.py -v -k sweep_fires_after_pr_created
```

Expected: FAIL — sweep is not yet wired into the move-out hook.

- [ ] **Step 3: Implementation**

No new implementation — T4 already added the sweep call. This task confirms the end-to-end wiring works against a real `fake_repo` clone.

- [ ] **Step 4: Verify**

```
uv run pytest tests/executor/test_loop_integration.py -v
```

Expected: all loop-integration tests green including the new one.

- [ ] **Step 5: Commit**

```
git add tests/executor/test_loop_integration.py
git commit -m "sweep-trigger: integration test — sweep observes move-out in pending-pr/"
```

---

## Task T7: Documentation — **confidence: 97%**

**Files:** `README.md`, `docs/runbooks/ralph-setup.md` (modify)

- [ ] **Step 1: README**

In the "Running ralph" section (under the iteration_sleep_seconds reference), add:

```markdown
- `iteration_idle_sleep_seconds` — duration in seconds the loop sleeps after an `idle` iteration. Default 300 (5 min). The first non-idle outcome implicitly resets cadence to `iteration_sleep_seconds`. Set in `<repo>/.ralph/config.toml` or via `RALPH_ITERATION_IDLE_SLEEP_SECONDS`.
```

Also add a brief note in the same section:

> Ralph runs the pending-pr sweep on three triggers: process startup (catch-up), after a PBI moves out of `current/` (`pr_created` or `stuck`), and before `pick_next` when `current/` is empty at iteration entry.

- [ ] **Step 2: Runbook**

In `docs/runbooks/ralph-setup.md` config knob table, add the new row alongside `iteration_sleep_seconds`:

| Key | Default | Description |
|---|---|---|
| `iteration_idle_sleep_seconds` | 300 | Sleep after idle iterations. Resets implicitly to `iteration_sleep_seconds` on first non-idle outcome. |

- [ ] **Step 3: Commit**

```
git add README.md docs/runbooks/ralph-setup.md
git commit -m "sweep-trigger: docs — README + runbook for new sweep model"
```

---

## Final sweep

- [ ] `uv run pytest -v` — full suite green.
- [ ] `uv run ruff check . && uv run ruff format --check .` — clean.
- [ ] `uv run mypy ralph_executor scripts skills tests` — clean.
- [ ] `git push origin main` (or via PR per operator preference).

---

## Confidence summary

| Task | % | Key risk | Mitigation baked in |
|---|---|---|---|
| T1 config field | 97 | low | mirror existing `iteration_sleep_seconds` resolution pattern |
| T2 TOML stub | 99 | trivial | direct |
| T3 startup sweep | 94 | clone-not-yet-cloned race at first run | explicit `_pull_queue` before sweep; try/except around the whole startup block; WARNING + proceed on failure |
| T4 post-move-out sweep | 93 | event-log noise affecting cycle detector | insert AFTER `_check_cycle_detector` per spec; sweep failure WARNING + preserve outcome |
| T5 adaptive sleep | 95 | implicit reset works only because branch re-evaluates each iter | explicit unit test asserts idle→claimed→idle sequence sleeps correctly |
| T6 integration | 92 | fixture build | reuse `cfg_for_repo` + `sample_pbi` from conftest; observer pattern in sweep stub |
| T7 docs | 97 | low | direct |

All tasks ≥ 92%. All sub-95% tasks carry baked-in mitigations.
