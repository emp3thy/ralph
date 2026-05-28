"""Tests for ``ralph_executor.git_ops``.

Each helper is exercised against the ``fake_repo`` fixture (a real local
bare + worktree pair). The tests assert observable git state — refs,
HEAD, commit shas — rather than mocking subprocess.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph_executor import git_ops
from ralph_executor.git_ops import GitCommandError


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def _push_extra_branch(fake_repo: Path, name: str = "feature") -> None:
    """Create ``name`` locally + push to ``origin`` so the tests have a
    second branch alongside the queue clone's default ``main``."""
    _git(fake_repo, "branch", name, "main")
    _git(fake_repo, "push", "origin", name)


def test_current_branch_returns_active_branch(fake_repo: Path) -> None:
    _push_extra_branch(fake_repo)
    _git(fake_repo, "checkout", "main")
    assert git_ops.current_branch(fake_repo) == "main"
    _git(fake_repo, "checkout", "feature")
    assert git_ops.current_branch(fake_repo) == "feature"


def test_branch_exists_true_for_local_and_remote(fake_repo: Path) -> None:
    _push_extra_branch(fake_repo)
    assert git_ops.branch_exists(fake_repo, "main") is True
    assert git_ops.branch_exists(fake_repo, "feature") is True
    assert git_ops.branch_exists(fake_repo, "nope") is False


def test_is_branch_remote_for_origin(fake_repo: Path) -> None:
    _push_extra_branch(fake_repo)
    assert git_ops.is_branch_remote(fake_repo, "main") is True
    assert git_ops.is_branch_remote(fake_repo, "feature") is True
    assert git_ops.is_branch_remote(fake_repo, "nope") is False


def test_checkout_switches_branch(fake_repo: Path) -> None:
    _push_extra_branch(fake_repo)
    _git(fake_repo, "checkout", "main")
    git_ops.checkout(fake_repo, "feature")
    assert git_ops.current_branch(fake_repo) == "feature"


def test_checkout_unknown_branch_raises(fake_repo: Path) -> None:
    with pytest.raises(GitCommandError):
        git_ops.checkout(fake_repo, "definitely-not-a-branch")


def test_checkout_new_creates_branch_off_head(fake_repo: Path) -> None:
    _git(fake_repo, "checkout", "main")
    git_ops.checkout_new(fake_repo, "ralph/WI-9999")
    assert git_ops.current_branch(fake_repo) == "ralph/WI-9999"
    assert git_ops.branch_exists(fake_repo, "ralph/WI-9999") is True


def test_fetch_does_not_modify_working_tree(fake_repo: Path) -> None:
    _git(fake_repo, "checkout", "main")
    before = _git(fake_repo, "rev-parse", "HEAD").strip()
    git_ops.fetch(fake_repo)
    after = _git(fake_repo, "rev-parse", "HEAD").strip()
    assert before == after


def test_pull_is_a_noop_when_nothing_to_pull(fake_repo: Path) -> None:
    before = _git(fake_repo, "rev-parse", "HEAD").strip()
    git_ops.pull(fake_repo, "main")
    after = _git(fake_repo, "rev-parse", "HEAD").strip()
    assert before == after


def test_commit_all_creates_a_commit(fake_repo: Path) -> None:
    (fake_repo / "scratch.txt").write_text("hello", encoding="utf-8")
    before = _git(fake_repo, "rev-parse", "HEAD").strip()
    sha = git_ops.commit_all(fake_repo, "test: add scratch")
    after = _git(fake_repo, "rev-parse", "HEAD").strip()
    assert sha == after
    assert before != after


def test_commit_all_no_changes_returns_head(fake_repo: Path) -> None:
    head = _git(fake_repo, "rev-parse", "HEAD").strip()
    sha = git_ops.commit_all(fake_repo, "test: no changes")
    assert sha == head


def test_push_advances_remote_ref(fake_repo: Path) -> None:
    (fake_repo / "scratch2.txt").write_text("y", encoding="utf-8")
    git_ops.commit_all(fake_repo, "test: scratch2")
    git_ops.push(fake_repo, "main")
    remote_sha = _git(fake_repo, "ls-remote", "origin", "main").split()[0]
    local_sha = _git(fake_repo, "rev-parse", "HEAD").strip()
    assert local_sha == remote_sha


def test_mv_moves_a_file(fake_repo: Path) -> None:
    src = fake_repo / "src.txt"
    src.write_text("x", encoding="utf-8")
    git_ops.add(fake_repo, src)
    git_ops.commit_all(fake_repo, "test: add src")
    dst = fake_repo / "subdir" / "dst.txt"
    git_ops.mv(fake_repo, src, dst)
    assert not src.exists()
    assert dst.is_file()


def test_rev_parse_head_returns_sha(fake_repo: Path) -> None:
    _git(fake_repo, "checkout", "main")
    sha = git_ops.rev_parse_head(fake_repo)
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


def test_git_command_error_carries_argv_and_stderr(tmp_path: Path) -> None:
    with pytest.raises(GitCommandError) as excinfo:
        git_ops.current_branch(tmp_path)  # tmp_path is not a git repo
    assert "git" in str(excinfo.value).lower()


def test_run_git_survives_non_ascii_commit_subject(fake_repo: Path) -> None:
    """Regression for BUG-SUBPROCESS-WINDOWS-ENCODING-AUDIT: a commit
    subject containing non-ASCII characters (emoji, accented Latin) must
    round-trip through ``_run_git`` without UnicodeDecodeError on
    Windows. Pre-fix, ``_run_git`` decoded with the host locale (cp1252
    on stock Windows), which raised on any byte outside cp1252's
    coverage (e.g. ``0xF0`` from a UTF-8 emoji)."""
    _git(fake_repo, "checkout", "main")
    fname = fake_repo / "unicode.txt"
    fname.write_text("hi", encoding="utf-8")
    git_ops.add(fake_repo, fname)

    subject = "feat: hello héllo 🚀 — unicode commit"
    _git(fake_repo, "commit", "-m", subject)

    # Read the subject back via _run_git — this is the call that crashed
    # before the fix.
    result = git_ops._run_git(fake_repo, "log", "-1", "--pretty=%s")
    assert result.stdout.strip() == subject


def test_clone_invokes_git_clone_with_url_and_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git_ops.clone delegates to ``git clone <url> <dest>``."""
    captured: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(list(argv))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ralph_executor.git_ops.run_text", fake_run)
    dest = tmp_path / "newclone"

    git_ops.clone("https://github.com/x/y.git", dest)

    assert len(captured) == 1
    assert captured[0][:2] == ["git", "clone"]
    assert "https://github.com/x/y.git" in captured[0]
    assert str(dest) in captured[0]


def test_clone_raises_git_command_error_on_non_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=argv,
            returncode=128,
            stdout="",
            stderr="fatal: repository not found",
        )

    monkeypatch.setattr("ralph_executor.git_ops.run_text", fake_run)

    with pytest.raises(GitCommandError, match="repository not found"):
        git_ops.clone("https://github.com/missing/repo.git", tmp_path / "x")


def test_clone_passes_timeout_to_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_timeout: list[object] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_timeout.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ralph_executor.git_ops.run_text", fake_run)
    git_ops.clone("https://github.com/x/y.git", tmp_path / "x", timeout=60.0)

    assert captured_timeout == [60.0]
