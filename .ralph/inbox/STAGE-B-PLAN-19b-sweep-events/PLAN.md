# Plan: wire sweep-side cycle-detector events

Self-contained; no separate doc under `docs/superpowers/plans/`.

Requires Plan 08 (sweep observer) to be merged first — this PBI's depends_on enforces that.

## Why

The three events sweep can produce that loop cannot:

- `PR_MERGED` — only observable across iterations; the iteration that opens a PR never sees the merge
- `PR_GREEN_THEN_RED` — requires comparing prior vs current required-check state; only sweep has the history
- `PBI_CLOSED` — fires on pending-pr → done, which is sweep's job (per Plan 08)

Without these, `regression_cascade`, `signature_recurrence` (partial), and `whack_a_mole` (partial) cannot fully fire.

## Design

Sweep state tracking (introduced by Plan 08, refined here):

```python
# .ralph/state/sweep.json
{
  "<pbi_id>": {
    "pr_number": 42,
    "pr_url": "https://github.com/.../pull/42",
    "last_state": "MERGED" | "OPEN" | "CLOSED",
    "last_check_bucket": "pass" | "fail" | "pending" | "unknown",
    "signature": "abc123def456..."
  }
}
```

On each sweep tick, for each pending-pr PBI:
1. Query current PR state + check bucket via gh API
2. Compare to last-known state
3. Emit events for transitions:
   - `OPEN → MERGED` → emit PR_MERGED + move to done + emit PBI_CLOSED
   - `last_check_bucket == "pass"` AND current includes any "fail" → emit PR_GREEN_THEN_RED
4. Write updated state

## Tasks

### Task 1 — sweep state structure

Add to whatever module Plan 08 introduces (typically `ralph_executor/sweep/state.py`):

```python
@dataclass
class SweepState:
    by_pbi: dict[str, PerPBIState]

@dataclass
class PerPBIState:
    pr_number: int
    pr_url: str
    last_state: str
    last_check_bucket: str
    signature: str

def load_sweep_state(repo: Path) -> SweepState: ...
def save_sweep_state(repo: Path, state: SweepState) -> None: ...
```

If Plan 08 already has this structure, extend it; do not duplicate.

Commit: `feat(sweep): per-PBI state tracking for cycle-detector event transitions`

### Task 2 — PR_MERGED + PBI_CLOSED on merge

In sweep's pending-pr observer, when `gh pr view <num> --json state -q .state` returns `MERGED`:

```python
event_log.append(Event(
    kind=EventType.PR_MERGED,
    recorded_at=now,
    pbi_id=pbi.id,
    payload={
        "pr_url": pr_url,
        "signature": stored_signature,  # from sweep state
        "files": diff_names_at_merge,   # gh pr view --json files
    },
))
# move PBI to done/
move_pending_pr_to_done(cfg, pbi, event_log=event_log)
event_log.append(Event(
    kind=EventType.PBI_CLOSED,
    recorded_at=now,
    pbi_id=pbi.id,
    payload={"signature": stored_signature},
))
```

Commit: `feat(sweep): emit PR_MERGED + PBI_CLOSED on PR merge transition`

### Task 3 — PR_GREEN_THEN_RED on CI regression

In the same sweep loop, when a PR's current `gh pr checks --required --json bucket` includes any `bucket=="fail"` AND the stored `last_check_bucket` was `"pass"`:

```python
event_log.append(Event(
    kind=EventType.PR_GREEN_THEN_RED,
    recorded_at=now,
    pbi_id=pbi.id,
    payload={
        "pr_url": pr_url,
        "signature": stored_signature,
        "files": current_diff_files,
    },
))
```

Update stored `last_check_bucket` after emission to prevent re-emission of the same transition.

Commit: `feat(sweep): emit PR_GREEN_THEN_RED on green→red CI transition`

### Task 4 — `move_pending_pr_to_done` helper

If Plan 08 doesn't already add it, add to `ralph_executor/queue/movements.py`:

```python
def move_pending_pr_to_done(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    event_log: EventLog | None = None,
) -> PBI:
    """git mv .ralph/pending-pr/<id> .ralph/done/<id>; commit + push."""
```

Commit: `feat(queue): move_pending_pr_to_done helper`

### Task 5 — regression tests

- `test_sweep_emits_pr_merged_on_merge_transition`
- `test_sweep_emits_pbi_closed_after_pr_merged`
- `test_sweep_emits_pr_green_then_red_on_check_regression`
- `test_sweep_does_not_re_emit_pr_green_then_red_after_state_update`
- `test_regression_cascade_trips_with_pr_merged_then_matching_green_then_red` — integration: emit synthetic events, assert detector trips
- `test_signature_recurrence_trips_across_pr_merged_and_signature_observed` — integration

Commit: `test(sweep): coverage for sweep-side detector event emission`

## Out of scope

- Loop-side events (PBI 19a)
- Tuning detector thresholds
- Sweep frequency policy — inherits from Plan 08
- Notification on PR_GREEN_THEN_RED — that's just an event; detector trip writes the META-BUG

## Adversarial pre-pass

| Failure mode | Mitigation |
|---|---|
| gh API throttling | Sweep already polls on a schedule; on rate-limit, skip this tick |
| Sweep state file missing on first run | Initialise empty; first tick records baseline, no events emitted |
| PR closed without merge | `gh pr view --json state` returns CLOSED; move to done with no PR_MERGED; emit PBI_CLOSED |
| PR force-pushed and CI re-runs | last_check_bucket logic re-evaluates; emits PR_GREEN_THEN_RED if green→red across that re-run |
| Sweep state schema drift | Schema versioning: `{"_version": 1, "by_pbi": {...}}`; migration on load |
| Concurrent sweeps (two workers) | Out of scope here — Plan 08 owns concurrency; this PBI assumes single sweeper |
