"""Tests for ``ralph_executor.cli``."""

from __future__ import annotations

import logging
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
    monkeypatch.setattr(cli, "prepare_host_environment", lambda: "github")


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


def test_main_uses_repo_flag_to_override_env(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def _capture(cfg: ExecutorConfig) -> IterationResult:
        observed.append(str(cfg.repo_path))
        return IterationResult(outcome="idle", pbi_id=None)

    monkeypatch.setattr(cli, "iterate_once", _capture)

    def _fake_load() -> ExecutorConfig:
        return cfg_for_repo

    monkeypatch.setattr(cli, "load_config", _fake_load)

    exit_code = cli.main(["--once", "--repo", str(fake_repo)])
    assert exit_code == 0
    assert observed == [str(fake_repo)]


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
        raise ConfigError("RALPH_REPO_PATH is required")

    monkeypatch.setattr(cli, "load_config", _explode)
    exit_code = cli.main(["--once"])
    assert exit_code == 2
    assert "RALPH_REPO_PATH" in capsys.readouterr().err


def test_main_calls_prepare_host_environment_before_loop(
    cfg_for_repo: ExecutorConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup order: load_config -> prepare_host_environment -> iterate."""
    order: list[str] = []

    def _record_load() -> ExecutorConfig:
        order.append("load_config")
        return cfg_for_repo

    def _record_prepare() -> str:
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

    def _explode() -> str:
        raise HostSelectionError("RALPH_GIT_HOST is required but unset or blank.")

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
    assert "RALPH_GIT_HOST" in err


def test_main_uses_workspace_flag_to_resolve_repo(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--workspace NAME resolves to $RALPH_HOME/NAME and validates the
    result against the git-repo invariants."""
    home = fake_repo.parent
    workspace_name = fake_repo.name

    observed: list[str] = []

    def _capture(cfg: ExecutorConfig) -> IterationResult:
        observed.append(str(cfg.repo_path))
        return IterationResult(outcome="idle", pbi_id=None)

    monkeypatch.setattr(cli, "iterate_once", _capture)
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.setenv("RALPH_HOME", str(home))

    exit_code = cli.main(["--once", "--workspace", workspace_name])
    assert exit_code == 0
    assert observed == [str(fake_repo.resolve())]


def test_main_workspace_without_ralph_home_errors(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--workspace without RALPH_HOME must exit 2 with a clear error."""
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.delenv("RALPH_HOME", raising=False)

    exit_code = cli.main(["--once", "--workspace", "any-name"])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "RALPH_HOME" in err


def test_main_repo_and_workspace_are_mutually_exclusive(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """argparse should reject --repo together with --workspace."""
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--once", "--repo", "/x", "--workspace", "y"])
    # argparse exits 2 on usage errors.
    assert exc_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_main_workspace_validates_resolved_path(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """$RALPH_HOME/NAME pointing at a non-git directory must exit 2."""
    home = tmp_path
    bogus = home / "no-git-here"
    bogus.mkdir()

    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.setenv("RALPH_HOME", str(home))

    exit_code = cli.main(["--once", "--workspace", "no-git-here"])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "not a git repository" in err
    assert "--workspace" in err


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
