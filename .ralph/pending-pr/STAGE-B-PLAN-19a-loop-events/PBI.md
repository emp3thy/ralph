---
id: STAGE-B-PLAN-19a-loop-events
type: feature
status: pending-pr
severity: high
attempts: 0
created_at: 2026-05-26T07:30:00+00:00
updated_at: 2026-05-26T22:44:29+00:00
depends_on: []
---

# Emit loop-side cycle-detector events (PR_CREATED, PBI_OPENED, FILE_TOUCHED, SIGNATURE_OBSERVED)

`ralph_executor/safety/cycle_detector.py` defines 6 detector rules but production code only emits 2 of the 8 event types they consume. 4 of 6 detector rules can never fire. The wrong-but-passing convergence failure mode the detector exists to catch — `signature_recurrence`, `regression_cascade`, `same_file_thrashing`, `whack_a_mole` — is mechanically uncatchable.

This PBI emits the 4 events the loop driver can produce locally without sweep. The remaining 3 events (PR_MERGED, PR_GREEN_THEN_RED, PBI_CLOSED) are produced by the sweep observer and live in PBI 19b (depends_on Plan 08).

See `PLAN.md` for the full implementation plan.

## Acceptance criteria

- `PR_CREATED` emitted in `move_current_to_pending_pr` with payload `{pr_url, signature, files}`
- `PBI_OPENED` emitted in `move_inbox_to_current` with payload `{pbi_id}`
- `FILE_TOUCHED` emitted in `_persist_iteration_writes` after each iteration's commit with payload `{files: <git diff --name-only HEAD@{1} HEAD>}`
- `SIGNATURE_OBSERVED` emitted in `safety.stuck.handle_stuck` after reading STUCK.md, with payload `{signature: sha256(normalised_reason)[:16]}`
- All payloads match the schema the detectors consume (see `cycle_detector._signature`, `cycle_detector._files`)
- Regression tests: each emission point covered; each detector's wiring covered (detector observes the new event and trips when synthetic conditions met)
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/STAGE-B-PLAN-19a-loop-events`
