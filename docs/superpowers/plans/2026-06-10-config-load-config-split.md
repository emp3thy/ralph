# Decompose config.load_config() (tech-debt: config-load-config-god-function)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** `load_config()` (390 lines, 20 branches) becomes a <100-line orchestrator delegating to single-concern `_resolve_*` helpers; behavior identical, every existing test untouched and green.

**Architecture:** Investigator scan 2026-06-10 mapped load_config into 10 sequential blocks (line ranges below); blocks 2–9 are mutually independent — each reads only `toml_overrides` + env and produces locals consumed solely by the final ExecutorConfig construction (block 10). Extraction is therefore mechanical: one helper per block (block 7 split into three — it covers 137 lines of unrelated knobs), each helper taking `(toml_overrides: Mapping[str, Any], source_label: str)` and returning a `NamedTuple` of its values. `load_config()` keeps the exact block ORDER so the first-failing-validation `ConfigError` is unchanged on any input.

**The behavior lock:** ~180 existing tests across `tests/executor/test_config.py`, `test_config_toml.py`, `test_config_autobug.py` cover every block (queue_repo URL validation, branch rules, permission-mode enum, use_worktrees rejection, workspace parent check, instance-id precedence, autobug positive-value checks, env-beats-TOML precedence). NO test file changes in this refactor — if a test needs editing, the refactor broke behavior; stop and fix the helper instead.

**Guardrails:** Stage specific paths [[0547374e]]. mypy strict gate after every task — it catches signature/return-type slips in extractions [[7847b0dc]]. Suite green at every task boundary. No API change: `load_config()` signature, `ExecutorConfig`, `ConfigError` messages all byte-identical.

**Confidence:** T1–T5 93% each — mechanical block moves with exact pre-mapped line ranges, independent blocks, dense test net + mypy strict; order preserved so first-error semantics hold. T6 95% — gates + PR flow proven. Floor 92% met.

**Helper signature pattern** (worked example for block 2; all helpers follow this exact shape):

```python
class _QueueRepoSettings(NamedTuple):
    queue_repo: str


def _resolve_queue_repo(toml_overrides: Mapping[str, Any], source_label: str) -> _QueueRepoSettings:
    # body = config.py lines 637-654 moved verbatim; `return _QueueRepoSettings(queue_repo)` at the end
    ...
```

In `load_config()` the block is replaced by:

```python
queue_repo = _resolve_queue_repo(toml_overrides, source_label).queue_repo
```

Helpers live directly above `load_config()` in the module, in block order. Single-value groups may skip the NamedTuple and return the bare value only when ONE value comes back (block 2); multi-value groups always use the NamedTuple. If a block's body references module-level constants or `_resolve_*` primitives, those stay where they are — only the block body moves.

---

### Task 1: Extract queue_repo + branches (blocks 2–3)

**Files:**
- Modify: `ralph_executor/config.py`

- [ ] **Step 1:** Add `NamedTuple` to the `typing` import if absent. Extract:
  - `_resolve_queue_repo(toml_overrides, source_label) -> str` — body = lines 637–654 verbatim (type check, existence check, `parse_target_repo` HTTPS validation), returns the bare `queue_repo` string.
  - `class _BranchSettings(NamedTuple): main_branch: str; queue_branch: str` and `_resolve_branches(toml_overrides, source_label) -> _BranchSettings` — body = lines 655–678 verbatim (main_branch `_resolve_str`; queue_branch `_resolve_str` + empty/`HEAD`/`refs/heads/` validations).
  - Replace the two blocks in `load_config()` with the two calls, unpacking into the same local names.
- [ ] **Step 2:** `uv run pytest tests/executor/test_config.py tests/executor/test_config_toml.py -q` green; `uv run mypy ralph_executor` clean.
- [ ] **Step 3:** Commit — `git add ralph_executor/config.py && git commit -m "refactor(config): extract queue_repo and branch resolution from load_config"`

### Task 2: Extract runtime knobs + claude settings (blocks 4–5)

**Files:**
- Modify: `ralph_executor/config.py`

- [ ] **Step 1:** Extract:
  - `class _RuntimeKnobs(NamedTuple): max_attempts: int; log_level: int; iteration_sleep_seconds: float` and `_resolve_runtime_knobs(...)` — body = lines 679–697 verbatim.
  - `class _ClaudeSettings(NamedTuple): claude_binary: str; claude_permission_mode: str` and `_resolve_claude_settings(...)` — body = lines 698–716 verbatim (incl. `_VALID_CLAUDE_PERMISSION_MODES` check).
  - Replace blocks with calls in `load_config()`.
- [ ] **Step 2:** Same gates as Task 1 Step 2.
- [ ] **Step 3:** Commit — `"refactor(config): extract runtime and claude settings from load_config"`

### Task 3: Extract git-host/auth settings (block 6)

**Files:**
- Modify: `ralph_executor/config.py`

- [ ] **Step 1:** Extract `class _HostSettings(NamedTuple): git_host: str; gh_owner: str; ado_org_url: str; ado_project: str; halt_webhook: str` and `_resolve_host_settings(...)` — body = lines 717–751 verbatim (five `_resolve_str` calls with their env names and empty-string defaults). Replace block with call.
- [ ] **Step 2:** Same gates.
- [ ] **Step 3:** Commit — `"refactor(config): extract git-host and auth settings from load_config"`

### Task 4: Extract sweep/safety knobs (block 7, three helpers)

**Files:**
- Modify: `ralph_executor/config.py`

- [ ] **Step 1:** Extract three helpers, splitting lines 752–889 verbatim by concern:
  - `class _TimeoutSettings(NamedTuple): bot_author_email: str; stale_days: int; bash_max_timeout_ms: int; claude_session_timeout_seconds: int; pr_check_poll_max_attempts: int; pr_check_poll_interval_seconds: float` from lines 752–804.
  - `class _WorkspaceSettings(NamedTuple): use_worktrees: bool; auto_merge_clean_prs: bool; workspace_root: Path` from lines 805–848 (incl. the use_worktrees=False rejection and the workspace parent-directory existence check).
  - `class _SweepThresholds(NamedTuple): same_file_min_prs: int; same_file_window_hours: float; watch_mode: bool; idle_exit_threshold: int` from lines 849–889.
  - Replace the block with the three calls IN THE SAME ORDER (preserves first-error semantics within the former block).
- [ ] **Step 2:** Same gates.
- [ ] **Step 3:** Commit — `"refactor(config): extract sweep, workspace, and timeout settings from load_config"`

### Task 5: Extract instance-id + autobug (blocks 8–9)

**Files:**
- Modify: `ralph_executor/config.py`

- [ ] **Step 1:** Extract:
  - `_resolve_instance_id_setting(toml_overrides, source_label) -> str` — body = lines 891–909 verbatim (env read, TOML type check, `resolve_instance_id` call with `socket.gethostname()`); bare-str return.
  - `class _AutobugSettings(NamedTuple): autobug_enabled: bool; autobug_rate_max: int; autobug_rate_window_minutes: int; autobug_dedup_done_window_days: int; autobug_severity_python_crash: str; autobug_severity_subprocess_crash: str` and `_resolve_autobug(...)` — body = lines 911–966 verbatim.
  - Replace blocks with calls. `load_config()` is now: setup (block 1) → 9 helper calls → ExecutorConfig construction. Verify it is under 100 lines (`grep -n "def load_config" ralph_executor/config.py` then count to the closing line).
- [ ] **Step 2:** `uv run pytest tests/executor/test_config.py tests/executor/test_config_toml.py tests/executor/test_config_autobug.py -q` green; mypy clean.
- [ ] **Step 3:** Commit — `"refactor(config): extract instance-id and autobug settings; load_config is now an orchestrator"`

### Task 6: Full gates, PR

- [ ] **Step 1:** `uv run pytest -q` full suite; `uv run ruff check scripts skills tests ralph_executor`; `uv run mypy ralph_executor` — all green. Confirm zero diffs under `tests/` (`git status tests/` clean).
- [ ] **Step 2:** Docs sweep: `git grep -n "load_config" docs/ README.md` — update any structural description if one exists; "docs unaffected" if none.
- [ ] **Step 3:** Push `tech-debt/config-load-config-god-function`, PR per campaign plan Task 10, bot-watch to merge.
