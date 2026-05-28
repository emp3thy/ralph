"""Shared fixtures for ``ralph_executor`` tests.

Every test that touches the queue or the loop needs a local git repo
with both ``main`` and ``ralph-queue`` plus a stand-in for the
``claude`` binary. Building those in one place keeps each test focused.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent

import pytest

from ralph_executor.config import ExecutorConfig


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.fixture
def fake_repo(tmp_path: Path) -> Iterator[Path]:
    """Initialise a local bare + worktree pair with main and ralph-queue."""
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"

    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test User")
    _git(work, "commit", "--allow-empty", "-m", "chore: initial main commit")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")
    # Mirror the production ``.gitignore`` which excludes ``.ralph/state/``
    # so the cycle-detector event DB (created at runtime by ``open_log``)
    # is never staged into a commit and never blocks a branch switch.
    (work / ".gitignore").write_text(".ralph/state/\n", encoding="utf-8")
    _git(work, "add", ".gitignore")
    _git(work, "commit", "-m", "chore: gitignore .ralph/state/")
    _git(work, "push", "origin", "main")
    _git(work, "checkout", "-b", "ralph-queue")
    # The queue branch starts with an empty ``.ralph/`` tree.
    (work / ".ralph" / "inbox").mkdir(parents=True)
    (work / ".ralph" / "current").mkdir()
    (work / ".ralph" / "pending-pr").mkdir()
    (work / ".ralph" / "done").mkdir()
    (work / ".ralph" / "blocked").mkdir()
    # Git ignores empty directories; drop ``.gitkeep`` files.
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (work / ".ralph" / sub / ".gitkeep").write_text("", encoding="utf-8")
    _git(work, "add", ".ralph")
    _git(work, "commit", "-m", "chore(queue): bootstrap .ralph/ tree")
    _git(work, "push", "-u", "origin", "ralph-queue")
    _git(work, "checkout", "main")
    yield work


def write_sample_pbi(
    repo: Path,
    *,
    pbi_id: str = "WI-1234",
    pbi_type: str = "feature",
    severity: str = "normal",
    created_at: str = "2026-05-24T09:15:00+00:00",
    where: str = "inbox",
) -> Path:
    """Write a minimal feature PBI directory into ``.ralph/<where>/<pbi_id>``.

    Returns the absolute path to the new directory. Assumes the repo is
    currently on ``ralph-queue`` (callers checkout before calling).
    """
    pbi_dir = repo / ".ralph" / where / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=True)
    if pbi_type == "bug":
        entry_name = "BUG.md"
        sibling = "REPRODUCE.md"
    elif pbi_type == "pr-feedback":
        entry_name = "FEEDBACK.md"
        sibling = "PR-LINK.md"
    else:
        entry_name = "PBI.md"
        sibling = "PLAN.md"

    status = where if where in {"inbox", "current", "pending-pr", "done", "blocked"} else "inbox"
    frontmatter = dedent(
        f"""\
        ---
        id: {pbi_id}
        type: {pbi_type}
        status: {status}
        severity: {severity}
        attempts: 0
        created_at: {created_at}
        updated_at: {created_at}
        ---

        # {pbi_id} sample body
        """
    )
    (pbi_dir / entry_name).write_text(frontmatter, encoding="utf-8")
    (pbi_dir / sibling).write_text(f"# {sibling}\n", encoding="utf-8")
    if pbi_type == "pr-feedback":
        (pbi_dir / "ORIGINAL.md").write_text("# original\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    return pbi_dir


@pytest.fixture
def sample_pbi(fake_repo: Path) -> Path:
    """Write a minimal feature PBI into ``.ralph/inbox/WI-1234`` and commit it."""
    _git(fake_repo, "checkout", "ralph-queue")
    pbi_dir = write_sample_pbi(fake_repo)
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", "feat(ralph-queue): add WI-1234")
    _git(fake_repo, "push", "origin", "ralph-queue")
    _git(fake_repo, "checkout", "main")
    return pbi_dir


@pytest.fixture
def fake_claude_binary(tmp_path: Path) -> Path:
    """Write a stand-in ``claude`` script that echoes its argv to stdout.

    The default script returns exit code 0 with empty stdout — tests
    override its content by writing a new script at the same path.

    On Windows, shebang scripts cannot be executed directly; the fixture
    creates a ``.py`` file alongside a ``.cmd`` wrapper and returns the
    ``.cmd`` path so ``subprocess.run`` can invoke it without a shell.
    On POSIX the extensionless script with a shebang is returned directly.
    """
    import platform

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    py_body = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
    if platform.system() == "Windows":
        py_script = bin_dir / "claude.py"
        py_script.write_text(py_body, encoding="utf-8")
        cmd_script = bin_dir / "claude.cmd"
        cmd_script.write_text(
            f'@"{sys.executable}" "%~dp0claude.py" %*\n',
            encoding="utf-8",
        )
        return cmd_script
    else:
        script = bin_dir / "claude"
        script.write_text(py_body, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return script


def write_claude_script(path: Path, body: str) -> None:
    """Overwrite the stand-in ``claude`` Python body.

    ``path`` is the value returned by the ``fake_claude_binary`` fixture —
    either an extensionless POSIX script or a ``.cmd`` wrapper on Windows.
    On Windows, the actual Python source lives at ``<stem>.py`` next to the
    ``.cmd`` file; on POSIX it is the file itself.
    """
    import platform

    if platform.system() == "Windows":
        # Write to the .py file that the .cmd wrapper delegates to.
        py_script = path.with_suffix(".py")
        py_script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    else:
        shebang = "#!/usr/bin/env python3\n"
        path.write_text(shebang + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def cfg_for_repo(fake_repo: Path, fake_claude_binary: Path) -> ExecutorConfig:
    """Build an ExecutorConfig pointing at the fake repo + fake claude."""
    return ExecutorConfig(
        repo_path=fake_repo,
        queue_branch="ralph-queue",
        main_branch="main",
        max_attempts=3,
        log_level=20,  # logging.INFO
        iteration_sleep_seconds=0.0,
        claude_binary=str(fake_claude_binary),
        claude_permission_mode="bypassPermissions",
        anthropic_api_key="fake-key",
        git_host="github",
        gh_owner="",
        ado_org_url="",
        ado_project="",
        halt_webhook="",
        pr_check_poll_max_attempts=6,
        pr_check_poll_interval_seconds=30.0,
        # Existing executor tests exercise the legacy single-checkout
        # branch-dance path; worktree-mode coverage gets its own fixtures
        # alongside the dedicated tests in Task 9.
        use_worktrees=False,
        bot_author_email="",
        stale_days=3,
        bash_max_timeout_ms=900_000,
        claude_session_timeout_seconds=1200,
        same_file_min_prs=10,
        same_file_window_hours=24.0,
    )


@pytest.fixture(autouse=True)
def _claude_path_for_subprocess(monkeypatch: pytest.MonkeyPatch, fake_claude_binary: Path) -> None:
    """Prepend the stand-in ``claude`` to PATH for any spawn under test."""
    monkeypatch.setenv(
        "PATH",
        f"{fake_claude_binary.parent}{os.pathsep}{os.environ.get('PATH', '')}",
    )
