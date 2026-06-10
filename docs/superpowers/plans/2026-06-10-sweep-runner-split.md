# Decompose sweep/runner.py (tech-debt: sweep-runner-god-functions)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** sweep/runner.py (757 lines) splits into: `sweep/events.py` (event emission), `sweep/feedback_emit.py` (feedback-PBI two-phase write with a single shared cleanup helper), `sweep/actions.py` (action execution: moves, history, dispatch, merge routing), with runner keeping config/context dataclasses, the pure decision functions, and a slimmer run()/_process_pbi orchestration pair. Behavior identical.

**Architecture (investigator map 2026-06-10):** runner's 18 functions group cleanly: emission (\_emit_pr_merged_and_pbi_closed 558–615, \_emit_pr_green_then_red 617–660) has no deps on other extraction targets; feedback emission (\_emit_feedback_pbi 494–556, \_read_original_summary 747–758) depends on state/feedback_pbi siblings + history append; action execution (\_move_with_history 456–477, \_append_history 479–492, \_dispatch 415–454, \_invoke_merge_pr 662–705, \_dispatch_merge_pr 707–745) depends on the other two. Extraction order: errors/events → feedback → actions. `decide_action` + `_new_active_human_comments` (just locked by PR #79's tests) and `_per_pbi_subprocess_overrides` (imported by reconcile.py:35 — DO NOT MOVE) stay in runner.

**Test compatibility (the easy part this time):** tests exercise the public `run(ctx)` only — the investigator found ZERO symbol-level monkeypatches of runner internals (shims work via env vars). Re-exports under old private names in runner are still required for: reconcile.py's `_per_pbi_subprocess_overrides` import (stays put anyway) and any internal cross-calls. `sweep/__init__.py` re-exports `run`, `SweepResult` — unchanged.

**`_SweepPbiError` placement:** raised by functions landing in three different modules — moves FIRST to `sweep/types.py` as public `SweepPbiError` with `_SweepPbiError = SweepPbiError` alias kept in runner (grep shows runner + tests reference the private name; check reconcile too).

**rmtree dedup:** the two cleanup blocks (527–529, 552–554) keep their two except-sites and distinct messages (behavior-identical: different exception origins must produce different messages) but share a `_cleanup_partial_feedback_dir(target_dir)` helper so the cleanup action is single-sourced.

**Guardrails:** Stage specific paths [[0547374e]]. mypy strict + ruff every task. Convention-depth docstrings, no dead loggers in new modules [[db26c435]]. Helper signatures = exactly what the body consumes [[ffdd01f6]]. ruff --fix decides import formatting [[327adbfc]]. New modules are leaf-level within sweep/ (import types/state/feedback_pbi/subprocess_utils; never runner — runner imports THEM).

**Confidence:** T1 94% — error-class move + emission module, no patch targets, suite via public API. T2 93% — feedback module with the dedup helper; two-phase semantics locked by existing round-trip tests (feedback round escalation, repeat-sweep no-regen). T3 93% — actions module; merge-exit routing locked by the three auto-merge exit-code tests. T4 93% — in-module slimming of run/_process_pbi with enumerated phases. T5 95% — gates + PR proven. Floor 92% met.

---

### Task 1: SweepPbiError → types.py; create sweep/events.py

**Files:**
- Modify: `ralph_executor/sweep/types.py`, `ralph_executor/sweep/runner.py`
- Create: `ralph_executor/sweep/events.py`

- [ ] **Step 1:** Move `_SweepPbiError` (runner 217–219) to types.py as `class SweepPbiError(Exception)` (docstring moved verbatim); runner adds `from ralph_executor.sweep.types import SweepPbiError as _SweepPbiError` (grep reconcile.py + tests for `_SweepPbiError` references first — alias whatever is needed).
- [ ] **Step 2:** Create `ralph_executor/sweep/events.py` ("Event emission for sweep outcomes — PR merged/closed and CI green-to-red transitions."): move `_emit_pr_merged_and_pbi_closed` and `_emit_pr_green_then_red` VERBATIM as public `emit_pr_merged_and_pbi_closed` / `emit_pr_green_then_red`. Module logger kept only if the bodies log (they do — WARNING swallow paths). SweepContext type: import from runner would be circular — accept `event_log`/`config` params instead? NO — read the bodies first: they take (pbi_id, snapshot, ctx). To stay leaf-level, change signatures to take exactly what they consume (`event_log`, `now`/config fields) ONLY if the body uses just those; otherwise move `SweepContext`+`SweepConfig` to types.py too (preferred if bodies use ctx broadly — decide from the source, document the choice). cli.py + iteration_safety import SweepConfig/SweepContext FROM runner — keep re-export aliases in runner either way.
- [ ] **Step 3:** runner imports the moved functions under old private names; delete originals.
- [ ] **Step 4:** Gates — `uv run pytest tests/executor/sweep -q` green; `uv run mypy ralph_executor`; `uv run ruff check ralph_executor tests` (run `--fix` for import formatting).
- [ ] **Step 5:** Commit — `"refactor(sweep): move SweepPbiError to types; extract event emission to events.py"`

### Task 2: Create sweep/feedback_emit.py

**Files:**
- Create: `ralph_executor/sweep/feedback_emit.py`
- Modify: `ralph_executor/sweep/runner.py`

- [ ] **Step 1:** Create the module ("Two-phase feedback-PBI emission: write the bundle directory, then persist sidecar state; either failure rolls the directory back so the next sweep can retry."): move `_emit_feedback_pbi` (494–556) and `_read_original_summary` (747–758) VERBATIM as public `emit_feedback_pbi` / `read_original_summary`, plus new private `_cleanup_partial_feedback_dir(target_dir: Path) -> None` wrapping the `shutil.rmtree(target_dir, ignore_errors=True)` call — both except blocks call it; their distinct `SweepPbiError` messages stay byte-identical. History-append dependency: the body calls `_append_history` (still in runner at this point — circular!). Resolution: move `_append_history` HERE? No — actions needs it too. Instead `emit_feedback_pbi` takes an `append_history: Callable[[Path, str], None]` parameter? Simpler: Task 2 moves `_append_history` and `_move_with_history` into a tiny `sweep/history.py` FIRST (both verbatim, public names `append_history`/`move_with_history`), then feedback_emit imports append_history from history, and runner/actions import both. Adjust: this task creates BOTH `sweep/history.py` and `sweep/feedback_emit.py` in that order.
- [ ] **Step 2:** runner re-imports everything moved under old private names; delete originals.
- [ ] **Step 3:** Gates — sweep suite + mypy + ruff. The feedback-round tests (test_runner.py:171–246) and repeat-sweep no-regen test are the behavior lock.
- [ ] **Step 4:** Commit — `"refactor(sweep): extract history primitives and feedback-PBI emission"`

### Task 3: Create sweep/actions.py

**Files:**
- Create: `ralph_executor/sweep/actions.py`
- Modify: `ralph_executor/sweep/runner.py`

- [ ] **Step 1:** Create the module ("Executes sweep decisions: folder moves, reviewer pings, feedback emission, and PR merge routing."): move `_dispatch` (415–454), `_invoke_merge_pr` (662–705), `_dispatch_merge_pr` (707–745) VERBATIM as public `dispatch` / `_invoke_merge_pr` (stays private) / `dispatch_merge_pr`. Imports: history (move/append), feedback_emit (emit_feedback_pbi), events (emit_pr_merged_and_pbi_closed), types (Action, SweepPbiError), `_per_pbi_subprocess_overrides` — called by `_dispatch_merge_pr` but LIVES in runner (reconcile seam): pass it through? No — `dispatch_merge_pr` receives `subprocess_overrides: tuple[dict | None, str]` precomputed, OR actions imports it from runner (CIRCULAR — forbidden). Read the call sites: _dispatch_merge_pr calls `_per_pbi_subprocess_overrides(target_info, ctx)` at line 723. Cleanest: move `_per_pbi_subprocess_overrides` to types.py? It's pure (no I/O) per the investigator — move it to `sweep/target.py` (subprocess/repo-name resolution fits target-repo concerns) as public `per_pbi_subprocess_overrides`, keep `_per_pbi_subprocess_overrides` alias in runner so reconcile.py:35's import keeps working unchanged. Then actions imports from target.
- [ ] **Step 2:** runner re-imports `dispatch` under `_dispatch`; deletes originals.
- [ ] **Step 3:** Gates — sweep suite (the 3 auto-merge exit-code tests + terminal-action tests are the lock) + mypy + ruff.
- [ ] **Step 4:** Commit — `"refactor(sweep): extract action execution and merge routing to actions.py"`

### Task 4: Slim run() and _process_pbi in-module

**Files:**
- Modify: `ralph_executor/sweep/runner.py`

- [ ] **Step 1:** In-module extraction (no new files): `run()`'s three passes become `_process_pending_pass(ctx) -> tuple[list[PbiActionRecord], list[str]]`, `_reconcile_orphans_pass(ctx, ...)`, `_reconcile_stale_current_pass(ctx, ...)` — bodies verbatim from the respective regions (221–282), run() becomes a <25-line composer. `_process_pbi`'s phases split into `_fetch_pbi_state(pbi_dir, ctx)` (PR-id/attempts/sidecar/target reads + snapshot fetch) and `_track_ci_transition(...)` (green→red emit + sidecar persist, 324–339), with `_process_pbi` as the <30-line pipeline: fetch → decide → track → dispatch. Locals == names rule [[57bcc63e]].
- [ ] **Step 2:** Gates — full sweep suite + mypy + ruff. Report final line counts: runner.py target <420; each new module <120.
- [ ] **Step 3:** Commit — `"refactor(sweep): run and _process_pbi become thin composers over staged passes"`

### Task 5: Full gates, PR

- [ ] **Step 1:** `uv run pytest -q` full suite; mypy; ruff — all green. `python -c "from ralph_executor.sweep import run, SweepResult; from ralph_executor.sweep.runner import SweepConfig, SweepContext"` resolves (public API intact); reconcile.py import unchanged (`git diff main -- ralph_executor/sweep/reconcile.py` empty or alias-only).
- [ ] **Step 2:** Docs sweep: `git grep -n "runner.py\|sweep/runner" docs/runbooks/ README.md` — update structural descriptions; "docs unaffected" if none.
- [ ] **Step 3:** Push `tech-debt/sweep-runner-god-functions`, PR per campaign Task 10, bot-watch to merge.
