<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-27T18:30:00+00:00

- Step: fix `pr_state.fetch` to pass `--repo <repo_name> --pr-id <pr_id>` to skill scripts (matches argparse `required=True` signature in `skills/pr-github/scripts/show.py` and `read_threads.py`). Threaded `repo_name` through `fetch`'s signature; caller `runner._process_pbi` resolves `repo_name=ctx.repo_name or ctx.queue_root.parent.name` (same fallback as `reconcile._resolve_repo_name`). Updated the `fake_ado_pr_skill` shim in `tests/executor/sweep/conftest.py` to read `--pr-id` instead of `argv[1]`. Added regression test `test_fetch_passes_repo_and_pr_id_flags` that captures argv to disk and asserts both flags + values.
- Tests: 626 passed, 2 skipped (full suite); `tests/executor/sweep/` 29 passed.
- Lint: `uv run ruff check` clean; `uv run ruff format --check` clean.
- Types: `uv run mypy ralph_executor scripts skills tests` clean (101 files).
- Notes: `docs/INVESTIGATE.md` absent in the code worktree; not needed for this bug — root cause was already pinpointed in BUG.md (commit 87fcbac landed sweep-side cycle-detector events without updating `pr_state.py` argv shape).

Root cause: `ralph_executor/sweep/pr_state.py::fetch` invoked `show.py` and `read_threads.py` with a single positional `pr_id`, but the skill scripts require `--repo` and `--pr-id` flags (argparse `required=True`), so every sweep tick on a `pending-pr/` entry raised `AdoSkillError: show.py exited with exit code 2`.
Fix: pass `--repo <repo_name> --pr-id <pr_id>` to both skill scripts; thread `repo_name` from `SweepContext` via the same `ctx.repo_name or ctx.queue_root.parent.name` fallback `sweep/reconcile.py::_resolve_repo_name` uses.

## Iteration 1 — 2026-05-27T18:35:00+00:00 — PR created

- PR: #37
- URL: https://github.com/emp3thy/ralph/pull/37
- Branch: ralph/BUG-sweep-show-args-flags
- Title: BUG-sweep-show-args-flags: sweep PR-state wrapper must pass --repo + --pr-id flags
