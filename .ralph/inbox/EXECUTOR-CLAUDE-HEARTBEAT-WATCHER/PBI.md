---
id: EXECUTOR-CLAUDE-HEARTBEAT-WATCHER
type: feature
status: inbox
severity: normal
attempts: 0
created_at: 2026-05-27T23:50:00+00:00
updated_at: 2026-05-27T23:50:00+00:00
depends_on: []
target_repo: https://github.com/emp3thy/ralph
---

# Heartbeat-based hang detection for the spawned Claude subprocess

## Problem

`EXECUTOR-CLAUDE-SESSION-TIMEOUT` (also in inbox) adds a wall-clock deadline on the whole Claude session. That's a useful belt-and-braces guard but it's coarse: an iteration that legitimately takes 25 minutes is indistinguishable from one that wedged at minute 1, until the 30-minute budget burns down.

The finer signal already exists: `claude_spawn.py:500-511` tees `claude --output-format stream-json` stdout/stderr line-by-line. Every Claude tool call emits a JSON envelope on stdout. **If those lines stop arriving, Claude is wedged** (network hang inside a non-bash tool, dead MCP server, stuck OAuth refresh, etc.). CPU is unreliable — Claude can burn CPU on IO waits without making progress — but the tee output is ground truth.

Observed on 2026-05-27: a Claude session for `EXECUTOR-PERMISSION-MODE-SCOPED` went 15 minutes with no `[claude]` tee lines before the operator manually killed it. A heartbeat watcher set to ~5 minutes would have caught it ~10 minutes sooner with no operator intervention.

## What to do

Add a heartbeat watchdog to `claude_spawn.py`:

1. Both `_tee_stream` threads update a shared `last_line_at: float` (monotonic seconds) on every successful `readline()`. Initialise to `time.monotonic()` at spawn so a slow first line doesn't insta-trip.
2. Replace the blocking `proc.wait()` in `spawn_claude_p` with a poll loop:
   - `proc.wait(timeout=poll_interval)` where `poll_interval` is small (5s).
   - If `subprocess.TimeoutExpired`: check `now - last_line_at`. If it exceeds `cfg.claude_heartbeat_seconds`, log a warning, `proc.kill()`, join the tee threads, return a `ClaudeOutcome(kind="error", stderr=f"claude heartbeat exceeded {N}s — likely wedged")`. Otherwise loop.
3. Honour cooperative shutdown: KeyboardInterrupt during the poll loop kills the child cleanly (existing `proc.kill() / join / re-raise` block already does this; preserve the contract).

Default: **`claude_heartbeat_seconds = 300` (5 min)**. Tee lines arrive on every tool call so 5 min of silence is genuinely abnormal for any active session. Operators can raise it via TOML for slow links / batch jobs.

Implementation sketch:

```python
# claude_spawn.py
last_line_at = [time.monotonic()]  # mutable holder shared with tee threads
t_out = threading.Thread(target=_tee_stream, args=(proc.stdout, "[claude]", stdout_buf, stdout_err, last_line_at), daemon=True)
# ... start threads ...
poll = 5.0
heartbeat = cfg.claude_heartbeat_seconds
try:
    while True:
        try:
            returncode = proc.wait(timeout=poll)
            break
        except subprocess.TimeoutExpired:
            idle = time.monotonic() - last_line_at[0]
            if idle > heartbeat:
                log.warning("claude heartbeat exceeded %.0fs for PBI %s -- killing", idle, pbi.id)
                proc.kill()
                t_out.join()
                t_err.join()
                stderr_buf.append(f"\n[ralph] heartbeat exceeded {idle:.0f}s; child killed\n")
                returncode = -1
                break
except BaseException:
    proc.kill()
    t_out.join()
    t_err.join()
    raise
```

`_tee_stream` updates the holder on each line:

```python
def _tee_stream(stream, prefix, buf, err_holder, last_line_at):
    try:
        for line in stream:
            last_line_at[0] = time.monotonic()
            buf.append(line)
            log.info("%s %s", prefix, line.rstrip())
    except BaseException as exc:
        err_holder.append(exc)
```

`ExecutorConfig` adds `claude_heartbeat_seconds: int = 300`, overridable via TOML `[claude] heartbeat_seconds`.

## Acceptance criteria

- `ExecutorConfig` exposes `claude_heartbeat_seconds` with default 300.
- TOML loader honours `[claude] heartbeat_seconds`.
- `_tee_stream` updates a shared monotonic timestamp on every line.
- `spawn_claude_p` polls in a 5s loop and kills the child when `idle > heartbeat`, returning a `ClaudeOutcome(kind="error", …)` with a clear stderr explanation.
- Existing tee semantics preserved: stdout/stderr fully captured up to the kill point; tee-thread exceptions still re-raised.
- KeyboardInterrupt still kills cleanly.
- Unit test: monkeypatch a fake `proc` whose stdout emits one line then blocks; assert kill after `heartbeat_seconds` elapses, assert outcome is the heartbeat error.
- Unit test: monkeypatch a fake `proc` whose stdout emits a line every (`heartbeat_seconds`/2) and exits normally; assert NO kill, outcome is normal exit.
- Integration test: a fake `claude` script that prints one line then sleeps; cfg sets `claude_heartbeat_seconds=2`; assert the spawn returns within ~3-7 seconds with the heartbeat outcome.
- Plays well with `EXECUTOR-CLAUDE-SESSION-TIMEOUT` if/when both land — heartbeat fires first on quiet hangs, wall-clock fires on chatty-but-runaway hangs. Whichever trips first wins; both produce `ClaudeOutcome(kind="error")`.
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/EXECUTOR-CLAUDE-HEARTBEAT-WATCHER`

## Notes

- Out of scope: distinguishing "Claude thinking deeply between tools" from "Claude wedged". 5 min default tolerates normal thinking; raise the TOML knob if it false-trips.
- Out of scope: smarter signals (per-tool-type timeouts, MCP-aware probing). Tee-line silence is the strongest universally-available signal.
- Pair with `EXECUTOR-CLAUDE-SESSION-TIMEOUT` for layered protection. Either rule killing the child should leave the PBI in `current/` with the attempts counter incremented, matching today's error-iteration path.
