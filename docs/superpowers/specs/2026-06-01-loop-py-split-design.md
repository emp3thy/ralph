# loop.py split — design

- **Status:** Draft — pending user review
- **Date:** 2026-06-01
- **Source finding:** `.tech-debt/design.md` finding #1 (`loop-py-god-module`, severity 5)
- **Scope:** B — extraction + light cleanup (collapse `use_worktrees=False` dead branches, drop stale Plan-8/9 stub comments, rename `_claim_pbi_worktree` → `_setup_worktree`, promote `_ClaimError` → `ClaimError`).

## Problem

`ralph_executor/loop.py` is 1078 lines covering iteration orchestration, sweep wiring, cycle detection, PBI claiming, worktree lifecycle, queue-clone git operations, and Claude-run dispatch. The file is the central hub of every iteration — changes anywhere in it risk breaking unrelated surfaces, and the function-level coupling (`iterate_once` at 210 lines, `_claim_pbi` at 90 lines, `_run_ralph` orchestrating spawn + classify + persist) blocks safe modification of claim, sweep, or persist logic in isolation.

The tech-debt scan ranked this severity 5 because every iteration goes through it, and the suggested split aligns with module boundaries already present in the codebase (`sweep/`, `safety/`, `target_clone`, `queue_clone`, `worktree`). Tests for the file have grown to 1432 lines in `test_loop.py` plus four satellites.

## Goals

- Decompose `loop.py` into focused modules whose responsibilities can be reasoned about and tested independently.
- Preserve current behavior exactly. No semantic change to claim, spawn, classify, persist, sweep, or safety paths.
- Land scope-B cleanups that live in the touched code (legacy `use_worktrees=False` branches, stale Plan-8 stub docstrings) so future iteration of these modules doesn't drag dead code.
- Migrate tests so per-module unit tests live with their target and orchestration tests stay together as the regression net.

## Non-goals

- claude_spawn.py refactor (finding #2 — separate spec).
- sweep/runner.py refactor (finding #3 — separate spec).
- config.py refactor (separate finding, not in this scan's top 5).
- Behavior changes to the Claude spawn / classify / outcome pipeline.
- New features, new error classes (other than promoting the existing `_ClaimError` → `ClaimError`), or new CLI surfaces.

## Locked decisions

| Decision | Choice |
| --- | --- |
| Scope | B — extraction + light cleanup |
| Granularity | 5 modules (`iteration`, `pbi_claim`, `worktree_manager`, `queue_git`, `iteration_safety`) |
| Test migration | C (hybrid — unit tests fan out; orchestration tests consolidate) |
| File rename | `loop.py` → `iteration.py`; `test_loop.py` → `test_iteration.py` |
| Cleanups bundled | Legacy `use_worktrees=False` branches removed in the touched files; stale Plan-8/9 stub docstrings dropped |

## Architecture

Target module map:

```
ralph_executor/
├── iteration.py          # ~400 lines — orchestrator + Claude-run dispatcher
├── pbi_claim.py          # ~250 lines — claim a PBI to current/
├── worktree_manager.py   # ~180 lines — work-worktree lifecycle
├── queue_git.py          # ~100 lines — queue-clone git operations
├── iteration_safety.py   # ~250 lines — sweep + cycle-detector wiring
```

`loop.py` is deleted at the end of the sequence. `iteration.py` replaces it; `cli.py`, package `__init__.py`, `scripts/`, and tests update their imports accordingly.

### Dependency direction

`iteration` depends on every other extracted module plus the existing `claude_spawn`, `config`, `sweep.runner`, `safety.*`, `queue.*`, `target_clone`, `queue_clone`, `git_ops` modules. The extracted modules MUST NOT import `iteration` (re-tangling). The only inter-extracted-module edge allowed is `pbi_claim → worktree_manager`.

## Components

### `iteration.py`

Public surface:
- `iterate_once(cfg: ExecutorConfig) -> IterationResult`
- `run_loop(cfg: ExecutorConfig) -> None`
- `IterationResult` dataclass

Private:
- `_run_ralph(cfg, pbi) -> tuple[ClaudeOutcome, IterationResult]` (Claude spawn → classify → move dispatcher)
- `_move_to_blocked_with_reason(cfg, pbi, *, reason)`
- `_queue_repo_root(cfg) -> Path`
- `_pr_skill_scripts_path(cfg) -> Path`
- `_warn_project_toml_in_target_clone(clone_root)`

Depends on: `pbi_claim`, `worktree_manager`, `queue_git`, `iteration_safety`, `claude_spawn`, `config`, `sweep.runner`, `queue.movements`.

### `pbi_claim.py`

Public surface:
- `claim_pbi(cfg, pbi) -> PBI` (renamed from `_claim_pbi`)
- `ClaimError(RuntimeError)` (renamed from `_ClaimError`)

Private:
- `_feature_branch_name(pbi) -> str`
- `_read_target_repo_from_pbi(pbi) -> str`
- `_setup_worktree(cfg, pbi, target_clone_path) -> PBI` (renamed from `_claim_pbi_worktree`; calls `worktree_manager.materialise_worktree`)

Depends on: `worktree_manager`, `target_clone`, `git_ops`, `queue.movements`, `config`, `types`.

### `worktree_manager.py`

Public surface:
- `cleanup_work_worktree(cfg, pbi) -> None` (renamed from `_cleanup_work_worktree`)
- `materialise_worktree(cfg, pbi, target_clone_path, branch) -> Path` (extracted from the worktree-setup half of `_claim_pbi_worktree`)

Depends on: `git_ops`, `config`, `types`.

### `queue_git.py`

Public surface:
- `pull_queue(cfg) -> None` (renamed from `_pull_queue`)
- `persist_iteration_writes(cfg, source) -> bool` (renamed from `_persist_iteration_writes`)

Depends on: `git_ops`, `queue.filesystem`, `config`.

### `iteration_safety.py`

Public surface:
- `run_sweep(cfg, source) -> None` (renamed from `_run_sweep`)
- `check_cycle_detector(cfg, source) -> bool` (renamed from `_check_cycle_detector`)

Depends on: `sweep.runner`, `safety.cycle_detector`, `safety.events`, `config`.

## Data flow

```
iterate_once(cfg)
  ├─ queue_git.pull_queue(cfg)
  ├─ source = FilesystemQueueSource(cfg)
  ├─ iteration_safety.check_cycle_detector(cfg, source)   # halt if loop dead
  ├─ pbi = source.pick_current() or source.pick_inbox()
  │
  │  # current/ or inbox branch:
  ├─ _run_ralph(cfg, pbi)
  │    ├─ pbi = pbi_claim.claim_pbi(cfg, pbi)            # only if from inbox
  │    │    ├─ queue.movements.move_inbox_to_current(...)
  │    │    ├─ target_clone.ensure_clone(...)
  │    │    └─ worktree_manager.materialise_worktree(...)
  │    ├─ outcome = claude_spawn.spawn_claude_p(cfg, pbi)
  │    ├─ classify → done | partial | stuck | error
  │    ├─ on stuck/error: _move_to_blocked_with_reason → queue.movements.move_current_to_blocked
  │    └─ on done/pr_created: queue.movements.move_current_to_pending_pr + worktree_manager.cleanup_work_worktree
  │
  │  # no PBI to claim:
  └─ iteration_safety.run_sweep(cfg, source)              # pending-pr reconciliation
       └─ queue_git.persist_iteration_writes(cfg, source) # commit + push HISTORY/PBI edits
```

## Error handling

- `ClaimError` is raised only inside `pbi_claim` and caught only inside `iteration._run_ralph`. `_run_ralph` decides whether the claim failure → `_move_to_blocked_with_reason` or `IterationResult(kind="error", ...)`.
- `PushRebaseConflict` (raised by `git_ops` today) — caught in `iteration.iterate_once` around the `_run_ralph` call, exactly as today. Not moved.
- `TargetUnreachable` (from `target_clone.ensure_clone`) — caught inside `pbi_claim.claim_pbi` and converted to `ClaimError(reason="target unreachable: ...")` (matches current behavior).
- All other exceptions propagate unchanged. No new exception classes introduced beyond promoting `_ClaimError` → `ClaimError`.

## Testing

End state under `tests/executor/`:

```
test_iteration.py             # renamed from test_loop.py; iterate_once, run_loop, integration
test_pbi_claim.py             # claim flow, ClaimError paths
test_worktree_manager.py      # materialise + cleanup
test_queue_git.py             # pull_queue, persist_iteration_writes
test_iteration_safety.py      # run_sweep, check_cycle_detector
test_loop_diff_against_target.py    # stays (already focused)
test_iteration_integration.py       # renamed from test_loop_integration.py
test_loop_pr_skill_scripts_path.py  # stays (covers iteration.py helper)
test_loop_project_toml_warning.py   # stays (covers iteration.py helper)
```

Migration rule: tests for symbols that move to a new module fan out into the matching `test_<module>.py`. Tests that hit `iterate_once` or `run_loop` end-to-end (claim → spawn → classify → move) stay in `test_iteration.py`.

Migrated tests preserve their assertions verbatim — fixtures and imports rewire, no semantic edits to the tests themselves.

## Cleanup landing alongside the split (scope B)

- Remove `use_worktrees=False` legacy branches in `_claim_pbi`, `_run_ralph`, and helpers gated on it (matches finding #5 of the same scan, scoped to the files touched here).
- Drop stale "Plan 8 stub" / "Plan 9 stub" docstrings in `_run_sweep`, the file header comments, and `_check_cycle_detector`.
- Rename `_claim_pbi_worktree` → `_setup_worktree` (internal to `pbi_claim`, calls `worktree_manager.materialise_worktree`).
- Promote `_ClaimError` → `ClaimError` on `pbi_claim`.

No other refactoring; legacy branches outside the touched files are not removed in this PR.

## Migration sequence

Eight commits, each green on `uv run pytest tests/`, `uv run ruff format --check .`, and `uv run mypy --strict ralph_executor`. Each commit is independently revertable.

1. **Create `queue_git.py`** — move `_pull_queue` → `pull_queue`, `_persist_iteration_writes` → `persist_iteration_writes`. Update imports in `loop.py`. Migrate matching tests → `test_queue_git.py`. Lowest coupling, lands first.
2. **Create `iteration_safety.py`** — move `_run_sweep`, `_check_cycle_detector`. Drop "Plan 8 / Plan 9 stub" docstrings. Migrate cycle-detector + sweep wiring tests → `test_iteration_safety.py`.
3. **Create `worktree_manager.py`** — move `_cleanup_work_worktree` → `cleanup_work_worktree`. Extract `materialise_worktree` from the worktree-setup half of `_claim_pbi_worktree` (still inside loop.py for now). Migrate worktree lifecycle tests → `test_worktree_manager.py`.
4. **Create `pbi_claim.py`** — move `_ClaimError` → `ClaimError`, `_feature_branch_name`, `_read_target_repo_from_pbi`, `_claim_pbi` → `claim_pbi`, `_claim_pbi_worktree` (calls `worktree_manager.materialise_worktree`). Rename `_claim_pbi_worktree` → `_setup_worktree`. Migrate claim tests → `test_pbi_claim.py`.
5. **Collapse legacy `use_worktrees=False` branches** in the now-thinner `loop.py` plus references in `pbi_claim.py` and `claude_spawn.py` call sites. Finding #5 scope, landed mid-sequence because step 6 needs the simplified call sites.
6. **Rename `loop.py` → `iteration.py`** — `git mv`, single commit. Updates `cli.py`, `ralph_executor/__init__.py`, `scripts/`, and tests imports. No code change.
7. **Rename `test_loop.py` → `test_iteration.py`** and `test_loop_integration.py` → `test_iteration_integration.py`. No semantic changes; the per-module test files already exist from steps 1–4.
8. **Doc + comment sweep** — update module docstrings in the new files, drop any residual Plan-8/Plan-9 references, update `docs/runbooks/ralph-architecture.md` if it names `loop.py`.

## Acceptance criteria

- The five new modules listed above exist with the symbols listed above.
- `loop.py` no longer exists in `ralph_executor/`. `iteration.py` is its successor.
- `uv run pytest tests/` passes with the same passing count as `main` (modulo the 2 pre-existing `test_config_toml.py` failures unrelated to this change).
- `uv run ruff format --check .` is clean.
- `uv run mypy --strict ralph_executor` is clean.
- No module other than `iteration.py` imports from another extracted module, with the single exception of `pbi_claim → worktree_manager`.
- `grep -rn "use_worktrees" ralph_executor/iteration.py ralph_executor/pbi_claim.py ralph_executor/worktree_manager.py ralph_executor/claude_spawn.py` returns nothing inside conditional branches (legacy gating removed from the touched files).
- `grep -rn "Plan 8\|Plan 9 stub" ralph_executor/iteration*.py ralph_executor/queue_git.py` returns nothing.

## Risks

- **Hidden cross-references.** A symbol thought private to `loop.py` is imported by an unexpected caller. Mitigation: each step's commit runs the full test suite + a `grep` for old symbol names before merging.
- **Test fixtures coupling.** `test_loop.py` shares fixtures across many tests; fanning them out can break the fixtures. Mitigation: per-module fixtures duplicated where needed (the duplication is small and self-contained per test file).
- **PushRebaseConflict catch-site misplacement.** The catch lives in `iterate_once` around `_run_ralph`; moving `_run_ralph` to `iteration.py` keeps the catch local, but moving the persist code to `queue_git.py` must not duplicate or drop the catch. Mitigation: step 1 (the persist extraction) explicitly asserts the existing catch path still fires via an existing test.
- **Plan-8 stub comment removal misreading live code as dead.** Mitigation: the comments removed in step 2 are docstring lines / `# Stubs for Plans 8 and 9` section headers — bodies are untouched.

## References

- Source finding: `.tech-debt/design.md` finding #1 (`loop-py-god-module`).
- Related finding (cleanup overlaps with): `.tech-debt/design.md` finding #5 (`legacy-single-checkout-branches`).
- Existing modules to reuse: `ralph_executor/git_ops.py`, `ralph_executor/target_clone.py`, `ralph_executor/queue_clone.py`, `ralph_executor/queue/movements.py`, `ralph_executor/queue/filesystem.py`, `ralph_executor/safety/cycle_detector.py`, `ralph_executor/sweep/runner.py`.
