# Decompose claude_spawn.py (tech-debt: claude-spawn-god-module)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** claude_spawn.py (917 lines) splits into: `ralph_executor/outcome.py` (pure outcome domain — ClaudeOutcome, OutcomeKind, PrCheckState, classify_outcome), `ralph_executor/gh_queries.py` (gh subprocess wrappers — query_open_pr_via_gh, query_pr_checks, wait_for_pr_checks), and a slimmer claude_spawn.py focused on process lifecycle, with `spawn_claude_p` shrunk by extracting path-resolution and env-build helpers. Behavior identical; public API preserved via re-exports.

**Architecture (from investigator map 2026-06-10):** classify_outcome (813–917) is pure (one disk read), zero subprocess deps — clean move. The three gh functions (183–398) are self-contained subprocess wrappers with no PBI logic — clean move. spawn_claude_p's phases 1 (path resolution, 581–598) and 3 (env build, 600–637) have enumerated cross-phase locals — clean in-module extraction. No circular-import hazard: both new modules are leaf-level (outcome imports nothing local; gh_queries imports outcome for PrCheckState + subprocess_utils).

**API compatibility:** loop.py and 8 test files import `ClaudeOutcome`/`OutcomeKind`/`classify_outcome`/`spawn_claude_p` FROM claude_spawn — claude_spawn re-exports everything it moves, so NONE of those imports change.

**Monkeypatch-target strategy (the load-bearing constraint):** tests patch these module-attribute strings:
- `ralph_executor.claude_spawn._query_open_pr_via_gh` + `._wait_for_pr_checks` (spawn-level stubs, test_claude_spawn.py:409–410) — PRESERVED: claude_spawn imports the moved functions under the old private names (`from ralph_executor.gh_queries import query_open_pr_via_gh as _query_open_pr_via_gh, wait_for_pr_checks as _wait_for_pr_checks`); spawn_claude_p keeps resolving them as claude_spawn module globals, so the patches keep working unchanged.
- `ralph_executor.claude_spawn.run_text` (×4, gh-query internals), `._query_pr_checks` + `.time.sleep` (×2 each, wait-loop internals) — these test the gh functions themselves and MIGRATE with them: the affected tests move to `tests/executor/test_gh_queries.py` with targets rewritten to `ralph_executor.gh_queries.run_text` / `.query_pr_checks` / `.time.sleep`. This is module-by-module test migration, not behavior change.
- `ralph_executor.claude_spawn.popen_text` + `._kill_process_tree` — untouched (those symbols stay in claude_spawn).

**Guardrails:** Stage specific paths [[0547374e]]. mypy strict after every task [[7847b0dc]]. Suite green at every boundary. Field==local naming on any new NamedTuple [[57bcc63e]]. New modules stay leaf-level — importing loop or sweep from them is forbidden (claude_spawn.py:85–88 documents the cycle hazard).

**Confidence:** T1 93% — pure move + re-export, callers untouched, classify_outcome tests keep passing through the re-export. T2 92% — the move itself is mechanical; the risk is the patch-target migration, mitigated by the explicit target inventory above and by running the migrated tests plus the spawn-level tests at the boundary. T3 93% — in-module extraction with enumerated cross-phase locals (effective_cwd/effective_pbi_dir/env); no test patches reference the new helper names. T4 95% — gates + PR flow proven. Floor 92% met.

---

### Task 1: Create outcome.py (pure outcome domain)

**Files:**
- Create: `ralph_executor/outcome.py`
- Modify: `ralph_executor/claude_spawn.py`

- [ ] **Step 1:** Create `ralph_executor/outcome.py` with module docstring ("Outcome domain for Claude subprocess runs: result dataclass and pure classification.") containing, moved VERBATIM from claude_spawn.py: `OutcomeKind` type alias (line 51), `_STUCK_FILENAME` (53), `ClaudeOutcome` dataclass (100–109), `classify_outcome` (813–917), plus a `PrCheckState = Literal["pass", "fail", "pending", "error"]` definition (moved from claude_spawn.py line 241 — classify_outcome's signature uses it). Imports: only stdlib (`logging`, `pathlib.Path`, `typing.Literal`, `collections.abc.Callable` as the originals require). Own `log = logging.getLogger(__name__)`.
- [ ] **Step 2:** In claude_spawn.py: delete the moved code; add `from ralph_executor.outcome import ClaudeOutcome, OutcomeKind, PrCheckState, classify_outcome, _STUCK_FILENAME` (keep `_STUCK_FILENAME` import only if claude_spawn still references it — grep; spawn_claude_p does not, but verify). The gh functions still in claude_spawn keep using `PrCheckState` via this import. Re-export keeps loop.py + all test imports working unchanged.
- [ ] **Step 3:** Gates — `uv run pytest tests/executor/test_claude_spawn.py tests/executor/test_loop.py -q` green; `uv run mypy ralph_executor` clean; `uv run ruff check ralph_executor` clean (it flags unused re-export imports — silence per repo convention, e.g. `__all__` or `# noqa: F401` on the re-export line, matching how other modules re-export; check repo precedent first).
- [ ] **Step 4:** Commit — `git add ralph_executor/outcome.py ralph_executor/claude_spawn.py && git commit -m "refactor(claude_spawn): extract pure outcome domain to outcome.py"`

### Task 2: Create gh_queries.py (gh subprocess wrappers)

**Files:**
- Create: `ralph_executor/gh_queries.py`
- Create: `tests/executor/test_gh_queries.py`
- Modify: `ralph_executor/claude_spawn.py`, `tests/executor/test_claude_spawn.py`

- [ ] **Step 1:** Create `ralph_executor/gh_queries.py` ("GitHub CLI queries for PR state and required checks.") with, moved VERBATIM but renamed public: `_GH_BINARY` (stays private), `query_open_pr_via_gh` (was `_query_open_pr_via_gh`, 183–238), `query_pr_checks` (was `_query_pr_checks`, 244–346), `wait_for_pr_checks` (was `_wait_for_pr_checks`, 348–398; its internal call updated to the public name `query_pr_checks`). `PrCheckState` imported from `ralph_executor.outcome`. Imports: `json`, `logging`, `subprocess`, `time`, `run_text` from subprocess_utils. Leaf module — must not import loop/sweep/claude_spawn.
- [ ] **Step 2:** In claude_spawn.py: delete the moved functions; add `from ralph_executor.gh_queries import query_open_pr_via_gh as _query_open_pr_via_gh, wait_for_pr_checks as _wait_for_pr_checks` (old private names preserved as module globals so test patches at claude_spawn._query_open_pr_via_gh/._wait_for_pr_checks still intercept spawn_claude_p). Remove now-dead imports (e.g. `json`/`run_text` if nothing else in claude_spawn uses them — grep before pruning).
- [ ] **Step 3:** Migrate tests: move from test_claude_spawn.py to new `tests/executor/test_gh_queries.py` exactly the tests that exercise the gh functions directly — `test_query_pr_checks_returns_pass`, `test_query_pr_checks_returns_fail`, `test_query_pr_checks_pending_no_json`, `test_query_pr_checks_handles_timeout` (patch target `ralph_executor.claude_spawn.run_text` → `ralph_executor.gh_queries.run_text`), `test_wait_for_pr_checks_polls_until_pass`, `test_wait_for_pr_checks_returns_pending_on_budget_exhausted` (targets `._query_pr_checks` → `ralph_executor.gh_queries.query_pr_checks`, `.time.sleep` → `ralph_executor.gh_queries.time.sleep`), plus any `_query_open_pr_via_gh`-direct tests. Imports in the new file: `from ralph_executor.gh_queries import query_pr_checks, wait_for_pr_checks, query_open_pr_via_gh`. Leave the spawn-level tests (409–410 patches) in test_claude_spawn.py UNCHANGED.
- [ ] **Step 4:** Gates — `uv run pytest tests/executor/test_gh_queries.py tests/executor/test_claude_spawn.py -q` green; mypy + ruff clean.
- [ ] **Step 5:** Commit — `git add ralph_executor/gh_queries.py ralph_executor/claude_spawn.py tests/executor/test_gh_queries.py tests/executor/test_claude_spawn.py && git commit -m "refactor(claude_spawn): extract gh PR queries to gh_queries.py"`

### Task 3: Shrink spawn_claude_p (in-module helpers)

**Files:**
- Modify: `ralph_executor/claude_spawn.py`

- [ ] **Step 1:** Extract two helpers placed directly above `spawn_claude_p`:
  - `_resolve_spawn_paths(cfg: ExecutorConfig, pbi: PBI, cwd: Path | None, pbi_dir: Path | None) -> tuple[Path, Path]` — phase 1 (581–598) verbatim: returns `(effective_cwd, effective_pbi_dir)`, raising the same ConfigError(s) on missing paths.
  - `_build_subprocess_env(cfg: ExecutorConfig, pbi: PBI, effective_pbi_dir: Path) -> dict[str, str]` — phase 3 (600–637) verbatim: os.environ copy + RALPH_PBI_DIR, ANTHROPIC_API_KEY, BASH_MAX_TIMEOUT_MS, GH_OWNER, BETTER_MEMORY_PROJECT, RALPH_AUTOBUG_DEPTH wiring. (Verify the phase's actual inputs by reading the block — if it reads more than cfg/pbi/effective_pbi_dir, add exactly those parameters.)
  - spawn_claude_p phases 1 and 3 become two calls binding the same locals.
- [ ] **Step 2:** Gates — `uv run pytest tests/executor/test_claude_spawn.py tests/executor/test_claude_spawn_no_repo_path_fallback.py tests/executor/autobug/integration -q` green (the autobug e2e tests capture the env wiring); mypy + ruff clean.
- [ ] **Step 3:** Commit — `git add ralph_executor/claude_spawn.py && git commit -m "refactor(claude_spawn): extract path-resolution and env-build helpers from spawn_claude_p"`

### Task 4: Full gates, PR

- [ ] **Step 1:** `uv run pytest -q` full suite; `uv run ruff check scripts skills tests ralph_executor`; `uv run mypy ralph_executor` — all green. Report claude_spawn.py final line count (`wc -l`).
- [ ] **Step 2:** Docs sweep: `git grep -n "claude_spawn" docs/ README.md` — update structural descriptions if any describe the module layout; "docs unaffected" if behavior-level only.
- [ ] **Step 3:** Push `tech-debt/claude-spawn-god-module`, PR per campaign plan Task 10, bot-watch to merge.
