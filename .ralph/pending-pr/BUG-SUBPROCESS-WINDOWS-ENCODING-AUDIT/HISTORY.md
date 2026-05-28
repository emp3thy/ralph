<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-28T12:30:00+00:00

- Step: Audited every `subprocess.Popen`/`subprocess.run` call in `ralph_executor/` (and `scripts/queue_writer.py`) that uses `text=True` without an explicit `encoding=`. Added `ralph_executor/subprocess_utils.py` exporting `run_text` / `popen_text` thin wrappers that default to `text=True, encoding="utf-8", errors="replace"`. Migrated all 13 call sites listed in BUG.md plus the `claude_spawn.spawn_claude_p` hot-fix to the wrapper. Added a repo-wide guard test (`tests/executor/test_subprocess_encoding_audit.py`) that fails the build if a new `text=True` call site forgets `encoding=`. Backfilled `encoding="utf-8", errors="replace"` on every test-fixture `subprocess.run` so the guard stays satisfied across the whole tree.
- Tests: green — `uv run pytest` 960 passed / 4 skipped (the skips are opt-in `RALPH_PROMPT_SMOKE` and missing `shellcheck`); `uv run ruff check .` clean; `uv run ruff format --check .` clean; `uv run mypy ralph_executor scripts skills tests` clean.
- Regression coverage:
  - `tests/executor/test_subprocess_utils.py` — wrapper-level tests including the 0x90-byte case from BUG.md and a streaming-tee variant.
  - `tests/executor/test_claude_spawn.py::test_tee_stream_survives_invalid_utf8_byte` — drives the real `_tee_stream` with a fixture child emitting `b'\x90\n'` and asserts the line lands in the buffer as `'�\n'`.
  - `tests/executor/test_git_ops.py::test_run_git_survives_non_ascii_commit_subject` — commits with an emoji/accented subject and round-trips it through `_run_git`.
- Notes:
  - `docs/INVESTIGATE.md` is not present in this repo; the bug PBI's investigation surface is fully described by BUG.md (call-site table + reproduction), so no separate INVESTIGATE.md guidance was needed.
  - Pre-existing mypy comparison-overlap on `tests/executor/test_claude_spawn.py` (the `_FakeProc` vs `subprocess.Popen[str]` equality) was retyped to `list[object]` so mypy stays clean — this was a latent HEAD failure rather than a new regression, and it sat on the only PRs path through `uv run mypy`.
  - `ralph_executor/claude_spawn._kill_process_tree` invokes `taskkill` with `capture_output=True` but NO `text=True`; it discards output, so it stays bytes-mode and the guard correctly ignores it.
  - `scripts/queue_writer.py` has two `subprocess.run` callsites that use `text=True`; both got `encoding="utf-8", errors="replace"` inline because the `scripts/` package can't import `ralph_executor.*`.

Root cause: Every `subprocess.Popen` / `subprocess.run` call that passed `text=True` without `encoding=` fell back to `locale.getpreferredencoding()` (cp1252 on stock Windows); a single non-cp1252 byte in the child's stdout (e.g. `0x90` mid-UTF-8) raised `UnicodeDecodeError` inside the reader and crashed the executor iteration.
Fix: Introduced `run_text` / `popen_text` wrappers pinning `text=True, encoding="utf-8", errors="replace"`; migrated all 13 vulnerable call sites; added a CI guard test that fails the build if a new `text=True` call site forgets `encoding=`.

## Iteration 2 — 2026-05-28T12:35:00+00:00 — PR created

- PR: #49 (https://github.com/emp3thy/ralph/pull/49)
- Branch: ralph/BUG-SUBPROCESS-WINDOWS-ENCODING-AUDIT
- Title: BUG-SUBPROCESS-WINDOWS-ENCODING-AUDIT: pin utf-8 on every subprocess text-mode call
