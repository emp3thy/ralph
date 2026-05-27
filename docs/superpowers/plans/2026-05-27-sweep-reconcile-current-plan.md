# Sweep Reconciles Stale `.ralph/current/` Entries — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sweep auto-deletes stale `.ralph/current/<PBI-ID>/` directories that lack `PBI.md` and have a sibling in `done/`, `blocked/`, or `pending-pr/`. Purely filesystem-driven — no host API calls. Removes the recurring "orphan current/" leak caused by feature-branch HISTORY.md commits being replayed onto ralph-queue via `merge main`.

**Architecture:** Extends `ralph_executor/sweep/reconcile.py` with two new functions (`reconcile_stale_current_one`, `reconcile_stale_current_all`) and `sweep/types.py` with two new types (`CurrentReconcileAction`, `CurrentReconcileReport`). `sweep/runner.py::run()` calls the all-iterator after the existing pending-pr reconcile pass. `cli.py::_cmd_reconcile` extends its summary table to include current/ rows.

**Tech Stack:** Python 3.12, `shutil.rmtree`, `pathlib`, `StrEnum`, `@dataclass(frozen=True)`. pytest, ruff, mypy strict. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-27-sweep-reconcile-current-design.md`

---

## Confidence per task

All tasks ≥ 90%. Pre-flight: types module, reconcile module, runner call-site, and CLI dispatch handler all verified against current repo state.

| Task | % | Notes |
|---|---|---|
| 1. Add types + failing tests for current-reconcile | 95% | `StrEnum` + frozen-dataclass pattern at `sweep/types.py:71,106` — direct precedent. Failing tests assert on functions that don't yet exist; collection error is the failure signal. |
| 2. Implement `reconcile_stale_current_one` + `reconcile_stale_current_all` | 93% | Pure filesystem decisions. `shutil.rmtree` wrap matches existing `_move_dir` pattern at `reconcile.py:188`. Risk: ordering of sibling checks must match the spec table — test cases enumerate every shape. |
| 3. Wire into `sweep/runner.py::run()` | 94% | Insert point is immediately after the existing pending-pr loop, before `return SweepResult(...)` at `runner.py:239`. No change to `SweepResult` shape — errors merge into existing `errors` tuple, actions emit as `log.info`. |
| 4. Extend `cli.py::_cmd_reconcile` to print current/ rows | 92% | `_print_reconcile_report` at `cli.py:423` handles the pending-pr table; add a sibling `_print_current_reconcile_report` and call both from `_cmd_reconcile`. The CLI also calls the new `reconcile_stale_current_all`. |
| 5. Full-suite green + grep for legacy expectations | 91% | Risk: existing tests in `tests/executor/sweep/test_runner.py` may assert exact-shape sweep results that the new info-log doesn't alter, but errors aggregation could change row counts if any current/ test PBI happens to be set up. Step 5.1 greps to find such tests first. |

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `ralph_executor/sweep/types.py` | Modify | Append `CurrentReconcileAction` (StrEnum) + `CurrentReconcileReport` (frozen dataclass). |
| `ralph_executor/sweep/reconcile.py` | Modify | Append `reconcile_stale_current_one` + `reconcile_stale_current_all`. Reuse existing `ReconcileError`. |
| `ralph_executor/sweep/runner.py` | Modify | In `run()`, after the existing pending-pr loop and before `return SweepResult`, call `reconcile_stale_current_all`, merge its errors into the result, and log per-action lines. |
| `ralph_executor/cli.py` | Modify | In `_cmd_reconcile`, also call `reconcile_stale_current_all` and print its report. Add `_print_current_reconcile_report` helper next to `_print_reconcile_report`. |
| `tests/executor/sweep/test_reconcile_current.py` | Create | Per-shape unit tests for `reconcile_stale_current_one` + `reconcile_stale_current_all`. |
| `tests/executor/sweep/test_runner.py` | Modify | Add a test asserting `run()` deletes a stale `current/X/` whose sibling exists in `done/`, alongside processing the normal pending-pr loop. |
| `tests/executor/test_cli_reconcile.py` | Modify | Extend with a case asserting the CLI prints the current/ table alongside the pending-pr table. |

---

## Task 1: Add types + failing tests for current-reconcile

**Files:**
- Modify: `ralph_executor/sweep/types.py` (append at end)
- Create: `tests/executor/sweep/test_reconcile_current.py`

- [ ] **Step 1.1: Append the new types to `sweep/types.py`**

Append at the end of `ralph_executor/sweep/types.py`:

```python


class CurrentReconcileAction(StrEnum):
    DELETED_DONE_SIBLING = "deleted_done_sibling"
    DELETED_BLOCKED_SIBLING = "deleted_blocked_sibling"
    DELETED_PENDING_SIBLING = "deleted_pending_sibling"
    KEEP_ACTIVE_CLAIM = "keep_active_claim"
    KEEP_NO_SIBLING = "keep_no_sibling"


@dataclass(frozen=True)
class CurrentReconcileReport:
    """Aggregate of reconcile_stale_current_all over .ralph/current/.

    ``actions`` maps each scanned PBI id to its chosen action (including
    KEEP outcomes — full audit of what was inspected).
    ``errors`` carries human-readable error strings for PBIs that
    raised ReconcileError during rmtree.
    """

    actions: Mapping[str, "CurrentReconcileAction"]
    errors: Mapping[str, str]
```

`Mapping` is already imported at line 9. `dataclass`, `StrEnum` already imported. No additional imports.

- [ ] **Step 1.2: Run mypy + ruff on the types file**

```bash
uv run ruff check ralph_executor/sweep/types.py
uv run mypy --strict ralph_executor/sweep/types.py
```

Expected: green.

- [ ] **Step 1.3: Create the failing tests file**

Create `tests/executor/sweep/test_reconcile_current.py`:

```python
"""Tests for the current/ reconciliation pass in
ralph_executor.sweep.reconcile.

Companion to ``test_reconcile.py`` which covers the pending-pr/ pass.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.sweep.reconcile import (
    ReconcileError,
    reconcile_stale_current_all,
    reconcile_stale_current_one,
)
from ralph_executor.sweep.runner import SweepConfig, SweepContext
from ralph_executor.sweep.types import CurrentReconcileAction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_queue_root(tmp_path: Path) -> Path:
    """A .ralph/ skeleton with empty inbox/current/pending-pr/done/blocked."""
    queue = tmp_path / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue / sub).mkdir(parents=True)
    return queue


@pytest.fixture
def fake_ctx(fake_queue_root: Path, tmp_path: Path) -> SweepContext:
    scripts_path = tmp_path / "pr_scripts"
    scripts_path.mkdir()
    return SweepContext(
        queue_root=fake_queue_root,
        ado_pr_scripts_path=scripts_path,
        config=SweepConfig(
            ralph_author_email="ralph@example.com",
            max_attempts=20,
            stale_threshold=timedelta(days=3),
            now=datetime.now(tz=UTC),
        ),
    )


def _make_orphan_current(queue: Path, pbi_id: str) -> Path:
    """Mirror the on-disk shape produced by a feature-branch HISTORY.md
    replay: only HISTORY.md is present.
    """
    d = queue / "current" / pbi_id
    d.mkdir()
    (d / "HISTORY.md").write_text("# iteration 1\n", encoding="utf-8")
    return d


def _make_sibling(queue: Path, state: str, pbi_id: str) -> Path:
    """Create a placeholder sibling dir (done/, blocked/, or pending-pr/).
    The actual content is irrelevant — reconcile only checks existence.
    """
    d = queue / state / pbi_id
    d.mkdir()
    (d / "PBI.md").write_text(f"---\nid: {pbi_id}\n---\n", encoding="utf-8")
    return d


def _make_active_claim(queue: Path, pbi_id: str) -> Path:
    """Real claim shape: PBI.md (always), usually PLAN.md + HISTORY.md."""
    d = queue / "current" / pbi_id
    d.mkdir()
    (d / "PBI.md").write_text(f"---\nid: {pbi_id}\n---\n", encoding="utf-8")
    (d / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    (d / "HISTORY.md").write_text("# history\n", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# reconcile_stale_current_one — single-PBI decisions
# ---------------------------------------------------------------------------


def test_stale_current_with_done_sibling_is_deleted(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    orphan = _make_orphan_current(fake_queue_root, "X")
    _make_sibling(fake_queue_root, "done", "X")

    action = reconcile_stale_current_one(orphan, fake_ctx)

    assert action == CurrentReconcileAction.DELETED_DONE_SIBLING
    assert not orphan.exists()


def test_stale_current_with_blocked_sibling_is_deleted(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    orphan = _make_orphan_current(fake_queue_root, "X")
    _make_sibling(fake_queue_root, "blocked", "X")

    action = reconcile_stale_current_one(orphan, fake_ctx)

    assert action == CurrentReconcileAction.DELETED_BLOCKED_SIBLING
    assert not orphan.exists()


def test_stale_current_with_pending_sibling_is_deleted(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    orphan = _make_orphan_current(fake_queue_root, "X")
    _make_sibling(fake_queue_root, "pending-pr", "X")

    action = reconcile_stale_current_one(orphan, fake_ctx)

    assert action == CurrentReconcileAction.DELETED_PENDING_SIBLING
    assert not orphan.exists()


def test_active_claim_is_never_deleted_even_with_done_sibling(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    """A current/<id>/ that has PBI.md is an active claim. Even if a stale
    done/<id>/ leaked in from history, the claim wins."""
    claim = _make_active_claim(fake_queue_root, "X")
    _make_sibling(fake_queue_root, "done", "X")

    action = reconcile_stale_current_one(claim, fake_ctx)

    assert action == CurrentReconcileAction.KEEP_ACTIVE_CLAIM
    assert claim.exists()
    assert (claim / "PBI.md").is_file()


def test_active_claim_with_only_pbi_md_is_kept(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    """PBI.md alone (no PLAN.md / HISTORY.md) is still an active claim."""
    d = fake_queue_root / "current" / "X"
    d.mkdir()
    (d / "PBI.md").write_text("---\nid: X\n---\n", encoding="utf-8")

    action = reconcile_stale_current_one(d, fake_ctx)

    assert action == CurrentReconcileAction.KEEP_ACTIVE_CLAIM
    assert d.exists()


def test_orphan_with_no_sibling_is_kept_with_warning(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    """No sibling anywhere → don't guess; leave it for operator review.
    The action is KEEP_NO_SIBLING; the caller emits a warning."""
    orphan = _make_orphan_current(fake_queue_root, "X")

    action = reconcile_stale_current_one(orphan, fake_ctx)

    assert action == CurrentReconcileAction.KEEP_NO_SIBLING
    assert orphan.exists()


def test_dry_run_does_not_delete(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    orphan = _make_orphan_current(fake_queue_root, "X")
    _make_sibling(fake_queue_root, "done", "X")

    action = reconcile_stale_current_one(orphan, fake_ctx, dry_run=True)

    assert action == CurrentReconcileAction.DELETED_DONE_SIBLING
    assert orphan.exists(), "dry-run must not delete"


def test_rmtree_oserror_is_wrapped_in_reconcile_error(
    fake_queue_root: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orphan = _make_orphan_current(fake_queue_root, "X")
    _make_sibling(fake_queue_root, "done", "X")

    def _boom(path: str) -> None:
        raise OSError("simulated permission denied")

    monkeypatch.setattr("ralph_executor.sweep.reconcile.shutil.rmtree", _boom)

    with pytest.raises(ReconcileError):
        reconcile_stale_current_one(orphan, fake_ctx)


def test_sibling_precedence_done_over_blocked_over_pending(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    """If multiple siblings exist (shouldn't happen normally but defensively):
    done wins over blocked over pending. The action reflects the chosen
    classifier; the deletion happens once."""
    orphan = _make_orphan_current(fake_queue_root, "X")
    _make_sibling(fake_queue_root, "done", "X")
    _make_sibling(fake_queue_root, "blocked", "X")
    _make_sibling(fake_queue_root, "pending-pr", "X")

    action = reconcile_stale_current_one(orphan, fake_ctx)

    assert action == CurrentReconcileAction.DELETED_DONE_SIBLING
    assert not orphan.exists()


# ---------------------------------------------------------------------------
# reconcile_stale_current_all — iteration
# ---------------------------------------------------------------------------


def test_all_processes_every_current_dir(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    _make_orphan_current(fake_queue_root, "A")
    _make_sibling(fake_queue_root, "done", "A")
    _make_orphan_current(fake_queue_root, "B")
    _make_sibling(fake_queue_root, "blocked", "B")
    _make_active_claim(fake_queue_root, "C")

    report = reconcile_stale_current_all(fake_ctx)

    assert set(report.actions.keys()) == {"A", "B", "C"}
    assert report.actions["A"] == CurrentReconcileAction.DELETED_DONE_SIBLING
    assert report.actions["B"] == CurrentReconcileAction.DELETED_BLOCKED_SIBLING
    assert report.actions["C"] == CurrentReconcileAction.KEEP_ACTIVE_CLAIM
    assert not (fake_queue_root / "current" / "A").exists()
    assert not (fake_queue_root / "current" / "B").exists()
    assert (fake_queue_root / "current" / "C").exists()


def test_all_skips_dotfiles_and_non_dirs(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    """`.gitkeep` and any other non-dir entry must be ignored."""
    (fake_queue_root / "current" / ".gitkeep").write_text("", encoding="utf-8")
    _make_orphan_current(fake_queue_root, "REAL")
    _make_sibling(fake_queue_root, "done", "REAL")

    report = reconcile_stale_current_all(fake_ctx)

    assert ".gitkeep" not in report.actions
    assert "REAL" in report.actions


def test_all_isolates_per_pbi_rmtree_failures(
    fake_queue_root: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_orphan_current(fake_queue_root, "A")
    _make_sibling(fake_queue_root, "done", "A")
    _make_orphan_current(fake_queue_root, "B")
    _make_sibling(fake_queue_root, "done", "B")

    call_count = {"n": 0}
    real_rmtree = __import__("shutil").rmtree

    def _flaky(path: str) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated failure on first call")
        real_rmtree(path)

    monkeypatch.setattr("ralph_executor.sweep.reconcile.shutil.rmtree", _flaky)

    report = reconcile_stale_current_all(fake_ctx)

    # One PBI errored; the other still processed.
    assert len(report.errors) == 1
    assert len(report.actions) == 1


def test_all_on_empty_current_returns_empty_report(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    report = reconcile_stale_current_all(fake_ctx)
    assert dict(report.actions) == {}
    assert dict(report.errors) == {}


def test_all_dry_run_keeps_dirs_but_populates_report(
    fake_queue_root: Path, fake_ctx: SweepContext
) -> None:
    _make_orphan_current(fake_queue_root, "X")
    _make_sibling(fake_queue_root, "done", "X")

    report = reconcile_stale_current_all(fake_ctx, dry_run=True)

    assert report.actions["X"] == CurrentReconcileAction.DELETED_DONE_SIBLING
    assert (fake_queue_root / "current" / "X").exists()
```

- [ ] **Step 1.4: Confirm failing tests fail at import time**

```bash
uv run pytest tests/executor/sweep/test_reconcile_current.py -v
```

Expected: collection error — `ImportError: cannot import 'reconcile_stale_current_all' from 'ralph_executor.sweep.reconcile'`. Or, if the import succeeds but the functions are absent, an `AttributeError` at first test call.

- [ ] **Step 1.5: Commit**

```powershell
git add ralph_executor/sweep/types.py tests/executor/sweep/test_reconcile_current.py
git commit -m "test(sweep): failing tests for current/ reconciliation + CurrentReconcileAction/Report types"
```

---

## Task 2: Implement `reconcile_stale_current_one` + `reconcile_stale_current_all`

**Files:**
- Modify: `ralph_executor/sweep/reconcile.py` (append at end)

- [ ] **Step 2.1: Append the per-orphan reconciler**

Append to `ralph_executor/sweep/reconcile.py`:

```python


# ---------------------------------------------------------------------------
# Stale current/ reconciliation
# ---------------------------------------------------------------------------
#
# Background: feature branches commit ``.ralph/current/<id>/HISTORY.md``
# during iteration. When the feature PR squash-merges into ``main``, that
# file lands on main. Subsequent ``merge main into ralph-queue`` commits
# resurrect ``current/<id>/HISTORY.md`` on the queue branch, undoing the
# ``move current to pending-pr`` ``git mv`` from the original cycle. The
# orphan never gets cleaned up because the existing reconcile pass only
# scans ``pending-pr/``. This pass closes that gap by deleting any
# ``current/<id>/`` that lacks ``PBI.md`` AND has a sibling somewhere
# else in the queue.

from ralph_executor.sweep.types import (
    CurrentReconcileAction,
    CurrentReconcileReport,
)

_CURRENT_SIBLING_PRECEDENCE: tuple[tuple[str, CurrentReconcileAction], ...] = (
    ("done", CurrentReconcileAction.DELETED_DONE_SIBLING),
    ("blocked", CurrentReconcileAction.DELETED_BLOCKED_SIBLING),
    ("pending-pr", CurrentReconcileAction.DELETED_PENDING_SIBLING),
)


def reconcile_stale_current_one(
    pbi_dir: Path,
    ctx: SweepContext,
    *,
    dry_run: bool = False,
) -> CurrentReconcileAction:
    """Decide and (unless dry_run) apply the disposition for one
    ``.ralph/current/<id>/`` directory.

    Decision table (spec):
      - has PBI.md anywhere in the dir → KEEP_ACTIVE_CLAIM (never delete)
      - no PBI.md AND sibling exists in done/   → DELETED_DONE_SIBLING
      - no PBI.md AND sibling exists in blocked/ → DELETED_BLOCKED_SIBLING
      - no PBI.md AND sibling exists in pending-pr/ → DELETED_PENDING_SIBLING
      - no PBI.md AND no sibling → KEEP_NO_SIBLING (leave + warn)

    Raises ReconcileError on shutil.rmtree OSError.
    """
    if (pbi_dir / "PBI.md").is_file():
        return CurrentReconcileAction.KEEP_ACTIVE_CLAIM

    pbi_id = pbi_dir.name
    queue_root = ctx.queue_root

    for state, action in _CURRENT_SIBLING_PRECEDENCE:
        sibling = queue_root / state / pbi_id
        if sibling.is_dir():
            if not dry_run:
                _rmtree_dir(pbi_dir)
            return action

    return CurrentReconcileAction.KEEP_NO_SIBLING


def _rmtree_dir(path: Path) -> None:
    """``shutil.rmtree`` wrapped to surface OSError as ReconcileError.

    Matches ``_move_dir``'s OSError-to-ReconcileError contract so callers
    can treat both move and delete failures uniformly.
    """
    try:
        shutil.rmtree(str(path))
    except OSError as exc:
        raise ReconcileError(f"failed to delete {path}: {exc}") from exc
```

- [ ] **Step 2.2: Append the iterator**

```python
def reconcile_stale_current_all(
    ctx: SweepContext,
    *,
    dry_run: bool = False,
) -> CurrentReconcileReport:
    """Iterate ``.ralph/current/`` and reconcile every entry.

    Per-PBI failures are captured in ``report.errors`` without aborting.
    Non-directory entries (``.gitkeep``, etc.) are skipped.
    """
    current_dir = ctx.queue_root / "current"
    actions: dict[str, CurrentReconcileAction] = {}
    errors: dict[str, str] = {}

    if not current_dir.is_dir():
        return CurrentReconcileReport(actions={}, errors={})

    for entry in sorted(current_dir.iterdir()):
        if not entry.is_dir():
            continue
        pbi_id = entry.name
        try:
            action = reconcile_stale_current_one(entry, ctx, dry_run=dry_run)
            actions[pbi_id] = action
            log.info(
                "reconcile-current: %s -> %s%s",
                pbi_id,
                action.value,
                " (dry-run)" if dry_run else "",
            )
        except ReconcileError as err:
            errors[pbi_id] = str(err)
            log.warning("reconcile-current: failed for %s: %s", pbi_id, err)

    return CurrentReconcileReport(actions=actions, errors=errors)
```

- [ ] **Step 2.3: Run the failing tests**

```bash
uv run pytest tests/executor/sweep/test_reconcile_current.py -v
```

Expected: **all PASS**.

If `test_sibling_precedence_done_over_blocked_over_pending` fails because precedence differs: the implementation iterates `_CURRENT_SIBLING_PRECEDENCE` in order, so done is checked first. The test asserts done wins; the implementation matches.

- [ ] **Step 2.4: Run ruff + mypy on the modified file**

```bash
uv run ruff check ralph_executor/sweep/reconcile.py
uv run mypy --strict ralph_executor/sweep/reconcile.py
```

Expected: green.

- [ ] **Step 2.5: Commit**

```powershell
git add ralph_executor/sweep/reconcile.py
git commit -m "feat(sweep): reconcile_stale_current_one + reconcile_stale_current_all"
```

---

## Task 3: Wire into `sweep/runner.py::run()`

**Files:**
- Modify: `ralph_executor/sweep/runner.py` (lines 200–243 area)
- Modify: `tests/executor/sweep/test_runner.py` (append new test)

- [ ] **Step 3.1: Verify the insertion point**

```bash
uv run python -c "from ralph_executor.sweep.runner import run; import inspect; print(inspect.getsourcefile(run))"
```

Find the `return SweepResult(...)` at the end of `run()` (currently around line 239). The current/ pass goes immediately before that `return`.

- [ ] **Step 3.2: Modify `run()` to invoke the current/ pass**

Edit `ralph_executor/sweep/runner.py`. After the existing pending-pr `for pbi_dir in pbis:` loop and before `return SweepResult(...)`, insert:

```python
    # Reconcile stale .ralph/current/ entries (filesystem-only janitor pass).
    # Runs AFTER the pending-pr loop so that an iteration which promotes
    # pending-pr/<id>/ to done/<id>/ above can also delete the leftover
    # current/<id>/ shadow in the same pass.
    from ralph_executor.sweep.reconcile import reconcile_stale_current_all
    from ralph_executor.sweep.types import CurrentReconcileAction

    current_report = reconcile_stale_current_all(ctx)
    for pbi_id, current_action in current_report.actions.items():
        if current_action == CurrentReconcileAction.KEEP_NO_SIBLING:
            log.warning(
                "sweep: current/%s has no sibling in done/blocked/pending-pr "
                "and no PBI.md; leaving for operator review",
                pbi_id,
            )
    for pbi_id, current_err in current_report.errors.items():
        errors.append(f"current/{pbi_id}: reconcile error: {current_err}")
```

The local import mirrors the existing local-import pattern at `run()`'s top (lines 207–211) to avoid the same `SweepContext` cycle. `SweepResult`'s shape is unchanged — `current_report.errors` flow into the existing `errors` tuple; actions surface as `log.info` lines from inside `reconcile_stale_current_all`.

- [ ] **Step 3.3: Append a sweep test**

Append to `tests/executor/sweep/test_runner.py`:

```python
def test_run_deletes_stale_current_with_done_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run` deletes a current/<id>/ orphan whose sibling lives in done/."""
    queue = tmp_path / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue / sub).mkdir(parents=True)

    # Orphan in current/ (HISTORY.md only — the feature-branch-replay shape).
    orphan = queue / "current" / "MERGED-PBI"
    orphan.mkdir()
    (orphan / "HISTORY.md").write_text("# iter 1\n", encoding="utf-8")

    # Sibling in done/ (real PBI metadata).
    done = queue / "done" / "MERGED-PBI"
    done.mkdir()
    (done / "PBI.md").write_text("---\nid: MERGED-PBI\n---\n", encoding="utf-8")

    from datetime import UTC, datetime, timedelta

    from ralph_executor.sweep.runner import SweepConfig, SweepContext, run

    scripts_path = tmp_path / "pr_scripts"
    scripts_path.mkdir()

    ctx = SweepContext(
        queue_root=queue,
        ado_pr_scripts_path=scripts_path,
        config=SweepConfig(
            ralph_author_email="ralph@example.com",
            max_attempts=20,
            stale_threshold=timedelta(days=3),
            now=datetime.now(tz=UTC),
        ),
    )

    result = run(ctx=ctx)

    assert not orphan.exists(), "stale current/ entry must be deleted"
    assert done.exists(), "sibling in done/ must be left alone"
    assert result.errors == ()


def test_run_keeps_active_current_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run` does not touch a real active claim (PBI.md present)."""
    queue = tmp_path / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue / sub).mkdir(parents=True)

    claim = queue / "current" / "ACTIVE"
    claim.mkdir()
    (claim / "PBI.md").write_text("---\nid: ACTIVE\n---\n", encoding="utf-8")
    (claim / "PLAN.md").write_text("# plan\n", encoding="utf-8")

    from datetime import UTC, datetime, timedelta

    from ralph_executor.sweep.runner import SweepConfig, SweepContext, run

    scripts_path = tmp_path / "pr_scripts"
    scripts_path.mkdir()

    ctx = SweepContext(
        queue_root=queue,
        ado_pr_scripts_path=scripts_path,
        config=SweepConfig(
            ralph_author_email="ralph@example.com",
            max_attempts=20,
            stale_threshold=timedelta(days=3),
            now=datetime.now(tz=UTC),
        ),
    )

    run(ctx=ctx)

    assert claim.exists()
    assert (claim / "PBI.md").is_file()
    assert (claim / "PLAN.md").is_file()


def test_run_surfaces_no_sibling_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`current/<id>/` with no sibling and no PBI.md gets a WARNING but
    is not deleted — paranoia fallback."""
    queue = tmp_path / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue / sub).mkdir(parents=True)
    orphan = queue / "current" / "MYSTERY"
    orphan.mkdir()
    (orphan / "HISTORY.md").write_text("hand-written\n", encoding="utf-8")

    from datetime import UTC, datetime, timedelta

    from ralph_executor.sweep.runner import SweepConfig, SweepContext, run

    scripts_path = tmp_path / "pr_scripts"
    scripts_path.mkdir()

    ctx = SweepContext(
        queue_root=queue,
        ado_pr_scripts_path=scripts_path,
        config=SweepConfig(
            ralph_author_email="ralph@example.com",
            max_attempts=20,
            stale_threshold=timedelta(days=3),
            now=datetime.now(tz=UTC),
        ),
    )

    import logging
    caplog.set_level(logging.WARNING, logger="ralph_executor.sweep.runner")
    run(ctx=ctx)

    assert orphan.exists()
    assert any("MYSTERY" in rec.message and "operator review" in rec.message
               for rec in caplog.records), caplog.text
```

- [ ] **Step 3.4: Run the sweep test module**

```bash
uv run pytest tests/executor/sweep/ -v
```

Expected: all green. The new tests pass; existing tests must continue to pass (the new current/ pass has no effect when `current/` is empty, which is the typical test setup).

- [ ] **Step 3.5: Run ruff + mypy on the touched files**

```bash
uv run ruff check ralph_executor/sweep/runner.py tests/executor/sweep/test_runner.py
uv run mypy --strict ralph_executor/sweep/runner.py
```

- [ ] **Step 3.6: Commit**

```powershell
git add ralph_executor/sweep/runner.py tests/executor/sweep/test_runner.py
git commit -m "feat(sweep): run() also reconciles stale current/ entries"
```

---

## Task 4: Extend `cli.py::_cmd_reconcile`

**Files:**
- Modify: `ralph_executor/cli.py` (around lines 418–445)
- Modify: `tests/executor/test_cli_reconcile.py` (append a new test)

- [ ] **Step 4.1: Locate the existing CLI handler**

The handler is `_cmd_reconcile` at `cli.py:372` and the printer is `_print_reconcile_report` at `cli.py:423`. The `reconcile` call site is line 418.

- [ ] **Step 4.2: Extend `_cmd_reconcile`**

In `ralph_executor/cli.py`, replace the body of `_cmd_reconcile` from the `report = reconcile_all(...)` line to the `return 0`:

```python
    report = reconcile_all(ctx, dry_run=args.dry_run)
    _print_reconcile_report(report, dry_run=args.dry_run)

    from ralph_executor.sweep.reconcile import reconcile_stale_current_all

    current_report = reconcile_stale_current_all(ctx, dry_run=args.dry_run)
    _print_current_reconcile_report(current_report, dry_run=args.dry_run)
    return 0
```

- [ ] **Step 4.3: Add the printer**

Immediately after `_print_reconcile_report` in `cli.py`, add:

```python
def _print_current_reconcile_report(
    report: "CurrentReconcileReport",
    *,
    dry_run: bool,
) -> None:
    """Print a one-line-per-entry summary for the current/ reconciliation pass."""
    prefix = "would: " if dry_run else ""
    if not report.actions and not report.errors:
        print("reconcile-current: no current/ entries found")
        return
    print()
    print(f"{'PBI-ID':<40} {'Current/ action':<25}")
    print("-" * 67)
    for pbi_id, action in sorted(report.actions.items()):
        print(f"{pbi_id:<40} {prefix}{action.value:<25}")
    for pbi_id, err in sorted(report.errors.items()):
        print(f"{pbi_id:<40} ERROR: {err}")
```

Add the import at the top of `cli.py` next to the existing `ReconcileReport` import (line 69):

```python
from ralph_executor.sweep.types import CurrentReconcileReport, ReconcileReport
```

Make sure to use the actual class (`CurrentReconcileReport`), not a string forward-ref, in the function signature (the string was a hedge for the snippet above — replace with the real import).

- [ ] **Step 4.4: Add the CLI test**

Append to `tests/executor/test_cli_reconcile.py`:

```python
def test_reconcile_cli_prints_current_section_when_stale_orphan_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`ralph-executor reconcile` prints the current/ section after the
    pending-pr section when a stale current/<id>/ exists."""
    # Set up a queue layout with one stale current/ orphan that has a
    # sibling in done/. No pending-pr orphans — pending pass should print
    # the "no orphans" line, the current pass should print one row.
    queue = tmp_path / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue / sub).mkdir(parents=True)
    orphan = queue / "current" / "MERGED-X"
    orphan.mkdir()
    (orphan / "HISTORY.md").write_text("iter\n", encoding="utf-8")
    sibling = queue / "done" / "MERGED-X"
    sibling.mkdir()
    (sibling / "PBI.md").write_text("---\nid: MERGED-X\n---\n", encoding="utf-8")

    # Build the same kind of ctx the CLI handler builds. Bypass
    # _cmd_reconcile's config-loading by monkeypatching it to use our
    # local queue root.
    from ralph_executor.cli import _cmd_reconcile

    class _Args:
        repo = str(tmp_path)
        workspace = None
        dry_run = False

    # Monkeypatch the load_config + scripts-path discovery to point at
    # the in-test queue.
    import ralph_executor.cli as cli_mod

    def _fake_load_config() -> object:
        from ralph_executor.config import ExecutorConfig  # adjust import to project's real shape
        # Build the minimal cfg the handler needs — only repo_path,
        # queue_branch, git_host, and the bot_author_email are touched.
        return ExecutorConfig(  # type: ignore[call-arg]
            repo_path=tmp_path,
            queue_branch="ralph-queue",
            git_host="github",
            bot_author_email="ralph@example.com",
            # remaining fields fall back to defaults
        )

    # If the real load_config requires more fields, accept the test must
    # adapt — but the assertion below is about output shape, so as long
    # as _cmd_reconcile reaches the print calls the test still asserts
    # what matters. If the constructor needs more args, switch to
    # invoking the helpers directly:
    #   from ralph_executor.sweep.reconcile import reconcile_stale_current_all
    #   from ralph_executor.cli import _print_current_reconcile_report
    #   ctx = ... (build SweepContext directly, as in test_runner.py)
    #   _print_current_reconcile_report(reconcile_stale_current_all(ctx), dry_run=False)
    # and assert on capsys.

    # Direct-call form (works without faking load_config):
    from datetime import UTC, datetime, timedelta

    from ralph_executor.cli import _print_current_reconcile_report
    from ralph_executor.sweep.reconcile import reconcile_stale_current_all
    from ralph_executor.sweep.runner import SweepConfig, SweepContext

    scripts_path = tmp_path / "pr_scripts"
    scripts_path.mkdir()
    ctx = SweepContext(
        queue_root=queue,
        ado_pr_scripts_path=scripts_path,
        config=SweepConfig(
            ralph_author_email="ralph@example.com",
            max_attempts=20,
            stale_threshold=timedelta(days=3),
            now=datetime.now(tz=UTC),
        ),
    )
    report = reconcile_stale_current_all(ctx)
    _print_current_reconcile_report(report, dry_run=False)
    captured = capsys.readouterr()
    assert "MERGED-X" in captured.out
    assert "deleted_done_sibling" in captured.out
    assert not orphan.exists()
```

(Note: the test ends up testing the printer + reconciler directly to keep it free of `load_config` mocking complexity. The end-to-end `_cmd_reconcile` path is exercised by the existing CLI tests that already mock the config layer.)

- [ ] **Step 4.5: Run the CLI tests**

```bash
uv run pytest tests/executor/test_cli_reconcile.py -v
```

Expected: all green.

- [ ] **Step 4.6: Run ruff + mypy**

```bash
uv run ruff check ralph_executor/cli.py tests/executor/test_cli_reconcile.py
uv run mypy --strict ralph_executor/cli.py
```

- [ ] **Step 4.7: Commit**

```powershell
git add ralph_executor/cli.py tests/executor/test_cli_reconcile.py
git commit -m "feat(cli): reconcile subcommand prints stale current/ entries"
```

---

## Task 5: Full-suite green + grep for legacy expectations

**Files:** none modified by default; conditional fixes if Step 5.1 surfaces broken tests.

- [ ] **Step 5.1: Grep for tests that may break under new current/ pass behaviour**

```bash
grep -rn "current/" tests/ | grep -v ".gitkeep" | head
```

Look for tests that:
- Build a `current/X/` directory without a sibling but expect it to survive (the no-sibling case warns but leaves the dir — should be fine).
- Build a `current/X/` directory shadowing a `done/X/` sibling but expect the dir to survive (this is exactly what the new pass deletes — these tests need updating).
- Assert exact sweep `result.errors` counts that the new pass could grow.

For each match, decide:
- Has `PBI.md` → still safe (kept as active claim).
- No `PBI.md`, no sibling → still safe (kept as no-sibling).
- No `PBI.md`, has sibling → will be deleted by new pass. Either add `PBI.md` to the fixture (if it represents a real claim) or update the assertion (if the test was implicitly tolerating the leak).

- [ ] **Step 5.2: Run the full test suite**

```bash
uv run pytest -x --maxfail=5
```

Expected: all green. If a test fails because the new pass deleted a fixture dir it didn't expect to lose, fix per Step 5.1.

- [ ] **Step 5.3: Final ruff + mypy + format**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy ralph_executor scripts skills tests
```

Expected: all green.

- [ ] **Step 5.4: Smoke-check against the live ralph-queue**

This is observation only (no mutation). After this PBI's PR merges and Ralph runs one sweep iteration:

```bash
git fetch origin ralph-queue
git ls-tree -r origin/ralph-queue | grep "\.ralph/current/" | awk '{print $NF}'
```

Expected output: `.ralph/current/.gitkeep` plus `.ralph/current/STAGE-B-PLAN-10/HISTORY.md`, `PBI.md`, `PLAN.md` (the genuine in-flight claim) — and only those. The three orphans (`CONFIG-PROMOTE-SWEEP-KNOBS`, `STAGE-B-PLAN-08`, `SWEEP-RECONCILE-ORPHANS`) gone.

- [ ] **Step 5.5: Final commit (if Step 5.1 surfaced fixture updates)**

```powershell
git add <touched test files>
git commit -m "test: update fixtures to coexist with current/ reconciliation"
```

If no fixture changes were needed, skip this step.

- [ ] **Step 5.6: Open the PR**

Per the project's PR-creation pattern (`gh pr create` from `ralph/SWEEP-RECONCILE-CURRENT` against `main`). After PR opens, begin the bot-watch loop without asking the user (per CLAUDE.md memory): poll `gh pr view` + `gh api graphql` for unresolved reviewThreads; address findings; resolve threads; merge when clean.

---

## Acceptance Criteria (mirrors spec)

- `ralph_executor/sweep/types.py` exports `CurrentReconcileAction` (StrEnum) + `CurrentReconcileReport` (frozen dataclass).
- `ralph_executor/sweep/reconcile.py` exposes `reconcile_stale_current_one` + `reconcile_stale_current_all`.
- `sweep/runner.py::run()` calls `reconcile_stale_current_all` after the existing pending-pr loop; per-PBI errors merge into `SweepResult.errors`.
- `ralph-executor reconcile [--dry-run]` prints two sections: one for pending-pr/ (existing), one for current/ (new).
- After one sweep iteration following PBI merge: `origin/ralph-queue`'s `.ralph/current/` contains only `STAGE-B-PLAN-10/` and `.gitkeep`.
- `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy ralph_executor scripts skills tests` all green.
- No host-API calls added (pure filesystem pass).
- Active claims (`current/<id>/` with `PBI.md`) are never deleted, even if `done/<id>/` happens to exist.

---

## depends_on

None. Files touched (`sweep/types.py`, `sweep/reconcile.py`, `sweep/runner.py`, `cli.py`, plus new test files) do not overlap with the queued PBIs (`STAGE-B-PLAN-10` is in-flight on its own feature branch; `CONFIG-PROMOTE-BASH-TIMEOUT` touches `config.py` / `claude_spawn.py` / `conftest.py`). Ralph processes one PBI at a time anyway.
