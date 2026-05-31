"""Tests for the one-time stale-`ralph_home` warning in user_config."""
from pathlib import Path

import pytest

from ralph_executor.user_config import _warn_stale_ralph_home_in_user_config


def _seed_user_toml(home: Path, body: str) -> Path:
    cfg = home / ".ralph" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body, encoding="utf-8")
    return cfg


def test_warns_when_ralph_home_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _seed_user_toml(tmp_path, 'ralph_home = "/x"\nworkspace_root = "/y"\n')

    with caplog.at_level("WARNING", logger="ralph_executor.user_config"):
        _warn_stale_ralph_home_in_user_config()

    messages = [r.getMessage() for r in caplog.records]
    assert any("ralph_home" in m and "no longer supported" in m for m in messages)
    assert any(str(tmp_path / ".ralph" / "config.toml") in m for m in messages)


def test_no_warning_when_ralph_home_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _seed_user_toml(tmp_path, 'workspace_root = "/y"\n')

    with caplog.at_level("WARNING", logger="ralph_executor.user_config"):
        _warn_stale_ralph_home_in_user_config()

    assert caplog.records == []


def test_no_warning_when_user_toml_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # No file written.

    with caplog.at_level("WARNING", logger="ralph_executor.user_config"):
        _warn_stale_ralph_home_in_user_config()

    assert caplog.records == []
