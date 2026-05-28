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


def test_main_workspace_rejects_absolute_name(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An absolute --workspace name would silently escape RALPH_HOME
    (Path('home') / '/etc' yields Path('/etc')). Must be rejected.

    Uses a POSIX-style absolute path because Path('/etc').is_absolute()
    is True on both Windows and POSIX, whereas 'C:\\\\etc' only registers
    as absolute on Windows.
    """
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.setenv("RALPH_HOME", "/dev/ralph")

    exit_code = cli.main(["--once", "--workspace", "/etc"])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "plain directory name" in err


def test_main_workspace_rejects_parent_traversal(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A `..` segment would resolve outside RALPH_HOME. Must be rejected.

    Uses '/'-separated input so Path treats '..' as a separate component
    on both Windows and POSIX (backslash is a regular filename character
    on POSIX, so '..\\\\sibling' would slip past the parts check there).
    """
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.setenv("RALPH_HOME", "/dev/ralph")

    exit_code = cli.main(["--once", "--workspace", "../sibling"])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "plain directory name" in err


def test_main_workspace_rejects_single_dot(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--workspace .` would resolve to $RALPH_HOME itself, silently
    making Ralph operate on the home root. Must be rejected."""
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.setenv("RALPH_HOME", "/dev/ralph")

    exit_code = cli.main(["--once", "--workspace", "."])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "plain directory name" in err


def test_main_workspace_rejects_path_separator(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Multi-segment names break the one-ralph-per-workspace convention
    and are easy to confuse with `--repo`. Reject them."""
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
    monkeypatch.setenv("RALPH_HOME", "C:\\dev\\ralph")

    exit_code = cli.main(["--once", "--workspace", "a/b"])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "plain directory name" in err


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


def test_main_init_subcommand_writes_user_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`ralph-executor init --ralph-home PATH` writes ~/.ralph/config.toml."""
    from ralph_executor.user_config import read_ralph_home

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    target = tmp_path / "dev" / "ralph"

    exit_code = cli.main(["init", "--ralph-home", str(target), "--yes"])
    assert exit_code == 0
    assert read_ralph_home() == target.resolve()
    out = capsys.readouterr().out
    assert str(target.resolve()) in out


def test_main_scaffold_subcommand_creates_queue_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ralph-executor scaffold --repo PATH` creates ralph-queue with .ralph/."""
    import subprocess

    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "branch", "-M", "main"],
        check=True,
        capture_output=True,
    )

    exit_code = cli.main(["scaffold", "--repo", str(repo)])
    assert exit_code == 0
    assert (repo / ".ralph" / "inbox" / ".gitkeep").is_file()
    assert (repo / ".ralph" / "config.toml").is_file()


def test_main_workspace_reads_ralph_home_from_user_config(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When $RALPH_HOME is unset, --workspace falls back to ~/.ralph/config.toml."""
    from ralph_executor.user_config import write_ralph_home

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("RALPH_HOME", raising=False)
    # Persist fake_repo's parent as ralph_home so --workspace <fake_repo.name>
    # resolves to fake_repo.
    write_ralph_home(fake_repo.parent)

    observed: list[str] = []

    def _capture(cfg: ExecutorConfig) -> IterationResult:
        observed.append(str(cfg.repo_path))
        return IterationResult(outcome="idle", pbi_id=None)

    monkeypatch.setattr(cli, "iterate_once", _capture)
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)

    exit_code = cli.main(["--once", "--workspace", fake_repo.name])
    assert exit_code == 0
    assert observed == [str(fake_repo.resolve())]


def test_main_workspace_errors_when_no_ralph_home_anywhere(
    cfg_for_repo: ExecutorConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Neither env nor user-config set → clear error referencing `ralph-executor init`."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("RALPH_HOME", raising=False)
    monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)

    exit_code = cli.main(["--once", "--workspace", "anything"])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "ralph-executor init" in err


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
