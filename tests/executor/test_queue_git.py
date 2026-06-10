"""Tests for ``ralph_executor.queue_git``."""

from __future__ import annotations

import dataclasses
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import ClaudeOutcome
from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import iterate_once
from ralph_executor.safety.events import EventType, open_log
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


def test_persist_iteration_writes_excludes_state_dir(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_persist_iteration_writes must stage ONLY the PBI dir, never the
    .ralph/state/ tree (events.db, halt sentinel, etc. are per-checkout
    local state — committing them would produce a noisy commit every
    iteration even when Claude wrote nothing).
    """
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim

    # Drop a sentinel file under .ralph/state/ — it must NOT end up in
    # any commit the persist helper creates.
    state_marker = fake_repo / ".ralph" / "state" / "marker.txt"
    state_marker.parent.mkdir(parents=True, exist_ok=True)
    state_marker.write_text("local-only", encoding="utf-8")

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)

    # The marker is still untracked — never committed.
    tracked = _git(fake_repo, "ls-files", ".ralph/state/marker.txt").strip()
    assert tracked == "", "state/marker.txt was incorrectly tracked"
    assert state_marker.read_text(encoding="utf-8") == "local-only"


def test_file_touched_event_emitted_on_iteration_commit(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After ``_persist_iteration_writes`` commits Claude's HISTORY.md
    edit, a ``FILE_TOUCHED`` event must land in the log with the path
    of the changed file in its payload. The cycle detector reserves
    this event for future per-iteration rules; emit unconditionally
    for forward compatibility.
    """
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim WI-1234 → current/

    history_path = fake_repo / ".ralph" / "current" / "WI-1234" / "HISTORY.md"
    history_before = history_path.read_text(encoding="utf-8")

    def _appending_spawn(cfg: ExecutorConfig, pbi: object, **kwargs: object) -> ClaudeOutcome:
        history_path.write_text(
            history_before + "\n## Iteration 1 — partial\n",
            encoding="utf-8",
        )
        return ClaudeOutcome(
            kind="partial",
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _appending_spawn)
    iterate_once(cfg_for_repo)

    now = datetime.now(tz=UTC)
    event_log = open_log(fake_repo)
    try:
        events = event_log.recent(window=timedelta(hours=1), now=now)
    finally:
        event_log.close()
    touched = [ev for ev in events if ev.kind == EventType.FILE_TOUCHED]
    assert len(touched) == 1, f"expected one FILE_TOUCHED event, got {touched!r}"
    assert touched[0].pbi_id == "WI-1234"
    files = touched[0].payload.get("files")
    assert isinstance(files, list) and files, "FILE_TOUCHED payload must list files"
    assert any("HISTORY.md" in path for path in files), (
        f"expected HISTORY.md in touched files, got {files!r}"
    )


def test_file_touched_skipped_on_empty_commit(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Claude writes nothing inside the PBI dir during a partial
    iteration, ``_persist_iteration_writes`` produces no new commit
    and must NOT emit ``FILE_TOUCHED`` — empty payloads would dilute
    the cycle detector's signal.
    """
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)

    now = datetime.now(tz=UTC)
    event_log = open_log(fake_repo)
    try:
        events = event_log.recent(window=timedelta(hours=1), now=now)
    finally:
        event_log.close()
    assert [ev for ev in events if ev.kind == EventType.FILE_TOUCHED] == []


def test_pull_queue_calls_ensure_queue_clone(
    cfg_for_repo: ExecutorConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pull_queue`` must delegate to ``queue_clone.ensure_queue_clone``."""
    from ralph_executor import queue_git

    cfg = dataclasses.replace(
        cfg_for_repo,
        workspace_root=tmp_path,
        queue_repo="https://github.com/example/q",
    )
    calls: list[tuple[Path, str, str, str]] = []

    def fake_ensure(
        workspace_root: Path,
        queue_repo: str,
        queue_branch: str,
        *,
        instance_id: str,
        timeout: float = 120.0,
    ) -> Path:
        calls.append((workspace_root, queue_repo, queue_branch, instance_id))
        return workspace_root / f"queue-{instance_id}"

    monkeypatch.setattr(queue_git, "ensure_queue_clone", fake_ensure)

    queue_git.pull_queue(cfg)

    assert calls == [
        (tmp_path, "https://github.com/example/q", cfg.queue_branch, cfg.instance_id),
    ]


def test_pull_queue_passes_configured_branch(
    cfg_for_repo: ExecutorConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pull_queue`` forwards ``cfg.queue_branch`` to ``ensure_queue_clone``."""
    from ralph_executor import queue_git

    cfg = dataclasses.replace(
        cfg_for_repo,
        workspace_root=tmp_path,
        queue_repo="https://github.com/example/q",
        queue_branch="custom-branch",
    )
    captured: dict[str, object] = {}

    def fake_ensure(
        workspace_root: Path,
        queue_repo: str,
        queue_branch: str,
        *,
        instance_id: str,
        timeout: float = 120.0,
    ) -> Path:
        captured["queue_branch"] = queue_branch
        captured["instance_id"] = instance_id
        return workspace_root / f"queue-{instance_id}"

    monkeypatch.setattr(queue_git, "ensure_queue_clone", fake_ensure)

    queue_git.pull_queue(cfg)

    assert captured["queue_branch"] == "custom-branch"
    assert captured["instance_id"] == cfg.instance_id
