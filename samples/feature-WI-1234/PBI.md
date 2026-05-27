---
id: WI-1234
type: feature
status: inbox
severity: normal
attempts: 0
created_at: 2026-05-24T09:15:00+00:00
updated_at: 2026-05-24T09:15:00+00:00
target_repo: https://github.com/emp3thy/ralph
---

# Add `/healthz` endpoint to service-auth

## Context

Platform requires every service to expose a liveness probe at `GET /healthz`
that returns `200 OK` with a small JSON body when the process can serve
traffic. service-auth currently has no liveness probe, which blocks the
ROSA migration backlog (the deployment manifest references a probe path that
does not exist; the pod is marked unhealthy on first scrape).

## Acceptance criteria

- `GET /healthz` returns status `200` with body `{"status":"ok"}` when the
  process is running.
- The endpoint requires no authentication.
- The endpoint completes in under 50ms p99 against the existing test
  harness.
- A new test in `tests/test_health.py` asserts the contract.

## Constraints

- Do not introduce a new web framework. Use the existing FastAPI app
  factory in `service_auth/app.py`.
- Do not log on the hot path — health checks fire every 10s and would
  drown the log stream.
