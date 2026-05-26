---
id: BUG-claude-stdout-streaming-windows
type: bug
status: current
severity: high
attempts: 0
created_at: 2026-05-26T22:15:00+00:00
updated_at: 2026-05-26T22:44:32+00:00
depends_on: []
---

# Claude stdout never streams on Windows — operator sees no output until iteration ends

## Symptom

Running `uv run ralph-executor` on Windows, the executor logs:

```
INFO ralph_executor.claude_spawn: spawning claude for PBI STAGE-B-PLAN-18-verifier
```

…and then **goes silent for the entire iteration** (minutes). No `[claude] ...` prefixed lines appear in the terminal until Claude exits. The operator cannot tell whether Claude is working, stuck, hanging on an interactive prompt, or has crashed.

In reality Claude IS working — confirmed by inspecting `git log ralph/<PBI-ID>` in a second terminal and seeing per-Task commits land. The bug is purely in the streaming path; Claude executes correctly.

## Root cause (suspected)

`ralph_executor/claude_spawn.py::spawn_claude_p` uses:

```python
proc = subprocess.Popen(
    argv,
    cwd=str(cfg.repo_path),
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,            # POSIX: line-buffered. Windows: NOT honored.
)
```

`bufsize=1` requests line buffering. On POSIX with `text=True` this works — Python wraps the pipe in `io.TextIOWrapper(line_buffering=True)`. On Windows the underlying native pipe doesn't propagate the line-buffering hint to the child process's CRT, so the **child's** stdout stays block-buffered (default ~4 KB). The tee threads receive nothing until Claude flushes a block or exits.

Python docs (subprocess section "Replacing Older Functions"):
> Note: The bufsize argument has the same meaning as for the built-in open() function. […] On Windows, this is currently ignored.

Plus: `claude` is a Node binary, not Python — `PYTHONUNBUFFERED=1` doesn't help. Node has its own stdout buffering behaviour that differs depending on whether stdout is a TTY (line-buffered) or a pipe (block-buffered). When we route through a pipe, Node block-buffers.

## Impact

- Operator visibility into long-running iterations is zero on Windows
- Indistinguishable from "Claude is stuck" — operator may Ctrl-C a working iteration
- Defeats the entire point of PR #12's streaming fix on the platform Ralph is currently developed on
- Will recur in any future Windows-host deployment (less critical on Linux pods)

Severity high because it directly degrades the operator's ability to babysit the loop, which is one of the v1 use cases.

## Acceptance criteria

- Streaming works on Windows: each line Claude writes to stdout appears in the executor's logs **as Claude writes it**, not when Claude exits
- Stderr streaming works the same way (already mostly does, but verify on Windows too)
- POSIX behaviour unchanged (existing tests still pass)
- New regression test: spawn a child that prints "tick-N" with 200ms gaps and sleeps; assert at least one tick line is captured before the child completes
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/BUG-claude-stdout-streaming-windows`

## Possible fixes (implementer to evaluate)

1. **`claude -p --output-format stream-json`** — if Claude Code supports a streaming structured output flag, use it. Output is line-delimited JSON, actively flushed. Cleanest fix if available.
2. **Pseudo-TTY via `pywinpty`** — wrap claude in a Windows ConPTY/pty so Node sees a TTY and line-buffers stdout. Adds a Windows-only dependency.
3. **Periodic small reads on raw bytes** — drop `text=True bufsize=1`; use `subprocess.Popen(stdout=PIPE)` and read N bytes at a time with a short timeout in the tee thread. Decode + emit per newline found. More code, no dependency.
4. **`winpty` shim** (separate executable) — wrap the claude command in a `winpty` invocation. External-dep flavour of option 2.

Option 1 is preferred if it exists. Spike that before implementing the others.
