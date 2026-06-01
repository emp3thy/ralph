import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.autobug.compose import _safe, _section, build_frontmatter
from ralph_executor.autobug.types import Context


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


def _ctx(tmp_path: Path) -> Context:
    return Context(
        queue_root=tmp_path / "queue",
        state_dir=tmp_path / "queue" / ".ralph" / "state",
        env={},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="51cc97a",
        bot_author_email="bot@example.com",
        triggering_pbi_id="WI-247",
        queue_branch="ralph-queue",
    )


def test_build_frontmatter_includes_required_fields(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    fm = build_frontmatter(
        pbi_id="autobug-a3f2c9-001",
        signature="a3f2c9d8" + "0" * 56,
        trigger_kind="python_crash",
        severity="critical",
        ctx=ctx,
        target_repo="https://github.com/emp3thy/ralph",
    )
    assert "id: autobug-a3f2c9-001" in fm
    assert "type: bug" in fm
    assert "severity: critical" in fm
    assert "status: inbox" in fm
    assert "attempts: 0" in fm
    assert "created_at: 2026-05-31T14:23:01+00:00" in fm
    assert "updated_at: 2026-05-31T14:23:01+00:00" in fm
    assert "target_repo: https://github.com/emp3thy/ralph" in fm
    assert "signature: a3f2c9d8" + "0" * 56 in fm
    assert "trigger_kind: python_crash" in fm
    assert "occurrences: 1" in fm
    assert "first_seen: 2026-05-31T14:23:01+00:00" in fm
    assert "last_seen: 2026-05-31T14:23:01+00:00" in fm
    assert "triggering_pbi: WI-247" in fm
    assert "ralph_sha: 51cc97a" in fm
    assert fm.startswith("---\n")
    assert fm.endswith("---\n")


def test_build_frontmatter_regression_of_optional(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    fm = build_frontmatter(
        pbi_id="autobug-a3f2c9-002",
        signature="a" * 64,
        trigger_kind="python_crash",
        severity="critical",
        ctx=ctx,
        target_repo="https://github.com/emp3thy/ralph",
        regression_of="autobug-a3f2c9-001",
    )
    assert "regression_of: autobug-a3f2c9-001" in fm
