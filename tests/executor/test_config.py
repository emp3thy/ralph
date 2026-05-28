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


QUEUE_REPO_URL = "https://github.com/example/queue"


def _write_queue_repo_toml(repo: Path) -> None:
    cfg_dir = repo / ".ralph"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "config.toml").write_text(
        f'queue_repo = "{QUEUE_REPO_URL}"\n',
        encoding="utf-8",
    )


@pytest.fixture
def env_minimal(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> Path:
    monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    _write_queue_repo_toml(git_repo)
    for var in (
        "RALPH_MAIN_BRANCH",
        "RALPH_MAX_ATTEMPTS",
        "RALPH_LOG_LEVEL",
        "RALPH_ITERATION_SLEEP_SECONDS",
        "RALPH_CLAUDE_BINARY",
        "RALPH_CLAUDE_PERMISSION_MODE",
        "RALPH_USE_WORKTREES",
        "RALPH_AUTO_MERGE_CLEAN_PRS",
    ):
        monkeypatch.delenv(var, raising=False)
    return git_repo


def test_load_config_uses_defaults(env_minimal: Path) -> None:
    cfg = load_config()
    assert cfg.repo_path == env_minimal
    assert cfg.queue_repo == QUEUE_REPO_URL
    assert cfg.main_branch == "main"
    assert cfg.max_attempts == 20
    assert cfg.log_level == logging.INFO
    assert cfg.iteration_sleep_seconds == 30.0
    assert cfg.claude_binary == "claude"
    assert cfg.claude_permission_mode == "bypassPermissions"
    assert cfg.anthropic_api_key == "fake-key"
    assert cfg.use_worktrees is True


def test_load_config_overrides_via_env(monkeypatch: pytest.MonkeyPatch, env_minimal: Path) -> None:
    monkeypatch.setenv("RALPH_MAIN_BRANCH", "trunk")
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("RALPH_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("RALPH_ITERATION_SLEEP_SECONDS", "0.5")
    monkeypatch.setenv("RALPH_CLAUDE_BINARY", "/usr/local/bin/claude")
    monkeypatch.setenv("RALPH_CLAUDE_PERMISSION_MODE", "acceptEdits")
    cfg = load_config()
    assert cfg.main_branch == "trunk"
    assert cfg.max_attempts == 5
    assert cfg.log_level == logging.DEBUG
    assert cfg.iteration_sleep_seconds == 0.5
    assert cfg.claude_binary == "/usr/local/bin/claude"
    assert cfg.claude_permission_mode == "acceptEdits"


def test_executor_config_has_queue_repo_field() -> None:
    from dataclasses import fields

    from ralph_executor.config import ExecutorConfig

    names = {f.name for f in fields(ExecutorConfig)}
    assert "queue_repo" in names
    assert "queue_branch" in names


def test_load_config_rejects_missing_queue_repo(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """queue_repo is required. Missing TOML key → ConfigError."""
    monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    # Write a TOML that lacks queue_repo.
    (git_repo / ".ralph").mkdir(exist_ok=True)
    (git_repo / ".ralph" / "config.toml").write_text("main_branch = 'main'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="queue_repo"):
        load_config()


def test_load_config_rejects_bad_queue_repo_url(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    (git_repo / ".ralph").mkdir(exist_ok=True)
    (git_repo / ".ralph" / "config.toml").write_text(
        'queue_repo = "ftp://example.com/queue"\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="queue_repo"):
        load_config()


def test_load_config_accepts_queue_repo(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    _write_queue_repo_toml(git_repo)
    cfg = load_config()
    assert cfg.queue_repo == QUEUE_REPO_URL


def test_load_config_invalid_permission_mode(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    """A value outside the claude CLI's documented enum must raise
    ConfigError at load time — not silently flow through and surface as
    a confusing claude subprocess exit-1 later."""
    monkeypatch.setenv("RALPH_CLAUDE_PERMISSION_MODE", "yolo")
    with pytest.raises(ConfigError, match="claude_permission_mode"):
        load_config()


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
    _write_queue_repo_toml(repo)
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
    _write_queue_repo_toml(git_repo)
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


def test_load_config_use_worktrees_env_false_rejected(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    """Legacy single-checkout mode is gone — use_worktrees=False must raise."""
    monkeypatch.setenv("RALPH_USE_WORKTREES", "false")
    with pytest.raises(ConfigError, match="use_worktrees=False is no longer supported"):
        load_config()


def test_load_config_use_worktrees_toml_false_rejected(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """TOML ``use_worktrees = false`` is rejected the same as the env var."""
    monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))
    monkeypatch.delenv("RALPH_USE_WORKTREES", raising=False)
    cfg_dir = git_repo / ".ralph"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(
        'queue_repo = "https://github.com/example/queue"\nuse_worktrees = false\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="use_worktrees=False is no longer supported"):
        load_config()


def test_load_config_use_worktrees_env_invalid(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    monkeypatch.setenv("RALPH_USE_WORKTREES", "maybe")
    with pytest.raises(ConfigError, match="RALPH_USE_WORKTREES"):
        load_config()


def test_load_config_auto_merge_clean_prs_default_false(env_minimal: Path) -> None:
    cfg = load_config()
    assert cfg.auto_merge_clean_prs is False


def test_load_config_auto_merge_clean_prs_env_true(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    monkeypatch.setenv("RALPH_AUTO_MERGE_CLEAN_PRS", "true")
    cfg = load_config()
    assert cfg.auto_merge_clean_prs is True


def test_load_config_auto_merge_clean_prs_env_invalid(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    monkeypatch.setenv("RALPH_AUTO_MERGE_CLEAN_PRS", "maybe")
    with pytest.raises(ConfigError, match="RALPH_AUTO_MERGE_CLEAN_PRS"):
        load_config()


def test_executor_config_is_frozen(env_minimal: Path) -> None:
    cfg = load_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.queue_repo = "other"  # type: ignore[misc]


def test_default_queue_branch_is_ralph_queue(tmp_path, monkeypatch):
    """Default queue_branch is 'ralph-queue' when no TOML / env override."""
    from ralph_executor.config import load_config

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".ralph").mkdir()
    (repo / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/test/queue"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_REPO_PATH", str(repo))
    monkeypatch.delenv("RALPH_QUEUE_BRANCH", raising=False)

    cfg = load_config()
    assert cfg.queue_branch == "ralph-queue"


def test_queue_branch_toml_override(tmp_path, monkeypatch):
    """queue_branch in project TOML overrides the default."""
    from ralph_executor.config import load_config

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".ralph").mkdir()
    (repo / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/test/queue"\n'
        'queue_branch = "custom-branch"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_REPO_PATH", str(repo))
    monkeypatch.delenv("RALPH_QUEUE_BRANCH", raising=False)

    cfg = load_config()
    assert cfg.queue_branch == "custom-branch"


def test_queue_branch_env_override_beats_toml(tmp_path, monkeypatch):
    """RALPH_QUEUE_BRANCH env var overrides TOML."""
    from ralph_executor.config import load_config

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".ralph").mkdir()
    (repo / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/test/queue"\n'
        'queue_branch = "toml-value"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_REPO_PATH", str(repo))
    monkeypatch.setenv("RALPH_QUEUE_BRANCH", "env-value")

    cfg = load_config()
    assert cfg.queue_branch == "env-value"


@pytest.mark.parametrize("bad_value", ["", "   ", "HEAD", "refs/heads/foo"])
def test_queue_branch_rejects_invalid(tmp_path, monkeypatch, bad_value):
    """Empty / HEAD / refs-prefixed branch names raise ConfigError."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".ralph").mkdir()
    (repo / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/test/queue"\n'
        f'queue_branch = "{bad_value}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_REPO_PATH", str(repo))
    monkeypatch.delenv("RALPH_QUEUE_BRANCH", raising=False)

    with pytest.raises(ConfigError, match="queue_branch"):
        load_config()
