"""Tests for ``ralph_executor.user_config``."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.config import ConfigError
from ralph_executor.user_config import (
    read_ralph_home,
    user_config_path,
    write_ralph_home,
)


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Path.home() at a temp dir for the duration of the test."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def test_read_returns_none_when_file_missing(fake_home: Path) -> None:
    assert read_ralph_home() is None


def test_write_then_read_roundtrip(fake_home: Path) -> None:
    target = fake_home / "dev" / "ralph"
    written = write_ralph_home(target)
    assert written == fake_home / ".ralph" / "config.toml"
    assert written.is_file()
    assert read_ralph_home() == target


def test_write_creates_parent_directory(fake_home: Path) -> None:
    assert not (fake_home / ".ralph").exists()
    write_ralph_home(fake_home / "anywhere")
    assert (fake_home / ".ralph").is_dir()


def test_write_overwrites_existing_file(fake_home: Path) -> None:
    write_ralph_home(fake_home / "first")
    write_ralph_home(fake_home / "second")
    assert read_ralph_home() == fake_home / "second"


def test_write_escapes_backslashes_in_windows_paths(fake_home: Path) -> None:
    """Backslash-heavy Windows paths must round-trip cleanly through TOML."""
    target = Path("C:\\dev\\ralph")
    write_ralph_home(target)
    raw = user_config_path().read_text(encoding="utf-8")
    # TOML basic string needs each backslash escaped as ``\\``.
    assert '"C:\\\\dev\\\\ralph"' in raw
    assert read_ralph_home() == target


def test_read_returns_none_when_key_absent(fake_home: Path) -> None:
    cfg = fake_home / ".ralph" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("other_key = 1\n", encoding="utf-8")
    assert read_ralph_home() is None


def test_read_rejects_non_string_ralph_home(fake_home: Path) -> None:
    cfg = fake_home / ".ralph" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("ralph_home = 42\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="ralph_home must be a non-empty string"):
        read_ralph_home()


def test_read_rejects_empty_string_ralph_home(fake_home: Path) -> None:
    cfg = fake_home / ".ralph" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('ralph_home = "   "\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="ralph_home must be a non-empty string"):
        read_ralph_home()


def test_read_raises_on_malformed_toml(fake_home: Path) -> None:
    cfg = fake_home / ".ralph" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("ralph_home = ===bogus===\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        read_ralph_home()


def test_read_expands_user_in_value(fake_home: Path) -> None:
    """Paths persisted as ``~/...`` (the literal tilde) should expand on read."""
    write_ralph_home(Path("~/dev/ralph"))
    assert read_ralph_home() == Path("~/dev/ralph").expanduser()
