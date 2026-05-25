"""Tests for the halt-and-acknowledge mechanism."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import responses

from ralph_executor.safety.cycle_detector import CycleSignal, SignalKind
from ralph_executor.safety.events import EventType
from ralph_executor.safety.halt import (
    HaltedError,
    HaltStatus,
    MetaBug,
    StateSnapshot,
    check_halt_sentinel,
    halt_and_acknowledge,
    notify_halt,
    snapshot_state,
    write_halt_sentinel,
    write_meta_bug,
)
from tests.safety.conftest import make_event, write_pbi_dir


def _signal(kind: SignalKind = SignalKind.SIGNATURE_RECURRENCE) -> CycleSignal:
    return CycleSignal(
        kind=kind,
        description=f"{kind.value} fired",
        supporting_events=(
            make_event(
                kind=EventType.SIGNATURE_OBSERVED,
                recorded_at=datetime(2026, 5, 24, tzinfo=UTC),
            ),
        ),
    )


def test_snapshot_state_collects_queue_contents(repo_dir: Path) -> None:
    write_pbi_dir(repo_dir, bucket="inbox", pbi_id="WI-1")
    write_pbi_dir(repo_dir, bucket="current", pbi_id="WI-2")
    write_pbi_dir(repo_dir, bucket="blocked", pbi_id="WI-3")

    snapshot = snapshot_state(repo_dir)
    assert isinstance(snapshot, StateSnapshot)
    assert "WI-1" in snapshot.inbox
    assert snapshot.current == ("WI-2",)
    assert "WI-3" in snapshot.blocked


def test_write_meta_bug_emits_frontmatter_and_body(repo_dir: Path) -> None:
    snapshot = StateSnapshot(
        repo_path=repo_dir,
        inbox=("WI-1",),
        current=("WI-2",),
        pending_pr=(),
        done=(),
        blocked=("WI-3",),
        taken_at=datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC),
    )
    signals = [_signal(), _signal(SignalKind.WHACK_A_MOLE)]
    meta = write_meta_bug(repo=repo_dir, snapshot=snapshot, signals=signals)
    assert isinstance(meta, MetaBug)
    assert meta.path.is_file()
    assert meta.path.parent == repo_dir / ".ralph" / "blocked"
    text = meta.path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert f"id: {meta.id}" in text
    assert "type: meta" in text
    assert "severity: critical" in text
    assert "signature_recurrence" in text
    assert "whack_a_mole" in text


def test_write_halt_sentinel_records_meta_bug_id(repo_dir: Path) -> None:
    path = write_halt_sentinel(repo=repo_dir, meta_bug_id="META-cycle-1")
    assert path == repo_dir / ".ralph" / "state" / "halted"
    content = path.read_text(encoding="utf-8")
    assert "meta_bug_id: META-cycle-1" in content
    assert "acknowledged_by:" in content
    assert "acknowledged_at:" in content


def test_check_halt_sentinel_running_when_absent(repo_dir: Path) -> None:
    assert check_halt_sentinel(repo_dir) == HaltStatus.RUNNING


def test_check_halt_sentinel_halted_when_not_acknowledged(repo_dir: Path) -> None:
    write_halt_sentinel(repo=repo_dir, meta_bug_id="META-cycle-2")
    assert check_halt_sentinel(repo_dir) == HaltStatus.HALTED


def test_check_halt_sentinel_acknowledged_when_filled_in(repo_dir: Path) -> None:
    sentinel = write_halt_sentinel(repo=repo_dir, meta_bug_id="META-cycle-3")
    text = sentinel.read_text(encoding="utf-8")
    text = text.replace("acknowledged_by:", "acknowledged_by: gethin@example.com").replace(
        "acknowledged_at:", "acknowledged_at: 2026-05-25T08:00:00+00:00"
    )
    sentinel.write_text(text, encoding="utf-8")
    assert check_halt_sentinel(repo_dir) == HaltStatus.ACKNOWLEDGED


@responses.activate
def test_notify_halt_posts_to_webhook_when_url_provided(
    repo_dir: Path,
) -> None:
    url = "https://example.com/hooks/ralph"
    responses.add(responses.POST, url, status=200, json={"ok": True})
    meta = MetaBug(
        id="META-cycle-4",
        path=repo_dir / ".ralph" / "blocked" / "META-cycle-4.md",
        severity="critical",
        summary="signature recurrence + whack_a_mole",
        signals=(_signal(), _signal(SignalKind.WHACK_A_MOLE)),
        created_at=datetime(2026, 5, 24, tzinfo=UTC),
    )
    notify_halt(meta, webhook_url=url)
    assert len(responses.calls) == 1
    body = json.loads(responses.calls[0].request.body or "{}")
    assert body["meta_bug_id"] == "META-cycle-4"
    assert body["signals"]
    assert body["severity"] == "critical"


def test_notify_halt_logs_to_stderr_when_no_webhook(
    repo_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    meta = MetaBug(
        id="META-cycle-5",
        path=repo_dir / ".ralph" / "blocked" / "META-cycle-5.md",
        severity="critical",
        summary="x",
        signals=(_signal(),),
        created_at=datetime(2026, 5, 24, tzinfo=UTC),
    )
    notify_halt(meta, webhook_url=None)
    err = capsys.readouterr().err
    assert "META-cycle-5" in err
    assert "RALPH HALT" in err


def test_halt_and_acknowledge_writes_meta_bug_and_sentinel(
    repo_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RALPH_HALT_WEBHOOK", raising=False)
    write_pbi_dir(repo_dir, bucket="current", pbi_id="WI-active")
    meta = halt_and_acknowledge(
        repo=repo_dir, signals=[_signal()], now=datetime(2026, 5, 24, tzinfo=UTC)
    )
    assert meta.path.is_file()
    assert check_halt_sentinel(repo_dir) == HaltStatus.HALTED
    # current/ contents are preserved — the spec says we DO NOT kick the
    # PBI back to inbox.
    assert (repo_dir / ".ralph" / "current" / "WI-active").is_dir()


def test_halted_error_carries_meta_bug_path(repo_dir: Path) -> None:
    with pytest.raises(HaltedError) as exc_info:
        raise HaltedError(
            meta_bug_id="META-x",
            meta_bug_path=repo_dir / ".ralph" / "blocked" / "META-x.md",
            sentinel_path=repo_dir / ".ralph" / "state" / "halted",
        )
    assert exc_info.value.meta_bug_id == "META-x"
    assert "META-x.md" in str(exc_info.value)
