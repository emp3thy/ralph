"""Tests for the TOML override layer in ``ralph_executor.config``."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ralph_executor.config import ConfigError, load_config


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".ralph").mkdir()
    return repo


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> Path:
    """RALPH_REPO_PATH set, every overridable env var removed."""
    monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for var in (
        "RALPH_QUEUE_BRANCH",
        "RALPH_MAIN_BRANCH",
        "RALPH_MAX_ATTEMPTS",
        "RALPH_LOG_LEVEL",
        "RALPH_ITERATION_SLEEP_SECONDS",
        "RALPH_CLAUDE_BINARY",
        "RALPH_GIT_HOST",
    ):
        monkeypatch.delenv(var, raising=False)
    return git_repo


def _write_toml(repo: Path, body: str) -> Path:
    cfg_file = repo / ".ralph" / "config.toml"
    cfg_file.write_text(body, encoding="utf-8")
    return cfg_file


def test_missing_toml_uses_defaults(clean_env: Path) -> None:
    """No config.toml -> behaves exactly like the env-only path."""
    cfg = load_config()
    assert cfg.queue_branch == "ralph-queue"
    assert cfg.max_attempts == 20
    assert cfg.iteration_sleep_seconds == 30.0


def test_toml_overrides_defaults(clean_env: Path) -> None:
    _write_toml(
        clean_env,
        """
        queue_branch = "ops-queue"
        main_branch = "trunk"
        max_attempts = 7
        iteration_sleep_seconds = 1.5
        claude_binary = "/opt/claude/bin/claude"
        log_level = "DEBUG"
        """,
    )
    cfg = load_config()
    assert cfg.queue_branch == "ops-queue"
    assert cfg.main_branch == "trunk"
    assert cfg.max_attempts == 7
    assert cfg.iteration_sleep_seconds == 1.5
    assert cfg.claude_binary == "/opt/claude/bin/claude"
    assert cfg.log_level == logging.DEBUG


def test_env_wins_over_toml(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_toml(clean_env, 'queue_branch = "from-toml"\nmax_attempts = 7\n')
    monkeypatch.setenv("RALPH_QUEUE_BRANCH", "from-env")
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "99")
    cfg = load_config()
    assert cfg.queue_branch == "from-env"
    assert cfg.max_attempts == 99


def test_toml_partial_override_falls_back_to_defaults(clean_env: Path) -> None:
    """Knobs absent from TOML fall through to defaults."""
    _write_toml(clean_env, "max_attempts = 4\n")
    cfg = load_config()
    assert cfg.max_attempts == 4
    assert cfg.queue_branch == "ralph-queue"  # default kept
    assert cfg.iteration_sleep_seconds == 30.0


def test_unknown_toml_key_is_warned_and_ignored(
    clean_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_toml(clean_env, 'queue_branch = "ops"\nmystery_knob = 42\n')
    with caplog.at_level(logging.WARNING, logger="ralph_executor.config"):
        cfg = load_config()
    assert cfg.queue_branch == "ops"
    assert any("mystery_knob" in rec.message for rec in caplog.records)


def test_malformed_toml_raises(clean_env: Path) -> None:
    _write_toml(clean_env, "queue_branch = ===bogus===\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config()


def test_toml_wrong_type_int_raises(clean_env: Path) -> None:
    _write_toml(clean_env, 'max_attempts = "five"\n')
    with pytest.raises(ConfigError, match="max_attempts must be an integer"):
        load_config()


def test_toml_wrong_type_bool_for_int_raises(clean_env: Path) -> None:
    """bool is a subclass of int — TOML ``max_attempts = true`` must NOT
    silently parse as 1."""
    _write_toml(clean_env, "max_attempts = true\n")
    with pytest.raises(ConfigError, match="max_attempts must be an integer"):
        load_config()


def test_toml_wrong_type_string_raises(clean_env: Path) -> None:
    _write_toml(clean_env, "queue_branch = 42\n")
    with pytest.raises(ConfigError, match="queue_branch must be a string"):
        load_config()


def test_toml_git_host_is_picked_up(clean_env: Path) -> None:
    """`git_host = "github"` in TOML flows through to ExecutorConfig.git_host
    so the operator doesn't need $RALPH_GIT_HOST set in the shell."""
    _write_toml(clean_env, 'git_host = "github"\n')
    cfg = load_config()
    assert cfg.git_host == "github"


def test_env_git_host_wins_over_toml(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_toml(clean_env, 'git_host = "github"\n')
    monkeypatch.setenv("RALPH_GIT_HOST", "ado")
    cfg = load_config()
    assert cfg.git_host == "ado"


def test_git_host_empty_when_neither_set(clean_env: Path) -> None:
    """Empty string is the "not set" sentinel — host_select then errors
    pointing at both knobs."""
    cfg = load_config()
    assert cfg.git_host == ""


def test_toml_invalid_log_level_raises(clean_env: Path) -> None:
    _write_toml(clean_env, 'log_level = "VERBOSE"\n')
    with pytest.raises(ConfigError, match="log_level"):
        load_config()


def test_toml_array_of_tables_treated_as_unknown_key(
    clean_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`[[entries]]` produces ``{'entries': [...]}`` — a dict at the top
    level whose ``entries`` key is unknown. Confirms we surface this via
    the unknown-key warning rather than a confusing parse error."""
    _write_toml(clean_env, '[[entries]]\nname = "x"\n')
    with caplog.at_level(logging.WARNING, logger="ralph_executor.config"):
        cfg = load_config()
    assert cfg.queue_branch == "ralph-queue"
    assert any("entries" in rec.message for rec in caplog.records)
