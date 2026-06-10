"""Executor configuration loaded from defaults < TOML < env.

All runtime knobs live on the immutable ``ExecutorConfig`` dataclass so
the loop driver and the queue source can be tested with hand-rolled
config objects without any environment dependency.

Precedence (lowest → highest):

  1. Hard-coded defaults (``DEFAULT_*`` constants).
  2. ``~/.ralph/config.toml`` (per-machine; written by ``ralph-executor init``).
  3. ``RALPH_*`` environment variables.
  4. CLI flags applied by ``ralph_executor.cli._apply_overrides``.

The executor is queue-driven: the operator configures one
``workspace_root`` plus the queue repo URL / branch in user TOML and
every iteration the loop reads the next PBI's ``target_repo`` and
materialises a per-target clone under
``<workspace_root>/clones/<owner>/<name>/``. There is no top-level
"current repo" — ``ExecutorConfig`` has no ``repo_path`` field.

``anthropic_api_key`` is intentionally env-only (secret) and is the
only knob not readable from TOML.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, NamedTuple, cast

from ralph_executor.url_utils import parse_target_repo

# tomllib.load returns dict[str, Any] for any parseable TOML document.

DEFAULT_MAIN_BRANCH = "main"
DEFAULT_QUEUE_BRANCH = "ralph-queue"
# Counts only FAILED iterations (stuck / error) — partial is multi-step
# progress and doesn't decrement the budget. 20 gives a long plan plenty
# of room to surface a genuinely stuck loop without false-tripping.
DEFAULT_MAX_ATTEMPTS = 20
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_ITERATION_SLEEP_SECONDS = 30.0
DEFAULT_CLAUDE_BINARY = "claude"
# Permission mode passed to the spawned ``claude -p`` subprocess via
# ``--permission-mode``. Setting it explicitly stops the executor's
# Claude subprocess from inheriting the host's
# ``~/.claude/settings.json`` ``defaultMode``, so a host configured in
# ``auto`` mode (with its safety classifier) does not have to be
# globally relaxed just to let ralph write files like
# ``.claude/settings.json``. Default is ``bypassPermissions`` because
# ralph runs in non-interactive ``-p`` mode and cannot answer permission
# prompts.
DEFAULT_CLAUDE_PERMISSION_MODE = "bypassPermissions"
# Mirrors the claude CLI's documented ``--permission-mode`` enum (see
# ``claude --help``). Validated in ``load_config`` so a typo surfaces
# immediately rather than being passed through to claude (which exits 1
# on an unrecognised value).
_VALID_CLAUDE_PERMISSION_MODES = frozenset(
    {"acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan"}
)
# CI-green verifier budget (Plan 18, Task 6). 6 polls × 30 s = 3-minute
# wall budget per iteration; longer waits roll over into the next
# iteration via classify_outcome -> ``partial``.
DEFAULT_PR_CHECK_POLL_MAX_ATTEMPTS = 6
DEFAULT_PR_CHECK_POLL_INTERVAL_SECONDS = 30.0
DEFAULT_USE_WORKTREES = True
# Sweep stale-PR threshold in days. PRs older than this in pending-pr/
# get moved to blocked/. Promoted from RALPH_STALE_DAYS env-only.
DEFAULT_STALE_DAYS = 3
# Claude Code per-bash-tool ceiling in milliseconds. 15 minutes; Claude
# Code's own default is 600_000 (10 min). Propagated to the spawned
# claude subprocess via the BASH_MAX_TIMEOUT_MS env var in
# ``claude_spawn.spawn_claude_p`` (subprocess-scoped, NOT exported to
# ralph's parent env).
DEFAULT_BASH_MAX_TIMEOUT_MS = 900_000
# Per-iteration deadline (seconds) for the spawned ``claude -p``
# subprocess. ``BASH_MAX_TIMEOUT_MS`` caps each individual bash-tool
# call inside Claude, but a session can chain dozens of tool calls and
# stay alive much longer than any sensible per-iteration budget. When
# ``proc.wait(timeout=...)`` exceeds this value, the executor kills the
# child and surfaces a synthetic ``error`` outcome to the loop so
# attempts/max-attempts machinery handles the give-up policy. 1200 s
# (20 min) is tight enough to catch hangs within one short coffee break
# while leaving normal first-iteration work room to finish.
DEFAULT_CLAUDE_SESSION_TIMEOUT_SECONDS = 1200
# Sweep auto-merge opt-in (Plan SWEEP-AUTO-MERGE-CLEAN-PRS). When True,
# the sweep auto-merges PRs that GitHub reports as
# ``mergeable_state == "clean"`` (CI green + required approvals + no
# conflicts + branch up-to-date). Default False — operators opt in.
DEFAULT_AUTO_MERGE_CLEAN_PRS = False
# Cycle-detector same_file_thrashing thresholds. The rule trips when one
# file is touched by ``same_file_min_prs`` distinct PBIs inside a rolling
# ``same_file_window_hours`` window. Defaults preserve the historical
# module-level constants in ``ralph_executor.safety.cycle_detector``
# (10 PRs / 24h). Operators raise the floor manually for high-velocity
# sprints where the central executor module legitimately receives many
# feature additions and the rule otherwise false-trips.
DEFAULT_SAME_FILE_MIN_PRS = 10
DEFAULT_SAME_FILE_WINDOW_HOURS = 24.0
# Drain-on-idle defaults. With ``watch_mode=False`` (the default), ``run_loop``
# exits cleanly after ``idle_exit_threshold`` consecutive idle iterations so
# pods / containers stop billing minutes once their queue is drained.
# Operators who want the old "run forever, sleep on idle" daemon behaviour set
# ``watch_mode = true`` (TOML) / ``RALPH_WATCH_MODE=1`` (env) / ``--watch``
# (CLI). The threshold is 2 so a single false-idle caused by a sweep tick
# mid-flight doesn't cut a healthy loop short.
DEFAULT_WATCH_MODE = False
DEFAULT_IDLE_EXIT_THRESHOLD = 2
# Autobug feature flags. When ``autobug_enabled`` is True, executor /
# Claude-subprocess crashes are captured as bug PBIs on the queue (see
# ``ralph_executor.autobug``). The rate-limit caps how many new bug PBIs
# autobug may emit in a rolling window, so a runaway crash loop cannot
# spam the queue. The dedup-done-window controls how far back a
# previously-closed bug is considered for ``reopen_regression`` rather
# than a fresh emission. Severity defaults split python crashes
# (``critical``) from subprocess crashes (``high``) so triage stays
# aligned with the spec.
DEFAULT_AUTOBUG_ENABLED = True
DEFAULT_AUTOBUG_RATE_MAX = 5
DEFAULT_AUTOBUG_RATE_WINDOW_MINUTES = 10
DEFAULT_AUTOBUG_DEDUP_DONE_WINDOW_DAYS = 30
DEFAULT_AUTOBUG_SEVERITY_PYTHON_CRASH = "critical"
DEFAULT_AUTOBUG_SEVERITY_SUBPROCESS_CRASH = "high"

# Per-instance identity. Multi-ralph (Scope 1) lets several ralph
# executors share a queue repo by namespacing the workspace queue clone
# (``<workspace_root>/queue-<instance_id>/``) and writing a per-PBI
# ``CLAIM.json`` ownership marker. ``instance_id`` is resolved (highest
# precedence first) from the ``--instance-id`` CLI flag, the
# ``RALPH_INSTANCE_ID`` env var, the user TOML key, or — last resort —
# a sanitised hostname. Must match ``_INSTANCE_ID_RE`` (filesystem-safe
# lowercase, 1-63 chars, leading alnum).
_INSTANCE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

_VALID_LOG_LEVEL_NAMES = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off"})

# Keys recognised in the TOML config file. Any other top-level key is
# logged as a warning and ignored — keeps forward compatibility cheap.
_TOML_KNOWN_KEYS = frozenset(
    {
        # Queue repo HTTPS URL. Required — operators set this once via TOML
        # (no env var, no default; the loop crashes without it). The
        # executor clones it into ``<workspace_root>/queue/`` and reads /
        # writes ``.ralph/`` from that clone.
        "queue_repo",
        # Branch on the queue repo that holds .ralph/ state. Default
        # "ralph-queue". Operators wanting the post-split shipped behaviour
        # (state on main) override with queue_branch = "main".
        "queue_branch",
        "main_branch",
        "max_attempts",
        "log_level",
        "iteration_sleep_seconds",
        "claude_binary",
        # Permission mode for the spawned claude subprocess. See
        # DEFAULT_CLAUDE_PERMISSION_MODE above for why this is
        # bypassPermissions by default.
        "claude_permission_mode",
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
        # Claude Code per-bash-tool ceiling (ms). Propagated to the
        # spawned claude subprocess via BASH_MAX_TIMEOUT_MS — see
        # claude_spawn.spawn_claude_p.
        "bash_max_timeout_ms",
        # Per-iteration deadline for the spawned claude subprocess. See
        # DEFAULT_CLAUDE_SESSION_TIMEOUT_SECONDS above.
        "claude_session_timeout_seconds",
        # CI-green verifier budget — see DEFAULT_PR_CHECK_POLL_* above.
        "pr_check_poll_max_attempts",
        "pr_check_poll_interval_seconds",
        # Stage-B knob: opt into the two-worktree execution model. Default
        # True so new installs get the simpler claim/persist path; legacy
        # single-checkout setups can opt out with `use_worktrees = false`
        # in TOML or `RALPH_USE_WORKTREES=0` in the environment.
        "use_worktrees",
        # Sweep auto-merge opt-in. Default False; operators opt in via
        # TOML or RALPH_AUTO_MERGE_CLEAN_PRS=1. See
        # DEFAULT_AUTO_MERGE_CLEAN_PRS for the semantics.
        "auto_merge_clean_prs",
        # Target-repo clones live under this root: each PBI's
        # target_repo gets a subdir
        # ``<workspace_root>/clones/<owner>/<name>/``. Default
        # ``$HOME/ralph-workspaces``. Env override: RALPH_WORKSPACE.
        "workspace_root",
        # Cycle-detector same_file_thrashing thresholds — see
        # DEFAULT_SAME_FILE_MIN_PRS / DEFAULT_SAME_FILE_WINDOW_HOURS.
        "same_file_min_prs",
        "same_file_window_hours",
        # Drain-on-idle knobs — see DEFAULT_WATCH_MODE /
        # DEFAULT_IDLE_EXIT_THRESHOLD. Default (watch_mode=False) exits the
        # loop after a short idle streak; opt into daemon mode via
        # ``watch_mode = true``.
        "watch_mode",
        "idle_exit_threshold",
        # Multi-ralph (Scope 1) per-instance identity. Used to namespace
        # ``<workspace_root>/queue-<instance_id>/`` and to write
        # ``CLAIM.json`` ownership markers on inbox → current claims so
        # multiple ralph instances can co-operate on a shared queue repo.
        # See ``resolve_instance_id`` for the full precedence chain.
        "instance_id",
        # Autobug — see DEFAULT_AUTOBUG_* above. All knobs are optional
        # with safe defaults so a fresh install gets crash-capture on
        # without operator action.
        "autobug_enabled",
        "autobug_rate_max",
        "autobug_rate_window_minutes",
        "autobug_dedup_done_window_days",
        "autobug_severity_python_crash",
        "autobug_severity_subprocess_crash",
    }
)

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Raised when ``load_config`` cannot resolve a valid configuration."""


def validate_instance_id(value: str) -> None:
    """Validate a resolved ``instance_id``.

    Applied AFTER hostname sanitisation by ``resolve_instance_id``.
    Empty / missing values are the resolver's problem, not this
    validator's — by the time it reaches here the candidate is supposed
    to be the final, filesystem-safe form. Raises ``ConfigError`` on
    anything that would be unsafe to splice into a path or a git
    branch / commit subject.
    """
    if not _INSTANCE_ID_RE.fullmatch(value):
        raise ConfigError(
            f"instance_id {value!r} must match {_INSTANCE_ID_RE.pattern} "
            "(filesystem-safe lowercase, 1-63 chars, starts with alnum)"
        )


def sanitise_hostname(hostname: str) -> str:
    """Lowercase + replace disallowed chars with ``-`` for hostname fallback.

    Output is the candidate passed to ``validate_instance_id`` when
    nothing better is set. Returns the empty string if the input is
    empty; the resolver turns that into a ``ConfigError``.
    """
    lowered = hostname.lower()
    return re.sub(r"[^a-z0-9_-]", "-", lowered)


def resolve_instance_id(
    *,
    cli_value: str | None,
    env_value: str | None,
    toml_value: str | None,
    hostname: str,
) -> str:
    """First-match-wins resolver. Returns a validated ``instance_id``.

    Precedence: ``--instance-id`` CLI flag → ``RALPH_INSTANCE_ID`` env
    var → ``~/.ralph/config.toml`` key → sanitised hostname. The first
    non-``None`` candidate is validated and returned as-is — note that
    only ``None`` short-circuits the search; empty / malformed strings
    still reach the validator and raise ``ConfigError`` (so a deliberate
    ``--instance-id ""`` doesn't silently fall through to the env / TOML
    layers, per the truthiness-vs-None rule).
    """
    for candidate in (cli_value, env_value, toml_value):
        if candidate is not None:
            validate_instance_id(candidate)
            return candidate
    candidate = sanitise_hostname(hostname)
    if not candidate:
        raise ConfigError(
            "could not derive instance_id from hostname; set instance_id in "
            "~/.ralph/config.toml or pass --instance-id"
        )
    validate_instance_id(candidate)
    return candidate


@dataclass(frozen=True)
class ExecutorConfig:
    """Immutable runtime configuration for the executor.

    Created once at process start and passed through every public entry
    point of the loop driver, queue source, and claude-spawn helpers.
    """

    # HTTPS URL of the queue repo (e.g. ``https://github.com/emp3thy/ralph-queue``).
    # Required via TOML (or the ``--queue-repo`` CLI flag). The loop clones
    # this into ``<workspace_root>/queue/`` once and pulls on subsequent
    # iterations; every queue mutation pushes back to its ``main`` branch.
    queue_repo: str
    # Branch on queue_repo that holds .ralph/ state. Default "ralph-queue"
    # (see DEFAULT_QUEUE_BRANCH). The executor's queue clone is permanently
    # on this branch; every clone / pull / push uses it.
    queue_branch: str
    main_branch: str
    max_attempts: int
    log_level: int
    iteration_sleep_seconds: float
    claude_binary: str
    # Permission mode forwarded to the spawned ``claude -p`` subprocess
    # via ``--permission-mode``. Default ``"bypassPermissions"`` —
    # the executor runs Claude non-interactively and cannot answer
    # permission prompts, so the spawned subprocess must not inherit a
    # host ``defaultMode`` that prompts. Allowed values are the claude
    # CLI's documented ``--permission-mode`` enum (validated in
    # ``load_config``).
    claude_permission_mode: str
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
    # Multi-ralph (Scope 1) per-instance identity. Required, non-Optional
    # — ``resolve_instance_id`` guarantees a validated string (CLI > env
    # > TOML > sanitised hostname). The workspace queue clone is
    # namespaced at ``<workspace_root>/queue-<instance_id>/`` and every
    # claimed PBI carries a ``CLAIM.json`` whose ``instance_id`` is this
    # value. Validated against ``_INSTANCE_ID_RE`` so it is safe to
    # splice into directory names, commit subjects, and log lines.
    instance_id: str
    # Execution model. Must be True after EXECUTOR-QUEUE-REPO-SPLIT —
    # ``load_config`` rejects ``False`` because the Stage-A single-checkout
    # branch-dance path is gone. The loop runs each PBI inside a per-PBI
    # worktree under ``<target-clone>/.ralph-work/<PBI-id>/`` and
    # reads/writes ``.ralph/`` from the queue clone at
    # ``<workspace_root>/queue/`` (materialised by ``ensure_queue_clone``).
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
    # Claude Code bash-tool ceiling in milliseconds. Set on the Claude
    # subprocess env in ``claude_spawn.spawn_claude_p`` (subprocess-scoped
    # bridge — NOT exported to ralph's parent env). Default 900_000 (15
    # min); Claude Code's own default is 600_000 (10 min). Strictly
    # positive (validated in ``load_config``).
    bash_max_timeout_ms: int = DEFAULT_BASH_MAX_TIMEOUT_MS
    # Per-iteration wall-clock deadline (seconds) for the spawned
    # ``claude -p`` subprocess. Enforced by
    # ``claude_spawn.spawn_claude_p`` via ``proc.wait(timeout=...)``; on
    # expiry the child is killed, tee threads are joined, and a
    # synthetic ``ClaudeOutcome(kind="error", ...)`` surfaces to the
    # loop so attempts increments and max-attempts → blocked. Strictly
    # positive (validated in ``load_config``).
    claude_session_timeout_seconds: int = DEFAULT_CLAUDE_SESSION_TIMEOUT_SECONDS
    # Sweep auto-merge opt-in. When True the sweep act path merges PRs
    # that GitHub reports as ``mergeable_state == "clean"`` via the
    # ``pr-github`` skill's ``merge_pr`` op. Default False — flag must be
    # set in TOML (``auto_merge_clean_prs = true``) or env
    # (``RALPH_AUTO_MERGE_CLEAN_PRS=1``). Merging a PR a human still
    # wanted to review is unrecoverable; opt in carefully.
    auto_merge_clean_prs: bool = DEFAULT_AUTO_MERGE_CLEAN_PRS
    # Where target-repo clones live. Default: $HOME/ralph-workspaces.
    # Each target gets a subdir: <workspace_root>/clones/<owner>/<name>/.
    # Override via TOML key `workspace_root` or env `RALPH_WORKSPACE`.
    workspace_root: Path = field(default_factory=lambda: Path.home() / "ralph-workspaces")
    # Cycle-detector same_file_thrashing thresholds (Layer 3 safety).
    # ``same_file_min_prs`` is the distinct-PBI floor that trips the
    # rule; ``same_file_window_hours`` is the rolling window over which
    # PBIs are counted. Both strictly positive (validated in
    # ``load_config``). Defaults preserve the prior module-level
    # constants (10 / 24h) — operators raise them for high-velocity
    # sprints where a central module legitimately receives many PRs.
    same_file_min_prs: int = DEFAULT_SAME_FILE_MIN_PRS
    same_file_window_hours: float = DEFAULT_SAME_FILE_WINDOW_HOURS
    # Drain-on-idle. ``watch_mode=False`` (the default) means ``run_loop``
    # exits cleanly after ``idle_exit_threshold`` consecutive idle iterations
    # — the right shape for unattended pod / container runs where the queue
    # contents are baked in at launch and nobody is around to feed it more
    # work. Set ``watch_mode=True`` (TOML key ``watch_mode`` / env
    # ``RALPH_WATCH_MODE`` / CLI ``--watch``) for the legacy "run forever,
    # sleep on idle" daemon mode used on a workstation. ``idle_exit_threshold``
    # is strictly positive (validated in ``load_config``); a higher value
    # tolerates more transient false-idles before exit.
    watch_mode: bool = DEFAULT_WATCH_MODE
    idle_exit_threshold: int = DEFAULT_IDLE_EXIT_THRESHOLD
    # Autobug knobs. ``autobug_enabled`` is the master switch — when
    # False, ``detect_python_crash`` / ``detect_subprocess_crash`` short
    # circuit and never write to the queue. ``autobug_rate_max`` /
    # ``autobug_rate_window_minutes`` cap how many new bug PBIs autobug
    # may emit in a rolling window so a tight crash loop cannot spam the
    # queue (rate_check + rollup in ``ralph_executor.autobug.fuses``).
    # ``autobug_dedup_done_window_days`` is how far back a previously
    # closed bug is considered a regression candidate (otherwise a fresh
    # PBI is filed). The two severity strings are stamped onto each
    # emitted PBI's frontmatter unchanged — keep them in the PBI
    # severity vocabulary (``critical`` / ``high`` / ``normal`` /
    # ``low``).
    autobug_enabled: bool = DEFAULT_AUTOBUG_ENABLED
    autobug_rate_max: int = DEFAULT_AUTOBUG_RATE_MAX
    autobug_rate_window_minutes: int = DEFAULT_AUTOBUG_RATE_WINDOW_MINUTES
    autobug_dedup_done_window_days: int = DEFAULT_AUTOBUG_DEDUP_DONE_WINDOW_DAYS
    autobug_severity_python_crash: str = DEFAULT_AUTOBUG_SEVERITY_PYTHON_CRASH
    autobug_severity_subprocess_crash: str = DEFAULT_AUTOBUG_SEVERITY_SUBPROCESS_CRASH

    @property
    def queue_clone_path(self) -> Path:
        """Filesystem path of this instance's queue clone.

        Scope 1 multi-ralph: the queue clone lives at
        ``<workspace_root>/queue-<instance_id>/``. Every module that
        derives the queue-clone path (loop, claude_spawn, movements,
        filesystem, safety.stuck) routes through this property so the
        path stays consistent across the codebase.
        """
        return self.workspace_root / f"queue-{self.instance_id}"


def _load_user_toml_overrides() -> Mapping[str, Any]:
    """Read ``~/.ralph/config.toml`` and return overrides keyed by knob name.

    Replaces the per-repo ``<repo>/.ralph/config.toml`` loader removed in
    the KILL-RALPH-HOME refactor. Project TOML is no longer loaded; every
    knob previously sourced from it now lives at the user level.

    Missing file is a no-op (returns empty mapping). Malformed TOML
    raises ``ConfigError`` so the operator notices a typo rather than
    silently falling back to defaults. Unknown top-level keys are logged
    at WARNING and dropped; ``ralph_home``, ``skills_root`` and
    ``claude_skills_dir`` are allow-listed silently (stale-warn case for
    the first, read directly by ``host_select`` for the latter two).
    """
    from ralph_executor.user_config import user_config_path

    cfg_file = user_config_path()
    if not cfg_file.is_file():
        return {}
    try:
        with cfg_file.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{cfg_file}: invalid TOML: {exc}") from exc
    silent_allowlist = {"ralph_home", "skills_root", "claude_skills_dir"}
    unknown = sorted(k for k in data if k not in _TOML_KNOWN_KEYS and k not in silent_allowlist)
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


def _resolve_path(
    *, name: str, env_name: str, toml_value: Any, default: Path, source_label: str
) -> Path:
    """env > toml > default for a Path-valued knob.

    Both env and TOML values are strings; ``~`` is expanded via
    ``Path.expanduser()``. TOML must be a string; bools, ints, etc.
    raise ConfigError.
    """
    raw_env = os.environ.get(env_name)
    if raw_env is not None and raw_env.strip() != "":
        return Path(raw_env.strip()).expanduser()
    if toml_value is None:
        return default
    if not isinstance(toml_value, str):
        raise ConfigError(
            f"{source_label}: {name} must be a string path, got {type(toml_value).__name__}"
        )
    return Path(toml_value).expanduser()


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


def _resolve_queue_repo(toml_overrides: Mapping[str, Any], source_label: str) -> str:
    queue_repo_value = toml_overrides.get("queue_repo")
    if queue_repo_value is None:
        raise ConfigError(
            f"{source_label}: queue_repo not configured. "
            "Run `ralph-executor init` (writes ~/.ralph/config.toml) "
            "or pass --queue-repo."
        )
    if not isinstance(queue_repo_value, str):
        raise ConfigError(
            f"{source_label}: queue_repo must be a string, got {type(queue_repo_value).__name__}"
        )
    try:
        parse_target_repo(queue_repo_value)
    except ValueError as exc:
        raise ConfigError(
            f"{source_label}: queue_repo {queue_repo_value!r} is not a valid HTTPS URL: {exc}"
        ) from exc
    return queue_repo_value


class _BranchSettings(NamedTuple):
    main_branch: str
    queue_branch: str


def _resolve_branches(toml_overrides: Mapping[str, Any], source_label: str) -> _BranchSettings:
    main_branch = _resolve_str(
        name="main_branch",
        env_name="RALPH_MAIN_BRANCH",
        toml_value=toml_overrides.get("main_branch"),
        default=DEFAULT_MAIN_BRANCH,
        source_label=source_label,
    )
    queue_branch = _resolve_str(
        name="queue_branch",
        env_name="RALPH_QUEUE_BRANCH",
        toml_value=toml_overrides.get("queue_branch"),
        default=DEFAULT_QUEUE_BRANCH,
        source_label=source_label,
    )
    queue_branch = queue_branch.strip()
    if not queue_branch:
        raise ConfigError(f"{source_label}: queue_branch must be a non-empty branch name")
    if queue_branch == "HEAD":
        raise ConfigError(f"{source_label}: queue_branch must be a branch name, not 'HEAD'")
    if queue_branch.startswith("refs/heads/"):
        raise ConfigError(
            f"{source_label}: queue_branch must not include the 'refs/heads/' "
            f"prefix (got {queue_branch!r})"
        )
    return _BranchSettings(main_branch=main_branch, queue_branch=queue_branch)


class _RuntimeKnobs(NamedTuple):
    max_attempts: int
    log_level: int
    iteration_sleep_seconds: float


def _resolve_runtime_knobs(toml_overrides: Mapping[str, Any], source_label: str) -> _RuntimeKnobs:
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
    iteration_sleep_seconds = _resolve_float(
        name="iteration_sleep_seconds",
        env_name="RALPH_ITERATION_SLEEP_SECONDS",
        toml_value=toml_overrides.get("iteration_sleep_seconds"),
        default=DEFAULT_ITERATION_SLEEP_SECONDS,
        source_label=source_label,
    )
    return _RuntimeKnobs(
        max_attempts=max_attempts,
        log_level=log_level,
        iteration_sleep_seconds=iteration_sleep_seconds,
    )


class _ClaudeSettings(NamedTuple):
    claude_binary: str
    claude_permission_mode: str


def _resolve_claude_settings(
    toml_overrides: Mapping[str, Any], source_label: str
) -> _ClaudeSettings:
    claude_binary = _resolve_str(
        name="claude_binary",
        env_name="RALPH_CLAUDE_BINARY",
        toml_value=toml_overrides.get("claude_binary"),
        default=DEFAULT_CLAUDE_BINARY,
        source_label=source_label,
    )
    claude_permission_mode = _resolve_str(
        name="claude_permission_mode",
        env_name="RALPH_CLAUDE_PERMISSION_MODE",
        toml_value=toml_overrides.get("claude_permission_mode"),
        default=DEFAULT_CLAUDE_PERMISSION_MODE,
        source_label=source_label,
    )
    if claude_permission_mode not in _VALID_CLAUDE_PERMISSION_MODES:
        raise ConfigError(
            f"{source_label}: claude_permission_mode={claude_permission_mode!r} "
            f"not in {sorted(_VALID_CLAUDE_PERMISSION_MODES)}"
        )
    return _ClaudeSettings(
        claude_binary=claude_binary,
        claude_permission_mode=claude_permission_mode,
    )


class _HostSettings(NamedTuple):
    git_host: str
    gh_owner: str
    ado_org_url: str
    ado_project: str
    halt_webhook: str


def _resolve_host_settings(toml_overrides: Mapping[str, Any], source_label: str) -> _HostSettings:
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
    return _HostSettings(
        git_host=git_host,
        gh_owner=gh_owner,
        ado_org_url=ado_org_url,
        ado_project=ado_project,
        halt_webhook=halt_webhook,
    )


class _SweepTuningSettings(NamedTuple):
    bot_author_email: str
    stale_days: int
    bash_max_timeout_ms: int
    claude_session_timeout_seconds: int
    pr_check_poll_max_attempts: int
    pr_check_poll_interval_seconds: float


def _resolve_sweep_tuning(
    toml_overrides: Mapping[str, Any], source_label: str
) -> _SweepTuningSettings:
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
    bash_max_timeout_ms = _resolve_int(
        name="bash_max_timeout_ms",
        env_name="BASH_MAX_TIMEOUT_MS",
        toml_value=toml_overrides.get("bash_max_timeout_ms"),
        default=DEFAULT_BASH_MAX_TIMEOUT_MS,
        source_label=source_label,
    )
    if bash_max_timeout_ms <= 0:
        raise ConfigError(
            f"{source_label}: bash_max_timeout_ms must be positive (got {bash_max_timeout_ms})"
        )
    claude_session_timeout_seconds = _resolve_int(
        name="claude_session_timeout_seconds",
        env_name="RALPH_CLAUDE_SESSION_TIMEOUT_SECONDS",
        toml_value=toml_overrides.get("claude_session_timeout_seconds"),
        default=DEFAULT_CLAUDE_SESSION_TIMEOUT_SECONDS,
        source_label=source_label,
    )
    if claude_session_timeout_seconds <= 0:
        raise ConfigError(
            f"{source_label}: claude_session_timeout_seconds must be positive "
            f"(got {claude_session_timeout_seconds})"
        )
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
    return _SweepTuningSettings(
        bot_author_email=bot_author_email,
        stale_days=stale_days,
        bash_max_timeout_ms=bash_max_timeout_ms,
        claude_session_timeout_seconds=claude_session_timeout_seconds,
        pr_check_poll_max_attempts=pr_check_poll_max_attempts,
        pr_check_poll_interval_seconds=pr_check_poll_interval_seconds,
    )


class _WorkspaceSettings(NamedTuple):
    use_worktrees: bool
    auto_merge_clean_prs: bool
    workspace_root: Path


def _resolve_workspace_settings(
    toml_overrides: Mapping[str, Any], source_label: str
) -> _WorkspaceSettings:
    use_worktrees = _resolve_bool(
        name="use_worktrees",
        env_name="RALPH_USE_WORKTREES",
        toml_value=toml_overrides.get("use_worktrees"),
        default=DEFAULT_USE_WORKTREES,
        source_label=source_label,
    )
    # Stage-A single-checkout mode no longer reachable: the queue is now
    # its own clone at ``<workspace_root>/queue/`` (see ``queue_clone``),
    # so there is no ralph-queue branch to swap to on the primary
    # checkout. Reject the legacy knob outright so an operator who pinned
    # ``use_worktrees = false`` in TOML notices the migration instead of
    # silently running a half-broken claim path.
    if not use_worktrees:
        raise ConfigError(
            f"{source_label}: use_worktrees=False is no longer supported. "
            "The queue is a separate clone on the operator's workspace; the "
            "single-checkout branch-dance model is gone. Remove "
            "'use_worktrees = false' from your config.toml "
            "(or unset RALPH_USE_WORKTREES)."
        )
    auto_merge_clean_prs = _resolve_bool(
        name="auto_merge_clean_prs",
        env_name="RALPH_AUTO_MERGE_CLEAN_PRS",
        toml_value=toml_overrides.get("auto_merge_clean_prs"),
        default=DEFAULT_AUTO_MERGE_CLEAN_PRS,
        source_label=source_label,
    )
    workspace_root = _resolve_path(
        name="workspace_root",
        env_name="RALPH_WORKSPACE",
        toml_value=toml_overrides.get("workspace_root"),
        default=Path.home() / "ralph-workspaces",
        source_label=source_label,
    )
    # The executor creates ``workspace_root`` itself on first use, but
    # its parent must exist — without this guard a typo in the TOML
    # ("workspace_root = '/no/such/dir/ws'") would only surface much
    # later inside ``ensure_clone`` with a confusing OSError.
    if not workspace_root.parent.is_dir():
        raise ConfigError(
            f"{source_label}: workspace_root parent {workspace_root.parent} "
            "does not exist. Create it (or change workspace_root)."
        )
    return _WorkspaceSettings(
        use_worktrees=use_worktrees,
        auto_merge_clean_prs=auto_merge_clean_prs,
        workspace_root=workspace_root,
    )


class _SweepThresholds(NamedTuple):
    same_file_min_prs: int
    same_file_window_hours: float
    watch_mode: bool
    idle_exit_threshold: int


def _resolve_sweep_thresholds(
    toml_overrides: Mapping[str, Any], source_label: str
) -> _SweepThresholds:
    same_file_min_prs = _resolve_int(
        name="same_file_min_prs",
        env_name="RALPH_SAME_FILE_MIN_PRS",
        toml_value=toml_overrides.get("same_file_min_prs"),
        default=DEFAULT_SAME_FILE_MIN_PRS,
        source_label=source_label,
    )
    if same_file_min_prs <= 0:
        raise ConfigError(
            f"{source_label}: same_file_min_prs must be positive (got {same_file_min_prs})"
        )
    same_file_window_hours = _resolve_float(
        name="same_file_window_hours",
        env_name="RALPH_SAME_FILE_WINDOW_HOURS",
        toml_value=toml_overrides.get("same_file_window_hours"),
        default=DEFAULT_SAME_FILE_WINDOW_HOURS,
        source_label=source_label,
    )
    if same_file_window_hours <= 0:
        raise ConfigError(
            f"{source_label}: same_file_window_hours must be positive "
            f"(got {same_file_window_hours})"
        )
    watch_mode = _resolve_bool(
        name="watch_mode",
        env_name="RALPH_WATCH_MODE",
        toml_value=toml_overrides.get("watch_mode"),
        default=DEFAULT_WATCH_MODE,
        source_label=source_label,
    )
    idle_exit_threshold = _resolve_int(
        name="idle_exit_threshold",
        env_name="RALPH_IDLE_EXIT_THRESHOLD",
        toml_value=toml_overrides.get("idle_exit_threshold"),
        default=DEFAULT_IDLE_EXIT_THRESHOLD,
        source_label=source_label,
    )
    if idle_exit_threshold <= 0:
        raise ConfigError(
            f"{source_label}: idle_exit_threshold must be positive (got {idle_exit_threshold})"
        )
    return _SweepThresholds(
        same_file_min_prs=same_file_min_prs,
        same_file_window_hours=same_file_window_hours,
        watch_mode=watch_mode,
        idle_exit_threshold=idle_exit_threshold,
    )


def _resolve_instance_id_setting(toml_overrides: Mapping[str, Any], source_label: str) -> str:
    # Multi-ralph (Scope 1) instance_id. The CLI flag layer plugs in via
    # ``ralph_executor.cli._apply_overrides`` (added in a follow-up task);
    # at the ``load_config`` level we cover env + TOML + hostname so the
    # default N=1 operator never has to set anything.
    env_instance_id = os.environ.get("RALPH_INSTANCE_ID")
    if env_instance_id is not None and env_instance_id.strip() == "":
        env_instance_id = None
    toml_instance_id_raw = toml_overrides.get("instance_id")
    if toml_instance_id_raw is not None and not isinstance(toml_instance_id_raw, str):
        raise ConfigError(
            f"{source_label}: instance_id must be a string, "
            f"got {type(toml_instance_id_raw).__name__}"
        )
    return resolve_instance_id(
        cli_value=None,
        env_value=env_instance_id,
        toml_value=toml_instance_id_raw,
        hostname=socket.gethostname(),
    )


class _AutobugSettings(NamedTuple):
    autobug_enabled: bool
    autobug_rate_max: int
    autobug_rate_window_minutes: int
    autobug_dedup_done_window_days: int
    autobug_severity_python_crash: str
    autobug_severity_subprocess_crash: str


def _resolve_autobug(toml_overrides: Mapping[str, Any], source_label: str) -> _AutobugSettings:
    autobug_enabled = _resolve_bool(
        name="autobug_enabled",
        env_name="RALPH_AUTOBUG_ENABLED",
        toml_value=toml_overrides.get("autobug_enabled"),
        default=DEFAULT_AUTOBUG_ENABLED,
        source_label=source_label,
    )
    autobug_rate_max = _resolve_int(
        name="autobug_rate_max",
        env_name="RALPH_AUTOBUG_RATE_MAX",
        toml_value=toml_overrides.get("autobug_rate_max"),
        default=DEFAULT_AUTOBUG_RATE_MAX,
        source_label=source_label,
    )
    if autobug_rate_max <= 0:
        raise ConfigError(
            f"{source_label}: autobug_rate_max must be positive (got {autobug_rate_max})"
        )
    autobug_rate_window_minutes = _resolve_int(
        name="autobug_rate_window_minutes",
        env_name="RALPH_AUTOBUG_RATE_WINDOW_MINUTES",
        toml_value=toml_overrides.get("autobug_rate_window_minutes"),
        default=DEFAULT_AUTOBUG_RATE_WINDOW_MINUTES,
        source_label=source_label,
    )
    if autobug_rate_window_minutes <= 0:
        raise ConfigError(
            f"{source_label}: autobug_rate_window_minutes must be positive "
            f"(got {autobug_rate_window_minutes})"
        )
    autobug_dedup_done_window_days = _resolve_int(
        name="autobug_dedup_done_window_days",
        env_name="RALPH_AUTOBUG_DEDUP_DONE_WINDOW_DAYS",
        toml_value=toml_overrides.get("autobug_dedup_done_window_days"),
        default=DEFAULT_AUTOBUG_DEDUP_DONE_WINDOW_DAYS,
        source_label=source_label,
    )
    if autobug_dedup_done_window_days <= 0:
        raise ConfigError(
            f"{source_label}: autobug_dedup_done_window_days must be positive "
            f"(got {autobug_dedup_done_window_days})"
        )
    autobug_severity_python_crash = _resolve_str(
        name="autobug_severity_python_crash",
        env_name="RALPH_AUTOBUG_SEVERITY_PYTHON_CRASH",
        toml_value=toml_overrides.get("autobug_severity_python_crash"),
        default=DEFAULT_AUTOBUG_SEVERITY_PYTHON_CRASH,
        source_label=source_label,
    )
    autobug_severity_subprocess_crash = _resolve_str(
        name="autobug_severity_subprocess_crash",
        env_name="RALPH_AUTOBUG_SEVERITY_SUBPROCESS_CRASH",
        toml_value=toml_overrides.get("autobug_severity_subprocess_crash"),
        default=DEFAULT_AUTOBUG_SEVERITY_SUBPROCESS_CRASH,
        source_label=source_label,
    )
    return _AutobugSettings(
        autobug_enabled=autobug_enabled,
        autobug_rate_max=autobug_rate_max,
        autobug_rate_window_minutes=autobug_rate_window_minutes,
        autobug_dedup_done_window_days=autobug_dedup_done_window_days,
        autobug_severity_python_crash=autobug_severity_python_crash,
        autobug_severity_subprocess_crash=autobug_severity_subprocess_crash,
    )


def load_config() -> ExecutorConfig:
    """Read defaults < ``~/.ralph/config.toml`` < env, and validate.

    The executor is queue-driven: ``ExecutorConfig`` has no
    ``repo_path``. ``workspace_root`` is the only configured root; every
    per-iteration target clone is materialised under
    ``<workspace_root>/clones/<owner>/<name>/`` from the active PBI's
    ``target_repo`` frontmatter.

    ``ANTHROPIC_API_KEY`` is optional and env-only by policy (secret);
    empty string means "use claude CLI's OAuth session".
    """
    from ralph_executor.user_config import (
        _warn_stale_ralph_home_in_user_config,
        user_config_path,
    )

    _warn_stale_ralph_home_in_user_config()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    toml_overrides = _load_user_toml_overrides()
    source_label = str(user_config_path())

    queue_repo = _resolve_queue_repo(toml_overrides, source_label)
    main_branch, queue_branch = _resolve_branches(toml_overrides, source_label)
    max_attempts, log_level, iteration_sleep_seconds = _resolve_runtime_knobs(
        toml_overrides, source_label
    )
    claude_binary, claude_permission_mode = _resolve_claude_settings(toml_overrides, source_label)
    git_host, gh_owner, ado_org_url, ado_project, halt_webhook = _resolve_host_settings(
        toml_overrides, source_label
    )
    (
        bot_author_email,
        stale_days,
        bash_max_timeout_ms,
        claude_session_timeout_seconds,
        pr_check_poll_max_attempts,
        pr_check_poll_interval_seconds,
    ) = _resolve_sweep_tuning(toml_overrides, source_label)
    use_worktrees, auto_merge_clean_prs, workspace_root = _resolve_workspace_settings(
        toml_overrides, source_label
    )
    (
        same_file_min_prs,
        same_file_window_hours,
        watch_mode,
        idle_exit_threshold,
    ) = _resolve_sweep_thresholds(toml_overrides, source_label)

    instance_id = _resolve_instance_id_setting(toml_overrides, source_label)
    (
        autobug_enabled,
        autobug_rate_max,
        autobug_rate_window_minutes,
        autobug_dedup_done_window_days,
        autobug_severity_python_crash,
        autobug_severity_subprocess_crash,
    ) = _resolve_autobug(toml_overrides, source_label)

    return ExecutorConfig(
        queue_repo=queue_repo,
        queue_branch=queue_branch,
        main_branch=main_branch,
        max_attempts=max_attempts,
        log_level=log_level,
        iteration_sleep_seconds=iteration_sleep_seconds,
        claude_binary=claude_binary,
        claude_permission_mode=claude_permission_mode,
        anthropic_api_key=anthropic_key,
        git_host=git_host,
        gh_owner=gh_owner,
        ado_org_url=ado_org_url,
        ado_project=ado_project,
        halt_webhook=halt_webhook,
        pr_check_poll_max_attempts=pr_check_poll_max_attempts,
        pr_check_poll_interval_seconds=pr_check_poll_interval_seconds,
        instance_id=instance_id,
        use_worktrees=use_worktrees,
        bot_author_email=bot_author_email,
        stale_days=stale_days,
        bash_max_timeout_ms=bash_max_timeout_ms,
        claude_session_timeout_seconds=claude_session_timeout_seconds,
        auto_merge_clean_prs=auto_merge_clean_prs,
        workspace_root=workspace_root,
        same_file_min_prs=same_file_min_prs,
        same_file_window_hours=same_file_window_hours,
        watch_mode=watch_mode,
        idle_exit_threshold=idle_exit_threshold,
        autobug_enabled=autobug_enabled,
        autobug_rate_max=autobug_rate_max,
        autobug_rate_window_minutes=autobug_rate_window_minutes,
        autobug_dedup_done_window_days=autobug_dedup_done_window_days,
        autobug_severity_python_crash=autobug_severity_python_crash,
        autobug_severity_subprocess_crash=autobug_severity_subprocess_crash,
    )
