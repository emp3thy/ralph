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


def test_main_wrapper_skips_autobug_for_halted_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HaltedError is the intentional safety halt — autobug must NOT file it
    as a python_crash, otherwise every cycle-detector trip emits a fresh
    autobug PBI that itself contributes to blocked_growth on the next sweep,
    re-tripping the detector in a closed loop.
    """
    from ralph_executor.safety.halt import HaltedError

    called = 0

    def _track(exc: BaseException, ctx: object, **kw: object) -> None:
        nonlocal called
        called += 1

    monkeypatch.setattr(autobug, "detect_python_crash", _track)

    def halting_iter(cfg: ExecutorConfig):  # type: ignore[no-untyped-def]
        if False:
            yield
        raise HaltedError(
            meta_bug_id="META-cycle-TEST",
            meta_bug_path=tmp_path / "META.md",
            sentinel_path=tmp_path / "halted",
        )

    monkeypatch.setattr(cli, "run_loop", halting_iter)
    cfg = _build_cfg()
    # Wrapper should exit cleanly (return 0), NOT re-raise HaltedError, and
    # NOT call autobug. Same shape as the KeyboardInterrupt branch.
    rc = cli.run_loop_with_autobug(cfg)
    assert rc == 0, f"wrapper must exit cleanly on halt; got rc={rc}"
    assert called == 0, f"autobug must not fire for HaltedError; got {called} calls"


def test_main_wrapper_skips_autobug_when_already_emitted_by_inner_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: iterate_once marks exceptions it already routed through
    autobug with ``__autobug_emitted__ = True``. The outer wrapper must
    honour that flag or every iteration crash dedup-bumps to occurrences=2.
    """
    called = 0

    def _track(exc: BaseException, ctx: object, **kw: object) -> None:
        nonlocal called
        called += 1

    monkeypatch.setattr(autobug, "detect_python_crash", _track)

    def bad_iter(cfg: ExecutorConfig):  # type: ignore[no-untyped-def]
        if False:
            yield
        exc = RuntimeError("already-emitted")
        exc.__autobug_emitted__ = True  # type: ignore[attr-defined]
        raise exc

    monkeypatch.setattr(cli, "run_loop", bad_iter)
    cfg = _build_cfg()
    with pytest.raises(RuntimeError, match="already-emitted"):
        cli.run_loop_with_autobug(cfg)
    assert called == 0, (
        f"outer wrapper must not re-emit when iterate_once already did; got {called} calls"
    )
