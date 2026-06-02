"""Defensive autobug wire around ``loop.iterate_once`` body (T19)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ralph_executor import autobug, loop
from ralph_executor.config import ExecutorConfig


def test_iterate_once_autobug_wires_an_unexpected_crash(
    monkeypatch: pytest.MonkeyPatch,
    fake_repo: Path,
    cfg_for_repo: ExecutorConfig,
) -> None:
    captured: list[tuple[str, str]] = []

    def _fake_detect(exc: BaseException, ctx: Any, **kw: Any) -> None:
        captured.append((type(exc).__name__, str(exc)))

    monkeypatch.setattr(autobug, "detect_python_crash", _fake_detect)

    def _boom(_cfg: ExecutorConfig) -> None:
        raise RuntimeError("pull-queue boom")

    monkeypatch.setattr(loop, "_pull_queue", _boom)

    with pytest.raises(RuntimeError, match="pull-queue boom"):
        loop.iterate_once(cfg_for_repo)

    assert captured == [("RuntimeError", "pull-queue boom")]


def test_iterate_once_does_not_wire_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    fake_repo: Path,
    cfg_for_repo: ExecutorConfig,
) -> None:
    captured: list[BaseException] = []

    def _fake_detect(exc: BaseException, ctx: Any, **kw: Any) -> None:
        captured.append(exc)

    monkeypatch.setattr(autobug, "detect_python_crash", _fake_detect)

    def _interrupt(_cfg: ExecutorConfig) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(loop, "_pull_queue", _interrupt)

    with pytest.raises(KeyboardInterrupt):
        loop.iterate_once(cfg_for_repo)

    assert captured == []


def test_iterate_once_skips_autobug_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    fake_repo: Path,
    cfg_for_repo: ExecutorConfig,
) -> None:
    import dataclasses

    cfg_disabled = dataclasses.replace(cfg_for_repo, autobug_enabled=False)

    captured: list[BaseException] = []

    def _fake_detect(exc: BaseException, ctx: Any, **kw: Any) -> None:
        captured.append(exc)

    monkeypatch.setattr(autobug, "detect_python_crash", _fake_detect)

    def _boom(_cfg: ExecutorConfig) -> None:
        raise RuntimeError("disabled wire")

    monkeypatch.setattr(loop, "_pull_queue", _boom)

    with pytest.raises(RuntimeError, match="disabled wire"):
        loop.iterate_once(cfg_disabled)

    assert captured == []


def test_iterate_once_marks_exception_as_autobug_emitted(
    monkeypatch: pytest.MonkeyPatch,
    fake_repo: Path,
    cfg_for_repo: ExecutorConfig,
) -> None:
    """Regression: iterate_once must mark the re-raised exception with
    ``__autobug_emitted__ = True`` so cli.run_loop_with_autobug skips
    its own detect_python_crash call. Without the flag, the outer
    handler dedup-bumps occurrences from 1 to 2 for a single crash.
    """

    def _fake_detect(exc: BaseException, ctx: Any, **kw: Any) -> None:
        return None

    monkeypatch.setattr(autobug, "detect_python_crash", _fake_detect)

    def _boom(_cfg: ExecutorConfig) -> None:
        raise RuntimeError("double-emit guard")

    monkeypatch.setattr(loop, "_pull_queue", _boom)

    with pytest.raises(RuntimeError, match="double-emit guard") as excinfo:
        loop.iterate_once(cfg_for_repo)

    assert getattr(excinfo.value, "__autobug_emitted__", False) is True, (
        "iterate_once should mark the exception to suppress outer re-emission"
    )
