---
id: EXECUTOR-CLAUDE-SESSION-TIMEOUT
type: feature
status: current
severity: normal
attempts: 0
created_at: 2026-05-27T23:35:00+00:00
updated_at: 2026-05-28T01:23:51+00:00
depends_on: []
target_repo: https://github.com/emp3thy/ralph
---

# Per-iteration deadline on the spawned Claude subprocess

## Problem

`ralph_executor/claude_spawn.py:513` calls `proc.wait()` with no timeout. If the spawned Claude subprocess wedges — network hang outside a bash call, infinite tool loop, dead OAuth refresh, classifier deadlock — the executor blocks forever. Only escape today is operator `Ctrl-C`.

`BASH_MAX_TIMEOUT_MS` caps individual Bash tool calls inside Claude (15 min default), but a Claude session can chain dozens of tool calls and stay alive much longer than any sensible per-iteration budget. There is no overall session deadline.

Observed on 2026-05-27: a Claude iteration for EXECUTOR-PERMISSION-MODE-SCOPED ran 9+ minutes with no visible commit activity. It turned out to be normal first-iteration work, but the operator had no way to distinguish "slow" from "hung" without inspecting child processes manually.

## What to do

Add a per-iteration deadline (call it `claude_session_timeout_seconds`) that bounds the lifetime of a single Claude spawn. On timeout:

1. `proc.kill()` the Claude subprocess.
2. Join the tee threads so output captured so far is preserved.
3. Surface a synthetic `ClaudeOutcome(kind="error", stderr="claude session exceeded N seconds and was killed", exit_code=-1, …)` to the loop.
4. The loop then treats this like any other error iteration: attempts counter increments, PBI stays in `current/` for the next try (or moves to `blocked/` when attempts hits the cap, same as today's max-attempts path).

Suggested default: **1200 seconds (20 min)**. Operator policy is to restart Claude well before 20 min, so anything that runs past the budget is genuinely wedged. Tight enough to catch hangs within one short coffee break.

Implementation sketch:

```python
# claude_spawn.py
try:
    returncode = proc.wait(timeout=cfg.claude_session_timeout_seconds)
except subprocess.TimeoutExpired:
    log.warning(
        "claude session for PBI %s exceeded %ss -- killing",
        pbi.id,
        cfg.claude_session_timeout_seconds,
    )
    proc.kill()
    t_out.join()
    t_err.join()
    stderr_buf.append(
        f"\n[ralph] session killed after {cfg.claude_session_timeout_seconds}s\n"
    )
    returncode = -1
```

`ExecutorConfig` exposes `claude_session_timeout_seconds: int = 1200` (override via TOML `[claude] session_timeout_seconds`).

## Acceptance criteria

- `ExecutorConfig` exposes `claude_session_timeout_seconds` with default 1200.
- TOML loader honours `[claude] session_timeout_seconds`.
- `spawn_claude` (or whatever the public function is called) kills the child when `proc.wait()` exceeds the budget, joins the tee threads cleanly, and returns a `ClaudeOutcome(kind="error", ...)` with a clear stderr explanation.
- Tee threads see EOF and exit (no zombie threads).
- Unit test: monkeypatch a `proc` that sleeps past the timeout; assert `proc.kill` was called and the returned outcome is the timeout error.
- Integration test: a fake `claude` script that `time.sleep(60)` while the config sets `claude_session_timeout_seconds=2`; assert the spawn returns within ~3 seconds with the timeout outcome.
- Existing claude-spawn tests still pass (they use short-lived fake binaries that finish well under the default).
- Loop treats the timeout outcome the same as any other error iteration: PBI stays in `current/`, attempts increments, max-attempts → blocked.
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/EXECUTOR-CLAUDE-SESSION-TIMEOUT`

## Notes

- Out of scope: distinguishing "thinking slowly" from "hung" intelligently. A hard wall-clock budget is enough — the existing attempts / max-attempts machinery handles the retry / give-up policy.
- Out of scope: streaming heartbeat detection (e.g. kill if no stdout for N minutes). Could be a follow-up; start with the simple deadline.
- Companion PBI `CONFIG-PROMOTE-CYCLE-DETECTOR-THRESHOLDS` (in inbox) is the same shape (one TOML knob, one default, one new test). Use the existing `BASH_MAX_TIMEOUT_MS` plumbing in `claude_spawn.py:482` and `ExecutorConfig.bash_max_timeout_ms` as the template.
