# Implementation plan — WI-1234 — /healthz endpoint

- [ ] 1. Add `tests/test_health.py` with a failing test that asserts
  `GET /healthz` returns 200 and `{"status":"ok"}`.
- [ ] 2. Register a `/healthz` route on the FastAPI app in
  `service_auth/app.py` that returns the JSON payload above.
- [ ] 3. Verify the new test passes and the existing test suite still
  passes (`pytest -q`).
- [ ] 4. Confirm no new log lines are emitted by the route under the
  existing log configuration.
- [ ] 5. Update `README.md` with a one-line note that `/healthz` is the
  liveness path.
