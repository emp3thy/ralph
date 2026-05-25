"""Executor configuration loaded from environment variables.

All runtime knobs live on the immutable ``ExecutorConfig`` dataclass so
the loop driver and the queue source can be tested with hand-rolled
config objects without any environment dependency.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

DEFAULT_QUEUE_BRANCH = "ralph-queue"
DEFAULT_MAIN_BRANCH = "main"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_ITERATION_SLEEP_SECONDS = 30.0
DEFAULT_CLAUDE_BINARY = "claude"

_VALID_LOG_LEVEL_NAMES = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)


class ConfigError(RuntimeError):
    """Raised when ``load_config`` cannot resolve a valid configuration."""


@dataclass(frozen=True)
class ExecutorConfig:
    """Immutable runtime configuration for the executor.

    Created once at process start and passed through every public entry
    point of the loop driver, queue source, and claude-spawn helpers.
    """

    repo_path: Path
    queue_branch: str
    main_branch: str
    max_attempts: int
    log_level: int
    iteration_sleep_seconds: float
    claude_binary: str
    anthropic_api_key: str


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"environment variable {name} is required"
        )
    return value


def _parse_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{name}={raw!r} is not an integer: {exc}"
        ) from exc


def _parse_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{name}={raw!r} is not a float: {exc}"
        ) from exc


def _parse_log_level(name: str, default: str) -> int:
    raw = (os.environ.get(name) or default).strip().upper()
    if raw not in _VALID_LOG_LEVEL_NAMES:
        raise ConfigError(
            f"{name}={raw!r} not in {sorted(_VALID_LOG_LEVEL_NAMES)}"
        )
    return cast(int, logging.getLevelName(raw))


def _validate_repo_path(path: Path) -> Path:
    if not path.exists():
        raise ConfigError(f"RALPH_REPO_PATH={path} not a directory: does not exist")
    if not path.is_dir():
        raise ConfigError(f"RALPH_REPO_PATH={path} not a directory")
    if not (path / ".git").exists():
        raise ConfigError(
            f"RALPH_REPO_PATH={path} not a git repository (no .git/ entry)"
        )
    return path.resolve()


def load_config() -> ExecutorConfig:
    """Read environment variables and produce a validated ``ExecutorConfig``."""
    repo_raw = _require_env("RALPH_REPO_PATH")
    anthropic_key = _require_env("ANTHROPIC_API_KEY")
    repo_path = _validate_repo_path(Path(repo_raw))
    queue_branch = os.environ.get("RALPH_QUEUE_BRANCH") or DEFAULT_QUEUE_BRANCH
    main_branch = os.environ.get("RALPH_MAIN_BRANCH") or DEFAULT_MAIN_BRANCH
    max_attempts = _parse_int("RALPH_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)
    log_level = _parse_log_level("RALPH_LOG_LEVEL", DEFAULT_LOG_LEVEL)
    sleep_seconds = _parse_float(
        "RALPH_ITERATION_SLEEP_SECONDS", DEFAULT_ITERATION_SLEEP_SECONDS
    )
    claude_binary = (
        os.environ.get("RALPH_CLAUDE_BINARY") or DEFAULT_CLAUDE_BINARY
    )
    return ExecutorConfig(
        repo_path=repo_path,
        queue_branch=queue_branch,
        main_branch=main_branch,
        max_attempts=max_attempts,
        log_level=log_level,
        iteration_sleep_seconds=sleep_seconds,
        claude_binary=claude_binary,
        anthropic_api_key=anthropic_key,
    )
