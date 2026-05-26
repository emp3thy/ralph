<!-- Executor appends attempt records here. Do not delete -->

## Iteration 1 — 2026-05-26T22:00:00+00:00

- Step 1: added `signature_from_text` helper to `ralph_executor/safety/events.py` (sha256[:16] of whitespace-normalised, lower-cased text). Re-exported from `ralph_executor.safety`.
- Tests: green — 4 new tests in `tests/safety/test_events.py` (`test_signature_from_text_*`); full file 13/13 pass.
- Lint/format/mypy: clean on touched files.
- Notes: helper used by Tasks 2 + 5 next iterations.

## Iteration 2 — 2026-05-26T23:08:00+00:00

- Step 2: refactored `move_current_to_pending_pr` to accept kwargs-only `event_log`, `pr_url`, `touched_files`, `now`; emits `PR_CREATED` with `signature_from_text(pr_url)` and the cumulative feature-branch diff vs main as `files`. Added fault-tolerant `git_ops.diff_names(repo, base, head)` (empty list on failure). Wired `_run_ralph` to compute the diff and pass through.
- Tests: passed (deferred regression-test additions to Task 6).
- Lint/format/mypy: clean.
- Notes: emission lives AFTER the queue move commit, before the function returns — matches Task 5 ordering pattern.

## Iteration 3 — 2026-05-26T23:30:00+00:00

- Step 3: refactored `move_inbox_to_current` to accept kwargs-only `event_log`, `now`; emits `PBI_OPENED` with empty payload (cycle detector's whack_a_mole only needs the envelope timestamp + pbi_id). Wired `_claim_pbi` to open the event log, pass it through, and close in a try/finally.
- Tests: green — added `test_pbi_opened_event_emitted_on_claim` + `test_move_inbox_to_current_emits_no_event_when_event_log_omitted` to `tests/executor/test_movements.py`. Full suite 371 passed / 2 skipped.
- Lint/format/mypy: clean.
- Fixture fix: `tests/executor/conftest.py::fake_repo` now writes `.gitignore` with `.ralph/state/` to mirror production. Without it, `commit_all`'s `git add -A` swallowed the runtime `events.db` into the queue commit, and the next `event_log.append` inside `_claim_pbi` produced a tracked-file modification that blocked the subsequent `git checkout main`. Recorded as a better-memory `failure` observation (id 0ced4747).
- Notes: Tasks 4 + 5 + 6 remain.

## Iteration 4 — 2026-05-26T23:50:00+00:00

- Step 4: refactored `_persist_iteration_writes` to accept kwargs-only `event_log`, `now`; emits `FILE_TOUCHED` with the diff (`git_ops.diff_names(head_before, head_after)`) after the commit lands. Skipped on no-op commits via existing `head_after != head_before` guard, and on empty diff. Wired `iterate_once` to open the log, pass it through, and close in a try/finally.
- Tests: green — added `test_file_touched_event_emitted_on_iteration_commit` + `test_file_touched_skipped_on_empty_commit` to `tests/executor/test_loop.py`. Full suite 373 passed / 2 skipped.
- Lint/format/mypy: clean across the whole repo.
- Notes: Tasks 5 + 6 remain. Commit landed as feature-branch `b576cb4`.

## Iteration 5 — 2026-05-27T00:30:00+00:00

- Step 5: `handle_stuck` gained an optional kwargs-only `event_log: EventLog | None`. When provided, it emits `SIGNATURE_OBSERVED` with `payload={"signature": signature_from_text(reason)}` BEFORE `move_to_blocked` so the signature is logged even if the move fails. Existing `PBI_BLOCKED` outcome.event flow is unchanged. Wired `_run_ralph` (loop.py) to pass the per-iteration `event_log` through.
- Tests: green — added `test_signature_observed_event_emitted_on_stuck` (uses conftest `event_log` fixture) and `test_handle_stuck_emits_no_signature_when_event_log_omitted` to `tests/safety/test_stuck.py`; full safety + executor suites 260 passed.
- Lint/format/mypy: clean across the whole repo (`ruff check .`, `ruff format --check .`, `mypy ralph_executor scripts skills tests`).
- Notes: Task 6 (cycle-detector integration tests — the per-emit-site `test_signature_observed_event_emitted_on_stuck` already landed here) remains. Next iteration writes `test_same_file_thrashing_trips_after_six_distinct_prs_touching_one_file` and `test_signature_recurrence_trips_after_pbi_closed_then_signature_reobserved`. Commit landed as feature-branch `53e2bb4`.
