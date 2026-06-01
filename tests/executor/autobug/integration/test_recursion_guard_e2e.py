"""End-to-end: recursion guard prevents nested autobug emission."""

from __future__ import annotations

import contextlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ralph_executor import claude_spawn
from ralph_executor.autobug import detect
from ralph_executor.autobug.types import Context
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import parse_pbi_directory


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
