"""Tests for queue_clone.ensure_queue_clone using local bare git repos."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph_executor.queue_clone import QueueCloneError, ensure_queue_clone


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_bare_remote(tmp_path: Path) -> Path:
    """Build a bare repo with an initial commit on main, return its path."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "--initial-branch=main")

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    (seed / "README.md").write_text("queue\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "origin", "main")
    return bare


def _clone_into(dest: Path, remote: Path) -> None:
    """Helper for legacy-rename tests: clone ``remote`` into ``dest``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", f"file://{remote.as_posix()}", str(dest)],
        check=True,
        capture_output=True,
    )


def test_first_call_clones(tmp_path: Path) -> None:
    remote = _make_bare_remote(tmp_path)
    workspace = tmp_path / "ws"

    path = ensure_queue_clone(
        workspace, f"file://{remote.as_posix()}", "main", instance_id="test-ralph"
    )

    assert path == workspace / "queue-test-ralph"
    assert (path / ".git").exists()
    assert (path / "README.md").read_text(encoding="utf-8") == "queue\n"


def test_second_call_fetches_and_pulls(tmp_path: Path) -> None:
    remote = _make_bare_remote(tmp_path)
    workspace = tmp_path / "ws"

    path = ensure_queue_clone(
        workspace, f"file://{remote.as_posix()}", "main", instance_id="test-ralph"
    )

    push_src = tmp_path / "push_src"
    subprocess.run(
        ["git", "clone", str(remote), str(push_src)],
        check=True,
        capture_output=True,
    )
    (push_src / "new.md").write_text("new\n", encoding="utf-8")
    _git(push_src, "add", ".")
    _git(push_src, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "second")
    _git(push_src, "push", "origin", "main")

    ensure_queue_clone(workspace, f"file://{remote.as_posix()}", "main", instance_id="test-ralph")
    assert (path / "new.md").exists()


def test_bad_url_raises_queue_clone_error(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    with pytest.raises(QueueCloneError) as exc:
        ensure_queue_clone(
            workspace,
            "file:///definitely/not/a/repo",
            "main",
            instance_id="test-ralph",
        )
    assert "queue" in str(exc.value).lower()


def test_ensure_queue_clone_uses_branch_flag_on_clone(tmp_path, monkeypatch):
    """First-run clone uses `git clone -b <queue_branch> <url> <dest>`."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured_args: list[list[str]] = []

    def fake_run(argv, capture_output, timeout):  # noqa: ARG001
        captured_args.append(argv)
        # Simulate the clone creating .git
        if argv[0] == "git" and "clone" in argv:
            dest = Path(argv[-1])
            (dest / ".git").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ralph_executor.queue_clone.run_text", fake_run)

    ensure_queue_clone(
        workspace,
        "https://github.com/test/queue",
        "ralph-queue",
        instance_id="test-ralph",
    )

    clone_cmd = next(a for a in captured_args if "clone" in a)
    assert "-b" in clone_cmd
    assert "ralph-queue" in clone_cmd
    # ordering: ... clone -b <branch> <url> <dest>
    b_idx = clone_cmd.index("-b")
    assert clone_cmd[b_idx + 1] == "ralph-queue"


def test_ensure_queue_clone_pulls_configured_branch(tmp_path, monkeypatch):
    """Refresh pull uses `git pull --ff-only origin <queue_branch>`."""
    workspace = tmp_path / "workspace"
    dest = workspace / "queue-test-ralph"
    (dest / ".git").mkdir(parents=True)

    captured_args: list[list[str]] = []

    def fake_run(argv, capture_output, timeout):  # noqa: ARG001
        captured_args.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ralph_executor.queue_clone.run_text", fake_run)

    ensure_queue_clone(
        workspace,
        "https://github.com/test/queue",
        "ralph-queue",
        instance_id="test-ralph",
    )

    pull_cmd = next(a for a in captured_args if "pull" in a)
    # ['git', '-C', <dest>, 'pull', '--ff-only', 'origin', 'ralph-queue']
    assert "ralph-queue" in pull_cmd
    assert pull_cmd[-2:] == ["origin", "ralph-queue"]


# --- Task 6: namespaced path + legacy rename ------------------------------


def test_namespaced_path(tmp_path: Path) -> None:
    """``ensure_queue_clone`` writes into ``<workspace>/queue-<instance_id>/``."""
    remote = _make_bare_remote(tmp_path)
    workspace = tmp_path / "ws"

    dest = ensure_queue_clone(
        workspace,
        f"file://{remote.as_posix()}",
        "main",
        instance_id="ralph-a",
    )

    assert dest == workspace / "queue-ralph-a"
    assert (dest / ".git").is_dir()


def test_legacy_queue_renamed_to_namespaced(tmp_path: Path) -> None:
    """A pre-existing ``queue/`` is renamed once to ``queue-<instance_id>/``."""
    remote = _make_bare_remote(tmp_path)
    workspace = tmp_path / "ws"
    legacy = workspace / "queue"
    _clone_into(legacy, remote)

    dest = ensure_queue_clone(
        workspace,
        f"file://{remote.as_posix()}",
        "main",
        instance_id="ralph-a",
    )

    assert dest == workspace / "queue-ralph-a"
    assert not legacy.exists()
    assert (dest / ".git").is_dir()


def test_namespaced_only_is_noop_on_legacy(tmp_path: Path) -> None:
    """When only the namespaced path exists, no rename happens (refresh-only)."""
    remote = _make_bare_remote(tmp_path)
    workspace = tmp_path / "ws"
    namespaced = workspace / "queue-ralph-a"
    _clone_into(namespaced, remote)

    dest = ensure_queue_clone(
        workspace,
        f"file://{remote.as_posix()}",
        "main",
        instance_id="ralph-a",
    )

    assert dest == namespaced
    assert (dest / ".git").is_dir()
    assert not (workspace / "queue").exists()


def test_both_paths_exist_refuses(tmp_path: Path) -> None:
    """When BOTH ``queue/`` and ``queue-<instance_id>/`` exist, refuse loudly."""
    remote = _make_bare_remote(tmp_path)
    workspace = tmp_path / "ws"
    legacy = workspace / "queue"
    namespaced = workspace / "queue-ralph-a"
    _clone_into(legacy, remote)
    _clone_into(namespaced, remote)

    with pytest.raises(QueueCloneError, match="both legacy queue/ and queue-ralph-a/"):
        ensure_queue_clone(
            workspace,
            f"file://{remote.as_posix()}",
            "main",
            instance_id="ralph-a",
        )

    assert legacy.exists()
    assert namespaced.exists()
