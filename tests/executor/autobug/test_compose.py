import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.autobug.compose import (
    _safe,
    _section,
    build_bug_md,
    build_frontmatter,
    build_reproduce_md,
)
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


def test_build_bug_md_contains_all_sections(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    try:
        raise RuntimeError("test boom")
    except RuntimeError as exc:
        body = build_bug_md(exc, ctx)
    assert "## Stacktrace" in body
    assert "## Environment" in body
    assert "## Triggering PBI" in body
    assert "RuntimeError" in body
    assert "test boom" in body


def test_build_bug_md_survives_broken_env(tmp_path: Path) -> None:
    """Composer must not raise when env-snapshot section fails."""
    ctx = Context(
        queue_root=tmp_path,
        state_dir=tmp_path / "state",
        env={},
        now=datetime(2026, 5, 31, tzinfo=UTC),
        ralph_sha="sha",
        bot_author_email="b@e.com",
        triggering_pbi_id=None,
        queue_branch="ralph-queue",
    )
    try:
        raise RuntimeError("ok")
    except RuntimeError as exc:
        body = build_bug_md(exc, ctx)
    assert "## Stacktrace" in body


def test_build_reproduce_md_marks_starting_point(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        body = build_reproduce_md(
            "autobug-abc-001",
            trigger_kind="python_crash",
            exc=exc,
            stderr=None,
            exit_code=None,
            ctx=ctx,
        )
    assert "AUTOBUG NOTE" in body
    assert "starting point" in body.lower()
    assert "python_crash" in body


def test_build_reproduce_md_subprocess_includes_exit_code(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    body = build_reproduce_md(
        "autobug-abc-002",
        trigger_kind="subprocess_crash",
        exc=None,
        stderr="Killed",
        exit_code=137,
        ctx=ctx,
    )
    assert "Exit code: 137" in body
    assert "Killed" in body
