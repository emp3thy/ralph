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

    The reconcile CLI reads ``.ralph/`` from
    ``<workspace_root>/queue-<instance_id>/`` (Scope 1 multi-ralph). The
    fixture sets ``RALPH_INSTANCE_ID=test-ralph`` and seeds the clone
    at ``<tmp_path>/ws/queue-test-ralph/`` so ``cfg.queue_clone_path``
    resolves there.
    """
    monkeypatch.setenv("RALPH_INSTANCE_ID", "test-ralph")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    repo = workspace / "queue-test-ralph"
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
    # Reconcile resolves the scripts dir from the ralph executor source tree
    # (skills/pr-github/scripts) — the CLI rejects with exit 2 if that dir is
    # missing under the queue clone, so create it (empty — reconcile_all is
    # mocked in these tests so the script itself isn't invoked).
    scripts_dir = repo / "skills" / "pr-github" / "scripts"
    scripts_dir.mkdir(parents=True)
    _make_orphan(queue / "pending-pr", "ORPHAN-1")
    monkeypatch.setenv("RALPH_WORKSPACE", str(workspace))
    # KILL-RALPH-HOME made queue_repo a user-config-only field. Redirect
    # ``Path.home()`` to a tmp dir and write a minimal ``~/.ralph/config.toml``
    # so ``load_config`` finds a queue_repo on hosts (CI) without one.
    fake_home = tmp_path / "home"
    (fake_home / ".ralph").mkdir(parents=True)
    (fake_home / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/example/queue"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    return repo


@pytest.mark.usefixtures("fake_repo_with_orphan")
def test_reconcile_subcommand_calls_reconcile_all(
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
    # Reconcile derives the queue clone path from RALPH_WORKSPACE, which
    # is set by the fake_repo_with_orphan fixture.
    exit_code = cli_main(["reconcile"])

    assert exit_code == 0
    assert captured_calls == [False]
    out = capsys.readouterr().out
    assert "ORPHAN-1" in out
    assert "moved_to_inbox" in out


@pytest.mark.usefixtures("fake_repo_with_orphan")
def test_reconcile_dry_run_flag_flows_through(
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
    exit_code = cli_main(["reconcile", "--dry-run"])

    assert exit_code == 0
    assert captured_calls == [True]


@pytest.mark.usefixtures("fake_repo_with_orphan")
def test_reconcile_subcommand_reports_no_orphans_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ralph_executor.sweep.types import ReconcileReport

    def _fake_reconcile_all(ctx: object, *, dry_run: bool = False) -> ReconcileReport:
        return ReconcileReport(actions={}, errors={})

    monkeypatch.setattr("ralph_executor.cli.reconcile_all", _fake_reconcile_all)
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph@example.com")
    monkeypatch.setenv("GH_TOKEN", "fake")
    exit_code = cli_main(["reconcile"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "no orphans" in out


@pytest.mark.usefixtures("fake_repo_with_orphan")
def test_reconcile_summary_counts_include_errors(
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
    exit_code = cli_main(["reconcile"])

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
    exit_code = cli_main(["reconcile"])

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
    exit_code = cli_main(["reconcile", "--dry-run"])

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
    # _pr_skill_scripts_path resolves against the ralph executor source
    # tree. Stub it to an absent path so the CLI guard fails fast.
    bogus = tmp_path / "no" / "such" / "scripts"
    monkeypatch.setattr("ralph_executor.cli._pr_skill_scripts_path", lambda _cfg: bogus)
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph@example.com")
    monkeypatch.setenv("GH_TOKEN", "fake")
    # KILL-RALPH-HOME made queue_repo a user-config-only field, so without a
    # user TOML load_config raises before the scripts-dir guard fires. Point
    # Path.home() at a tmp dir holding a minimal user config so the CLI gets
    # past load_config and reaches the scripts-dir check we want to exercise.
    fake_home = tmp_path / "home"
    (fake_home / ".ralph").mkdir(parents=True)
    (fake_home / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/example/queue"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("RALPH_INSTANCE_ID", "test-ralph")
    monkeypatch.setenv("RALPH_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws" / "queue-test-ralph").mkdir(parents=True)
    exit_code = cli_main(["reconcile"])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "scripts" in err.lower()
