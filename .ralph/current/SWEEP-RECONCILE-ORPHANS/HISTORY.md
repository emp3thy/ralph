<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-27T00:00:00+00:00

- Step Task 1: appended failing tests for `lookup_by_branch` sub-op to `tests/skills/test_pr_github.py`; added `import os` and `import subprocess` to existing imports
- Tests: 8 FAIL + 1 unintended-pass for `test_lookup_by_branch_missing_branch_arg_exits_2` (Python's `can't open file` exits 2 which coincidentally matches the assertion); will become a meaningful pass once `lookup_by_branch.py` lands in Task 2
- Notes: branch `ralph/SWEEP-RECONCILE-ORPHANS` checked out; HISTORY.md staged on feature branch via `git checkout ralph-queue -- .ralph/current/SWEEP-RECONCILE-ORPHANS/HISTORY.md`. Next iteration: Task 2 — implement `skills/pr-github/scripts/lookup_by_branch.py`.

## Iteration 2 — 2026-05-27T09:00:00+00:00

- Step Task 2: implemented `skills/pr-github/scripts/lookup_by_branch.py` per plan Steps 2.1–2.4 (`_build_parser`, `_map_pr_state`, `_lookup_pr`, `_lookup_branch`, `main`). Typed `client: GitHubClient` instead of `Any` (mypy --strict clean).
- Plan-deviation: rewrote Task 1's 9 `lookup_by_branch` tests from subprocess-based to module-load pattern. Reason: `responses` library patches `requests` adapters in-process only — a subprocess Python interpreter never sees the mocks and hits real network (DNS fails in test env). The rewrite mirrors the rest of `test_pr_github.py` (`_load_module` + `module.main(argv)` + `capsys`); env via existing `env` fixture. Removed now-unused `import os` and `import subprocess`.
- Tests: 9/9 `lookup_by_branch` PASS; full `tests/skills/test_pr_github.py` 56/56 PASS. `ruff check` + `ruff format --check` + `mypy --strict` all green on touched files.
- Notes: Task 1 boxes were already ticked in iteration 1's plan view; Task 2 boxes (Step 2.1–2.7) ticked this iteration. Next iteration: Task 3 — add `ReconcileAction`/`ReconcileReport` to `sweep/types.py` and write failing reconcile tests.

## Iteration 3 — 2026-05-27T10:00:00+00:00

- Step Task 3: appended `ReconcileAction` (StrEnum) + `ReconcileReport` (frozen dataclass) to `ralph_executor/sweep/types.py`; added `from collections.abc import Mapping` to types.py imports. Created `tests/executor/sweep/test_reconcile.py` with 13 tests (10 `reconcile_orphan` cases incl. dry-run + argv check; 3 `reconcile_all` cases — iteration, per-PBI isolation, skip-dirs-with-PR-LINK).
- Plan-deviation: substituted `Iterable` → `Iterator` (`iter([...])` returns Iterator, not Iterable — strict typing). Used `datetime.now(tz=UTC)` from `from datetime import UTC, datetime, timedelta` instead of plan's `__import__('datetime')` inline gymnastics. Used queue path `tmp_path / "ralph" / ".ralph"` rather than `tmp_path / ".ralph"` so `_resolve_repo_name(ctx)` (which derives repo from `queue_root.parent.name`) returns the literal "ralph" expected by Task 7's smoke run; matches the production layout.
- Tests: collection FAILS as expected — `ModuleNotFoundError: No module named 'ralph_executor.sweep.reconcile'` (Task 4 will land that module). `mypy --strict ralph_executor/sweep/types.py` green. `ruff check` + `ruff format --check` clean on touched files.
- Notes: Task 3 boxes (Step 3.1–3.5) ticked. Next iteration: Task 4 — implement `ralph_executor/sweep/reconcile.py` (module skeleton, `_invoke_lookup`, `reconcile_orphan`, `reconcile_all`).
