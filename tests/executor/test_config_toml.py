"""Tests for the TOML override layer in ``ralph_executor.config``.

After KILL-RALPH-HOME T8, ``load_config`` reads ``~/.ralph/config.toml``
exclusively (project-TOML is gone). The fixture monkeypatches
``HOME`` / ``USERPROFILE`` so the loader lands on the temp dir.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ralph_executor.config import ConfigError, load_config

QUEUE_REPO_URL = "https://github.com/example/queue"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """User TOML seeded with queue_repo; every overridable env var removed.

    Returns the monkeypatched HOME dir. Tests overwrite the user TOML via
    ``_write_toml`` / ``_write_raw_toml``.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg_dir = tmp_path / ".ralph"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(
        f"queue_repo = '{QUEUE_REPO_URL}'\n",
        encoding="utf-8",
    )
    for var in (
        "RALPH_MAIN_BRANCH",
        "RALPH_MAX_ATTEMPTS",
        "RALPH_LOG_LEVEL",
        "RALPH_ITERATION_SLEEP_SECONDS",
        "RALPH_CLAUDE_BINARY",
        "RALPH_CLAUDE_PERMISSION_MODE",
        "RALPH_GIT_HOST",
        "GH_OWNER",
        "ADO_ORG_URL",
        "ADO_PROJECT",
        "RALPH_HALT_WEBHOOK",
        "RALPH_PR_CHECK_POLL_MAX_ATTEMPTS",
        "RALPH_PR_CHECK_POLL_INTERVAL_SECONDS",
        "RALPH_USE_WORKTREES",
        "RALPH_ADO_AUTHOR_EMAIL",
        "RALPH_STALE_DAYS",
        "BASH_MAX_TIMEOUT_MS",
        "RALPH_CLAUDE_SESSION_TIMEOUT_SECONDS",
        "RALPH_AUTO_MERGE_CLEAN_PRS",
        "RALPH_WORKSPACE",
        "RALPH_QUEUE_BRANCH",
        "RALPH_SAME_FILE_MIN_PRS",
        "RALPH_SAME_FILE_WINDOW_HOURS",
        "RALPH_INSTANCE_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _write_toml(home: Path, body: str) -> Path:
    """Write ``~/.ralph/config.toml`` ensuring queue_repo is present.

    queue_repo is required by load_config; this helper prepends a default
    queue_repo line so callers can focus their TOML body on the keys they
    care about. queue_repo uses a TOML literal-string (single-quoted) so
    Windows backslashes inside string values supplied by callers don't
    accidentally trigger TOML escape processing. Tests that need to test
    queue_repo-specific behavior or malformed TOML use ``_write_raw_toml``
    instead.
    """
    cfg_file = home / ".ralph" / "config.toml"
    cfg_file.write_text(
        f"queue_repo = '{QUEUE_REPO_URL}'\n" + body,
        encoding="utf-8",
    )
    return cfg_file


def _write_raw_toml(home: Path, body: str) -> Path:
    cfg_file = home / ".ralph" / "config.toml"
    cfg_file.write_text(body, encoding="utf-8")
    return cfg_file


def test_missing_toml_raises_for_missing_queue_repo(clean_env: Path) -> None:
    """No config.toml -> ConfigError because queue_repo is now required."""
    (clean_env / ".ralph" / "config.toml").unlink()
    with pytest.raises(ConfigError, match="queue_repo"):
        load_config()


def test_toml_overrides_defaults(clean_env: Path) -> None:
    _write_toml(
        clean_env,
        """
        main_branch = "trunk"
        max_attempts = 7
        iteration_sleep_seconds = 1.5
        claude_binary = "/opt/claude/bin/claude"
        log_level = "DEBUG"
        """,
    )
    cfg = load_config()
    assert cfg.queue_repo == QUEUE_REPO_URL
    assert cfg.main_branch == "trunk"
    assert cfg.max_attempts == 7
    assert cfg.iteration_sleep_seconds == 1.5
    assert cfg.claude_binary == "/opt/claude/bin/claude"
    assert cfg.log_level == logging.DEBUG


def test_env_wins_over_toml(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_toml(clean_env, 'main_branch = "from-toml"\nmax_attempts = 7\n')
    monkeypatch.setenv("RALPH_MAIN_BRANCH", "from-env")
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "99")
    cfg = load_config()
    assert cfg.main_branch == "from-env"
    assert cfg.max_attempts == 99


def test_toml_partial_override_falls_back_to_defaults(clean_env: Path) -> None:
    """Knobs absent from TOML fall through to defaults."""
    _write_toml(clean_env, "max_attempts = 4\n")
    cfg = load_config()
    assert cfg.max_attempts == 4
    assert cfg.main_branch == "main"  # default kept
    assert cfg.iteration_sleep_seconds == 30.0


def test_unknown_toml_key_is_warned_and_ignored(
    clean_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_toml(clean_env, "mystery_knob = 42\n")
    with caplog.at_level(logging.WARNING, logger="ralph_executor.config"):
        cfg = load_config()
    assert cfg.queue_repo == QUEUE_REPO_URL
    assert any("mystery_knob" in rec.message for rec in caplog.records)


def test_malformed_toml_raises(clean_env: Path) -> None:
    _write_raw_toml(clean_env, "main_branch = ===bogus===\n")
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
    _write_toml(clean_env, "main_branch = 42\n")
    with pytest.raises(ConfigError, match="main_branch must be a string"):
        load_config()


def test_queue_repo_picked_up_from_toml(clean_env: Path) -> None:
    """queue_repo flows from TOML into ExecutorConfig."""
    _write_raw_toml(
        clean_env,
        'queue_repo = "https://github.com/operator/my-queue"\n',
    )
    cfg = load_config()
    assert cfg.queue_repo == "https://github.com/operator/my-queue"


def test_queue_repo_required_missing_raises(clean_env: Path) -> None:
    _write_raw_toml(clean_env, "main_branch = 'main'\n")
    with pytest.raises(ConfigError, match="queue_repo"):
        load_config()


def test_queue_repo_invalid_url_raises(clean_env: Path) -> None:
    _write_raw_toml(clean_env, 'queue_repo = "ftp://example.com/queue"\n')
    with pytest.raises(ConfigError, match="queue_repo"):
        load_config()


def test_queue_repo_wrong_type_raises(clean_env: Path) -> None:
    _write_raw_toml(clean_env, "queue_repo = 42\n")
    with pytest.raises(ConfigError, match="queue_repo must be a string"):
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


def test_claude_permission_mode_default(clean_env: Path) -> None:
    """No TOML key + no env -> bypassPermissions default. The executor
    spawns claude non-interactively and cannot answer permission prompts."""
    cfg = load_config()
    assert cfg.claude_permission_mode == "bypassPermissions"


def test_claude_permission_mode_picked_up_from_toml(clean_env: Path) -> None:
    """Operators pinning a stricter mode (e.g. ``acceptEdits`` or
    ``plan``) for a particular project can do so via TOML rather than
    exporting env every shell."""
    _write_toml(clean_env, 'claude_permission_mode = "acceptEdits"\n')
    cfg = load_config()
    assert cfg.claude_permission_mode == "acceptEdits"


def test_env_claude_permission_mode_wins_over_toml(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_toml(clean_env, 'claude_permission_mode = "acceptEdits"\n')
    monkeypatch.setenv("RALPH_CLAUDE_PERMISSION_MODE", "plan")
    cfg = load_config()
    assert cfg.claude_permission_mode == "plan"


def test_invalid_claude_permission_mode_in_toml_raises(clean_env: Path) -> None:
    _write_toml(clean_env, 'claude_permission_mode = "yolo"\n')
    with pytest.raises(ConfigError, match="claude_permission_mode"):
        load_config()


def test_git_host_empty_when_neither_set(clean_env: Path) -> None:
    """Empty string is the "not set" sentinel — host_select then errors
    pointing at both knobs."""
    cfg = load_config()
    assert cfg.git_host == ""


def test_promoted_keys_picked_up_from_toml(clean_env: Path) -> None:
    """gh_owner / ado_org_url / ado_project / halt_webhook flow from
    TOML into ExecutorConfig — removes the need to export them per shell."""
    _write_toml(
        clean_env,
        """
        gh_owner = "emp3thy"
        ado_org_url = "https://dev.azure.com/example"
        ado_project = "example-proj"
        halt_webhook = "https://hooks.example/halt"
        """,
    )
    cfg = load_config()
    assert cfg.gh_owner == "emp3thy"
    assert cfg.ado_org_url == "https://dev.azure.com/example"
    assert cfg.ado_project == "example-proj"
    assert cfg.halt_webhook == "https://hooks.example/halt"


def test_env_wins_over_toml_for_promoted_keys(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_toml(clean_env, 'gh_owner = "from-toml"\nado_project = "from-toml-ado"\n')
    monkeypatch.setenv("GH_OWNER", "from-env")
    monkeypatch.setenv("ADO_PROJECT", "from-env-ado")
    cfg = load_config()
    assert cfg.gh_owner == "from-env"
    assert cfg.ado_project == "from-env-ado"


def test_promoted_keys_empty_when_neither_set(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("GH_OWNER", "ADO_ORG_URL", "ADO_PROJECT", "RALPH_HALT_WEBHOOK"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.gh_owner == ""
    assert cfg.ado_org_url == ""
    assert cfg.ado_project == ""
    assert cfg.halt_webhook == ""


def test_pr_check_poll_defaults_when_neither_set(clean_env: Path) -> None:
    """Plan 18 Task 6: defaults match the verifier's 6 × 30 s budget."""
    cfg = load_config()
    assert cfg.pr_check_poll_max_attempts == 6
    assert cfg.pr_check_poll_interval_seconds == 30.0


def test_pr_check_poll_knobs_picked_up_from_toml(clean_env: Path) -> None:
    _write_toml(
        clean_env,
        """
        pr_check_poll_max_attempts = 12
        pr_check_poll_interval_seconds = 5.0
        """,
    )
    cfg = load_config()
    assert cfg.pr_check_poll_max_attempts == 12
    assert cfg.pr_check_poll_interval_seconds == 5.0


def test_env_wins_over_toml_for_pr_check_poll_knobs(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_toml(
        clean_env,
        "pr_check_poll_max_attempts = 12\npr_check_poll_interval_seconds = 5.0\n",
    )
    monkeypatch.setenv("RALPH_PR_CHECK_POLL_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("RALPH_PR_CHECK_POLL_INTERVAL_SECONDS", "0.25")
    cfg = load_config()
    assert cfg.pr_check_poll_max_attempts == 3
    assert cfg.pr_check_poll_interval_seconds == 0.25


def test_use_worktrees_default_true(clean_env: Path) -> None:
    cfg = load_config()
    assert cfg.use_worktrees is True


def test_use_worktrees_toml_false(clean_env: Path) -> None:
    """``use_worktrees = false`` is rejected outright after the queue-repo
    split — the single-checkout branch-dance model is gone."""
    _write_toml(clean_env, "use_worktrees = false\n")
    with pytest.raises(ConfigError, match="use_worktrees=False is no longer supported"):
        load_config()


def test_use_worktrees_env_wins_over_toml(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env override flips TOML ``false`` back to ``true`` — both surfaces
    feed the same resolver, so the override path stays exercised."""
    _write_toml(clean_env, "use_worktrees = false\n")
    monkeypatch.setenv("RALPH_USE_WORKTREES", "true")
    cfg = load_config()
    assert cfg.use_worktrees is True


def test_use_worktrees_toml_wrong_type_raises(clean_env: Path) -> None:
    _write_toml(clean_env, 'use_worktrees = "yes"\n')
    with pytest.raises(ConfigError, match="use_worktrees must be a boolean"):
        load_config()


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
    assert cfg.queue_repo == QUEUE_REPO_URL
    assert any("entries" in rec.message for rec in caplog.records)


def test_sweep_knobs_picked_up_from_toml(clean_env: Path) -> None:
    """bot_author_email + stale_days flow from TOML into ExecutorConfig
    (the env-var read in loop._run_sweep is being retired)."""
    _write_toml(
        clean_env,
        """
        bot_author_email = "ralph-bot@example.com"
        stale_days = 7
        """,
    )
    cfg = load_config()
    assert cfg.bot_author_email == "ralph-bot@example.com"
    assert cfg.stale_days == 7


def test_env_wins_over_toml_for_sweep_knobs(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env names keep the historical ``RALPH_ADO_AUTHOR_EMAIL`` /
    ``RALPH_STALE_DAYS`` spelling for backwards compatibility — operators
    with these set today see no change."""
    _write_toml(
        clean_env,
        """
        bot_author_email = "from-toml@example.com"
        stale_days = 3
        """,
    )
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "from-env@example.com")
    monkeypatch.setenv("RALPH_STALE_DAYS", "10")
    cfg = load_config()
    assert cfg.bot_author_email == "from-env@example.com"
    assert cfg.stale_days == 10


def test_sweep_knobs_defaults_when_neither_set(clean_env: Path) -> None:
    cfg = load_config()
    assert cfg.bot_author_email == ""
    assert cfg.stale_days == 3


def test_env_string_value_is_stripped(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``_resolve_str`` must strip surrounding whitespace before storing
    the env value. Otherwise a value like ``' ralph@bot.com '`` survives
    the truthiness guard in consumers and downstream string-equality
    comparisons (PR-author matching in the sweep) silently fail."""
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "  ralph@bot.com  ")
    cfg = load_config()
    assert cfg.bot_author_email == "ralph@bot.com"


def test_stale_days_rejected_when_zero_in_toml(clean_env: Path) -> None:
    _write_toml(clean_env, "stale_days = 0\n")
    with pytest.raises(ConfigError, match="stale_days must be positive"):
        load_config()


def test_stale_days_rejected_when_negative_in_env(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RALPH_STALE_DAYS", "-1")
    with pytest.raises(ConfigError, match="stale_days must be positive"):
        load_config()


def test_bash_max_timeout_ms_from_toml(clean_env: Path) -> None:
    _write_toml(clean_env, "bash_max_timeout_ms = 1200000\n")
    cfg = load_config()
    assert cfg.bash_max_timeout_ms == 1_200_000


def test_bash_max_timeout_ms_env_wins(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_toml(clean_env, "bash_max_timeout_ms = 1200000\n")
    monkeypatch.setenv("BASH_MAX_TIMEOUT_MS", "600000")
    cfg = load_config()
    assert cfg.bash_max_timeout_ms == 600_000


def test_bash_max_timeout_ms_default(clean_env: Path) -> None:
    cfg = load_config()
    assert cfg.bash_max_timeout_ms == 900_000


def test_bash_max_timeout_ms_rejected_when_zero(clean_env: Path) -> None:
    _write_toml(clean_env, "bash_max_timeout_ms = 0\n")
    with pytest.raises(ConfigError, match="bash_max_timeout_ms must be positive"):
        load_config()


def test_bash_max_timeout_ms_rejected_when_negative_in_env(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASH_MAX_TIMEOUT_MS", "-1")
    with pytest.raises(ConfigError, match="bash_max_timeout_ms must be positive"):
        load_config()


def test_claude_session_timeout_seconds_default(clean_env: Path) -> None:
    cfg = load_config()
    assert cfg.claude_session_timeout_seconds == 1200


def test_claude_session_timeout_seconds_from_toml(clean_env: Path) -> None:
    _write_toml(clean_env, "claude_session_timeout_seconds = 600\n")
    cfg = load_config()
    assert cfg.claude_session_timeout_seconds == 600


def test_claude_session_timeout_seconds_env_wins(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_toml(clean_env, "claude_session_timeout_seconds = 600\n")
    monkeypatch.setenv("RALPH_CLAUDE_SESSION_TIMEOUT_SECONDS", "300")
    cfg = load_config()
    assert cfg.claude_session_timeout_seconds == 300


def test_claude_session_timeout_seconds_rejected_when_zero(clean_env: Path) -> None:
    _write_toml(clean_env, "claude_session_timeout_seconds = 0\n")
    with pytest.raises(ConfigError, match="claude_session_timeout_seconds must be positive"):
        load_config()


def test_claude_session_timeout_seconds_rejected_when_negative_in_env(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RALPH_CLAUDE_SESSION_TIMEOUT_SECONDS", "-1")
    with pytest.raises(ConfigError, match="claude_session_timeout_seconds must be positive"):
        load_config()


def test_auto_merge_clean_prs_default_false(clean_env: Path) -> None:
    cfg = load_config()
    assert cfg.auto_merge_clean_prs is False


def test_auto_merge_clean_prs_toml_true(clean_env: Path) -> None:
    _write_toml(clean_env, "auto_merge_clean_prs = true\n")
    cfg = load_config()
    assert cfg.auto_merge_clean_prs is True


def test_auto_merge_clean_prs_env_wins_over_toml(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_toml(clean_env, "auto_merge_clean_prs = true\n")
    monkeypatch.setenv("RALPH_AUTO_MERGE_CLEAN_PRS", "false")
    cfg = load_config()
    assert cfg.auto_merge_clean_prs is False


def test_auto_merge_clean_prs_toml_wrong_type_raises(clean_env: Path) -> None:
    _write_toml(clean_env, 'auto_merge_clean_prs = "yes"\n')
    with pytest.raises(ConfigError, match="auto_merge_clean_prs must be a boolean"):
        load_config()


def test_workspace_root_defaults_to_home_ralph_workspaces(clean_env: Path) -> None:
    cfg = load_config()
    assert cfg.workspace_root == Path.home() / "ralph-workspaces"


def test_workspace_root_from_toml(clean_env: Path, tmp_path: Path) -> None:
    target = tmp_path / "my-workspaces"
    # Use forward-slash form so a Windows backslash isn't interpreted as a
    # TOML escape sequence (e.g. ``\U``). ``Path`` round-trips identically.
    _write_toml(clean_env, f'workspace_root = "{target.as_posix()}"\n')
    cfg = load_config()
    assert cfg.workspace_root == target


def test_workspace_root_env_wins_over_toml(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_toml(
        clean_env,
        f'workspace_root = "{(tmp_path / "from-toml").as_posix()}"\n',
    )
    monkeypatch.setenv("RALPH_WORKSPACE", str(tmp_path / "from-env"))
    cfg = load_config()
    assert cfg.workspace_root == tmp_path / "from-env"


def test_workspace_root_tilde_expansion(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """~/ in TOML or env must be expanded relative to the home dir."""
    monkeypatch.setenv("RALPH_WORKSPACE", "~/my-ralph-stuff")
    cfg = load_config()
    assert cfg.workspace_root == Path.home() / "my-ralph-stuff"


def test_workspace_root_non_string_toml_rejected(clean_env: Path) -> None:
    _write_toml(clean_env, "workspace_root = 42\n")
    with pytest.raises(ConfigError, match="workspace_root"):
        load_config()


def test_same_file_thresholds_defaults(clean_env: Path) -> None:
    cfg = load_config()
    assert cfg.same_file_min_prs == 10
    assert cfg.same_file_window_hours == 24.0


def test_same_file_thresholds_picked_up_from_toml(clean_env: Path) -> None:
    _write_toml(
        clean_env,
        """
        same_file_min_prs = 25
        same_file_window_hours = 12.0
        """,
    )
    cfg = load_config()
    assert cfg.same_file_min_prs == 25
    assert cfg.same_file_window_hours == 12.0


def test_env_wins_over_toml_for_same_file_thresholds(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_toml(
        clean_env,
        "same_file_min_prs = 25\nsame_file_window_hours = 12.0\n",
    )
    monkeypatch.setenv("RALPH_SAME_FILE_MIN_PRS", "40")
    monkeypatch.setenv("RALPH_SAME_FILE_WINDOW_HOURS", "6")
    cfg = load_config()
    assert cfg.same_file_min_prs == 40
    assert cfg.same_file_window_hours == 6.0


def test_same_file_min_prs_rejected_when_zero(clean_env: Path) -> None:
    _write_toml(clean_env, "same_file_min_prs = 0\n")
    with pytest.raises(ConfigError, match="same_file_min_prs must be positive"):
        load_config()


def test_same_file_window_hours_rejected_when_negative_in_env(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RALPH_SAME_FILE_WINDOW_HOURS", "-1")
    with pytest.raises(ConfigError, match="same_file_window_hours must be positive"):
        load_config()


# --- instance_id (MULTI-RALPH-SCOPE-1 Task 3) ----------------------------


def test_instance_id_picked_up_from_toml(clean_env: Path) -> None:
    """`instance_id = "..."` in user TOML flows into ExecutorConfig.instance_id
    (no env / no CLI). Validator passes; resolver returns the TOML value."""
    _write_toml(clean_env, 'instance_id = "ralph-from-toml"\n')
    cfg = load_config()
    assert cfg.instance_id == "ralph-from-toml"


def test_env_instance_id_wins_over_toml(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RALPH_INSTANCE_ID overrides the TOML key per the resolver precedence
    chain (CLI > env > TOML > hostname)."""
    _write_toml(clean_env, 'instance_id = "ralph-from-toml"\n')
    monkeypatch.setenv("RALPH_INSTANCE_ID", "ralph-from-env")
    cfg = load_config()
    assert cfg.instance_id == "ralph-from-env"


def test_instance_id_invalid_value_in_toml_rejected(clean_env: Path) -> None:
    """A TOML value that fails ``validate_instance_id`` raises ConfigError
    out of load_config (resolver re-validates before returning)."""
    _write_toml(clean_env, 'instance_id = "Bad.Value"\n')
    with pytest.raises(ConfigError, match="instance_id"):
        load_config()
