"""Startup validation: warn once when bot_author_email is missing (T15).

Plan-prescribed fixture used ``repo_path=Path("/tmp")``; post-multi-ralph
``ExecutorConfig`` has no ``repo_path`` field and requires ``instance_id``.
Adapted to match the current schema (same pattern T14 used).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ralph_executor import cli
from ralph_executor.config import ExecutorConfig


def _minimal_cfg(*, bot_email: str = "", autobug_enabled: bool = True) -> ExecutorConfig:
    return ExecutorConfig(
        queue_repo="https://github.com/x/y",
        queue_branch="ralph-queue",
        main_branch="main",
        max_attempts=3,
        log_level=20,
        iteration_sleep_seconds=0.0,
        claude_binary="claude",
        claude_permission_mode="bypassPermissions",
        anthropic_api_key="",
        git_host="github",
        gh_owner="",
        ado_org_url="",
        ado_project="",
        halt_webhook="",
        pr_check_poll_max_attempts=6,
        pr_check_poll_interval_seconds=30.0,
        instance_id="test-autobug",
        bot_author_email=bot_email,
        autobug_enabled=autobug_enabled,
    )


def test_validate_startup_warns_when_bot_email_missing_and_autobug_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = _minimal_cfg(bot_email="", autobug_enabled=True)
    caplog.set_level(logging.WARNING, logger="ralph_executor")
    cli.validate_startup(cfg)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("bot_author_email" in m for m in msgs)


def test_validate_startup_silent_when_bot_email_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = _minimal_cfg(bot_email="bot@e.com", autobug_enabled=True)
    caplog.set_level(logging.WARNING, logger="ralph_executor")
    cli.validate_startup(cfg)
    assert not any("bot_author_email" in r.getMessage() for r in caplog.records)


def test_validate_startup_silent_when_autobug_off_and_no_sweep(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _minimal_cfg(bot_email="", autobug_enabled=False)
    # Patch the binding cli.py imported via ``from ralph_executor.iteration import
    # _pr_skill_scripts_path`` — patching loop_mod won't reach the local
    # reference in cli.
    monkeypatch.setattr(cli, "_pr_skill_scripts_path", lambda c: tmp_path / "nonexistent")
    caplog.set_level(logging.WARNING, logger="ralph_executor")
    cli.validate_startup(cfg)
    assert not any("bot_author_email" in r.getMessage() for r in caplog.records)
