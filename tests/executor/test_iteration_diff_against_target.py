"""T6: ``_run_ralph`` computes touched files against the per-PBI target clone.

After the KILL-RALPH-HOME refactor, ``cfg.repo_path`` no longer drives any
per-iteration filesystem operation. The pr_created branch of ``_run_ralph``
must read the diff from the target clone derived from
``pbi.target_info`` (``<workspace_root>/clones/<owner>/<name>/``), not
from ``cfg.repo_path`` (which is ralph's own checkout).

Also covers the missing-target_info defensive branch: a resumed PBI whose
``target_info`` is ``None`` (malformed frontmatter) must NOT crash and
must surface ``touched_files=[]`` to ``move_current_to_pending_pr``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import ClaudeOutcome
from ralph_executor.config import ExecutorConfig
from ralph_executor.types import PBI
from ralph_executor.url_utils import TargetRepoInfo


def _make_pbi(
    pbi_dir: Path,
    *,
    target_info: TargetRepoInfo | None,
    work_worktree: Path | None = None,
) -> PBI:
    pbi_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=UTC)
    return PBI(
        id="WI-T6",
        type="feature",
        status="current",
        severity="normal",
        attempts=0,
        created_at=now,
        updated_at=now,
        path=pbi_dir,
        target_info=target_info,
        work_worktree=work_worktree,
    )


def test_diff_names_uses_target_clone_root(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pr_created path must call ``git_ops.diff_names`` against
    ``<workspace_root>/clones/<owner>/<name>/`` — never against
    ``cfg.repo_path``."""
    from ralph_executor.iteration import _run_ralph

    info = TargetRepoInfo(host="github.com", owner="acme", name="widget")
    clone_root_expected = cfg_for_repo.workspace_root / "clones" / "acme" / "widget"
    clone_root_expected.mkdir(parents=True, exist_ok=True)

    pbi_dir = fake_repo / ".ralph" / "current" / "WI-T6"
    pbi = _make_pbi(pbi_dir, target_info=info)

    seen: dict[str, object] = {}

    def _fake_diff_names(repo: Path, base: str, head: str) -> list[str]:
        seen["repo"] = repo
        seen["base"] = base
        seen["head"] = head
        return ["a.py", "b.py"]

    monkeypatch.setattr("ralph_executor.iteration.git_ops.diff_names", _fake_diff_names)

    def _fake_spawn(
        cfg: ExecutorConfig,
        pbi_arg: PBI,
        *,
        cwd: Path | None = None,
        pbi_dir: Path | None = None,
    ) -> ClaudeOutcome:
        return ClaudeOutcome(
            kind="pr_created",
            pr_url="https://example.com/pr/1",
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )

    monkeypatch.setattr("ralph_executor.iteration.spawn_claude_p", _fake_spawn)

    captured_touched: dict[str, list[str]] = {}

    def _fake_move(
        cfg: ExecutorConfig,
        pbi_arg: PBI,
        *,
        event_log,  # noqa: ANN001
        pr_url: str | None,
        touched_files,  # noqa: ANN001
        now,  # noqa: ANN001
    ) -> None:
        captured_touched["touched"] = list(touched_files)

    monkeypatch.setattr("ralph_executor.iteration.move_current_to_pending_pr", _fake_move)
    monkeypatch.setattr("ralph_executor.iteration._cleanup_work_worktree", lambda cfg, pbi: None)

    outcome, result = _run_ralph(cfg_for_repo, pbi)

    assert outcome.kind == "pr_created"
    assert result.outcome == "ran_pr_created"
    assert seen["repo"] == clone_root_expected
    assert captured_touched["touched"] == ["a.py", "b.py"]


def test_diff_names_skipped_when_target_info_missing(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """target_info=None (malformed frontmatter on resume) must NOT crash;
    diff_names is not called and touched_files is empty."""
    from ralph_executor.iteration import _run_ralph

    pbi_dir = fake_repo / ".ralph" / "current" / "WI-T6-NOTGT"
    pbi = _make_pbi(pbi_dir, target_info=None)

    called: dict[str, bool] = {"diff": False}

    def _fake_diff_names(repo: Path, base: str, head: str) -> list[str]:
        called["diff"] = True
        return []

    monkeypatch.setattr("ralph_executor.iteration.git_ops.diff_names", _fake_diff_names)

    def _fake_spawn(
        cfg: ExecutorConfig,
        pbi_arg: PBI,
        *,
        cwd: Path | None = None,
        pbi_dir: Path | None = None,
    ) -> ClaudeOutcome:
        return ClaudeOutcome(
            kind="pr_created",
            pr_url="https://example.com/pr/2",
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )

    monkeypatch.setattr("ralph_executor.iteration.spawn_claude_p", _fake_spawn)

    captured: dict[str, list[str]] = {}

    def _fake_move(
        cfg: ExecutorConfig,
        pbi_arg: PBI,
        *,
        event_log,  # noqa: ANN001
        pr_url: str | None,
        touched_files,  # noqa: ANN001
        now,  # noqa: ANN001
    ) -> None:
        captured["touched"] = list(touched_files)

    monkeypatch.setattr("ralph_executor.iteration.move_current_to_pending_pr", _fake_move)
    monkeypatch.setattr("ralph_executor.iteration._cleanup_work_worktree", lambda cfg, pbi: None)

    with caplog.at_level("WARNING", logger="ralph_executor.iteration"):
        outcome, result = _run_ralph(cfg_for_repo, pbi)

    assert outcome.kind == "pr_created"
    assert result.outcome == "ran_pr_created"
    assert called["diff"] is False
    assert captured["touched"] == []
    assert any("target_info" in r.getMessage() for r in caplog.records)


def test_diff_names_skipped_when_clone_root_missing(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """target_info parsed but the deterministic clone_root is not on disk
    (transient fetch failure earlier in the iteration) must NOT crash;
    diff_names is not called and touched_files is empty.
    """
    from ralph_executor.iteration import _run_ralph

    info = TargetRepoInfo(host="github.com", owner="nope", name="missing-clone")
    # Deliberately do NOT mkdir ``<ws>/clones/nope/missing-clone``.

    pbi_dir = fake_repo / ".ralph" / "current" / "WI-T6-NOCLONE"
    pbi = _make_pbi(pbi_dir, target_info=info)

    called: dict[str, bool] = {"diff": False}

    def _fake_diff_names(repo: Path, base: str, head: str) -> list[str]:
        called["diff"] = True
        return []

    monkeypatch.setattr("ralph_executor.iteration.git_ops.diff_names", _fake_diff_names)

    def _fake_spawn(
        cfg: ExecutorConfig,
        pbi_arg: PBI,
        *,
        cwd: Path | None = None,
        pbi_dir: Path | None = None,
    ) -> ClaudeOutcome:
        return ClaudeOutcome(
            kind="pr_created",
            pr_url="https://example.com/pr/3",
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )

    monkeypatch.setattr("ralph_executor.iteration.spawn_claude_p", _fake_spawn)

    captured: dict[str, list[str]] = {}

    def _fake_move(
        cfg: ExecutorConfig,
        pbi_arg: PBI,
        *,
        event_log,  # noqa: ANN001
        pr_url: str | None,
        touched_files,  # noqa: ANN001
        now,  # noqa: ANN001
    ) -> None:
        captured["touched"] = list(touched_files)

    monkeypatch.setattr("ralph_executor.iteration.move_current_to_pending_pr", _fake_move)
    monkeypatch.setattr("ralph_executor.iteration._cleanup_work_worktree", lambda cfg, pbi: None)

    with caplog.at_level("WARNING", logger="ralph_executor.iteration"):
        outcome, result = _run_ralph(cfg_for_repo, pbi)

    assert outcome.kind == "pr_created"
    assert result.outcome == "ran_pr_created"
    assert called["diff"] is False
    assert captured["touched"] == []
    assert any(
        "target clone" in r.getMessage() and "missing" in r.getMessage() for r in caplog.records
    )


def test_sweep_repo_name_is_queue_clone_name(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_sweep labels SweepContext.repo_name with the queue clone
    directory name (``queue-<instance_id>`` under workspace_root).

    After KILL-RALPH-HOME T8, ``ExecutorConfig.repo_path`` is gone — the
    sweep label is unambiguously derived from ``_queue_repo_root(cfg).name``.
    Scope 1 multi-ralph: the queue clone is namespaced per-instance so
    the label is ``queue-<instance_id>`` (here ``queue-test-ralph``).
    """
    from ralph_executor.iteration import _run_sweep
    from ralph_executor.queue.filesystem import FilesystemQueueSource
    from ralph_executor.sweep.runner import SweepResult

    seen: dict[str, str] = {}

    def _fake_run_sweep(*, ctx) -> SweepResult:  # noqa: ANN001
        seen["repo_name"] = ctx.repo_name
        return SweepResult(pbis_scanned=0)

    monkeypatch.setattr("ralph_executor.sweep.run", _fake_run_sweep)

    cfg = replace(cfg_for_repo, bot_author_email="ralph-bot@example.com")
    _run_sweep(cfg, FilesystemQueueSource(cfg))

    assert seen["repo_name"] == "queue-test-ralph"
