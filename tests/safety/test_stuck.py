"""Tests for STUCK.md detection + folder mutation (Layer 1 self-halt)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.safety.events import EventLog, EventType, signature_from_text
from ralph_executor.safety.stuck import (
    StuckOutcome,
    detect_stuck,
    handle_stuck,
    move_to_blocked,
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


def test_move_to_blocked_relocates_directory(repo_dir: Path) -> None:
    pbi_dir = write_pbi_dir(
        repo_dir,
        bucket="current",
        pbi_id="WI-7",
        extra_files={"STUCK.md": "blocking: ambiguous acceptance criteria\n"},
    )
    new_path = move_to_blocked(
        repo=repo_dir,
        pbi_dir=pbi_dir,
        reason="blocking: ambiguous acceptance criteria",
    )
    assert new_path == repo_dir / ".ralph" / "blocked" / "WI-7"
    assert new_path.is_dir()
    assert not pbi_dir.exists()
    history = (new_path / "HISTORY.md").read_text(encoding="utf-8")
    assert "STUCK" in history
    assert "ambiguous acceptance criteria" in history


def test_move_to_blocked_refuses_outside_current(repo_dir: Path) -> None:
    pbi_dir = write_pbi_dir(repo_dir, bucket="inbox", pbi_id="WI-9")
    with pytest.raises(ValueError, match="must be under .ralph/current/"):
        move_to_blocked(repo=repo_dir, pbi_dir=pbi_dir, reason="x")


def test_move_to_blocked_refuses_collision(repo_dir: Path) -> None:
    pbi_dir = write_pbi_dir(
        repo_dir,
        bucket="current",
        pbi_id="WI-1",
        extra_files={"STUCK.md": "stuck\n"},
    )
    # Pre-create the collision target.
    write_pbi_dir(repo_dir, bucket="blocked", pbi_id="WI-1")
    with pytest.raises(FileExistsError):
        move_to_blocked(repo=repo_dir, pbi_dir=pbi_dir, reason="stuck")


def test_handle_stuck_returns_outcome_with_event(
    repo_dir: Path,
) -> None:
    pbi_dir = write_pbi_dir(
        repo_dir,
        bucket="current",
        pbi_id="WI-3",
        extra_files={"STUCK.md": "blocking: dependency missing\n"},
    )
    now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
    outcome = handle_stuck(repo=repo_dir, pbi_dir=pbi_dir, now=now)
    assert isinstance(outcome, StuckOutcome)
    assert outcome.blocked_path == repo_dir / ".ralph" / "blocked" / "WI-3"
    assert outcome.reason.startswith("blocking: dependency missing")
    assert outcome.event.pbi_id == "WI-3"
    assert outcome.event.kind.value == "pbi.blocked"
    assert outcome.event.recorded_at == now


def test_handle_stuck_returns_none_when_no_stuck_file(
    repo_dir: Path,
) -> None:
    pbi_dir = write_pbi_dir(repo_dir, bucket="current", pbi_id="WI-3")
    now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
    outcome = handle_stuck(repo=repo_dir, pbi_dir=pbi_dir, now=now)
    assert outcome is None


def test_signature_observed_event_emitted_on_stuck(
    repo_dir: Path,
    event_log: EventLog,
) -> None:
    reason_body = "blocking: dependency missing\n"
    pbi_dir = write_pbi_dir(
        repo_dir,
        bucket="current",
        pbi_id="WI-9",
        extra_files={"STUCK.md": reason_body},
    )
    now = datetime(2026, 5, 27, 0, 0, 0, tzinfo=UTC)
    outcome = handle_stuck(repo=repo_dir, pbi_dir=pbi_dir, now=now, event_log=event_log)
    assert outcome is not None
    recent = event_log.recent(window=timedelta(minutes=1), now=now)
    signatures = [e for e in recent if e.kind == EventType.SIGNATURE_OBSERVED]
    assert len(signatures) == 1
    assert signatures[0].pbi_id == "WI-9"
    assert signatures[0].recorded_at == now
    assert signatures[0].payload == {
        "signature": signature_from_text(reason_body.strip()),
    }


def test_handle_stuck_emits_no_signature_when_event_log_omitted(
    repo_dir: Path,
) -> None:
    pbi_dir = write_pbi_dir(
        repo_dir,
        bucket="current",
        pbi_id="WI-10",
        extra_files={"STUCK.md": "stuck\n"},
    )
    now = datetime(2026, 5, 27, 0, 0, 0, tzinfo=UTC)
    outcome = handle_stuck(repo=repo_dir, pbi_dir=pbi_dir, now=now)
    assert outcome is not None
    assert outcome.event.kind == EventType.PBI_BLOCKED
