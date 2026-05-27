# Sweep auto-merges clean PRs

**Date:** 2026-05-27
**Scope:** Sweep optionally auto-merges PRs that GitHub reports as `mergeable_state == "clean"`, gated by a new TOML config flag `auto_merge_clean_prs` (default `false`). Adds a `merge_pr` sub-op to `pr-github` and exposes raw `mergeable_state` from `show.py`. ADO merge support deferred.
**Status:** Design — pending review.

## Background

Today, when ralph opens a PR for a PBI, the PR sits in `.ralph/pending-pr/<PBI-ID>/` until something external merges it (operator manually, Claude in babysit mode, or GitHub auto-merge if enabled). Sweep observes the merge via `show.py` returning `pr_status == "completed"` and moves the dir to `done/`. Ralph itself never calls `gh pr merge` or equivalent.

For unattended operation this means PRs accumulate until external merge. We want ralph to merge any PR GitHub itself considers ready, with operator-opt-in via TOML flag (default off).

## Decision

Add `auto_merge_clean_prs: bool = False` to `ExecutorConfig`. When `True`, sweep's `decide_action` returns `Action.MERGE_PR` for any open PR whose `merge_state == "clean"`. Sweep's act path subprocess-invokes a new `merge_pr` sub-op on the `pr-github` skill; on success the PBI moves to `done/` with HISTORY.md note `"PR auto-merged by sweep"`.

**Predicate:** `cfg.auto_merge_clean_prs AND pr.merge_state == "clean"`. Single signal from GitHub. `mergeable_state == "clean"` captures all branch-protection requirements (CI green, required approvals met, no conflicts, branch up-to-date) in one bit. No client-side duplication of GitHub's branch-protection logic.

**Race handling:** Trust GitHub. Between sweep's `show.py` call and the `merge_pr` PUT, state may change (new red CI check, reviewer dismisses approval). GitHub will return 405/409. New exit code `4` from `merge_pr` signals "merge refused by host"; sweep logs INFO and leaves the PBI in pending-pr/ for next iteration.

**depends_on:** `SWEEP-RECONCILE-ORPHANS`. Both PBIs modify `skills/pr-github/SKILL.md` (each adds a sub-op to the op list) and both could touch `_common.py`. Sequencing prevents merge conflicts.

## Architecture

### File delta

```
skills/pr-github/scripts/show.py             MODIFY (expose raw mergeable_state)
skills/pr-github/scripts/merge_pr.py         NEW (7th sub-op)
skills/pr-github/SKILL.md                    MODIFY (document merge_pr)

ralph_executor/sweep/types.py                MODIFY (Action.MERGE_PR + PrSnapshot.merge_state)
ralph_executor/sweep/pr_state.py             MODIFY (parse mergeable_state into PrSnapshot.merge_state)
ralph_executor/sweep/runner.py               MODIFY (SweepConfig.auto_merge_clean_prs;
                                              decide_action; act subprocess to merge_pr)
ralph_executor/config.py                     MODIFY (auto_merge_clean_prs field + _resolve_bool)
ralph_executor/loop.py                       MODIFY (plumb cfg.auto_merge_clean_prs into SweepConfig)
ralph_executor/setup_cmds.py                 MODIFY (CONFIG_TOML_STUB)

tests/skills/test_pr_github.py               MODIFY
tests/executor/sweep/test_pr_state.py        MODIFY
tests/executor/sweep/test_runner.py          MODIFY
tests/executor/test_config_toml.py           MODIFY
```

### show.py change

Existing `merge_status` cross-host field stays (existing readers untouched). Add sibling field `mergeable_state` carrying the untranslated GitHub value (`clean`, `unstable`, `behind`, `blocked`, `dirty`, `has_hooks`, `draft`, `unknown`). show.py does not validate the enum — passes through whatever GitHub returns.

```jsonc
{
  // ... existing fields ...
  "merge_status": "mergeable",       // existing cross-host
  "mergeable_state": "clean"          // NEW, raw GitHub value
}
```

### merge_pr.py contract

```
Args:   --pr-id <int>             (required)
        --repo <repo-name>        (required, matches existing pr-github scripts)
        --merge-method <method>   (optional; squash | merge | rebase; default squash)
        --no-delete-branch        (optional flag; default behavior is to delete the
                                   source ref after a successful merge)

Env:    GH_TOKEN, GH_OWNER (standard pr-github)

REST:   PUT  /repos/{owner}/{repo}/pulls/{pr_number}/merge
        DELETE /repos/{owner}/{repo}/git/refs/heads/{branch}
            (only if delete-branch is true; failure non-fatal — merge already done)
```

JSON stdout on exit 0 (merge succeeded):

```json
{
  "merged": true,
  "sha": "<merge commit sha>",
  "pr_id": 25,
  "branch_deleted": true
}
```

JSON stdout on exit 4 (refused — race / state changed):

```json
{
  "merged": false,
  "reason": "<github error message>",
  "pr_id": 25
}
```

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | Merged successfully |
| 2 | Validation / arg / auth error (matches other ops) |
| 3 | GitHub HTTP error: 5xx, network, rate-limit |
| 4 | Merge refused by host: 405/409 — race condition, predicate stale. **NEW; deviates from existing 0/2/3 convention.** |

The exit 4 deviation is intentional. Sweep needs to distinguish "transient ralph-side problem, surface to operator" from "stale state, retry next iter".

### Sweep wiring

**`sweep/types.py`:**

```python
class Action(StrEnum):
    # ... existing values ...
    MERGE_PR = "merge-pr"

@dataclass(frozen=True)
class PrSnapshot:
    # ... existing fields ...
    merge_state: str  # raw GitHub mergeable_state; "unknown" when absent
```

**`sweep/pr_state.py`:** parse `mergeable_state` from show.py JSON into `PrSnapshot.merge_state`. Default missing field to `"unknown"` (defensive: handles partial rollout where show.py change hasn't deployed yet).

**`sweep/runner.py` — `decide_action`:** the new branch goes BEFORE existing CI / feedback / stale checks but AFTER the "PR completed" branch (external merge still wins if both fired):

```python
if pr.pr_status == "completed":
    return Decision(action=Action.MOVE_TO_DONE, reason="PR merged (completed)")

if config.auto_merge_clean_prs and pr.merge_state == "clean":
    return Decision(action=Action.MERGE_PR, reason="PR clean; auto-merging")

# ... existing CI-red / feedback / stale branches ...
```

**`sweep/runner.py` — act path:**

```python
if a is Action.MERGE_PR:
    result = _invoke_merge_pr_skill(pbi_dir, ctx)
    if result.merged:
        _move_with_history(pbi_dir, qr / "done" / pbi_dir.name,
                           "PR auto-merged by sweep", ctx)
    else:
        # Exit 4: GitHub refused. Stale state. Retry next iter.
        log.info("sweep: merge refused for %s: %s", pbi_dir.name, result.reason)
    return
```

**`SweepConfig`:** add `auto_merge_clean_prs: bool = False`. `loop._run_sweep` reads `cfg.auto_merge_clean_prs` and passes it to the SweepConfig constructor.

### Config + TOML

`ExecutorConfig` gains `auto_merge_clean_prs: bool = False`. `_TOML_KNOWN_KEYS` gains the key. `load_config` adds a `_resolve_bool` call (helper may not exist — add modeled on `_resolve_int`: accepts `"true"/"false"/"1"/"0"` from env; rejects non-bool from TOML).

`CONFIG_TOML_STUB`:

```
#
# Auto-merge clean PRs. When true, sweep merges any PR whose GitHub
# mergeable_state is "clean" (CI green + required approvals met + no
# conflicts + branch up-to-date). Default false for safety. Env override:
# RALPH_AUTO_MERGE_CLEAN_PRS.
# auto_merge_clean_prs = false
```

## Error handling

| merge_pr exit | Sweep response |
|---|---|
| 0 (merged) | move PBI to `done/`; HISTORY.md gets `"PR auto-merged by sweep"` |
| 2 (validation) | raise `_SweepPbiError`; surfaces in `result.errors` per existing convention |
| 3 (GitHub HTTP error) | log WARNING, leave PBI in pending-pr/, retry next iter |
| 4 (refused / race) | log INFO with reason, leave PBI in pending-pr/, retry next iter |

Subprocess invocation pattern mirrors what `SWEEP-RECONCILE-ORPHANS` introduces for `lookup_by_branch.py`. Code may share a `_invoke_pr_skill(name, args)` helper — extract during implementation if both callers want it.

## Testing

**`tests/skills/test_pr_github.py`:**
- show.py output includes `mergeable_state` field with raw GitHub value
- merge_pr.py PUT succeeds → exit 0 + `merged: true`
- merge_pr.py with `--no-delete-branch` skips the DELETE call
- merge_pr.py 405 from GitHub → exit 4, `merged: false`
- merge_pr.py 409 from GitHub → exit 4
- merge_pr.py 500 → exit 3
- merge_pr.py missing `GH_TOKEN` → exit 2
- merge_pr.py missing `--pr-id` → exit 2
- merge_pr.py `--merge-method=rebase` includes `"merge_method": "rebase"` in PUT body

**`tests/executor/sweep/test_pr_state.py`:**
- PrSnapshot.merge_state parsed from show.py JSON
- Missing `mergeable_state` in JSON → `merge_state == "unknown"`

**`tests/executor/sweep/test_runner.py`:**

decide_action:
- `auto_merge_clean_prs=False`, `merge_state="clean"` → not MERGE_PR
- `auto_merge_clean_prs=True`, `merge_state="clean"` → MERGE_PR
- `auto_merge_clean_prs=True`, `merge_state="blocked"` → existing action (not MERGE_PR)
- `auto_merge_clean_prs=True`, `merge_state="behind"` → existing action
- `pr_status="completed"` always wins over MERGE_PR (external merge precedence)

act:
- MERGE_PR + merge_pr exit 0 → PBI in done/, HISTORY.md reason `"PR auto-merged by sweep"`
- MERGE_PR + merge_pr exit 4 → PBI stays, INFO logged
- MERGE_PR + merge_pr exit 3 → PBI stays, WARNING logged
- MERGE_PR + merge_pr exit 2 → `_SweepPbiError`

**`tests/executor/test_config_toml.py`:**
- default: `cfg.auto_merge_clean_prs is False`
- TOML `auto_merge_clean_prs = true` → True
- env `RALPH_AUTO_MERGE_CLEAN_PRS=true` overrides TOML false
- env garbage (`RALPH_AUTO_MERGE_CLEAN_PRS=maybe`) → ConfigError

**Updates to existing tests:**
- Any test constructing `PrSnapshot` literally adds `merge_state="unknown"` keyword
- Any test constructing `SweepConfig` literally adds `auto_merge_clean_prs=False` keyword

## Acceptance

- `skills/pr-github/scripts/show.py` emits raw `mergeable_state` field
- `skills/pr-github/scripts/merge_pr.py` exists with contract above (exit 0/2/3/4)
- `SKILL.md` documents `merge_pr` operation
- `ExecutorConfig.auto_merge_clean_prs: bool = False`, layered defaults < TOML < env
- `PrSnapshot.merge_state: str` carries GitHub's raw value
- `Action.MERGE_PR` enum value exists
- `decide_action` returns `MERGE_PR` when `cfg.auto_merge_clean_prs AND pr.merge_state == "clean"`
- act path subprocess-invokes merge_pr; on success moves PBI to `done/` with audit string `"PR auto-merged by sweep"`
- `CONFIG_TOML_STUB` documents the new key
- pytest / ruff / mypy strict all green
- `responses` library used for all skill HTTP mocking
- PBI `depends_on: ["SWEEP-RECONCILE-ORPHANS"]`

## Out of scope

- ADO `merge_pr.py` (deferred — same tracker issue as ADO `lookup_by_branch`)
- Merge methods other than `squash | merge | rebase`
- Auto-merge for `unstable` or `has_hooks` states (only `clean`)
- Approval-count thresholds (trust GitHub branch protection)
- Auto-rebase before merge when state is `behind`
- `--force` / `--admin` bypass paths

## Risks

| Risk | Mitigation |
|---|---|
| Operator's repo has weak branch protection — ralph merges PRs without reviewer | Operator's repo config is operator's responsibility. `mergeable_state == "clean"` reflects whatever rules ARE set. Default-false flag ensures opt-in. |
| Race: state flips between show.py and merge_pr | merge_pr returns exit 4; sweep logs INFO + retries. No data loss. |
| Default-false means existing operators see no behavior change | Explicit test in plan asserting default is False. |
| Exit code 4 breaks the existing 0/2/3 convention | Documented in skill contract; sweep is the only consumer; PROMPT.md never invokes merge_pr. |
| Auto-merge surprises an unprepared operator | Default false; opt-in only; HISTORY.md audit string is distinct (`"PR auto-merged by sweep"` vs `"PR merged (completed)"`). |
| Partial deploy — pr_state.py reads old show.py output | Defensive default `merge_state="unknown"` in parser. Predicate never accidentally matches `"unknown"` against `"clean"`. |
