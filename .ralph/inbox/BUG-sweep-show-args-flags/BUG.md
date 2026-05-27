---
id: BUG-sweep-show-args-flags
type: bug
status: inbox
severity: high
attempts: 0
created_at: 2026-05-27T18:05:00+00:00
updated_at: 2026-05-27T18:05:00+00:00
depends_on: []
---

# Sweep invokes `show.py` and `read_threads.py` with positional pr_id; the skill requires `--repo` + `--pr-id` flags

## Symptom

Every sweep iteration warns once per pending-pr/ entry:

```
WARNING ralph_executor.sweep.runner: sweep error for STAGE-B-PLAN-10: PR skill failure: show.py exited with exit code 2: usage: pr-github show [-h] --repo REPO --pr-id PR_ID
pr-github show: error: the following arguments are required: --repo, --pr-id
INFO ralph_executor.loop: sweep: scanned 2 PBIs (actions=0, errors=2)
```

Result: sweep never advances any pending-pr/ entry to done/, blocked/, or feedback. PRs stack up indefinitely.

## Root cause

`ralph_executor/sweep/pr_state.py:48-55` invokes the skill scripts with a single positional arg:

```python
show_payload = _invoke_skill(
    skill_scripts_path / "show.py",
    [str(pr_id)],
)
threads_payload = _invoke_skill(
    skill_scripts_path / "read_threads.py",
    [str(pr_id)],
)
```

`skills/pr-github/scripts/show.py:183-184` (and `read_threads.py` similarly) declare:

```python
parser.add_argument("--repo", required=True, help="GitHub repository name.")
parser.add_argument("--pr-id", required=True, help="Pull request number.")
```

The skill rejects positional invocation with exit 2 (argparse `required` violation). The mismatch landed in commit `87fcbac STAGE-B-PLAN-19b: emit sweep-side cycle-detector events (#34)` — that PR touched `ralph_executor/sweep/runner.py` and `ralph_executor/loop.py` but did not update `pr_state.py` to match the skill's argparse signature.

The skill signature is consistent with the rest of pr-github (`create_pr.py`, `read_threads.py`, `lookup_by_branch.py` all take `--repo`). pr_state.py is the only outlier.

## Reproduction

1. Start ralph with at least one PBI in `.ralph/pending-pr/`.
2. Watch the sweep iteration log.
3. Expected: sweep advances merged/abandoned PRs.
4. Actual: sweep emits one `PR skill failure: show.py exited with exit code 2` per pending-pr/ entry; no movement.

## Fix

`pr_state.py::fetch` must pass `--repo <repo_name> --pr-id <pr_id>` to both `show.py` and `read_threads.py`. The repo name is available on `SweepContext.repo_name` (added by `STAGE-B-PLAN-20a-worktree`); fall back to `queue_root.parent.name` only when `repo_name` is empty (same precedence rule used by `sweep/reconcile.py::_resolve_repo_name`).

Signature change: `fetch(*, pr_id, skill_scripts_path)` → `fetch(*, pr_id, skill_scripts_path, repo_name)`. Caller `sweep/runner.py::_process_pbi:257` already has access to `ctx`, so add `repo_name=ctx.repo_name or ctx.queue_root.parent.name` there.

## Acceptance criteria

- `pr_state.fetch` accepts `repo_name: str` and passes `--repo`, `--pr-id` to both skill scripts.
- `_process_pbi` in `sweep/runner.py` resolves repo_name via the same fallback `sweep/reconcile.py::_resolve_repo_name` uses (`ctx.repo_name` else `ctx.queue_root.parent.name`).
- One sweep iteration on a real pending-pr/ entry succeeds without exit-2 error.
- pytest, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy ralph_executor scripts skills tests` all green.
- Regression test in `tests/executor/sweep/test_pr_state.py` (create if absent) monkeypatches `_invoke_skill` to capture argv and asserts `--repo` + `--pr-id` are present.
- PR opened against `main` from `ralph/BUG-sweep-show-args-flags`.
