"""Tests for ``ralph_executor.config``.

After KILL-RALPH-HOME T8, ``ExecutorConfig`` no longer carries
``repo_path`` and ``load_config`` no longer reads project TOML — every
TOML knob lives at the user level ``~/.ralph/config.toml``. These tests
seed that file via the ``_seed_user_config_toml`` helper and rely on a
monkeypatched ``HOME`` / ``USERPROFILE`` so reads land on the temp dir.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import pytest

from ralph_executor.config import (
    ConfigError,
    load_config,
    resolve_instance_id,
    sanitise_hostname,
    validate_instance_id,
)

QUEUE_REPO_URL = "https://github.com/example/queue"


def _seed_user_config_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    **kwargs: str,
) -> Path:
    """Seed ``~/.ralph/config.toml`` inside a monkeypatched HOME.

    ``kwargs`` hold the keys to write (``queue_repo``, ``queue_branch``,
    ``workspace_root``, ...). Values are emitted as TOML literal strings
    (single-quoted) so Windows backslashes survive without escape
    processing. Returns the config file path so callers can assert on
    its contents.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cfg_dir = tmp_path / ".ralph"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{k} = '{v}'" for k, v in kwargs.items()]
    cfg_file = cfg_dir / "config.toml"
    cfg_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg_file


@pytest.fixture
def env_minimal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Seed user TOML with a valid queue_repo + workspace_root.

    Returns ``tmp_path`` (the monkeypatched HOME) for callers that need
    to layer on more files.
    """
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo=QUEUE_REPO_URL,
        workspace_root=str(ws_parent / "ws"),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    for var in (
        "RALPH_MAIN_BRANCH",
        "RALPH_MAX_ATTEMPTS",
        "RALPH_LOG_LEVEL",
        "RALPH_ITERATION_SLEEP_SECONDS",
        "RALPH_CLAUDE_BINARY",
        "RALPH_CLAUDE_PERMISSION_MODE",
        "RALPH_USE_WORKTREES",
        "RALPH_AUTO_MERGE_CLEAN_PRS",
        "RALPH_WORKSPACE",
        "RALPH_QUEUE_BRANCH",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_load_config_uses_defaults(env_minimal: Path) -> None:
    cfg = load_config()
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
    # KILL-RALPH-HOME T8 deleted the field; assert its absence so a
    # future regression that re-adds it is caught at type-check time.
    assert "repo_path" not in names


def test_load_config_rejects_missing_queue_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """queue_repo is required. Missing TOML key → ConfigError."""
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        main_branch="main",
        workspace_root=str(ws_parent / "ws"),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with pytest.raises(ConfigError, match="queue_repo"):
        load_config()


def test_load_config_rejects_bad_queue_repo_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo="ftp://example.com/queue",
        workspace_root=str(ws_parent / "ws"),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with pytest.raises(ConfigError, match="queue_repo"):
        load_config()


def test_load_config_accepts_queue_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo=QUEUE_REPO_URL,
        workspace_root=str(ws_parent / "ws"),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
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


def test_load_config_missing_anthropic_key_is_optional(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ANTHROPIC_API_KEY is optional — claude -p falls back to OAuth.
    Missing key must NOT raise; cfg.anthropic_api_key is the empty
    string and claude_spawn skips propagating it.
    """
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo=QUEUE_REPO_URL,
        workspace_root=str(ws_parent / "ws"),
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = load_config()
    assert cfg.anthropic_api_key == ""


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """TOML ``use_worktrees = false`` is rejected the same as the env var."""
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cfg_dir = tmp_path / ".ralph"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(
        "queue_repo = 'https://github.com/example/queue'\n"
        f"workspace_root = '{ws_parent / 'ws'}'\n"
        "use_worktrees = false\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("RALPH_USE_WORKTREES", raising=False)
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


def test_default_queue_branch_is_ralph_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default queue_branch is 'ralph-queue' when no TOML / env override."""
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo="https://github.com/test/queue",
        workspace_root=str(ws_parent / "ws"),
    )
    monkeypatch.delenv("RALPH_QUEUE_BRANCH", raising=False)

    cfg = load_config()
    assert cfg.queue_branch == "ralph-queue"


def test_queue_branch_toml_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """queue_branch in user TOML overrides the default."""
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo="https://github.com/test/queue",
        workspace_root=str(ws_parent / "ws"),
        queue_branch="custom-branch",
    )
    monkeypatch.delenv("RALPH_QUEUE_BRANCH", raising=False)

    cfg = load_config()
    assert cfg.queue_branch == "custom-branch"


def test_queue_branch_env_override_beats_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RALPH_QUEUE_BRANCH env var overrides TOML."""
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo="https://github.com/test/queue",
        workspace_root=str(ws_parent / "ws"),
        queue_branch="toml-value",
    )
    monkeypatch.setenv("RALPH_QUEUE_BRANCH", "env-value")

    cfg = load_config()
    assert cfg.queue_branch == "env-value"


@pytest.mark.parametrize("bad_value", ["", "   ", "HEAD", "refs/heads/foo"])
def test_queue_branch_rejects_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_value: str
) -> None:
    """Empty / HEAD / refs-prefixed branch names raise ConfigError."""
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo="https://github.com/test/queue",
        workspace_root=str(ws_parent / "ws"),
        queue_branch=bad_value,
    )
    monkeypatch.delenv("RALPH_QUEUE_BRANCH", raising=False)

    with pytest.raises(ConfigError, match="queue_branch"):
        load_config()


def test_load_config_rejects_missing_workspace_root_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """workspace_root parent must exist — a typo surfaces here, not deep in ensure_clone."""
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo=QUEUE_REPO_URL,
        workspace_root=str(tmp_path / "no" / "such" / "parent" / "ws"),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with pytest.raises(ConfigError, match="workspace_root parent"):
        load_config()


# ---------------------------------------------------------------------------
# Multi-ralph (Scope 1) — instance_id validator + resolver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good",
    ["a", "ralph-a", "box1", "ralph_a", "0", "a" * 63],
)
def test_validate_instance_id_accepts(good: str) -> None:
    """Filesystem-safe lowercase 1-63 chars, leading alnum, ``[a-z0-9_-]`` follow-on."""
    validate_instance_id(good)


@pytest.mark.parametrize(
    "bad",
    ["", "A", "Ralph", "1ralph-A", "-ralph", "_ralph", "ralph!", "a" * 64, "ralph.a", "ralph a"],
)
def test_validate_instance_id_rejects(bad: str) -> None:
    """Empty, uppercase, leading non-alnum, oversize, or disallowed chars → ConfigError."""
    with pytest.raises(ConfigError, match="instance_id"):
        validate_instance_id(bad)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Box-1", "box-1"),
        ("HOST.local", "host-local"),
        ("Mixed_Case-123", "mixed_case-123"),
        ("a", "a"),
        ("UPPER spaces!", "upper-spaces-"),
    ],
)
def test_sanitise_hostname_replaces_disallowed(raw: str, expected: str) -> None:
    """Lowercase + replace non-``[a-z0-9_-]`` chars with ``-``."""
    assert sanitise_hostname(raw) == expected


def test_resolve_instance_id_cli_wins() -> None:
    """CLI flag wins over env / TOML / hostname."""
    assert (
        resolve_instance_id(
            cli_value="cli-id",
            env_value="env-id",
            toml_value="toml-id",
            hostname="host-id",
        )
        == "cli-id"
    )


def test_resolve_instance_id_env_beats_toml_and_hostname() -> None:
    assert (
        resolve_instance_id(
            cli_value=None,
            env_value="env-id",
            toml_value="toml-id",
            hostname="host-id",
        )
        == "env-id"
    )


def test_resolve_instance_id_toml_beats_hostname() -> None:
    assert (
        resolve_instance_id(
            cli_value=None,
            env_value=None,
            toml_value="toml-id",
            hostname="host-id",
        )
        == "toml-id"
    )


def test_resolve_instance_id_falls_back_to_sanitised_hostname() -> None:
    assert (
        resolve_instance_id(
            cli_value=None,
            env_value=None,
            toml_value=None,
            hostname="Box.LOCAL",
        )
        == "box-local"
    )


def test_resolve_instance_id_empty_cli_value_is_not_skipped() -> None:
    """Per the truthiness-vs-None rule: ``--instance-id ""`` must reach
    the validator, not silently fall through to env / TOML / hostname."""
    with pytest.raises(ConfigError, match="instance_id"):
        resolve_instance_id(
            cli_value="",
            env_value="env-id",
            toml_value="toml-id",
            hostname="host-id",
        )


def test_resolve_instance_id_raises_when_hostname_empty() -> None:
    """No CLI / env / TOML and an empty hostname → ConfigError (the
    operator needs to set ``instance_id`` explicitly)."""
    with pytest.raises(ConfigError, match="could not derive instance_id"):
        resolve_instance_id(
            cli_value=None,
            env_value=None,
            toml_value=None,
            hostname="",
        )


def test_executor_config_requires_instance_id_field() -> None:
    """``instance_id`` is a non-Optional dataclass field on ExecutorConfig."""
    from dataclasses import fields

    from ralph_executor.config import ExecutorConfig

    names = {f.name for f in fields(ExecutorConfig)}
    assert "instance_id" in names
    # The field must not have a default — multi-ralph relies on the
    # resolver supplying a validated value at every construction site.
    inst_field = next(f for f in fields(ExecutorConfig) if f.name == "instance_id")
    from dataclasses import MISSING

    assert inst_field.default is MISSING
    assert inst_field.default_factory is MISSING


def test_load_config_instance_id_defaults_to_sanitised_hostname(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    """No CLI / env / TOML — load_config falls back to the sanitised hostname."""
    monkeypatch.delenv("RALPH_INSTANCE_ID", raising=False)
    monkeypatch.setattr("ralph_executor.config.socket.gethostname", lambda: "Box.LOCAL")
    cfg = load_config()
    assert cfg.instance_id == "box-local"


def test_load_config_instance_id_from_env(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    """``RALPH_INSTANCE_ID`` beats TOML and hostname."""
    monkeypatch.setenv("RALPH_INSTANCE_ID", "ralph-from-env")
    cfg = load_config()
    assert cfg.instance_id == "ralph-from-env"


def test_load_config_instance_id_from_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``instance_id`` in user TOML is honoured."""
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo=QUEUE_REPO_URL,
        workspace_root=str(ws_parent / "ws"),
        instance_id="ralph-from-toml",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.delenv("RALPH_INSTANCE_ID", raising=False)
    cfg = load_config()
    assert cfg.instance_id == "ralph-from-toml"


def test_load_config_instance_id_env_beats_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo=QUEUE_REPO_URL,
        workspace_root=str(ws_parent / "ws"),
        instance_id="ralph-toml",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("RALPH_INSTANCE_ID", "ralph-env")
    cfg = load_config()
    assert cfg.instance_id == "ralph-env"


def test_load_config_instance_id_invalid_env_rejected(
    monkeypatch: pytest.MonkeyPatch, env_minimal: Path
) -> None:
    """An env value that fails the validator surfaces as ConfigError."""
    monkeypatch.setenv("RALPH_INSTANCE_ID", "BAD CHARS")
    with pytest.raises(ConfigError, match="instance_id"):
        load_config()


def test_load_config_instance_id_invalid_toml_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo=QUEUE_REPO_URL,
        workspace_root=str(ws_parent / "ws"),
        instance_id="Bad.Value",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.delenv("RALPH_INSTANCE_ID", raising=False)
    with pytest.raises(ConfigError, match="instance_id"):
        load_config()


def test_load_config_instance_id_non_string_toml_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-string TOML value (int, bool) is rejected with a typed message."""
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cfg_dir = tmp_path / ".ralph"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(
        f"queue_repo = '{QUEUE_REPO_URL}'\n"
        f"workspace_root = '{ws_parent / 'ws'}'\n"
        "instance_id = 42\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.delenv("RALPH_INSTANCE_ID", raising=False)
    with pytest.raises(ConfigError, match="instance_id must be a string"):
        load_config()


def test_load_config_instance_id_empty_env_falls_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty env value is treated as 'not set' and falls through to
    the next layer (TOML / hostname). This mirrors how the other env
    knobs in ``load_config`` treat empty strings."""
    ws_parent = tmp_path / "ws-parent"
    ws_parent.mkdir()
    _seed_user_config_toml(
        tmp_path,
        monkeypatch,
        queue_repo=QUEUE_REPO_URL,
        workspace_root=str(ws_parent / "ws"),
        instance_id="ralph-from-toml",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("RALPH_INSTANCE_ID", "")
    cfg = load_config()
    assert cfg.instance_id == "ralph-from-toml"
