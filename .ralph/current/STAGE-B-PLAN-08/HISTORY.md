<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-27T00:00:00+00:00

- Task 1: scaffolded `ralph_executor/sweep/` package with `__init__.py` + `types.py`, and the parallel `tests/executor/sweep/__init__.py`.
- Preconditions verified: `uv run --no-sync pytest tests/skills/ tests/executor/ -v` → 234 passed (Plans 5 and 7 merged; `test_iterate_once_invokes_sweep_stub_when_current_empty` already present).
- `RALPH_ADO_AUTHOR_EMAIL` row already present in `docs/superpowers/plans/2026-05-24-00-orchestrator.md` line 88; no doc edit required.
- Plan deviations (intentional):
  - Plan's Task 1 step 4 has `__init__.py` import `from ralph_executor.sweep.runner import SweepResult, run`, but `runner.py` is not created until Task 3 — mypy would fail. Left `__init__.py` with docstring only; public-surface re-exports will be added when runner lands in Task 3.
  - Plan's Task 1 step 5 declares `class Action(str, Enum)`; ruff `UP042` rejects mixed inheritance on py3.12. Used `class Action(StrEnum)` instead (semantically identical, lint-clean).
  - Host is GitHub (`skills/pr-github/`), not ADO. Task 1 has no skill binding so no impact this iteration; Task 4 will need to invoke the `pr-github` scripts in place of the plan's `ado-pr` references.
- Toolchain: `ruff check` clean, `ruff format --check` clean, `mypy ralph_executor` clean (21 source files).
- Tests: no new tests added this iteration (Task 1 is scaffold only).
- Notes: skill name mismatch (plan says `ado-pr`, repo has `pr-github`) is load-bearing for Tasks 4 and 6 — keep an eye on it.

## Iteration 2 — 2026-05-27T01:00:00+00:00

- Task 2: wrote `tests/executor/sweep/test_decide_action.py` (12 tests covering every row of the sweep table + SweepConfig validation). Verified red step: collection failed with `ModuleNotFoundError: No module named 'ralph_executor.sweep.runner'`.
- Task 3: created `ralph_executor/sweep/runner.py` with `SweepConfig`, `PbiActionRecord`, `SweepResult`, `decide_action` (pure-function core implementing the spec table), `_new_active_human_comments` helper, and stubbed `run` / `_utc_now` / `_pbi_dir_iter` for Task 6. Populated `ralph_executor/sweep/__init__.py` with the public re-exports per the canonical plan.
- Plan deviations (intentional):
  - Plan literally writes `from datetime import datetime, timedelta, timezone` + `timezone.utc`; ruff `UP017` rejects under py3.12. Used `from datetime import UTC, ...` + `tz=UTC` instead (semantically identical).
- Tests: `uv run --no-sync pytest tests/executor/sweep/test_decide_action.py -v` → 12 passed (green). Full suite `uv run --no-sync pytest` → 408 passed, 2 skipped (opt-in prompt smoke).
- Toolchain: `ruff check ralph_executor/sweep tests/executor/sweep` clean, `ruff format --check` clean, `mypy ralph_executor` clean (22 source files).
- Notes: Task 4 next. Skill mismatch (`ado-pr` vs `pr-github`) becomes load-bearing. Task 4 invokes `show.py` + `read_threads.py` under `skills/<skill>/scripts/`; the binding belongs in the runner's call site (Task 6), so `pr_state.fetch` should accept the scripts directory as a parameter and the loop driver picks the right one.

## Iteration 3 — 2026-05-27T02:00:00+00:00

- Task 4: created `ralph_executor/sweep/pr_state.py` (host-agnostic subprocess wrapper around `show.py` + `read_threads.py` under any PR-skill scripts dir). Wrote `tests/executor/sweep/test_pr_state.py` (5 tests covering merged-no-threads, active-with-threads, last_activity_at fallback to comments, non-zero exit, non-JSON output). Red step confirmed: collection failed with `ModuleNotFoundError: No module named 'ralph_executor.sweep.pr_state'`.
- Plan deviations (intentional):
  - Plan literally writes `from datetime import datetime, timezone` + `timezone.utc`; ruff `UP017` rejects under py3.12. Used `from datetime import UTC, ...` + `tzinfo=UTC` instead.
  - Added a defensive non-object JSON check after `json.loads` so the subprocess output can be safely narrowed to `dict | list` for downstream parsing (mypy + safety). The plan's `fetch` already assumes the returned shape; the extra raise simply turns a future malformed payload from a silent `AttributeError` into a clear `AdoSkillError`.
  - Plan's tests reference an unused `os` import in two places; removed both — ruff `F401` would otherwise fail.
  - Docstrings and comments now describe the PR skill generically (`show.py` / `read_threads.py`) so the binding to either `ado-pr` or `pr-github` is decided by the loop driver in Task 6.
- Tests: `uv run --no-sync pytest tests/executor/sweep/test_pr_state.py -v` → 5 passed.
- Toolchain: `ruff check` clean, `ruff format` (1 file reformatted then clean), `mypy ralph_executor` clean (23 source files).
- Notes: Task 5 next (sidecar state + feedback PBI renderer). No host-binding work yet; both are pure-Python file/template logic.

## Iteration 4 — 2026-05-27T03:00:00+00:00

- Task 5: created `ralph_executor/sweep/state.py` (`SweepSidecar` dataclass + `load_sidecar` / `write_sidecar` / `merge_seen_comment_ids`) and `ralph_executor/sweep/feedback_pbi.py` (`render` returning a `FeedbackPbiBundle`: FEEDBACK.md with verbatim boilerplate, PR-LINK.md, ORIGINAL.md, HISTORY.md). Wrote `tests/executor/sweep/test_state.py` (5 tests) and `tests/executor/sweep/test_feedback_pbi.py` (4 tests). Red steps confirmed twice: `ModuleNotFoundError` for each new module before the implementation landed.
- Plan deviations (intentional):
  - Plan literally writes `from datetime import datetime, timezone` + `timezone.utc`; ruff `UP017` rejects under py3.12. Used `from datetime import UTC, ...` instead (semantically identical, same wire format).
  - Added a defensive `isinstance(raw, dict)` check in `load_sidecar` after `json.loads` so a JSON file containing a non-object (e.g. a bare array, a string) is treated like a corrupt sidecar instead of crashing on `.get`.
  - FEEDBACK boilerplate refers generically to "the PR skill" rather than `ado-pr` (host-agnostic; matches Iteration 3's binding strategy).
- Tests: `uv run --no-sync pytest tests/executor/sweep/test_state.py tests/executor/sweep/test_feedback_pbi.py -v` → 9 passed.
- Toolchain: `ruff check ralph_executor/sweep tests/executor/sweep` clean, `ruff format --check` clean (11 files), `mypy ralph_executor` clean (25 source files).
- Notes: Task 6 next — orchestration (`run`, `SweepContext`, conftest fixtures, runner tests). The host-skill binding (`pr-github` vs `ado-pr`) gets decided in Task 7 at the loop driver, not here; `pr_state.fetch` is already host-agnostic so Task 6 stays generic too.

## Iteration 5 — 2026-05-27T04:00:00+00:00

- Task 6: implemented sweep runner orchestration. Wrote `tests/executor/sweep/conftest.py` (fixtures `queue_root`, `make_pending_pbi`, `fake_ado_pr_skill` + module-level `register_pr` helper) and `tests/executor/sweep/test_runner.py` (12 tests: terminal moves, no-op, feedback PBI generation, round-2 incremental, sidecar dedup, Ralph-authored skip, stale ping, empty pending, missing PR-LINK error). Red step confirmed: `ImportError: cannot import name 'SweepContext'`. Filled in `ralph_executor/sweep/runner.py` with `SweepContext`, real `run`, `_process_pbi`, `_read_pr_id`/`_read_attempts`, `_dispatch`, `_move_with_history`, `_append_history`, `_emit_feedback_pbi`, `_read_original_summary`, removed Task 3 stubs (`_utc_now`, `_pbi_dir_iter`).
- Plan deviations (intentional):
  - Renamed plan's `ado-pr failure` substring to `PR skill failure` so the error text is host-agnostic (matches Iteration 3's binding strategy; user-visible string only — no behaviour change).
  - `SweepContext.ado_pr_scripts_path` kept the plan's name even though the staged skill is `pr-github`; renaming would propagate into Task 7 wiring and the Plan-7 `LoopContext`, which is out of scope for this iteration.
- Tests: `uv run --no-sync pytest tests/executor/sweep/ -v` → 38 passed (10 new from `test_runner.py` parametrised to 12 cases). Full suite `uv run --no-sync pytest` → 434 passed, 2 skipped.
- Toolchain: `ruff check ralph_executor tests` clean, `ruff format` reformatted runner.py + test_runner.py once, then `ruff format --check` clean (62 files); `mypy ralph_executor` clean (25 source files).
- Notes: Task 7 next — wire `run_sweep(ctx=...)` into `ralph_executor/loop.py`. Existing `loop.py` already has a `_run_sweep(cfg, source)` placeholder rather than the plan-7 snippet's `LoopContext.iterate_once` shape; the integration will adapt to the actual loop signature. Task 7 will also need to thread `ado_pr_scripts_path` through `ExecutorConfig` (or read it from `host_select`) and source `RALPH_ADO_AUTHOR_EMAIL` / `RALPH_STALE_DAYS` from env.
