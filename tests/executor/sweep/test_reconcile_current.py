"""Tests for the current/ reconciliation pass in
ralph_executor.sweep.reconcile.

Companion to ``test_reconcile.py`` which covers the pending-pr/ pass.
"""

from __future__ import annotations

import shutil
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


def test_dry_run_does_not_delete(fake_queue_root: Path, fake_ctx: SweepContext) -> None:
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


def test_all_processes_every_current_dir(fake_queue_root: Path, fake_ctx: SweepContext) -> None:
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


def test_all_skips_dotfiles_and_non_dirs(fake_queue_root: Path, fake_ctx: SweepContext) -> None:
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
    real_rmtree = shutil.rmtree

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
