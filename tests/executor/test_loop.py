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


def test_partial_outcome_does_not_increment_attempts(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``partial`` outcomes are legitimate multi-step progress
    and must NOT decrement the attempts budget."""
    from ralph_executor.safety.attempts import read_attempts

    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim
    pbi_dir = fake_repo / ".ralph" / "current" / "WI-1234"
    before = read_attempts(pbi_dir)

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)
    iterate_once(cfg_for_repo)
    iterate_once(cfg_for_repo)

    assert read_attempts(pbi_dir) == before, "partial must not increment attempts"


def test_error_outcome_increments_attempts(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``error`` outcomes count as failed iterations and DO
    decrement the budget."""
    from ralph_executor.safety.attempts import read_attempts

    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim
    pbi_dir = fake_repo / ".ralph" / "current" / "WI-1234"
    before = read_attempts(pbi_dir)

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("error"),
    )
    iterate_once(cfg_for_repo)

    assert read_attempts(pbi_dir) == before + 1, "error must increment attempts"


def test_stuck_outcome_increments_attempts(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``stuck`` outcomes also count as failed iterations.

    The PBI is moved to ``blocked/`` by ``handle_stuck`` so we record the
    attempts value AFTER the move (from the new location).
    """
    from ralph_executor.safety.attempts import read_attempts

    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim
    pbi_dir = fake_repo / ".ralph" / "current" / "WI-1234"
    before = read_attempts(pbi_dir)

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
    iterate_once(cfg_for_repo)

    blocked_dir = fake_repo / ".ralph" / "blocked" / "WI-1234"
    assert blocked_dir.is_dir(), "stuck should move PBI to blocked/"
    assert read_attempts(blocked_dir) == before + 1, "stuck must increment attempts"


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


def test_run_loop_terminates_when_cycle_detector_trips(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_loop`` raises ``HaltedError`` when the cycle detector trips.

    Plan 9 changed the contract: ``_check_cycle_detector`` returning ``True``
    now causes ``iterate_once`` to raise ``HaltedError`` (after writing the
    META-BUG + sentinel), which ``run_loop`` re-raises immediately.
    """
    from ralph_executor.safety import HaltedError

    def _trip(cfg: ExecutorConfig, source: FilesystemQueueSource) -> bool:
        return True

    monkeypatch.setattr("ralph_executor.loop._check_cycle_detector", _trip)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    # run_loop raises HaltedError on the first iteration where the
    # cycle detector trips -- it never returns normally.
    with pytest.raises(HaltedError):
        list(run_loop(cfg_for_repo, max_iterations=5))


def test_iterate_once_persists_claude_history_writes_on_partial(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: when Claude appends to HISTORY.md during a partial-
    outcome iteration, the executor must commit + push that change on
    ralph-queue. Without _persist_iteration_writes, Claude's edits sit
    dirty in the working tree and get lost on the next iteration's
    branch checkout. Caught by first end-to-end self-host smoke
    (Ralph PR #7 — TEST-001 left HISTORY.md uncommitted on ralph-queue).
    """
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim TEST-001 → current/

    history_path = fake_repo / ".ralph" / "current" / "WI-1234" / "HISTORY.md"
    history_before = history_path.read_text(encoding="utf-8")

    def _appending_spawn(cfg: ExecutorConfig, pbi: object) -> ClaudeOutcome:
        # Claude appends an iteration record to HISTORY.md, then exits
        # with partial (multi-step PBI, no PR yet).
        history_path.write_text(
            history_before + "\n## Iteration 1 — partial — picked step 1\n",
            encoding="utf-8",
        )
        return ClaudeOutcome(
            kind="partial",
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _appending_spawn)

    head_before_iter = _git(fake_repo, "rev-parse", "HEAD").strip()
    result = iterate_once(cfg_for_repo)
    head_after_iter = _git(fake_repo, "rev-parse", "HEAD").strip()

    assert result.outcome == "ran_partial"
    # The persistence step must have produced a new commit on ralph-queue.
    assert head_after_iter != head_before_iter, (
        "expected _persist_iteration_writes to commit HISTORY.md edits"
    )
    # The PBI directory has no uncommitted changes after persist — local
    # per-checkout state like .ralph/state/events.db (Plan 9 event log)
    # is intentionally NOT staged, so untracked state/ may remain.
    pbi_status = _git(fake_repo, "status", "--porcelain", ".ralph/current/WI-1234").strip()
    assert pbi_status == "", f"PBI dir dirty after persist: {pbi_status!r}"
    # HISTORY.md on disk contains the appended iteration record.
    assert "## Iteration 1" in history_path.read_text(encoding="utf-8")
    # Commit message names the PBI.
    last_msg = _git(fake_repo, "log", "-1", "--pretty=%s").strip()
    assert "WI-1234" in last_msg


def test_persist_iteration_writes_excludes_state_dir(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_persist_iteration_writes must stage ONLY the PBI dir, never the
    .ralph/state/ tree (events.db, halt sentinel, etc. are per-checkout
    local state — committing them would produce a noisy commit every
    iteration even when Claude wrote nothing).
    """
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim

    # Drop a sentinel file under .ralph/state/ — it must NOT end up in
    # any commit the persist helper creates.
    state_marker = fake_repo / ".ralph" / "state" / "marker.txt"
    state_marker.parent.mkdir(parents=True, exist_ok=True)
    state_marker.write_text("local-only", encoding="utf-8")

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)

    # The marker is still untracked — never committed.
    tracked = _git(fake_repo, "ls-files", ".ralph/state/marker.txt").strip()
    assert tracked == "", "state/marker.txt was incorrectly tracked"
    assert state_marker.read_text(encoding="utf-8") == "local-only"
