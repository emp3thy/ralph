from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ralph_executor.autobug import detect
from ralph_executor.autobug.types import Context


def _ctx(fake_repo: Path, *, env: dict[str, str] | None = None) -> Context:
    return Context(
        queue_root=fake_repo,
        state_dir=fake_repo / ".ralph" / "state",
        env=env or {},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="sha",
        bot_author_email="bot@e.com",
        triggering_pbi_id="WI-247",
        queue_branch="main",
    )


def test_detect_python_crash_emits_when_no_existing(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(fake_repo)
    calls: list[tuple[str, dict[str, Any]]] = []
    from ralph_executor.autobug import emit

    def _capture(**kw: Any) -> str:
        calls.append(("new", kw))
        return "autobug-x-001"

    monkeypatch.setattr(emit, "new", _capture)
    try:
        raise RuntimeError("first")
    except RuntimeError as exc:
        detect.detect_python_crash(
            exc, ctx, target_repo="https://github.com/x/y", severity="critical"
        )
    assert calls and calls[0][0] == "new"


def test_detect_python_crash_skips_when_recursion_guard_tripped(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(fake_repo, env={"RALPH_AUTOBUG_DEPTH": "1"})
    calls: list[dict[str, Any]] = []
    from ralph_executor.autobug import emit

    def _capture(**kw: Any) -> str:
        calls.append(kw)
        return ""

    monkeypatch.setattr(emit, "new", _capture)
    try:
        raise RuntimeError("under recursion")
    except RuntimeError as exc:
        detect.detect_python_crash(
            exc, ctx, target_repo="https://github.com/x/y", severity="critical"
        )
    assert not calls


def test_detect_does_not_propagate_emit_failure(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(fake_repo)
    from ralph_executor.autobug import emit

    def _boom(**kw: Any) -> str:
        raise RuntimeError("emit broke")

    monkeypatch.setattr(emit, "new", _boom)
    try:
        raise RuntimeError("orig")
    except RuntimeError as exc:
        detect.detect_python_crash(
            exc, ctx, target_repo="https://github.com/x/y", severity="critical"
        )


def test_detect_signature_failure_aborts(
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ctx = _ctx(fake_repo)
    from ralph_executor.autobug import emit
    from ralph_executor.autobug import signature as sig_mod

    def _sig_boom(exc: BaseException) -> str:
        raise RuntimeError("sig broke")

    monkeypatch.setattr(sig_mod, "python_crash_signature", _sig_boom)
    calls: list[dict[str, Any]] = []

    def _capture(**kw: Any) -> str:
        calls.append(kw)
        return ""

    monkeypatch.setattr(emit, "new", _capture)
    caplog.set_level(logging.WARNING, logger="ralph_executor.autobug")
    try:
        raise RuntimeError("x")
    except RuntimeError as exc:
        detect.detect_python_crash(
            exc, ctx, target_repo="https://github.com/x/y", severity="critical"
        )
    assert not calls
