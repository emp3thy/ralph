"""Tests for ``ralph_executor.queue.filesystem``."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import (
    FilesystemQueueSource,
    QueueError,
    parse_pbi_directory,
)
from tests.executor.conftest import write_sample_pbi


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_parse_pbi_directory_reads_feature(fake_repo: Path) -> None:
    _git(fake_repo, "checkout", "ralph-queue")
    pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-1234", pbi_type="feature")
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    assert pbi.id == "WI-1234"
    assert pbi.type == "feature"
    assert pbi.status == "inbox"
    assert pbi.severity == "normal"
    assert pbi.attempts == 0
    assert pbi.path == pbi_dir
    assert pbi.created_at == datetime(2026, 5, 24, 9, 15, tzinfo=UTC)


def test_parse_pbi_directory_reads_bug(fake_repo: Path) -> None:
    _git(fake_repo, "checkout", "ralph-queue")
    pbi_dir = write_sample_pbi(fake_repo, pbi_id="BUG-1", pbi_type="bug", severity="critical")
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    assert pbi.type == "bug"
    assert pbi.severity == "critical"


def test_parse_pbi_directory_reads_pr_feedback(fake_repo: Path) -> None:
    _git(fake_repo, "checkout", "ralph-queue")
    pbi_dir = write_sample_pbi(
        fake_repo,
        pbi_id="PR-feedback-WI-1234-r1",
        pbi_type="pr-feedback",
        severity="high",
    )
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    assert pbi.type == "pr-feedback"
    assert pbi.severity == "high"


def test_parse_pbi_directory_missing_entry_file_raises(
    fake_repo: Path,
) -> None:
    _git(fake_repo, "checkout", "ralph-queue")
    empty = fake_repo / ".ralph" / "inbox" / "NO-FILES"
    empty.mkdir(parents=True)
    with pytest.raises(QueueError, match="no entry file"):
        parse_pbi_directory(empty, status="inbox")


def test_current_pbi_returns_none_when_empty(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    source = FilesystemQueueSource(cfg_for_repo)
    assert source.current_pbi() is None


def test_current_pbi_returns_the_one_entry(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _git(fake_repo, "checkout", "ralph-queue")
    write_sample_pbi(fake_repo, pbi_id="WI-42", where="current")
    _git(fake_repo, "add", ".ralph/current/WI-42")
    _git(fake_repo, "commit", "-m", "current: WI-42")
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.current_pbi()
    assert pbi is not None
    assert pbi.id == "WI-42"
    assert pbi.status == "current"


def test_current_pbi_raises_when_more_than_one(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    _git(fake_repo, "checkout", "ralph-queue")
    write_sample_pbi(fake_repo, pbi_id="WI-1", where="current")
    write_sample_pbi(fake_repo, pbi_id="WI-2", where="current")
    _git(fake_repo, "add", ".ralph/current")
    _git(fake_repo, "commit", "-m", "two in current")
    source = FilesystemQueueSource(cfg_for_repo)
    with pytest.raises(QueueError, match="more than one"):
        source.current_pbi()


def test_inbox_pbis_returns_all_in_priority_order(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    _git(fake_repo, "checkout", "ralph-queue")
    # Low-priority feature, normal feature, critical bug,
    # high pr-feedback. Expected order: pr-feedback, critical,
    # normal feature, low feature.
    write_sample_pbi(
        fake_repo,
        pbi_id="WI-low",
        pbi_type="feature",
        severity="low",
        created_at="2026-05-20T00:00:00+00:00",
    )
    write_sample_pbi(
        fake_repo,
        pbi_id="WI-normal",
        pbi_type="feature",
        severity="normal",
        created_at="2026-05-21T00:00:00+00:00",
    )
    write_sample_pbi(
        fake_repo,
        pbi_id="BUG-crit",
        pbi_type="bug",
        severity="critical",
        created_at="2026-05-22T00:00:00+00:00",
    )
    write_sample_pbi(
        fake_repo,
        pbi_id="PR-feedback-WI-1-r1",
        pbi_type="pr-feedback",
        severity="high",
        created_at="2026-05-23T00:00:00+00:00",
    )
    _git(fake_repo, "add", ".ralph/inbox")
    _git(fake_repo, "commit", "-m", "four pbis")
    source = FilesystemQueueSource(cfg_for_repo)
    pbis = source.inbox_pbis()
    assert [p.id for p in pbis] == [
        "PR-feedback-WI-1-r1",
        "BUG-crit",
        "WI-normal",
        "WI-low",
    ]


def test_pick_next_returns_highest_priority(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _git(fake_repo, "checkout", "ralph-queue")
    write_sample_pbi(
        fake_repo,
        pbi_id="WI-low",
        pbi_type="feature",
        severity="low",
        created_at="2026-05-20T00:00:00+00:00",
    )
    write_sample_pbi(
        fake_repo,
        pbi_id="PR-feedback-WI-1-r1",
        pbi_type="pr-feedback",
        severity="high",
        created_at="2026-05-23T00:00:00+00:00",
    )
    _git(fake_repo, "add", ".ralph/inbox")
    _git(fake_repo, "commit", "-m", "two pbis")
    source = FilesystemQueueSource(cfg_for_repo)
    pick = source.pick_next()
    assert pick is not None
    assert pick.id == "PR-feedback-WI-1-r1"


def test_pick_next_returns_none_when_inbox_empty(
    cfg_for_repo: ExecutorConfig,
) -> None:
    source = FilesystemQueueSource(cfg_for_repo)
    assert source.pick_next() is None


def test_inbox_pbis_age_tiebreak_within_same_lane(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    _git(fake_repo, "checkout", "ralph-queue")
    write_sample_pbi(
        fake_repo,
        pbi_id="WI-younger",
        pbi_type="feature",
        severity="normal",
        created_at="2026-05-22T00:00:00+00:00",
    )
    write_sample_pbi(
        fake_repo,
        pbi_id="WI-older",
        pbi_type="feature",
        severity="normal",
        created_at="2026-05-20T00:00:00+00:00",
    )
    _git(fake_repo, "add", ".ralph/inbox")
    _git(fake_repo, "commit", "-m", "age tiebreak")
    source = FilesystemQueueSource(cfg_for_repo)
    pbis = source.inbox_pbis()
    assert [p.id for p in pbis] == ["WI-older", "WI-younger"]
