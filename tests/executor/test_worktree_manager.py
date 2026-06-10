"""Tests for ``ralph_executor.worktree_manager``."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.claude_spawn import ClaudeOutcome
from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import iterate_once
from ralph_executor.worktree import work_worktree_path
from tests.executor.conftest import _git, write_sample_pbi


def _populate_inbox(fake_repo: Path, pbi_id: str = "WI-1234", severity: str = "normal") -> None:
    """Seed an inbox PBI directly on the queue clone's ``main`` branch."""
    write_sample_pbi(fake_repo, pbi_id=pbi_id, severity=severity)
    _git(fake_repo, "add", f".ralph/inbox/{pbi_id}")
    _git(fake_repo, "commit", "-m", f"inbox: {pbi_id}")
    _git(fake_repo, "push", "origin", "main")


def _stub_spawn(outcome_kind: str, pr_url: str | None = None) -> object:
    # Accept the worktree-mode ``cwd`` / ``pbi_dir`` kwargs so the same
    # stub works for both legacy and worktree-mode iterations.
    def _fake_spawn(
        cfg: ExecutorConfig,
        pbi: object,
        *,
        cwd: Path | None = None,
        pbi_dir: Path | None = None,
    ) -> ClaudeOutcome:
        return ClaudeOutcome(
            kind=outcome_kind,  # type: ignore[arg-type]
            pr_url=pr_url,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )

    return _fake_spawn


def test_claim_creates_work_worktree_on_feature_branch(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim materialises a per-PBI work worktree under the target
    clone, checked out on the feature branch ``ralph/<PBI-id>``."""
    _populate_inbox(fake_repo)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )

    result = iterate_once(cfg_for_repo)

    assert result.outcome == "claimed"
    # Queue clone has the PBI moved into current/.
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()
    # Work worktree exists on the feature branch (target clone IS fake_repo
    # via the autouse _fake_ensure_target_clone fixture).
    work_wt = work_worktree_path(fake_repo, "WI-1234")
    assert work_wt.is_dir()
    work_branch = _git(work_wt, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert work_branch == "ralph/WI-1234"


def test_terminal_outcome_removes_work_tree(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the iteration ends in a terminal outcome (pr_created here),
    the per-PBI work worktree is torn down. The queue clone persists."""
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim

    work_wt = work_worktree_path(fake_repo, "WI-1234")
    assert work_wt.is_dir(), "precondition: work worktree exists after claim"

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("pr_created", pr_url="https://example/pr/9"),
    )
    result = iterate_once(cfg_for_repo)

    assert result.outcome == "ran_pr_created"
    assert not work_wt.exists(), "work worktree should be removed on pr_created"
    # Queue clone persists.
    assert fake_repo.is_dir()
    # ``ralph/WI-1234`` ref is preserved — pending-pr PBIs need it.
    feature_ref = _git(fake_repo, "branch", "--list", "ralph/WI-1234").strip()
    assert "ralph/WI-1234" in feature_ref
