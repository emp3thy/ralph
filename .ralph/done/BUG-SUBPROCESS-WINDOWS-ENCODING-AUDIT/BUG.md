---
id: BUG-SUBPROCESS-WINDOWS-ENCODING-AUDIT
type: bug
status: pending-pr
severity: high
attempts: 1
created_at: 2026-05-28T10:30:00+00:00
updated_at: 2026-05-28T12:18:44+00:00
depends_on: []
target_repo: https://github.com/emp3thy/ralph
---

# subprocess.Popen/run uses Windows locale encoding (cp1252), crashes on non-cp1252 bytes

Every `subprocess.Popen` / `subprocess.run` call in `ralph_executor/` that passes `text=True` without an explicit `encoding=` falls back to `locale.getpreferredencoding()` — which is **cp1252** on Windows. Any child process that emits UTF-8 (Claude's stream-json, git output with non-ASCII file names / branch names / commit subjects, gh JSON, skill JSON) can produce a byte that is invalid cp1252 → `UnicodeDecodeError` → unhandled exception → ralph loop crash.

A 2-line hot-fix landed on main as commit `a7d2c89` (`fix(claude_spawn): force utf-8 decoding of claude subprocess streams`) covering ONLY the `spawn_claude_p` Popen at `claude_spawn.py:569`. The other 13 call sites are still vulnerable.

Observed crash 2026-05-28 10:02:26 in `_tee_stream` after a session-timeout kill closed the stdout pipe mid-UTF-8-sequence:
```
File "...\cp1252.py", line 23, in decode
    return codecs.charmap_decode(input,self.errors,decoding_table)[0]
UnicodeDecodeError: 'charmap' codec can't decode byte 0x90 in position 639: character maps to <undefined>
```

## Fix

1. Add an explicit `encoding="utf-8"` and `errors="replace"` to every `subprocess.Popen` / `subprocess.run` call that uses `text=True` (or `universal_newlines=True`). `errors="replace"` is the right choice for streams we log and feed to a JSON parser — a substitution character is recoverable; a crash is not.
2. Centralise the decoding policy. Add a `ralph_executor/subprocess_utils.py` exporting `run_text(...)` and `popen_text(...)` thin wrappers that set `text=True, encoding="utf-8", errors="replace"` by default. Migrate call sites to use them so future additions cannot regress.
3. Add a lint rule (ruff custom or grep-based CI check) that flags `subprocess.Popen(... text=True ...)` and `subprocess.run(... text=True ...)` without `encoding=`. Fast-fail in CI.

## Call sites to migrate

(`text=True` with no explicit `encoding=` as of HEAD; the `claude_spawn.py:569` hot-fix on main is already covered)

| File | Line | Process | Encoding risk |
|---|---|---|---|
| `ralph_executor/cli.py` | 354 | `ralph-doctor` | low (own output, mostly ASCII) |
| `ralph_executor/claude_spawn.py` | 131 | `gh pr list` | medium (PR titles can have unicode) |
| `ralph_executor/claude_spawn.py` | 218 | `gh pr checks` | medium |
| `ralph_executor/git_ops.py` | 35 | `_run_git` (every git op) | **high** (commit subjects, file paths, branch names) |
| `ralph_executor/git_ops.py` | 72 | `gh` runner | medium |
| `ralph_executor/setup_cmds.py` | 181 | `gh auth status` | low |
| `ralph_executor/setup_cmds.py` | 216 | git wrapper | high |
| `ralph_executor/setup_cmds.py` | 231 | `git rev-parse` | low |
| `ralph_executor/setup_cmds.py` | 335 | `git diff --cached --quiet` | low (no output) |
| `ralph_executor/setup_cmds.py` | 348 | `git commit` | high |
| `ralph_executor/sweep/reconcile.py` | 84 | skill subprocess (JSON out) | high (JSON spec is UTF-8) |
| `ralph_executor/sweep/runner.py` | 694 | sweep helper | high (JSON out) |
| `ralph_executor/sweep/pr_state.py` | 111 | PR state skill (JSON out) | high (JSON out) |

## Reproduction

On Windows with `PYTHONIOENCODING` unset:

```python
import subprocess, sys
p = subprocess.Popen(
    [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\x90\\n')"],
    stdout=subprocess.PIPE, text=True, bufsize=1,
)
for line in p.stdout:
    print(repr(line))
# → UnicodeDecodeError: 'charmap' codec can't decode byte 0x90 ...
```

After fix (`encoding="utf-8", errors="replace"`): prints `'�\n'` and exits cleanly.

## Acceptance criteria

- `ralph_executor/subprocess_utils.py` exists with `run_text` / `popen_text` wrappers (utf-8, replace by default)
- All 13 call sites in the table migrated to the wrapper OR pass `encoding="utf-8", errors="replace"` inline if the wrapper is impractical for that call (e.g. `_kill_process_tree`'s taskkill, which discards output)
- A CI grep / ruff check flags `subprocess.(Popen|run)(... text=True ...)` without `encoding=` and fails the build (or at least pre-commit)
- Unit test: a fixture child that writes a 0x90 byte does not crash the tee in `spawn_claude_p` (regression test for the 2026-05-28 crash) — assert the line lands in `outcome.stdout` with U+FFFD substitution
- Unit test: `git_ops._run_git` survives a commit subject containing emoji / non-ASCII (parametrise with a fixture repo)
- `uv run pytest` passes
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/BUG-SUBPROCESS-WINDOWS-ENCODING-AUDIT`

## Out of scope

- POSIX behaviour: `locale.getpreferredencoding()` is usually utf-8 on Linux/macOS, so the fix is a no-op there. We pin utf-8 anyway for determinism (a misconfigured container with `LC_ALL=C` would otherwise hit the same trap).
- The `claude_spawn.py:569` hot-fix already on main — leave it as-is or refactor to use the new wrapper; reviewer's call.
- Output direction (stdin writes): no current call writes non-ASCII to a child's stdin; if added later, the same policy applies.

## Related

- The hot-fix commit: `a7d2c89 fix(claude_spawn): force utf-8 decoding of claude subprocess streams`
- Original crash: PBI `EXECUTOR-EXIT-WHEN-IDLE-DEFAULT` iteration on 2026-05-28 10:02:26
