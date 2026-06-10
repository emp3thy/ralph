"""Tests for ``ralph_executor.pbi_claim``."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import iterate_once
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.types import PBI
from tests.executor.conftest import write_sample_pbi


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


def _populate_inbox(fake_repo: Path, pbi_id: str = "WI-1234", severity: str = "normal") -> None:
    """Seed an inbox PBI directly on the queue clone's ``main`` branch."""
    write_sample_pbi(fake_repo, pbi_id=pbi_id, severity=severity)
    _git(fake_repo, "add", f".ralph/inbox/{pbi_id}")
    _git(fake_repo, "commit", "-m", f"inbox: {pbi_id}")
    _git(fake_repo, "push", "origin", "main")


# ----------------------------------------------------------------------
# ClaimError + read_target_repo_from_pbi
# ----------------------------------------------------------------------


def _build_pbi(pbi_dir: Path, pbi_id: str) -> PBI:
    return PBI(
        id=pbi_id,
        type="feature",
        status="current",
        severity="normal",
        attempts=0,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        path=pbi_dir,
    )


def test_read_target_repo_from_pbi_reads_frontmatter(tmp_path: Path) -> None:
    """Helper reads target_repo field from PBI.md frontmatter."""
    from ralph_executor.pbi_claim import read_target_repo_from_pbi

    pbi_dir = tmp_path / "WI-1"
    pbi_dir.mkdir()
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        "id: WI-1\n"
        "type: feature\n"
        "status: current\n"
        "severity: normal\n"
        "attempts: 0\n"
        "target_repo: https://github.com/emp3thy/ralph\n"
        "---\n"
        "# Title\n",
        encoding="utf-8",
    )
    pbi = _build_pbi(pbi_dir, "WI-1")
    assert read_target_repo_from_pbi(pbi) == "https://github.com/emp3thy/ralph"


def test_read_target_repo_from_pbi_raises_when_missing(tmp_path: Path) -> None:
    """Missing target_repo field raises ClaimError."""
    from ralph_executor.pbi_claim import ClaimError, read_target_repo_from_pbi

    pbi_dir = tmp_path / "WI-2"
    pbi_dir.mkdir()
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        "id: WI-2\n"
        "type: feature\n"
        "status: current\n"
        "severity: normal\n"
        "attempts: 0\n"
        "---\n"
        "# Title (no target_repo)\n",
        encoding="utf-8",
    )
    pbi = _build_pbi(pbi_dir, "WI-2")
    with pytest.raises(ClaimError, match="missing target_repo"):
        read_target_repo_from_pbi(pbi)


def test_read_target_repo_from_pbi_raises_when_no_entry_file(tmp_path: Path) -> None:
    """Missing entry file (no PBI.md/BUG.md/FEEDBACK.md) raises ClaimError."""
    from ralph_executor.pbi_claim import ClaimError, read_target_repo_from_pbi

    pbi_dir = tmp_path / "WI-3"
    pbi_dir.mkdir()
    pbi = _build_pbi(pbi_dir, "WI-3")
    with pytest.raises(ClaimError, match="no entry file"):
        read_target_repo_from_pbi(pbi)


def test_read_target_repo_from_pbi_uses_bug_md_for_bug_type(tmp_path: Path) -> None:
    """Bug PBIs (no PBI.md, but BUG.md present) get target_repo from BUG.md."""
    from ralph_executor.pbi_claim import read_target_repo_from_pbi

    pbi_dir = tmp_path / "WI-4"
    pbi_dir.mkdir()
    (pbi_dir / "BUG.md").write_text(
        "---\n"
        "id: WI-4\n"
        "type: bug\n"
        "status: current\n"
        "severity: high\n"
        "attempts: 0\n"
        "target_repo: https://github.com/acme/svc\n"
        "---\n"
        "# Bug\n",
        encoding="utf-8",
    )
    pbi = _build_pbi(pbi_dir, "WI-4")
    assert read_target_repo_from_pbi(pbi) == "https://github.com/acme/svc"


# ----------------------------------------------------------------------
# parse + host check inside claim_pbi
# ----------------------------------------------------------------------


def _write_pbi_with_target(pbi_dir: Path, pbi_id: str, target_repo: str) -> None:
    """Write a minimal PBI.md with a custom ``target_repo`` value."""
    pbi_dir.mkdir(parents=True, exist_ok=True)
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        f"id: {pbi_id}\n"
        "type: feature\n"
        "status: current\n"
        "severity: normal\n"
        "attempts: 0\n"
        f'target_repo: "{target_repo}"\n'
        "---\n"
        f"# {pbi_id}\n",
        encoding="utf-8",
    )


def test_claim_raises_claim_error_for_non_github_host(
    cfg_for_repo: ExecutorConfig, tmp_path: Path
) -> None:
    """A PBI with target_repo on a non-github host raises ClaimError 'unsupported host'."""
    from ralph_executor.pbi_claim import ClaimError, claim_pbi

    pbi_dir = tmp_path / "WI-ADO"
    _write_pbi_with_target(pbi_dir, "WI-ADO", "https://dev.azure.com/myorg/myproj/_git/myrepo")
    pbi = _build_pbi(pbi_dir, "WI-ADO")
    with pytest.raises(ClaimError, match="unsupported host"):
        claim_pbi(cfg_for_repo, pbi)


def test_claim_raises_claim_error_for_invalid_url(
    cfg_for_repo: ExecutorConfig, tmp_path: Path
) -> None:
    """A PBI with a malformed target_repo raises ClaimError 'invalid target_repo URL'."""
    from ralph_executor.pbi_claim import ClaimError, claim_pbi

    pbi_dir = tmp_path / "WI-BAD"
    _write_pbi_with_target(pbi_dir, "WI-BAD", "not a url")
    pbi = _build_pbi(pbi_dir, "WI-BAD")
    with pytest.raises(ClaimError, match="invalid target_repo URL"):
        claim_pbi(cfg_for_repo, pbi)


# ----------------------------------------------------------------------
# ensure_clone + worktree creation inside clone
# ----------------------------------------------------------------------


def test_claim_clones_target_and_creates_worktree_in_clone(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: a claim runs ensure_clone, creates the per-PBI
    worktree INSIDE the target clone, and returns a PBI with target_info +
    work_worktree populated."""
    from ralph_executor.pbi_claim import claim_pbi
    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    cfg = cfg_for_repo
    custom_ws = cfg.workspace_root
    clone_root = custom_ws / "clones" / "test-repo"

    ensure_clone_calls: list[tuple[TargetRepoInfo, Path]] = []

    def _fake_ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        ensure_clone_calls.append((info, workspace_root))
        clone_root.mkdir(parents=True, exist_ok=True)
        # The ``_claim_pbi_worktree`` pre-flight checks
        # ``origin/<main_branch>`` exists in the clone before any
        # ``move_inbox_to_current``. Materialise the ref so this happy-path
        # test still exercises the worktree branch.
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

    ensure_wt_calls: list[dict[str, object]] = []

    def _fake_ensure_worktree(
        git_root: Path,
        *,
        worktree_path: Path,
        branch: str,
        create_branch_from: str | None = None,
    ) -> None:
        ensure_wt_calls.append(
            {
                "git_root": Path(git_root),
                "worktree_path": Path(worktree_path),
                "branch": branch,
                "create_branch_from": create_branch_from,
            }
        )

    _populate_inbox(fake_repo, pbi_id="WI-CLONE")

    # Prime the queue worktree and pick the PBI BEFORE installing the
    # ensure_worktree stub — otherwise pull_queue's real ensure_worktree
    # call gets replaced with the no-op stub and git pull errors on a
    # non-existent directory.
    from ralph_executor.queue_git import pull_queue

    pull_queue(cfg)
    source = FilesystemQueueSource(cfg)
    picked = source.pick_next()
    assert picked is not None and picked.id == "WI-CLONE"

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _fake_ensure_clone)
    # The claim path's ensure_worktree call now lives inside
    # ``worktree_manager.materialise_worktree`` — patch it there.
    monkeypatch.setattr("ralph_executor.worktree_manager.ensure_worktree", _fake_ensure_worktree)

    claimed = claim_pbi(cfg, picked)

    # ensure_clone was called with the parsed info + custom workspace.
    assert len(ensure_clone_calls) == 1
    called_info, called_ws = ensure_clone_calls[0]
    assert called_info.host == "github.com"
    assert called_info.owner == "test"
    assert called_info.name == "repo"
    assert called_ws == custom_ws

    # The per-PBI worktree was materialised against the CLONE, not ralph's repo.
    work_wt_calls = [c for c in ensure_wt_calls if c["branch"] == "ralph/WI-CLONE"]
    assert len(work_wt_calls) == 1
    assert work_wt_calls[0]["git_root"] == clone_root
    assert work_wt_calls[0]["worktree_path"] == clone_root / ".ralph-work" / "WI-CLONE"
    assert work_wt_calls[0]["create_branch_from"] == "origin/main"

    # Returned PBI carries the multi-target fields.
    assert claimed.target_repo == "https://github.com/test/repo"
    assert claimed.target_info is not None
    assert claimed.target_info.owner == "test"
    assert claimed.target_info.name == "repo"
    assert claimed.work_worktree == clone_root / ".ralph-work" / "WI-CLONE"


def test_claim_raises_claim_error_when_clone_unreachable(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure_clone -> TargetUnreachable maps to ClaimError("target unreachable: ...")."""
    from ralph_executor.pbi_claim import ClaimError, claim_pbi
    from ralph_executor.queue_git import pull_queue
    from ralph_executor.target_clone import TargetUnreachable
    from ralph_executor.url_utils import TargetRepoInfo

    _populate_inbox(fake_repo, pbi_id="WI-NET")
    pull_queue(cfg_for_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    picked = source.pick_next()
    assert picked is not None

    def _raise_unreachable(info: TargetRepoInfo, workspace_root: Path) -> None:
        raise TargetUnreachable("network unreachable")

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _raise_unreachable)

    with pytest.raises(ClaimError, match=r"target unreachable: network unreachable"):
        claim_pbi(cfg_for_repo, picked)


# ----------------------------------------------------------------------
# Regression: empty target repo (no origin/main) must NOT crash the loop
# ----------------------------------------------------------------------


def _init_empty_target_clone(clone_root: Path) -> None:
    """Materialise a non-empty local git repo with NO ``origin/<main>`` ref.

    Used by the empty-target-repo regression test below. The clone has
    an origin remote registered (so git_ops talks to it cleanly) but no
    ``refs/remotes/origin/main`` ref — simulating a freshly-cloned empty
    GitHub repository.
    """
    clone_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(clone_root)],
        check=True,
        capture_output=True,
    )
    _git(clone_root, "config", "user.email", "test@example.com")
    _git(clone_root, "config", "user.name", "Test User")
    _git(clone_root, "commit", "--allow-empty", "-m", "chore: initial")
    _git(clone_root, "remote", "add", "origin", "file:///nonexistent.git")


def test_iterate_once_moves_pbi_to_blocked_when_target_origin_main_missing(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty target repo (no ``origin/main``) must not crash iterate_once.

    Pre-bug behaviour: ``ensure_worktree`` raised a raw ``GitCommandError``
    AFTER the inbox -> current move had already committed, killing the
    loop and stranding the PBI in ``current/`` with no worktree.

    Fixed behaviour: ``_claim_pbi_worktree`` pre-flights ``origin/<main>``
    before the move, raises ``ClaimError``, ``iterate_once`` demotes
    the PBI inbox -> blocked with the reason recorded in HISTORY.md,
    and the loop continues to the next iteration.
    """
    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    _populate_inbox(fake_repo, pbi_id="WI-EMPTY")

    empty_clone = tmp_path / "ws" / "clones" / "test" / "repo"
    _init_empty_target_clone(empty_clone)

    def _fake_ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        return TargetClone(info=info, clone_root=empty_clone)

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _fake_ensure_clone)

    result = iterate_once(cfg_for_repo)

    assert result.outcome == "claim_failed"
    assert result.pbi_id == "WI-EMPTY"
    assert (fake_repo / ".ralph" / "blocked" / "WI-EMPTY").is_dir()
    assert not (fake_repo / ".ralph" / "inbox" / "WI-EMPTY").exists()
    assert not (fake_repo / ".ralph" / "current" / "WI-EMPTY").exists()
    history = (fake_repo / ".ralph" / "blocked" / "WI-EMPTY" / "HISTORY.md").read_text(
        encoding="utf-8"
    )
    assert "Claim failed" in history
    assert "origin/main" in history
