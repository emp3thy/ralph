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

## Iteration 4 — 2026-05-27T11:00:00+00:00

- Step Task 4: implemented `ralph_executor/sweep/reconcile.py` per plan Steps 4.1–4.4. Module exposes `ReconcileError`, `reconcile_orphan`, `reconcile_all`; internal helpers `_invoke_lookup`, `_resolve_repo_name`, `_move_dir`, `_write_pr_link`, `_now_iso`. Subprocess invocation routes argv with `--branch`, `--repo`, `--include-branch-check` per the test contract. State map: `merged` → done, `closed` → blocked, `open` → keep pending (writes PR-LINK.md), `None` + branch_exists True → blocked, `None` + branch_exists False → inbox, exit-3 → KEEP_API_ERROR.
- Tests: 13/13 `tests/executor/sweep/test_reconcile.py` PASS. `ruff format` auto-applied one line-length reflow; `ruff check` + `mypy --strict` green on touched file.
- Notes: Task 4 boxes (Step 4.1–4.7) ticked. Top-level import of `SweepContext` from `runner` is fine on its own — the cycle only appears when `runner.py` imports `reconcile` back, which is Task 5's local-import pattern. Next iteration: Task 5 — wire `reconcile_orphan` into `sweep/runner.py`'s `run()` loop and update any legacy "PR-LINK.md is missing" sweep-test assertions.

## Iteration 5 — 2026-05-27T12:00:00+00:00

- Step Task 5: wired `reconcile_orphan` into `ralph_executor/sweep/runner.py`'s `run()` loop. Per-PBI loop now branches on `PR-LINK.md is_file()`: missing → `reconcile_orphan` (logs API errors, surfaces `ReconcileError` into `errors`); present → existing `_process_pbi`. Local import inside `run()` per plan Step 5.2.
- Plan-deviation: split the local import — `ReconcileAction` comes from `ralph_executor.sweep.types` (not re-exported through `reconcile.py`) so `mypy --strict` is happy without needing `__all__` on the reconcile module.
- Step 5.4: rewrote `test_missing_pr_link_md_is_recorded_as_error` → `test_run_reconciles_orphan_when_pr_link_missing`. Monkeypatches `ralph_executor.sweep.reconcile.reconcile_orphan` to return `MOVED_TO_INBOX`; asserts zero sweep errors and that the orphan id is NOT in `result.actions`. Uses existing `queue_root` + `fake_ado_pr_skill` fixtures from conftest (cleaner than rolling new ones).
- Tests: 51/51 PASS in `tests/executor/sweep/`. `ruff check` + `ruff format --check` + `mypy --strict` green on `ralph_executor/sweep/runner.py` and `tests/executor/sweep/test_runner.py`.
- Notes: Task 5 boxes (Step 5.1–5.7) ticked in canonical plan. Next iteration: Task 6 — add `ralph-executor reconcile [--dry-run]` CLI subcommand and `tests/executor/test_cli_reconcile.py`.

## Iteration 6 — 2026-05-27T13:00:00+00:00

- Step Task 6: added `ralph-executor reconcile [--dry-run]` CLI subcommand. New subparser in `_build_parser` (own `--repo`/`--workspace` mutually-exclusive group + `--dry-run`), dispatch from `main()`, handler `_cmd_reconcile`, formatter `_print_reconcile_report`. Imports at top: `reconcile_all`, `SweepConfig`, `SweepContext`, `ReconcileReport`, `_pr_skill_scripts_path` (reused from `loop.py` rather than duplicated).
- Plan-deviation: plan's `_cmd_reconcile` referenced `cfg.bot_author_email`, `cfg.stale_days`, and `prepare_host_environment(cfg)` — none of which exist on the real `ExecutorConfig` / `host_select` API. Substituted: read `RALPH_ADO_AUTHOR_EMAIL` from env (fallback `reconcile@ralph.local`), hardcode `stale_threshold=timedelta(days=3)` (matches loop default), resolve scripts dir via `_pr_skill_scripts_path(cfg)` and exit 2 if absent. Plan was written against a stale snapshot of the config layer.
- Tests: `tests/executor/test_cli_reconcile.py` — 4 tests, all PASS (calls-reconcile-all, dry-run-flag-flows-through, no-orphans-message, missing-scripts-dir-exits-2). Added the no-orphans + missing-scripts cases on top of the plan's two original tests for branch coverage of the new exit-2 path and the empty-report message.
- Full `tests/executor/` suite: 270 PASS. `ruff check` + `ruff format --check` + `mypy --strict` green on `ralph_executor/cli.py` and `tests/executor/test_cli_reconcile.py`.
- Notes: Task 6 boxes (Step 6.1–6.7) ticked in canonical plan. Next iteration: Task 7 — repo-wide pytest/ruff/mypy sweep, then smoke `ralph-executor reconcile --dry-run --repo .` against the 8 real orphans.

## Iteration 7 — 2026-05-27T14:00:00+00:00 — PR created

- Step Task 7: repo-wide gates green. `uv run pytest tests/` → 515 passed, 2 skipped (opt-in prompt smoke). `uv run ruff check ralph_executor/ skills/ tests/` flagged unsorted imports in `tests/executor/sweep/test_reconcile.py`; ran `--fix` (one-line reordering) and committed as `style(tests): sort imports in test_reconcile.py`. `uv run ruff format --check` clean (84 files). `uv run mypy --strict ralph_executor/` clean (26 source files).
- Step 7.1 grep for residual `"PR-LINK.md is missing"` / `"cannot determine PR id"` in `tests/`: no matches.
- Step 7.4–7.6 (smoke against 8 real orphans + commit moved-orphan state): SKIPPED on this branch. `.ralph/pending-pr/<PBI-ID>/` orphan dirs only exist on the `ralph-queue` branch (queue state); on `ralph/SWEEP-RECONCILE-ORPHANS` (code branch) `.ralph/` contains only `current/` and `state/`. Running `ralph-executor reconcile --dry-run --repo .` here returns "no orphans found in pending-pr/". PROMPT.md explicitly forbids touching the `ralph-queue` branch from feature work, so per-step 7.7's `git add .ralph/` commit would also violate the queue/code branch split. The smoke is deferred to operator post-merge: after this PR lands, an operator runs `uv run ralph-executor reconcile --dry-run --repo <ralph-queue-worktree>` and (if clean) the real reconcile on `ralph-queue` to clear the 8 orphans.
- Tests: see Task 7.2 above.
- Notes: All Task 7 boxes ticked in canonical plan except the orphan-smoke (deferred). PR being opened against `main` from `ralph/SWEEP-RECONCILE-ORPHANS`.
