<!-- Executor appends attempt records here. Do not delete -->

## Iteration 1 — 2026-05-27T00:00:00+00:00

- Step: Task 1 — created `ralph_executor/worktree.py` with `ensure_worktree`, `list_worktrees`, `remove_worktree`, `worktree_branch` helpers built on the same `_run_git` wrapper as `git_ops`.
- Design notes:
  - `_local_branch_exists` added because `git_ops.branch_exists` also considers `origin/` refs, but `git worktree add <path> <branch>` requires a local ref.
  - `_existing_worktree_paths` resolves paths from `git worktree list --porcelain` to a `set[Path]` so idempotency checks survive path-style differences (forward vs back slashes on Windows).
  - `remove_worktree` always passes `--force` (to clear half-broken state from prior crashes) and runs `git worktree prune` even when the path is already gone.
  - Docstring on `remove_worktree` warns about the Windows CWD-pinning gotcha (memory id `0dda832cbec346c29e5e81e3cb2113f9`) so callers in Task 7 do not invoke it from inside the worktree being removed.
- Tests: full `pytest` suite green (447 passed, 2 skipped — opt-in prompt smoke). `ruff check`, `ruff format --check`, `mypy ralph_executor scripts skills tests` all clean.
- Notes: Task 9 still has to add dedicated worktree-helper tests; this iteration only re-ran the existing suite to confirm no regression from the new import. Commit: `d20581a feat(worktree): worktree.py helpers (ensure/list/remove/branch)` on `ralph/STAGE-B-PLAN-20a-worktree`.

## Iteration 2 — 2026-05-27T03:00:00+00:00

- Step: Task 2 — added `use_worktrees: bool` (default True) to `ExecutorConfig`, registered `use_worktrees` in `_TOML_KNOWN_KEYS`, introduced `_resolve_bool` helper (env > toml > default with explicit true/false/1/0/yes/no/on/off allow-list and rejection on unknown values), and threaded it through `load_config`. New env var `RALPH_USE_WORKTREES`.
- Design notes:
  - `_resolve_bool` mirrors the existing `_resolve_int` / `_resolve_float` shape; rejects non-bool TOML values (including ints / strings) so config typing stays honest.
  - `use_worktrees` is the only `ExecutorConfig` field with a default — placed last in the dataclass so existing positional callers still work, and so the Stage-B default is the no-config behaviour.
  - The feature branch was behind ralph-queue, but ralph-queue's `ralph_executor/config.py` is the OLDER snapshot (8e20469) and `main` carries the newer file (2d1cd99) with the PR-check poll knobs added by PR #22. The new code was therefore applied on top of `main`'s config.py via the `ralph/STAGE-B-PLAN-20a-worktree` checkout, not on top of ralph-queue's stale copy.
- Tests: added six new tests covering env true / false / invalid, TOML default / false / wrong-type, and env-wins-over-TOML. Full `pytest` suite green (454 passed, 2 skipped — opt-in prompt smoke). `ruff check`, `ruff format --check`, `mypy ralph_executor scripts skills tests` all clean.
- Notes: queue branch was checked out at iteration start; switched to `ralph/STAGE-B-PLAN-20a-worktree` to land the code commit, then returned to ralph-queue for HISTORY/PLAN edits (the executor's `_persist_iteration_writes` mirrors them onto ralph-queue). Commit: `78877b2 feat(config): use_worktrees TOML/env knob` on `ralph/STAGE-B-PLAN-20a-worktree`.

## Iteration 3 — 2026-05-27T06:00:00+00:00

- Step: Task 3 — worktree-mode `_claim_pbi`. Added `_claim_pbi_worktree` branch that ensures the queue worktree at `.ralph-work/queue/`, runs `git fetch origin`, calls `move_inbox_to_current` (which now routes through the queue worktree via `movements._queue_repo`), then materialises the per-PBI work worktree at `.ralph-work/repo-<PBI-id>/` forked from `origin/<main_branch>`. Legacy single-checkout path untouched. Also made `_ensure_on_queue_branch` a no-op in worktree mode and rerouted `_pull_queue` through the queue worktree — both needed because git refuses to check the queue branch out in two worktrees simultaneously.
- Design notes:
  - Added `queue_worktree_path` / `work_worktree_path` helpers in `ralph_executor/worktree.py` (pure path computation, no git dependency) so loop.py and movements.py share the canonical layout without circular imports.
  - `movements._move` now resolves the working directory via `_queue_repo(cfg)` — queue worktree in worktree mode, primary checkout in legacy mode — and skips the redundant `git checkout queue_branch` in worktree mode (the queue worktree is already pinned).
  - Worktree mode forks the new feature branch from `origin/<main_branch>` rather than local `main`. This avoids touching the primary checkout (which may have `main` checked out) and keeps the local `main` ref untouched; we rely on the `git fetch` at claim time for freshness.
  - Existing tests use the legacy path: `tests/executor/conftest.py::cfg_for_repo` and the inline `ExecutorConfig` in `tests/safety/test_integration_loop.py` now set `use_worktrees=False` explicitly. Worktree-mode coverage lands with Task 9.
  - Memory: recorded `git worktree add` rejecting a branch already-checked-out elsewhere (`ebaea5e4dc184729b9af24b8c68344a3`).
- Tests: `uv run --no-sync pytest` green (454 passed, 2 skipped — opt-in prompt smoke). `ruff check`, `ruff format --check`, `mypy ralph_executor scripts skills tests` all clean.
- Notes: switched to `ralph/STAGE-B-PLAN-20a-worktree` to land the code commit, then back to ralph-queue for HISTORY/PLAN edits (executor's `_persist_iteration_writes` mirrors them onto ralph-queue). Commit: `d969b73 feat(loop): worktree-mode claim creates per-PBI work tree` on `ralph/STAGE-B-PLAN-20a-worktree`.

## Iteration 4 — 2026-05-27T09:00:00+00:00

- Step: Task 4 — `spawn_claude_p` now takes kwarg-only `cwd: Path | None` and `pbi_dir: Path | None` overrides (default to `cfg.repo_path` and `pbi.path` for legacy mode). `_build_argv` substitutes the explicit `pbi_dir` into the prompt string and drops the unused `pbi` arg. `_run_ralph` resolves `cwd=work_worktree_path(cfg.repo_path, pbi.id)` and `pbi_dir=queue_worktree_path(cfg.repo_path) / .ralph / current / pbi.id` when `cfg.use_worktrees`, otherwise passes nothing (defaults preserve Stage-A behaviour).
- Design notes:
  - `RALPH_PBI_DIR` was already always set (Stage A invariant); now both modes resolve it from the new `effective_pbi_dir`.
  - Validation: `spawn_claude_p` raises `FileNotFoundError` if `effective_pbi_dir` or `effective_cwd` is not an existing directory before spawning, addressing the adversarial pre-pass "RALPH_PBI_DIR pointing nowhere" item.
  - `classify_outcome` also receives `effective_pbi_dir` so STUCK.md detection looks in the queue worktree (not the work worktree where Claude's cwd is rooted).
  - `_pr_query_open_pr_via_gh` keeps using `cfg.repo_path` — gh CLI infers the GitHub remote from `.git/config` which is shared by all worktrees, so the primary checkout is fine.
  - Existing test (`test_spawn_invokes_claude_with_pbi_context`) hits the legacy default path; no test update needed for Task 4 (worktree-mode spawn coverage lands with Task 9).
- Tests: full `pytest` suite green (454 passed, 2 skipped — opt-in prompt smoke). `ruff check`, `ruff format --check`, `mypy ralph_executor scripts skills tests` all clean.
- Notes: switched to `ralph/STAGE-B-PLAN-20a-worktree` to land the code commit, then back to ralph-queue for HISTORY/PLAN edits (executor's `_persist_iteration_writes` mirrors them onto ralph-queue). Commit: `881fce2 feat(claude_spawn): spawn into work tree with RALPH_PBI_DIR pointing at queue tree` on `ralph/STAGE-B-PLAN-20a-worktree`.

## Iteration 5 — 2026-05-27T12:00:00+00:00

- Step: Task 5 — rewrote `prompt/PROMPT.md` "What you read" section to use `$RALPH_PBI_DIR/...` for every active-PBI file reference (HISTORY.md, PBI.md, PLAN.md, BUG.md, REPRODUCE.md, ORIGINAL.md, FEEDBACK.md). Added preamble paragraph stating the absolute-path contract and warning that `.ralph/current/...` is the queue worktree's interior — Claude's cwd in worktree mode is the code worktree, so the relative form no longer resolves the way it used to.
- Design notes:
  - Kept one literal `.ralph/current/...` token in the preamble inside the "Do NOT use" sentence so the existing structural test (`test_prompt_contains_required_phrase[".ralph/current/"]`) still passes — that phrase is load-bearing as a recognisable anchor for human readers, not just as a path.
  - Reworded the `docs/INVESTIGATE.md` bullet to clarify it lives at the repo root of the **code worktree** (Claude's cwd), since that distinction now matters in worktree mode.
  - Did NOT touch the unprefixed mentions like "Open `PLAN.md`", "Write `STUCK.md`", "Append to `HISTORY.md`" inside the per-step instructions; the preamble explicitly establishes that any PBI-file shorthand resolves under `$RALPH_PBI_DIR/`. Rewriting every shorthand would have bloated the prompt without adding clarity beyond the preamble's contract.
  - STUCK.md never had the `.ralph/current/...` prefix in the file (it was always referred to by basename); preamble covers it.
- Tests: full `pytest` suite green (454 passed, 2 skipped — opt-in prompt smoke). `tests/test_prompt_structure.py` 30/30 passed. `ruff check`, `ruff format --check`, `mypy ralph_executor scripts skills tests` all clean.
- Notes: switched to `ralph/STAGE-B-PLAN-20a-worktree` to land the code commit, then back to ralph-queue for HISTORY/PLAN edits. Commit: `2ee88b6 docs(prompt): switch PBI-file references to $RALPH_PBI_DIR` on `ralph/STAGE-B-PLAN-20a-worktree`.

## Iteration 6 — 2026-05-27T15:00:00+00:00

- Step: Task 6 — `_persist_iteration_writes` now routes its `git add` / `commit` / `push` through the queue worktree when `cfg.use_worktrees=True`. Computes `queue_repo = queue_worktree_path(cfg.repo_path) if cfg.use_worktrees else cfg.repo_path` at function entry and uses that for the `pbi_dir` lookup and every `git_ops.*` call (including the `diff_names` used for the `FILE_TOUCHED` payload). Legacy single-checkout path keeps `cfg.repo_path` exactly as before. `_ensure_on_queue_branch(cfg)` is still called first — it already no-ops in worktree mode (iteration 3), so it costs nothing and keeps the legacy semantics intact.
- Design notes:
  - Same shape as `movements._queue_repo` (iteration 3): single-line ternary at the top, then every git op uses the chosen path. Did not extract a shared helper — two call sites is below the threshold where indirection pays for itself, and the loop module already imports `queue_worktree_path` directly.
  - Updated the function docstring with the worktree-vs-legacy paragraph so future readers do not have to re-derive the routing from the body.
  - No new tests this iteration — the existing `test_iterate_once_persists_claude_history_writes_on_partial`, `test_persist_iteration_writes_excludes_state_dir`, `test_file_touched_event_emitted_on_iteration_commit`, and `test_file_touched_skipped_on_empty_commit` all cover the legacy path (`cfg_for_repo` sets `use_worktrees=False`). Worktree-mode persist coverage lands with Task 9.
- Tests: full `uv run --no-sync pytest` green (454 passed, 2 skipped — opt-in prompt smoke). `ruff check`, `ruff format --check`, `mypy ralph_executor scripts skills tests` all clean.
- Notes: switched to `ralph/STAGE-B-PLAN-20a-worktree` to land the code commit, then back to ralph-queue for HISTORY/PLAN edits (the executor's `_persist_iteration_writes` mirrors them onto ralph-queue). Commit: `49fd914 refactor(loop): _persist_iteration_writes operates on queue worktree` on `ralph/STAGE-B-PLAN-20a-worktree`.

## Iteration 7 — 2026-05-27T18:00:00+00:00

- Step: Task 7 — added `_cleanup_work_worktree(cfg, pbi_id)` in `loop.py` and wired it into the three terminal-outcome sites in `_run_ralph`: max-attempts blocked (after `shutil.move` to `.ralph/blocked/`), `pr_created` (after `move_current_to_pending_pr`), and `stuck` (after `handle_stuck` returns a non-None outcome). Imported `remove_worktree` from `ralph_executor.worktree`. Queue worktree is intentionally NOT removed — it persists across PBIs.
- Design notes:
  - Helper is a no-op when `cfg.use_worktrees=False` so legacy single-checkout iterations are untouched.
  - Helper swallows removal exceptions with `log.warning(..., exc_info=True)` rather than raising — an orphan work tree is recoverable via `git worktree prune`, but raising here would obscure the real terminal outcome the iteration is reporting (and the event log entry has already been appended). Memory id `0dda832cbec346c29e5e81e3cb2113f9` (Windows CWD-pinning) does not apply: the executor's cwd is the primary checkout, not the work worktree being removed.
  - Feature branch ``ralph/<PBI-ID>`` is preserved by ``git worktree remove`` (it operates on the worktree dir + git's metadata, not the ref). That matters for `pr_created` because pending-pr PBIs still need the branch ref to point the open PR at.
  - Did NOT cleanup on `partial`/`error` — those outcomes leave the PBI in `current/` for the next iteration, which expects to find the work worktree already materialised. `_claim_pbi_worktree`'s `ensure_worktree` would re-create it on re-claim, but only if the PBI re-enters via the inbox path, not after a `partial` outcome.
  - No new tests this iteration — Task 9 owns the dedicated `test_terminal_outcome_removes_work_tree` coverage. Existing `test_iterate_once_routes_pr_created`, `test_iterate_once_routes_stuck`, and `test_max_attempts_moves_pbi_to_blocked` all use `use_worktrees=False` via `cfg_for_repo`, so the new code path is opt-out for them.
- Tests: full `uv run --no-sync pytest` green (454 passed, 2 skipped — opt-in prompt smoke). `ruff check`, `ruff format --check`, `mypy ralph_executor scripts skills tests` all clean.
- Notes: switched to `ralph/STAGE-B-PLAN-20a-worktree` to land the code commit, then back to ralph-queue for HISTORY/PLAN edits (the executor's `_persist_iteration_writes` mirrors them onto ralph-queue). Commit: `4aef065 feat(loop): clean up per-PBI work tree on terminal outcomes` on `ralph/STAGE-B-PLAN-20a-worktree`.

## Iteration 8 — 2026-05-27T21:00:00+00:00

- Step: Task 8 — added `.ralph-work/` to root `.gitignore` on the feature branch (`ralph/STAGE-B-PLAN-20a-worktree`), placed under the existing "Ralph runtime + tooling" group next to `.ralph/`, `.worktrees/`, `.superpowers/`.
- Design notes:
  - Only edited the feature branch's `.gitignore`. The `ralph-queue` branch's `.gitignore` deliberately diverges (it tracks `.ralph/` because queue state is the whole point of that branch) and PROMPT.md's standing instructions forbid touching ralph-queue from code work. If the operator's primary checkout ever sits on ralph-queue, `.ralph-work/` may show up untracked there until a follow-up addresses queue-branch gitignore drift — out of scope for this PBI.
  - No code or test changes; pure gitignore. Skipped the full pytest run since no Python source touched, but did run `ruff check .` and `ruff format --check .` — both clean. (The full suite landed clean in iteration 7 and nothing since then could have regressed it.)
- Tests: `ruff check .` clean, `ruff format --check .` clean (`72 files already formatted`). Full pytest not re-run — no Python touched.
- Notes: switched to `ralph/STAGE-B-PLAN-20a-worktree` to land the code commit, then back to ralph-queue for HISTORY/PLAN edits. Commit: `5c21fb0 chore(gitignore): exclude .ralph-work/` on `ralph/STAGE-B-PLAN-20a-worktree`.
