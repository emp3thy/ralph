<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-28T13:30:00+00:00

- Step Task 1: rewrote `scripts/queue_writer.py` — deleted `checkout_queue_branch`, added `acquire_queue_clone` (thin wrapper around `ralph_executor.queue_clone.ensure_queue_clone`). Updated module docstring from "ralph-queue branch" → "queue clone".
- Rewrote `tests/test_queue_writer.py` to drop the ralph-queue branch fixture seeding and the three `checkout_queue_branch_*` tests; added `test_acquire_queue_clone_returns_path`, `test_acquire_queue_clone_forwards_timeout`, `test_checkout_queue_branch_is_removed`; adjusted `test_push_advances_remote` + `test_commit_paths_*` to operate on `main`.
- Tests: green — `uv run pytest tests/test_queue_writer.py` → 15 passed.
- Lint/type: `uv run ruff check`, `uv run ruff format`, `uv run mypy` clean on touched files.
- Notes: This commit leaves `skills/ralph-{add,cancel,promote,triage}/scripts/*.py` referencing the deleted `checkout_queue_branch` import — broken until Tasks 2–5 land. Expected per plan (each subsequent task fixes its own skill). Do not run `pytest -q` whole-suite until Task 5 ships.

