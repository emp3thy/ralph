"""Tests for the ``ralph-executor reconcile`` subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.cli import main as cli_main


def _make_orphan(pending_dir: Path, pbi_id: str) -> Path:
    d = pending_dir / pbi_id
    d.mkdir()
    (d / "PBI.md").write_text(f"---\nid: {pbi_id}\n---\n", encoding="utf-8")
    return d


@pytest.fixture
def fake_repo_with_orphan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Layout the queue-clone topology the post-split CLI expects.

    The reconcile CLI reads ``.ralph/`` from ``<workspace_root>/queue/``
    (not ``cfg.repo_path``), so ``RALPH_WORKSPACE`` is monkeypatched to
    ``<tmp_path>/ws`` and the orphan is placed at
    ``<tmp_path>/ws/queue/.ralph/pending-pr/ORPHAN-1``.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    repo = workspace / "queue"
    repo.mkdir()
    (repo / ".git").mkdir()
    queue = repo / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue / sub).mkdir(parents=True)
    (queue / "config.toml").write_text(
        'git_host = "github"\ngh_owner = "emp3thy"\n'
        'queue_repo = "https://github.com/example/queue"\n',
        encoding="utf-8",
    )
    # Reconcile resolves the scripts dir from cfg.repo_path / skills/pr-github/scripts.
    # The CLI rejects with exit 2 if that dir is missing, so create it (empty —
    # reconcile_all is mocked in these tests so the script itself isn't invoked).
    scripts_dir = repo / "skills" / "pr-github" / "scripts"
    scripts_dir.mkdir(parents=True)
    _make_orphan(queue / "pending-pr", "ORPHAN-1")
    monkeypatch.setenv("RALPH_WORKSPACE", str(workspace))
    return repo


def test_reconcile_subcommand_calls_reconcile_all(
    fake_repo_with_orphan: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ralph_executor.sweep.types import ReconcileAction, ReconcileReport

    captured_calls: list[bool] = []

    def _fake_reconcile_all(ctx: object, *, dry_run: bool = False) -> ReconcileReport:
        captured_calls.append(dry_run)
        return ReconcileReport(
            actions={"ORPHAN-1": ReconcileAction.MOVED_TO_INBOX},
            errors={},
        )

    monkeypatch.setattr("ralph_executor.cli.reconcile_all", _fake_reconcile_all)
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph@example.com")
    monkeypatch.setenv("GH_TOKEN", "fake")
    monkeypatch.setenv("RALPH_REPO_PATH", str(fake_repo_with_orphan))
    exit_code = cli_main(["reconcile", "--repo", str(fake_repo_with_orphan)])

    assert exit_code == 0
    assert captured_calls == [False]
    out = capsys.readouterr().out
    assert "ORPHAN-1" in out
    assert "moved_to_inbox" in out


def test_reconcile_dry_run_flag_flows_through(
    fake_repo_with_orphan: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph_executor.sweep.types import ReconcileReport

    captured_calls: list[bool] = []

    def _fake_reconcile_all(ctx: object, *, dry_run: bool = False) -> ReconcileReport:
        captured_calls.append(dry_run)
        return ReconcileReport(actions={}, errors={})

    monkeypatch.setattr("ralph_executor.cli.reconcile_all", _fake_reconcile_all)
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph@example.com")
    monkeypatch.setenv("GH_TOKEN", "fake")
    monkeypatch.setenv("RALPH_REPO_PATH", str(fake_repo_with_orphan))
    exit_code = cli_main(["reconcile", "--repo", str(fake_repo_with_orphan), "--dry-run"])

    assert exit_code == 0
    assert captured_calls == [True]


def test_reconcile_subcommand_reports_no_orphans_cleanly(
    fake_repo_with_orphan: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ralph_executor.sweep.types import ReconcileReport

    def _fake_reconcile_all(ctx: object, *, dry_run: bool = False) -> ReconcileReport:
        return ReconcileReport(actions={}, errors={})

    monkeypatch.setattr("ralph_executor.cli.reconcile_all", _fake_reconcile_all)
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph@example.com")
    monkeypatch.setenv("GH_TOKEN", "fake")
    monkeypatch.setenv("RALPH_REPO_PATH", str(fake_repo_with_orphan))
    exit_code = cli_main(["reconcile", "--repo", str(fake_repo_with_orphan)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "no orphans" in out


def test_reconcile_summary_counts_include_errors(
    fake_repo_with_orphan: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Regression: BugBot PR #31 flagged that the summary line used
    # len(report.actions) as the headline count, so "3 successes + 2 errors"
    # printed "3 orphans processed … 2 errors" (counts didn't add up).
    # Fix: total = len(actions) + len(errors), label "attempted".
    from ralph_executor.sweep.types import ReconcileAction, ReconcileReport

    def _fake_reconcile_all(ctx: object, *, dry_run: bool = False) -> ReconcileReport:
        return ReconcileReport(
            actions={
                "ORPHAN-A": ReconcileAction.MOVED_TO_DONE,
                "ORPHAN-B": ReconcileAction.MOVED_TO_INBOX,
                "ORPHAN-C": ReconcileAction.KEEP_PENDING,
            },
            errors={
                "ORPHAN-D": "lookup failed",
                "ORPHAN-E": "subprocess died",
            },
        )

    monkeypatch.setattr("ralph_executor.cli.reconcile_all", _fake_reconcile_all)
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph@example.com")
    monkeypatch.setenv("GH_TOKEN", "fake")
    monkeypatch.setenv("RALPH_REPO_PATH", str(fake_repo_with_orphan))
    exit_code = cli_main(["reconcile", "--repo", str(fake_repo_with_orphan)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "5 orphans attempted" in out
    assert "2 moved" in out
    assert "1 stays" in out
    assert "2 errors" in out


def test_reconcile_subcommand_prints_current_section_when_stale_orphan_present(
    fake_repo_with_orphan: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`ralph-executor reconcile` prints the current/ section after the
    pending-pr section, deleting an orphan whose sibling lives in done/."""
    from ralph_executor.sweep.types import ReconcileReport

    queue = fake_repo_with_orphan / ".ralph"
    orphan = queue / "current" / "MERGED-X"
    orphan.mkdir()
    (orphan / "HISTORY.md").write_text("iter\n", encoding="utf-8")
    sibling = queue / "done" / "MERGED-X"
    sibling.mkdir()
    (sibling / "PBI.md").write_text("---\nid: MERGED-X\n---\n", encoding="utf-8")

    monkeypatch.setattr(
        "ralph_executor.cli.reconcile_all",
        lambda ctx, *, dry_run=False: ReconcileReport(actions={}, errors={}),
    )
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph@example.com")
    monkeypatch.setenv("GH_TOKEN", "fake")
    monkeypatch.setenv("RALPH_REPO_PATH", str(fake_repo_with_orphan))
    exit_code = cli_main(["reconcile", "--repo", str(fake_repo_with_orphan)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "MERGED-X" in out
    assert "deleted_done_sibling" in out
    assert "1 current/ entries inspected" in out
    assert not orphan.exists()


def test_reconcile_subcommand_current_dry_run_does_not_delete(
    fake_repo_with_orphan: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--dry-run flag flows through to the current/ pass too."""
    from ralph_executor.sweep.types import ReconcileReport

    queue = fake_repo_with_orphan / ".ralph"
    orphan = queue / "current" / "MERGED-Y"
    orphan.mkdir()
    (orphan / "HISTORY.md").write_text("iter\n", encoding="utf-8")
    sibling = queue / "done" / "MERGED-Y"
    sibling.mkdir()
    (sibling / "PBI.md").write_text("---\nid: MERGED-Y\n---\n", encoding="utf-8")

    monkeypatch.setattr(
        "ralph_executor.cli.reconcile_all",
        lambda ctx, *, dry_run=False: ReconcileReport(actions={}, errors={}),
    )
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph@example.com")
    monkeypatch.setenv("GH_TOKEN", "fake")
    monkeypatch.setenv("RALPH_REPO_PATH", str(fake_repo_with_orphan))
    exit_code = cli_main(["reconcile", "--repo", str(fake_repo_with_orphan), "--dry-run"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "would: deleted_done_sibling" in out
    assert orphan.exists()


def test_reconcile_subcommand_missing_scripts_dir_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    queue = repo / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (queue / sub).mkdir(parents=True)
    (queue / "config.toml").write_text(
        'git_host = "github"\ngh_owner = "emp3thy"\n'
        'queue_repo = "https://github.com/example/queue"\n',
        encoding="utf-8",
    )
    # No skills/pr-github/scripts/ — CLI must fail fast.
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph@example.com")
    monkeypatch.setenv("GH_TOKEN", "fake")
    monkeypatch.setenv("RALPH_REPO_PATH", str(repo))
    exit_code = cli_main(["reconcile", "--repo", str(repo)])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "scripts" in err.lower()
