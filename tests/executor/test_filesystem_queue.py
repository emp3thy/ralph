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
    pbi_dir = write_sample_pbi(fake_repo, pbi_id="BUG-1", pbi_type="bug", severity="critical")
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    assert pbi.type == "bug"
    assert pbi.severity == "critical"


def test_parse_pbi_directory_reads_pr_feedback(fake_repo: Path) -> None:
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
    empty = fake_repo / ".ralph" / "inbox" / "NO-FILES"
    empty.mkdir(parents=True)
    with pytest.raises(QueueError, match="no entry file"):
        parse_pbi_directory(empty, status="inbox")


def test_current_pbi_returns_none_when_empty(cfg_for_repo: ExecutorConfig) -> None:
    source = FilesystemQueueSource(cfg_for_repo)
    assert source.current_pbi() is None


def test_current_pbi_returns_the_one_entry(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
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


# ----------------------------------------------------------------------
# depends_on field — eligibility filter on pick_next
# ----------------------------------------------------------------------


def _write_pbi_with_depends_on(
    repo: Path,
    *,
    pbi_id: str,
    depends_on: list[str] | None,
    where: str = "inbox",
) -> Path:
    """Write a minimal feature PBI with an explicit depends_on field."""
    pbi_dir = repo / ".ralph" / where / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        "---",
        f"id: {pbi_id}",
        "type: feature",
        f"status: {where}",
        "severity: normal",
        "attempts: 0",
        "created_at: 2026-05-25T00:00:00+00:00",
        "updated_at: 2026-05-25T00:00:00+00:00",
    ]
    if depends_on is not None:
        if depends_on:
            fm_lines.append("depends_on: [" + ", ".join(f'"{d}"' for d in depends_on) + "]")
        else:
            fm_lines.append("depends_on: []")
    fm_lines.extend(["---", "", f"# {pbi_id}", ""])
    (pbi_dir / "PBI.md").write_text("\n".join(fm_lines), encoding="utf-8")
    (pbi_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    return pbi_dir


def test_parse_pbi_directory_defaults_depends_on_to_empty_tuple(
    fake_repo: Path,
) -> None:
    """A PBI with no depends_on field gets the empty tuple — backward-compatible."""
    pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-no-deps")
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    assert pbi.depends_on == ()


def test_parse_pbi_directory_reads_depends_on_list(fake_repo: Path) -> None:
    pbi_dir = _write_pbi_with_depends_on(
        fake_repo,
        pbi_id="WI-child",
        depends_on=["WI-parent-1", "WI-parent-2"],
    )
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    assert pbi.depends_on == ("WI-parent-1", "WI-parent-2")


def test_parse_pbi_directory_rejects_depends_on_with_non_string(
    fake_repo: Path,
) -> None:
    pbi_dir = fake_repo / ".ralph" / "inbox" / "WI-bad-deps"
    pbi_dir.mkdir(parents=True, exist_ok=True)
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        "id: WI-bad-deps\n"
        "type: feature\n"
        "status: inbox\n"
        "severity: normal\n"
        "attempts: 0\n"
        "created_at: 2026-05-25T00:00:00+00:00\n"
        "updated_at: 2026-05-25T00:00:00+00:00\n"
        "depends_on: [42, WI-parent]\n"  # 42 is an int, not a string
        "---\n\n# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    with pytest.raises(QueueError, match="depends_on must be a list of PBI-id strings"):
        parse_pbi_directory(pbi_dir, status="inbox")


def test_pick_next_skips_pbi_with_unsatisfied_dep(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """A PBI whose depends_on names an unsatisfied dep is skipped in favour
    of a sibling whose deps are all done (or empty)."""
    _write_pbi_with_depends_on(
        fake_repo,
        pbi_id="WI-blocked",
        depends_on=["WI-parent"],
    )
    _write_pbi_with_depends_on(
        fake_repo,
        pbi_id="WI-ready",
        depends_on=[],
    )
    _git(fake_repo, "add", ".ralph/inbox")
    _git(fake_repo, "commit", "-m", "deps test")
    source = FilesystemQueueSource(cfg_for_repo)
    picked = source.pick_next()
    assert picked is not None
    assert picked.id == "WI-ready"


def test_pick_next_returns_pbi_when_dep_is_in_done(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """Once a dep is in done/, the dependent PBI becomes eligible."""
    _write_pbi_with_depends_on(
        fake_repo,
        pbi_id="WI-child",
        depends_on=["WI-parent"],
    )
    _write_pbi_with_depends_on(
        fake_repo,
        pbi_id="WI-parent",
        depends_on=[],
        where="done",
    )
    _git(fake_repo, "add", ".ralph")
    _git(fake_repo, "commit", "-m", "parent in done")
    source = FilesystemQueueSource(cfg_for_repo)
    picked = source.pick_next()
    assert picked is not None
    assert picked.id == "WI-child"


def test_pick_next_returns_none_when_all_inbox_blocked_by_deps(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """If every inbox PBI has an unsatisfied dep, pick_next returns None."""
    _write_pbi_with_depends_on(fake_repo, pbi_id="WI-a", depends_on=["MISSING-1"])
    _write_pbi_with_depends_on(fake_repo, pbi_id="WI-b", depends_on=["MISSING-2"])
    _git(fake_repo, "add", ".ralph/inbox")
    _git(fake_repo, "commit", "-m", "all blocked")
    source = FilesystemQueueSource(cfg_for_repo)
    assert source.pick_next() is None
