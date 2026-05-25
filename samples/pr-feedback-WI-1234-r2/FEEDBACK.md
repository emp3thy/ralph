---
id: PR-feedback-WI-1234-r2
type: pr-feedback
status: inbox
severity: high
attempts: 0
created_at: 2026-05-24T11:02:00+00:00
updated_at: 2026-05-24T11:02:00+00:00
---

# PR feedback — PBI WI-1234 — round 2

**PR:** !4711 — "WI-1234: add /healthz endpoint"
**Branch:** ralph/WI-1234
**Commit at time of feedback:** 9f1c0a3d1e4b2c5a7f8b6e3d4c5a9b8e7d6c5b4a
**Reviewers:** alice@example.com, bob@example.com
**Feedback collected at:** 2026-05-24T11:00:00+00:00

## Active comments

### Comment 1 — file: service_auth/app.py, line 42
- **Thread ID:** 8821
- **Reviewer:** alice@example.com
- **Posted:** 2026-05-24T10:58:14+00:00

> The new route is registered before the existing `@app.middleware("http")`
> logging middleware. Move the registration below it so the health probes
> are excluded from the access log filter we already added in PR !4690.

---

### Comment 2 — file: tests/test_health.py, line 12
- **Thread ID:** 8822
- **Reviewer:** bob@example.com
- **Posted:** 2026-05-24T10:59:47+00:00

> The test asserts `response.json() == {"status": "ok"}` but the body in
> the implementation is `{"status":"ok"}` with no space. The test passes
> because `json()` round-trips, but please add an assertion on the raw
> content-type header (`application/json`) so a future regression that
> switches to `text/plain` would fail loudly.

---

## Instructions for Ralph

For each active comment above:

1. **Read the comment carefully** and examine the referenced code
   (file:line for line comments; the diff for general comments).

2. **Decide: is the reviewer correct?**
   - If the concern is valid → implement the fix.
   - If the concern is incorrect or based on a misunderstanding →
     don't change the code. Prepare a clear, respectful reply explaining why.
   - If you can't tell from available context → write STUCK.md
     with what's unclear.

3. **Always reply.** Use the `ado-pr` skill to post in the thread.
   Tell the reviewer:
   - What you changed (filename, line, brief description), OR
   - Why you didn't change anything (your reasoning).

4. **Set thread status after replying:**
   - Applied a fix → set thread to **fixed**.
   - Challenged AND your reasoning is strong → leave **active**;
     the reviewer will close it after reading.
   - Never set **wontFix** or **byDesign** — those are human-only.

5. **Commit and push** to the existing branch after all comments addressed.
   The PR will auto-update and CI re-runs.

## Constraints
- Don't open a new PR. Push commits to the existing branch.
- Don't address comments outside this FEEDBACK.md (no scope creep).
- If a comment references work that should be a separate PBI
  ("can we also rename X?"), note it in your reply and don't do it here.
- If you challenge and the reviewer later responds with a counter, that
  becomes a new FEEDBACK round — don't try to predict their response.
