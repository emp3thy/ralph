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
