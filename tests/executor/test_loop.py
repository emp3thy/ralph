"""Tests for ``ralph_executor.loop``."""

from __future__ import annotations

import dataclasses
import subprocess
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
from ralph_executor.worktree import work_worktree_path
from tests.executor.conftest import write_sample_pbi


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def _populate_inbox(fake_repo: Path, pbi_id: str = "WI-1234", severity: str = "normal") -> None:
    """Seed an inbox PBI directly on the queue clone's ``main`` branch."""
    write_sample_pbi(fake_repo, pbi_id=pbi_id, severity=severity)
    _git(fake_repo, "add", f".ralph/inbox/{pbi_id}")
    _git(fake_repo, "commit", "-m", f"inbox: {pbi_id}")
    _git(fake_repo, "push", "origin", "main")


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

    def _stuck_spawn(cfg: ExecutorConfig, pbi: object, **kwargs: object) -> ClaudeOutcome:
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
    # Regression for BUG-HANDLE-STUCK-NO-COMMIT: the move must be
    # committed (not stranded in the queue clone's working tree).
    status = _git(fake_repo, "status", "--porcelain").strip()
    assert status == "", f"working tree must be clean after stuck-move; got: {status!r}"
    head_files = _git(fake_repo, "ls-tree", "-r", "--name-only", "HEAD")
    assert ".ralph/blocked/WI-1234/PBI.md" in head_files
    assert ".ralph/current/WI-1234/PBI.md" not in head_files


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


def test_iterate_once_catches_prompt_compose_error(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for SETUP-RALPH-QUEUE-GITHUB-OMITS-PROMPT-TREE.

    Before this fix, a PromptComposeError raised inside ``spawn_claude_p``
    (e.g. when the queue clone is missing the ``prompt/`` topic-folder
    tree) propagated unhandled through ``_run_ralph`` → ``iterate_once``
    → ``run_loop`` → ``cli.main``'s safety net, killing the whole
    executor process. The loop must classify this as an ``error``
    iteration instead so the PBI stays in ``current/`` (or moves to
    ``blocked/`` after max attempts) and the executor keeps running.
    """
    from ralph_executor.prompt_composer import PromptComposeError

    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim WI-1234

    def _raise_compose_error(
        cfg: ExecutorConfig,
        pbi: object,
        *,
        cwd: Path | None = None,
        pbi_dir: Path | None = None,
    ) -> ClaudeOutcome:
        raise PromptComposeError("prompt_root /fake/queue/prompt is missing or not a directory")

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _raise_compose_error)

    result = iterate_once(cfg_for_repo)
    assert result.outcome == "ran_error"
    assert result.pbi_id == "WI-1234"
    # PBI must stay in current/ for the next iteration to retry.
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()
    history = (fake_repo / ".ralph" / "current" / "WI-1234" / "HISTORY.md").read_text(
        encoding="utf-8"
    )
    assert "prompt compose error" in history
    assert "is missing or not a directory" in history


def test_iterate_once_recovers_from_push_conflict(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PushRebaseConflict from the persist path must NOT crash the loop.

    Reproduction of the LOOP-PERSIST-PUSH-RACE bug: a concurrent writer
    advanced origin/main on the queue repo in a way that conflicts with
    the iteration's persist commit. The helper raises PushRebaseConflict;
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


def test_iterate_once_skips_uncommitted_inbox_dir_then_claims_after_commit(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: BUG-CLAIM-RACE-UNCOMMITTED-INBOX-DIR.

    An external writer (operator running ``ralph-new``, a second ralph
    session) wrote a new inbox PBI dir into the queue clone's working
    tree but has not yet committed it on the queue branch when the
    executor's next sweep tick fires. ``_list_pbis`` picks the dir up
    from the filesystem (no git-tracked filter), ``movements._move``
    calls ``git mv``, and ``git`` fails with
    ``fatal: source directory is empty``. Without the guard, the
    ``GitCommandError`` kills the loop.

    With the guard, ``iterate_once`` returns
    ``outcome="uncommitted_source"`` for the first tick. Once the
    writer's commit lands on origin, the next iteration claims the PBI
    cleanly.
    """
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )

    # Simulate the external writer mid-add: directory + entry file are on
    # disk inside the queue clone but NOT staged or committed yet.
    write_sample_pbi(fake_repo, pbi_id="WI-RACE")

    first = iterate_once(cfg_for_repo)
    assert first.outcome == "uncommitted_source"
    assert first.pbi_id == "WI-RACE"
    # The loop must not crash and must not have moved the dir.
    assert (fake_repo / ".ralph" / "inbox" / "WI-RACE").is_dir()
    assert not (fake_repo / ".ralph" / "current" / "WI-RACE").exists()

    # External writer finally commits + pushes their PBI.
    _git(fake_repo, "add", ".ralph/inbox/WI-RACE")
    _git(fake_repo, "commit", "-m", "inbox: WI-RACE")
    _git(fake_repo, "push", "origin", "main")

    # Next iteration sees the now-tracked dir and claims cleanly.
    second = iterate_once(cfg_for_repo)
    assert second.outcome == "claimed"
    assert second.pbi_id == "WI-RACE"
    assert (fake_repo / ".ralph" / "current" / "WI-RACE").is_dir()
    assert not (fake_repo / ".ralph" / "inbox" / "WI-RACE").exists()


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

    def _stuck_spawn(cfg: ExecutorConfig, pbi: object, **kwargs: object) -> ClaudeOutcome:
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


def test_iterate_once_refreshes_queue_clone_every_iteration(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each iteration calls ``ensure_queue_clone`` to refresh the local clone."""
    calls: list[tuple[Path, str, str, str]] = []
    from ralph_executor.queue_clone import ensure_queue_clone as real_ensure

    def _spy(
        workspace_root: Path,
        queue_repo: str,
        queue_branch: str,
        *,
        instance_id: str,
        timeout: float = 120.0,
    ) -> Path:
        calls.append((workspace_root, queue_repo, queue_branch, instance_id))
        return real_ensure(
            workspace_root,
            queue_repo,
            queue_branch,
            instance_id=instance_id,
            timeout=timeout,
        )

    monkeypatch.setattr("ralph_executor.loop.ensure_queue_clone", _spy)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)
    assert calls == [
        (
            cfg_for_repo.workspace_root,
            cfg_for_repo.queue_repo,
            cfg_for_repo.queue_branch,
            cfg_for_repo.instance_id,
        )
    ]


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
    outcome iteration, the executor must commit + push that change to the
    queue clone's main. Without _persist_iteration_writes, Claude's edits
    sit dirty in the queue clone and get lost on the next iteration.
    Caught by first end-to-end self-host smoke (Ralph PR #7 — TEST-001
    left HISTORY.md uncommitted).
    """
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim TEST-001 → current/

    history_path = fake_repo / ".ralph" / "current" / "WI-1234" / "HISTORY.md"
    history_before = history_path.read_text(encoding="utf-8")

    def _appending_spawn(cfg: ExecutorConfig, pbi: object, **kwargs: object) -> ClaudeOutcome:
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
    # The persistence step must have produced a new commit on the queue clone.
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

    def _appending_spawn(cfg: ExecutorConfig, pbi: object, **kwargs: object) -> ClaudeOutcome:
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


def test_run_sweep_queue_root_points_at_queue_clone(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep's ``queue_root`` must point at the queue clone's
    ``.ralph/`` (where pending-pr/ actually lives). After the queue-repo
    split there is only one queue path: ``<workspace_root>/queue/.ralph/``.
    """
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

    cfg = replace(cfg_for_repo, bot_author_email="ralph@x.test")
    _run_sweep(cfg, FilesystemQueueSource(cfg))

    expected = fake_repo / ".ralph"
    assert captured["queue_root"] == expected, (
        f"sweep queue_root must point at the queue clone's .ralph/; "
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
# Queue-clone-model integration tests. After EXECUTOR-QUEUE-REPO-SPLIT
# the queue clone IS the working tree (``cfg.workspace_root/queue``)
# and per-PBI work worktrees live under the target clone. The shared
# ``cfg_for_repo`` fixture sets up that topology; tests below pin the
# behaviours that previously lived in the now-deleted worktree-mode
# integration block.
# ----------------------------------------------------------------------


def test_claim_creates_work_worktree_on_feature_branch(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim materialises a per-PBI work worktree under the target
    clone, checked out on the feature branch ``ralph/<PBI-id>``."""
    _populate_inbox(fake_repo)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )

    result = iterate_once(cfg_for_repo)

    assert result.outcome == "claimed"
    # Queue clone has the PBI moved into current/.
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()
    # Work worktree exists on the feature branch (target clone IS fake_repo
    # via the autouse _fake_ensure_target_clone fixture).
    work_wt = work_worktree_path(fake_repo, "WI-1234")
    assert work_wt.is_dir()
    work_branch = _git(work_wt, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert work_branch == "ralph/WI-1234"


def test_terminal_outcome_removes_work_tree(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the iteration ends in a terminal outcome (pr_created here),
    the per-PBI work worktree is torn down. The queue clone persists."""
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim

    work_wt = work_worktree_path(fake_repo, "WI-1234")
    assert work_wt.is_dir(), "precondition: work worktree exists after claim"

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("pr_created", pr_url="https://example/pr/9"),
    )
    result = iterate_once(cfg_for_repo)

    assert result.outcome == "ran_pr_created"
    assert not work_wt.exists(), "work worktree should be removed on pr_created"
    # Queue clone persists.
    assert fake_repo.is_dir()
    # ``ralph/WI-1234`` ref is preserved — pending-pr PBIs need it.
    feature_ref = _git(fake_repo, "branch", "--list", "ralph/WI-1234").strip()
    assert "ralph/WI-1234" in feature_ref


def test_event_log_lives_in_queue_clone(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every ``open_log`` call targets the queue clone. The
    ``PBI_OPENED`` event from ``move_inbox_to_current`` must land in
    ``<queue-clone>/.ralph/state/events.db`` — that's the file the cycle
    detector reads on subsequent process restarts."""
    _populate_inbox(fake_repo)
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn("partial"))

    iterate_once(cfg_for_repo)

    queue_db = fake_repo / ".ralph" / "state" / "events.db"
    assert queue_db.is_file(), f"event log must live in the queue clone; expected at {queue_db}"
    event_log = open_log(fake_repo)
    try:
        events = event_log.recent(window=timedelta(hours=1), now=datetime.now(tz=UTC))
    finally:
        event_log.close()
    pbi_opened = [e for e in events if e.kind == EventType.PBI_OPENED]
    assert pbi_opened, "PBI_OPENED event missing from queue-clone event log"


def test_stuck_blocked_move_targets_queue_clone(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``handle_stuck`` must move the PBI inside the queue clone's
    ``.ralph/blocked/`` — where the next push to origin/main carries it
    to the queue repo. The PBI dir for the running iteration lives at
    ``<queue-clone>/.ralph/current/<id>``."""
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim

    pbi_dir_in_queue = fake_repo / ".ralph" / "current" / "WI-1234"

    def _stuck_spawn(cfg: ExecutorConfig, pbi: object, **kwargs: object) -> ClaudeOutcome:
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
    result = iterate_once(cfg_for_repo)

    assert result.outcome == "ran_stuck"
    assert (fake_repo / ".ralph" / "blocked" / "WI-1234").is_dir()
    # Regression for BUG-HANDLE-STUCK-NO-COMMIT: assert the move landed
    # as a git commit, not as stranded D/?? entries in the working tree.
    status = _git(fake_repo, "status", "--porcelain").strip()
    assert status == "", f"working tree must be clean after stuck-move; got: {status!r}"
    # The most recent commit must reflect the move (R for tracked renames,
    # A for the newly-added STUCK.md).
    name_status = _git(fake_repo, "log", "-1", "--name-status", "--format=")
    assert ".ralph/blocked/WI-1234/" in name_status


def test_max_attempts_blocked_move_targets_queue_clone(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The max-attempts blocked move must land the PBI in the queue
    clone's ``.ralph/blocked/`` — otherwise it falls outside git
    tracking and disappears from the queue."""
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "1")
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn("error"))
    iterate_once(cfg_for_repo)  # attempts 0 -> 1 (== limit, allowed)
    result = iterate_once(cfg_for_repo)  # attempts 1 -> 2 (> limit), max-attempts

    assert result.outcome == "ran_stuck"
    assert (fake_repo / ".ralph" / "blocked" / "WI-1234").is_dir(), (
        "max-attempts blocked move must target the queue clone"
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


def test_claim_clones_target_and_creates_worktree_in_clone(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: a claim runs ensure_clone, creates the per-PBI
    worktree INSIDE the target clone, and returns a PBI with target_info +
    work_worktree populated."""
    from ralph_executor.loop import _claim_pbi
    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    cfg = cfg_for_repo
    custom_ws = cfg.workspace_root
    clone_root = custom_ws / "clones" / "test-repo"

    ensure_clone_calls: list[tuple[TargetRepoInfo, Path]] = []

    def _fake_ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        ensure_clone_calls.append((info, workspace_root))
        clone_root.mkdir(parents=True, exist_ok=True)
        # The new ``_claim_pbi_worktree`` pre-flight checks
        # ``origin/<main_branch>`` exists in the clone before any
        # ``move_inbox_to_current``. Materialise the ref so this happy-path
        # test still exercises the worktree branch.
        subprocess.run(
            ["git", "init", "--initial-branch=main", str(clone_root)],
            check=True,
            capture_output=True,
        )
        _git(clone_root, "config", "user.email", "test@example.com")
        _git(clone_root, "config", "user.name", "Test User")
        _git(clone_root, "commit", "--allow-empty", "-m", "chore: initial")
        head_sha = _git(clone_root, "rev-parse", "HEAD").strip()
        _git(clone_root, "update-ref", "refs/remotes/origin/main", head_sha)
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


def test_claim_raises_claim_error_when_clone_unreachable(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure_clone -> TargetUnreachable maps to _ClaimError("target unreachable: ...")."""
    from ralph_executor.loop import _claim_pbi, _ClaimError, _pull_queue
    from ralph_executor.target_clone import TargetUnreachable
    from ralph_executor.url_utils import TargetRepoInfo

    _populate_inbox(fake_repo, pbi_id="WI-NET")
    _pull_queue(cfg_for_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    picked = source.pick_next()
    assert picked is not None

    def _raise_unreachable(info: TargetRepoInfo, workspace_root: Path) -> None:
        raise TargetUnreachable("network unreachable")

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _raise_unreachable)

    with pytest.raises(_ClaimError, match=r"target unreachable: network unreachable"):
        _claim_pbi(cfg_for_repo, picked)


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
    pbi_dir = write_sample_pbi(
        fake_repo,
        pbi_id="WI-ADO",
        target_repo="https://dev.azure.com/myorg/myproj/_git/myrepo",
    )
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", "inbox: WI-ADO")
    _git(fake_repo, "push", "origin", "main")

    result = iterate_once(cfg_for_repo)

    assert result.outcome == "claim_failed"
    assert result.pbi_id == "WI-ADO"

    assert (fake_repo / ".ralph" / "blocked" / "WI-ADO").is_dir()
    assert not (fake_repo / ".ralph" / "inbox" / "WI-ADO").exists()
    history = (fake_repo / ".ralph" / "blocked" / "WI-ADO" / "HISTORY.md").read_text(
        encoding="utf-8"
    )
    assert "Claim failed" in history
    assert "unsupported host" in history


def test_pull_queue_calls_ensure_queue_clone(
    cfg_for_repo: ExecutorConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_pull_queue`` must delegate to ``queue_clone.ensure_queue_clone``."""
    from ralph_executor import loop

    cfg = dataclasses.replace(
        cfg_for_repo,
        workspace_root=tmp_path,
        queue_repo="https://github.com/example/q",
    )
    calls: list[tuple[Path, str, str, str]] = []

    def fake_ensure(
        workspace_root: Path,
        queue_repo: str,
        queue_branch: str,
        *,
        instance_id: str,
        timeout: float = 120.0,
    ) -> Path:
        calls.append((workspace_root, queue_repo, queue_branch, instance_id))
        return workspace_root / f"queue-{instance_id}"

    monkeypatch.setattr(loop, "ensure_queue_clone", fake_ensure)

    loop._pull_queue(cfg)

    assert calls == [
        (tmp_path, "https://github.com/example/q", cfg.queue_branch, cfg.instance_id),
    ]


def test_pull_queue_passes_configured_branch(
    cfg_for_repo: ExecutorConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_pull_queue`` forwards ``cfg.queue_branch`` to ``ensure_queue_clone``."""
    from ralph_executor import loop

    cfg = dataclasses.replace(
        cfg_for_repo,
        workspace_root=tmp_path,
        queue_repo="https://github.com/example/q",
        queue_branch="custom-branch",
    )
    captured: dict[str, object] = {}

    def fake_ensure(
        workspace_root: Path,
        queue_repo: str,
        queue_branch: str,
        *,
        instance_id: str,
        timeout: float = 120.0,
    ) -> Path:
        captured["queue_branch"] = queue_branch
        captured["instance_id"] = instance_id
        return workspace_root / f"queue-{instance_id}"

    monkeypatch.setattr(loop, "ensure_queue_clone", fake_ensure)

    loop._pull_queue(cfg)

    assert captured["queue_branch"] == "custom-branch"
    assert captured["instance_id"] == cfg.instance_id


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


def test_iterate_once_moves_pbi_to_blocked_when_target_unreachable(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure_clone raises TargetUnreachable -> _ClaimError -> iterate_once
    moves PBI to blocked/<id>/ with reason in HISTORY.md."""
    from ralph_executor.target_clone import TargetUnreachable
    from ralph_executor.url_utils import TargetRepoInfo

    _populate_inbox(fake_repo, pbi_id="WI-NET2")

    def _raise_unreachable(info: TargetRepoInfo, workspace_root: Path) -> None:
        raise TargetUnreachable("network unreachable")

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _raise_unreachable)

    result = iterate_once(cfg_for_repo)

    assert result.outcome == "claim_failed"
    assert result.pbi_id == "WI-NET2"

    assert (fake_repo / ".ralph" / "blocked" / "WI-NET2").is_dir()
    assert not (fake_repo / ".ralph" / "inbox" / "WI-NET2").exists()
    history = (fake_repo / ".ralph" / "blocked" / "WI-NET2" / "HISTORY.md").read_text(
        encoding="utf-8"
    )
    assert "Claim failed" in history
    assert "target unreachable: network unreachable" in history


# ----------------------------------------------------------------------
# Regression: empty target repo (no origin/main) must NOT crash the loop
# ----------------------------------------------------------------------


def _init_empty_target_clone(clone_root: Path) -> None:
    """Materialise a non-empty local git repo with NO ``origin/<main>`` ref.

    Used by the empty-target-repo regression tests below. The clone has
    an origin remote registered (so git_ops talks to it cleanly) but no
    ``refs/remotes/origin/main`` ref — simulating a freshly-cloned empty
    GitHub repository.
    """
    clone_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(clone_root)],
        check=True,
        capture_output=True,
    )
    _git(clone_root, "config", "user.email", "test@example.com")
    _git(clone_root, "config", "user.name", "Test User")
    _git(clone_root, "commit", "--allow-empty", "-m", "chore: initial")
    _git(clone_root, "remote", "add", "origin", "file:///nonexistent.git")


def test_iterate_once_moves_pbi_to_blocked_when_target_origin_main_missing(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty target repo (no ``origin/main``) must not crash iterate_once.

    Pre-bug behaviour: ``ensure_worktree`` raised a raw ``GitCommandError``
    AFTER the inbox -> current move had already committed, killing the
    loop and stranding the PBI in ``current/`` with no worktree.

    Fixed behaviour: ``_claim_pbi_worktree`` pre-flights ``origin/<main>``
    before the move, raises ``_ClaimError``, ``iterate_once`` demotes
    the PBI inbox -> blocked with the reason recorded in HISTORY.md,
    and the loop continues to the next iteration.
    """
    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    _populate_inbox(fake_repo, pbi_id="WI-EMPTY")

    empty_clone = tmp_path / "ws" / "clones" / "test" / "repo"
    _init_empty_target_clone(empty_clone)

    def _fake_ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        return TargetClone(info=info, clone_root=empty_clone)

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _fake_ensure_clone)

    result = iterate_once(cfg_for_repo)

    assert result.outcome == "claim_failed"
    assert result.pbi_id == "WI-EMPTY"
    assert (fake_repo / ".ralph" / "blocked" / "WI-EMPTY").is_dir()
    assert not (fake_repo / ".ralph" / "inbox" / "WI-EMPTY").exists()
    assert not (fake_repo / ".ralph" / "current" / "WI-EMPTY").exists()
    history = (fake_repo / ".ralph" / "blocked" / "WI-EMPTY" / "HISTORY.md").read_text(
        encoding="utf-8"
    )
    assert "Claim failed" in history
    assert "origin/main" in history


def test_iterate_once_resume_self_heals_missing_work_worktree(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PBI sitting in ``current/`` with no work worktree must be re-materialised
    on resume rather than handing a non-existent ``cwd`` to ``spawn_claude_p``.

    Simulates a prior iteration's claim that crashed AFTER the inbox -> current
    move but BEFORE ``ensure_worktree`` completed. The resume path's
    ``ensure_worktree`` call must idempotently (re)create the worktree.
    """
    from dataclasses import replace as _replace
    from textwrap import dedent

    # Manually seed a PBI directly in current/ (skipping the claim path)
    # to model "previous claim crashed mid-way".
    pbi_dir = fake_repo / ".ralph" / "current" / "WI-RESUME"
    pbi_dir.mkdir(parents=True, exist_ok=True)
    (pbi_dir / "PBI.md").write_text(
        dedent(
            """\
            ---
            id: WI-RESUME
            type: feature
            status: current
            severity: normal
            attempts: 0
            created_at: 2026-05-24T09:15:00+00:00
            updated_at: 2026-05-24T09:15:00+00:00
            target_repo: https://github.com/test/repo
            ---

            # WI-RESUME body
            """
        ),
        encoding="utf-8",
    )
    (pbi_dir / "PLAN.md").write_text("# PLAN\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", "test: seed WI-RESUME directly in current/")
    _git(fake_repo, "push", "origin", "main")

    # Count ensure_worktree invocations — must run on resume, not just claim.
    from ralph_executor import loop as loop_mod

    real_ensure = loop_mod.ensure_worktree
    calls: list[dict[str, object]] = []

    def _spy(*args: object, **kwargs: object) -> None:
        calls.append({"args": args, "kwargs": kwargs})
        return real_ensure(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(loop_mod, "ensure_worktree", _spy)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )

    result = iterate_once(cfg_for_repo)

    # Resume completed normally, did not crash.
    assert result.outcome == "ran_partial"
    assert result.pbi_id == "WI-RESUME"

    # ensure_worktree was called on the resume path with the right args.
    resume_calls = [c for c in calls if c["kwargs"].get("branch") == "ralph/WI-RESUME"]
    assert resume_calls, f"ensure_worktree not invoked on resume; calls={calls}"
    kw = resume_calls[0]["kwargs"]
    assert kw["create_branch_from"] == "origin/main"
    # The worktree was materialised inside the target clone (the
    # ``_fake_ensure_target_clone`` autouse fixture aliases the target
    # clone to ``fake_repo``).
    work_wt = Path(str(kw["worktree_path"]))
    assert work_wt == fake_repo / ".ralph-work" / "WI-RESUME"
    _ = _replace  # silence unused-import lint when the dedent helper changes


def test_iterate_once_resume_demotes_to_blocked_when_worktree_cannot_be_created(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``ensure_worktree`` raises ``GitCommandError`` on the resume path
    (e.g. ``origin/main`` is STILL missing after a previous mid-claim crash),
    the PBI must be demoted current -> blocked with the reason in HISTORY.md
    — the loop must not crash."""
    from dataclasses import replace as _replace
    from textwrap import dedent

    from ralph_executor.git_ops import GitCommandError
    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    pbi_dir = fake_repo / ".ralph" / "current" / "WI-STRANDED"
    pbi_dir.mkdir(parents=True, exist_ok=True)
    (pbi_dir / "PBI.md").write_text(
        dedent(
            """\
            ---
            id: WI-STRANDED
            type: feature
            status: current
            severity: normal
            attempts: 0
            created_at: 2026-05-24T09:15:00+00:00
            updated_at: 2026-05-24T09:15:00+00:00
            target_repo: https://github.com/test/repo
            ---

            # WI-STRANDED body
            """
        ),
        encoding="utf-8",
    )
    (pbi_dir / "PLAN.md").write_text("# PLAN\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", "test: seed WI-STRANDED in current/")
    _git(fake_repo, "push", "origin", "main")

    # Point ensure_clone at an empty target clone (no origin/main); also
    # stub ensure_worktree to raise GitCommandError to model "origin/main
    # still missing on the retry".
    empty_clone = tmp_path / "ws" / "clones" / "test" / "repo"
    _init_empty_target_clone(empty_clone)

    def _fake_ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        return TargetClone(info=info, clone_root=empty_clone)

    def _fake_ensure_worktree(*args: object, **kwargs: object) -> None:
        raise GitCommandError(
            ["git", "worktree", "add", "-b", "ralph/WI-STRANDED", "<path>", "origin/main"],
            128,
            "fatal: invalid reference: origin/main",
        )

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _fake_ensure_clone)
    monkeypatch.setattr("ralph_executor.loop.ensure_worktree", _fake_ensure_worktree)

    result = iterate_once(cfg_for_repo)

    assert result.outcome == "claim_failed"
    assert result.pbi_id == "WI-STRANDED"
    assert (fake_repo / ".ralph" / "blocked" / "WI-STRANDED").is_dir()
    assert not (fake_repo / ".ralph" / "current" / "WI-STRANDED").exists()
    history = (fake_repo / ".ralph" / "blocked" / "WI-STRANDED" / "HISTORY.md").read_text(
        encoding="utf-8"
    )
    assert "Claim failed" in history
    assert "origin/main" in history
    _ = _replace
