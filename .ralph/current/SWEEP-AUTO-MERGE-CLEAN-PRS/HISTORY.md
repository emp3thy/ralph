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
