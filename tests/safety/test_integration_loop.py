"""End-to-end safety integration test against ``ralph_executor.loop``.

This module is self-contained: it builds its own git repo + ExecutorConfig
rather than importing executor fixtures (which would double-register the
executor conftest plugin when the full suite runs).
"""

from __future__ import annotations

import os
import platform
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import ClaudeOutcome
from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import iterate_once
from ralph_executor.safety import HaltedError, HaltStatus, check_halt_sentinel
from ralph_executor.safety.events import EventType, open_log
from tests.safety.conftest import make_event

# ---------------------------------------------------------------------------
# Local helpers (mirrors tests/executor/conftest.py — avoid cross-package
# fixture import which causes "Plugin already registered" on full-suite run)
# ---------------------------------------------------------------------------


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


def _make_fake_claude(tmp_path: Path) -> Path:
    """Return a no-op stand-in ``claude`` binary (mirrors executor conftest)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    py_body = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
    if platform.system() == "Windows":
        py_script = bin_dir / "claude.py"
        py_script.write_text(py_body, encoding="utf-8")
        cmd_script = bin_dir / "claude.cmd"
        cmd_script.write_text(f'@"{sys.executable}" "%~dp0claude.py" %*\n', encoding="utf-8")
        return cmd_script
    script = bin_dir / "claude"
    script.write_text(py_body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _init_repo(tmp_path: Path) -> tuple[Path, ExecutorConfig]:
    """Materialise the queue-clone topology used by the post-split loop.

    Layout::

        <tmp>/queue.git                 bare remote (queue_repo URL)
        <tmp>/ws/queue-test-ralph       queue clone the executor reads/writes

    Returns (queue_clone_path, cfg). ``cfg.workspace_root`` is ``<tmp>/ws``
    and ``cfg.instance_id="test-ralph"``, so ``cfg.queue_clone_path``
    resolves to ``<tmp>/ws/queue-test-ralph/`` and ``ensure_queue_clone``
    no-ops on the already-materialised clone. ``cfg.queue_repo`` is
    ``file://<tmp>/queue.git`` so any rebase-pull can resolve a real remote.
    """
    bare = tmp_path / "queue.git"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    clone = workspace / "queue-test-ralph"

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

    fake_claude = _make_fake_claude(tmp_path)
    cfg = ExecutorConfig(
        queue_repo=f"file://{bare.as_posix()}",
        # NOTE: the bare remote seeded above only carries ``main``, so this
        # fixture stays on ``main`` for the queue branch. The production
        # default is ``ralph-queue`` — exercised by
        # ``tests/executor/test_loop_integration.py``, which builds a
        # dual-branch bare. See the matching comment in
        # ``tests/executor/conftest.py``.
        queue_branch="main",
        main_branch="main",
        max_attempts=3,
        log_level=20,
        iteration_sleep_seconds=0.0,
        claude_binary=str(fake_claude),
        claude_permission_mode="bypassPermissions",
        anthropic_api_key="fake-key",
        git_host="github",
        gh_owner="",
        ado_org_url="",
        ado_project="",
        halt_webhook="",
        pr_check_poll_max_attempts=6,
        pr_check_poll_interval_seconds=30.0,
        instance_id="test-ralph",
        use_worktrees=True,
        workspace_root=workspace,
    )
    return clone, cfg


def _write_pbi_in_current(repo: Path, pbi_id: str, attempts: int = 0) -> None:
    """Write a minimal PBI directory into ``.ralph/current/<pbi_id>`` on the
    queue clone's ``main`` and push to ``origin``."""
    pbi_dir = repo / ".ralph" / "current" / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=True)
    (pbi_dir / "PBI.md").write_text(
        f"---\nid: {pbi_id}\ntype: feature\nstatus: current\n"
        f"severity: normal\nattempts: {attempts}\n"
        f"created_at: 2026-05-24T09:00:00+00:00\n"
        f"updated_at: 2026-05-24T09:00:00+00:00\n"
        f"target_repo: https://github.com/test/repo\n---\n\n# {pbi_id}\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    (pbi_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    _git(repo, "add", f".ralph/current/{pbi_id}")
    _git(repo, "commit", "-m", f"test: seed {pbi_id} in current/")
    _git(repo, "push", "origin", "main")


def _stub_target_clone(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    """Replace ``ensure_clone`` so it returns the queue clone path.

    The integration loop calls ``target_clone.ensure_clone`` for the
    resumed-PBI path and would otherwise try to ``git clone`` a real
    GitHub URL. Returning the queue clone keeps all worktree machinery
    pointed at the in-tmp_path repo.
    """
    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    def _fake(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        return TargetClone(info=info, clone_root=repo)

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _fake)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_iteration_refuses_to_start_when_sentinel_halted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """iterate_once raises HaltedError when the sentinel is in HALTED state."""
    repo, cfg = _init_repo(tmp_path)
    _write_pbi_in_current(repo, "WI-active")
    _stub_target_clone(monkeypatch, repo)

    # Prepend fake claude to PATH so spawn subprocess resolves it.
    monkeypatch.setenv(
        "PATH",
        f"{Path(cfg.claude_binary).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    sentinel_dir = repo / ".ralph" / "state"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel = sentinel_dir / "halted"
    sentinel.write_text(
        "meta_bug_id: META-test\nacknowledged_by:\nacknowledged_at:\n",
        encoding="utf-8",
    )
    assert check_halt_sentinel(repo) == HaltStatus.HALTED
    with pytest.raises(HaltedError):
        iterate_once(cfg)


def test_iteration_resumes_once_sentinel_acknowledged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ACKNOWLEDGED sentinel must not block the loop."""
    repo, cfg = _init_repo(tmp_path)
    _write_pbi_in_current(repo, "WI-active")
    _stub_target_clone(monkeypatch, repo)

    monkeypatch.setenv(
        "PATH",
        f"{Path(cfg.claude_binary).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    from ralph_executor import loop as loop_module

    monkeypatch.setattr(
        loop_module,
        "spawn_claude_p",
        lambda cfg, pbi, *, cwd=None, pbi_dir=None: ClaudeOutcome(
            kind="partial",
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        ),
    )
    monkeypatch.setattr(loop_module, "_check_cycle_detector", lambda cfg, src: False)

    sentinel_dir = repo / ".ralph" / "state"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel = sentinel_dir / "halted"
    sentinel.write_text(
        "meta_bug_id: META-test\n"
        "acknowledged_by: gethin\n"
        "acknowledged_at: 2026-05-24T12:00:00+00:00\n",
        encoding="utf-8",
    )
    assert check_halt_sentinel(repo) == HaltStatus.ACKNOWLEDGED
    # Should not raise.
    iterate_once(cfg)


def test_iteration_triggers_halt_when_detector_fires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-seeded signature_recurrence events cause iterate_once to raise HaltedError."""
    repo, cfg = _init_repo(tmp_path)
    _write_pbi_in_current(repo, "WI-active")
    _stub_target_clone(monkeypatch, repo)

    monkeypatch.setenv(
        "PATH",
        f"{Path(cfg.claude_binary).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    log = open_log(repo)
    try:
        now = datetime.now(tz=UTC)
        log.append(
            make_event(
                kind=EventType.PBI_CLOSED,
                recorded_at=now.replace(microsecond=0) - timedelta(hours=23),
                pbi_id="BUG-old",
                payload={"signature": "ZeroDivisionError @ calc.py:10"},
            )
        )
        log.append(
            make_event(
                kind=EventType.SIGNATURE_OBSERVED,
                recorded_at=now.replace(microsecond=0) - timedelta(hours=1),
                pbi_id="BUG-new",
                payload={"signature": "ZeroDivisionError @ calc.py:10"},
            )
        )
    finally:
        log.close()

    from ralph_executor import loop as loop_module

    monkeypatch.setattr(
        loop_module,
        "spawn_claude_p",
        lambda cfg, pbi, *, cwd=None, pbi_dir=None: ClaudeOutcome(
            kind="partial",
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        ),
    )

    with pytest.raises(HaltedError):
        iterate_once(cfg)

    # The current PBI must remain in current/ (spec: do NOT kick to inbox).
    assert (repo / ".ralph" / "current" / "WI-active").is_dir()
    # A META-cycle-* file was written.
    blocked = repo / ".ralph" / "blocked"
    meta_bugs = list(blocked.glob("META-cycle-*.md"))
    assert meta_bugs, "halt_and_acknowledge must write a META-cycle-* file"


def test_attempts_exceeded_moves_pbi_to_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the attempt counter fires, iterate_once moves the PBI to blocked/.

    The new attempts semantic only increments on ``stuck`` / ``error``
    outcomes, so this test forces an ``error`` outcome (the fake claude
    binary's clean exit would classify as ``partial`` otherwise and not
    decrement the budget at all).
    """
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "1")
    repo, cfg = _init_repo(tmp_path)
    # Write a PBI with attempts already at the limit so the next increment fires.
    _write_pbi_in_current(repo, "WI-overrun", attempts=1)
    _stub_target_clone(monkeypatch, repo)

    monkeypatch.setenv(
        "PATH",
        f"{Path(cfg.claude_binary).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    from ralph_executor import loop as loop_module

    monkeypatch.setattr(loop_module, "_check_cycle_detector", lambda cfg, src: False)
    monkeypatch.setattr(
        loop_module,
        "spawn_claude_p",
        lambda cfg, pbi, *, cwd=None, pbi_dir=None: ClaudeOutcome(
            kind="error",
            pr_url=None,
            stdout="",
            stderr="boom",
            exit_code=2,
            duration_seconds=0.01,
        ),
    )

    result = iterate_once(cfg)
    assert not (repo / ".ralph" / "current" / "WI-overrun").exists()
    assert (repo / ".ralph" / "blocked" / "WI-overrun").is_dir()
    assert result.outcome == "ran_stuck"
