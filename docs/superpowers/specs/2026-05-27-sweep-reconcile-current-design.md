## Sweep reconciles stale `.ralph/current/` entries

**Date:** 2026-05-27
**Scope:** Extend `ralph_executor/sweep/reconcile.py` (and its sweep-loop call site in `runner.py`) to also scan `.ralph/current/` and delete entries that no longer represent an active claim. No new skill, no new CLI subcommand — purely a filesystem reconciliation.
**Status:** Design — pending review.

## Background

`.ralph/current/` is the single-focus folder: at most one PBI lives there. `queue/filesystem.py::FilesystemQueue.claim_current` enforces this and raises `"current/ contains more than one PBI: [...]"` when invariant breaks.

Observed on `origin/ralph-queue` at 2026-05-27: `.ralph/current/` holds four entries. Three are stale:

| Entry | done/ exists? | Status |
|---|---|---|
| CONFIG-PROMOTE-SWEEP-KNOBS | yes | orphan (merged #30) |
| STAGE-B-PLAN-08 | yes | orphan (merged earlier) |
| STAGE-B-PLAN-10 | no | actually in-flight |
| SWEEP-RECONCILE-ORPHANS | yes | orphan (merged #31) |

Each stale entry contains only `HISTORY.md` (no `PBI.md` / `PLAN.md`).

### Root cause

The feature branch `ralph/<PBI-ID>` commits iteration progress into `.ralph/current/<PBI-ID>/HISTORY.md` (per the existing "feature-branch checkout of PBI HISTORY.md from ralph-queue" pattern — `PBI.md` and `PLAN.md` are .gitignored on the feature branch, but `HISTORY.md` is committed). When the PR squash-merges to `main`, that single file lands on `main` as a net addition under `current/<id>/`.

Periodic `Merge remote-tracking branch 'origin/main' into ralph-queue` commits (`4d62353`, `bef9362`, `e057157` on the current ralph-queue tip) replay the addition back onto `ralph-queue`, re-creating a `current/<id>/HISTORY.md` that the earlier `chore(ralph-queue): move <id> from current to pending-pr` commit had `git mv`'d away. Net effect: every merged PBI leaves a stale `current/<id>/HISTORY.md` on ralph-queue.

The existing `sweep/reconcile.py` (landed via PR #31) only scans `pending-pr/`, so these never get cleared.

## Decision

Add a `current/` reconciliation pass to `sweep/reconcile.py` and call it from the sweep loop. It is **filesystem-only** — no `lookup_by_branch` calls, because the disposition is unambiguous from the on-disk shape:

| `current/<id>/` shape on ralph-queue | Sibling state | Action |
|---|---|---|
| has only `HISTORY.md`, no `PBI.md` and no `PLAN.md` | `done/<id>/` exists | delete `current/<id>/` (merged elsewhere) |
| has only `HISTORY.md`, no `PBI.md` and no `PLAN.md` | `blocked/<id>/` exists | delete `current/<id>/` (rejected elsewhere) |
| has only `HISTORY.md`, no `PBI.md` and no `PLAN.md` | `pending-pr/<id>/` exists | delete `current/<id>/` (move-in-flight residue) |
| has only `HISTORY.md`, no `PBI.md` and no `PLAN.md` | no sibling | leave (could be transient; surface as warning, do not delete) |
| has `PBI.md` (with or without `PLAN.md` / `HISTORY.md`) | any | leave (genuine active claim — the in-flight invariant) |

The "has `PBI.md`" check is the key safety gate. A real claim is created by `move_inbox_to_current` (`queue/movements.py`), which `git mv`s the full inbox dir into `current/` — that dir always contains `PBI.md`. A HISTORY-only directory cannot have come from `move_inbox_to_current`; it can only have come from a feature-branch merge replay.

Action ordering: the `current/` pass runs **after** the existing `pending-pr/` reconcile pass, so if a sweep iteration first promotes `pending-pr/<id>/` to `done/<id>/`, the same iteration's `current/` pass can then delete any leftover `current/<id>/` shadow.

## Architecture

### Files

```
ralph_executor/sweep/
  reconcile.py            MODIFY: add reconcile_stale_current_all() + reconcile_stale_current_one()
  types.py                MODIFY: add CurrentReconcileAction enum + CurrentReconcileReport dataclass
  runner.py               MODIFY: call reconcile_stale_current_all after the pending-pr pass

tests/executor/sweep/
  test_reconcile_current.py     NEW
  test_runner.py                MODIFY: assert the new pass runs and counts surface in sweep result
```

No skill changes, no CLI subcommand changes. `ralph-executor reconcile` already exists and is the operator-on-demand entry point — extend its summary table to include current/ rows, but the same flow.

### Types (`sweep/types.py`)

```python
class CurrentReconcileAction(StrEnum):
    DELETED_DONE_SIBLING = "deleted_done_sibling"
    DELETED_BLOCKED_SIBLING = "deleted_blocked_sibling"
    DELETED_PENDING_SIBLING = "deleted_pending_sibling"
    KEEP_ACTIVE_CLAIM = "keep_active_claim"        # has PBI.md
    KEEP_NO_SIBLING = "keep_no_sibling"            # orphan with no sibling — warn but leave


@dataclass(frozen=True)
class CurrentReconcileReport:
    actions: Mapping[str, CurrentReconcileAction]
    errors: Mapping[str, str]
```

`KEEP_NO_SIBLING` is the "I don't know what this is" outcome — emit a `log.warning` so the operator sees it, but never delete on guess. In practice the only way to land in this bucket is operator intervention (someone wrote a bare HISTORY.md by hand), so leaving it alone is correct.

### API (`sweep/reconcile.py`)

```python
def reconcile_stale_current_one(
    pbi_dir: Path,
    ctx: SweepContext,
    *,
    dry_run: bool = False,
) -> CurrentReconcileAction:
    """Decide and (unless dry_run) apply the disposition for one
    `.ralph/current/<id>/` directory based on its shape and siblings.

    Returns the action. Raises ReconcileError on shutil.rmtree OSError."""


def reconcile_stale_current_all(
    ctx: SweepContext,
    *,
    dry_run: bool = False,
) -> CurrentReconcileReport:
    """Iterate every dir in `.ralph/current/`, decide per the table
    above, and (unless dry_run) delete the stale ones.

    Per-PBI failures captured in report.errors without aborting."""
```

Deletion uses `shutil.rmtree(pbi_dir)`. Wrap in try/except OSError → `ReconcileError` per existing `_move_dir` pattern.

### Sweep loop integration (`runner.py`)

After the existing pending-pr reconcile call, append:

```python
current_report = reconcile.reconcile_stale_current_all(ctx)
for pbi_id, action in current_report.actions.items():
    if action == CurrentReconcileAction.KEEP_NO_SIBLING:
        log.warning(
            "sweep: current/%s has no sibling in done/blocked/pending-pr "
            "and no PBI.md; leaving for operator review",
            pbi_id,
        )
```

`current_report.errors` merges into the sweep result error dict (same shape as the existing reconcile_all error merge — extend the result type if a new sub-field is preferred).

### Commit + push

Deletions on `.ralph/current/<id>/` happen on the queue worktree (ctx.queue_root). The sweep loop already has `git add -A` + `git commit` + `git push` semantics on the queue worktree for the pending-pr reconcile pass. The same flush covers the current/ deletions — no second commit needed if both passes are in the same iteration.

Commit message format: `chore(ralph-queue): reconcile stale current/<id>/ (sibling: done)` per deleted dir, OR a single grouped commit `chore(ralph-queue): reconcile <N> stale current/ entries` listing the deletions in the body. Group form preferred — fewer commits, simpler git log.

## Error handling

| Failure | Response |
|---|---|
| `shutil.rmtree` OSError | per-PBI: capture as `ReconcileError`, log + add to errors dict, continue with other dirs |
| `current/<id>/PBI.md` is partially present (e.g. just `PBI.md` missing but `PLAN.md` present) | leave (treat as active claim — defensive: only the **absence of PBI.md** triggers deletion) |
| Branch is not `ralph-queue` when reconcile runs | not applicable in worktree mode (queue worktree is always pinned). Legacy single-checkout: ctx already does the checkout in `_move`; the same path covers reconcile |
| `.ralph/current/.gitkeep` enumerated as a dir-entry | skip non-directory entries (matches existing `reconcile_all` iteration) |

## Testing (`tests/executor/sweep/test_reconcile_current.py`)

Per-shape unit tests, each builds a fake queue_root tmpdir:

- `current/X/HISTORY.md` + `done/X/PBI.md` → `DELETED_DONE_SIBLING`; current/X gone after call
- `current/X/HISTORY.md` + `blocked/X/PBI.md` → `DELETED_BLOCKED_SIBLING`
- `current/X/HISTORY.md` + `pending-pr/X/PBI.md` → `DELETED_PENDING_SIBLING`
- `current/X/HISTORY.md` alone (no sibling) → `KEEP_NO_SIBLING`; current/X still present
- `current/X/PBI.md` + `current/X/PLAN.md` (active) + `done/X/...` sibling → `KEEP_ACTIVE_CLAIM` (PBI.md presence wins over sibling — invariant: don't delete an active claim even if a stale done/ leak exists)
- `current/X/PBI.md` alone → `KEEP_ACTIVE_CLAIM`
- `dry_run=True` on a delete case → returns the action but the dir still exists
- `shutil.rmtree` raises OSError (monkeypatch) → `ReconcileError`; other entries in same `reconcile_stale_current_all` batch still processed
- `reconcile_stale_current_all` on empty `.ralph/current/` → empty report
- `reconcile_stale_current_all` skips `.gitkeep` (non-dir / dot-prefix)

`tests/executor/sweep/test_runner.py` additions:

- Sweep iteration with one stale current/X (done sibling exists) → current/X deleted by end of iteration; sweep result counts include the deletion
- Sweep iteration where current pass raises mid-loop → other actions still complete; error surfaces in result
- Existing "single in-flight current" test: must still pass (`KEEP_ACTIVE_CLAIM` is a no-op)

## Acceptance

- `ralph_executor/sweep/reconcile.py` exposes `reconcile_stale_current_one` + `reconcile_stale_current_all`
- Sweep loop calls `reconcile_stale_current_all` after the existing pending-pr reconcile pass
- After the first sweep iteration following merge of this PBI: `.ralph/current/` on `ralph-queue` contains only `STAGE-B-PLAN-10/` (the genuinely in-flight claim) plus `.gitkeep`
- `claim_current()`'s "more than one PBI" guard no longer fires under normal operation
- pytest, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy ralph_executor scripts skills tests` all clean
- `ralph-executor reconcile [--dry-run]` table includes current/ rows (one line per inspected current/ dir)

## Out of scope

- Preventing the leak at the source (e.g. stop committing `.ralph/current/<id>/HISTORY.md` on feature branches, or filter on the main → ralph-queue merge). Considered and deferred: changing the HISTORY.md path on the feature branch is a wider rework (touches `claude_spawn.py`, PROMPT.md, and the executor's history-checkout invariant) and the documented pattern is load-bearing. A janitor pass is the smaller blast radius.
- Reconciling `inbox/` or other queue dirs.
- Auto-recovery when `current/X/PBI.md` IS present but the claim is genuinely abandoned (no commits in N days). Stuck-claim detection is `safety/stuck.py`'s job — distinct concern.

## Risks

| Risk | Mitigation |
|---|---|
| Deleting a real in-flight claim that happens to be missing PBI.md | Invariant: `move_inbox_to_current` always brings PBI.md along (git mv of inbox dir). Absence of PBI.md is sufficient evidence the dir is not an active claim. Backed by `KEEP_NO_SIBLING` warn-don't-delete fallback for paranoid cases. |
| Race: feature branch in-progress merges main → ralph-queue while sweep runs | Sweep is single-threaded in the executor loop; queue worktree is on `ralph-queue`. The merge that resurrects current/<id>/HISTORY.md is performed by `setup_cmds.py` / loop merge step, which runs in the same loop iteration. The next sweep iteration catches it. |
| Operator hand-creates `current/X/HISTORY.md` for diagnostic purposes | `KEEP_NO_SIBLING` path leaves it alone with a warning. Operator sees the warning and decides. |
| Spike of "deleted" actions in the first sweep after this PBI lands (4 deletes at once) | Expected. The grouped commit message lists every deletion in the body for audit. |

## depends_on

None. Existing reconcile module is the natural place; types extension is additive; no overlap with the in-flight PBI (`STAGE-B-PLAN-10`).
