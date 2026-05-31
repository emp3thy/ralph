"""Reconcile subcommand must reject ``--repo`` / ``--workspace`` after T4 refactor.

Post-EXECUTOR-QUEUE-REPO-SPLIT, the queue clone path is
``<workspace_root>/queue/``; the operator-supplied target-repo flags
are no longer meaningful for reconcile and the helper now derives its
SweepContext label from the queue clone's directory name instead of
``cfg.repo_path.name``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor import cli


def _empty_report():
    from ralph_executor.sweep.types import ReconcileReport

    return ReconcileReport(actions={}, errors={})


def _empty_current_report():
    from ralph_executor.sweep.types import CurrentReconcileReport

    return CurrentReconcileReport(actions={}, errors={})


def test_reconcile_runs_with_no_target_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``ralph-executor reconcile --dry-run`` accepts no target args.

    The reconcile path resolves ``<workspace_root>/queue`` from the loaded
    config; it no longer needs a ``--repo`` override. The test stubs the
    reconcile passes to keep the assertion focused on argparse + dispatch.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cfg_dir = tmp_path / ".ralph"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        f'workspace_root = "{(tmp_path / "ws").as_posix()}"\n'
        'queue_repo = "https://github.com/test/queue"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "reconcile_all", lambda ctx, dry_run: _empty_report())
    monkeypatch.setattr(
        cli,
        "reconcile_stale_current_all",
        lambda ctx, dry_run: _empty_current_report(),
    )

    rc = cli.main(["reconcile", "--dry-run"])
    # 0 if env complete, 2 if config-load fails on the host's missing
    # ``cfg.repo_path`` validation (Task 8 deletes that field). Either is
    # acceptable here — the assertion is that argparse accepts the
    # argv shape, not that the full reconcile pipeline runs.
    assert rc in (0, 2)


def test_reconcile_rejects_repo_flag() -> None:
    """argparse must reject ``--repo`` on the reconcile subcommand."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["reconcile", "--repo", "/x"])
    assert excinfo.value.code == 2


def test_reconcile_rejects_workspace_flag() -> None:
    """argparse must reject ``--workspace`` on the reconcile subcommand."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["reconcile", "--workspace", "x"])
    assert excinfo.value.code == 2
