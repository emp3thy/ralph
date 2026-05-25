"""Tests for ``ralph_executor.loop``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import ClaudeOutcome
from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import (
    iterate_once,
    run_loop,
)
from ralph_executor.queue.filesystem import FilesystemQueueSource
from tests.executor.conftest import write_sample_pbi


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    ).stdout


def _populate_inbox(fake_repo: Path, pbi_id: str = "WI-1234", severity: str = "normal") -> None:
    _git(fake_repo, "checkout", "ralph-queue")
    write_sample_pbi(fake_repo, pbi_id=pbi_id, severity=severity)
    _git(fake_repo, "add", f".ralph/inbox/{pbi_id}")
    _git(fake_repo, "commit", "-m", f"inbox: {pbi_id}")
    _git(fake_repo, "push", "origin", "ralph-queue")
    _git(fake_repo, "checkout", "main")


def _stub_spawn(outcome_kind: str, pr_url: str | None = None) -> object:
    def _fake_spawn(cfg: ExecutorConfig, pbi: object) -> ClaudeOutcome:
        return ClaudeOutcome(
            kind=outcome_kind,  # type: ignore[arg-type]
            pr_url=pr_url,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )

    return _fake_spawn


def test_iterate_once_idle_when_no_work(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "idle"
    assert result.pbi_id is None


def test_iterate_once_claims_inbox_pbi_when_current_empty(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "claimed"
    assert result.pbi_id == "WI-1234"
    # After claim, the PBI should be on disk under current/, not inbox/.
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()
    assert not (fake_repo / ".ralph" / "inbox" / "WI-1234").exists()
    # The feature branch ralph/WI-1234 must exist after a fresh claim.
    branches = _git(fake_repo, "branch", "--list", "ralph/WI-1234").strip()
    assert branches != "", "ralph/WI-1234 branch should be created on claim"


def test_iterate_once_runs_ralph_when_current_occupied(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    # Two iterations: first claims, second spawns Ralph (returns partial).
    iterate_once(cfg_for_repo)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "ran_partial"
    assert result.pbi_id == "WI-1234"
    # PBI stays in current/.
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()


def test_iterate_once_moves_to_pending_pr_when_pr_created(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("pr_created", pr_url="https://example/pr/1"),
    )
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "ran_pr_created"
    assert result.pr_url == "https://example/pr/1"
    assert (fake_repo / ".ralph" / "pending-pr" / "WI-1234").is_dir()
    assert not (fake_repo / ".ralph" / "current" / "WI-1234").exists()


def test_iterate_once_moves_to_blocked_when_stuck(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)
    # Simulate Ralph writing STUCK.md before exit.
    pbi_dir = fake_repo / ".ralph" / "current" / "WI-1234"

    def _stuck_spawn(cfg: ExecutorConfig, pbi: object) -> ClaudeOutcome:
        (pbi_dir / "STUCK.md").write_text("# stuck\n", encoding="utf-8")
        return ClaudeOutcome(
            kind="stuck",
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.0,
        )

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stuck_spawn)
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "ran_stuck"
    assert (fake_repo / ".ralph" / "blocked" / "WI-1234").is_dir()


def test_iterate_once_treats_error_like_partial(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("error"),
    )
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "ran_error"
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()


def test_iterate_once_invokes_sweep_stub_when_current_empty(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan 8 fills in the sweep; for v1 the stub must be invoked."""
    called: list[bool] = []

    def _spy_sweep(cfg: ExecutorConfig, source: FilesystemQueueSource) -> None:
        called.append(True)

    monkeypatch.setattr("ralph_executor.loop._run_sweep", _spy_sweep)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)
    assert called == [True], "sweep stub must be invoked when current/ is empty"


def test_iterate_once_invokes_cycle_detector_stub(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan 9 fills in cycle detection; for v1 the stub must be invoked."""
    called: list[bool] = []

    def _spy_check(cfg: ExecutorConfig, source: FilesystemQueueSource) -> bool:
        called.append(True)
        return False

    monkeypatch.setattr("ralph_executor.loop._check_cycle_detector", _spy_check)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)
    assert called == [True]


def test_iterate_once_pulls_ralph_queue_every_iteration(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pull_calls: list[str] = []
    from ralph_executor import git_ops as real_git_ops

    original_pull = real_git_ops.pull

    def _spy_pull(repo: Path, branch: str, remote: str = "origin") -> None:
        pull_calls.append(branch)
        original_pull(repo, branch, remote)

    monkeypatch.setattr("ralph_executor.loop.git_ops.pull", _spy_pull)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)
    assert "ralph-queue" in pull_calls


def test_iterate_once_pulls_main_only_on_fresh_claim(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    pull_calls: list[str] = []
    from ralph_executor import git_ops as real_git_ops

    original_pull = real_git_ops.pull

    def _spy_pull(repo: Path, branch: str, remote: str = "origin") -> None:
        pull_calls.append(branch)
        original_pull(repo, branch, remote)

    monkeypatch.setattr("ralph_executor.loop.git_ops.pull", _spy_pull)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)  # claim → main pulled
    assert "main" in pull_calls

    pull_calls.clear()
    iterate_once(cfg_for_repo)  # current occupied → main NOT pulled
    assert "main" not in pull_calls
    assert "ralph-queue" in pull_calls


def test_run_loop_terminates_when_iterate_returns_halt(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_loop`` honours a halt signal from the cycle-detector stub."""

    def _trip(cfg: ExecutorConfig, source: FilesystemQueueSource) -> bool:
        return True

    monkeypatch.setattr("ralph_executor.loop._check_cycle_detector", _trip)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    results = list(run_loop(cfg_for_repo, max_iterations=5))
    assert any(r.outcome == "halted" for r in results)
    # The halt must terminate run_loop early.
    assert len(results) < 5
