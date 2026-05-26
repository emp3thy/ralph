# Plan: real CI-green verifier in classify_outcome

This plan is self-contained — there is no separate canonical plan file under `docs/superpowers/plans/` for this PBI.

## Why this is load-bearing

Ralph's "deterministically bad beats undeterministically good" framing rests on a hard verifier. Today there is no such verifier — `pr_created` is asserted on URL existence alone. The wrong-but-passing convergence failure mode goes undetected end-to-end. Closing this gap is the floor for trusting ralph's pending-pr lane.

## Design

Placement: **classifier-mode**. The verifier runs inside `classify_outcome` (`ralph_executor/claude_spawn.py`), not in sweep. Reasons:
- Sweep does not exist yet (Plan 08 lands it); this PBI must not block on that.
- Classifier-mode is simpler and self-contained — one polling helper, no cross-component state.
- A pessimistic classifier ("don't claim pr_created until CI is actually green") composes cleanly with sweep later (sweep will detect post-merge regressions via `PR_GREEN_THEN_RED`, separately).

## Tasks

Progress (each Ralph iteration completes one):

- [x] Task 1 — `_query_pr_checks` helper
- [x] Task 2 — `_wait_for_pr_checks` polling loop
- [x] Task 3 — integrate into `classify_outcome`
- [x] Task 4 — tests
- [ ] Task 5 — `_run_ralph` docstring update (NO-OP code-wise)
- [ ] Task 6 — config knob for budget

### Task 1 — `_query_pr_checks` helper

Add to `ralph_executor/claude_spawn.py`:

```python
def _query_pr_checks(
    repo_path: Path,
    pr_number: int,
    *,
    timeout_seconds: float = 30.0,
) -> tuple[Literal["pass", "fail", "pending", "error"], list[str]]:
    """Call `gh pr checks <num> --required --json bucket,name` once.

    Returns:
        ("pass",    []) — every required check has bucket=="pass"
        ("fail",    [<failed names>])
        ("pending", []) — at least one bucket=="pending" (gh exit 8)
        ("error",   [<stderr summary>]) — gh failed for unknown reasons
    """
```

Behaviour:
- `subprocess.run(["gh", "pr", "checks", str(pr_number), "--required", "--json", "bucket,name"], cwd=repo_path, capture_output=True, text=True, timeout=timeout_seconds, check=False)`
- gh exit code 0 + bucket=="pass" for every entry → return "pass"
- gh exit code 0 + any bucket=="fail" → return "fail" with names list
- gh exit code 8 → return "pending"
- Any other → return "error"
- Wrap subprocess.TimeoutExpired → return "error"

Commit: `feat(claude_spawn): _query_pr_checks helper for required-check state`

### Task 2 — `_wait_for_pr_checks` polling loop

```python
def _wait_for_pr_checks(
    repo_path: Path,
    pr_number: int,
    *,
    max_polls: int = 6,
    interval_seconds: float = 30.0,
) -> tuple[Literal["pass", "fail", "pending", "error"], list[str]]:
    """Poll _query_pr_checks until terminal or budget exhausted.

    Returns "pending" if budget exhausted with no decision — caller treats
    as partial (PBI stays in current/, next iteration polls again).
    """
```

Total wall budget = `max_polls * interval_seconds` = 3 minutes per iteration.

Commit: `feat(claude_spawn): _wait_for_pr_checks polling loop`

### Task 3 — integrate into `classify_outcome`

In `spawn_claude_p`, after `_query_open_pr_via_gh` resolves a `pr_url`:

```python
if pr_url is not None:
    pr_number = _pr_number_from_url(pr_url)
    state, failed_names = _wait_for_pr_checks(cfg.repo_path, pr_number)
    if state == "pass":
        # actually pr_created; proceed as today
        ...
    elif state == "fail":
        # treat as partial; Claude needs to fix the failing checks
        synthetic_stderr = (
            f"PR #{pr_number} required checks failed: {failed_names}. "
            f"Fix the failures next iteration."
        )
        return ClaudeOutcome(
            kind="partial",
            pr_url=pr_url,
            stdout=stdout_text,
            stderr=stderr_text + "\n" + synthetic_stderr,
            ...
        )
    else:  # pending / error
        # treat as partial; next iteration will re-poll
        return ClaudeOutcome(kind="partial", ...)
```

Add `_pr_number_from_url` helper (parses `/pull/<N>` suffix).

Commit: `feat(classifier): gate pr_created on CI-green; partial otherwise`

### Task 4 — tests

In `tests/executor/test_claude_spawn.py` add:

- `test_query_pr_checks_returns_pass` — monkeypatch subprocess.run with bucket=pass for all
- `test_query_pr_checks_returns_fail_with_names` — bucket=fail for one, names list populated
- `test_query_pr_checks_returns_pending_on_exit_8`
- `test_query_pr_checks_returns_error_on_gh_failure`
- `test_wait_for_pr_checks_polls_until_pass`
- `test_wait_for_pr_checks_budget_exhausted_returns_pending`
- `test_classify_outcome_pass_returns_pr_created`
- `test_classify_outcome_fail_returns_partial_with_stderr`
- `test_classify_outcome_pending_returns_partial`

Commit: `test(classifier): regression coverage for CI-green verifier`

### Task 5 — update `_run_ralph` event emission (spec only — actual emission lands in Plan 19a)

This task is a NO-OP code-wise. Document in `ralph_executor/loop.py` `_run_ralph` docstring that PR_GREEN_THEN_RED emission is delegated to Plan 19b (sweep observer); the verifier here only decides classification, not event emission.

Commit: `docs(loop): note PR_GREEN_THEN_RED is sweep-side per Plan 19b`

### Task 6 — config knob for budget

Add `pr_check_poll_max_attempts` and `pr_check_poll_interval_seconds` to `ExecutorConfig` + `_TOML_KNOWN_KEYS` with defaults 6 and 30.0. Plumb into `_wait_for_pr_checks` via cfg.

Commit: `feat(config): pr_check_poll_* TOML knobs`

## Out of scope

- Post-merge regression detection (PR_GREEN_THEN_RED event) — that's Plan 19b
- Distinguishing "no required checks configured" — treat as "pass" (gh returns empty list); document explicitly
- Branch protection auto-setup — operator's job

## Adversarial pre-pass (per general feedback memory)

| Failure mode | Mitigation |
|---|---|
| gh CLI not in PATH | `_query_pr_checks` returns "error"; classifier returns partial; iteration retries |
| Subprocess hangs | `subprocess.run(..., timeout=30)`; wrap TimeoutExpired |
| `--required` returns empty (no required checks set) | Treat empty list as "pass"; document that this PBI assumes operator has required checks configured. Add WARNING log line so this is visible. |
| PR URL malformed | `_pr_number_from_url` raises; classifier returns "error"-equivalent partial |
| Race: PR exists but checks haven't been registered yet | exit 8 ("pending"); polling loop covers |
| All checks pass but PR is closed/merged externally | Out of scope for this PBI; sweep handles closed-PR state |
