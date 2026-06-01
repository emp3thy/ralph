"""Tests for ``ralph_executor.cli``."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from ralph_executor import cli
from ralph_executor.config import ExecutorConfig
from ralph_executor.host_select import HostSelectionError
from ralph_executor.loop import IterationResult


@pytest.fixture(autouse=True)
def _stub_prepare_host_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: stub host-environment prep so it succeeds silently.

    Tests that exercise host-prep failure modes override this with their
    own monkeypatch.
    """
    monkeypatch.setattr(cli, "prepare_host_environment", lambda **_: "github")


@pytest.fixture(autouse=True)
def _cleanup_synced_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot + restore the env vars that ``cli._export_cfg_to_env``
    mutates directly. Production code writes them via ``os.environ[k]=v``
    which monkeypatch can't auto-undo, so tests that exercise that path
    would otherwise leak values into siblings."""
    for var in ("GH_OWNER", "ADO_ORG_URL", "ADO_PROJECT", "RALPH_HALT_WEBHOOK"):
        # Captures current value; monkeypatch will restore on teardown
        # whether or not the test mutates it.
        existing = os.environ.get(var)
        if existing is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, existing)


def test_main_runs_one_iteration_with_once_flag(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[ExecutorConfig] = []

    def _fake_iterate(cfg: ExecutorConfig) -> IterationResult:
        calls.append(cfg)
        return IterationResult(outcome="idle", pbi_id=None)

    monkeypatch.setattr(cli, "iterate_once", _fake_iterate)
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)

    exit_code = cli.main(["--once"])
    assert exit_code == 0
    assert calls == [cfg_for_repo]


def test_main_handles_keyboard_interrupt_cleanly(
    cfg_for_repo: ExecutorConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _explode(cfg: ExecutorConfig) -> IterationResult:
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "iterate_once", _explode)
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)

    exit_code = cli.main(["--once"])
    assert exit_code == 0  # graceful exit


def test_main_log_level_flag(cfg_for_repo: ExecutorConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.setattr(
        cli,
        "iterate_once",
        lambda cfg: IterationResult(outcome="idle", pbi_id=None),
    )
    cli.main(["--once", "--log-level", "DEBUG"])
    assert logging.getLogger("ralph_executor").level == logging.DEBUG


def test_main_exits_2_on_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ralph_executor.config import ConfigError

    def _explode() -> ExecutorConfig:
        raise ConfigError("queue_repo is required")

    monkeypatch.setattr(cli, "load_config", _explode)
    exit_code = cli.main(["--once"])
    assert exit_code == 2
    assert "queue_repo" in capsys.readouterr().err


def test_main_calls_prepare_host_environment_before_loop(
    cfg_for_repo: ExecutorConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup order: load_config -> prepare_host_environment -> iterate."""
    order: list[str] = []

    def _record_load() -> ExecutorConfig:
        order.append("load_config")
        return cfg_for_repo

    def _record_prepare(**_kwargs: object) -> str:
        order.append("prepare_host_environment")
        return "github"

    def _record_iterate(cfg: ExecutorConfig) -> IterationResult:
        order.append("iterate_once")
        return IterationResult(outcome="idle", pbi_id=None)

    monkeypatch.setattr(cli, "load_config", _record_load)
    monkeypatch.setattr(cli, "prepare_host_environment", _record_prepare)
    monkeypatch.setattr(cli, "iterate_once", _record_iterate)

    exit_code = cli.main(["--once"])
    assert exit_code == 0
    assert order == [
        "load_config",
        "prepare_host_environment",
        "iterate_once",
    ]


def test_main_exits_2_on_host_selection_error(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A HostSelectionError aborts before the loop and prints to stderr."""
    iterate_calls: list[ExecutorConfig] = []

    def _explode(**_kwargs: object) -> str:
        raise HostSelectionError("git host is required but unset or blank.")

    def _record(cfg: ExecutorConfig) -> IterationResult:
        iterate_calls.append(cfg)
        return IterationResult(outcome="idle", pbi_id=None)

    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.setattr(cli, "prepare_host_environment", _explode)
    monkeypatch.setattr(cli, "iterate_once", _record)

    exit_code = cli.main(["--once"])
    assert exit_code == 2
    assert iterate_calls == []  # loop never entered
    err = capsys.readouterr().err
    assert "git host" in err


def test_main_init_subcommand_writes_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`ralph-executor init --yes` writes the OS-default workspace_root."""
    from ralph_executor.setup_cmds import _default_workspace_root
    from ralph_executor.user_config import read_workspace_root

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    exit_code = cli.main(["init", "--yes"])
    assert exit_code == 0
    # --yes uses the OS default; assert the default path was written.
    assert read_workspace_root() == _default_workspace_root().resolve()
    out = capsys.readouterr().out
    assert "workspace_root" in out


def test_main_syncs_cfg_to_env_before_host_select(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`load_config` may have read project identifiers from TOML — those
    must be exported to os.environ BEFORE prepare_host_environment so
    host_select.verify_auth_env (and downstream skill scripts) see them."""
    import dataclasses as _dc

    monkeypatch.delenv("GH_OWNER", raising=False)
    monkeypatch.delenv("ADO_ORG_URL", raising=False)
    monkeypatch.delenv("ADO_PROJECT", raising=False)
    monkeypatch.delenv("RALPH_HALT_WEBHOOK", raising=False)

    cfg = _dc.replace(
        cfg_for_repo,
        gh_owner="acme",
        ado_org_url="https://dev.azure.com/acme",
        ado_project="acme-project",
        halt_webhook="https://hooks.acme/halt",
    )

    observed_env: dict[str, str | None] = {}

    def _spy_prepare(**_kwargs: object) -> str:
        observed_env["GH_OWNER"] = os.environ.get("GH_OWNER")
        observed_env["ADO_ORG_URL"] = os.environ.get("ADO_ORG_URL")
        observed_env["ADO_PROJECT"] = os.environ.get("ADO_PROJECT")
        observed_env["RALPH_HALT_WEBHOOK"] = os.environ.get("RALPH_HALT_WEBHOOK")
        return "github"

    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "prepare_host_environment", _spy_prepare)
    monkeypatch.setattr(
        cli,
        "iterate_once",
        lambda c: IterationResult(outcome="idle", pbi_id=None),
    )

    cli.main(["--once"])

    assert observed_env == {
        "GH_OWNER": "acme",
        "ADO_ORG_URL": "https://dev.azure.com/acme",
        "ADO_PROJECT": "acme-project",
        "RALPH_HALT_WEBHOOK": "https://hooks.acme/halt",
    }


def test_main_does_not_clobber_existing_env_with_empty_cfg(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a TOML key is empty (not configured), don't overwrite an
    existing env var with empty string — that would mask the env value."""
    monkeypatch.setenv("GH_OWNER", "pre-existing")
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)  # gh_owner=""

    observed: dict[str, str | None] = {}

    def _spy_prepare(**_kwargs: object) -> str:
        observed["GH_OWNER"] = os.environ.get("GH_OWNER")
        return "github"

    monkeypatch.setattr(cli, "prepare_host_environment", _spy_prepare)
    monkeypatch.setattr(cli, "iterate_once", lambda c: IterationResult(outcome="idle", pbi_id=None))

    cli.main(["--once"])
    assert observed["GH_OWNER"] == "pre-existing"


def test_main_migrate_queue_dispatches_to_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``ralph-executor migrate-queue`` forwards --source/--target."""
    from ralph_executor import migrate_queue as migrate_mod

    captured: list[list[str]] = []

    def _fake_main(argv: list[str]) -> int:
        captured.append(argv)
        return 0

    monkeypatch.setattr(migrate_mod, "main", _fake_main)

    rc = cli.main(
        [
            "migrate-queue",
            "--source",
            str(tmp_path),
            "--target",
            "https://github.com/example/q",
        ]
    )
    assert rc == 0
    assert captured == [["--source", str(tmp_path), "--target", "https://github.com/example/q"]]


def test_main_migrate_queue_surfaces_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``MigrateQueueError`` from the module becomes a clean exit 2."""
    from ralph_executor import migrate_queue as migrate_mod

    def _raise(_argv: list[str]) -> int:
        raise migrate_mod.MigrateQueueError("nope")

    monkeypatch.setattr(migrate_mod, "main", _raise)

    rc = cli.main(
        [
            "migrate-queue",
            "--source",
            str(tmp_path),
            "--target",
            "https://github.com/example/q",
        ]
    )
    assert rc == 2


def test_main_queue_repo_flag_overrides_cfg(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Top-level ``--queue-repo`` overrides ``cfg.queue_repo`` for the iteration."""
    seen: list[ExecutorConfig] = []

    def _fake_iterate(cfg: ExecutorConfig) -> IterationResult:
        seen.append(cfg)
        return IterationResult(outcome="idle", pbi_id=None)

    monkeypatch.setattr(cli, "iterate_once", _fake_iterate)
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)

    cli.main(["--once", "--queue-repo", "https://github.com/example/override"])
    assert len(seen) == 1
    assert seen[0].queue_repo == "https://github.com/example/override"


def test_cli_queue_branch_override_lands_on_cfg(
    cfg_for_repo: ExecutorConfig,
) -> None:
    """--queue-branch on the CLI overrides cfg.queue_branch via _apply_overrides."""
    import argparse
    import dataclasses as _dc

    from ralph_executor.cli import _apply_overrides

    cfg = _dc.replace(cfg_for_repo, queue_branch="ralph-queue")
    args = argparse.Namespace(
        repo=None,
        workspace=None,
        log_level=None,
        queue_repo=None,
        queue_branch="override-branch",
        watch=False,
    )
    new_cfg = _apply_overrides(cfg, args)
    assert new_cfg.queue_branch == "override-branch"


def test_cli_queue_branch_rejects_empty(
    cfg_for_repo: ExecutorConfig,
) -> None:
    """--queue-branch '   ' (whitespace-only) raises ConfigError."""
    import argparse
    import dataclasses as _dc

    from ralph_executor.cli import _apply_overrides
    from ralph_executor.config import ConfigError

    cfg = _dc.replace(cfg_for_repo, queue_branch="ralph-queue")
    args = argparse.Namespace(
        repo=None,
        workspace=None,
        log_level=None,
        queue_repo=None,
        queue_branch="   ",
        watch=False,
    )
    with pytest.raises(ConfigError, match="non-empty"):
        _apply_overrides(cfg, args)


def test_cli_queue_branch_rejects_empty_string(
    cfg_for_repo: ExecutorConfig,
) -> None:
    """--queue-branch '' raises ConfigError instead of silently no-op'ing.

    Regression for BugBot finding: the override guard used truthiness
    (``if args.queue_branch:``) so an empty string fell through and the
    cfg.queue_branch from TOML/env was kept silently.
    """
    import argparse
    import dataclasses as _dc

    from ralph_executor.cli import _apply_overrides
    from ralph_executor.config import ConfigError

    cfg = _dc.replace(cfg_for_repo, queue_branch="ralph-queue")
    args = argparse.Namespace(
        repo=None,
        workspace=None,
        log_level=None,
        queue_repo=None,
        queue_branch="",
        watch=False,
    )
    with pytest.raises(ConfigError, match="non-empty"):
        _apply_overrides(cfg, args)


def test_cli_queue_branch_rejects_head(
    cfg_for_repo: ExecutorConfig,
) -> None:
    """--queue-branch HEAD raises ConfigError (not a plain branch name)."""
    import argparse
    import dataclasses as _dc

    from ralph_executor.cli import _apply_overrides
    from ralph_executor.config import ConfigError

    cfg = _dc.replace(cfg_for_repo, queue_branch="ralph-queue")
    args = argparse.Namespace(
        repo=None,
        workspace=None,
        log_level=None,
        queue_repo=None,
        queue_branch="HEAD",
        watch=False,
    )
    with pytest.raises(ConfigError, match="plain branch name"):
        _apply_overrides(cfg, args)


def test_cli_queue_branch_rejects_refs_prefix(
    cfg_for_repo: ExecutorConfig,
) -> None:
    """--queue-branch refs/heads/foo raises ConfigError (not a plain branch name)."""
    import argparse
    import dataclasses as _dc

    from ralph_executor.cli import _apply_overrides
    from ralph_executor.config import ConfigError

    cfg = _dc.replace(cfg_for_repo, queue_branch="ralph-queue")
    args = argparse.Namespace(
        repo=None,
        workspace=None,
        log_level=None,
        queue_repo=None,
        queue_branch="refs/heads/foo",
        watch=False,
    )
    with pytest.raises(ConfigError, match="plain branch name"):
        _apply_overrides(cfg, args)


def test_cli_instance_id_override_lands_on_cfg(
    cfg_for_repo: ExecutorConfig,
) -> None:
    """--instance-id on the CLI overrides cfg.instance_id via _apply_overrides."""
    import argparse
    import dataclasses as _dc

    from ralph_executor.cli import _apply_overrides

    cfg = _dc.replace(cfg_for_repo, instance_id="from-toml")
    args = argparse.Namespace(
        repo=None,
        workspace=None,
        log_level=None,
        queue_repo=None,
        queue_branch=None,
        watch=False,
        instance_id="from-cli",
    )
    new_cfg = _apply_overrides(cfg, args)
    assert new_cfg.instance_id == "from-cli"


def test_cli_instance_id_rejects_empty_string(
    cfg_for_repo: ExecutorConfig,
) -> None:
    """``--instance-id ''`` raises ConfigError — must not silently fall through
    to env / TOML / hostname.

    The override guard uses ``is not None`` rather than truthiness, so the
    empty string reaches ``validate_instance_id`` and the regex surfaces a
    clean error.
    """
    import argparse
    import dataclasses as _dc

    from ralph_executor.cli import _apply_overrides
    from ralph_executor.config import ConfigError

    cfg = _dc.replace(cfg_for_repo, instance_id="from-toml")
    args = argparse.Namespace(
        repo=None,
        workspace=None,
        log_level=None,
        queue_repo=None,
        queue_branch=None,
        watch=False,
        instance_id="",
    )
    with pytest.raises(ConfigError, match="instance_id"):
        _apply_overrides(cfg, args)


def test_cli_instance_id_rejects_invalid_chars(
    cfg_for_repo: ExecutorConfig,
) -> None:
    """``--instance-id 'BAD CHARS'`` raises ConfigError via the regex validator."""
    import argparse
    import dataclasses as _dc

    from ralph_executor.cli import _apply_overrides
    from ralph_executor.config import ConfigError

    cfg = _dc.replace(cfg_for_repo, instance_id="from-toml")
    args = argparse.Namespace(
        repo=None,
        workspace=None,
        log_level=None,
        queue_repo=None,
        queue_branch=None,
        watch=False,
        instance_id="BAD CHARS",
    )
    with pytest.raises(ConfigError, match="instance_id"):
        _apply_overrides(cfg, args)


def test_main_instance_id_flag_overrides_cfg(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Top-level ``--instance-id`` reaches iterate_once via ``_apply_overrides``."""
    seen: list[ExecutorConfig] = []

    def _fake_iterate(cfg: ExecutorConfig) -> IterationResult:
        seen.append(cfg)
        return IterationResult(outcome="idle", pbi_id=None)

    monkeypatch.setattr(cli, "iterate_once", _fake_iterate)
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)

    exit_code = cli.main(["--once", "--instance-id", "ralph-b"])
    assert exit_code == 0
    assert len(seen) == 1
    assert seen[0].instance_id == "ralph-b"


def test_main_instance_id_empty_string_exits_2(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``cli.main(["--instance-id", ""])`` exits 2 with a clear ConfigError.

    Regression guard for the truthiness-vs-None rule at the CLI boundary:
    an empty string must surface an error rather than silently letting
    the env / TOML / hostname fall-through take over.
    """
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.setattr(
        cli, "iterate_once", lambda cfg: IterationResult(outcome="idle", pbi_id=None)
    )
    exit_code = cli.main(["--once", "--instance-id", ""])
    assert exit_code == 2
    assert "instance_id" in capsys.readouterr().err


def test_main_instance_id_invalid_chars_exits_2(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``cli.main(["--instance-id", "BAD CHARS"])`` exits 2 via the regex validator."""
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.setattr(
        cli, "iterate_once", lambda cfg: IterationResult(outcome="idle", pbi_id=None)
    )
    exit_code = cli.main(["--once", "--instance-id", "BAD CHARS"])
    assert exit_code == 2
    assert "instance_id" in capsys.readouterr().err


def test_main_rejects_watch_with_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--watch`` and ``--once`` collide (drain-forever vs run-once); the
    CLI must reject the combo with a clear error before touching config."""
    exit_code = cli.main(["--watch", "--once"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "mutually exclusive" in captured.err


def test_main_rejects_watch_with_iterations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Same mutex applies to ``--iterations N``."""
    exit_code = cli.main(["--watch", "--iterations", "3"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "mutually exclusive" in captured.err


def test_main_watch_flag_sets_watch_mode_on_config(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--watch`` flips ``cfg.watch_mode`` to True; without it the loaded
    config's existing value is preserved (default False in the fixture)."""
    seen: list[bool] = []

    def _fake_run_loop(cfg: ExecutorConfig):  # type: ignore[no-untyped-def]
        seen.append(cfg.watch_mode)
        # Yield a single non-idle result then terminate so main() returns.
        yield IterationResult(outcome="ran_partial", pbi_id="WI-NOP")

    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.setattr(cli, "run_loop", _fake_run_loop)

    exit_code = cli.main(["--watch"])
    assert exit_code == 0
    assert seen == [True]


def test_public_reexports_are_stable() -> None:
    """The names listed below are imported by Plans 8, 9, 10."""
    from ralph_executor import (
        PBI,
        ExecutorConfig,
        IterationResult,
        iterate_once,
        main,
        prepare_host_environment,
        run_loop,
    )

    assert PBI is not None
    assert ExecutorConfig is not None
    assert IterationResult is not None
    assert callable(iterate_once)
    assert callable(run_loop)
    assert callable(main)
    assert callable(prepare_host_environment)


def test_init_prompts_for_queue_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ralph-executor init` prompts for queue_branch (default ralph-queue)
    after queue_repo. Blank input → default persisted to ``~/.ralph/config.toml``.
    """
    import builtins

    from ralph_executor import user_config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    # Stub the network smoke-check so the test doesn't hit the wire.
    monkeypatch.setattr(
        "ralph_executor.setup_cmds._smoke_clone_queue_repo",
        lambda _url, *, timeout=10.0: True,
    )

    # The interactive flow calls input() three times in order:
    #   1. workspace_root prompt (blank → OS default)
    #   2. queue_repo prompt (valid URL accepted)
    #   3. queue_branch prompt (blank → default "ralph-queue")
    answers = iter(
        [
            "",
            "https://github.com/test/queue",
            "",
        ]
    )
    monkeypatch.setattr(builtins, "input", lambda *_a, **_k: next(answers))

    rc = cli.main(["init"])
    assert rc == 0
    assert user_config.read_queue_branch() == "ralph-queue"


def test_init_persists_non_default_queue_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-blank answer at the queue_branch prompt is persisted verbatim."""
    import builtins

    from ralph_executor import user_config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    monkeypatch.setattr(
        "ralph_executor.setup_cmds._smoke_clone_queue_repo",
        lambda _url, *, timeout=10.0: True,
    )

    answers = iter(
        [
            "",  # workspace_root → default
            "https://github.com/test/queue",
            "custom-queue-branch",
        ]
    )
    monkeypatch.setattr(builtins, "input", lambda *_a, **_k: next(answers))

    rc = cli.main(["init"])
    assert rc == 0
    assert user_config.read_queue_branch() == "custom-queue-branch"


def test_init_assume_yes_writes_default_queue_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--yes` skips every prompt and writes the default ``ralph-queue``."""
    from ralph_executor import user_config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    rc = cli.main(["init", "--yes"])
    assert rc == 0
    assert user_config.read_queue_branch() == "ralph-queue"
