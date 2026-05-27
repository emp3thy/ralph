# Sweep auto-merges clean PRs (gated by TOML flag)

**Date:** 2026-05-27
**Scope:** Sweep optionally auto-merges PRs that GitHub reports as `mergeable_state == "clean"`, gated by a new TOML config flag `auto_merge_clean_prs` (default `false`). Adds `merge_pr` sub-op to `pr-github` (new exit code 4 for race / refused-by-host) and exposes raw `mergeable_state` from `show.py`. `PrSnapshot.merge_state: str` carries the raw GitHub value. `Action.MERGE_PR` enum value triggers the subprocess merge from sweep's act path.
**Status:** Design — pending review.

## Background

Today, when sweep observes a PBI in `pending-pr/` whose PR has `pr_status == "completed"` (merged), it moves the PBI to `done/`. The act of merging is performed by a human (or out-of-band auto-merge GitHub policy). Ralph never merges its own PRs.

For high-volume autonomous loops we want the option to close the loop without a human in the merge step. GitHub's `mergeable_state == "clean"` is authoritative — captures CI green + required approvals + no conflicts + branch up-to-date in one bit. When it is `clean`, the PR is safe to merge.

The feature is OFF by default. Operators who want it on set `auto_merge_clean_prs = true` in `.ralph/config.toml` (or `RALPH_AUTO_MERGE_CLEAN_PRS=true` in env). Default-false because mistakenly merging a PR that a human still wanted to review is unrecoverable.

## Decision

Add a `merge_pr` operation to the `pr-github` skill (7th sub-op) wrapping GitHub's `PUT /repos/{owner}/{repo}/pulls/{number}/merge`. Expose the raw GitHub `mergeable_state` alongside the existing `merge_status` field in `show.py`'s JSON. Carry the raw value through `PrSnapshot.merge_state` so the sweep predicate can read it.

`decide_action` returns a new `Action.MERGE_PR` when (a) the config flag is on, (b) `pr.merge_state == "clean"`. Sweep's act path subprocess-invokes the `merge_pr` skill. On exit 0 the PBI moves to `done/` (same destination as a PR that was merged by a human). On exit 4 (race / refused-by-host — typically 405 Method Not Allowed or 409 Conflict from GitHub) the PBI stays in `pending-pr/` and is retried on the next sweep iteration. On exit 3 (other GitHub error) the same retry semantics apply, with a WARNING log. Exit 2 (validation / IO) escapes as `_SweepPbiError`.

## State machine

For each PBI in `pending-pr/`:

| Condition | Existing action | New action (this PBI) |
|---|---|---|
| `pr_status == "completed"` (already merged) | `MOVE_TO_DONE` | unchanged |
| `pr_status == "abandoned"` | `MOVE_TO_BLOCKED_ABANDONED` | unchanged |
| `ci_status == "failed"` | retry / blocked | unchanged |
| `cfg.auto_merge_clean_prs AND pr.merge_state == "clean"` (active branch) | `NOOP` (or `PING_REVIEWER` if stale) | **`MERGE_PR`** |
| New active human comments | `CREATE_FEEDBACK_PBI` | unchanged (precedes the merge check) |
| Otherwise | `NOOP` / `PING_REVIEWER` | unchanged |

Ordering inside the active branch of `decide_action`: (1) CI failed → retry/blocked; (2) new active human comments → feedback PBI; (3) **auto-merge predicate** → MERGE_PR; (4) stale → ping reviewer; (5) default NOOP. New-comments takes precedence over auto-merge so a `clean` PR with a still-open review thread doesn't get merged over the reviewer's outstanding question. (Real-world `mergeable_state == "clean"` requires approval, so this is belt-and-braces.)

## Architecture

**File layout (delta):**

```
skills/pr-github/
  SKILL.md                                MODIFY (document merge_pr op)
  scripts/
    merge_pr.py                           NEW
    show.py                               MODIFY (emit raw mergeable_state)

ralph_executor/
  config.py                               MODIFY (auto_merge_clean_prs knob)
  setup_cmds.py                           MODIFY (CONFIG_TOML_STUB)
  sweep/
    types.py                              MODIFY (Action.MERGE_PR, PrSnapshot.merge_state)
    pr_state.py                           MODIFY (parse merge_state from show payload)
    runner.py                             MODIFY (predicate + dispatch + subprocess invoke)

tests/skills/test_pr_github.py            MODIFY (show emits mergeable_state; merge_pr tests)
tests/executor/sweep/
  test_decide_action.py                   MODIFY (MERGE_PR predicate)
  test_runner.py                          MODIFY (merge_pr subprocess dispatch)
  conftest.py                             MODIFY (shim merge_pr.py; register merge_state)
tests/executor/
  test_config.py                          MODIFY (auto_merge_clean_prs env)
  test_config_toml.py                     MODIFY (auto_merge_clean_prs TOML)
```

### `merge_pr.py` contract

```
Args:   --repo <repo-name>                (required)
        --pr-id <int>                     (required)
        --merge-method <enum>             (optional; default "squash"; one of merge|squash|rebase)
        --commit-title <text>             (optional; passed through to GitHub)
        --commit-message <text>           (optional; passed through to GitHub)

Env:    GH_TOKEN, GH_OWNER  (same as the rest of pr-github)

Stdout: JSON {pr_id, repo, merged: bool, sha: str|null, message: str}

Exit codes:
  0  success — PR merged. Stdout JSON has merged=true and the merge commit sha.
  2  validation / IO error before any HTTP call.
  3  GitHub returned an unexpected error.
  4  Race / refused-by-host: 405 Method Not Allowed or 409 Conflict.
     Caller (sweep) treats this as "not ready" and retries on next iteration.
```

REST endpoint: `PUT /repos/{owner}/{repo}/pulls/{number}/merge`
(docs: https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request).

GitHub's documented responses:
- `200 OK` — merged. Response body has `sha` + `merged: true`.
- `405 Method Not Allowed` — PR is not mergeable (e.g. CI not green, branch out of date, required reviews missing). The `message` body explains.
- `409 Conflict` — head SHA doesn't match (someone pushed during the merge attempt).
- `422 Unprocessable Entity` — validation failure.

`405` and `409` map to exit 4. `422` and anything else 4xx/5xx map to exit 3.

### `show.py` change

Emit `mergeable_state` in the JSON output verbatim (it's a string; null when GitHub's async mergeability check hasn't resolved). Existing `merge_status` (mapped onto `mergeable`/`conflicting`/`unknown`) stays untouched — it serves downstream code that doesn't care about the host's specific vocabulary.

### `PrSnapshot.merge_state` + `pr_state.py`

Add a `merge_state: str` field to `PrSnapshot`. `pr_state.fetch` populates it from `show_dict.get("mergeable_state", "")`. Empty string is the "not present / null" sentinel — same convention as `last_ci_status`.

### `Action.MERGE_PR` + `decide_action`

Add `MERGE_PR = "merge-pr"` to the `Action` enum. `decide_action` accepts the new predicate via a new `auto_merge_clean_prs: bool` field on `SweepConfig`. When the predicate fires, return `Decision(action=Action.MERGE_PR, reason="auto-merging clean PR")`.

### Runner dispatch

`_dispatch` gains a new branch handling `Action.MERGE_PR`:

```
elif a is Action.MERGE_PR:
    rc = _invoke_merge_pr(pbi_dir=pbi_dir, pr_id=snapshot.pr_id, ctx=ctx)
    if rc == 0:
        _move_with_history(pbi_dir, qr / "done" / pbi_dir.name, "PR auto-merged by sweep", ctx)
        _emit_pr_merged_and_pbi_closed(...)
    elif rc == 4:
        log.info("sweep: merge_pr refused for %s (race / not-ready); retry next iter", ...)
    elif rc == 3:
        log.warning("sweep: merge_pr GitHub error for %s; retry next iter", ...)
    else:
        raise _SweepPbiError(f"merge_pr exit {rc}")
```

The subprocess call mirrors `pr_state._invoke_skill`: same `sys.executable`, same `capture_output=True`. New helper `_invoke_merge_pr` in `runner.py` (kept local — only one call site).

### Config knob

`ExecutorConfig.auto_merge_clean_prs: bool = False`. TOML key `auto_merge_clean_prs`. Env var `RALPH_AUTO_MERGE_CLEAN_PRS`. Layered defaults < TOML < env per the existing `_resolve_bool` helper. Documented in `CONFIG_TOML_STUB`.

`loop._run_sweep` reads `cfg.auto_merge_clean_prs` and passes it through to `SweepConfig`.

## Out of scope (this PBI)

- ADO host: `pr-ado` does not get `merge_pr`. The TOML flag is honoured on either host but the act path raises `_SweepPbiError` if the host is ADO (no merge skill). Operators on ADO leave the flag off. A follow-up PBI covers ADO when warranted.
- Auto-rebase before merge: the merge call uses the merge method as-is. If GitHub returns 409 (head SHA mismatch), the next sweep tick will re-fetch and try again — no in-line rebase logic.
- Operator override / abort window: there is no "wait N minutes" delay between observing `clean` and triggering the merge. Operators who want a human gate set the flag to `false`.
- Concurrency with manual merge: if a human merges the PR between sweep's `show` call and sweep's `merge_pr` call, `merge_pr` returns 4 (409 Conflict, head SHA moved) and the next sweep tick observes `pr_status == "completed"` and routes through the existing `MOVE_TO_DONE` path.

## Tests

- `pr-github`:
  - `show` emits `mergeable_state` verbatim, including when the value is `null` (then the JSON has `mergeable_state: null` or omits — pick one and lock it).
  - `merge_pr` happy path: 200 from GitHub → exit 0, JSON has `merged: true, sha: "..."`.
  - `merge_pr` 405 → exit 4. 409 → exit 4.
  - `merge_pr` 422 → exit 3. 500 → exit 3.
  - `merge_pr` rejects bad `--merge-method` at argparse choice level → exit 2.
- `decide_action`:
  - Flag off + `merge_state="clean"` → `NOOP` (not MERGE_PR).
  - Flag on + `merge_state="clean"` → `MERGE_PR`.
  - Flag on + `merge_state="dirty"` → not MERGE_PR (NOOP or other).
  - Flag on + `merge_state="clean"` BUT new active human comments → `CREATE_FEEDBACK_PBI` (precedence preserved).
- Sweep runner:
  - End-to-end: PBI in `pending-pr/`, `merge_state="clean"`, flag on, shim `merge_pr.py` returns 0 → PBI lands in `done/`.
  - Same scenario, shim returns 4 → PBI stays in `pending-pr/` with no PR-LINK.md change.
  - Same scenario, shim returns 3 → stays in `pending-pr/`, WARNING logged.
- Config:
  - Default `auto_merge_clean_prs == False`.
  - TOML `auto_merge_clean_prs = true` flows through.
  - Env `RALPH_AUTO_MERGE_CLEAN_PRS=true` wins over TOML.
  - Env invalid value → `ConfigError`.

## Acceptance criteria

- `skills/pr-github/scripts/show.py` emits raw `mergeable_state` field alongside existing `merge_status`.
- `skills/pr-github/scripts/merge_pr.py` exists (7th sub-op) with exit codes 0/2/3/4.
- `SKILL.md` documents `merge_pr` operation.
- `ExecutorConfig.auto_merge_clean_prs: bool = False`, layered defaults < TOML < env.
- `PrSnapshot.merge_state: str` carries GitHub's raw value (parsed by `pr_state.py`).
- `Action.MERGE_PR` enum value exists on `sweep/types.py`.
- `decide_action` returns `MERGE_PR` only when `cfg.auto_merge_clean_prs AND pr.merge_state == "clean"`.
- Act path subprocess-invokes `merge_pr` skill; on exit 0 moves PBI to `done/` with HISTORY.md reason `"PR auto-merged by sweep"`; on exit 4 leaves PBI in `pending-pr/` with INFO log; on exit 3 logs WARNING; on exit 2 raises `_SweepPbiError`.
- `CONFIG_TOML_STUB` documents the new key (default-false note).
- Plan-defined tests pass: `uv run pytest`.
- `uv run ruff check .` clean.
- `uv run ruff format --check .` clean.
- `uv run mypy ralph_executor scripts skills tests` clean.
- PR opened against `main` from `ralph/SWEEP-AUTO-MERGE-CLEAN-PRS`.
