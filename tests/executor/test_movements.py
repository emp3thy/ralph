"""Tests for ``ralph_executor.queue.movements``."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue.movements import (
    QueueMovementError,
    move_current_to_blocked,
    move_current_to_pending_pr,
    move_inbox_to_current,
)
from ralph_executor.safety.cycle_detector import (
    SignalKind,
    evaluate_same_file_thrashing,
)
from ralph_executor.safety.events import EventType, open_log
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


def _populate_inbox(fake_repo: Path, pbi_id: str = "WI-1234") -> Path:
    _git(fake_repo, "checkout", "ralph-queue")
    pbi_dir = write_sample_pbi(fake_repo, pbi_id=pbi_id)
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", f"inbox: {pbi_id}")
    _git(fake_repo, "push", "origin", "ralph-queue")
    return pbi_dir


def test_move_inbox_to_current_relocates_directory(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    moved = move_inbox_to_current(cfg_for_repo, pbi)
    assert moved.status == "current"
    assert moved.path == fake_repo / ".ralph" / "current" / "WI-1234"
    assert not (fake_repo / ".ralph" / "inbox" / "WI-1234").exists()
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()


def test_move_inbox_to_current_rewrites_status(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    moved = move_inbox_to_current(cfg_for_repo, pbi)
    entry = (moved.path / "PBI.md").read_text(encoding="utf-8")
    assert "status: current" in entry
    assert "status: inbox" not in entry


def test_move_inbox_to_current_pushes_commit(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _populate_inbox(fake_repo)
    remote_before = _git(fake_repo, "ls-remote", "origin", "ralph-queue").split()[0]
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    move_inbox_to_current(cfg_for_repo, pbi)
    remote_after = _git(fake_repo, "ls-remote", "origin", "ralph-queue").split()[0]
    assert remote_before != remote_after


def test_move_current_to_pending_pr(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    pbi = move_inbox_to_current(cfg_for_repo, pbi)
    moved = move_current_to_pending_pr(cfg_for_repo, pbi)
    assert moved.status == "pending-pr"
    assert moved.path == fake_repo / ".ralph" / "pending-pr" / "WI-1234"
    assert not (fake_repo / ".ralph" / "current" / "WI-1234").exists()
    entry = (moved.path / "PBI.md").read_text(encoding="utf-8")
    assert "status: pending-pr" in entry


def test_move_current_to_blocked(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    pbi = move_inbox_to_current(cfg_for_repo, pbi)
    moved = move_current_to_blocked(cfg_for_repo, pbi)
    assert moved.status == "blocked"
    assert moved.path == fake_repo / ".ralph" / "blocked" / "WI-1234"
    entry = (moved.path / "PBI.md").read_text(encoding="utf-8")
    assert "status: blocked" in entry


def test_move_from_wrong_state_raises(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    with pytest.raises(QueueMovementError, match="must be in current"):
        move_current_to_pending_pr(cfg_for_repo, pbi)


def test_pbi_opened_event_emitted_on_claim(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    now = datetime.now(tz=UTC)
    event_log = open_log(fake_repo)
    try:
        move_inbox_to_current(cfg_for_repo, pbi, event_log=event_log, now=now)
        events = event_log.recent(window=timedelta(hours=1), now=now)
    finally:
        event_log.close()
    opened = [ev for ev in events if ev.kind == EventType.PBI_OPENED]
    assert len(opened) == 1
    assert opened[0].pbi_id == "WI-1234"
    assert opened[0].payload == {}


def test_move_inbox_to_current_emits_no_event_when_event_log_omitted(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    move_inbox_to_current(cfg_for_repo, pbi)
    now = datetime.now(tz=UTC)
    event_log = open_log(fake_repo)
    try:
        events = event_log.recent(window=timedelta(hours=1), now=now)
    finally:
        event_log.close()
    assert [ev for ev in events if ev.kind == EventType.PBI_OPENED] == []


def test_same_file_thrashing_trips_after_ten_distinct_prs_touching_one_file(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """End-to-end wiring: ten PR_CREATED events through ``move_current_to_pending_pr``
    sharing a single file trip ``evaluate_same_file_thrashing``.

    This is the integration counterpart to the pure-function detector tests
    in ``tests/safety/test_cycle_detector.py`` -- it exercises the real
    emission site against the real ``EventLog``.
    """
    target_file = "src/auth/handler.py"
    _git(fake_repo, "checkout", "ralph-queue")
    for i in range(10):
        pbi_dir = write_sample_pbi(fake_repo, pbi_id=f"WI-2{i:03d}")
        _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
        _git(fake_repo, "commit", "-m", f"inbox: WI-2{i:03d}")
    _git(fake_repo, "push", "origin", "ralph-queue")

    now = datetime.now(tz=UTC)
    event_log = open_log(fake_repo)
    try:
        for i in range(10):
            pbi_id = f"WI-2{i:03d}"
            source = FilesystemQueueSource(cfg_for_repo)
            inbox = [p for p in source.inbox_pbis() if p.id == pbi_id]
            assert inbox, f"expected {pbi_id} in inbox before claim"
            claimed = move_inbox_to_current(cfg_for_repo, inbox[0])
            move_current_to_pending_pr(
                cfg_for_repo,
                claimed,
                event_log=event_log,
                pr_url=f"https://example.com/pr/{pbi_id}",
                touched_files=[target_file],
                now=now - timedelta(hours=20 - i * 2),
            )
        events = event_log.recent(window=timedelta(hours=24), now=now)
    finally:
        event_log.close()

    pr_created = [ev for ev in events if ev.kind == EventType.PR_CREATED]
    assert len(pr_created) == 10
    assert {ev.pbi_id for ev in pr_created} == {f"WI-2{i:03d}" for i in range(10)}

    signal = evaluate_same_file_thrashing(events, now)
    assert signal is not None
    assert signal.kind == SignalKind.SAME_FILE_THRASHING
    assert target_file in signal.description


def test_move_inbox_to_current_survives_concurrent_remote_advance(
    cfg_for_repo: ExecutorConfig, fake_repo: Path, tmp_path: Path
) -> None:
    """A concurrent push to origin/ralph-queue must not crash the move.

    Without push_with_rebase the second push is rejected as non-FF and
    GitCommandError propagates. With it, the helper fetches + rebases
    onto the racing commit and the move's push succeeds.
    """
    _populate_inbox(fake_repo)

    # Simulate a concurrent writer: clone the bare remote, add a commit
    # touching an unrelated file on ralph-queue, push it back.
    bare_remote = fake_repo.parent / "remote.git"
    racer = tmp_path / "racer"
    subprocess.run(
        ["git", "clone", "--branch", "ralph-queue", str(bare_remote), str(racer)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(racer), "config", "user.email", "r@x"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(racer), "config", "user.name", "r"],
        check=True,
        capture_output=True,
    )
    (racer / "race.txt").write_text("from-racer\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(racer), "add", "race.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(racer), "commit", "-m", "race: concurrent commit"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(racer), "push", "origin", "ralph-queue"],
        check=True,
        capture_output=True,
    )

    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    moved = move_inbox_to_current(cfg_for_repo, pbi)
    assert moved.status == "current"

    # The racing commit is now part of the queue branch's history.
    log = _git(fake_repo, "log", "--format=%s", "ralph-queue", "-5").splitlines()
    assert "race: concurrent commit" in log
    # And the move's own commit landed on top of (or rebased above) it.
    assert any("move WI-1234 from inbox to current" in line for line in log)


def test_move_uses_branch_from_config(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _populate_inbox(fake_repo)
    # Read the PBI while on ralph-queue (where .ralph/ exists), then
    # switch to main — the move helper must checkout ralph-queue itself.
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    _git(fake_repo, "checkout", "main")
    move_inbox_to_current(cfg_for_repo, pbi)
    branch = _git(fake_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert branch == "ralph-queue"
