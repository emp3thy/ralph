"""Tests for ralph_executor.sweep.reconcile."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.sweep.reconcile import (
    ReconcileError,
    reconcile_all,
    reconcile_orphan,
)
from ralph_executor.sweep.runner import SweepConfig, SweepContext
from ralph_executor.sweep.types import ReconcileAction

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_queue_root(tmp_path: Path) -> Path:
    """A .ralph/ skeleton with empty inbox/current/pending-pr/done/blocked."""
    queue = tmp_path / "ralph" / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue / sub).mkdir(parents=True)
    return queue


@pytest.fixture
def fake_orphan(fake_queue_root: Path) -> Path:
    """A pending-pr/<PBI-ID>/ dir WITHOUT PR-LINK.md."""
    orphan = fake_queue_root / "pending-pr" / "STAGE-B-PLAN-03"
    orphan.mkdir()
    (orphan / "PBI.md").write_text("---\nid: STAGE-B-PLAN-03\n---\n", encoding="utf-8")
    (orphan / "HISTORY.md").write_text("", encoding="utf-8")
    return orphan


@pytest.fixture
def fake_ctx(fake_queue_root: Path, tmp_path: Path) -> SweepContext:
    scripts_path = tmp_path / "pr_scripts"
    scripts_path.mkdir()
    # The actual lookup_by_branch.py is mocked via subprocess monkeypatch,
    # so the file under scripts_path doesn't need to exist.
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


def _stub_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    returncode: int,
    stderr: str = "",
) -> list[list[str]]:
    """Replace subprocess.run inside reconcile with a captured stub.

    Returns a list that will be populated with each argv the production
    code calls subprocess.run with — assertable in the test.
    """
    invocations: list[list[str]] = []

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        invocations.append(list(argv))
        return subprocess.CompletedProcess(
            args=argv,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    monkeypatch.setattr("ralph_executor.sweep.reconcile.subprocess.run", _fake_run)
    return invocations


# ---------------------------------------------------------------------------
# reconcile_orphan
# ---------------------------------------------------------------------------


def test_reconcile_orphan_merged_moves_to_done(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps(
            {
                "pr": {
                    "state": "merged",
                    "url": "https://github.com/emp3thy/ralph/pull/25",
                    "pr_id": 25,
                    "merged_at": "2026-05-26T18:00:00Z",
                },
                "branch_exists": None,
            }
        ),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.MOVED_TO_DONE
    moved = fake_queue_root / "done" / "STAGE-B-PLAN-03"
    assert moved.is_dir()
    assert not fake_orphan.exists()
    pr_link = moved / "PR-LINK.md"
    assert pr_link.is_file()
    assert "https://github.com/emp3thy/ralph/pull/25" in pr_link.read_text(encoding="utf-8")


def test_reconcile_orphan_open_stays_in_pending(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps(
            {
                "pr": {
                    "state": "open",
                    "url": "https://github.com/emp3thy/ralph/pull/42",
                    "pr_id": 42,
                    "merged_at": None,
                },
                "branch_exists": None,
            }
        ),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.KEEP_PENDING
    assert fake_orphan.is_dir(), "open-PR orphan must stay in pending-pr/"
    pr_link = fake_orphan / "PR-LINK.md"
    assert pr_link.is_file(), "open-PR orphan must get PR-LINK.md so next sweep uses normal path"
    assert "pull/42" in pr_link.read_text(encoding="utf-8")


def test_reconcile_orphan_closed_unmerged_moves_to_blocked(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps(
            {
                "pr": {
                    "state": "closed",
                    "url": "https://github.com/emp3thy/ralph/pull/7",
                    "pr_id": 7,
                    "merged_at": None,
                },
                "branch_exists": None,
            }
        ),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.MOVED_TO_BLOCKED
    moved = fake_queue_root / "blocked" / "STAGE-B-PLAN-03"
    assert moved.is_dir()
    assert (moved / "PR-LINK.md").is_file()


def test_reconcile_orphan_no_pr_branch_exists_goes_to_blocked(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({"pr": None, "branch_exists": True}),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.MOVED_TO_BLOCKED
    moved = fake_queue_root / "blocked" / "STAGE-B-PLAN-03"
    assert moved.is_dir()
    assert not (moved / "PR-LINK.md").is_file(), "no PR -> no URL to write"


def test_reconcile_orphan_no_pr_branch_missing_goes_to_inbox(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({"pr": None, "branch_exists": False}),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.MOVED_TO_INBOX
    moved = fake_queue_root / "inbox" / "STAGE-B-PLAN-03"
    assert moved.is_dir()
    assert not (moved / "PR-LINK.md").is_file()


def test_reconcile_orphan_api_error_returns_keep_api_error(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout="",
        returncode=3,
        stderr="github error: HTTP 500",
    )

    action = reconcile_orphan(fake_orphan, fake_ctx)

    assert action == ReconcileAction.KEEP_API_ERROR
    assert fake_orphan.is_dir(), "API error must leave orphan in place"


def test_reconcile_orphan_validation_error_raises(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout="",
        returncode=2,
        stderr="error: GH_TOKEN required",
    )

    with pytest.raises(ReconcileError):
        reconcile_orphan(fake_orphan, fake_ctx)


def test_reconcile_orphan_malformed_json_raises(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(monkeypatch, stdout="not valid json {{{", returncode=0)
    with pytest.raises(ReconcileError):
        reconcile_orphan(fake_orphan, fake_ctx)


def test_reconcile_orphan_dry_run_does_not_move(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    fake_queue_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps(
            {
                "pr": {
                    "state": "merged",
                    "url": "https://github.com/emp3thy/ralph/pull/25",
                    "pr_id": 25,
                    "merged_at": "2026-05-26T18:00:00Z",
                },
                "branch_exists": None,
            }
        ),
        returncode=0,
    )

    action = reconcile_orphan(fake_orphan, fake_ctx, dry_run=True)

    assert action == ReconcileAction.MOVED_TO_DONE  # action is what WOULD happen
    assert fake_orphan.is_dir(), "dry-run must not move the dir"
    assert not (fake_queue_root / "done" / "STAGE-B-PLAN-03").exists()


def test_reconcile_orphan_invokes_lookup_with_correct_args(
    fake_orphan: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocations = _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({"pr": None, "branch_exists": False}),
        returncode=0,
    )

    reconcile_orphan(fake_orphan, fake_ctx)

    assert len(invocations) == 1
    argv = invocations[0]
    assert "--branch" in argv
    branch_value = argv[argv.index("--branch") + 1]
    assert branch_value == "ralph/STAGE-B-PLAN-03"
    assert "--include-branch-check" in argv


def test_reconcile_uses_explicit_repo_name_not_queue_root_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In worktree mode ``ctx.queue_root.parent.name`` is the worktree
    directory name (``"queue"``), not the repo name. The reconcile path
    must read ``ctx.repo_name`` explicitly so ``--repo`` lands as e.g.
    ``ralph`` and not ``queue`` (which produces 404s against
    ``/repos/<owner>/queue/pulls``).

    Build a synthetic ``queue_root`` whose parent is named ``"queue"``
    (simulating ``<repo>/.ralph-work/queue/.ralph``). The new code path
    must return ``ctx.repo_name="ralph"`` instead of the parent name
    ``"queue"`` — and the old fallback would visibly fail this
    assertion, which is the regression we want covered."""
    queue_root = tmp_path / "queue" / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue_root / sub).mkdir(parents=True)
    orphan = queue_root / "pending-pr" / "STAGE-B-PLAN-03"
    orphan.mkdir()
    (orphan / "PBI.md").write_text("---\nid: STAGE-B-PLAN-03\n---\n", encoding="utf-8")
    (orphan / "HISTORY.md").write_text("", encoding="utf-8")
    scripts_path = tmp_path / "pr_scripts"
    scripts_path.mkdir()

    ctx = SweepContext(
        queue_root=queue_root,
        ado_pr_scripts_path=scripts_path,
        config=SweepConfig(
            ralph_author_email="ralph@example.com",
            max_attempts=20,
            stale_threshold=timedelta(days=3),
            now=datetime.now(tz=UTC),
        ),
        repo_name="ralph",
    )
    # Sanity: queue_root.parent.name is "queue", not "ralph" — so the
    # old fallback would return the wrong value, making this assertion
    # protective against a regression that removed the explicit branch.
    assert ctx.queue_root.parent.name == "queue"

    invocations = _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({"pr": None, "branch_exists": False}),
        returncode=0,
    )

    reconcile_orphan(orphan, ctx)

    argv = invocations[0]
    assert "--repo" in argv
    repo_value = argv[argv.index("--repo") + 1]
    assert repo_value == "ralph", (
        f"--repo must use ctx.repo_name='ralph', not queue_root.parent.name "
        f"('queue'); got {repo_value!r}"
    )


# ---------------------------------------------------------------------------
# reconcile_all
# ---------------------------------------------------------------------------


def _make_orphan(pending_dir: Path, pbi_id: str) -> Path:
    d = pending_dir / pbi_id
    d.mkdir()
    (d / "PBI.md").write_text(f"---\nid: {pbi_id}\n---\n", encoding="utf-8")
    (d / "HISTORY.md").write_text("", encoding="utf-8")
    return d


def test_reconcile_all_processes_every_orphan(
    fake_queue_root: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = fake_queue_root / "pending-pr"
    _make_orphan(pending, "A")
    _make_orphan(pending, "B")
    _make_orphan(pending, "C")

    # Cycle through three different states.
    state_payloads: Iterator[str] = iter(
        [
            json.dumps(
                {
                    "pr": {"state": "merged", "url": "u1", "pr_id": 1, "merged_at": "t"},
                    "branch_exists": None,
                }
            ),
            json.dumps(
                {
                    "pr": {"state": "open", "url": "u2", "pr_id": 2, "merged_at": None},
                    "branch_exists": None,
                }
            ),
            json.dumps({"pr": None, "branch_exists": False}),
        ]
    )

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=next(state_payloads),
            stderr="",
        )

    monkeypatch.setattr("ralph_executor.sweep.reconcile.subprocess.run", _fake_run)

    report = reconcile_all(fake_ctx)

    assert set(report.actions.keys()) == {"A", "B", "C"}
    assert ReconcileAction.MOVED_TO_DONE in report.actions.values()
    assert ReconcileAction.KEEP_PENDING in report.actions.values()
    assert ReconcileAction.MOVED_TO_INBOX in report.actions.values()


def test_reconcile_all_isolates_per_pbi_failures(
    fake_queue_root: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = fake_queue_root / "pending-pr"
    _make_orphan(pending, "A")
    _make_orphan(pending, "B")

    state_payloads: Iterator[tuple[str, int]] = iter(
        [
            ("", 2),  # A fails validation
            (json.dumps({"pr": None, "branch_exists": False}), 0),  # B succeeds
        ]
    )

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        out, code = next(state_payloads)
        return subprocess.CompletedProcess(args=argv, returncode=code, stdout=out, stderr="")

    monkeypatch.setattr("ralph_executor.sweep.reconcile.subprocess.run", _fake_run)

    report = reconcile_all(fake_ctx)

    assert "A" in report.errors
    assert report.actions.get("B") == ReconcileAction.MOVED_TO_INBOX


def test_reconcile_all_skips_dirs_with_pr_link(
    fake_queue_root: Path,
    fake_ctx: SweepContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = fake_queue_root / "pending-pr"
    healthy = _make_orphan(pending, "HEALTHY")
    (healthy / "PR-LINK.md").write_text("PR ID 99\n", encoding="utf-8")
    _make_orphan(pending, "ORPHAN")

    _stub_subprocess(
        monkeypatch,
        stdout=json.dumps({"pr": None, "branch_exists": False}),
        returncode=0,
    )

    report = reconcile_all(fake_ctx)

    assert "ORPHAN" in report.actions
    assert "HEALTHY" not in report.actions
