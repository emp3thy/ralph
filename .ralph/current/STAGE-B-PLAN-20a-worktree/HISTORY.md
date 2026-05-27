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
