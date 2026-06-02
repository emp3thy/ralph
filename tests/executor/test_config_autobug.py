"""Tests for autobug TOML keys + ExecutorConfig defaults (T14).

Adapts the v3 plan-prescribed tests to the post-multi-ralph schema:
- ``ExecutorConfig`` no longer has ``repo_path``
- ``instance_id`` is required
- ``load_config`` reads ``~/.ralph/config.toml`` (user-scope) — not
  ``<repo>/.ralph/config.toml`` — so the TOML-read test seeds via the
  same ``HOME`` / ``USERPROFILE`` monkeypatch that ``test_config.py``
  uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.config import ExecutorConfig, load_config

QUEUE_REPO_URL = "https://github.com/example/queue"


def _seed_user_config_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cfg_dir = tmp_path / ".ralph"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "config.toml"
    cfg_file.write_text(body, encoding="utf-8")
    return cfg_file


def test_executor_config_has_autobug_defaults() -> None:
    """Constructing ExecutorConfig with no autobug-* kwargs must succeed
    and surface the documented defaults.
    """
    cfg = ExecutorConfig(
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
    )
    assert cfg.autobug_enabled is True
    assert cfg.autobug_rate_max == 5
    assert cfg.autobug_rate_window_minutes == 10
    assert cfg.autobug_dedup_done_window_days == 30
    assert cfg.autobug_severity_python_crash == "critical"
    assert cfg.autobug_severity_subprocess_crash == "high"


def test_load_config_reads_autobug_keys_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    body = (
        f"queue_repo = '{QUEUE_REPO_URL}'\n"
        f"workspace_root = '{(ws_parent / 'ws').as_posix()}'\n"
        "autobug_enabled = false\n"
        "autobug_rate_max = 10\n"
        "autobug_rate_window_minutes = 30\n"
        "autobug_dedup_done_window_days = 60\n"
        "autobug_severity_python_crash = 'high'\n"
        "autobug_severity_subprocess_crash = 'normal'\n"
    )
    _seed_user_config_toml(tmp_path, monkeypatch, body)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    for var in (
        "RALPH_AUTOBUG_ENABLED",
        "RALPH_AUTOBUG_RATE_MAX",
        "RALPH_AUTOBUG_RATE_WINDOW_MINUTES",
        "RALPH_AUTOBUG_DEDUP_DONE_WINDOW_DAYS",
        "RALPH_AUTOBUG_SEVERITY_PYTHON_CRASH",
        "RALPH_AUTOBUG_SEVERITY_SUBPROCESS_CRASH",
        "RALPH_WORKSPACE",
        "RALPH_QUEUE_BRANCH",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.autobug_enabled is False
    assert cfg.autobug_rate_max == 10
    assert cfg.autobug_rate_window_minutes == 30
    assert cfg.autobug_dedup_done_window_days == 60
    assert cfg.autobug_severity_python_crash == "high"
    assert cfg.autobug_severity_subprocess_crash == "normal"


def test_load_config_autobug_env_overrides_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    body = (
        f"queue_repo = '{QUEUE_REPO_URL}'\n"
        f"workspace_root = '{(ws_parent / 'ws').as_posix()}'\n"
        "autobug_rate_max = 5\n"
    )
    _seed_user_config_toml(tmp_path, monkeypatch, body)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("RALPH_AUTOBUG_RATE_MAX", "20")
    monkeypatch.setenv("RALPH_AUTOBUG_ENABLED", "0")
    cfg = load_config()
    assert cfg.autobug_rate_max == 20
    assert cfg.autobug_enabled is False
