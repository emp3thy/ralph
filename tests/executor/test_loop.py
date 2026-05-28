"""Tests for ``ralph_executor.loop``."""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import ClaudeOutcome
from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import (
    iterate_once,
    run_loop,
)
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.safety.events import EventType, open_log
from ralph_executor.types import PBI
from ralph_executor.worktree import (
    list_worktrees,
    queue_worktree_path,
    work_worktree_path,
)
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
    # Accept the worktree-mode ``cwd`` / ``pbi_dir`` kwargs so the same
    # stub works for both legacy and worktree-mode iterations.
    def _fake_spawn(
        cfg: ExecutorConfig,
        pbi: object,
        *,
        cwd: Path | None = None,
        pbi_dir: Path | None = None,
    ) -> ClaudeOutcome:
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


def test_iterate_once_recovers_from_push_conflict(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PushRebaseConflict from the persist path must NOT crash the loop.

    Reproduction of the LOOP-PERSIST-PUSH-RACE bug: a concurrent writer
    advanced origin/ralph-queue in a way that conflicts with the
    iteration's persist commit. The helper raises PushRebaseConflict;
    iterate_once must catch it, log a warning, and return outcome
    push_conflict so the loop keeps running.
    """
    from ralph_executor.git_ops import PushRebaseConflict

    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim WI-1234 into current/
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )

    def _raise_conflict(*args: object, **kwargs: object) -> None:
        raise PushRebaseConflict(("HISTORY.md",))

    monkeypatch.setattr(
        "ralph_executor.loop._persist_iteration_writes",
        _raise_conflict,
    )

    # No crash; outcome surfaces the conflict.
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "push_conflict"
    assert result.pbi_id == "WI-1234"


def test_iterate_once_recovers_from_push_conflict_in_run_ralph(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: BugBot PR #36 flagged that the existing PushRebaseConflict
    catch only wraps `_persist_iteration_writes`. `_run_ralph` reaches
    into `move_current_to_pending_pr` / `handle_stuck`, both of which
    invoke `push_with_rebase`. A conflict during those movement paths
    must also be caught — otherwise the executor crashes on the exact
    failure mode this PR claims to handle."""
    from ralph_executor.git_ops import PushRebaseConflict

    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim WI-1234

    def _raise_conflict(cfg: ExecutorConfig, pbi: object) -> tuple[ClaudeOutcome, object]:
        raise PushRebaseConflict(("pending-pr/WI-1234/PBI.md",))

    monkeypatch.setattr("ralph_executor.loop._run_ralph", _raise_conflict)

    result = iterate_once(cfg_for_repo)
    assert result.outcome == "push_conflict"
    assert result.pbi_id == "WI-1234"


def test_iterate_once_recovers_from_push_conflict_in_claim_pbi(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same regression for the claim path: `_claim_pbi` invokes
    `move_inbox_to_current` → `push_with_rebase`. A concurrent writer
    can race the claim push, and the loop must keep running."""
    from ralph_executor.git_ops import PushRebaseConflict

    _populate_inbox(fake_repo)

    def _raise_conflict(cfg: ExecutorConfig, pbi: object) -> object:
        raise PushRebaseConflict(("inbox/WI-1234/PBI.md",))

    monkeypatch.setattr("ralph_executor.loop._claim_pbi", _raise_conflict)

    result = iterate_once(cfg_for_repo)
    assert result.outcome == "push_conflict"
    assert result.pbi_id == "WI-1234"


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


def test_file_touched_event_emitted_on_iteration_commit(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After ``_persist_iteration_writes`` commits Claude's HISTORY.md
    edit, a ``FILE_TOUCHED`` event must land in the log with the path
    of the changed file in its payload. The cycle detector reserves
    this event for future per-iteration rules; emit unconditionally
    for forward compatibility.
    """
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim WI-1234 → current/

    history_path = fake_repo / ".ralph" / "current" / "WI-1234" / "HISTORY.md"
    history_before = history_path.read_text(encoding="utf-8")

    def _appending_spawn(cfg: ExecutorConfig, pbi: object) -> ClaudeOutcome:
        history_path.write_text(
            history_before + "\n## Iteration 1 — partial\n",
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
    iterate_once(cfg_for_repo)

    now = datetime.now(tz=UTC)
    event_log = open_log(fake_repo)
    try:
        events = event_log.recent(window=timedelta(hours=1), now=now)
    finally:
        event_log.close()
    touched = [ev for ev in events if ev.kind == EventType.FILE_TOUCHED]
    assert len(touched) == 1, f"expected one FILE_TOUCHED event, got {touched!r}"
    assert touched[0].pbi_id == "WI-1234"
    files = touched[0].payload.get("files")
    assert isinstance(files, list) and files, "FILE_TOUCHED payload must list files"
    assert any("HISTORY.md" in path for path in files), (
        f"expected HISTORY.md in touched files, got {files!r}"
    )


def test_run_sweep_skips_when_bot_author_email_empty(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without bot_author_email set, sweep must skip with a WARNING that
    still mentions the legacy env-var name so operators grepping logs see
    the same anchor."""
    from dataclasses import replace

    from ralph_executor.loop import _run_sweep

    # Ensure no inherited env can satisfy the sweep — only cfg matters.
    monkeypatch.delenv("RALPH_ADO_AUTHOR_EMAIL", raising=False)

    cfg = replace(cfg_for_repo, bot_author_email="")
    source = FilesystemQueueSource(cfg)
    with caplog.at_level("WARNING", logger="ralph_executor.loop"):
        _run_sweep(cfg, source)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("bot_author_email" in m for m in msgs), msgs
    assert any("RALPH_ADO_AUTHOR_EMAIL" in m for m in msgs), msgs


def test_run_sweep_passes_cfg_values_to_sweep_config(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SweepConfig must be constructed from cfg fields, not from os.environ."""
    from dataclasses import replace

    from ralph_executor.loop import _run_sweep

    # If _run_sweep regressed to reading env, this poisoned env would
    # cause SweepConfig to be built with the env value rather than cfg's.
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "WRONG@example.com")
    monkeypatch.setenv("RALPH_STALE_DAYS", "999")

    captured: dict[str, object] = {}

    class _SpySweepConfig:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "ralph_executor.sweep.runner.SweepConfig",
        _SpySweepConfig,
    )

    # Stub out the actual sweep run so we don't need a real PR skill on disk.
    # Must return a SweepResult-shaped object — _run_sweep logs .pbis_scanned /
    # .actions / .errors after the call.
    from types import SimpleNamespace

    monkeypatch.setattr(
        "ralph_executor.sweep.run",
        lambda ctx: SimpleNamespace(pbis_scanned=0, actions=[], errors=[]),
    )
    # Bypass the scripts-path check.
    monkeypatch.setattr(
        "ralph_executor.loop._pr_skill_scripts_path",
        lambda cfg: fake_repo,
    )

    cfg = replace(
        cfg_for_repo,
        bot_author_email="ralph@x.test",
        stale_days=5,
        auto_merge_clean_prs=True,
    )
    _run_sweep(cfg, FilesystemQueueSource(cfg))

    assert captured["ralph_author_email"] == "ralph@x.test"
    assert captured["stale_threshold"] == timedelta(days=5)
    assert captured["max_attempts"] == cfg.max_attempts
    assert captured["auto_merge_clean_prs"] is True


def test_run_sweep_does_not_read_env_for_promoted_knobs(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a regression where someone re-adds os.environ.get for these
    two names inside _run_sweep."""
    import ralph_executor.loop as loop_mod

    src = Path(loop_mod.__file__).read_text(encoding="utf-8")
    assert 'os.environ.get("RALPH_ADO_AUTHOR_EMAIL"' not in src
    assert 'os.environ.get("RALPH_STALE_DAYS"' not in src


def test_run_sweep_queue_root_uses_queue_worktree_under_worktree_mode(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In worktree mode the sweep's ``queue_root`` must point at the
    queue worktree's ``.ralph/`` (where pending-pr/ actually lives), not
    at the primary checkout's ``.ralph/`` (which is empty — main has no
    queue state). Without this fix the sweep scans 0 PBIs while
    pending-pr/ accumulates orphans in the queue worktree."""
    from dataclasses import replace

    from ralph_executor.loop import _run_sweep

    captured: dict[str, object] = {}

    class _SpySweepContext:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "ralph_executor.sweep.runner.SweepContext",
        _SpySweepContext,
    )
    from types import SimpleNamespace

    monkeypatch.setattr(
        "ralph_executor.sweep.run",
        lambda ctx: SimpleNamespace(pbis_scanned=0, actions=[], errors=[]),
    )
    monkeypatch.setattr(
        "ralph_executor.loop._pr_skill_scripts_path",
        lambda cfg: fake_repo,
    )

    cfg = replace(cfg_for_repo_worktree, bot_author_email="ralph@x.test")
    _run_sweep(cfg, FilesystemQueueSource(cfg))

    expected = queue_worktree_path(fake_repo) / ".ralph"
    assert captured["queue_root"] == expected, (
        f"sweep queue_root must point at the queue worktree's .ralph/; "
        f"got {captured['queue_root']!r}, expected {expected!r}"
    )


def test_file_touched_skipped_on_empty_commit(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Claude writes nothing inside the PBI dir during a partial
    iteration, ``_persist_iteration_writes`` produces no new commit
    and must NOT emit ``FILE_TOUCHED`` — empty payloads would dilute
    the cycle detector's signal.
    """
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)

    now = datetime.now(tz=UTC)
    event_log = open_log(fake_repo)
    try:
        events = event_log.recent(window=timedelta(hours=1), now=now)
    finally:
        event_log.close()
    assert [ev for ev in events if ev.kind == EventType.FILE_TOUCHED] == []


# ----------------------------------------------------------------------
# Task 9 — worktree-mode integration tests (`cfg.use_worktrees=True`).
# The shared ``cfg_for_repo`` fixture defaults to legacy single-checkout
# (use_worktrees=False); tests below derive a worktree-mode config so
# both branches stay covered. The helpers tests live in
# ``test_worktree.py`` — these focus on the loop integration.
# ----------------------------------------------------------------------


@pytest.fixture
def cfg_for_repo_worktree(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ExecutorConfig]:
    """Worktree-mode variant of ``cfg_for_repo`` with best-effort cleanup.

    Pytest's ``tmp_path`` cleanup trips on Windows when ``.ralph-work/``
    still holds git-registered worktrees; force-remove them on teardown
    so the next test (and the harness) start clean.

    Task 7 sub-step 7C wires ``_claim_pbi`` to call ``target_clone.ensure_clone``
    before creating the work worktree. To keep the existing worktree-mode
    tests (which never set up a real clone) working unchanged, this fixture
    monkeypatches ``ensure_clone`` to return a ``TargetClone`` whose
    ``clone_root`` IS ``fake_repo``. Tests that need a distinct clone root
    can override the monkeypatch.
    """
    cfg = dataclasses.replace(cfg_for_repo, use_worktrees=True)

    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    def _fake_ensure_clone_to_fake_repo(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        return TargetClone(info=info, clone_root=fake_repo)

    monkeypatch.setattr(
        "ralph_executor.target_clone.ensure_clone",
        _fake_ensure_clone_to_fake_repo,
    )

    try:
        yield cfg
    finally:
        for entry in list_worktrees(fake_repo):
            path = entry.get("path")
            if isinstance(path, str) and Path(path).resolve() != fake_repo.resolve():
                subprocess.run(
                    ["git", "-C", str(fake_repo), "worktree", "remove", "--force", path],
                    check=False,
                    capture_output=True,
                )
        subprocess.run(
            ["git", "-C", str(fake_repo), "worktree", "prune"],
            check=False,
            capture_output=True,
        )


def test_claim_with_worktrees_creates_queue_and_work_trees(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worktree-mode claim materialises both the long-lived queue tree
    and a per-PBI work tree without touching the primary checkout's
    branch."""
    _populate_inbox(fake_repo)
    primary_before = _git(fake_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )

    result = iterate_once(cfg_for_repo_worktree)

    assert result.outcome == "claimed"
    queue_wt = queue_worktree_path(fake_repo)
    work_wt = work_worktree_path(fake_repo, "WI-1234")
    # Queue worktree is on ralph-queue with the PBI moved into current/.
    assert queue_wt.is_dir()
    assert (queue_wt / ".ralph" / "current" / "WI-1234").is_dir()
    # Work worktree exists on the feature branch.
    assert work_wt.is_dir()
    work_branch = _git(work_wt, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert work_branch == "ralph/WI-1234"
    # Primary checkout's branch is untouched.
    primary_after = _git(fake_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert primary_after == primary_before


def test_persist_iteration_writes_worktree_mode_commits_from_queue_tree(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In worktree mode ``_persist_iteration_writes`` commits and pushes
    from the queue worktree — the primary checkout is never branch-swapped
    and the new commit lands on ``origin/ralph-queue`` via the queue
    worktree's push."""
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo_worktree)  # claim — creates worktrees

    queue_wt = queue_worktree_path(fake_repo)
    pbi_dir_in_queue = queue_wt / ".ralph" / "current" / "WI-1234"
    history_path = pbi_dir_in_queue / "HISTORY.md"
    history_before = history_path.read_text(encoding="utf-8")

    def _appending_spawn(
        cfg: ExecutorConfig,
        pbi: object,
        *,
        cwd: Path | None = None,
        pbi_dir: Path | None = None,
    ) -> ClaudeOutcome:
        history_path.write_text(
            history_before + "\n## Iteration 1 — partial\n",
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
    head_before = _git(queue_wt, "rev-parse", "HEAD").strip()
    primary_before = _git(fake_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()

    result = iterate_once(cfg_for_repo_worktree)

    assert result.outcome == "ran_partial"
    head_after = _git(queue_wt, "rev-parse", "HEAD").strip()
    assert head_after != head_before, "queue worktree must produce a new commit"
    # Primary's HEAD is unchanged — no branch swap on the primary.
    primary_after = _git(fake_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert primary_after == primary_before
    # The push landed on origin/ralph-queue.
    _git(fake_repo, "fetch", "origin")
    remote_head = _git(fake_repo, "rev-parse", "origin/ralph-queue").strip()
    assert remote_head == head_after
    # No dirty files left in the queue worktree's PBI dir.
    pbi_status = _git(queue_wt, "status", "--porcelain", ".ralph/current/WI-1234").strip()
    assert pbi_status == "", f"queue PBI dir dirty after persist: {pbi_status!r}"


def test_terminal_outcome_removes_work_tree(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the iteration ends in a terminal outcome (pr_created here),
    the per-PBI work worktree is torn down. The queue worktree persists
    across PBIs."""
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo_worktree)  # claim → worktrees created

    work_wt = work_worktree_path(fake_repo, "WI-1234")
    queue_wt = queue_worktree_path(fake_repo)
    assert work_wt.is_dir(), "precondition: work worktree exists after claim"

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("pr_created", pr_url="https://example/pr/9"),
    )
    result = iterate_once(cfg_for_repo_worktree)

    assert result.outcome == "ran_pr_created"
    assert not work_wt.exists(), "work worktree should be removed on pr_created"
    # Queue worktree persists.
    assert queue_wt.is_dir()
    # ``ralph/WI-1234`` ref is preserved — pending-pr PBIs need it.
    feature_ref = _git(fake_repo, "branch", "--list", "ralph/WI-1234").strip()
    assert "ralph/WI-1234" in feature_ref


# ``test_legacy_single_checkout_path_still_works_when_flag_false``: the
# entire test_loop.py suite above uses ``cfg_for_repo`` (use_worktrees=
# False) and exercises claim → spawn → persist → terminal-outcome moves
# end-to-end. The legacy single-checkout path therefore has dense
# regression coverage already; no dedicated test added here.


def test_event_log_lives_in_queue_worktree_under_worktree_mode(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In worktree mode every ``open_log`` call must target the queue
    worktree, not the primary checkout. Otherwise events (notably
    ``PBI_OPENED`` from ``move_inbox_to_current``) silently land in a
    stray ``.ralph/state/events.db`` in the primary checkout — never
    committed/pushed with the queue branch and invisible to the cycle
    detector after a process restart."""
    _populate_inbox(fake_repo)
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn("partial"))

    iterate_once(cfg_for_repo_worktree)

    queue_wt = queue_worktree_path(fake_repo)
    queue_db = queue_wt / ".ralph" / "state" / "events.db"
    primary_db = fake_repo / ".ralph" / "state" / "events.db"
    assert queue_db.is_file(), f"event log must live in the queue worktree; expected at {queue_db}"
    assert not primary_db.exists(), (
        f"primary checkout should never get a stray events.db; found {primary_db}"
    )
    # And the PBI_OPENED event from move_inbox_to_current actually lives there.
    event_log = open_log(queue_wt)
    try:
        events = event_log.recent(window=timedelta(hours=1), now=datetime.now(tz=UTC))
    finally:
        event_log.close()
    pbi_opened = [e for e in events if e.kind == EventType.PBI_OPENED]
    assert pbi_opened, "PBI_OPENED event missing from queue-worktree event log"


def test_stuck_blocked_move_targets_queue_worktree(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In worktree mode `handle_stuck` must operate against the queue
    worktree, not the primary checkout. ``pbi.path`` lives under
    ``.ralph-work/queue/.ralph/current/<id>`` so calling
    ``move_to_blocked`` with ``repo=cfg.repo_path`` raises ValueError
    (``parts[0] == '.ralph-work'`` fails the ``parts[0] != '.ralph'``
    guard in ``ralph_executor/safety/stuck.py::move_to_blocked``).
    The blocked PBI must end up in the queue worktree's ``.ralph/blocked/``,
    where the next ``git push`` from the queue worktree will commit it."""
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo_worktree)  # claim → worktrees created

    queue_wt = queue_worktree_path(fake_repo)
    pbi_dir_in_queue = queue_wt / ".ralph" / "current" / "WI-1234"

    def _stuck_spawn(
        cfg: ExecutorConfig,
        pbi: object,
        *,
        cwd: Path | None = None,
        pbi_dir: Path | None = None,
    ) -> ClaudeOutcome:
        (pbi_dir_in_queue / "STUCK.md").write_text("# stuck\n", encoding="utf-8")
        return ClaudeOutcome(
            kind="stuck",
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.0,
        )

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stuck_spawn)
    result = iterate_once(cfg_for_repo_worktree)

    assert result.outcome == "ran_stuck"
    # Lands in queue worktree, not primary.
    assert (queue_wt / ".ralph" / "blocked" / "WI-1234").is_dir()
    assert not (fake_repo / ".ralph" / "blocked" / "WI-1234").exists()


def test_max_attempts_blocked_move_targets_queue_worktree(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In worktree mode the max-attempts blocked move must target the
    queue worktree's ``.ralph/blocked/``. Otherwise the PBI is moved to
    the primary checkout's working tree (on `main`, no `.ralph/`),
    landing outside git tracking and silently disappearing from the
    queue system."""
    # `max_attempts()` reads RALPH_MAX_ATTEMPTS from the env, not from cfg.
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "1")
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo_worktree)  # claim

    queue_wt = queue_worktree_path(fake_repo)

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn("error"))
    # First error: attempts 0 -> 1 (== limit, allowed).
    iterate_once(cfg_for_repo_worktree)
    # Second error: attempts 1 -> 2 (> limit), triggers max-attempts move.
    result = iterate_once(cfg_for_repo_worktree)

    assert result.outcome == "ran_stuck"
    assert (queue_wt / ".ralph" / "blocked" / "WI-1234").is_dir(), (
        "max-attempts blocked move must target the queue worktree"
    )
    assert not (fake_repo / ".ralph" / "blocked" / "WI-1234").exists(), (
        "primary checkout must never receive blocked PBI dirs in worktree mode"
    )


# ----------------------------------------------------------------------
# Task 7 sub-step 7A: _ClaimError + _read_target_repo_from_pbi
# ----------------------------------------------------------------------


def _build_pbi(pbi_dir: Path, pbi_id: str) -> PBI:
    return PBI(
        id=pbi_id,
        type="feature",
        status="current",
        severity="normal",
        attempts=0,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        path=pbi_dir,
    )


def test_read_target_repo_from_pbi_reads_frontmatter(tmp_path: Path) -> None:
    """Helper reads target_repo field from PBI.md frontmatter."""
    from ralph_executor.loop import _read_target_repo_from_pbi

    pbi_dir = tmp_path / "WI-1"
    pbi_dir.mkdir()
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        "id: WI-1\n"
        "type: feature\n"
        "status: current\n"
        "severity: normal\n"
        "attempts: 0\n"
        "target_repo: https://github.com/emp3thy/ralph\n"
        "---\n"
        "# Title\n",
        encoding="utf-8",
    )
    pbi = _build_pbi(pbi_dir, "WI-1")
    assert _read_target_repo_from_pbi(pbi) == "https://github.com/emp3thy/ralph"


def test_read_target_repo_from_pbi_raises_when_missing(tmp_path: Path) -> None:
    """Missing target_repo field raises _ClaimError."""
    from ralph_executor.loop import _ClaimError, _read_target_repo_from_pbi

    pbi_dir = tmp_path / "WI-2"
    pbi_dir.mkdir()
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        "id: WI-2\n"
        "type: feature\n"
        "status: current\n"
        "severity: normal\n"
        "attempts: 0\n"
        "---\n"
        "# Title (no target_repo)\n",
        encoding="utf-8",
    )
    pbi = _build_pbi(pbi_dir, "WI-2")
    with pytest.raises(_ClaimError, match="missing target_repo"):
        _read_target_repo_from_pbi(pbi)


def test_read_target_repo_from_pbi_raises_when_no_entry_file(tmp_path: Path) -> None:
    """Missing entry file (no PBI.md/BUG.md/FEEDBACK.md) raises _ClaimError."""
    from ralph_executor.loop import _ClaimError, _read_target_repo_from_pbi

    pbi_dir = tmp_path / "WI-3"
    pbi_dir.mkdir()
    pbi = _build_pbi(pbi_dir, "WI-3")
    with pytest.raises(_ClaimError, match="no entry file"):
        _read_target_repo_from_pbi(pbi)


def test_read_target_repo_from_pbi_uses_bug_md_for_bug_type(tmp_path: Path) -> None:
    """Bug PBIs (no PBI.md, but BUG.md present) get target_repo from BUG.md."""
    from ralph_executor.loop import _read_target_repo_from_pbi

    pbi_dir = tmp_path / "WI-4"
    pbi_dir.mkdir()
    (pbi_dir / "BUG.md").write_text(
        "---\n"
        "id: WI-4\n"
        "type: bug\n"
        "status: current\n"
        "severity: high\n"
        "attempts: 0\n"
        "target_repo: https://github.com/acme/svc\n"
        "---\n"
        "# Bug\n",
        encoding="utf-8",
    )
    pbi = _build_pbi(pbi_dir, "WI-4")
    assert _read_target_repo_from_pbi(pbi) == "https://github.com/acme/svc"


# ----------------------------------------------------------------------
# Task 7 sub-step 7B: parse + host check inside _claim_pbi
# ----------------------------------------------------------------------


def _write_pbi_with_target(pbi_dir: Path, pbi_id: str, target_repo: str) -> None:
    """Write a minimal PBI.md with a custom ``target_repo`` value."""
    pbi_dir.mkdir(parents=True, exist_ok=True)
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        f"id: {pbi_id}\n"
        "type: feature\n"
        "status: current\n"
        "severity: normal\n"
        "attempts: 0\n"
        f'target_repo: "{target_repo}"\n'
        "---\n"
        f"# {pbi_id}\n",
        encoding="utf-8",
    )


def test_claim_raises_claim_error_for_non_github_host(
    cfg_for_repo: ExecutorConfig, tmp_path: Path
) -> None:
    """A PBI with target_repo on a non-github host raises _ClaimError 'unsupported host'."""
    from ralph_executor.loop import _claim_pbi, _ClaimError

    pbi_dir = tmp_path / "WI-ADO"
    _write_pbi_with_target(pbi_dir, "WI-ADO", "https://dev.azure.com/myorg/myproj/_git/myrepo")
    pbi = _build_pbi(pbi_dir, "WI-ADO")
    with pytest.raises(_ClaimError, match="unsupported host"):
        _claim_pbi(cfg_for_repo, pbi)


def test_claim_raises_claim_error_for_invalid_url(
    cfg_for_repo: ExecutorConfig, tmp_path: Path
) -> None:
    """A PBI with a malformed target_repo raises _ClaimError 'invalid target_repo URL'."""
    from ralph_executor.loop import _claim_pbi, _ClaimError

    pbi_dir = tmp_path / "WI-BAD"
    _write_pbi_with_target(pbi_dir, "WI-BAD", "not a url")
    pbi = _build_pbi(pbi_dir, "WI-BAD")
    with pytest.raises(_ClaimError, match="invalid target_repo URL"):
        _claim_pbi(cfg_for_repo, pbi)


# ----------------------------------------------------------------------
# Task 7 sub-step 7C: ensure_clone + worktree creation inside clone
# ----------------------------------------------------------------------


def test_claim_in_worktree_mode_clones_target_and_creates_worktree_in_clone(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Happy path: worktree-mode claim runs ensure_clone, creates the per-PBI
    worktree INSIDE the clone, and returns a PBI with target_info +
    work_worktree populated."""
    from dataclasses import replace as dc_replace

    from ralph_executor.loop import _claim_pbi
    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    custom_ws = tmp_path / "ws"
    clone_root = custom_ws / "clones" / "test-repo"
    cfg = dc_replace(cfg_for_repo_worktree, workspace_root=custom_ws)

    ensure_clone_calls: list[tuple[TargetRepoInfo, Path]] = []

    def _fake_ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        ensure_clone_calls.append((info, workspace_root))
        clone_root.mkdir(parents=True, exist_ok=True)
        return TargetClone(info=info, clone_root=clone_root)

    ensure_wt_calls: list[dict[str, object]] = []

    def _fake_ensure_worktree(
        git_root: Path,
        *,
        worktree_path: Path,
        branch: str,
        create_branch_from: str | None = None,
    ) -> None:
        ensure_wt_calls.append(
            {
                "git_root": Path(git_root),
                "worktree_path": Path(worktree_path),
                "branch": branch,
                "create_branch_from": create_branch_from,
            }
        )

    _populate_inbox(fake_repo, pbi_id="WI-CLONE")

    # Prime the queue worktree and pick the PBI BEFORE installing the
    # ensure_worktree stub — otherwise _pull_queue's real ensure_worktree
    # call gets replaced with the no-op stub and git pull errors on a
    # non-existent directory.
    from ralph_executor.loop import _pull_queue

    _pull_queue(cfg)
    source = FilesystemQueueSource(cfg)
    picked = source.pick_next()
    assert picked is not None and picked.id == "WI-CLONE"

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _fake_ensure_clone)
    monkeypatch.setattr("ralph_executor.loop.ensure_worktree", _fake_ensure_worktree)

    claimed = _claim_pbi(cfg, picked)

    # ensure_clone was called with the parsed info + custom workspace.
    assert len(ensure_clone_calls) == 1
    called_info, called_ws = ensure_clone_calls[0]
    assert called_info.host == "github.com"
    assert called_info.owner == "test"
    assert called_info.name == "repo"
    assert called_ws == custom_ws

    # The per-PBI worktree was materialised against the CLONE, not ralph's repo.
    work_wt_calls = [c for c in ensure_wt_calls if c["branch"] == "ralph/WI-CLONE"]
    assert len(work_wt_calls) == 1
    assert work_wt_calls[0]["git_root"] == clone_root
    assert work_wt_calls[0]["worktree_path"] == clone_root / ".ralph-work" / "WI-CLONE"
    assert work_wt_calls[0]["create_branch_from"] == "origin/main"

    # Returned PBI carries the multi-target fields.
    assert claimed.target_repo == "https://github.com/test/repo"
    assert claimed.target_info is not None
    assert claimed.target_info.owner == "test"
    assert claimed.target_info.name == "repo"
    assert claimed.work_worktree == clone_root / ".ralph-work" / "WI-CLONE"


def test_claim_in_worktree_mode_raises_claim_error_when_clone_unreachable(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure_clone -> TargetUnreachable maps to _ClaimError("target unreachable: ...")."""
    from ralph_executor.loop import _claim_pbi, _ClaimError
    from ralph_executor.target_clone import TargetUnreachable
    from ralph_executor.url_utils import TargetRepoInfo

    _populate_inbox(fake_repo, pbi_id="WI-NET")
    from ralph_executor.loop import _pull_queue

    _pull_queue(cfg_for_repo_worktree)
    source = FilesystemQueueSource(cfg_for_repo_worktree)
    picked = source.pick_next()
    assert picked is not None

    def _raise_unreachable(info: TargetRepoInfo, workspace_root: Path) -> None:
        raise TargetUnreachable("network unreachable")

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _raise_unreachable)

    with pytest.raises(_ClaimError, match=r"target unreachable: network unreachable"):
        _claim_pbi(cfg_for_repo_worktree, picked)


# ----------------------------------------------------------------------
# Task 7 sub-step 7.8: iterate_once catches _ClaimError -> blocked/
# ----------------------------------------------------------------------


def test_iterate_once_moves_pbi_to_blocked_when_claim_raises_claim_error(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim that hits a non-github host raises _ClaimError; iterate_once
    catches it, moves the PBI from inbox/ to blocked/<id>/, appends the
    failure reason to HISTORY.md, and returns ``claim_failed``."""
    _git(fake_repo, "checkout", "ralph-queue")
    pbi_dir = write_sample_pbi(
        fake_repo,
        pbi_id="WI-ADO",
        target_repo="https://dev.azure.com/myorg/myproj/_git/myrepo",
    )
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", "inbox: WI-ADO")
    _git(fake_repo, "push", "origin", "ralph-queue")
    _git(fake_repo, "checkout", "main")

    result = iterate_once(cfg_for_repo)

    assert result.outcome == "claim_failed"
    assert result.pbi_id == "WI-ADO"

    _git(fake_repo, "checkout", "ralph-queue")
    _git(fake_repo, "pull", "origin", "ralph-queue")
    assert (fake_repo / ".ralph" / "blocked" / "WI-ADO").is_dir()
    assert not (fake_repo / ".ralph" / "inbox" / "WI-ADO").exists()
    history = (fake_repo / ".ralph" / "blocked" / "WI-ADO" / "HISTORY.md").read_text(
        encoding="utf-8"
    )
    assert "Claim failed" in history
    assert "unsupported host" in history


def test_run_loop_exits_after_idle_exit_threshold_consecutive_idles(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default (``watch_mode=False``): two idle iterations in a row drain the
    loop. ``run_loop`` exits cleanly after the second idle without
    re-entering ``iterate_once`` a third time."""
    from ralph_executor.loop import IterationResult

    calls: list[int] = []

    def _fake_iterate(cfg: ExecutorConfig) -> IterationResult:
        calls.append(len(calls))
        return IterationResult(outcome="idle", pbi_id=None)

    monkeypatch.setattr("ralph_executor.loop.iterate_once", _fake_iterate)
    results = list(run_loop(cfg_for_repo))
    assert len(results) == 2
    assert all(r.outcome == "idle" for r in results)
    assert len(calls) == 2, "third iteration should NOT have been entered"


def test_run_loop_watch_mode_does_not_drain_on_idle(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``watch_mode=True``: idle iterations never exit the loop. Bound the
    test via ``max_iterations`` so it terminates."""
    from ralph_executor.loop import IterationResult

    cfg_watch = dataclasses.replace(cfg_for_repo, watch_mode=True)

    def _fake_iterate(cfg: ExecutorConfig) -> IterationResult:
        return IterationResult(outcome="idle", pbi_id=None)

    monkeypatch.setattr("ralph_executor.loop.iterate_once", _fake_iterate)
    results = list(run_loop(cfg_watch, max_iterations=3))
    assert len(results) == 3
    assert all(r.outcome == "idle" for r in results)


def test_run_loop_non_idle_outcome_resets_consecutive_idle_counter(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-idle outcome (claimed, ran_partial, …) between idle ticks
    resets the consecutive-idle counter so the loop only drains after a
    fresh streak of idles fills the threshold."""
    from ralph_executor.loop import IterationResult

    scripted: list[IterationResult] = [
        IterationResult(outcome="idle", pbi_id=None),
        IterationResult(outcome="claimed", pbi_id="WI-XYZ"),
        IterationResult(outcome="idle", pbi_id=None),
        IterationResult(outcome="idle", pbi_id=None),
        # Anything after this would indicate the drain didn't trip on the
        # second consecutive idle — set a sentinel so the test fails loudly
        # if run_loop overshoots.
        IterationResult(outcome="ran_partial", pbi_id="WI-OVERSHOOT"),
    ]
    iterator = iter(scripted)

    def _fake_iterate(cfg: ExecutorConfig) -> IterationResult:
        return next(iterator)

    monkeypatch.setattr("ralph_executor.loop.iterate_once", _fake_iterate)
    results = list(run_loop(cfg_for_repo))
    assert [r.outcome for r in results] == ["idle", "claimed", "idle", "idle"]


def test_run_loop_drains_against_empty_filesystem_queue(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: with no PBIs in ``.ralph/inbox/`` / ``.ralph/current/``
    and the default ``idle_exit_threshold=2``, ``run_loop`` yields exactly
    two ``idle`` results then returns. Exercises the real ``iterate_once``
    against the on-disk queue rather than a monkeypatched stub."""
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    results = list(run_loop(cfg_for_repo))
    assert len(results) == 2
    assert all(r.outcome == "idle" for r in results)


def test_iterate_once_moves_pbi_to_blocked_when_target_unreachable_in_worktree_mode(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worktree-mode: ensure_clone raises TargetUnreachable -> _ClaimError
    -> iterate_once moves PBI to blocked/<id>/ with reason in HISTORY.md."""
    from ralph_executor.target_clone import TargetUnreachable
    from ralph_executor.url_utils import TargetRepoInfo

    _populate_inbox(fake_repo, pbi_id="WI-NET2")

    def _raise_unreachable(info: TargetRepoInfo, workspace_root: Path) -> None:
        raise TargetUnreachable("network unreachable")

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _raise_unreachable)

    result = iterate_once(cfg_for_repo_worktree)

    assert result.outcome == "claim_failed"
    assert result.pbi_id == "WI-NET2"

    queue_wt = queue_worktree_path(fake_repo)
    assert (queue_wt / ".ralph" / "blocked" / "WI-NET2").is_dir()
    assert not (queue_wt / ".ralph" / "inbox" / "WI-NET2").exists()
    history = (queue_wt / ".ralph" / "blocked" / "WI-NET2" / "HISTORY.md").read_text(
        encoding="utf-8"
    )
    assert "Claim failed" in history
    assert "target unreachable: network unreachable" in history
