# Plan: Sweep auto-merges clean PRs

Canonical plan: `docs/superpowers/plans/2026-05-27-sweep-auto-merge-clean-prs-plan.md`.
This file mirrors the top-level task checkboxes so the executor loop can
track per-iteration progress.

- [x] Task 0 — Author spec + plan docs and PLAN.md pointer.
- [x] Task 1 — Expose `mergeable_state` from `show.py` + tests.
- [x] Task 2 — `merge_pr.py` skill script + tests (incl. `put_rest`, `RaceError`).
- [x] Task 3 — `SKILL.md` documents `merge_pr`.
- [x] Task 4 — `ExecutorConfig.auto_merge_clean_prs` (defaults < TOML < env) + tests.
- [ ] Task 5 — `PrSnapshot.merge_state` + `pr_state.fetch` parse + tests.
- [ ] Task 6 — `Action.MERGE_PR` + `decide_action` predicate + tests.
- [ ] Task 7 — Runner dispatch branch + `_invoke_merge_pr` helper.
- [ ] Task 8 — Wire flag through `loop._run_sweep`.
- [ ] Task 9 — Conftest shim + runner tests.
- [ ] Task 10 — Full-suite green (`uv run pytest`, `ruff check`, `ruff format --check`, `mypy`) + ship PR.
