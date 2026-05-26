---
id: STAGE-B-PLAN-19b-sweep-events
type: feature
status: inbox
severity: high
attempts: 0
created_at: 2026-05-26T07:30:00+00:00
updated_at: 2026-05-26T07:30:00+00:00
depends_on: ["STAGE-B-PLAN-08"]
---

# Emit sweep-side cycle-detector events (PR_MERGED, PR_GREEN_THEN_RED, PBI_CLOSED)

Sibling of PBI 19a. The sweep observer (introduced by Plan 08) is the natural place to detect PR state transitions across iterations. This PBI wires the three events that only sweep can produce because only sweep observes pending-pr → done transitions and post-creation CI state changes.

Once both this PBI and PBI 19a land, all 6 detector rules in `cycle_detector.py` have their inputs and can fire.

See `PLAN.md` for the implementation plan.

## Acceptance criteria

- `PR_MERGED` emitted when sweep detects a pending-pr PBI's PR has transitioned to `MERGED` state, payload `{pr_url, signature, files}` (same signature scheme as PBI 19a's PR_CREATED so signature_recurrence can match across pairs)
- `PR_GREEN_THEN_RED` emitted when sweep detects a PR whose required-checks were previously green now have at least one `bucket=="fail"`, payload `{pr_url, signature, files}`
- `PBI_CLOSED` emitted when sweep moves a PBI from pending-pr to done, payload `{pbi_id, signature}` (PR's signature, so signature_recurrence can match against later SIGNATURE_OBSERVED)
- Sweep state file (introduced by Plan 08) tracks last-known check state per PR; transition detection compares prior vs current
- Regression tests cover each emission point + the integration: `regression_cascade` trips when PR_MERGED then matching PR_GREEN_THEN_RED arrives
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/STAGE-B-PLAN-19b-sweep-events`
