<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-27T20:59:17+00:00

- Task 0: Authored `docs/superpowers/specs/2026-05-27-sweep-auto-merge-clean-prs-design.md` + `docs/superpowers/plans/2026-05-27-sweep-auto-merge-clean-prs-plan.md` (PBI referenced them but they did not exist).
- Task 0: Rewrote `PLAN.md` as a 10-task checklist pointer.
- Task 1: Added raw `mergeable_state` passthrough to `skills/pr-github/scripts/show.py` (`null` when GitHub's async check hasn't resolved). Updated module docstring.
- Task 1: Extended `test_show_happy_path` to assert `mergeable_state == "clean"`. Extended the parametrized `test_show_merge_status_mapping` to also assert raw passthrough. Added new `test_show_mergeable_state_null_passthrough`.
- Tests: `uv run pytest tests/skills/test_pr_github.py -k show` → 14 passed.
- Lint: `ruff check` clean. `ruff format` applied (one minor reflow inside `output` dict). `mypy skills/pr-github/scripts/show.py` → no issues.
- Notes: pyright "unreachable code" diagnostic on `show.py:209` is pre-existing (annotation-driven narrowing of `pr: dict[str, Any]`). Not in scope.
