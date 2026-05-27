# Plan: worktree-based execution refactor (Phase 1 of KEDA pod work)

Self-contained; no separate doc under `docs/superpowers/plans/`.

## Layout

After this PBI lands:

```
<repo>/
  .git/                            (shared object store)
  ralph_executor/                  (executor source, on whatever branch is HEAD)
  ...
  .ralph-work/                     (worktree root, gitignored)
    queue/                         (worktree: ralph-queue)
      .ralph/inbox/
      .ralph/current/<PBI-ID>/     ← executor reads/writes HERE
      ...
    repo-<PBI-ID>/                 (worktree: ralph/<PBI-ID>)
      ralph_executor/              ← Claude reads/writes code HERE
      ...
```

The primary checkout (where the user runs `uv run ralph-executor`) is on whatever branch they care about. The executor creates the two worktrees under `.ralph-work/` per-PBI.

## Design notes

- **Why `.ralph-work/`?** Keeps both worktrees out of the way of the operator's primary checkout. Add `.ralph-work/` to `.gitignore`. Worktrees are per-iteration ephemera; primary checkout is for the operator.
- **Why per-PBI `repo-<id>/`?** Multiple ralphs in the same repo simultaneously (rare but possible) get isolated work trees. Cleanup is per-PBI on completion.
- **Single queue worktree** because `.ralph/current/` allows only one active PBI at a time. Operator can also see queue state without their primary checkout being on ralph-queue.

## Tasks

### Task 1 — `ralph_executor/worktree.py`

```python
from pathlib import Path
import subprocess

def ensure_worktree(
    git_root: Path,
    *,
    worktree_path: Path,
    branch: str,
    create_branch_from: str | None = None,
) -> None:
    """Idempotent: create worktree if absent; otherwise switch its branch
    to `branch` (assumes worktree's working tree is clean).

    If create_branch_from is set and the branch doesn't exist locally,
    create it from that base (e.g. 'main').
    """

def list_worktrees(git_root: Path) -> list[dict]:
    """Parse `git worktree list --porcelain` into structured dicts."""

def remove_worktree(git_root: Path, worktree_path: Path) -> None:
    """`git worktree remove <path>` then prune. Tolerant of missing."""

def worktree_branch(worktree_path: Path) -> str:
    """`git -C <path> rev-parse --abbrev-ref HEAD`. Used to verify state."""
```

Implementation notes:
- `git worktree add` requires the branch to exist OR `-b <new_branch>` to create it
- Detect existing worktree by `worktree_path in {entry['path'] for entry in list_worktrees(...)}`
- Cleanup uses `git worktree remove --force` to discard any incomplete state

Commit: `feat(worktree): worktree.py helpers (ensure/list/remove/branch)`

- [x] Task 1 complete (iteration 1, 2026-05-27, commit d20581a)

### Task 2 — config knob

Add to `ExecutorConfig`:

```python
use_worktrees: bool = True   # NEW Stage-B default
```

Add to `_TOML_KNOWN_KEYS`. Resolve via `_resolve_bool`. Operators on legacy single-checkout setups can opt out via TOML or env.

Commit: `feat(config): use_worktrees TOML/env knob`

- [x] Task 2 complete (iteration 2, 2026-05-27, commit 78877b2)

### Task 3 — refactor `_claim_pbi`

When `cfg.use_worktrees`:

```python
queue_wt = cfg.repo_path / ".ralph-work" / "queue"
ensure_worktree(cfg.repo_path, worktree_path=queue_wt, branch=cfg.queue_branch)

# move inbox -> current happens via the queue worktree
move_inbox_to_current(...)  # operates on queue_wt's .ralph/

# feature branch
feature_branch = _feature_branch_name(pbi)
work_wt = cfg.repo_path / ".ralph-work" / f"repo-{pbi.id}"
ensure_worktree(
    cfg.repo_path,
    worktree_path=work_wt,
    branch=feature_branch,
    create_branch_from=cfg.main_branch,
)
return moved
```

When `not cfg.use_worktrees`: fall through to today's behaviour (single checkout, branch switching). Keep both paths in code during this PBI; remove the single-checkout path in a follow-up PBI once Stage B confirms the worktree path is solid.

Commit: `feat(loop): worktree-mode claim creates per-PBI work tree`

- [x] Task 3 complete (iteration 3, 2026-05-27, commit d969b73)

### Task 4 — refactor `_run_ralph` to spawn Claude with worktree paths

```python
work_wt = cfg.repo_path / ".ralph-work" / f"repo-{pbi.id}"
pbi_dir_in_queue = cfg.repo_path / ".ralph-work" / "queue" / ".ralph" / "current" / pbi.id

outcome = spawn_claude_p(
    cfg,
    pbi,
    cwd=work_wt,                   # Claude runs in the code worktree
    pbi_dir=pbi_dir_in_queue,      # absolute path, becomes RALPH_PBI_DIR
)
```

Extend `spawn_claude_p` to accept `cwd` and `pbi_dir` overrides. When `use_worktrees=False`, the existing call site behaviour stays.

In `claude_spawn.py`:

```python
env["RALPH_PBI_DIR"] = str(pbi_dir)
```

Always set RALPH_PBI_DIR, not just in worktree mode. Single-checkout mode sets it to the same path that's currently the cwd's `.ralph/current/<id>/`.

Commit: `feat(claude_spawn): spawn into work tree with RALPH_PBI_DIR pointing at queue tree`

- [x] Task 4 complete (iteration 4, 2026-05-27, commit 881fce2)

### Task 5 — update PROMPT.md

Every `.ralph/current/<PBI-ID>/HISTORY.md` reference becomes `$RALPH_PBI_DIR/HISTORY.md`. Same for STUCK.md, PLAN.md, PBI.md.

Preamble line near the top:

> All file paths in this prompt that refer to the active PBI use `$RALPH_PBI_DIR/...` (set by the executor). Read and write to those absolute paths. Do NOT use the relative `.ralph/current/...` path — your current working directory is the code worktree, not the queue worktree.

Commit: `docs(prompt): switch PBI-file references to $RALPH_PBI_DIR`

- [x] Task 5 complete (iteration 5, 2026-05-27, commit 2ee88b6)

### Task 6 — `_persist_iteration_writes` simplification

When use_worktrees, the function operates directly on the queue worktree (`queue_wt`); no branch switching, just `git -C queue_wt add .ralph/current/<id>/` + commit + push.

Single-checkout path stays unchanged.

Commit: `refactor(loop): _persist_iteration_writes operates on queue worktree`

- [x] Task 6 complete (iteration 6, 2026-05-27, commit 49fd914)

### Task 7 — worktree cleanup on PBI completion

When PBI moves to pending-pr / blocked / done, remove the per-PBI work tree:

```python
work_wt = cfg.repo_path / ".ralph-work" / f"repo-{pbi.id}"
if cfg.use_worktrees:
    remove_worktree(cfg.repo_path, work_wt)
```

Queue worktree persists across PBIs.

Commit: `feat(loop): clean up per-PBI work tree on terminal outcomes`

- [x] Task 7 complete (iteration 7, 2026-05-27, commit 4aef065)

### Task 8 — `.ralph-work/` gitignore

Add `.ralph-work/` to root `.gitignore`. Worktrees are not tracked content; the entries under `.git/worktrees/` are what makes them work.

Commit: `chore(gitignore): exclude .ralph-work/`

- [x] Task 8 complete (iteration 8, 2026-05-27, commit 5c21fb0)

### Task 9 — tests

- `test_ensure_worktree_creates_new_when_absent`
- `test_ensure_worktree_idempotent_when_already_exists`
- `test_ensure_worktree_creates_branch_from_base`
- `test_remove_worktree_cleans_up_and_prunes`
- `test_remove_worktree_tolerates_missing`
- `test_claim_with_worktrees_creates_queue_and_work_trees`
- `test_run_ralph_spawns_claude_with_cwd_and_ralph_pbi_dir`
- `test_persist_iteration_writes_worktree_mode_commits_from_queue_tree`
- `test_terminal_outcome_removes_work_tree`
- `test_legacy_single_checkout_path_still_works_when_flag_false`

Commit: `test(worktree): regression coverage for two-worktree execution`

- [x] Task 9 complete (iteration 9, 2026-05-27, commit f565a41)

## Out of scope

- KEDA pod runner (Issue #20 Phase 2)
- Removing the single-checkout fallback (do this only once Stage B confirms worktrees are solid; follow-up PBI)
- PVC / container layout for ROSA (Phase 2)

## Adversarial pre-pass

| Failure mode | Mitigation |
|---|---|
| Windows file-locking on worktree remove (already in memory) | Run `git worktree remove` from queue worktree's cwd or primary cwd, NEVER from within the worktree being removed |
| Orphan worktrees from prior crashes | `list_worktrees` + `git worktree prune` on startup; remove any work tree without a matching .ralph/current/<id> |
| Disk full creating worktree | OSError caught at the boundary; reported as ConfigError-equivalent; iteration retries |
| Concurrent ralph processes on the same checkout | Locking out of scope here; document that single-process is the assumption |
| Claude writes code into queue worktree by mistake | Cheap post-spawn guard: `git -C queue_wt status --porcelain` — anything not under `.ralph/current/<id>/` is anomaly, log + stuck-trigger (or at minimum WARNING log) |
| Feature branch already exists locally from a prior partial claim | `ensure_worktree` reuses it; no force-create |
| RALPH_PBI_DIR pointing nowhere (worktree never created) | Fail fast in `spawn_claude_p` before exec; clear error message |
