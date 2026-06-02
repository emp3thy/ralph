"""End-to-end: a Python crash in run_loop produces a bug PBI in inbox/."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ralph_executor import cli, loop
from ralph_executor.config import ExecutorConfig


def test_python_crash_in_iteration_emits_autobug_pbi(
    monkeypatch: pytest.MonkeyPatch, fake_repo: Path, cfg_for_repo: ExecutorConfig
) -> None:
    def _bad_pull(_cfg: ExecutorConfig) -> None:
        raise RuntimeError("e2e crash")

    monkeypatch.setattr(loop, "_pull_queue", _bad_pull)

    cfg = replace(cfg_for_repo, bot_author_email="bot@e.com")

    with pytest.raises(RuntimeError, match="e2e crash"):
        cli.run_loop_with_autobug(cfg)

    inbox = fake_repo / ".ralph" / "inbox"
    autobug_dirs = [d for d in inbox.iterdir() if d.is_dir() and d.name.startswith("autobug-")]
    assert autobug_dirs, "expected an autobug PBI to land in inbox/"
    bug_md = (autobug_dirs[0] / "BUG.md").read_text(encoding="utf-8")
    assert "RuntimeError" in bug_md
    assert "e2e crash" in bug_md
