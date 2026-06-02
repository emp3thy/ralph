"""closed_at stamping on move-to-done (T6.5)."""

from __future__ import annotations

from pathlib import Path

from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import parse_pbi_directory
from ralph_executor.queue.movements import (
    move_current_to_pending_pr,
    move_inbox_to_current,
    move_pending_pr_to_done,
)
from tests.executor.conftest import _git, write_sample_pbi


def _stage_and_commit(fake_repo: Path, pbi_dir: Path) -> None:
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", f"chore(queue): add {pbi_dir.name}")
    _git(fake_repo, "push", "origin", "main")


def test_move_pending_pr_to_done_stamps_closed_at(
    fake_repo: Path, cfg_for_repo: ExecutorConfig
) -> None:
    pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-DONE", where="inbox")
    _stage_and_commit(fake_repo, pbi_dir)
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    pbi = move_inbox_to_current(cfg_for_repo, pbi)
    pbi = move_current_to_pending_pr(cfg_for_repo, pbi)
    moved = move_pending_pr_to_done(cfg_for_repo, pbi)
    entry = moved.path / "PBI.md"
    text = entry.read_text(encoding="utf-8")
    assert "closed_at:" in text


def test_move_inbox_to_current_does_not_stamp_closed_at(
    fake_repo: Path, cfg_for_repo: ExecutorConfig
) -> None:
    pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-NOCL", where="inbox")
    _stage_and_commit(fake_repo, pbi_dir)
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    moved = move_inbox_to_current(cfg_for_repo, pbi)
    text = (moved.path / "PBI.md").read_text(encoding="utf-8")
    assert "closed_at:" not in text
