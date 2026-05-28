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

## Iteration 3 — 2026-05-28T15:30:00+00:00

- Step Task 3: rewrote `skills/ralph-cancel/scripts/cancel.py` to operate on the queue clone. Dropped `--repo`, `--branch`, `DEFAULT_QUEUE_BRANCH`, and the `RALPH_QUEUE_BRANCH` env-var fallback. Added optional `--workspace`/`--queue-repo` overrides; resolves both from `~/.ralph/config.toml` via `resolve_workspace_root` / `resolve_queue_repo`. Calls `acquire_queue_clone` to materialise the clone; sentinel writes target `<clone>/.ralph/current/<id>/CANCEL`; commit message style unchanged (`chore(queue): cancel <id>`); push goes to `origin/main`.
- `CancelResult` schema change: removed `repo_path` + `branch` fields; added `queue_clone`.
- Dry-run now skips the clone entirely (matches `ralph-add` semantics) and reports the would-be sentinel path against the would-be clone location without network/disk side effects.
- Rewrote `tests/skills/test_ralph_cancel.py` from scratch around the `queue_env` fixture (bare-remote queue with seeded `main` + tmp workspace). Replaced 8 existing tests; new tests cover happy-path, outside-current refuse, missing PBI, `--no-push`, `--dry-run`, idempotency-when-committed, regression for staged-but-uncommitted sentinel, queue_repo-unset error path, and TOML resolution.
- Updated `skills/ralph-cancel/SKILL.md` for the new argv shape and queue-clone model.
- Updated `docs/superpowers/plans/2026-05-28-skills-queue-clone-migration-plan.md` Task 3 checkboxes (Steps 1–6 → [x]).
- Tests: green — `uv run pytest tests/skills/test_ralph_cancel.py` → 9 passed.
- Lint/type: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` clean on touched files.
- Notes: `skills/ralph-{promote,triage}/scripts/*.py` and `skills/ralph-status/scripts/status.py` still reference the deleted `checkout_queue_branch` import — broken until Tasks 4–6 land. Whole-suite `pytest -q` still expected to fail at import time on those three skills.

## Iteration 4 — 2026-05-28T16:30:00+00:00

- Step Task 4: rewrote `skills/ralph-promote/scripts/promote.py` to operate on the queue clone as a STATE MOVER (was: severity bumper). Per spec line 143 and plan Task 4, `ralph-promote` now takes `--pbi-id` + `--from <state>` + `--to <state>` (both required, must differ, both restricted to `QUEUE_STATE_FOLDERS`). Dropped `--repo`, `--branch`, `DEFAULT_QUEUE_BRANCH`, `--severity`, `ALLOWED_SEVERITIES`, and the entire severity-bump idempotency dance (head/working severity cross-check, history dedup loop). Reduced the file from 287 LOC → 271 LOC.
- New behaviour: resolve `workspace_root` + `queue_repo` via `resolve_workspace_root` / `resolve_queue_repo`; `acquire_queue_clone` to materialise the clone; `git mv .ralph/<from>/<pbi-id> .ralph/<to>/<pbi-id>` inside the clone; rewrite entry-file frontmatter (`status: <to>`, refresh `updated_at`); append a single `HISTORY.md` entry (`detail: <from> -> <to>`); commit `chore(queue): promote <id> (<from> -> <to>)`; push `main` to `origin`. Idempotent against HEAD: if the destination entry file already exists in HEAD the script exits 0 with `already_promoted=True` and no new commit.
- `PromoteResult` schema change: removed `previous_severity`, `new_severity`, `state_folder`, `repo_path`, `branch`; added `from_state`, `to_state`, `queue_clone`, `already_promoted`. Kept `pbi_id`, `entry_file`, `commit_sha`, `pushed`, `dry_run`.
- Dry-run skips the clone (matches `ralph-add`/`ralph-cancel`) and reports the would-be move without network/disk side effects.
- Rewrote `tests/skills/test_ralph_promote.py` from scratch around the `queue_env` fixture (bare queue remote + tmp workspace). Replaced 11 existing severity-bump tests with 12 state-mover tests: happy path (feature), bug variant, missing-PBI-at-from-state, same-from-and-to refused, unknown state refused via argparse choices, `--no-push`, `--dry-run`, idempotency-when-already-at-destination, HISTORY appended, queue_repo-unset error path, TOML resolution, destination-dir-exists refused.
- Rewrote `skills/ralph-promote/SKILL.md` for the new argv shape and state-mover purpose. Description on the frontmatter line + body sections (`When to use it`, `Inputs`, `Output`, `What this skill does NOT do`) all reframed around state moves rather than severity bumps. Cross-reference added: `ralph-triage --to inbox` is preferred when `attempts:` should reset.
- Updated `docs/superpowers/plans/2026-05-28-skills-queue-clone-migration-plan.md` Task 4 checkboxes (Steps 1–6 → [x]).
- Tests: green — `uv run pytest tests/skills/test_ralph_promote.py tests/skills/test_ralph_cancel.py tests/skills/test_ralph_add.py tests/test_queue_writer.py` → 56 passed.
- Lint/type: `uv run ruff check`, `uv run ruff format`, `uv run mypy` clean on touched files.
- Notes: `skills/ralph-triage/scripts/triage.py` and `skills/ralph-status/scripts/status.py` still reference the deleted `checkout_queue_branch` import — broken until Tasks 5–6 land. Whole-suite `pytest -q` still expected to fail at import time on those two skills.
