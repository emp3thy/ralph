# Reproduce: Claude stdout doesn't stream on Windows

## Environment

- Windows 10/11 with PowerShell 5.1 or 7
- Python 3.12 + uv
- `claude` CLI on PATH (any recent version)
- A ralph-queue checkout with an inbox PBI ready to claim

## Steps

1. From a Windows shell, claim an inbox PBI by starting the executor:

   ```powershell
   cd C:\path\to\ralph-checkout
   git checkout ralph-queue
   $env:GH_TOKEN = (gh auth token)
   uv run ralph-executor
   ```

2. Observe the executor logs up to:

   ```
   INFO ralph_executor.claude_spawn: spawning claude for PBI <id>
   ```

3. Wait 2–10 minutes. Do not Ctrl-C.

4. In a second Windows shell, run:

   ```powershell
   cd C:\path\to\ralph-checkout
   git log --oneline ralph/<id>
   ```

   Observe that Claude has been committing work the whole time — typically one commit per Task in the PBI's PLAN.md.

## Expected

The executor's first shell should print `[claude] <line>` entries as Claude writes them — Tool calls, reasoning, status updates, etc. — visible in real time.

## Actual

The first shell is **completely silent** from the moment Claude spawns until the iteration completes. The operator has no way to tell that Claude is making progress without checking the feature branch from another shell.

## Counter-check on POSIX (sanity)

The same setup on Linux / WSL2 / macOS streams correctly — `[claude]` lines appear within seconds of spawn. The bug is Windows-specific.

## Quick diagnostic to confirm root cause

```python
# In a Windows Python shell, NOT inside ralph:
import subprocess
proc = subprocess.Popen(
    ["node", "-e", "let i=0; setInterval(()=>{console.log('tick',i++)}, 200)"],
    stdout=subprocess.PIPE,
    text=True,
    bufsize=1,
)
import threading, time
def tee():
    for line in proc.stdout:
        print(f"PARENT: {line!r}", flush=True)
threading.Thread(target=tee, daemon=True).start()
time.sleep(3)
proc.kill()
```

Expected: parent prints `PARENT: 'tick 0\n'`, `PARENT: 'tick 1\n'`, ... in real time.
Actual on Windows: parent prints **nothing** until the child is killed and Node flushes on exit.

This confirms the bug is in the Python ↔ Node ↔ Windows-pipe interaction, not in Claude specifically.
