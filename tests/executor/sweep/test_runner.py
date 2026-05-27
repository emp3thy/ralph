"""End-to-end (in-temp) tests for the sweep runner.

``run`` is fed:
  - a ``.ralph/`` skeleton populated with pending-pr PBIs (via ``make_pending_pbi``)
  - a shim PR-skill ``scripts/`` directory (via ``fake_ado_pr_skill``)
  - canned PR/thread JSON registered through the ``register_pr`` helper

The tests assert the on-disk effects:
  - folder moves (done/, blocked/, inbox/)
  - feedback PBI directory creation (under inbox/)
  - sidecar updates
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ralph_executor.safety.events import EventLog, EventType, open_log, signature_from_text
from ralph_executor.sweep.runner import SweepConfig, SweepContext, run
from tests.executor.sweep.conftest import register_pr

NOW = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)


def _config() -> SweepConfig:
    return SweepConfig(
        ralph_author_email="ralph-bot@example.com",
        max_attempts=3,
        stale_threshold=timedelta(days=3),
        now=NOW,
    )


def _ctx(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    *,
    event_log: EventLog | None = None,
) -> SweepContext:
    return SweepContext(
        queue_root=queue_root,
        ado_pr_scripts_path=fake_ado_pr_skill,
        config=_config(),
        event_log=event_log,
    )


def _open_event_log(queue_root: Path) -> EventLog:
    """Open an event log rooted at the parent of ``queue_root``.

    ``open_log`` expects the *repo* path (not ``.ralph/``); its
    ``_DB_RELATIVE`` already adds ``.ralph/state/events.db``. The sweep
    fixture's ``queue_root`` IS ``<tmp>/repo/.ralph``, so the matching
    repo path is ``queue_root.parent``.
    """
    return open_log(queue_root.parent)


@pytest.mark.parametrize(
    "wi_id,pr_id,attempts,pr_status,ci_status,expected_landing",
    [
        ("WI-1234", 100, 1, "completed", "succeeded", "done"),
        ("WI-2222", 200, 1, "abandoned", "succeeded", "blocked"),
        ("WI-3333", 300, 1, "active", "failed", "inbox"),
        ("WI-4444", 400, 3, "active", "failed", "blocked"),
    ],
)
def test_terminal_actions_move_pbi(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    make_pending_pbi: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    wi_id: str,
    pr_id: int,
    attempts: int,
    pr_status: str,
    ci_status: str,
    expected_landing: str,
) -> None:
    pbi = make_pending_pbi(wi_id, pr_id=pr_id, attempts=attempts)
    register_pr(monkeypatch, pr_id, status=pr_status, ci_status=ci_status)

    result = run(ctx=_ctx(queue_root, fake_ado_pr_skill))

    assert result.pbis_scanned == 1
    assert not pbi.exists()
    assert (queue_root / expected_landing / wi_id).is_dir()


def test_open_green_no_comments_is_noop(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    make_pending_pbi: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pbi = make_pending_pbi("WI-5555", pr_id=500)
    register_pr(monkeypatch, 500, status="active", ci_status="succeeded")

    run(ctx=_ctx(queue_root, fake_ado_pr_skill))

    assert pbi.exists()
    for landing in ("inbox", "done", "blocked"):
        assert not (queue_root / landing / "WI-5555").exists()


def _thread_with_comment(
    *,
    thread_id: int,
    comment_id: int,
    author_email: str = "reviewer@example.com",
    text: str = "Please rename this variable.",
    posted_at: str = "2026-05-24T09:00:00+00:00",
) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "status": "active",
        "file_path": "src/app.py",
        "line": 42,
        "comments": [
            {
                "comment_id": comment_id,
                "author_email": author_email,
                "posted_at": posted_at,
                "text": text,
                "file_path": "src/app.py",
                "line": 42,
            }
        ],
    }


def test_new_human_comment_generates_feedback_pbi(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    make_pending_pbi: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pbi = make_pending_pbi("WI-6666", pr_id=600)
    register_pr(
        monkeypatch,
        600,
        status="active",
        ci_status="succeeded",
        threads=[_thread_with_comment(thread_id=11, comment_id=101)],
    )

    run(ctx=_ctx(queue_root, fake_ado_pr_skill))

    assert pbi.exists()
    feedback_dir = queue_root / "inbox" / "PR-feedback-WI-6666-r1"
    assert feedback_dir.is_dir()
    assert (feedback_dir / "FEEDBACK.md").is_file()
    assert (feedback_dir / "PR-LINK.md").is_file()
    assert (feedback_dir / "ORIGINAL.md").is_file()
    assert (feedback_dir / "HISTORY.md").is_file()
    sidecar = (pbi / ".ralph-state.json").read_text(encoding="utf-8")
    assert "11:101" in sidecar
    assert "last_feedback_round" in sidecar
    assert '"last_feedback_round": 1' in sidecar


def test_repeat_sweep_does_not_regenerate_same_feedback_pbi(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    make_pending_pbi: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_pending_pbi("WI-7777", pr_id=700)
    threads = [_thread_with_comment(thread_id=11, comment_id=101)]
    register_pr(monkeypatch, 700, status="active", ci_status="succeeded", threads=threads)

    run(ctx=_ctx(queue_root, fake_ado_pr_skill))
    run(ctx=_ctx(queue_root, fake_ado_pr_skill))

    assert (queue_root / "inbox" / "PR-feedback-WI-7777-r1").is_dir()
    assert not (queue_root / "inbox" / "PR-feedback-WI-7777-r2").exists()


def test_second_comment_after_first_round_creates_round_two(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    make_pending_pbi: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_pending_pbi("WI-8888", pr_id=800)
    round_one = [_thread_with_comment(thread_id=11, comment_id=101, text="First comment.")]
    register_pr(monkeypatch, 800, status="active", ci_status="succeeded", threads=round_one)
    run(ctx=_ctx(queue_root, fake_ado_pr_skill))

    round_two = round_one + [
        _thread_with_comment(
            thread_id=12,
            comment_id=201,
            text="Second comment.",
            posted_at="2026-05-24T10:00:00+00:00",
        )
    ]
    register_pr(monkeypatch, 800, status="active", ci_status="succeeded", threads=round_two)
    run(ctx=_ctx(queue_root, fake_ado_pr_skill))

    assert (queue_root / "inbox" / "PR-feedback-WI-8888-r1").is_dir()
    assert (queue_root / "inbox" / "PR-feedback-WI-8888-r2").is_dir()
    r2 = (queue_root / "inbox" / "PR-feedback-WI-8888-r2" / "FEEDBACK.md").read_text(
        encoding="utf-8"
    )
    assert "Second comment" in r2
    assert "First comment" not in r2


def test_ralph_authored_comment_does_not_trigger_feedback(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    make_pending_pbi: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_pending_pbi("WI-9999", pr_id=900)
    register_pr(
        monkeypatch,
        900,
        status="active",
        ci_status="succeeded",
        threads=[
            _thread_with_comment(
                thread_id=11,
                comment_id=101,
                author_email="ralph-bot@example.com",
                text="Fixed; see latest commit.",
            )
        ],
    )

    run(ctx=_ctx(queue_root, fake_ado_pr_skill))

    assert not (queue_root / "inbox" / "PR-feedback-WI-9999-r1").exists()


def test_stale_pr_appends_history(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    make_pending_pbi: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pbi = make_pending_pbi("WI-1010", pr_id=1010)
    register_pr(
        monkeypatch,
        1010,
        status="active",
        ci_status="succeeded",
        last_activity_at="2026-05-19T08:00:00+00:00",  # > 3 days ago
    )

    run(ctx=_ctx(queue_root, fake_ado_pr_skill))

    assert pbi.exists()
    history = (pbi / "HISTORY.md").read_text(encoding="utf-8")
    assert "stale" in history.lower()


def test_sweep_with_empty_pending_returns_zero_scanned(
    queue_root: Path,
    fake_ado_pr_skill: Path,
) -> None:
    result = run(ctx=_ctx(queue_root, fake_ado_pr_skill))
    assert result.pbis_scanned == 0
    assert result.actions == ()


def test_sweep_emits_pr_merged_on_merge_transition(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    make_pending_pbi: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan 19b Task 2: PR_MERGED event fires with pr_url + signature payload."""
    make_pending_pbi("WI-MERGE1", pr_id=1100)
    register_pr(monkeypatch, 1100, status="completed", ci_status="succeeded")

    log_handle = _open_event_log(queue_root)
    try:
        run(ctx=_ctx(queue_root, fake_ado_pr_skill, event_log=log_handle))
        events = log_handle.recent(window=timedelta(hours=1), now=NOW)
    finally:
        log_handle.close()

    merged_events = [e for e in events if e.kind == EventType.PR_MERGED]
    assert len(merged_events) == 1
    pr_url = "https://example/_git/svc/pullrequest/1100"
    payload = merged_events[0].payload
    assert merged_events[0].pbi_id == "WI-MERGE1"
    assert payload["pr_url"] == pr_url
    assert payload["signature"] == signature_from_text(pr_url)
    assert payload["files"] == []


def test_sweep_emits_pbi_closed_after_pr_merged(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    make_pending_pbi: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PBI_CLOSED follows PR_MERGED with matching signature; both fire on
    the single pending-pr → done transition."""
    make_pending_pbi("WI-MERGE2", pr_id=1200)
    register_pr(monkeypatch, 1200, status="completed", ci_status="succeeded")

    log_handle = _open_event_log(queue_root)
    try:
        run(ctx=_ctx(queue_root, fake_ado_pr_skill, event_log=log_handle))
        events = log_handle.recent(window=timedelta(hours=1), now=NOW)
    finally:
        log_handle.close()

    kinds = [e.kind for e in events if e.pbi_id == "WI-MERGE2"]
    assert kinds == [EventType.PR_MERGED, EventType.PBI_CLOSED]

    merged, closed = [e for e in events if e.pbi_id == "WI-MERGE2"]
    expected_sig = signature_from_text("https://example/_git/svc/pullrequest/1200")
    assert merged.payload["signature"] == expected_sig
    assert closed.payload == {"signature": expected_sig}


def test_sweep_does_not_emit_pr_merged_without_event_log(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    make_pending_pbi: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward compatibility: a SweepContext without event_log still
    moves the PBI to done/ without raising."""
    pbi = make_pending_pbi("WI-MERGE3", pr_id=1300)
    register_pr(monkeypatch, 1300, status="completed", ci_status="succeeded")

    run(ctx=_ctx(queue_root, fake_ado_pr_skill))

    assert not pbi.exists()
    assert (queue_root / "done" / "WI-MERGE3").is_dir()


def test_sweep_does_not_emit_events_on_non_merge_paths(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    make_pending_pbi: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOVE_TO_BLOCKED_ABANDONED and MOVE_TO_INBOX_RETRY must NOT emit
    PR_MERGED / PBI_CLOSED (only pending-pr → done does)."""
    make_pending_pbi("WI-ABANDON", pr_id=1400)
    register_pr(monkeypatch, 1400, status="abandoned", ci_status="succeeded")
    make_pending_pbi("WI-RETRY", pr_id=1500)
    register_pr(monkeypatch, 1500, status="active", ci_status="failed")

    log_handle = _open_event_log(queue_root)
    try:
        run(ctx=_ctx(queue_root, fake_ado_pr_skill, event_log=log_handle))
        events = log_handle.recent(window=timedelta(hours=1), now=NOW)
    finally:
        log_handle.close()

    assert all(e.kind not in {EventType.PR_MERGED, EventType.PBI_CLOSED} for e in events)


def test_run_reconciles_orphan_when_pr_link_missing(
    queue_root: Path,
    fake_ado_pr_skill: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sweep no longer surfaces missing PR-LINK.md as a sweep error;
    instead it calls reconcile_orphan and continues."""
    from ralph_executor.sweep.types import ReconcileAction

    pbi_dir = queue_root / "pending-pr" / "WI-ORPHAN"
    pbi_dir.mkdir()
    (pbi_dir / "PBI.md").write_text(
        "---\nid: WI-ORPHAN\ntype: feature\nstatus: pending-pr\n"
        "severity: normal\nattempts: 1\n"
        "created_at: 2026-05-20T08:00:00+00:00\n"
        "updated_at: 2026-05-20T08:00:00+00:00\n---\n",
        encoding="utf-8",
    )

    captured: list[Path] = []

    def _fake_reconcile(pbi: Path, ctx: SweepContext, **kwargs: Any) -> ReconcileAction:
        del ctx, kwargs
        captured.append(pbi)
        return ReconcileAction.MOVED_TO_INBOX

    monkeypatch.setattr(
        "ralph_executor.sweep.reconcile.reconcile_orphan",
        _fake_reconcile,
    )

    result = run(ctx=_ctx(queue_root, fake_ado_pr_skill))
    assert result.errors == ()
    assert captured == [pbi_dir]
    assert all(r.pbi_id != "WI-ORPHAN" for r in result.actions)
