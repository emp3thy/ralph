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

## Iteration 5 — 2026-05-28T17:30:00+00:00

- Step Task 6b: rewrote `skills/ralph-status/scripts/status.py` argparse + entry-point flow to operate on the single queue clone. New argument shape: `--state` (unchanged choices), `--target-repo` (new optional URL filter), `--json` (unchanged), `--workspace` (config override), `--queue-repo` (config override). Dropped `--repo` / `--repos-file` / `--branch` / `--no-cleanup` and the `RALPH_QUEUE_BRANCH` env-var fallback.
- New `_main` flow: `resolve_workspace_root(args.workspace)` → `resolve_queue_repo(args.queue_repo)` → `acquire_queue_clone(...)` → `_collect_rows` walks `STATE_FOLDERS` (or just `--state` if filtered) via `scripts.pbi_reader.enumerate_state`. Applies `--target-repo` filter post-collection. Renderer still uses `row.repo_name` for now (REPO/STATE/ID/TYPE/SEVERITY/AGE/TITLE columns) — Task 6c swaps to a `TARGET` column.
- `_render_json` + `_row_to_json` rewritten to a single-queue shape: envelope is `{"rows": [...], "errors": []}` with `target_repo` per row and no `repos` array. Task 6d finalises field set + tests.
- Imports trimmed of `subprocess`, `shutil`, `tempfile`, `os`, `contextlib` (Task 6a deleted their usages; Task 6b cleared the leftover argparse references). Adds `scripts.queue_writer.{QueueWriterError, acquire_queue_clone, resolve_queue_repo, resolve_workspace_root}`.
- Smoke check: `uv run python skills/ralph-status/scripts/status.py --help` prints the new shape (no `--repo` / `--repos-file` / `--branch` / `--no-cleanup`).
- Lint/type: `uv run ruff check`, `uv run ruff format`, `uv run mypy ralph_executor scripts skills` clean (66 source files, no issues).
- Updated `docs/superpowers/plans/2026-05-28-skills-queue-clone-migration-plan.md` Task 6b checkboxes (Steps 1–4 → [x]).
- Notes: Prior iterations (Task 5 ralph-triage, Task 6a delete worktree machinery) committed in earlier sessions (9e0fe9f, ca8ba45) but did not append to this HISTORY.md — no iteration numbers were claimed for them; this Iteration 5 entry follows directly from Iteration 4 (Task 4). `tests/skills/test_ralph_status.py` still references the old multi-repo fixtures — left untouched per plan ("Do not run tests yet"). Whole-suite `pytest -q` still expected to fail at import on the test module.

## Iteration 6 — 2026-05-28T18:30:00+00:00

- Step Task 6c: rewrote `_COLUMN_ORDER`, `_row_to_cells`, and the entry-point flow in `skills/ralph-status/scripts/status.py` so the table renders a `TARGET` column (was `REPO`) sourced from `PBIRow.target_repo`. Long URLs are truncated to 50 chars (47 + "...") via a new `_truncate_target` helper. Missing/empty `target_repo` renders as `"?"`. The PBIRowError row still renders 7 cells: `"?"` for TARGET, then state, dir-name, three `"?"` placeholders, and the `(parse error) <msg>` title cell.
- Added `_group_and_sort` which stable-sorts rows by `(target_repo, state, created_at-iso)` so the default output groups all rows for a given target under contiguous lines. PBIRowError rows sort to the head (empty target). `main()` now applies the `--target-repo` filter first (already in place from Task 6b) and pipes the result through `_group_and_sort` before rendering — same ordering applies to both `--json` and table output.
- Smoke check: `uv run python skills/ralph-status/scripts/status.py --help` still prints the Task 6b argument shape (no `--repo` / `--repos-file` / `--branch` / `--no-cleanup`).
- Lint/type: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy skills/ralph-status/scripts/status.py` clean.
- Updated `docs/superpowers/plans/2026-05-28-skills-queue-clone-migration-plan.md` Task 6c checkboxes (Steps 1–5 → [x]).
- Notes: `tests/skills/test_ralph_status.py` still references the old multi-repo fixtures — left untouched per plan ("Do not run tests yet" carries through to Task 6d, which rewrites them). Whole-suite `pytest -q` still expected to fail at import on that test module. Stale Pyright diagnostics flagged `_TARGET_DISPLAY_MAX` and `_group_and_sort` as unused after the first two edits — both are referenced after the third edit (`_truncate_target` reads the const; `main` calls `_group_and_sort`) and mypy confirms.

## Iteration 7 — 2026-05-28T19:30:00+00:00

- Step Task 6d: rewrote `tests/skills/test_ralph_status.py` from scratch around a `queue_env` fixture (bare-remote queue with seeded `main` + tmp workspace). Replaced 12 legacy multi-repo tests with 11 single-queue-clone tests. Coverage: default lists all states with TARGET column, rows grouped contiguously by target_repo, `--state current` filter, `--target-repo` filter, JSON envelope shape (`{"rows","errors"}`, no `repos` key, no legacy `repo`/`repo_name` fields, `target_repo` present per row), JSON+`--target-repo` filter, empty queue header-only render, malformed PBI → error row (`error` set, `target_repo`/`type` null), `--state limbo` rejected by argparse choices, queue_repo-unset error path, TOML-resolved queue_repo. Added a `seeded_queue` fixture composing `queue_env` that pushes 5 PBIs (WI-1234 inbox/feature, BUG-rosa inbox/bug, WI-1235 current/feature, WI-980 pending-pr/feature, WI-3000 blocked/feature) across 2 target URLs (svc-auth, svc-billing).
- Step Task 6d / Step 1 (Replace `_row_to_json`): already in place from Iteration 5 (Task 6b's JSON rewrite); confirmed it matches the plan's spec (envelope `{"rows","errors"}`, per-row `target_repo`/`state`/`id`/`type`/`severity`/`attempts`/`created_at`/`updated_at`/`title`/`pbi_dir`/`error`). No code change needed.
- Tests: green — `uv run pytest tests/skills/test_ralph_status.py` → 11 passed.
- Broader suite (`uv run pytest tests/skills tests/test_queue_writer.py -q`) → 208 passed, 2 expected failures in `tests/skills/test_supervisor_skills_smoke.py` (still references the deleted `--repo` argv on cancel/promote/triage). That file is out of scope for Task 6d — Tasks 7 (SKILL.md sweep) / 10 (full-suite gate) finalise it.
- Lint/type: `uv run ruff check`, `uv run ruff format`, `uv run mypy` clean on touched files.
- Updated `docs/superpowers/plans/2026-05-28-skills-queue-clone-migration-plan.md` Task 6d checkboxes (Steps 1–4 → [x]).
- Notes: status.py already had the correct JSON shape and grouping from Iterations 5–6; this iteration's only code surface is the new test file. After this commit, Task 6 (split into 6a–6d) is fully done; next iteration picks up Task 7 (SKILL.md sweep + supervisor_skills_smoke fix to unblock the full suite).

## Iteration 8 — 2026-05-28T20:30:00+00:00

- Step Task 7: SKILL.md sweep across `skills/ralph-*/SKILL.md`. Grep for stale model references (`ralph-queue branch`, `--branch`, `--repos-file`, `service repo`, `--no-cleanup`, `RALPH_QUEUE_BRANCH`) turned up `skills/ralph-status/SKILL.md` as the only file still describing the old multi-repo / worktree model. All other matches in `ralph-{add,cancel,promote,triage}/SKILL.md` and `ralph-doctor/SKILL.md` were on the phrase "target service repo" used in correct context ("does not touch the target service repo" / informational metadata) and intentionally retained.
- Rewrote `skills/ralph-status/SKILL.md` from scratch to match the queue-clone model. New frontmatter description references the single queue clone and `--target-repo` grouping. Body sections updated: `Inputs` table now lists `--state`, `--target-repo`, `--json`, `--workspace`, `--queue-repo` only; the `--repo` / `--repos-file` / `--branch` / `--no-cleanup` rows are gone. `Configuration` section added describing TOML resolution + override flags. `Output > Table mode` example reflects the new TARGET column (50-char URL truncate), grouping by `(target_repo, state, created_at)`, and the new stderr summary line. `Output > JSON mode` example shows the `{"rows","errors"}` envelope with no `repos` key and the per-row field set (`target_repo`, `state`, `id`, `type`, `severity`, `attempts`, `created_at`, `updated_at`, `title`, `pbi_dir`, `error`). `Environment variables` section removed (no env vars read directly). `What this skill does NOT do` reframed around the queue clone — no worktree cleanup language.
- Lint: `uv run ruff check skills/ralph-status` clean (SKILL.md is markdown so ruff scans the script only; no regressions on `status.py`).
- Updated `docs/superpowers/plans/2026-05-28-skills-queue-clone-migration-plan.md` Task 7 checkboxes (Steps 1–3 → [x]).
- Notes: Tasks 1–6d + 7 now complete. Next iteration picks up Task 8 (README rewrite — Install + "Working the queue" section). Whole-suite `pytest -q` from Iteration 7 still green (208 passed, 2 expected fails in `tests/skills/test_supervisor_skills_smoke.py` — out of scope until Task 10's full-suite gate).

## Iteration 9 — 2026-05-28T21:30:00+00:00

- Step Task 8: rewrote `README.md` Install + One-time setup sections + added new "Working the queue" section. Verbatim per plan Task 8 Steps 1–2. Install section now splits into Workstation (`uv sync`) vs Container (ROSA image pull + pointer to pod-deployment runbook) subsections. One-time setup describes the three TOML keys (`ralph_home`, `workspace_root`, `queue_repo`) plus a scripted-setup example and the `init`-clone smoke-test note. New "Working the queue" section walks all five skills (`ralph-status`, `ralph-add`, `ralph-cancel`, `ralph-promote`, `ralph-triage`) with example invocations and the grouped-by-target table sample for `ralph-status`.
- Lint/type: README.md is markdown — ruff/mypy untouched by this iteration (no .py edits).
- Updated `docs/superpowers/plans/2026-05-28-skills-queue-clone-migration-plan.md` Task 8 checkboxes (Steps 1–3 → [x]).
- Notes: `## Per-repo setup` section (lines after the new "Working the queue" block) still describes the obsolete `ralph-queue` branch scaffold model (`scaffold --workspace`, `git push -u origin ralph-queue`, `setup_ralph_queue_github.py`). Same for the `## Running ralph` section ("switches to the `ralph-queue` branch"). Both contradict the queue-clone model the new sections introduce. Task 8 scope is bounded by the plan template (Install + One-time setup + Working the queue only) so deferred — flag for a separate doc-cleanup PBI or fold into Task 10's final sweep if the reviewer flags it. Next iteration picks up Task 9 (new `docs/superpowers/ops/2026-05-28-pod-deployment.md` runbook).

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

## Iteration 10 — 2026-05-28T22:30:00+00:00

- Step Task 9: created new file `docs/superpowers/ops/2026-05-28-pod-deployment.md` with the verbatim template from plan Task 9 Step 1. Sections: Image (ROSA Phase 1 GitHub variant + reference to `docs/superpowers/plans/2026-05-24-12-rosa-packaging.md`), Required config (the two-key TOML minimum + three optional keys), Required secrets (GH_TOKEN or mounted gh hosts.yml; mounted `~/.claude/.credentials.json` for Claude Code OAuth), Layout under workspace_root (tree showing `queue/` clone + `clones/<owner>-<repo>/.ralph-work/<id>/` per-target worktrees), Exit behaviour (default exit-when-idle; `--watch` to poll forever; refers to PBI `EXECUTOR-EXIT-WHEN-IDLE-DEFAULT`), Health and logs (stderr INFO + halt-sentinel path at `$workspace_root/queue/.ralph/state/halted`), Updating the image (pull new tag — zero-downtime single-instance), Troubleshooting (queue_repo missing, clone-failed = auth, halt-sentinel-active = inspect META-cycle-* + edit halted file). Created the `docs/superpowers/ops/` directory in the same operation (no prior ops docs in the tree — `docs/superpowers/` previously held only `plans/`, `runbooks/`, `specs/`).
- Lint/type: doc-only; no .py edits in this iteration.
- Updated `docs/superpowers/plans/2026-05-28-skills-queue-clone-migration-plan.md` Task 9 checkboxes (Steps 1–2 → [x]).
- Notes: Tasks 1–9 now complete. Next iteration picks up Task 10 (full gate: ruff check + ruff format --check + mypy + `pytest -q` + push + open PR). Iteration 7's broader-suite run was 208 passed / 2 expected fails in `tests/skills/test_supervisor_skills_smoke.py` (still references deleted `--repo` argv on cancel/promote/triage); Task 10 must either fix that test or note it as a known-failing scope-creep candidate before opening the PR.

## Iteration 11 — 2026-05-28T23:30:00+00:00 — PR ship

- Step Task 10: full gate. `uv run ruff check .` → clean (139 files, only the 5 stale `# noqa` warnings in `skills/ralph-doctor/scripts/checks/{hooks,skills}.py` that pre-date this PBI). `uv run ruff format --check .` → 139 files already formatted. `uv run mypy ralph_executor scripts skills tests` → no issues in 133 source files.
- Step Task 10 / supervisor-smoke fix: rewrote `tests/skills/test_supervisor_skills_smoke.py` from scratch around the `queue_env` fixture pattern (bare queue remote + seeded `main`). Replaced the 2 obsolete tests (`test_promote_then_triage_round_trip` exercising the deleted `--severity` bump path, `test_cancel_then_promote_is_independent` exercising `--repo` on cancel + the deleted promote severity bump) with two tests fitting the new model:
  - `test_triage_then_promote_round_trip` — seed `.ralph/blocked/WI-9000` (attempts=3) → `ralph-triage --to inbox --note ...` → `ralph-promote --from inbox --to current`. Verifies the PBI ends in `current/`, attempts=0 (set by triage), status=current (set by promote), HISTORY.md carries entries from both skills.
  - `test_cancel_and_promote_are_independent` — seed `.ralph/current/WI-9100` + `.ralph/inbox/WI-9101` → cancel WI-9100, promote WI-9101 inbox→current. Verifies CANCEL sentinel + the promoted PBI moved correctly without stomping.
- Step Task 10 / encoding-audit fix: `tests/executor/test_subprocess_encoding_audit.py` flagged 8 violations across `tests/skills/test_ralph_{add,cancel,promote,triage}.py` — two `subprocess.run(["git", "ls-remote", queue_repo, "main"], ..., text=True)` calls per file with no `encoding=`/`errors=`. Added `encoding="utf-8", errors="replace"` to all 8 call sites via `replace_all` (each file has identical pattern, two occurrences).
- Tests: green — `uv run pytest -q` → 982 passed, 4 skipped (2 shellcheck-not-installed in `tests/packaging/test_scripts.py`, 2 opt-in `RALPH_PROMPT_SMOKE` in `tests/test_prompt_smoke.py`).
- Updated `docs/superpowers/plans/2026-05-28-skills-queue-clone-migration-plan.md` Task 10 checkboxes (Steps 1–4 → [x]).
- Notes: All 10 plan tasks now complete. Ready to push `ralph/SKILLS-QUEUE-CLONE-MIGRATION` and open the PR against `main` via `gh pr create`. The README `## Per-repo setup` + `## Running ralph` sections still describe the obsolete `ralph-queue` branch model (flagged in Iteration 9 notes) — left for a separate doc-cleanup PBI per Task 8's bounded scope.

## Iteration 12 — 2026-05-28T23:45:00+00:00 — PR created

- PR: #50
- URL: https://github.com/emp3thy/ralph/pull/50
- Branch: ralph/SKILLS-QUEUE-CLONE-MIGRATION (pushed to origin, 13 commits ahead of main)
- Title: SKILLS-QUEUE-CLONE-MIGRATION: migrate operator skills + docs to queue-clone model
