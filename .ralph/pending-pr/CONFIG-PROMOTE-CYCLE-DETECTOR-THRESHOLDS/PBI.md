---
id: CONFIG-PROMOTE-CYCLE-DETECTOR-THRESHOLDS
type: feature
status: pending-pr
severity: normal
attempts: 0
created_at: 2026-05-27T23:20:00+00:00
updated_at: 2026-05-28T01:23:42+00:00
depends_on: []
target_repo: https://github.com/emp3thy/ralph
---

# Promote cycle-detector thresholds to TOML config

## Problem

The `same_file_thrashing` rule in `ralph_executor/safety/cycle_detector.py` uses hardcoded constants:

```python
SAME_FILE_WINDOW = timedelta(hours=24)
SAME_FILE_MIN_PRS = 6
```

The rule halted the executor on 2026-05-27T22:10:18Z because `ralph_executor/loop.py` was touched by 6 distinct PBIs in 24h (META-cycle-20260527T221018Z, false-positive acked by the operator). During bootstrap / high-velocity work, the central executor module legitimately receives many feature additions — 6 PRs/24h is a normal rate (we did roughly 40 PRs in one day), not a thrash signal.

Hardcoded thresholds mean each false positive requires a code change + PR + ack cycle. Promote them to TOML so operators can dial them per repo / per phase.

## What to do

Promote both thresholds to `ExecutorConfig` and the TOML config file (`.ralph.toml` or whatever the existing pattern uses — match `CONFIG-PROMOTE-SWEEP-KNOBS` and `CONFIG-PROMOTE-BASH-TIMEOUT` exactly).

Suggested config keys:
- `[safety.cycle_detector] same_file_min_prs = 6` (default — preserves current behavior)
- `[safety.cycle_detector] same_file_window_hours = 24` (default)

Update `evaluate_same_file_thrashing` (`ralph_executor/safety/cycle_detector.py:180`) to read the threshold + window from the passed `ExecutorConfig` (or whatever signature the cycle detector currently uses — check the existing rule plumbing first; the function currently takes `events, now` so adding a config argument may require a small refactor of the dispatcher in the same file).

## Acceptance criteria

- `ExecutorConfig` exposes `same_file_min_prs` (int) and `same_file_window_hours` (int or float).
- TOML loader reads both; defaults preserved (6 / 24).
- `evaluate_same_file_thrashing` uses the config values, not the module-level constants. Remove the module-level constants OR keep them as fallback defaults wired through `ExecutorConfig.__post_init__` — pick one and be consistent with the other CONFIG-PROMOTE PBIs.
- Existing tests for the rule keep passing; add at least one test that overrides the threshold via a config fixture and asserts trip / no-trip at the new boundary.
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/CONFIG-PROMOTE-CYCLE-DETECTOR-THRESHOLDS`

## Notes

- Out of scope: changing the default. The default stays 6 / 24h; this PBI only makes it tunable. Operators raise it manually for high-velocity sprints.
- Same-shape PBIs already merged: `CONFIG-PROMOTE-SWEEP-KNOBS` (#30), `CONFIG-PROMOTE-BASH-TIMEOUT`. Use them as templates for naming, TOML section choice, and test style.
