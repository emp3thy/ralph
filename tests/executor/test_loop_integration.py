"""Integration tests for the loop ↔ sweep wiring (Plan 8 Task 7).

The unit tests in ``tests/executor/sweep/`` exercise ``run_sweep`` in
isolation. These tests confirm that ``iterate_once`` actually invokes
the sweep when ``current/`` is empty and skips it when occupied — and
that the configuration plumbing (``RALPH_ADO_AUTHOR_EMAIL``, the
PR-skill scripts directory) is honoured.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.claude_spawn import ClaudeOutcome
from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import iterate_once
from ralph_executor.sweep.runner import SweepContext, SweepResult
from tests.executor.conftest import write_sample_pbi


def _stub_spawn(outcome_kind: str = "partial") -> object:
    def _fake_spawn(cfg: ExecutorConfig, pbi: object) -> ClaudeOutcome:
        return ClaudeOutcome(
            kind=outcome_kind,  # type: ignore[arg-type]
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )

    return _fake_spawn


def _set_sweep_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "ralph-bot@example.com")
    monkeypatch.setenv("RALPH_STALE_DAYS", "3")


def _populate_inbox_via_git(fake_repo: Path, pbi_id: str = "WI-INTEG") -> None:
    """Stage a sample PBI on the ralph-queue branch the way the loop expects."""
    import subprocess

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(fake_repo),
            check=True,
            capture_output=True,
            text=True,
        )

    _git("checkout", "ralph-queue")
    write_sample_pbi(fake_repo, pbi_id=pbi_id)
    _git("add", f".ralph/inbox/{pbi_id}")
    _git("commit", "-m", f"inbox: {pbi_id}")
    _git("push", "origin", "ralph-queue")
    _git("checkout", "main")


def test_sweep_runs_when_current_is_empty(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``iterate_once`` should invoke the real sweep when current/ is empty."""
    _set_sweep_env(monkeypatch)
    # Stage a PR-skill scripts directory so _run_sweep's guard accepts the path.
    (fake_repo / "skills" / "pr-github" / "scripts").mkdir(parents=True)

    captured: list[SweepContext] = []

    def _spy_run(*, ctx: SweepContext) -> SweepResult:
        captured.append(ctx)
        return SweepResult(pbis_scanned=0)

    # The loop driver imports `run` lazily from `ralph_executor.sweep` inside
    # `_run_sweep`. Patch the resolved name on the runner module so the lazy
    # `from ralph_executor.sweep import run` picks up the spy.
    monkeypatch.setattr("ralph_executor.sweep.run", _spy_run)
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn())

    iterate_once(cfg_for_repo)

    assert len(captured) == 1, "sweep must run exactly once when current/ is empty"
    ctx = captured[0]
    assert ctx.queue_root == fake_repo / ".ralph"
    assert ctx.ado_pr_scripts_path == fake_repo / "skills" / "pr-github" / "scripts"
    assert ctx.config.ralph_author_email == "ralph-bot@example.com"


def test_sweep_does_not_run_when_current_has_pbi(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``current/`` is occupied the loop spawns Claude, not the sweep."""
    _set_sweep_env(monkeypatch)
    (fake_repo / "skills" / "pr-github" / "scripts").mkdir(parents=True)
    _populate_inbox_via_git(fake_repo)
    # First iteration claims the PBI into current/.
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn())
    iterate_once(cfg_for_repo)
    assert (fake_repo / ".ralph" / "current" / "WI-INTEG").is_dir()

    captured: list[SweepContext] = []

    def _spy_run(*, ctx: SweepContext) -> SweepResult:
        captured.append(ctx)
        return SweepResult(pbis_scanned=0)

    monkeypatch.setattr("ralph_executor.sweep.run", _spy_run)
    iterate_once(cfg_for_repo)
    assert captured == [], "sweep must NOT run while current/ is occupied"


def test_sweep_skipped_when_author_email_missing(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``RALPH_ADO_AUTHOR_EMAIL`` is unset the sweep is skipped with a warning."""
    monkeypatch.delenv("RALPH_ADO_AUTHOR_EMAIL", raising=False)
    (fake_repo / "skills" / "pr-github" / "scripts").mkdir(parents=True)

    captured: list[SweepContext] = []

    def _spy_run(*, ctx: SweepContext) -> SweepResult:
        captured.append(ctx)
        return SweepResult(pbis_scanned=0)

    monkeypatch.setattr("ralph_executor.sweep.run", _spy_run)
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn())

    iterate_once(cfg_for_repo)
    assert captured == [], "sweep must skip when RALPH_ADO_AUTHOR_EMAIL is unset"


def test_sweep_skipped_when_pr_skill_scripts_dir_missing(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the PR-skill scripts directory on disk, the sweep is skipped."""
    _set_sweep_env(monkeypatch)
    # Deliberately do NOT create skills/pr-github/scripts/ or skills/ado-pr/scripts/.

    captured: list[SweepContext] = []

    def _spy_run(*, ctx: SweepContext) -> SweepResult:
        captured.append(ctx)
        return SweepResult(pbis_scanned=0)

    monkeypatch.setattr("ralph_executor.sweep.run", _spy_run)
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn())

    iterate_once(cfg_for_repo)
    assert captured == [], "sweep must skip when the PR-skill scripts directory is missing"
