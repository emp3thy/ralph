# Sweep trigger model + idle backoff — design

**Date:** 2026-05-31
**Status:** draft, awaiting user review
**Author:** Claude / gethin (brainstorm)

## Summary

Replace the implicit "sweep runs only when `current/` is empty at iteration entry" trigger with an explicit event-driven trigger model, and add adaptive idle sleep so a quiet ralph stops burning cycles polling its inbox.

Sweep fires on three discrete events:

1. **Process startup** — one-shot catch-up sweep before the iteration loop begins.
2. **After a PBI leaves `current/`** (`ran_pr_created` or `ran_stuck` outcome) — fires AFTER persist-iteration-writes and AFTER the cycle detector check, so the sweep sees the fully consistent post-iteration state.
3. **Before `pick_next`** (current was empty at iteration entry) — the existing trigger, preserved so an idle ralph in watch_mode still reconciles PRs going CLEAN.

Idle backoff: whenever an iteration outcome is `idle`, sleep `iteration_idle_sleep_seconds` (default 300 = 5 minutes) instead of the existing 30-second baseline. The first non-idle outcome resets back to the 30-second baseline. The backoff lives in `run_loop` and is orthogonal to the sweep trigger model.

## Goals

- Stop the "sweep fires every 30 s during idle" timer-shaped behaviour while still reconciling PRs going CLEAN during quiet periods
- React quickly to a PBI moving out of `current/` — particularly the just-opened PR, which may auto-merge under `auto_merge_clean_prs = true`
- Catch up on PR state immediately when ralph restarts after being offline
- No new external dependencies; no new service; no new background thread

## Non-goals

- Fleet-wide sweep coordination across multiple ralphs (deferred — see [multi-ralph spec](2026-05-31-multi-ralph-design.md))
- Per-PR cooldown / TTL caching on `gh pr view` results
- Webhook-driven sweep
- Exponential backoff beyond a single 30 s → 5 min step
- Per-PR rate limiting

## Architecture

### Trigger 1 — startup catch-up sweep

`run_loop` calls `_run_sweep` once before entering the iteration loop. The sweep operates against the queue clone the same way every other sweep call does. If startup sweep raises any handled exception, log a WARNING and proceed to the loop — startup-sweep failure must not block ralph from running.

If `cfg.watch_mode` is False AND the queue clone does not yet exist (first run), `ensure_queue_clone` creates it via the existing `_pull_queue` call inside `iterate_once`. To avoid clone-before-sweep ordering bugs, the startup sweep runs AFTER an explicit `_pull_queue(cfg)` call in `run_loop`. This is the only new I/O in `run_loop` beyond the existing iteration.

### Trigger 2 — after move-out, after persist + cycle

Inside `iterate_once`, when `current_pbi()` is populated, the existing call chain runs:

1. `_run_ralph(cfg, current)` returns `(_outcome, result)`
2. `_persist_iteration_writes(cfg, current.id, ...)` commits any HISTORY/STUCK/PLAN edits
3. `_check_cycle_detector(cfg, source)` evaluates and may raise `HaltedError`

Insert a new step 4 between cycle-detector check and the existing `return result`:

```python
if result.outcome in ("ran_pr_created", "ran_stuck"):
    _run_sweep(cfg, source)
```

`ran_partial` and `ran_error` outcomes keep the PBI in `current/`, so no move-out → no sweep on those. `push_conflict` and `uncommitted_source` outcomes return early before the sweep call — they represent transient git races and the next iteration retries.

If the new sweep call raises, the iteration still returns its existing `result` so the loop continues — sweep failure on a successful move-out is logged at WARNING but does not poison the outcome the caller sees.

### Trigger 3 — before pick_next (existing, retained)

When `iterate_once` enters with `current_pbi()` returning None, the existing flow runs:

```python
_run_sweep(cfg, source)
picked = source.pick_next()
```

No change. Retained so an idle watch_mode ralph still reconciles PRs.

### Idle backoff

Today's `run_loop` has:

```python
if result.outcome == "idle":
    time.sleep(cfg.iteration_sleep_seconds)
```

Replace with:

```python
sleep_seconds = (
    cfg.iteration_idle_sleep_seconds
    if result.outcome == "idle"
    else cfg.iteration_sleep_seconds
)
if result.outcome == "idle":
    time.sleep(sleep_seconds)
```

The non-idle branch already doesn't sleep, so the conditional is purely for clarity — the actual behaviour change is the longer duration on idle outcomes.

`iteration_sleep_seconds` (existing, default 30) keeps its semantics as the busy-period baseline. `iteration_idle_sleep_seconds` (new, default 300) governs idle sleep. Both are TOML / env / non-CLI knobs.

The first non-idle outcome after a string of idle iterations does not need explicit reset logic because `run_loop` re-evaluates the sleep duration each iteration. The duration adapts naturally.

### Why sweep runs AFTER persist + cycle

The cycle detector consumes the event log to decide whether to halt. Events the iteration produced (PBI_OPENED, PR_CREATED, ATTEMPT_INCREMENTED, PBI_BLOCKED, FILE_TOUCHED) are written by `_run_ralph` and `_persist_iteration_writes`. Sweep also writes events (`PR_MERGED`, `PBI_CLOSED`, `PR_GREEN_THEN_RED`). If sweep ran BEFORE cycle-detector check, this iteration's cycle check would observe sweep-emitted events from a sweep tied to a different PBI's lifecycle — semantic noise.

Running sweep AFTER cycle detector means:
- Cycle detector's evaluation window is "events from this PBI's iteration only"
- Sweep's emissions land in the event log for the NEXT iteration's cycle check
- The persist commit has already settled `current/` → `pending-pr/` so sweep sees the fully consistent tree

If sweep itself produces events that would trip a cycle rule (e.g. `PR_GREEN_THEN_RED` across many PBIs), the next iteration's cycle check catches it. One-iteration lag is acceptable for a slow-moving rule.

## Configuration

Two knobs, both TOML / env, no CLI flags:

| Key | Default | Env | Description |
|---|---|---|---|
| `iteration_sleep_seconds` | 30 | `RALPH_ITERATION_SLEEP_SECONDS` | Existing. Used during non-idle outcomes (currently unused since non-idle skips sleep; reserved as the "busy" baseline). |
| `iteration_idle_sleep_seconds` | 300 | `RALPH_ITERATION_IDLE_SLEEP_SECONDS` | New. Sleep duration applied when the iteration outcome was `idle`. Must be > `iteration_sleep_seconds`. |

Validation: `iteration_idle_sleep_seconds > 0`, and a WARNING (not error) if `< iteration_sleep_seconds` — operator may have a tuning case where they want faster idle re-poll than busy baseline; honour it.

## Component map

| File | Change |
|---|---|
| `ralph_executor/loop.py::run_loop` | Add startup catch-up: one `_pull_queue(cfg)` + `_run_sweep(cfg, source)` before the iteration `while` loop. Compute sleep duration from `cfg.iteration_idle_sleep_seconds` on idle outcomes. |
| `ralph_executor/loop.py::iterate_once` | After cycle detector check + before existing return, conditionally call `_run_sweep(cfg, source)` when `result.outcome in {"ran_pr_created", "ran_stuck"}`. Existing `_run_sweep` in the current-empty branch is unchanged. |
| `ralph_executor/config.py` | New `iteration_idle_sleep_seconds: float` field on `ExecutorConfig` (default 300.0). Add to `_TOML_KNOWN_KEYS`. Add `_resolve_float` resolution in `load_config`. |
| `ralph_executor/config.py` constants | New `DEFAULT_ITERATION_IDLE_SLEEP_SECONDS = 300.0`. |
| `ralph_executor/setup_cmds.py::CONFIG_TOML_STUB` | Add commented `iteration_idle_sleep_seconds = 300` line with explanation. |
| `tests/executor/test_loop.py` | Tests for the new trigger paths (see Testing). |
| `tests/executor/test_loop_integration.py` | One end-to-end sleep-duration assertion. |
| `tests/executor/test_config.py` | Resolution + validation tests for the new knob. |
| `README.md` | Update "Running ralph" section if it documents `iteration_sleep_seconds`; add the idle knob alongside. |
| `docs/runbooks/ralph-setup.md` | Add the new knob to the config table. |

No changes to `sweep/runner.py`, `sweep/types.py`, the PR-skill, or any host-specific code. The sweep itself is unchanged — only its trigger sites change.

## Testing

### Unit

- `test_run_loop_startup_sweep_fires` — monkeypatch `_run_sweep` to count calls; assert one call before the iteration loop begins.
- `test_run_loop_startup_sweep_failure_is_warned_not_fatal` — make startup `_run_sweep` raise; assert loop still runs and warning is logged.
- `test_iterate_once_sweeps_after_pr_created` — fixture: PBI in `current/`, claude outcome stubbed to `pr_created`. Assert `_run_sweep` called once after `_check_cycle_detector` and before return.
- `test_iterate_once_sweeps_after_stuck` — same shape with stuck outcome.
- `test_iterate_once_no_sweep_after_partial` — `ran_partial` outcome leaves PBI in current/; assert sweep NOT called in the post-cycle slot.
- `test_iterate_once_no_sweep_after_error` — same shape with `error` outcome.
- `test_iterate_once_sweeps_before_pick_when_current_empty` — existing trigger still works.
- `test_run_loop_idle_uses_idle_sleep_seconds` — monkeypatch `time.sleep` to a recorder; iteration returns idle; assert recorded duration equals `cfg.iteration_idle_sleep_seconds` (300 by default).
- `test_run_loop_non_idle_does_not_sleep` — claimed outcome; recorder confirms no sleep call.
- `test_run_loop_idle_then_claimed_resets_cadence` — first iter idle (5min recorded), second iter claimed (no sleep), third iter idle again (5min recorded). Confirms no "stickiness".
- `test_load_config_idle_sleep_default` — no env / TOML overrides → `cfg.iteration_idle_sleep_seconds == 300.0`.
- `test_load_config_idle_sleep_from_env` — `RALPH_ITERATION_IDLE_SLEEP_SECONDS=120` → resolved to 120.0.
- `test_load_config_idle_sleep_from_toml` — TOML key honoured.
- `test_load_config_idle_sleep_validates_positive` — `-1` or `0` raises `ConfigError`.

### Integration

- `test_loop_integration_sweep_fires_after_pr_created_then_loops` — drive `iterate_once` against a fake_repo seeded with one inbox PBI; stub claude to `pr_created`; assert sweep call lands at the expected hook point AND the loop's next iteration sees the updated `pending-pr/` count.

No new end-to-end / smoke test — the sweep itself is exhaustively tested in `tests/executor/test_sweep_runner.py`; this change is purely about trigger sites.

## Rollout

1. Land the executor changes behind no feature flag — both triggers fire from day one.
2. Update `ralph-executor init`'s `CONFIG_TOML_STUB` so fresh `init` runs land the new knob commented in the project TOML.
3. Documentation: README + runbook + brief release note ("ralph now sweeps after PBI move-out and at startup; idle daemons back off to 5 min between polls").
4. No upgrade migration. Existing operators get the new behaviour on next executor restart. The new knob is optional with a sane default.

## Assumptions surfaced (3-bucket)

### Real concerns (with mitigations baked in)

- **Concern**: Startup catch-up sweep on a stale clone could fire `gh` calls against PRs that were already reconciled by a sibling ralph. Wasteful but not incorrect (sweep is idempotent — it re-evaluates state per `gh pr view` and decides no-op if nothing changed).
  - **Mitigation**: accept the redundant API spend. Each ralph already pulls the queue at startup; the extra cost is the cost of N PRs × ~3 calls — small.
- **Concern**: Sweep emits events (`PR_MERGED` etc) that the next iteration's cycle detector sees. Could a startup sweep emit events that trip a cycle on the FIRST iteration?
  - **Mitigation**: cycle detector windows on a rolling 72-hour event log; one extra batch of sweep events at startup falls within normal rolling-window noise. No special case needed.

### Verified-safe assumptions (read existing source at spec-write time)

- `_run_sweep(cfg, source)` is idempotent and self-contained — verified by reading `ralph_executor/loop.py::_run_sweep` (lines 121-199) and `ralph_executor/sweep/runner.py::run` (entry point for sweep execution).
- `iterate_once` returns the `IterationResult` from `_run_ralph` unchanged on `ran_pr_created` / `ran_stuck` — verified by reading `ralph_executor/loop.py::iterate_once` (lines 825-870).
- `run_loop`'s sleep call only fires on idle outcomes — verified by reading `ralph_executor/loop.py::run_loop` (lines 946-1003); the conditional `if result.outcome == "idle": time.sleep(...)` is the only sleep site.
- `cfg.iteration_sleep_seconds` is a float with env override `RALPH_ITERATION_SLEEP_SECONDS` and TOML key `iteration_sleep_seconds` — verified at `ralph_executor/config.py:49` (constant) + the `_resolve_float` call in `load_config`.
- `_TOML_KNOWN_KEYS` extension is the right insertion mechanism for new TOML keys — same pattern used in the multi-ralph spec's T3 task.

### Minor / accepted

- Startup sweep runs even when current/ is populated (resume case). One sweep per process boot; cheap.
- The "first non-idle outcome resets cadence" reset logic is implicit (each iteration re-evaluates the sleep duration); no explicit reset state needed.
- `iteration_sleep_seconds` is currently dead code at the busy-baseline call site (non-idle iterations skip sleep entirely). Kept for backward compatibility and as a reserved knob for any future "throttle busy iterations" need.

## Standards consulted

- `standards/ralph-runtime.md` (better-memory knowledge) — per-task confidence scoring in the plan, render in visualiser at every doc handoff, 3-bucket assumption surface in this spec (above).

## Out of scope

- Per-PR cooldown / TTL on `gh pr view`
- Webhook-triggered sweep
- Fleet-wide sweep coordination (see multi-ralph Scope 2)
- Adaptive sweep depth (e.g. "sweep up to N PBIs per call")
- Exponential backoff beyond a single 30 s → 5 min step
