"""End-to-end: recursion guard prevents nested autobug emission."""

from __future__ import annotations

import contextlib
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ralph_executor import claude_spawn
from ralph_executor.autobug import detect
from ralph_executor.autobug.types import Context
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import parse_pbi_directory
from tests.executor.conftest import _git, write_claude_script


def test_recursion_marker_blocks_python_emission(fake_repo: Path) -> None:
    ctx = Context(
        queue_root=fake_repo,
        state_dir=fake_repo / ".ralph" / "state",
        env={"RALPH_AUTOBUG_DEPTH": "1"},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="sha",
        bot_author_email="bot@e.com",
        triggering_pbi_id=None,
        queue_branch="main",
    )
    try:
        raise RuntimeError("nested")
    except RuntimeError as exc:
        detect.detect_python_crash(
            exc, ctx, target_repo="https://github.com/x/y", severity="critical"
        )
    inbox = fake_repo / ".ralph" / "inbox"
    autobug_dirs = (
        [d for d in inbox.iterdir() if d.is_dir() and d.name.startswith("autobug-")]
        if inbox.is_dir()
        else []
    )
    assert not autobug_dirs, "recursion guard should have suppressed emission"


def test_pbi_with_signature_sets_RALPH_AUTOBUG_DEPTH(
    fake_repo: Path,
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spawning against an autobug PBI must set RALPH_AUTOBUG_DEPTH=1 in child env."""
    captured_envs: list[dict[str, str]] = []
    real_popen = subprocess.Popen

    def _capture_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        captured_envs.append(dict(kwargs.get("env") or {}))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _capture_popen)

    pbi_dir = fake_repo / ".ralph" / "current" / "autobug-zzzzzz-001"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "BUG.md").write_text(
        "---\nid: autobug-zzzzzz-001\ntype: bug\nseverity: critical\n"
        "status: current\nattempts: 0\n"
        "created_at: 2026-05-31T00:00:00+00:00\n"
        "updated_at: 2026-05-31T00:00:00+00:00\n"
        "target_repo: https://github.com/x/y\n"
        "signature: abc123def456\n---\n# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "REPRODUCE.md").write_text("# r\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    pbi = parse_pbi_directory(pbi_dir, status="current")
    with contextlib.suppress(Exception):
        claude_spawn.spawn_claude_p(cfg_for_repo, pbi, cwd=fake_repo, pbi_dir=pbi_dir)
    assert any(env.get("RALPH_AUTOBUG_DEPTH") == "1" for env in captured_envs), (
        f"expected RALPH_AUTOBUG_DEPTH=1 in spawned env; saw {captured_envs}"
    )


def test_subprocess_crash_on_autobug_pbi_does_not_re_emit(
    fake_repo: Path,
    cfg_for_repo: ExecutorConfig,
    fake_claude_binary: Path,
) -> None:
    """Regression: when spawn for an autobug PBI exits non-zero, the
    subprocess wire's autobug context must inherit RALPH_AUTOBUG_DEPTH=1
    from the child env so ``fuses.recursion_check`` suppresses a second
    autobug emission. Before the env-dict fix, the wire used
    ``os.environ`` (parent) instead of the child ``env`` dict and the
    recursion guard always saw depth=0.
    """
    write_claude_script(
        fake_claude_binary,
        "import sys\nsys.stderr.write('Killed\\n')\nsys.exit(137)\n",
    )
    cfg = replace(cfg_for_repo, bot_author_email="bot@e.com")

    pbi_dir = fake_repo / ".ralph" / "current" / "autobug-deadbe-001"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "BUG.md").write_text(
        "---\nid: autobug-deadbe-001\ntype: bug\nseverity: critical\n"
        "status: current\nattempts: 0\n"
        "created_at: 2026-05-31T00:00:00+00:00\n"
        "updated_at: 2026-05-31T00:00:00+00:00\n"
        "target_repo: https://github.com/x/y\n"
        "signature: deadbeefcafe\n---\n# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "REPRODUCE.md").write_text("# r\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", "test: stage autobug-deadbe-001 in current")
    _git(fake_repo, "push", "origin", "main")
    pbi = parse_pbi_directory(pbi_dir, status="current")

    claude_spawn.spawn_claude_p(cfg, pbi, cwd=fake_repo, pbi_dir=pbi_dir)

    inbox = fake_repo / ".ralph" / "inbox"
    nested = (
        [d for d in inbox.iterdir() if d.is_dir() and d.name.startswith("autobug-")]
        if inbox.is_dir()
        else []
    )
    assert not nested, f"recursion guard should suppress nested autobug emission; got {nested}"
