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
from ralph_executor.safety.events import EventType, open_log
from tests.executor.conftest import write_sample_pbi


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
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
