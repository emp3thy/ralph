"""Tests for STUCK.md detection (Layer 1 self-halt — pure helpers).

The folder-mutation paths (``move_to_blocked`` and ``handle_stuck``) now
route through ``queue.movements.move_current_to_blocked`` and therefore
need a real git-backed queue clone. Those tests live in
``tests/executor/test_stuck.py`` next to the ``fake_repo`` fixture; only
the pure pathless helpers are covered here.
"""

from __future__ import annotations

from pathlib import Path

from ralph_executor.safety.stuck import (
    detect_stuck,
    read_stuck_reason,
)
from tests.safety.conftest import write_pbi_dir


def test_detect_stuck_returns_true_when_file_has_content(repo_dir: Path) -> None:
    pbi_dir = write_pbi_dir(
        repo_dir,
        bucket="current",
        pbi_id="WI-1",
        extra_files={"STUCK.md": "what I tried: read INVESTIGATE.md, nothing helped\n"},
    )
    assert detect_stuck(pbi_dir) is True


def test_detect_stuck_returns_false_when_file_is_missing(repo_dir: Path) -> None:
    pbi_dir = write_pbi_dir(repo_dir, bucket="current", pbi_id="WI-1")
    assert detect_stuck(pbi_dir) is False


def test_detect_stuck_returns_false_when_file_is_blank(repo_dir: Path) -> None:
    pbi_dir = write_pbi_dir(
        repo_dir,
        bucket="current",
        pbi_id="WI-1",
        extra_files={"STUCK.md": "   \n\n   \n"},
    )
    assert detect_stuck(pbi_dir) is False


def test_read_stuck_reason_returns_trimmed_content(repo_dir: Path) -> None:
    body = "blocking: ADO PAT lacks workitem read scope\n"
    pbi_dir = write_pbi_dir(
        repo_dir,
        bucket="current",
        pbi_id="WI-1",
        extra_files={"STUCK.md": body},
    )
    assert read_stuck_reason(pbi_dir) == body.strip()


def test_read_stuck_reason_truncates_overlong_content(repo_dir: Path) -> None:
    huge = "x" * 5000
    pbi_dir = write_pbi_dir(
        repo_dir,
        bucket="current",
        pbi_id="WI-1",
        extra_files={"STUCK.md": huge},
    )
    reason = read_stuck_reason(pbi_dir)
    assert len(reason) <= 2048
    assert reason.endswith("...[truncated]")
