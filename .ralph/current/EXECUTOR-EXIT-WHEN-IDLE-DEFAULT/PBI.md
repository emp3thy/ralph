---
id: EXECUTOR-EXIT-WHEN-IDLE-DEFAULT
type: feature
status: current
severity: high
attempts: 2
created_at: 2026-05-28T00:30:00+00:00
updated_at: 2026-05-28T08:34:53+00:00
depends_on: []
target_repo: https://github.com/emp3thy/ralph
---

# Exit when idle by default; opt into daemon mode with `--watch`

## Problem

Today's `run_loop` (`ralph_executor/loop.py:755`) sleeps `cfg.iteration_sleep_seconds` after every idle iteration and polls forever. The only exit paths are `KeyboardInterrupt` or `HaltedError`. This is correct for a workstation operator who keeps pushing PBIs all day, but wrong for the primary deployment target: **unattended runs inside a pod / container** where:

- Idle iterations burn compute (Anthropic API tokens, pod CPU minutes, persistent disk for the worktree).
- There is no operator to push new PBIs into the queue mid-run — the queue contents are baked into the pod's environment at launch.
- A pod that doesn't terminate when its job is done is just paying rent.

The current default (daemon) inverts the cost calculus for the common case. Operators are stuck either babysitting a `Ctrl-C` or wiring an external supervisor (k8s `activeDeadlineSeconds`, systemd timer, GitHub Action job timeout) just to make ralph stop after it finishes its work.

## What to do

**Flip the default.** Exit-when-idle becomes the standard behavior; daemon mode becomes the opt-in for the workstation case.

1. Add `--watch` (or `--daemon` — pick one and be consistent across CLI + TOML + env var) flag to `cli.py`. When passed, `run_loop` retains today's "sleep on idle, never exit" behavior.
2. Default behavior (no `--watch`): exit cleanly after **N consecutive idle iterations** (default `idle_exit_threshold = 2`, configurable via TOML). The small threshold tolerates a single false-idle caused by a sweep tick that's mid-flight when `pick_next` runs — but doesn't burn more than one extra sleep cycle.
3. Exit code: 0 (success) for "drained cleanly to idle". The existing exit-on-HaltedError path keeps its non-zero code unchanged.
4. Last log line on idle-exit: `INFO ralph_executor.cli: queue drained -- exiting after N consecutive idle iterations`. The supervisor running the pod can grep for that string to confirm orderly shutdown.

Suggested config keys (via TOML `[loop]`):
- `idle_exit_threshold = 2` (int, default 2; set to a very high number to effectively disable, or use `--watch` for clarity)
- `iteration_sleep_seconds` is unchanged (still controls sleep BETWEEN idle iterations within the threshold window).

CLI surface:
- `ralph-executor` — drain queue, exit after 2 consecutive idles (NEW default).
- `ralph-executor --watch` — old behavior; run forever, sleep on idle.
- `ralph-executor --once` — unchanged; run one iteration and exit regardless of outcome.

## Acceptance criteria

- `ExecutorConfig` exposes `idle_exit_threshold: int = 2` and `watch_mode: bool = False`.
- TOML loader honours `[loop] idle_exit_threshold` and a top-level `watch_mode` (or `[loop] watch = true`).
- CLI adds `--watch` (mutually exclusive with `--once` — pick one or specify neither for default drain mode). `--watch` overrides `watch_mode` from TOML.
- `run_loop` exits cleanly (no exception) once `consecutive_idle_count >= idle_exit_threshold` and `watch_mode is False` and `--once` is False. Final log line names the threshold and idle count.
- Exit code: 0 for idle-exit (matches today's `--once` clean exit).
- Existing `--once` continues to exit after exactly one iteration regardless of outcome.
- Existing `--watch` (or whatever name) preserves today's daemon behavior.
- Unit test: monkeypatch `iterate_once` to return `IterationResult("idle")` twice in a row; assert `run_loop` exits after the 2nd idle and yields exactly 2 results.
- Unit test: same but `watch_mode=True`; assert `run_loop` does NOT exit (use `max_iterations` to bound the test to 3 idles, assert 3 results yielded).
- Unit test: monkeypatch `iterate_once` to return `idle` once then `claimed` once then `idle` twice; assert exit after the second consecutive idle. (Verifies that a non-idle outcome resets the consecutive-idle counter.)
- Integration test: real `iterate_once` against an empty `.ralph/` fixture; assert `run_loop` returns after 2 yields, both `outcome="idle"`.
- README + ops docs: document the flipped default and the `--watch` opt-in for daemon usage. Specifically call out the pod / container deployment as the intended default.
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/EXECUTOR-EXIT-WHEN-IDLE-DEFAULT`

## Why severity=high

This changes user-visible default behavior. Operators currently running ralph on a workstation in daemon mode will be surprised by their next invocation if they don't update their command. Land it cleanly with the `--watch` opt-in documented, OR ship a one-release deprecation cycle (default unchanged, but a deprecation warning + `--watch` flag introduced now, default flipped next release). Pick one — but the underlying motivation (don't burn pod minutes on idle) makes the eventual default-flip the right end state.

## Notes

- Out of scope: distinguishing "queue truly empty" from "queue has eligible-but-dependency-blocked PBIs". The `IterationResult("idle")` outcome already covers both; if downstream wants to differentiate, that's a follow-up signal on `IterationResult`.
- Out of scope: graceful in-flight handling. Idle means no Claude was spawned this tick — there's nothing in flight to drain. KeyboardInterrupt during a running iteration remains the existing path for that case.
- Pair with EXECUTOR-CLAUDE-SESSION-TIMEOUT (just landed) for layered cost containment: per-session wall-clock + drain-on-idle = no path for a pod to overrun its budget.
