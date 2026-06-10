from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.autobug import emit as emit_mod
from ralph_executor.autobug.emit import bump, new, reopen
from ralph_executor.autobug.types import Context


def _ctx_for_repo(fake_repo: Path) -> Context:
    return Context(
        queue_root=fake_repo,
        state_dir=fake_repo / ".ralph" / "state",
        env={},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="51cc97a",
        bot_author_email="bot@example.com",
        triggering_pbi_id="WI-247",
        queue_branch="main",
    )


def test_emit_new_writes_pbi_dir_with_bug_md(fake_repo: Path) -> None:
    ctx = _ctx_for_repo(fake_repo)
    try:
        raise RuntimeError("emit-new boom")
    except RuntimeError as exc:
        pbi_id = new(
            signature="a" * 64,
            exc=exc,
            ctx=ctx,
            trigger_kind="python_crash",
            severity="critical",
            target_repo="https://github.com/emp3thy/ralph",
        )
    assert pbi_id.startswith("autobug-")
    pbi_dir = fake_repo / ".ralph" / "inbox" / pbi_id
    assert pbi_dir.is_dir()
    assert (pbi_dir / "BUG.md").is_file()
    assert (pbi_dir / "REPRODUCE.md").is_file()
    assert (pbi_dir / "HISTORY.md").is_file()
    bug_text = (pbi_dir / "BUG.md").read_text(encoding="utf-8")
    assert "signature: " + "a" * 64 in bug_text
    assert "type: bug" in bug_text


def test_emit_bump_increments_occurrences_and_appends_trace(fake_repo: Path) -> None:
    ctx = _ctx_for_repo(fake_repo)
    try:
        raise RuntimeError("first")
    except RuntimeError as exc:
        pbi_id = new(
            signature="b" * 64,
            exc=exc,
            ctx=ctx,
            trigger_kind="python_crash",
            severity="critical",
            target_repo="https://github.com/emp3thy/ralph",
        )
    try:
        raise RuntimeError("second")
    except RuntimeError as exc:
        bump(pbi_id, exc, ctx)
    bug_text = (fake_repo / ".ralph" / "inbox" / pbi_id / "BUG.md").read_text(encoding="utf-8")
    assert "occurrences: 2" in bug_text
    assert "## Trace 2" in bug_text


def test_emit_reopen_creates_new_pbi_with_regression_of(fake_repo: Path) -> None:
    ctx = _ctx_for_repo(fake_repo)
    try:
        raise RuntimeError("first")
    except RuntimeError as exc:
        new_pbi_id = reopen(
            existing_pbi_id="autobug-cccccc-001",
            exc=exc,
            signature="c" * 64,
            ctx=ctx,
            trigger_kind="python_crash",
            severity="critical",
            target_repo="https://github.com/emp3thy/ralph",
        )
    assert new_pbi_id.startswith("autobug-cccccc-")
    assert new_pbi_id != "autobug-cccccc-001"
    bug_text = (fake_repo / ".ralph" / "inbox" / new_pbi_id / "BUG.md").read_text(encoding="utf-8")
    assert "regression_of: autobug-cccccc-001" in bug_text


def test_emit_new_rolls_back_pbi_dir_when_commit_fails(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without atomic emit, a commit/push failure strands the PBI dir on disk
    # and the iterate_once loop spins forever on UncommittedSource.
    ctx = _ctx_for_repo(fake_repo)

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("simulated commit_and_push failure")

    monkeypatch.setattr(emit_mod, "_commit_and_push", boom)
    try:
        raise RuntimeError("emit-new boom")
    except RuntimeError as exc:
        with pytest.raises(RuntimeError, match="simulated commit_and_push failure"):
            new(
                signature="d" * 64,
                exc=exc,
                ctx=ctx,
                trigger_kind="python_crash",
                severity="critical",
                target_repo="https://github.com/emp3thy/ralph",
            )
    inbox = fake_repo / ".ralph" / "inbox"
    leftover = [child for child in inbox.iterdir() if child.name.startswith("autobug-dddddd-")]
    assert leftover == [], f"orphaned inbox dirs: {leftover}"


def test_emit_reopen_rolls_back_pbi_dir_when_commit_fails(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx_for_repo(fake_repo)

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("simulated commit_and_push failure")

    monkeypatch.setattr(emit_mod, "_commit_and_push", boom)
    try:
        raise RuntimeError("regression boom")
    except RuntimeError as exc:
        with pytest.raises(RuntimeError, match="simulated commit_and_push failure"):
            reopen(
                existing_pbi_id="autobug-eeeeee-001",
                exc=exc,
                signature="e" * 64,
                ctx=ctx,
                trigger_kind="python_crash",
                severity="critical",
                target_repo="https://github.com/emp3thy/ralph",
            )
    inbox = fake_repo / ".ralph" / "inbox"
    leftover = [child for child in inbox.iterdir() if child.name.startswith("autobug-eeeeee-")]
    assert leftover == [], f"orphaned regression dirs: {leftover}"


def test_emit_bump_restores_bug_md_when_commit_fails(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx_for_repo(fake_repo)
    try:
        raise RuntimeError("first")
    except RuntimeError as exc:
        pbi_id = new(
            signature="f" * 64,
            exc=exc,
            ctx=ctx,
            trigger_kind="python_crash",
            severity="critical",
            target_repo="https://github.com/emp3thy/ralph",
        )
    bug_path = fake_repo / ".ralph" / "inbox" / pbi_id / "BUG.md"
    original = bug_path.read_bytes()

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("simulated commit_and_push failure")

    monkeypatch.setattr(emit_mod, "_commit_and_push", boom)
    try:
        raise RuntimeError("second")
    except RuntimeError as exc:
        with pytest.raises(RuntimeError, match="simulated commit_and_push failure"):
            bump(pbi_id, exc, ctx)
    assert bug_path.read_bytes() == original, "bump must restore BUG.md on failure"
