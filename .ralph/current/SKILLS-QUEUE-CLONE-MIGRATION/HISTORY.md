<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-28T13:30:00+00:00

- Step Task 1: rewrote `scripts/queue_writer.py` — deleted `checkout_queue_branch`, added `acquire_queue_clone` (thin wrapper around `ralph_executor.queue_clone.ensure_queue_clone`). Updated module docstring from "ralph-queue branch" → "queue clone".
- Rewrote `tests/test_queue_writer.py` to drop the ralph-queue branch fixture seeding and the three `checkout_queue_branch_*` tests; added `test_acquire_queue_clone_returns_path`, `test_acquire_queue_clone_forwards_timeout`, `test_checkout_queue_branch_is_removed`; adjusted `test_push_advances_remote` + `test_commit_paths_*` to operate on `main`.
- Tests: green — `uv run pytest tests/test_queue_writer.py` → 15 passed.
- Lint/type: `uv run ruff check`, `uv run ruff format`, `uv run mypy` clean on touched files.
- Notes: This commit leaves `skills/ralph-{add,cancel,promote,triage}/scripts/*.py` referencing the deleted `checkout_queue_branch` import — broken until Tasks 2–5 land. Expected per plan (each subsequent task fixes its own skill). Do not run `pytest -q` whole-suite until Task 5 ships.

## Iteration 2 — 2026-05-28T14:30:00+00:00

- Step Task 2: rewrote `skills/ralph-add/scripts/add.py` to operate on the queue clone. Dropped `--repo`/`--repo-url`/`--branch`/`DEFAULT_QUEUE_BRANCH` and the auto-derive `_derive_target_repo` path (origin sniffing). Added required `--target-repo` flag plus optional `--workspace`/`--queue-repo` config overrides. Resolves `workspace_root` + `queue_repo` via new helpers in `scripts/queue_writer.py` (`resolve_workspace_root`, `resolve_queue_repo`) which read `~/.ralph/config.toml`. Writes PBI under `<queue_clone>/.ralph/inbox/<id>/`, commits with `chore(queue): add <id>`, pushes `origin/main`.
- Added `read_workspace_root()` to `ralph_executor/user_config.py` so skills can read the workspace_root TOML key without depending on the executor's full config loader.
- `AddResult` schema change: removed `repo_path` + `branch` fields; added `queue_clone` + `target_repo`. Existing PR-pipeline consumers were already on the v2 shape (target_repo metadata).
- Rewrote `tests/skills/test_ralph_add.py` from scratch around a `queue_env` fixture (bare-remote queue with seeded `main` + tmp workspace). Replaced 16 existing tests, added 5 new tests covering `--target-repo` required, TOML resolution, and missing-queue_repo error path. Removed obsolete `_derive_target_repo_*` tests and the `git_repo`/`_init_repo_with_origin` helpers.
- Updated `skills/ralph-add/SKILL.md` for the new argv shape and queue-clone model.
- Updated `docs/superpowers/plans/2026-05-28-skills-queue-clone-migration-plan.md` Task 2 checkboxes (Steps 1–6 → [x]).
- Tests: green — `uv run pytest tests/skills/test_ralph_add.py tests/test_queue_writer.py` → 35 passed.
- Lint/type: `uv run ruff check`, `uv run ruff format`, `uv run mypy` clean on touched files.
- Notes: `skills/ralph-{cancel,promote,triage}/scripts/*.py` and `skills/ralph-status/scripts/status.py` still reference the deleted `checkout_queue_branch` import — broken until Tasks 3–6 land. Whole-suite `pytest -q` still expected to fail at import time on those four skills.

