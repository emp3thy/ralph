"""Tests for ``ralph_executor.config``."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import pytest

from ralph_executor.config import (
    ConfigError,
    load_config,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


@pytest.fixture
def env_minimal(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> Path:
    monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    for var in (
        "RALPH_QUEUE_BRANCH",
        "RALPH_MAIN_BRANCH",
        "RALPH_MAX_ATTEMPTS",
        "RALPH_LOG_LEVEL",
        "RALPH_ITERATION_SLEEP_SECONDS",
        "RALPH_CLAUDE_BINARY",
        "RALPH_USE_WORKTREES",
    ):
        monkeypatch.delenv(var, raising=False)
    return git_repo


def test_load_config_uses_defaults(env_minimal: Path) -> None:
    cfg = load_config()
    assert cfg.repo_path == env_minimal
    assert cfg.queue_branch == "ralph-queue"
    assert cfg.main_branch == "main"
    assert cfg.max_attempts == 20
    assert cfg.log_level == logging.INFO
    assert cfg.iteration_sleep_seconds == 30.0
    assert cfg.claude_binary == "claude"
    assert cfg.anthropic_api_key == "fake-key"
    assert cfg.use_worktrees is True


def test_load_config_overrides_via_env(monkeypatch: pytest.MonkeyPatch, env_minimal: Path) -> None:
    monkeypatch.setenv("RALPH_QUEUE_BRANCH", "custom-queue")
    monkeypatch.setenv("RALPH_MAIN_BRANCH", "trunk")
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("RALPH_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("RALPH_ITERATION_SLEEP_SECONDS", "0.5")
    monkeypatch.setenv("RALPH_CLAUDE_BINARY", "/usr/local/bin/claude")
    cfg = load_config()
    assert cfg.queue_branch == "custom-queue"
    assert cfg.main_branch == "trunk"
    assert cfg.max_attempts == 5
    assert cfg.log_level == logging.DEBUG
    assert cfg.iteration_sleep_seconds == 0.5
    assert cfg.claude_binary == "/usr/local/bin/claude"


def test_load_config_missing_repo_path_falls_back_to_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RALPH_REPO_PATH unset falls back to the current working directory.

    Validation still runs against cwd, so a cwd that isn't a git repo
    surfaces a clear ConfigError pointing at "cwd" rather than the env var.
    """
    monkeypatch.delenv("RALPH_REPO_PATH", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.chdir(tmp_path)  # tmp_path has no .git
    with pytest.raises(ConfigError, match="from cwd.*not a git repository"):
        load_config()


def test_load_config_uses_cwd_when_repo_path_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Happy path: cwd is a valid git repo and RALPH_REPO_PATH is unset."""
    repo = tmp_path / "in-here"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.delenv("RALPH_REPO_PATH", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.chdir(repo)
    cfg = load_config()
    assert cfg.repo_path == repo.resolve()


def test_load_config_missing_anthropic_key_is_optional(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """ANTHROPIC_API_KEY is optional — claude -p falls back to OAuth.
    Missing key must NOT raise; cfg.anthropic_api_key is the empty
    string and claude_spawn skips propagating it.
    """
    monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = load_config()
    assert cfg.anthropic_api_key == ""


def test_load_config_repo_path_not_a_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "nope"
    monkeypatch.setenv("RALPH_REPO_PATH", str(missing))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with pytest.raises(ConfigError, match="does not exist"):
        load_config()


def test_load_config_repo_path_not_a_git_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("RALPH_REPO_PATH", str(plain))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with pytest.raises(ConfigError, match="not a git repository"):
        load_config()


def test_load_config_invalid_max_attempts(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "not-a-number")
    with pytest.raises(ConfigError, match="RALPH_MAX_ATTEMPTS"):
        load_config()


def test_load_config_invalid_log_level(monkeypatch: pytest.MonkeyPatch, env_minimal: Path) -> None:
    monkeypatch.setenv("RALPH_LOG_LEVEL", "VERBOSE")
    with pytest.raises(ConfigError, match="RALPH_LOG_LEVEL"):
        load_config()


def test_load_config_use_worktrees_env_true(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    monkeypatch.setenv("RALPH_USE_WORKTREES", "true")
    cfg = load_config()
    assert cfg.use_worktrees is True


def test_load_config_use_worktrees_env_false(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    monkeypatch.setenv("RALPH_USE_WORKTREES", "false")
    cfg = load_config()
    assert cfg.use_worktrees is False


def test_load_config_use_worktrees_env_invalid(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    monkeypatch.setenv("RALPH_USE_WORKTREES", "maybe")
    with pytest.raises(ConfigError, match="RALPH_USE_WORKTREES"):
        load_config()


def test_executor_config_is_frozen(env_minimal: Path) -> None:
    cfg = load_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.queue_branch = "other"  # type: ignore[misc]
