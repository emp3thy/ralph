"""T18: ``run_loop_with_autobug`` — top-level autobug wrapper around ``run_loop``.

The wrapper iterates ``run_loop(cfg)``, catches any unhandled exception,
hands it to ``autobug.detect_python_crash``, then re-raises. If autobug
itself crashes, the ORIGINAL exception is still re-raised and an
``AUTOBUG FAILED`` log line records the inner failure.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ralph_executor import autobug, cli
from ralph_executor.config import ExecutorConfig
from tests.executor.test_cli_validate_startup import _minimal_cfg


def _build_cfg() -> ExecutorConfig:
    return _minimal_cfg(bot_email="bot@e.com", autobug_enabled=True)


def test_main_wrapper_calls_autobug_on_unhandled_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[BaseException] = []
    monkeypatch.setattr(
        autobug,
        "detect_python_crash",
        lambda exc, ctx, **kw: captured.append(exc),
    )

    def bad_iter(cfg: ExecutorConfig):  # type: ignore[no-untyped-def]
        yield "first-ok"
        raise RuntimeError("mid-iter crash")

    monkeypatch.setattr(cli, "run_loop", bad_iter)
    cfg = _build_cfg()
    with pytest.raises(RuntimeError, match="mid-iter crash"):
        cli.run_loop_with_autobug(cfg)
    assert captured and isinstance(captured[0], RuntimeError)


def test_main_wrapper_reraises_original_when_autobug_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _boom(exc: BaseException, ctx: object, **kw: object) -> None:
        raise TypeError("autobug self-crash")

    monkeypatch.setattr(autobug, "detect_python_crash", _boom)

    def bad_iter(cfg: ExecutorConfig):  # type: ignore[no-untyped-def]
        if False:
            yield
        raise RuntimeError("original crash")

    monkeypatch.setattr(cli, "run_loop", bad_iter)
    cfg = _build_cfg()
    caplog.set_level(logging.ERROR, logger="ralph_executor")
    with pytest.raises(RuntimeError, match="original crash"):
        cli.run_loop_with_autobug(cfg)
    assert any("AUTOBUG FAILED" in r.getMessage() for r in caplog.records)


def test_main_wrapper_skips_autobug_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _track(exc: BaseException, ctx: object, **kw: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(autobug, "detect_python_crash", _track)

    def bad_iter(cfg: ExecutorConfig):  # type: ignore[no-untyped-def]
        if False:
            yield
        raise RuntimeError("skip-autobug")

    monkeypatch.setattr(cli, "run_loop", bad_iter)
    cfg = _minimal_cfg(bot_email="bot@e.com", autobug_enabled=False)
    with pytest.raises(RuntimeError, match="skip-autobug"):
        cli.run_loop_with_autobug(cfg)
    assert called is False
