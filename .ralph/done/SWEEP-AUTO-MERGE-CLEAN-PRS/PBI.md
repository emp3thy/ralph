---
id: SWEEP-AUTO-MERGE-CLEAN-PRS
type: feature
status: pending-pr
severity: normal
attempts: 0
created_at: 2026-05-27T00:04:00+00:00
updated_at: 2026-05-27T21:50:13+00:00
depends_on: []
target_repo: https://github.com/emp3thy/ralph
---

# Sweep auto-merges clean PRs (gated by TOML flag)

Sweep optionally auto-merges PRs that GitHub reports as `mergeable_state == "clean"`, gated by a new TOML config flag `auto_merge_clean_prs` (default `false`).

Adds `merge_pr` sub-op to `pr-github` (new exit code 4 for race / refused-by-host) and exposes raw `mergeable_state` from `show.py`. `PrSnapshot.merge_state: str` carries the raw GitHub value. `Action.MERGE_PR` enum value triggers the subprocess merge from sweep's act path.

Predicate: `cfg.auto_merge_clean_prs AND pr.merge_state == "clean"`. GitHub's `mergeable_state == "clean"` is authoritative — captures CI green + required approvals + no conflicts + branch up-to-date in one bit. Race handling: attempt merge, treat 405/409 as not-ready, log INFO and retry next iter.

Originally specced with `depends_on: ["SWEEP-RECONCILE-ORPHANS"]` to sequence the `pr-github/SKILL.md` edits. SWEEP-RECONCILE-ORPHANS is now done (merged), so `depends_on: []` is the actual current state.

Spec: `docs/superpowers/specs/2026-05-27-sweep-auto-merge-clean-prs-design.md`
Plan: `docs/superpowers/plans/2026-05-27-sweep-auto-merge-clean-prs-plan.md`

See `PLAN.md` for the implementation plan pointer.

## Acceptance criteria

- `skills/pr-github/scripts/show.py` emits raw `mergeable_state` field alongside existing `merge_status`
- `skills/pr-github/scripts/merge_pr.py` exists (7th sub-op) with exit codes 0/2/3/4
- `SKILL.md` documents `merge_pr` operation
- `ExecutorConfig.auto_merge_clean_prs: bool = False`, layered defaults < TOML < env
- `PrSnapshot.merge_state: str` carries GitHub's raw value (parsed by `pr_state.py`)
- `Action.MERGE_PR` enum value exists on `sweep/types.py`
- `decide_action` returns `MERGE_PR` only when `cfg.auto_merge_clean_prs AND pr.merge_state == "clean"`
- Act path subprocess-invokes `merge_pr` skill; on exit 0 moves PBI to `done/` with HISTORY.md reason `"PR auto-merged by sweep"`; on exit 4 leaves PBI in `pending-pr/` with INFO log; on exit 3 logs WARNING; on exit 2 raises `_SweepPbiError`
- `CONFIG_TOML_STUB` documents the new key (default-false note)
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/SWEEP-AUTO-MERGE-CLEAN-PRS`
