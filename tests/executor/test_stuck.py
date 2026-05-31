"""Git-backed tests for ``ralph_executor.safety.stuck``.

These exercise the move_to_blocked / handle_stuck path now that the
folder relocation is committed + pushed via
``queue.movements.move_current_to_blocked`` (``git mv`` + frontmatter
rewrite + commit + ``push_with_rebase``) instead of a bare
``shutil.move``. Each test asserts the queue clone's working tree is
clean after the move so the bug captured by BUG-HANDLE-STUCK-NO-COMMIT
(the move stranded as ``D``/``??`` entries) does not regress.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue.movements import move_inbox_to_current
from ralph_executor.safety.cycle_detector import (
    SignalKind,
    evaluate_signature_recurrence,
)
from ralph_executor.safety.events import (
    Event,
    EventType,
    open_log,
    signature_from_text,
)
from ralph_executor.safety.stuck import (
    StuckOutcome,
    handle_stuck,
    move_to_blocked,
)
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


def _seed_current_pbi(cfg: ExecutorConfig, fake_repo: Path, pbi_id: str = "WI-1234"):
    """Push an inbox PBI, then claim it into current/ via the real movement."""
    pbi_dir = write_sample_pbi(fake_repo, pbi_id=pbi_id)
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", f"inbox: {pbi_id}")
    _git(fake_repo, "push", "origin", "main")
    source = FilesystemQueueSource(cfg)
    pbi = source.inbox_pbis()[0]
    return move_inbox_to_current(cfg, pbi)


def test_move_to_blocked_commits_rename_and_leaves_clean_tree(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """``move_to_blocked`` must land the move in HEAD, not the working tree.

    Regression for BUG-HANDLE-STUCK-NO-COMMIT: the prior ``shutil.move``
    body left the queue clone showing ``D current/<id>/*`` and
    ``?? blocked/<id>/`` for days because no git op ran.
    """
    pbi = _seed_current_pbi(cfg_for_repo, fake_repo)
    # Simulate Claude having written STUCK.md before exit.
    (pbi.path / "STUCK.md").write_text("blocking: dependency missing\n", encoding="utf-8")

    blocked_path = move_to_blocked(cfg=cfg_for_repo, pbi=pbi, reason="blocking: dependency missing")

    assert blocked_path == fake_repo / ".ralph" / "blocked" / "WI-1234"
    assert blocked_path.is_dir()
    assert not (fake_repo / ".ralph" / "current" / "WI-1234").exists()
    # Working tree clean — the move is in HEAD, not stranded as
    # D/?? entries.
    status = _git(fake_repo, "status", "--porcelain").strip()
    assert status == "", f"working tree must be clean after move; got: {status!r}"
    # HEAD's tree carries the new path; the old path is gone.
    head_files = _git(fake_repo, "ls-tree", "-r", "--name-only", "HEAD")
    assert "blocked/WI-1234/PBI.md" in head_files
    assert "blocked/WI-1234/STUCK.md" in head_files
    assert "current/WI-1234/PBI.md" not in head_files
    # HISTORY.md in HEAD carries the stuck reason recorded BEFORE the move.
    history = (blocked_path / "HISTORY.md").read_text(encoding="utf-8")
    assert "STUCK" in history
    assert "dependency missing" in history


def test_move_to_blocked_pushes_rename_to_origin(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """The move commit must reach origin/<queue-branch> so the next
    iteration's ``git pull`` sees a consistent queue state."""
    pbi = _seed_current_pbi(cfg_for_repo, fake_repo)
    (pbi.path / "STUCK.md").write_text("blocking\n", encoding="utf-8")
    remote_before = _git(fake_repo, "ls-remote", "origin", "main").split()[0]

    move_to_blocked(cfg=cfg_for_repo, pbi=pbi, reason="blocking")

    remote_after = _git(fake_repo, "ls-remote", "origin", "main").split()[0]
    assert remote_before != remote_after, "move must push a new commit to origin"


def test_move_to_blocked_raises_when_pbi_not_in_current(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """A PBI dataclass referencing a missing current/ dir raises ValueError
    before any git op runs."""
    from dataclasses import replace

    pbi = _seed_current_pbi(cfg_for_repo, fake_repo)
    # Remove the directory to simulate a stale dataclass.
    import shutil

    shutil.rmtree(pbi.path)
    with pytest.raises(ValueError, match="not in .ralph/current/"):
        move_to_blocked(
            cfg=cfg_for_repo,
            pbi=replace(pbi),
            reason="x",
        )


def test_handle_stuck_returns_outcome_with_event(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    pbi = _seed_current_pbi(cfg_for_repo, fake_repo)
    (pbi.path / "STUCK.md").write_text("blocking: dependency missing\n", encoding="utf-8")
    now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)

    outcome = handle_stuck(cfg=cfg_for_repo, pbi=pbi, now=now)

    assert isinstance(outcome, StuckOutcome)
    assert outcome.blocked_path == fake_repo / ".ralph" / "blocked" / "WI-1234"
    assert outcome.reason.startswith("blocking: dependency missing")
    assert outcome.event.pbi_id == "WI-1234"
    assert outcome.event.kind == EventType.PBI_BLOCKED
    assert outcome.event.recorded_at == now
    # Working tree clean (no stranded D/?? entries).
    assert _git(fake_repo, "status", "--porcelain").strip() == ""


def test_handle_stuck_returns_none_when_no_stuck_file(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    pbi = _seed_current_pbi(cfg_for_repo, fake_repo)
    now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)

    outcome = handle_stuck(cfg=cfg_for_repo, pbi=pbi, now=now)

    assert outcome is None
    # The PBI is still in current/ — no move happened.
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()


def test_handle_stuck_emits_signature_observed_event(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """The SIGNATURE_OBSERVED event must land in the event log so the
    cycle detector's ``signature_recurrence`` rule can spot repeating
    blockers across PBIs."""
    pbi = _seed_current_pbi(cfg_for_repo, fake_repo)
    reason_body = "blocking: dependency missing\n"
    (pbi.path / "STUCK.md").write_text(reason_body, encoding="utf-8")
    now = datetime(2026, 5, 27, 0, 0, 0, tzinfo=UTC)

    event_log = open_log(fake_repo)
    try:
        outcome = handle_stuck(cfg=cfg_for_repo, pbi=pbi, now=now, event_log=event_log)
        assert outcome is not None
        events = event_log.recent(window=timedelta(minutes=1), now=now)
    finally:
        event_log.close()

    signatures = [e for e in events if e.kind == EventType.SIGNATURE_OBSERVED]
    assert len(signatures) == 1
    assert signatures[0].pbi_id == "WI-1234"
    assert signatures[0].recorded_at == now
    assert signatures[0].payload == {
        "signature": signature_from_text(reason_body.strip()),
    }


def test_signature_recurrence_trips_after_pbi_closed_then_signature_reobserved(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """End-to-end wiring: a sweep-emitted PBI_CLOSED followed by a
    handle_stuck-emitted SIGNATURE_OBSERVED with the same hash trips the
    ``evaluate_signature_recurrence`` detector."""
    reason_body = "blocking: dependency cli missing\n"
    expected_sig = signature_from_text(reason_body.strip())
    now = datetime(2026, 5, 27, 0, 0, 0, tzinfo=UTC)

    event_log = open_log(fake_repo)
    try:
        event_log.append(
            Event(
                kind=EventType.PBI_CLOSED,
                recorded_at=now - timedelta(hours=22),
                pbi_id="WI-old",
                payload={"signature": expected_sig},
            )
        )
    finally:
        event_log.close()

    pbi = _seed_current_pbi(cfg_for_repo, fake_repo, pbi_id="WI-new")
    (pbi.path / "STUCK.md").write_text(reason_body, encoding="utf-8")

    event_log = open_log(fake_repo)
    try:
        outcome = handle_stuck(cfg=cfg_for_repo, pbi=pbi, now=now, event_log=event_log)
        assert outcome is not None
        events = event_log.recent(window=timedelta(hours=24), now=now)
    finally:
        event_log.close()

    observed = [
        ev for ev in events if ev.kind == EventType.SIGNATURE_OBSERVED and ev.pbi_id == "WI-new"
    ]
    assert len(observed) == 1
    assert observed[0].payload == {"signature": expected_sig}

    signal = evaluate_signature_recurrence(events, now)
    assert signal is not None
    assert signal.kind == SignalKind.SIGNATURE_RECURRENCE
    assert expected_sig in signal.description


def test_handle_stuck_emits_no_signature_when_event_log_omitted(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    pbi = _seed_current_pbi(cfg_for_repo, fake_repo)
    (pbi.path / "STUCK.md").write_text("stuck\n", encoding="utf-8")
    now = datetime(2026, 5, 27, 0, 0, 0, tzinfo=UTC)

    outcome = handle_stuck(cfg=cfg_for_repo, pbi=pbi, now=now)

    assert outcome is not None
    assert outcome.event.kind == EventType.PBI_BLOCKED
