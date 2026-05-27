"""Executor configuration loaded from defaults < TOML < env.

All runtime knobs live on the immutable ``ExecutorConfig`` dataclass so
the loop driver and the queue source can be tested with hand-rolled
config objects without any environment dependency.

Precedence (lowest → highest):

  1. Hard-coded defaults (``DEFAULT_*`` constants).
  2. ``<repo>/.ralph/config.toml`` (optional, checked-in).
  3. ``RALPH_*`` environment variables.
  4. CLI flags applied by ``ralph_executor.cli._apply_overrides``.

Repo path resolution (highest → lowest, all evaluated by the CLI layer
except the last two which live here):

  1. ``--repo <PATH>``
  2. ``--workspace <NAME>``  →  ``$RALPH_HOME/<NAME>``
  3. ``RALPH_REPO_PATH`` env var
  4. Current working directory

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
# CI-green verifier budget (Plan 18, Task 6). 6 polls × 30 s = 3-minute
# wall budget per iteration; longer waits roll over into the next
# iteration via classify_outcome -> ``partial``.
DEFAULT_PR_CHECK_POLL_MAX_ATTEMPTS = 6
DEFAULT_PR_CHECK_POLL_INTERVAL_SECONDS = 30.0
DEFAULT_USE_WORKTREES = True
# Sweep stale-PR threshold in days. PRs older than this in pending-pr/
# get moved to blocked/. Promoted from RALPH_STALE_DAYS env-only.
DEFAULT_STALE_DAYS = 3

_VALID_LOG_LEVEL_NAMES = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off"})

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
        "git_host",
        # Per-host project identifiers + alerting. Promoted from env-only
        # in this layer because they are project state, not secrets.
        "gh_owner",
        "ado_org_url",
        "ado_project",
        "halt_webhook",
        # Sweep tuning. Promoted from env-only so operators can pin them
        # in .ralph/config.toml instead of exporting RALPH_* every shell.
        "bot_author_email",
        "stale_days",
        # CI-green verifier budget — see DEFAULT_PR_CHECK_POLL_* above.
        "pr_check_poll_max_attempts",
        "pr_check_poll_interval_seconds",
        # Stage-B knob: opt into the two-worktree execution model. Default
        # True so new installs get the simpler claim/persist path; legacy
        # single-checkout setups can opt out with `use_worktrees = false`
        # in TOML or `RALPH_USE_WORKTREES=0` in the environment.
        "use_worktrees",
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
    # Empty string = "not set in TOML/env"; host_select falls back to
    # reading ``RALPH_GIT_HOST`` directly and errors if that is also
    # unset. Validation of allowed values (github/ado) happens in
    # host_select, not here, to keep config layer host-agnostic.
    git_host: str
    # Per-host project identifiers + alerting. Empty string = "not set
    # via TOML or env"; downstream consumers (host_select.verify_auth_env,
    # safety.halt) decide whether absence is an error. Layered same as
    # the other knobs: defaults < project-TOML < env.
    gh_owner: str
    ado_org_url: str
    ado_project: str
    halt_webhook: str
    # CI-green verifier budget consumed by
    # ``ralph_executor.claude_spawn._wait_for_pr_checks``. ``max_attempts``
    # × ``interval_seconds`` is the wall budget per iteration; on timeout
    # the classifier returns ``partial`` and the next iteration re-polls.
    pr_check_poll_max_attempts: int
    pr_check_poll_interval_seconds: float
    # Stage-B execution model. When True, the loop runs each PBI inside a
    # per-PBI worktree under ``<repo>/.ralph-work/repo-<PBI-id>/`` and
    # reads/writes ``.ralph/`` from a separate ``<repo>/.ralph-work/queue/``
    # worktree pinned to ``queue_branch``. When False, behaviour reverts
    # to the Stage-A single-checkout branch-dance path.
    use_worktrees: bool = DEFAULT_USE_WORKTREES
    # Sweep tuning — promoted from env-only. ``bot_author_email`` is the
    # commit/PR author email ralph uses; sweep skips comments by this
    # author so the loop doesn't feed back into itself. Env name keeps
    # the historical ``RALPH_ADO_AUTHOR_EMAIL`` spelling for
    # backwards-compat — sweep is host-agnostic, so the TOML key drops
    # the misleading ADO prefix. ``stale_days`` is the pending-PR
    # staleness threshold (days), strictly positive (validated in
    # ``load_config``).
    bot_author_email: str = ""
    stale_days: int = DEFAULT_STALE_DAYS


def validate_repo_path(path: Path, *, source: str) -> Path:
    """Validate that ``path`` is a git repo directory and return it resolved.

    ``source`` describes where the path came from (env var name, CLI flag,
    or "cwd") so the error message points the operator at the right knob.
    """
    if not path.exists():
        raise ConfigError(f"repo path {path} (from {source}) does not exist")
    if not path.is_dir():
        raise ConfigError(f"repo path {path} (from {source}) is not a directory")
    if not (path / ".git").exists():
        raise ConfigError(
            f"repo path {path} (from {source}) is not a git repository (no .git/ entry)"
        )
    return path.resolve()


def _resolve_repo_path() -> Path:
    """Resolve the repo path from env → cwd, then validate.

    Resolution order (highest → lowest):
      1. ``RALPH_REPO_PATH`` env var (operator/daemon escape hatch).
      2. Current working directory.

    CLI flags (``--repo``, ``--workspace``) override the result via
    ``ralph_executor.cli._apply_overrides``.
    """
    env_value = os.environ.get("RALPH_REPO_PATH", "").strip()
    if env_value:
        return validate_repo_path(Path(env_value), source="RALPH_REPO_PATH")
    return validate_repo_path(Path.cwd(), source="cwd")


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
    """env > toml > default for a string-valued knob.

    Env values are stripped so downstream consumers never see surrounding
    whitespace. Without this, a value like
    ``RALPH_ADO_AUTHOR_EMAIL=' ralph@bot.com '`` survives the truthiness
    guards in consumers (the string is non-empty) and string-equality
    comparisons (e.g. PR-author matching in the sweep) silently fail.
    Mirrors the strip already done in ``_resolve_repo_path``.
    """
    env_value = os.environ.get(env_name)
    if env_value and env_value.strip():
        return env_value.strip()
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


def _resolve_bool(
    *, name: str, env_name: str, toml_value: Any, default: bool, source_label: str
) -> bool:
    """env > toml > default for a boolean-valued knob.

    Env strings parse case-insensitively from a small allow-list (true /
    false / 1 / 0 / yes / no / on / off). Anything else is a
    ``ConfigError`` rather than a silent ``bool(s) == True`` coercion.
    TOML values must be a real bool — strings are rejected to keep config
    typing honest.
    """
    raw_env = os.environ.get(env_name)
    if raw_env is not None and raw_env.strip() != "":
        candidate = raw_env.strip().lower()
        if candidate in _TRUE_STRINGS:
            return True
        if candidate in _FALSE_STRINGS:
            return False
        allowed = sorted(_TRUE_STRINGS | _FALSE_STRINGS)
        raise ConfigError(f"{env_name}={raw_env!r} not a boolean (expected one of {allowed})")
    if toml_value is None:
        return default
    if not isinstance(toml_value, bool):
        raise ConfigError(
            f"{source_label}: {name} must be a boolean, got {type(toml_value).__name__}"
        )
    return toml_value


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

    Repo path resolution: ``RALPH_REPO_PATH`` env var if set, else the
    current working directory. CLI flags (``--repo``, ``--workspace``)
    override the result downstream in ``cli._apply_overrides``.

    ``ANTHROPIC_API_KEY`` is optional and env-only by policy (secret);
    empty string means "use claude CLI's OAuth session".
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    repo_path = _resolve_repo_path()

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
    git_host = _resolve_str(
        name="git_host",
        env_name="RALPH_GIT_HOST",
        toml_value=toml_overrides.get("git_host"),
        default="",
        source_label=source_label,
    )
    gh_owner = _resolve_str(
        name="gh_owner",
        env_name="GH_OWNER",
        toml_value=toml_overrides.get("gh_owner"),
        default="",
        source_label=source_label,
    )
    ado_org_url = _resolve_str(
        name="ado_org_url",
        env_name="ADO_ORG_URL",
        toml_value=toml_overrides.get("ado_org_url"),
        default="",
        source_label=source_label,
    )
    ado_project = _resolve_str(
        name="ado_project",
        env_name="ADO_PROJECT",
        toml_value=toml_overrides.get("ado_project"),
        default="",
        source_label=source_label,
    )
    halt_webhook = _resolve_str(
        name="halt_webhook",
        env_name="RALPH_HALT_WEBHOOK",
        toml_value=toml_overrides.get("halt_webhook"),
        default="",
        source_label=source_label,
    )
    bot_author_email = _resolve_str(
        name="bot_author_email",
        env_name="RALPH_ADO_AUTHOR_EMAIL",
        toml_value=toml_overrides.get("bot_author_email"),
        default="",
        source_label=source_label,
    )
    stale_days = _resolve_int(
        name="stale_days",
        env_name="RALPH_STALE_DAYS",
        toml_value=toml_overrides.get("stale_days"),
        default=DEFAULT_STALE_DAYS,
        source_label=source_label,
    )
    if stale_days <= 0:
        raise ConfigError(f"{source_label}: stale_days must be positive (got {stale_days})")
    pr_check_poll_max_attempts = _resolve_int(
        name="pr_check_poll_max_attempts",
        env_name="RALPH_PR_CHECK_POLL_MAX_ATTEMPTS",
        toml_value=toml_overrides.get("pr_check_poll_max_attempts"),
        default=DEFAULT_PR_CHECK_POLL_MAX_ATTEMPTS,
        source_label=source_label,
    )
    pr_check_poll_interval_seconds = _resolve_float(
        name="pr_check_poll_interval_seconds",
        env_name="RALPH_PR_CHECK_POLL_INTERVAL_SECONDS",
        toml_value=toml_overrides.get("pr_check_poll_interval_seconds"),
        default=DEFAULT_PR_CHECK_POLL_INTERVAL_SECONDS,
        source_label=source_label,
    )
    use_worktrees = _resolve_bool(
        name="use_worktrees",
        env_name="RALPH_USE_WORKTREES",
        toml_value=toml_overrides.get("use_worktrees"),
        default=DEFAULT_USE_WORKTREES,
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
        git_host=git_host,
        gh_owner=gh_owner,
        ado_org_url=ado_org_url,
        ado_project=ado_project,
        halt_webhook=halt_webhook,
        pr_check_poll_max_attempts=pr_check_poll_max_attempts,
        pr_check_poll_interval_seconds=pr_check_poll_interval_seconds,
        use_worktrees=use_worktrees,
        bot_author_email=bot_author_email,
        stale_days=stale_days,
    )
