# Remove legacy queue/ path handling (tech-debt: legacy-queue-clone-path)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** `ensure_queue_clone` requires `instance_id`; the silent `None` → legacy `queue/` fallback and the magic auto-rename migration are gone (replaced by a loud error); ralph-new — the one un-migrated caller — threads `instance_id` like every other skill; dry-run output in cancel/promote/triage reports the real namespaced path.

**Architecture (from investigator scan):** `acquire_queue_clone` (queue_writer.py:89) already REQUIRES `instance_id` and all 6 operator skills resolve + pass it; `queue_git.pull_queue` threads `cfg.instance_id`. The only live `None`-fallback user is `skills/ralph-new/scripts/new.py:393`. The legacy machinery in `queue_clone.py` (lines 88–113: `None`→`queue/` dest, collision check, auto-rename) therefore protects exactly one caller — fix the caller first, then make the helper strict. Three skills additionally print `workspace_root / "queue"` in dry-run output (cancel.py:165, promote.py:175, triage.py:142) while their real calls use the namespaced path — wrong output, same root cause.

**Behavior changes (deliberate, per the approved finding):**
1. `ensure_queue_clone(..., instance_id=None)` was accepted → now a TypeError (parameter required, no default).
2. Pre-multi-ralph legacy `queue/` dir was silently renamed → now raises `QueueCloneError` with an explicit migration message telling the operator to rename `queue/` to `queue-<instance_id>/` (or delete it). Collision case (both exist) keeps its existing error.
3. Dry-run JSON/log output in cancel/promote/triage shows `queue-<instance_id>` instead of `queue`.

**Guardrails:** Stage specific paths [[0547374e]]. mypy strict + ruff per task. New tests import conftest helpers, don't redefine [[1ca10d57]]. Verify reviewer bug-claims by running named tests [[1ca10d57]]. Implementers: run gates in FOREGROUND with generous timeout; do not background-and-exit.

**Confidence:** T1 93% — mirrors the 6 existing skills' resolve-and-pass pattern exactly; TDD with a namespaced-path assertion. T2 92% — strictness change is mechanical; the 3 legacy tests are enumerated and get explicit new expectations; deliberate behavior change documented above. T3 94% — three one-line output fixes with test updates. T4 95% — gates + PR proven. Floor 92% met.

---

### Task 1: ralph-new threads instance_id (TDD)

**Files:**
- Modify: `skills/ralph-new/scripts/new.py`
- Test: the ralph-new test file that exercises queue-clone acquisition (locate: `git grep -n "ensure_queue_clone\|queue_clone" tests/skills/test_ralph_new*` — likely test_ralph_new_e2e.py)

- [ ] **Step 1: Write the failing test** — in the ralph-new e2e/queue test file, assert that the queue clone created/used by ralph-new is at `workspace / "queue-<instance_id>"` (use the instance id the test environment resolves — read how sibling skills' tests pin it, e.g. `--instance-id test-ralph` argv or env). Test fails today because new.py:393 calls `ensure_queue_clone(workspace, queue_repo, queue_branch)` without instance_id → legacy `queue/`.
- [ ] **Step 2: Run it, verify FAIL** for that reason.
- [ ] **Step 3: Implement** — in new.py: resolve the operator instance id the same way the other skills do (read cancel.py/promote.py for the pattern: `resolve_instance_id(...)` from scripts.queue_writer with CLI flag/env/TOML/hostname precedence — new.py may need the `--instance-id` argparse flag added to match; mirror the sibling skills' flag definition and help text exactly) and pass `instance_id=` to the `ensure_queue_clone` call. If new.py's import layout doesn't reach scripts.queue_writer (it bootstraps differently per the 2026-06-10 morning scan), add the same `_REPO_ROOT`/sys.path bootstrap the other skills use.
- [ ] **Step 4: Run** — the new test + `uv run pytest tests/skills -q` green; `uv run mypy ralph_executor` clean; `uv run ruff check skills tests` clean.
- [ ] **Step 5: Commit** — `git add skills/ralph-new/scripts/new.py <testfile> && git commit -m "fix(ralph-new): thread instance_id into queue clone acquisition"`

### Task 2: ensure_queue_clone strictness (TDD)

**Files:**
- Modify: `ralph_executor/queue_clone.py`
- Test: `tests/executor/test_queue_clone.py`

- [ ] **Step 1: Rewrite the three legacy tests' expectations first (failing):**
  - `test_legacy_queue_renamed_to_namespaced` (line ~169) → rename to `test_legacy_queue_dir_raises_with_migration_message`; expects `QueueCloneError` matching `"legacy .*queue.*rename"`-style message (write the exact message in the test).
  - `test_namespaced_only_is_noop_on_legacy` (~188) → keep semantics (no legacy dir present → normal operation), update only if it referenced rename behavior.
  - `test_both_paths_exist_refuses` (~207) → keep; same collision error.
  - Add `test_instance_id_is_required` → calling without instance_id raises TypeError.
- [ ] **Step 2: Run, verify the changed tests FAIL** against current behavior.
- [ ] **Step 3: Implement in queue_clone.py:** signature becomes `(..., *, instance_id: str, timeout: float = 120.0)`; delete the `None`→legacy dest branch; replace the auto-rename block with:

```python
legacy = workspace_root / "queue"
dest = workspace_root / f"queue-{instance_id}"
if legacy.exists() and dest.exists():
    raise QueueCloneError(
        f"both legacy queue clone {legacy} and namespaced {dest} exist; "
        "remove or merge one of them manually"
    )
if legacy.exists():
    raise QueueCloneError(
        f"legacy queue clone found at {legacy}. Multi-ralph requires the "
        f"namespaced path: rename it to {dest} (git-aware: plain mv/rename is fine) "
        "or delete it to re-clone"
    )
```

  (Keep the existing collision message if tests pin it — read the current text and preserve byte-identically where the case is unchanged.) Rewrite the module + function docstrings: no more "Tasks 12-15" / backwards-compat prose; document the strict contract and the migration error.
- [ ] **Step 4: Run** — `uv run pytest tests/executor/test_queue_clone.py tests/executor/test_queue_git.py tests/skills -q` green (skills suite proves all callers still work); mypy + ruff clean.
- [ ] **Step 5: Commit** — `"refactor(queue_clone): require instance_id; legacy queue/ now errors instead of auto-migrating"`

### Task 3: Dry-run output shows namespaced path

**Files:**
- Modify: `skills/ralph-cancel/scripts/cancel.py` (~165), `skills/ralph-promote/scripts/promote.py` (~175), `skills/ralph-triage/scripts/triage.py` (~142)

- [ ] **Step 1:** In each, the dry-run branch computes `clone = workspace_root / "queue"` for reporting only — change to the namespaced path using the already-resolved operator instance id (`workspace_root / f"queue-{operator_instance_id}"`; confirm the variable name in each file). If any dry-run test asserts on the old path string (`git grep -n '"queue"' tests/skills/`), update it to expect the namespaced path.
- [ ] **Step 2:** `uv run pytest tests/skills -q` green; ruff clean.
- [ ] **Step 3:** Commit — `"fix(skills): dry-run output reports namespaced queue clone path"`

### Task 4: Full gates, PR

- [ ] **Step 1:** `uv run pytest -q` full suite; `uv run mypy ralph_executor`; `uv run ruff check scripts skills tests ralph_executor` — all green. Verify: `git grep -n 'workspace_root / "queue"' ralph_executor/ skills/ scripts/` returns zero live hits (queue_clone's error-message construction may reference the legacy path string — that's allowed).
- [ ] **Step 2:** Docs sweep: `git grep -n "auto-rename\|renamed" docs/runbooks/` — update if any runbook documents the auto-migration; "docs unaffected" if none. (SKILL.md queue-path prose is finding #3's scope — do NOT fix it here.)
- [ ] **Step 3:** Push `tech-debt/legacy-queue-clone-path`, PR per campaign Task 10, bot-watch to merge.
