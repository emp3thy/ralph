# Close sweep decision-tree test gaps (tech-debt: sweep-decision-test-gaps)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** The remaining untested decide_action precedence interactions and boundary conditions, plus direct `_new_active_human_comments` edge cases, are covered by unit tests in `tests/executor/sweep/test_decide_action.py`.

**Scope correction (investigator 2026-06-10 evening):** the scan finding claimed only "4 terminal cases" of coverage, citing test_runner.py — it missed `test_decide_action.py`, which already holds 11 direct tests (terminal table, comment trigger, author/thread filters, seen-ids, stale-vs-comments, missing-email validation, auto-merge off/on/dirty, comments-preempt-merge). This plan covers only the verified residual gaps. `_dispatch` direct unit tests are explicitly NOT pursued: the investigator's testability analysis shows it needs 5+ stubs while the existing integration suite (test_runner.py, 20+ tests incl. all three merge exit codes, event emissions, sidecar transitions) already exercises every Action branch — adding mock-heavy unit duplicates would be negative-value.

**Character of the work:** these are characterization tests of EXISTING behavior — expected to pass on first run. If any new test FAILS, that is a real bug discovery: STOP, do not adjust the test to pass, report the failing case to the user before proceeding.

**The residual gaps (each maps to one test):**
1. `abandoned` + new human comments → `MOVE_TO_BLOCKED_ABANDONED` (status precedes comments)
2. `unknown` + new human comments → `NOOP` (status precedes comments)
3. CI `failed` + auto-merge flag on + `merge_state="clean"` → `MOVE_TO_INBOX_RETRY` (CI precedes merge)
4. CI `failed` + new human comments → CI branch wins (`MOVE_TO_INBOX_RETRY` at attempts < max)
5. Staleness boundary: `config.now - last_activity_at == stale_threshold` exactly → `PING_REVIEWER` (the `>=` is inclusive); one second under → `NOOP`
6. `merge_state` exact-match: flag on + `merge_state="unstable"` (and `"CLEAN"` uppercase) → not `MERGE_PR`
7. `_new_active_human_comments` direct: author email case-insensitivity (`RALPH@X.COM` config vs `ralph@x.com` comment and vice versa → filtered)
8. `_new_active_human_comments` direct: every non-active ThreadStatus (`pending`, `fixed`, `closed`, `wontFix`, `byDesign`, `unknown`) excluded even with fresh human comments
9. `_new_active_human_comments` direct: one thread, multiple comments, mixed seen/unseen → only unseen returned, order preserved
10. `CREATE_FEEDBACK_PBI` decision carries exactly the new comments in `decision.new_comments` (tuple contents asserted, not just length)

**Guardrails:** Reuse the file's existing builders (`_config`, `_snapshot`, `_comment`, `_thread`, `_decide`) — no new fixture machinery [[1ca10d57 conftest-reuse lesson]]. Tests-only diff: zero changes under `ralph_executor/`. Stage specific paths [[0547374e]].

**Confidence:** T1 94% — pure-function tests against enumerated, investigator-verified branch logic with existing builders; the only risk is a characterization test exposing a real bug, which the plan converts into a STOP-and-report. T2 95%. Floor 92% met.

---

### Task 1: Add the gap tests

**Files:**
- Modify: `tests/executor/sweep/test_decide_action.py` (only)

- [ ] **Step 1:** Read the file fully (builders at lines ~29-110). Add the 10 tests above, grouped: precedence-interaction tests after the existing terminal-table section; boundary tests near the stale tests; `_new_active_human_comments` direct tests in a new section importing it explicitly (`from ralph_executor.sweep.runner import _new_active_human_comments` — check how decide_action is imported and match style).
- [ ] **Step 2:** Run `uv run pytest tests/executor/sweep/test_decide_action.py -q`. ALL tests green expected. Any failure = real bug: stop, report to the user with the failing case, await direction.
- [ ] **Step 3:** `uv run ruff check tests` clean; `uv run mypy ralph_executor` clean (unchanged, sanity).
- [ ] **Step 4:** Commit — `git add tests/executor/sweep/test_decide_action.py && git commit -m "test(sweep): cover decide_action precedence interactions and comment-filter edges"`

### Task 2: Gates, PR

- [ ] **Step 1:** `uv run pytest tests/executor/sweep -q` green.
- [ ] **Step 2:** Push `tech-debt/sweep-decision-test-gaps`, PR per campaign Task 10 (PR body must note the scope correction — finding shrank on verification because test_decide_action.py already existed), bot-watch to merge.
