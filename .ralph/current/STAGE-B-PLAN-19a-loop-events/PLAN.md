# Plan: wire loop-side cycle-detector events

Self-contained; no separate doc under `docs/superpowers/plans/`.

## Why

Detector inputs are the gap. Without emission, the safety net is half-wired and the rules in `cycle_detector.py` are dead code in production.

Sibling PBI 19b covers sweep-side events (PR_MERGED, PR_GREEN_THEN_RED, PBI_CLOSED). This PBI covers everything emittable from the loop driver.

## Event payload schemas (extracted from `cycle_detector.py`)

| Event | Required payload | Used by |
|---|---|---|
| `PR_CREATED` | `signature: str`, `files: list[str]` | `same_file_thrashing`, optional in `regression_cascade` |
| `PBI_OPENED` | (timestamp + pbi_id from envelope) | `whack_a_mole` |
| `FILE_TOUCHED` | `files: list[str]` | (currently no rule reads this directly, but detector design anticipates per-iteration emission for future rules; emit anyway for forward compat) |
| `SIGNATURE_OBSERVED` | `signature: str` | `signature_recurrence` |

`signature` is an opaque string the producer chooses. The detector only compares for equality. Convention: sha256[:16] of a normalised string.

## Tasks

### Task 1 — signature helpers ✅

Add to `ralph_executor/safety/events.py`:

```python
def signature_from_text(text: str) -> str:
    """Return a 16-hex-char sha256 of a normalised string.

    Normalisation: strip leading/trailing whitespace, collapse runs of
    whitespace to single space, lower-case. Deterministic across runs.
    """
```

Used by every emit site that needs a signature.

Commit: `feat(events): signature_from_text helper for cycle-detector event payloads`

### Task 2 — PR_CREATED in `move_current_to_pending_pr` ✅

`ralph_executor/queue/movements.py::move_current_to_pending_pr` currently moves the PBI directory but emits nothing. Refactor to accept an event log + PR URL, and emit:

```python
event_log.append(Event(
    kind=EventType.PR_CREATED,
    recorded_at=now,
    pbi_id=pbi.id,
    payload={
        "pr_url": pr_url,
        "signature": signature_from_text(pr_url),
        "files": touched_files,
    },
))
```

`touched_files` is computed by the caller as `git diff --name-only main..ralph/<PBI-ID>` (i.e. the cumulative diff of the feature branch). Add `git_ops.diff_names(repo, base, head)` helper if not present.

Update the caller in `loop.py::_run_ralph` to pass `event_log` and the diff.

Commit: `feat(queue): emit PR_CREATED with files+signature when promoting to pending-pr`

### Task 3 — PBI_OPENED in `move_inbox_to_current` ✅

Similarly refactor `move_inbox_to_current` to emit:

```python
event_log.append(Event(
    kind=EventType.PBI_OPENED,
    recorded_at=now,
    pbi_id=pbi.id,
    payload={},
))
```

Update caller in `loop.py::_claim_pbi`.

Commit: `feat(queue): emit PBI_OPENED on claim`

### Task 4 — FILE_TOUCHED in `_persist_iteration_writes`

After the commit in `_persist_iteration_writes` (loop.py) succeeds, compute `git diff --name-only HEAD@{1} HEAD` and emit FILE_TOUCHED. Skip if the commit was a no-op (no files changed).

Note: only emit if a NEW commit landed (compare HEAD before and after).

Commit: `feat(loop): emit FILE_TOUCHED per iteration commit`

### Task 5 — SIGNATURE_OBSERVED in `handle_stuck`

`ralph_executor/safety/stuck.py::handle_stuck` reads STUCK.md when Claude reports stuck. After reading the reason text, emit:

```python
event_log.append(Event(
    kind=EventType.SIGNATURE_OBSERVED,
    recorded_at=now,
    pbi_id=pbi_id,
    payload={"signature": signature_from_text(stuck_reason)},
))
```

The existing PBI_BLOCKED emission stays as-is.

Commit: `feat(stuck): emit SIGNATURE_OBSERVED with hashed reason`

### Task 6 — regression tests

In `tests/safety/` or `tests/executor/`:

- `test_pr_created_event_emitted_with_files_and_signature`
- `test_pbi_opened_event_emitted_on_claim`
- `test_file_touched_event_emitted_on_iteration_commit`
- `test_file_touched_skipped_on_empty_commit`
- `test_signature_observed_event_emitted_on_stuck`
- `test_same_file_thrashing_trips_after_six_distinct_prs_touching_one_file` — integration: emit 6 PR_CREATED events with shared file, assert detector trips
- `test_signature_recurrence_trips_after_pbi_closed_then_signature_reobserved` — integration

Commit: `test(safety): integration coverage for loop-side detector events`

## Out of scope

- PR_MERGED / PR_GREEN_THEN_RED / PBI_CLOSED — those are in PBI 19b (sweep)
- Tuning detector thresholds — separate concern
- Removing already-emitted ATTEMPT_INCREMENTED / PBI_BLOCKED — those stay

## Adversarial pre-pass

| Failure mode | Mitigation |
|---|---|
| diff_names against unborn branch fails | Catch + emit empty files list; log warning |
| git diff returns binary file marker | Filter to text paths only |
| Stuck reason is empty / whitespace | signature_from_text on empty returns deterministic hash; harmless |
| Event log append fails | Already-failure-tolerant in existing code; wrap in try/except matching the existing pattern |
| Movement runs but commit fails | Emission already happens before commit in current code; this PBI doesn't change ordering |
| Backwards compat | Old events without these payloads still in event log; detectors skip events lacking required payload (already verified — _signature, _files return None / empty for absent keys) |
