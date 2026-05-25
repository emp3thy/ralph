---
id: TEST-001
type: feature
status: inbox
severity: normal
attempts: 0
created_at: 2026-05-25T17:15:00+00:00
updated_at: 2026-05-25T17:15:00+00:00
---

# Smoke test — add a marker comment to README

This is the first Ralph self-host iteration. Goal: verify the executor → claude -p → pr-github skill loop end-to-end.

## Acceptance criteria

- Append the following line to the bottom of `README.md` (on a new line, after any existing trailing newline):
  ```
  <!-- ralph smoke test passed on 2026-05-25 -->
  ```
- Commit on the feature branch `ralph/TEST-001` with message `test(smoke): ralph smoke marker`.
- Push the branch.
- Create a PR via the `pr` skill (the executor stages it as `pr` at startup).
- Do NOT change anything else.

## Notes

- This is a trivial PBI to verify the loop wiring. No tests required, no other files touched.
- If you cannot complete this, write `STUCK.md` with what stopped you.
