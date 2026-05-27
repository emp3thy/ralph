<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-27T20:59:17+00:00

- Task 0: Authored `docs/superpowers/specs/2026-05-27-sweep-auto-merge-clean-prs-design.md` + `docs/superpowers/plans/2026-05-27-sweep-auto-merge-clean-prs-plan.md` (PBI referenced them but they did not exist).
- Task 0: Rewrote `PLAN.md` as a 10-task checklist pointer.
- Task 1: Added raw `mergeable_state` passthrough to `skills/pr-github/scripts/show.py` (`null` when GitHub's async check hasn't resolved). Updated module docstring.
- Task 1: Extended `test_show_happy_path` to assert `mergeable_state == "clean"`. Extended the parametrized `test_show_merge_status_mapping` to also assert raw passthrough. Added new `test_show_mergeable_state_null_passthrough`.
- Tests: `uv run pytest tests/skills/test_pr_github.py -k show` → 14 passed.
- Lint: `ruff check` clean. `ruff format` applied (one minor reflow inside `output` dict). `mypy skills/pr-github/scripts/show.py` → no issues.
- Notes: pyright "unreachable code" diagnostic on `show.py:209` is pre-existing (annotation-driven narrowing of `pr: dict[str, Any]`). Not in scope.

## Iteration 2 — 2026-05-27T21:30:00+00:00

- Task 2: Added `put_rest` + `RaceError` + `handle_race_error` to `skills/pr-github/scripts/_common.py`. `_read_rest_response` now raises `RaceError` on 405/409 (additive — grep confirmed no existing call site relied on those codes). Updated module docstring + `__all__`.
- Task 2: Created `skills/pr-github/scripts/merge_pr.py` (7th sub-op). argparse choices for `--merge-method` (merge|squash|rebase, default squash). `PUT /repos/{owner}/{repo}/pulls/{n}/merge`. Exit chain: `FatalError → 2`, `RaceError → 4`, `HttpError → 3`.
- Task 2: Added `merge_pr_module` fixture + 9 test cases in `tests/skills/test_pr_github.py` (happy path, optional commit fields, 405 race, 409 race, 422 error, 500 error, bad merge method via argparse, missing env, malformed pr-id).
- Tests: `uv run pytest tests/skills/test_pr_github.py -k merge_pr` → 9 passed. Full `tests/skills/` → 194 passed.
- Lint: `ruff check .` clean (only pre-existing noqa-format warnings in `ralph-doctor`). `ruff format` applied to `test_pr_github.py`. `mypy skills` → no issues.

## Iteration 3 — 2026-05-27T22:00:00+00:00

- Task 3: Updated `skills/pr-github/SKILL.md` frontmatter description (`Five operations` → `Six operations`, added `merge_pr` + sweep-auto-merge use case). Prose `five Python entry scripts` → `six`. `Same pattern for all five entry scripts` → `six`.
- Task 3: Added a `### merge_pr — merge a clean PR` section after the `show` section (usage example, flag table, return JSON shape, exit codes incl. exit 4, REST endpoint + docs link, note that the `mergeable_state` predicate lives in the caller).
- Task 3: Appended a paragraph to the `show` section documenting the new raw `mergeable_state` field exposed by Task 1 (verbatim string or `null`; sweep keys off `"clean"`).
- Task 3: Added a row to the "Exit codes (every operation)" table for exit 4 ("Race / refused-by-host… Only emitted by `merge_pr`").
- Tests: `uv run pytest tests/skills/test_pr_github.py -q` → 67 passed. (Markdown-only change; no test references SKILL.md text.)
- Lint: `uv run ruff check .` → All checks passed.
- Notes: Used op name `merge_pr` (underscore) to match plan + PBI wording; sibling ops use hyphens externally but plan/spec explicitly say `merge_pr`. The `pr-ado` SKILL.md is unchanged — this PBI is GitHub-only (per `target_repo` + spec); ADO mirror is a follow-up.

## Iteration 4 — 2026-05-27T22:30:00+00:00

- Task 4: Added `DEFAULT_AUTO_MERGE_CLEAN_PRS = False` constant + `ExecutorConfig.auto_merge_clean_prs: bool = DEFAULT_AUTO_MERGE_CLEAN_PRS` field. Added `"auto_merge_clean_prs"` to `_TOML_KNOWN_KEYS`. Added `_resolve_bool` call after `use_worktrees`. Threaded through the `ExecutorConfig(...)` constructor at the bottom of `load_config`.
- Task 4: Extended `CONFIG_TOML_STUB` in `setup_cmds.py` with a commented `auto_merge_clean_prs = false` block, documenting the opt-in semantics + env override.
- Task 4: Added `RALPH_AUTO_MERGE_CLEAN_PRS` to the env-cleanup fixtures in both `tests/executor/test_config.py::env_minimal` and `tests/executor/test_config_toml.py::clean_env` so no host env leak influences other tests.
- Task 4: Three new env-level cases in `test_config.py` (`test_load_config_auto_merge_clean_prs_default_false`, `_env_true`, `_env_invalid`). Four TOML-level cases in `test_config_toml.py` (`_default_false`, `_toml_true`, `_env_wins_over_toml`, `_toml_wrong_type_raises`).
- Tests: `uv run pytest tests/executor/test_config.py tests/executor/test_config_toml.py -q` → 55 passed.
- Lint: `uv run ruff check` on changed files → All checks passed. `uv run ruff format --check` → 4 files already formatted. `uv run mypy ralph_executor/config.py ralph_executor/setup_cmds.py` → no issues.
- Notes: Pyright "function ... is not accessed" warnings on the new field's assignment are spurious (constructor uses it); same for pyright noise about pytest fixture params — pre-existing pattern across the file.

## Iteration 5 — 2026-05-27T23:00:00+00:00

- Task 5: Added `merge_state: str = ""` field to `PrSnapshot` (after `url`, default-valued so existing constructors in `test_decide_action.py` / `test_feedback_pbi.py` keep compiling).
- Task 5: Threaded `merge_state=str(show_dict.get("mergeable_state") or "")` into `pr_state.fetch`'s `PrSnapshot(...)` call. `or ""` collapses `None` (async-pending) AND missing key into the empty sentinel.
- Task 5: Three new tests in `tests/executor/sweep/test_pr_state.py` — clean carries verbatim, `null` → "", missing → "". Kept `SHOW_PAYLOAD_MERGED` constant unchanged (avoids semantically wrong "merged completed PR with clean mergeable_state" in unrelated tests); per-test overrides instead.
- Tests: `uv run pytest tests/executor/sweep/test_pr_state.py -q` → 9 passed. Full `tests/executor/sweep/` → 70 passed (no regressions).
- Lint: `ruff check` clean on touched files. `ruff format` reformatted `test_pr_state.py` (line-fit only). `mypy ralph_executor/sweep/types.py ralph_executor/sweep/pr_state.py` → no issues.

## Iteration 6 — 2026-05-27T23:30:00+00:00

- Task 6: Added `Action.MERGE_PR = "merge-pr"` to the `Action` StrEnum in `ralph_executor/sweep/types.py` (placed before `NOOP`).
- Task 6: Added `auto_merge_clean_prs: bool = False` field to `SweepConfig` in `ralph_executor/sweep/runner.py` (after `now`, default-valued so existing constructors stay compiling).
- Task 6: Added predicate to `decide_action`: `if config.auto_merge_clean_prs and pr.merge_state == "clean": return Decision(action=Action.MERGE_PR, reason="auto-merging clean PR")`. Positioned AFTER the new-comments branch (so active human comments still preempt) and BEFORE the stale branch (so we don't ping a reviewer on a PR we are about to merge).
- Task 6: Extended `_snapshot` helper in `tests/executor/sweep/test_decide_action.py` with `merge_state: str = ""` kwarg threaded through `PrSnapshot(...)`. Added four tests: `test_flag_off_clean_is_noop`, `test_flag_on_clean_yields_merge_pr`, `test_flag_on_dirty_is_not_merge_pr`, `test_new_comments_preempt_merge_pr`.
- Tests: `uv run pytest tests/executor/sweep/test_decide_action.py -q` → 16 passed. Full `tests/executor/sweep/` → 74 passed (no regressions; +4 new).
- Lint: `uv run ruff check` on touched files → All checks passed. `uv run ruff format --check` → 3 files already formatted. `uv run mypy ralph_executor/sweep/runner.py ralph_executor/sweep/types.py` → no issues.
- Notes: `MERGE_PR` placed before `NOOP` for visual grouping (active dispatch values cluster, then NOOP terminator). String value `"merge-pr"` matches the hyphenated convention of other Action members. Dispatch wiring (Task 7) will add the `elif a is Action.MERGE_PR` branch in `_dispatch`; this iteration leaves the `# pragma: no cover` defensive `else` to absorb the new enum value without dispatching, which is acceptable for one iteration boundary.

## Iteration 7 — 2026-05-28T00:00:00+00:00

- Task 7: Added `import subprocess` + `import sys` at top of `ralph_executor/sweep/runner.py`.
- Task 7: Added `_invoke_merge_pr(*, pr_id, ctx) -> int` helper. Subprocess-invokes `ado_pr_scripts_path / "merge_pr.py"` with `--repo` (from `ctx.repo_name or ctx.queue_root.parent.name`, mirroring the `pr_state.fetch` repo resolution) + `--pr-id`. Missing script → `_SweepPbiError`. Exit 2 → `_SweepPbiError` (validation; never a retry condition). Every other exit code returned verbatim for the dispatcher to interpret.
- Task 7: Added `_dispatch_merge_pr(*, pbi_dir, snapshot, ctx)` that maps exit code → action: 0 → `_move_with_history` to `done/` with reason `"PR auto-merged by sweep"` + `_emit_pr_merged_and_pbi_closed`; 4 → `log.info` "race / not-ready; retry next iter"; 3 → `log.warning` "GitHub error; retry next iter"; anything else → `_SweepPbiError`. Both helpers placed just before `_read_original_summary`.
- Task 7: Added `elif a is Action.MERGE_PR: _dispatch_merge_pr(...)` branch in `_dispatch`, between `PING_REVIEWER` and `NOOP`. Replaces the `# pragma: no cover` defensive `else` swallow that Task 6 left in place — the defensive `else` is preserved for any *future* enum value, but `MERGE_PR` is now explicitly routed.
- Tests: `uv run pytest tests/executor/sweep/test_runner.py -q` → 23 passed (existing tests unaffected; runner-side MERGE_PR coverage lands in Task 9 alongside the conftest shim). Full `tests/executor/sweep/` → 74 passed.
- Lint: `uv run ruff check ralph_executor/sweep/runner.py` → all checks passed. `uv run ruff format --check` → already formatted. `uv run mypy ralph_executor/sweep/runner.py` → no issues.
- Notes: Pyright reports a spurious "_dispatch_merge_pr is not defined" diagnostic at the call site because the helper is defined later in the module. Python resolves the name at call time (function bodies are not eagerly bound), and mypy agrees there is no issue. Placement matches the file's existing convention (helpers below `_dispatch`).
