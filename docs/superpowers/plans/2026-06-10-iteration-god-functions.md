# Split iteration.py's god functions (tech-debt: iteration-god-functions)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** `_run_ralph` (199 lines) and `_iterate_once_inner` (249 lines) become thin composers over phase helpers, IN-MODULE (no new files — the module split already happened; this finding is about the two surviving functions). Behavior identical, every monkeypatch target preserved.

**Architecture (investigator map 2026-06-10 evening):** both functions decompose along already-mapped phase boundaries with enumerated cross-phase locals and exit paths. All extraction stays inside iteration.py: new private helpers call the existing module-level names (`spawn_claude_p`, `_cleanup_work_worktree`, `_queue_repo_root`, `handle_stuck`, `ensure_clone`, `ensure_worktree`, `_run_ralph`, `_persist_iteration_writes`, `_check_cycle_detector`, `_claim_pbi`, `_run_sweep`, `move_current_to_pending_pr`, `git_ops.diff_names`...) so every test monkeypatch at `ralph_executor.iteration.<name>` keeps intercepting via call-time module-global resolution.

**Monkeypatch contract (binding — from the inventory):** patched names that the new helpers must resolve as module globals, never bind at def-time or import into helper-local scope: `spawn_claude_p` (10+ patches), `_run_ralph`, `_claim_pbi`, `_run_sweep`, `_check_cycle_detector`, `ensure_worktree`, `_cleanup_work_worktree` (4), `move_current_to_pending_pr` (3), `git_ops.diff_names` (4), `_pr_skill_scripts_path`. `iterate_once`/`run_loop` wrappers untouched. Tests import `_run_ralph` and `_run_sweep` directly — both keep their names and module-level def-ness (`_run_ralph` becomes the composer, still a real def).

**Decomposition:**

`_run_ralph` (245–443) → composer + 4 helpers (event_log open/finally-close stays in the composer):
- `_spawn_and_classify(cfg, pbi, pbi_dir_in_queue, now) -> ClaudeOutcome` — spawn phase 282–320 incl. PromptComposeError → synthetic error outcome + history append.
- `_bump_attempts_on_failure(cfg, pbi, outcome, now, event_log) -> IterationResult | None` — phase 322–374; returns the `ran_stuck` result on AttemptsExceeded (composer early-returns it), None otherwise (composer falls through). The non-overflow event append stays inside.
- `_handle_pr_created(cfg, pbi, outcome, now, event_log) -> IterationResult` — phase 376–421 (diff_names via module global, defensive empties, move_current_to_pending_pr, cleanup, events).
- `_handle_stuck_outcome(cfg, pbi, outcome, now, event_log) -> IterationResult | None` — phase 423–437; None = fall through to partial.
- Composer: spawn → bump(early return) → pr_created(return) → stuck(return-if-not-None) → error(return) → partial, inside the existing try/finally closing event_log. Target <55 lines.

`_iterate_once_inner` (511–760) → composer + 4 helpers:
- `_resume_current(cfg, current) -> tuple[PBI, IterationResult | None]` — region 555–646: target read (`_ClaimError` → empty), info parse (ValueError → None), worktree self-heal (generic-Exception clone fallback, GitCommandError → `_move_current_to_blocked_with_reason` + claim_failed result), dataclass replace. Returns (possibly-replaced PBI, early-result-or-None).
- `_execute_current(cfg, current, queue_repo, source) -> IterationResult` — region 647–700: `_run_ralph` + PushRebaseConflict return, `_persist_iteration_writes` + PushRebaseConflict return, `_check_cycle_detector` raise/return.
- `_sweep_and_pick(cfg, source, queue_repo) -> tuple[PBI | None, IterationResult | None]` — region 702–715: sweep, pick, idle result (with its cycle check) when None.
- `_claim_picked(cfg, picked, queue_repo, source) -> IterationResult` — region 717–760: the 3-catch claim + post-claim cycle check.
- Composer: halt check → pull → current? (resume → early-result? → execute) : (sweep_and_pick → early-result? → claim). Target <35 lines. The exact exception types caught per region and all 10 exit paths must survive verbatim — the map's exit-path table is the checklist.

**Guardrails:** Stage specific paths [[0547374e]]. Helpers resolve patched globals at call time (no def-time default binding) [[seam lesson, loop split T2]]. Params = exactly what bodies consume [[ffdd01f6]]; locals == names [[57bcc63e]]. New private helpers get docstrings where behavior is non-obvious (self-heal, attempt overflow) [[810c8e56]]. Gates include `ruff format --check` [[f50203d8]]. mypy strict every task.

**Confidence:** T1 93% — phase boundaries + cross-phase locals + 6 exit paths enumerated; spawn patches resolve via module global; diff-against-target tests (which patch 3 globals used by the pr_created phase) are the lock. T2 92% — deepest nesting, but all 10 exit paths and 6 catch types are tabulated; the resume self-heal tests + push-conflict tests + multi-ralph integration are the lock. T3 95% — gates + PR proven. Floor 92% met.

---

### Task 1: Split _run_ralph

**Files:**
- Modify: `ralph_executor/iteration.py`

- [ ] **Step 1:** Extract the 4 helpers (bodies verbatim from the mapped regions; place directly above `_run_ralph`); rewrite `_run_ralph` as the composer preserving: the try/finally event_log lifecycle, the exact early-return order (attempt-bump's ran_stuck → pr_created → stuck-non-None → error → partial fallthrough), and all event/history side-effect ordering.
- [ ] **Step 2:** Gates — `uv run pytest tests/executor/test_iteration.py tests/executor/test_iteration_diff_against_target.py tests/executor/test_iteration_integration.py tests/safety/test_integration_loop.py -q`; `uv run mypy ralph_executor`; `uv run ruff check ralph_executor tests`; `uv run ruff format ralph_executor` then `--check`.
- [ ] **Step 3:** Commit — `git add ralph_executor/iteration.py && git commit -m "refactor(iteration): _run_ralph becomes a composer over outcome-phase helpers"`

### Task 2: Split _iterate_once_inner

**Files:**
- Modify: `ralph_executor/iteration.py`

- [ ] **Step 1:** Extract the 4 helpers (bodies verbatim; place above `_iterate_once_inner`); rewrite the function as the composer. The exit-path table from the plan header is the verification checklist — every return/raise site must map 1:1 to the original (same result outcomes, same exception types, same order of side effects).
- [ ] **Step 2:** Gates — same selection as Task 1 PLUS `tests/executor/test_multi_ralph_integration.py` and `tests/executor/test_queue_git.py`; mypy; ruff check + format.
- [ ] **Step 3:** Commit — `"refactor(iteration): _iterate_once_inner becomes a composer over resume/execute/sweep/claim phases"`

### Task 3: Full gates, PR

- [ ] **Step 1:** `uv run pytest -q` full suite; mypy; ruff check; ruff format --check — all green. Report `wc -l ralph_executor/iteration.py` and the composer line counts (`_run_ralph` <55, `_iterate_once_inner` <35).
- [ ] **Step 2:** Docs sweep: `git grep -n "_iterate_once_inner\|_run_ralph" docs/runbooks/` — update or report unaffected.
- [ ] **Step 3:** Push `tech-debt/iteration-god-functions`, PR per campaign Task 10, bot-watch to merge.
