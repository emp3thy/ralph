"""Executor configuration loaded from defaults < TOML < env.

All runtime knobs live on the immutable ``ExecutorConfig`` dataclass so
the loop driver and the queue source can be tested with hand-rolled
config objects without any environment dependency.

Precedence (lowest → highest):

  1. Hard-coded defaults (``DEFAULT_*`` constants).
  2. ``<repo>/.ralph/config.toml`` (optional, checked-in).
  3. ``RALPH_*`` environment variables.
  4. CLI flags applied by ``ralph_executor.cli._apply_overrides``.

Two values are intentionally NOT readable from TOML:

* ``repo_path`` — chicken-and-egg (we need it to find the TOML file).
* ``anthropic_api_key`` — secret; env-only by policy.
"""

from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

# tomllib.load returns dict[str, Any] for any parseable TOML document.

DEFAULT_QUEUE_BRANCH = "ralph-queue"
DEFAULT_MAIN_BRANCH = "main"
# Counts only FAILED iterations (stuck / error) — partial is multi-step
# progress and doesn't decrement the budget. 20 gives a long plan plenty
# of room to surface a genuinely stuck loop without false-tripping.
DEFAULT_MAX_ATTEMPTS = 20
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_ITERATION_SLEEP_SECONDS = 30.0
DEFAULT_CLAUDE_BINARY = "claude"

_VALID_LOG_LEVEL_NAMES = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

# Keys recognised in the TOML config file. Any other top-level key is
# logged as a warning and ignored — keeps forward compatibility cheap.
_TOML_KNOWN_KEYS = frozenset(
    {
        "queue_branch",
        "main_branch",
        "max_attempts",
        "log_level",
        "iteration_sleep_seconds",
        "claude_binary",
    }
)

log = logging.getLogger(__name__)


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
        raise ConfigError(f"environment variable {name} is required")
    return value


def _validate_repo_path(path: Path) -> Path:
    if not path.exists():
        raise ConfigError(f"RALPH_REPO_PATH={path} not a directory: does not exist")
    if not path.is_dir():
        raise ConfigError(f"RALPH_REPO_PATH={path} not a directory")
    if not (path / ".git").exists():
        raise ConfigError(f"RALPH_REPO_PATH={path} not a git repository (no .git/ entry)")
    return path.resolve()


def _load_toml_overrides(repo_path: Path) -> Mapping[str, Any]:
    """Read ``<repo>/.ralph/config.toml`` and return a dict of overrides.

    Missing file is a no-op (returns empty mapping). Malformed TOML or
    a non-table top level raises ``ConfigError`` so the operator notices
    a typo rather than silently falling back to defaults. Unknown
    top-level keys are logged at WARNING and dropped.
    """
    cfg_file = repo_path / ".ralph" / "config.toml"
    if not cfg_file.is_file():
        return {}
    try:
        with cfg_file.open("rb") as fh:
            # tomllib.load always returns a dict for any parseable TOML
            # document; invalid TOML raises TOMLDecodeError above.
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{cfg_file}: invalid TOML: {exc}") from exc
    unknown = sorted(k for k in data if k not in _TOML_KNOWN_KEYS)
    for key in unknown:
        log.warning("%s: unknown key %r (ignored)", cfg_file, key)
    return {k: v for k, v in data.items() if k in _TOML_KNOWN_KEYS}


def _resolve_str(
    *, name: str, env_name: str, toml_value: Any, default: str, source_label: str
) -> str:
    """env > toml > default for a string-valued knob."""
    env_value = os.environ.get(env_name)
    if env_value and env_value.strip():
        return env_value
    if toml_value is None:
        return default
    if not isinstance(toml_value, str):
        raise ConfigError(
            f"{source_label}: {name} must be a string, got {type(toml_value).__name__}"
        )
    return toml_value


def _resolve_int(
    *, name: str, env_name: str, toml_value: Any, default: int, source_label: str
) -> int:
    raw_env = os.environ.get(env_name)
    if raw_env is not None and raw_env.strip() != "":
        try:
            return int(raw_env)
        except ValueError as exc:
            raise ConfigError(f"{env_name}={raw_env!r} is not an integer: {exc}") from exc
    if toml_value is None:
        return default
    # bool is a subclass of int — reject explicitly so `max_attempts = true`
    # doesn't silently parse as 1.
    if isinstance(toml_value, bool) or not isinstance(toml_value, int):
        raise ConfigError(
            f"{source_label}: {name} must be an integer, got {type(toml_value).__name__}"
        )
    return toml_value


def _resolve_float(
    *, name: str, env_name: str, toml_value: Any, default: float, source_label: str
) -> float:
    raw_env = os.environ.get(env_name)
    if raw_env is not None and raw_env.strip() != "":
        try:
            return float(raw_env)
        except ValueError as exc:
            raise ConfigError(f"{env_name}={raw_env!r} is not a float: {exc}") from exc
    if toml_value is None:
        return default
    if isinstance(toml_value, bool) or not isinstance(toml_value, (int, float)):
        raise ConfigError(
            f"{source_label}: {name} must be a number, got {type(toml_value).__name__}"
        )
    return float(toml_value)


def _resolve_log_level(*, toml_value: Any, default: str, source_label: str) -> int:
    raw_env = os.environ.get("RALPH_LOG_LEVEL")
    if raw_env is not None and raw_env.strip() != "":
        candidate = raw_env.strip().upper()
        if candidate not in _VALID_LOG_LEVEL_NAMES:
            raise ConfigError(
                f"RALPH_LOG_LEVEL={candidate!r} not in {sorted(_VALID_LOG_LEVEL_NAMES)}"
            )
        return cast(int, logging.getLevelName(candidate))
    if toml_value is None:
        candidate = default.upper()
    else:
        if not isinstance(toml_value, str):
            raise ConfigError(
                f"{source_label}: log_level must be a string, got {type(toml_value).__name__}"
            )
        candidate = toml_value.upper()
    if candidate not in _VALID_LOG_LEVEL_NAMES:
        raise ConfigError(
            f"{source_label}: log_level={candidate!r} not in {sorted(_VALID_LOG_LEVEL_NAMES)}"
        )
    return cast(int, logging.getLevelName(candidate))


def load_config() -> ExecutorConfig:
    """Read defaults < ``<repo>/.ralph/config.toml`` < env, and validate.

    ``RALPH_REPO_PATH`` is required (env-only — needed to locate the
    TOML file). ``ANTHROPIC_API_KEY`` is optional and env-only by policy
    (secret); empty string means "use claude CLI's OAuth session".
    """
    repo_raw = _require_env("RALPH_REPO_PATH")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    repo_path = _validate_repo_path(Path(repo_raw))

    toml_overrides = _load_toml_overrides(repo_path)
    source_label = str(repo_path / ".ralph" / "config.toml")

    queue_branch = _resolve_str(
        name="queue_branch",
        env_name="RALPH_QUEUE_BRANCH",
        toml_value=toml_overrides.get("queue_branch"),
        default=DEFAULT_QUEUE_BRANCH,
        source_label=source_label,
    )
    main_branch = _resolve_str(
        name="main_branch",
        env_name="RALPH_MAIN_BRANCH",
        toml_value=toml_overrides.get("main_branch"),
        default=DEFAULT_MAIN_BRANCH,
        source_label=source_label,
    )
    max_attempts = _resolve_int(
        name="max_attempts",
        env_name="RALPH_MAX_ATTEMPTS",
        toml_value=toml_overrides.get("max_attempts"),
        default=DEFAULT_MAX_ATTEMPTS,
        source_label=source_label,
    )
    log_level = _resolve_log_level(
        toml_value=toml_overrides.get("log_level"),
        default=DEFAULT_LOG_LEVEL,
        source_label=source_label,
    )
    sleep_seconds = _resolve_float(
        name="iteration_sleep_seconds",
        env_name="RALPH_ITERATION_SLEEP_SECONDS",
        toml_value=toml_overrides.get("iteration_sleep_seconds"),
        default=DEFAULT_ITERATION_SLEEP_SECONDS,
        source_label=source_label,
    )
    claude_binary = _resolve_str(
        name="claude_binary",
        env_name="RALPH_CLAUDE_BINARY",
        toml_value=toml_overrides.get("claude_binary"),
        default=DEFAULT_CLAUDE_BINARY,
        source_label=source_label,
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
