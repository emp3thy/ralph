# Sweep auto-merges clean PRs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sweep optionally auto-merges PRs that GitHub reports as `mergeable_state == "clean"`, gated by a new TOML config flag `auto_merge_clean_prs` (default `false`). Adds `merge_pr` sub-op to `pr-github` (new exit code 4 for race / refused-by-host) and exposes raw `mergeable_state` from `show.py`. `PrSnapshot.merge_state: str` carries the raw GitHub value. `Action.MERGE_PR` enum value triggers the subprocess merge from sweep's act path.

**Spec:** `docs/superpowers/specs/2026-05-27-sweep-auto-merge-clean-prs-design.md`

**Tech Stack:** Python 3.12, `requests`, `responses` (test mocking — already a dep, used in `tests/skills/test_pr_github.py`), `subprocess.run`, `dataclasses`, `StrEnum`. pytest, ruff, mypy strict.

---

## Confidence per task

All tasks ≥ 90%. Cross-checked against actual line numbers and patterns in the repo.

| Task | % | Notes |
|---|---|---|
| 1. Expose `mergeable_state` from `show.py` | 95% | `_map_merge_status` already reads `mergeable_state`; just thread the raw value into the output dict. Tests append to existing `test_show_*` cluster in `tests/skills/test_pr_github.py`. |
| 2. `merge_pr.py` skill script | 92% | Mirrors `create_pr.py` argparse + `client.put_rest` (need to add put_rest to `_common.py` — verified missing). 405/409 → exit 4 branch new. Tests use `@responses.activate` + `responses.PUT`. |
| 3. SKILL.md documents `merge_pr` | 95% | Append a new `### merge_pr` section under existing operations + bump "five operations" → "six operations" in the frontmatter description. |
| 4. `ExecutorConfig.auto_merge_clean_prs` | 95% | `_resolve_bool` exists; add field + TOML key + env name + `CONFIG_TOML_STUB` entry. Mirrors `use_worktrees` (verified). |
| 5. `PrSnapshot.merge_state` + `pr_state` parse | 95% | Add field to frozen dataclass; parse in `fetch` via `str(show_dict.get("mergeable_state", ""))`. |
| 6. `Action.MERGE_PR` + `decide_action` | 94% | New enum value + new predicate branch. SweepConfig grows `auto_merge_clean_prs: bool = False`. Ordering after new-comments check, before stale check. |
| 7. Runner dispatch + subprocess invoke | 92% | New `_invoke_merge_pr` helper (subprocess pattern mirrors `pr_state._invoke_skill`). Map exit codes to behaviour per spec. Existing `_move_with_history` handles the done move. |
| 8. Wire flag through `loop._run_sweep` | 95% | Pass `cfg.auto_merge_clean_prs` into `SweepConfig` construction. One-line plumbing change. |
| 9. Conftest shim + runner tests | 93% | Add `merge_pr.py` shim to `fake_ado_pr_skill`. Extend `register_pr` to accept `mergeable_state`. Three new test cases (exit 0, exit 4, exit 3). |
| 10. Full-suite green + smoke | 91% | `pytest`, `ruff check`, `ruff format --check`, `mypy ralph_executor scripts skills tests` clean. |

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `skills/pr-github/scripts/show.py` | Modify | Emit `mergeable_state` field verbatim alongside existing `merge_status`. |
| `skills/pr-github/scripts/merge_pr.py` | Create | argparse + `PUT /pulls/{n}/merge` + exit 0/2/3/4. |
| `skills/pr-github/scripts/_common.py` | Modify | Add `put_rest` to `GitHubClient`. Add a `merge_pr_raise_class_for_status` if needed (or fold the 405/409 branching into the skill). |
| `skills/pr-github/SKILL.md` | Modify | Document `merge_pr` operation; bump op count to six. |
| `ralph_executor/config.py` | Modify | `auto_merge_clean_prs: bool = False` + TOML key + env name. |
| `ralph_executor/setup_cmds.py` | Modify | `CONFIG_TOML_STUB` documents the new key. |
| `ralph_executor/sweep/types.py` | Modify | `Action.MERGE_PR` + `PrSnapshot.merge_state: str`. |
| `ralph_executor/sweep/pr_state.py` | Modify | Parse `merge_state` from show payload in `fetch`. |
| `ralph_executor/sweep/runner.py` | Modify | `SweepConfig.auto_merge_clean_prs`. `decide_action` predicate. Dispatch branch + `_invoke_merge_pr` helper. |
| `ralph_executor/loop.py` | Modify | Wire `cfg.auto_merge_clean_prs` into `SweepConfig`. |
| `tests/skills/test_pr_github.py` | Modify | `show` tests assert `mergeable_state`. New cluster for `merge_pr` happy / 405 / 409 / 422 / 500 / bad merge method. |
| `tests/executor/sweep/conftest.py` | Modify | Add `merge_pr.py` shim. Extend `register_pr` to accept `mergeable_state`. |
| `tests/executor/sweep/test_decide_action.py` | Modify | Cases: flag off + clean → NOOP. Flag on + clean → MERGE_PR. Flag on + dirty → not MERGE_PR. Flag on + clean + new comments → CREATE_FEEDBACK_PBI. |
| `tests/executor/sweep/test_runner.py` | Modify | End-to-end: flag on + clean + shim exit 0 → done. shim exit 4 → stays. shim exit 3 → stays + WARNING. |
| `tests/executor/test_config.py` | Modify | `auto_merge_clean_prs` env (true/false/invalid/default). |
| `tests/executor/test_config_toml.py` | Modify | `auto_merge_clean_prs` TOML default + on. |

---

## Task 1: Expose `mergeable_state` from `show.py`

**Files:**
- Modify: `skills/pr-github/scripts/show.py`
- Modify: `tests/skills/test_pr_github.py`

- [x] **Step 1.1: Add `mergeable_state` to the show.py output dict**

  In `skills/pr-github/scripts/show.py`'s `main()`, in the `output` dict (around line 246), add:
  ```python
  "mergeable_state": (
      str(pr["mergeable_state"]) if isinstance(pr.get("mergeable_state"), str) else None
  ),
  ```
  This preserves GitHub's raw string verbatim, or emits JSON `null` when GitHub returned `null` (async check pending).

- [x] **Step 1.2: Update the show.py module docstring**

  Mention `mergeable_state` in the output description.

- [x] **Step 1.3: Add tests**

  In `tests/skills/test_pr_github.py`:
  - `test_show_happy_path` (existing): assert `payload["mergeable_state"] == "clean"` (the sample payload already has that field).
  - New `test_show_mergeable_state_null`: when GitHub returns `mergeable_state: null`, assert `payload["mergeable_state"] is None`.

- [x] **Step 1.4: Run `uv run pytest tests/skills/test_pr_github.py -k show` until green.**

---

## Task 2: `merge_pr.py` skill script

**Files:**
- Modify: `skills/pr-github/scripts/_common.py`
- Create: `skills/pr-github/scripts/merge_pr.py`
- Modify: `tests/skills/test_pr_github.py`

- [x] **Step 2.1: Add `put_rest` to `GitHubClient` in `_common.py`**

  Mirror the existing `post_rest` shape:
  ```python
  def put_rest(self, path: str, body: dict[str, Any]) -> Any:
      url = f"{GITHUB_API}{path}"
      response = self.session.put(url, headers=self._headers(), json=body, timeout=30)
      return self._read_rest_response(response)
  ```

- [x] **Step 2.2: Add a new `RaceError` exception class to `_common.py`**

  Distinct from `HttpError` so the skill can map 405/409 specifically to exit 4 without parsing message strings.

  ```python
  class RaceError(RuntimeError):
      """Raised on 405 Method Not Allowed or 409 Conflict from GitHub. Mapped to exit code 4."""
  ```

  Also add a `handle_race_error(error: RaceError) -> int` helper that prints and returns 4.

  Modify `_read_rest_response` to raise `RaceError` (not `HttpError`) when `response.status_code in (405, 409)`. (Verify no existing call site relies on those codes producing `HttpError` — `grep -n "405\|409" skills/`. Currently neither code is referenced.)

- [x] **Step 2.3: Create `skills/pr-github/scripts/merge_pr.py`**

  Mirror `create_pr.py` for structure. Argparse:
  ```python
  parser.add_argument("--repo", required=True)
  parser.add_argument("--pr-id", required=True)
  parser.add_argument("--merge-method", choices=("merge", "squash", "rebase"), default="squash")
  parser.add_argument("--commit-title", default=None)
  parser.add_argument("--commit-message", default=None)
  ```

  Body:
  ```python
  body: dict[str, Any] = {"merge_method": args.merge_method}
  if args.commit_title is not None:
      body["commit_title"] = args.commit_title
  if args.commit_message is not None:
      body["commit_message"] = args.commit_message
  payload = client.put_rest(f"/repos/{client.owner}/{repo}/pulls/{pr_id}/merge", body)
  ```

  Output:
  ```python
  output = {
      "pr_id": pr_id,
      "repo": repo,
      "merged": bool(payload.get("merged", True)) if isinstance(payload, dict) else True,
      "sha": str(payload["sha"]) if isinstance(payload, dict) and isinstance(payload.get("sha"), str) else None,
      "message": str(payload.get("message", "")) if isinstance(payload, dict) else "",
  }
  ```

  `main`'s except chain: `FatalError → 2`; `RaceError → 4`; `HttpError → 3`.

- [x] **Step 2.4: Add tests in `tests/skills/test_pr_github.py`**

  New cluster `# merge_pr tests`:
  - `test_merge_pr_happy_path`: 200 response with `{"sha": "abc", "merged": true, "message": "Pull Request successfully merged"}`. Assert exit 0 + JSON has `merged=True`, `sha="abc"`.
  - `test_merge_pr_method_not_allowed_exits_four`: 405 response. Assert exit 4.
  - `test_merge_pr_conflict_exits_four`: 409 response. Assert exit 4.
  - `test_merge_pr_unprocessable_exits_three`: 422 response. Assert exit 3.
  - `test_merge_pr_server_error_exits_three`: 500 response. Assert exit 3.
  - `test_merge_pr_bad_merge_method_exits_two`: `--merge-method xyz` → argparse rejects → exit 2.

  Use `responses.PUT` (or `responses.add(responses.PUT, ...)`). Endpoint URL builder: `f"{GH_API}/repos/{OWNER}/{REPO}/pulls/{PR_ID}/merge"`.

- [x] **Step 2.5: `uv run pytest tests/skills/test_pr_github.py -k merge_pr` until green.**

---

## Task 3: SKILL.md documents `merge_pr`

**Files:**
- Modify: `skills/pr-github/SKILL.md`

- [x] **Step 3.1: Update the frontmatter description**

  Replace "Five operations — create-pr, read-threads, reply, set-status, show" with "Six operations — create-pr, read-threads, reply, set-status, show, merge_pr". (And update the prose "contains five Python entry scripts" to "six".)

- [x] **Step 3.2: Add a `### merge_pr — merge a clean PR` section**

  After the existing `### show` section (around line 207). Document: usage example, flag table, return JSON shape, exit codes including 4. Mirror tone of other sections.

- [x] **Step 3.3: Update the "Exit codes (every operation)" table**

  Add a row for exit code 4: "Race / refused-by-host: GitHub returned 405 Method Not Allowed or 409 Conflict on a merge attempt. Only emitted by `merge_pr`."

---

## Task 4: `ExecutorConfig.auto_merge_clean_prs`

**Files:**
- Modify: `ralph_executor/config.py`
- Modify: `ralph_executor/setup_cmds.py`
- Modify: `tests/executor/test_config.py`
- Modify: `tests/executor/test_config_toml.py`

- [ ] **Step 4.1: Add the field to `ExecutorConfig`**

  After `bash_max_timeout_ms` in the dataclass (around line 168):
  ```python
  # When True, sweep auto-merges PRs that GitHub reports as
  # mergeable_state == "clean" (CI green + approvals + no conflicts +
  # branch up-to-date). Default False — operators opt in.
  auto_merge_clean_prs: bool = False
  ```

- [ ] **Step 4.2: Add TOML key + env to `load_config`**

  Add `"auto_merge_clean_prs"` to `_TOML_KNOWN_KEYS`. Add the `_resolve_bool` call after `use_worktrees`:
  ```python
  auto_merge_clean_prs = _resolve_bool(
      name="auto_merge_clean_prs",
      env_name="RALPH_AUTO_MERGE_CLEAN_PRS",
      toml_value=toml_overrides.get("auto_merge_clean_prs"),
      default=False,
      source_label=source_label,
  )
  ```
  Pass it through to the `ExecutorConfig(...)` constructor at the bottom.

- [ ] **Step 4.3: Update `CONFIG_TOML_STUB` in `setup_cmds.py`**

  Add a commented block (after `bash_max_timeout_ms`):
  ```toml
  # Sweep auto-merge: when true, sweep auto-merges PRs that GitHub reports
  # as mergeable_state == "clean" (CI green + approvals + no conflicts +
  # branch up-to-date). Default false. Opt in carefully — merging a PR
  # that a human still wanted to review is unrecoverable.
  # auto_merge_clean_prs = false
  ```

- [ ] **Step 4.4: Tests**

  In `tests/executor/test_config.py`:
  - `test_load_config_auto_merge_clean_prs_default_false`
  - `test_load_config_auto_merge_clean_prs_env_true`
  - `test_load_config_auto_merge_clean_prs_env_invalid` (raises ConfigError)

  In `tests/executor/test_config_toml.py`:
  - `test_auto_merge_clean_prs_default_false`
  - `test_auto_merge_clean_prs_toml_true`
  - `test_auto_merge_clean_prs_env_wins_over_toml`
  - `test_auto_merge_clean_prs_toml_wrong_type_raises`

- [ ] **Step 4.5: `uv run pytest tests/executor/test_config.py tests/executor/test_config_toml.py` until green.**

---

## Task 5: `PrSnapshot.merge_state` + `pr_state` parse

**Files:**
- Modify: `ralph_executor/sweep/types.py`
- Modify: `ralph_executor/sweep/pr_state.py`
- Modify: `tests/executor/sweep/test_pr_state.py`

- [ ] **Step 5.1: Add `merge_state: str` to `PrSnapshot`**

  In `ralph_executor/sweep/types.py`, after `target_branch`:
  ```python
  merge_state: str = ""
  ```
  Empty string is the "not present / null" sentinel — same convention as `last_ci_status`.

- [ ] **Step 5.2: Parse in `pr_state.fetch`**

  In `ralph_executor/sweep/pr_state.py`, in `fetch()`'s `PrSnapshot(...)` constructor call, add:
  ```python
  merge_state=str(show_dict.get("mergeable_state") or ""),
  ```
  (Use `or ""` so a `None` from GitHub becomes `""`.)

- [ ] **Step 5.3: Tests**

  In `tests/executor/sweep/test_pr_state.py`, add cases:
  - `mergeable_state="clean"` → `snapshot.merge_state == "clean"`
  - `mergeable_state=null` → `snapshot.merge_state == ""`
  - `mergeable_state` missing → `snapshot.merge_state == ""`

- [ ] **Step 5.4: `uv run pytest tests/executor/sweep/test_pr_state.py` until green.**

---

## Task 6: `Action.MERGE_PR` + `decide_action`

**Files:**
- Modify: `ralph_executor/sweep/types.py`
- Modify: `ralph_executor/sweep/runner.py`
- Modify: `tests/executor/sweep/test_decide_action.py`

- [ ] **Step 6.1: Add `MERGE_PR` enum value**

  In `ralph_executor/sweep/types.py`, in the `Action` StrEnum:
  ```python
  MERGE_PR = "merge-pr"
  ```

- [ ] **Step 6.2: Add `auto_merge_clean_prs` to `SweepConfig`**

  In `ralph_executor/sweep/runner.py`'s `SweepConfig`:
  ```python
  auto_merge_clean_prs: bool = False
  ```

- [ ] **Step 6.3: Add the predicate branch to `decide_action`**

  In `decide_action`, AFTER the new-comments check, BEFORE the stale check:
  ```python
  if config.auto_merge_clean_prs and pr.merge_state == "clean":
      return Decision(action=Action.MERGE_PR, reason="auto-merging clean PR")
  ```

- [ ] **Step 6.4: Tests**

  In `tests/executor/sweep/test_decide_action.py`, update `_snapshot` to accept `merge_state` kwarg (default ""). Add:
  - `test_flag_off_clean_is_noop`: `auto_merge_clean_prs=False`, `merge_state="clean"` → `NOOP`.
  - `test_flag_on_clean_yields_merge_pr`: `auto_merge_clean_prs=True`, `merge_state="clean"` → `MERGE_PR`.
  - `test_flag_on_dirty_is_not_merge_pr`: `auto_merge_clean_prs=True`, `merge_state="dirty"` → `NOOP` (or matches stale/etc., but NOT `MERGE_PR`).
  - `test_new_comments_preempt_merge_pr`: `auto_merge_clean_prs=True`, `merge_state="clean"`, new active human comment → `CREATE_FEEDBACK_PBI`.

- [ ] **Step 6.5: `uv run pytest tests/executor/sweep/test_decide_action.py` until green.**

---

## Task 7: Runner dispatch + subprocess invoke

**Files:**
- Modify: `ralph_executor/sweep/runner.py`

- [ ] **Step 7.1: Add `_invoke_merge_pr` helper**

  Local to `runner.py`. Mirror `pr_state._invoke_skill` shape (`subprocess.run` with `check=False`, `capture_output=True`, `sys.executable`). Take `pbi_dir`, `pr_id`, `repo_name`, `skill_scripts_path` and return the integer exit code:

  ```python
  def _invoke_merge_pr(*, pr_id: int, ctx: SweepContext) -> int:
      script = ctx.ado_pr_scripts_path / "merge_pr.py"
      if not script.is_file():
          raise _SweepPbiError(f"merge_pr.py not found at {script}")
      result = subprocess.run(  # noqa: S603
          [sys.executable, str(script),
           "--repo", ctx.repo_name or ctx.queue_root.parent.name,
           "--pr-id", str(pr_id)],
          check=False, capture_output=True, text=True,
      )
      if result.returncode not in (0, 3, 4):
          # Exit 2 = validation; raise so the per-PBI guard catches it.
          if result.returncode == 2:
              raise _SweepPbiError(f"merge_pr exit 2: {result.stderr.strip()}")
      return result.returncode
  ```

  (Import `subprocess` and `sys` at the top.)

- [ ] **Step 7.2: Add the dispatch branch**

  In `_dispatch`, after `MOVE_TO_DONE` branch:
  ```python
  elif a is Action.MERGE_PR:
      rc = _invoke_merge_pr(pr_id=snapshot.pr_id, ctx=ctx)
      if rc == 0:
          _move_with_history(pbi_dir, ctx.queue_root / "done" / pbi_dir.name,
                             "PR auto-merged by sweep", ctx)
          _emit_pr_merged_and_pbi_closed(pbi_id=pbi_dir.name, snapshot=snapshot, ctx=ctx)
      elif rc == 4:
          log.info("sweep: merge_pr refused for %s (race / not-ready); retry next iter", pbi_dir.name)
      elif rc == 3:
          log.warning("sweep: merge_pr GitHub error for %s; retry next iter", pbi_dir.name)
      else:
          raise _SweepPbiError(f"merge_pr returned unexpected exit {rc}")
  ```

- [ ] **Step 7.3: `uv run pytest tests/executor/sweep/test_runner.py` until existing tests stay green.**

---

## Task 8: Wire flag through `loop._run_sweep`

**Files:**
- Modify: `ralph_executor/loop.py`

- [ ] **Step 8.1: Pass `cfg.auto_merge_clean_prs` to `SweepConfig`**

  In `loop._run_sweep`, in the `SweepConfig(...)` construction:
  ```python
  sweep_cfg = SweepConfig(
      ralph_author_email=cfg.bot_author_email,
      max_attempts=cfg.max_attempts,
      stale_threshold=timedelta(days=cfg.stale_days),
      now=datetime.now(tz=UTC),
      auto_merge_clean_prs=cfg.auto_merge_clean_prs,
  )
  ```

- [ ] **Step 8.2: `uv run pytest tests/executor/test_loop.py tests/executor/test_loop_integration.py` until green.**

---

## Task 9: Conftest shim + runner tests

**Files:**
- Modify: `tests/executor/sweep/conftest.py`
- Modify: `tests/executor/sweep/test_runner.py`

- [ ] **Step 9.1: Add `merge_pr.py` shim to `fake_ado_pr_skill`**

  In `tests/executor/sweep/conftest.py`, append after `read_threads.py` shim:
  ```python
  (skill_dir / "merge_pr.py").write_text(
      textwrap.dedent(
          """\
          import json, os, sys
          pr_id = sys.argv[sys.argv.index("--pr-id") + 1]
          rc = int(os.environ.get(f"SHIM_MERGE_PR_{pr_id}_EXIT", "0"))
          payload = {"pr_id": int(pr_id), "merged": rc == 0, "sha": "deadbeef"}
          print(json.dumps(payload))
          sys.exit(rc)
          """
      )
  )
  ```

- [ ] **Step 9.2: Extend `register_pr` to accept `mergeable_state`**

  Add a `mergeable_state: str = "unknown"` kwarg. Add to the `show` payload dict: `"mergeable_state": mergeable_state`.

- [ ] **Step 9.3: Add a `register_merge_pr_exit` helper**

  ```python
  def register_merge_pr_exit(monkeypatch: pytest.MonkeyPatch, pr_id: int, exit_code: int) -> None:
      monkeypatch.setenv(f"SHIM_MERGE_PR_{pr_id}_EXIT", str(exit_code))
  ```

- [ ] **Step 9.4: Add a `_ctx_with_auto_merge` test helper in `test_runner.py`**

  Builds a `SweepContext` with `auto_merge_clean_prs=True` in the embedded `SweepConfig`.

- [ ] **Step 9.5: Three new test cases in `test_runner.py`**

  - `test_auto_merge_clean_pr_lands_in_done`: shim exit 0; assert PBI lands in `done/`.
  - `test_auto_merge_race_keeps_pbi_in_pending`: shim exit 4; assert PBI still in `pending-pr/`.
  - `test_auto_merge_github_error_keeps_pbi_in_pending`: shim exit 3; assert PBI still in `pending-pr/`. Capture log at WARNING level and assert the line contains "merge_pr GitHub error".

- [ ] **Step 9.6: `uv run pytest tests/executor/sweep/test_runner.py` until green.**

---

## Task 10: Full-suite green + smoke

- [ ] **Step 10.1: `uv run pytest`** — all green.
- [ ] **Step 10.2: `uv run ruff check .`** — clean.
- [ ] **Step 10.3: `uv run ruff format --check .`** — clean.
- [ ] **Step 10.4: `uv run mypy ralph_executor scripts skills tests`** — clean.
- [ ] **Step 10.5: Open PR** against `main` from `ralph/SWEEP-AUTO-MERGE-CLEAN-PRS` via the `pr` skill (`create-pr` op).

---

## Risks

| Risk | Mitigation |
|---|---|
| GitHub returns 405 for "branch out of date" (not just race) — sweep retries forever. | The branch-up-to-date condition is part of `mergeable_state == "clean"`. If GitHub reports `clean` but then returns 405, that IS a race (someone pushed during the call). Next sweep observes the new state and either retries or sees a different `mergeable_state`. |
| Operator turns the flag on by mistake and a PR they wanted to review gets merged. | Default is `false`. CONFIG_TOML_STUB warns. The PBI's HISTORY.md has the auto-merge marker so it's auditable. Recovery is `git revert`. |
| `mergeable_state == "clean"` predicate races with a freshly-pushed commit that flips CI to red. | GitHub serialises `mergeable_state` updates against pushes. If a push lands between sweep's `show` and `merge_pr`, the head-SHA mismatch produces 409 → exit 4 → retry. |
| ADO host has no merge skill. | Out of scope. Flag-on + ADO host raises `_SweepPbiError` from the dispatch (merge_pr.py not found on the ADO scripts path). Operators on ADO leave the flag off. Follow-up PBI tracks ADO support. |
| `_read_rest_response` change breaks existing skills that relied on 405/409 → HttpError. | Verified via grep that no existing path tests 405/409 specifically. The change is additive: 405/409 → RaceError (new), everything else 4xx/5xx → HttpError (unchanged). |
