"""``spawn_claude_p`` must raise when neither ``cwd`` nor ``pbi.work_worktree`` is set.

T5 of the queue-driven cleanup plan removed the legacy
``effective_cwd = cfg.repo_path`` fallback. Production callers in
``loop.py`` always populate ``pbi.work_worktree`` (via ``_claim_pbi`` or
the resume path) before invoking ``spawn_claude_p``; absence of both is
a misconfiguration that surfaces as ``ConfigError`` rather than silently
picking an arbitrary cwd.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import spawn_claude_p
from ralph_executor.config import ConfigError
from ralph_executor.types import PBI
from tests.executor.conftest import _build_minimal_cfg


def test_spawn_errors_without_work_worktree(tmp_path: Path) -> None:
    cfg = _build_minimal_cfg(tmp_path)
    pbi_dir = tmp_path / "pbi"
    pbi_dir.mkdir()
    pbi = PBI(
        id="WI-1",
        type="feature",
        status="current",
        severity="normal",
        attempts=0,
        created_at=datetime(2026, 5, 31, tzinfo=UTC),
        updated_at=datetime(2026, 5, 31, tzinfo=UTC),
        path=pbi_dir,
        work_worktree=None,
    )

    with pytest.raises(ConfigError, match="work_worktree"):
        spawn_claude_p(cfg, pbi, pbi_dir=pbi_dir)
