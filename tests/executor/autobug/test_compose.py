import logging

import pytest

from ralph_executor.autobug.compose import _safe, _section


def test_safe_returns_value_when_fn_succeeds() -> None:
    assert _safe(lambda: "ok", "fallback") == "ok"


def test_safe_returns_fallback_when_fn_raises(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="ralph_executor.autobug.compose")
    out = _safe(lambda: 1 / 0, "fallback")
    assert out == "fallback"
    assert any("autobug.compose section failed" in r.getMessage() for r in caplog.records)


def test_section_renders_heading_and_body() -> None:
    out = _section("Stacktrace", "boom")
    assert "## Stacktrace" in out
    assert "boom" in out
