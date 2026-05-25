"""Tests for ``ralph_executor.setup_cmds``."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from ralph_executor.setup_cmds import (
    QUEUE_BRANCH,
    QUEUE_SUBDIRS,
    cmd_init,
    cmd_scaffold,
)
from ralph_executor.user_config import read_ralph_home, user_config_path

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Path.home() at a temp dir so user_config.* writes there."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.fixture
def fresh_repo(tmp_path: Path) -> Iterator[Path]:
    """A freshly-initialised git repo on a clean main branch with one
    initial commit. Yields the repo path."""
    repo = tmp_path / "target-repo"
    repo.mkdir()
    _g = subprocess.run
    _g(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    _g(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    _g(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    _g(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    _g(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
    yield repo


def _current_branch(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


# ---------------------------------------------------------------------------
# cmd_init
# ---------------------------------------------------------------------------


def test_init_writes_user_config_with_explicit_ralph_home(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = fake_home / "dev" / "ralph"
    exit_code = cmd_init(ralph_home=target, assume_yes=False)
    assert exit_code == 0
    assert read_ralph_home() == target.resolve()
    assert target.is_dir()  # init creates the dir
    out = capsys.readouterr().out
    assert str(user_config_path()) in out


def test_init_assume_yes_uses_os_default(fake_home: Path) -> None:
    """--yes path: no prompt, picks the OS default."""
    exit_code = cmd_init(ralph_home=None, assume_yes=True)
    assert exit_code == 0
    home = read_ralph_home()
    assert home is not None
    assert home.exists() and home.is_dir()


def test_init_is_idempotent_when_already_configured(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """If ralph_home is already set, plain init reports the value and
    exits 0 without overwriting."""
    first = fake_home / "first"
    cmd_init(ralph_home=first, assume_yes=False)
    capsys.readouterr()
    exit_code = cmd_init(ralph_home=None, assume_yes=False)
    assert exit_code == 0
    assert read_ralph_home() == first.resolve()
    out = capsys.readouterr().out
    assert "already set" in out


def test_init_overwrites_when_ralph_home_explicit(fake_home: Path) -> None:
    """Explicit --ralph-home overwrites an existing value."""
    cmd_init(ralph_home=fake_home / "first", assume_yes=False)
    cmd_init(ralph_home=fake_home / "second", assume_yes=False)
    assert read_ralph_home() == (fake_home / "second").resolve()


def test_init_warns_when_gh_missing(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "ralph_executor.setup_cmds._check_tool",
        lambda name: None,  # noqa: ARG005
    )
    cmd_init(ralph_home=fake_home / "x", assume_yes=False)
    out = capsys.readouterr().out
    assert "gh CLI not found" in out
    assert "claude CLI not found" in out


# ---------------------------------------------------------------------------
# cmd_scaffold
# ---------------------------------------------------------------------------


def test_scaffold_creates_branch_dirs_and_commits(
    fresh_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cmd_scaffold(repo_path=fresh_repo, force=False, with_config_toml=True)
    assert exit_code == 0
    assert _current_branch(fresh_repo) == QUEUE_BRANCH
    ralph_dir = fresh_repo / ".ralph"
    for sub in QUEUE_SUBDIRS:
        assert (ralph_dir / sub / ".gitkeep").is_file(), sub
    assert (ralph_dir / "config.toml").is_file()
    # Commit landed on the queue branch.
    log = subprocess.run(
        ["git", "-C", str(fresh_repo), "log", "--format=%s", "-n", "1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert "scaffold" in log
    out = capsys.readouterr().out
    assert "scaffolded" in out
    assert "git -C" in out and "push" in out


def test_scaffold_skips_config_toml_when_flag_false(fresh_repo: Path) -> None:
    cmd_scaffold(repo_path=fresh_repo, force=False, with_config_toml=False)
    assert not (fresh_repo / ".ralph" / "config.toml").exists()


def test_scaffold_fails_on_dirty_working_tree(
    fresh_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (fresh_repo / "uncommitted.txt").write_text("dirty", encoding="utf-8")
    exit_code = cmd_scaffold(repo_path=fresh_repo, force=False, with_config_toml=True)
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "working tree is not clean" in err


def test_scaffold_fails_when_queue_branch_exists_without_force(
    fresh_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    subprocess.run(
        ["git", "-C", str(fresh_repo), "branch", QUEUE_BRANCH],
        check=True,
        capture_output=True,
    )
    exit_code = cmd_scaffold(repo_path=fresh_repo, force=False, with_config_toml=True)
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "already exists" in err


def test_scaffold_force_switches_into_existing_queue_branch(fresh_repo: Path) -> None:
    subprocess.run(
        ["git", "-C", str(fresh_repo), "branch", QUEUE_BRANCH],
        check=True,
        capture_output=True,
    )
    exit_code = cmd_scaffold(repo_path=fresh_repo, force=True, with_config_toml=True)
    assert exit_code == 0
    assert _current_branch(fresh_repo) == QUEUE_BRANCH


def test_scaffold_restores_original_branch_on_commit_failure(
    fresh_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """If `git commit` fails after the queue switch + stage, scaffold
    must (a) switch back to the original branch and (b) leave NO
    .ralph/ files staged or on disk — without the hard-reset step,
    git carries new staged files silently across the branch switch."""
    # Install a pre-commit hook that always rejects.
    hooks_dir = fresh_repo / ".git" / "hooks"
    hook = hooks_dir / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    exit_code = cmd_scaffold(repo_path=fresh_repo, force=False, with_config_toml=True)
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "git commit failed" in err
    # Back on main.
    assert _current_branch(fresh_repo) == "main"
    # And nothing left over: no staged entries, no .ralph/ on disk.
    porcelain = subprocess.run(
        ["git", "-C", str(fresh_repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert porcelain == "", f"working tree should be clean, got: {porcelain!r}"
    assert not (fresh_repo / ".ralph").exists(), ".ralph/ should be cleaned up"


def test_scaffold_skips_branch_switch_when_reset_fails(
    fresh_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If `git reset --hard HEAD` fails during cleanup, we must NOT
    proceed to `git switch` — that would silently carry staged .ralph/
    files to the original branch."""
    # Install a pre-commit hook so commit fails, triggering the cleanup path.
    hook = fresh_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    # Monkeypatch _git so any "reset" call raises but everything else
    # passes through to the real implementation.
    import ralph_executor.setup_cmds as sc

    real_git = sc._git

    def faulty_git(repo: Path, *args: str) -> str:
        if args and args[0] == "reset":
            raise sc.ScaffoldError("simulated reset failure")
        return real_git(repo, *args)

    monkeypatch.setattr(sc, "_git", faulty_git)

    exit_code = cmd_scaffold(repo_path=fresh_repo, force=False, with_config_toml=True)
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "could not reset queue branch" in err
    # Critical: we stay on ralph-queue (NOT switched). The warning
    # tells the operator how to recover manually.
    assert _current_branch(fresh_repo) == QUEUE_BRANCH


def test_scaffold_fails_on_non_git_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    exit_code = cmd_scaffold(repo_path=not_a_repo, force=False, with_config_toml=True)
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "not a git repository" in err
