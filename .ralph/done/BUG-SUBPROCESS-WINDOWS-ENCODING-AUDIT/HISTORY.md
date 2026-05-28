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
- 2026-05-28T12:19:23.107149+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:19:59.569711+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:20:36.211673+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:21:12.788798+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:21:49.093817+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:22:25.927425+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:23:02.729901+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:23:39.866275+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:24:16.687641+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:24:53.598144+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:25:29.904914+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:26:06.842796+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:26:44.514379+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:27:21.864978+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:27:58.368245+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:28:35.611716+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:29:12.662149+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:29:49.716343+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:30:26.933759+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:31:04.945258+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:31:42.123156+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:32:19.320486+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:32:56.817739+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:33:34.182528+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:34:11.586227+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:34:49.812956+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:35:27.481142+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:36:04.548601+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:36:44.877040+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:37:22.451932+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:37:59.959599+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:38:37.422345+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:39:15.240096+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:39:52.038863+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:40:29.143748+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:41:06.451185+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:41:43.759436+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:42:20.835577+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:42:57.507601+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:43:34.485990+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:44:11.537305+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:44:48.760301+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:45:26.333820+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:46:03.094441+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:46:40.536539+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:47:17.537769+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:47:55.373942+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:48:32.101678+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:49:09.462361+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:49:46.226761+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:50:23.426567+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:51:00.738357+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:51:37.972728+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:52:15.208867+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:52:52.226749+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:53:29.137368+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:54:06.395022+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:54:43.748108+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:55:25.647393+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:56:02.610848+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:56:39.918306+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:57:16.879502+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:57:53.911821+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:58:31.079890+00:00 sweep: PR stale: no activity for >= 3 days, 0:00:00 (last activity 0001-01-01T00:00:00+00:00)
- 2026-05-28T12:59:08.160299+00:00 sweep: PR merged (completed)
