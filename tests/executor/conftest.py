"""Shared fixtures for ``ralph_executor`` tests.

After the queue-repo-split (PBI EXECUTOR-QUEUE-REPO-SPLIT), the queue is
a separate clone at ``<workspace_root>/queue/`` cloned from
``cfg.queue_repo`` rather than a branch on the source repo. The
``fake_repo`` fixture builds that queue clone with an empty ``.ralph/``
skeleton on ``main`` and returns its path.
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
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


@pytest.fixture
def fake_repo(tmp_path: Path) -> Iterator[Path]:
    """Materialise a queue clone at ``<tmp_path>/ws/queue`` with ``.ralph/``.

    Builds a bare remote at ``<tmp_path>/queue.git`` seeded with the
    ``.ralph/`` skeleton on ``main`` and a sibling clone at
    ``<tmp_path>/ws/queue``. Returns the clone path. The bare remote
    path is reachable as ``<tmp_path>/queue.git`` for tests that need
    to query / push to ``origin``.
    """
    bare = tmp_path / "queue.git"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    clone = workspace / "queue"

    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(seed)],
        check=True,
        capture_output=True,
    )
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "Test User")
    _git(seed, "commit", "--allow-empty", "-m", "chore: initial main commit")
    # Mirror production .gitignore — .ralph/state/ stays local.
    (seed / ".gitignore").write_text(".ralph/state/\n", encoding="utf-8")
    _git(seed, "add", ".gitignore")
    _git(seed, "commit", "-m", "chore: gitignore .ralph/state/")
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (seed / ".ralph" / sub).mkdir(parents=True)
        (seed / ".ralph" / sub / ".gitkeep").write_text("", encoding="utf-8")
    _git(seed, "add", ".ralph")
    _git(seed, "commit", "-m", "chore(queue): bootstrap .ralph/ tree")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-u", "origin", "main")

    subprocess.run(
        ["git", "clone", str(bare), str(clone)],
        check=True,
        capture_output=True,
    )
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "Test User")
    yield clone


def write_sample_pbi(
    repo: Path,
    *,
    pbi_id: str = "WI-1234",
    pbi_type: str = "feature",
    severity: str = "normal",
    created_at: str = "2026-05-24T09:15:00+00:00",
    where: str = "inbox",
    target_repo: str = "https://github.com/test/repo",
) -> Path:
    """Write a minimal feature PBI directory into ``.ralph/<where>/<pbi_id>``.

    ``repo`` is the queue clone (the ``fake_repo`` fixture's return value).
    Returns the absolute path to the new directory. Callers commit + push.
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
        target_repo: {target_repo}
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
    """Write a minimal feature PBI into ``.ralph/inbox/WI-1234`` and push it."""
    pbi_dir = write_sample_pbi(fake_repo)
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", "feat(queue): add WI-1234")
    _git(fake_repo, "push", "origin", "main")
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
def cfg_for_repo(
    fake_repo: Path,
    fake_claude_binary: Path,
    tmp_path: Path,
) -> ExecutorConfig:
    """Build an ExecutorConfig pointing at the queue clone + fake claude.

    ``workspace_root`` is ``<tmp_path>/ws`` so ``<workspace_root>/queue``
    resolves to the ``fake_repo`` clone built by that fixture. The bare
    remote at ``<tmp_path>/queue.git`` is the ``queue_repo`` URL.
    """
    bare = tmp_path / "queue.git"
    return ExecutorConfig(
        repo_path=fake_repo,
        queue_repo=f"file://{bare.as_posix()}",
        # NOTE: the ``fake_repo`` seed pushes a single ``main`` branch to
        # the bare remote, and the queue movements helpers in
        # ``ralph_executor.queue.movements`` still hard-code ``branch="main"``
        # for ``push_with_rebase`` until Task 8 of the
        # queue-branch-configurable plan threads ``cfg.queue_branch`` through
        # ``_move``. Until both land together, the conftest fixture uses
        # ``main`` so integration tests don't tear the fixture in half.
        # Task 11 introduces a dedicated ``ralph-queue`` seed for the
        # end-to-end test of the new branch routing.
        queue_branch="main",
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
        use_worktrees=True,
        bot_author_email="",
        stale_days=3,
        bash_max_timeout_ms=900_000,
        workspace_root=tmp_path / "ws",
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


@pytest.fixture(autouse=True)
def _fake_ensure_target_clone(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Replace ``target_clone.ensure_clone`` with a stand-in pointing at ``fake_repo``.

    Tests seed PBIs whose ``target_repo`` frontmatter points at non-resolving
    GitHub URLs (e.g. ``https://github.com/test/repo``). The new multi-repo
    claim path calls ``ensure_clone`` which tries to ``git clone`` the URL.
    Reuse the ``fake_repo`` queue clone as the target clone so existing
    test assertions about feature branches / commits live in one place.

    Tests that need a distinct target can override this monkeypatch.
    """
    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    def _fake_ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        try:
            fake_repo: Path = request.getfixturevalue("fake_repo")
        except pytest.FixtureLookupError:
            # Test does not exercise the queue clone (e.g. pure cycle-detector
            # unit tests). Fall back to creating a minimal local repo.
            clone_root = workspace_root / "clones" / info.owner / info.name
            if not (clone_root / ".git").is_dir():
                clone_root.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["git", "init", "--initial-branch=main", str(clone_root)],
                    check=True,
                    capture_output=True,
                )
                _git(clone_root, "config", "user.email", "test@example.com")
                _git(clone_root, "config", "user.name", "Test User")
                _git(clone_root, "commit", "--allow-empty", "-m", "chore: initial")
                head_sha = _git(clone_root, "rev-parse", "HEAD").strip()
                _git(clone_root, "update-ref", "refs/remotes/origin/main", head_sha)
            return TargetClone(info=info, clone_root=clone_root)
        return TargetClone(info=info, clone_root=fake_repo)

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _fake_ensure_clone)
