"""Tests for ``ralph_executor.loop``."""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import ClaudeOutcome
from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import (
    iterate_once,
    run_loop,
)
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.safety.events import EventType, open_log
from ralph_executor.worktree import (
    list_worktrees,
    queue_worktree_path,
    work_worktree_path,
)
from tests.executor.conftest import write_sample_pbi


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    ).stdout


def _populate_inbox(fake_repo: Path, pbi_id: str = "WI-1234", severity: str = "normal") -> None:
    _git(fake_repo, "checkout", "ralph-queue")
    write_sample_pbi(fake_repo, pbi_id=pbi_id, severity=severity)
    _git(fake_repo, "add", f".ralph/inbox/{pbi_id}")
    _git(fake_repo, "commit", "-m", f"inbox: {pbi_id}")
    _git(fake_repo, "push", "origin", "ralph-queue")
    _git(fake_repo, "checkout", "main")


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


def test_iterate_once_idle_when_no_work(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "idle"
    assert result.pbi_id is None


def test_iterate_once_claims_inbox_pbi_when_current_empty(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "claimed"
    assert result.pbi_id == "WI-1234"
    # After claim, the PBI should be on disk under current/, not inbox/.
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()
    assert not (fake_repo / ".ralph" / "inbox" / "WI-1234").exists()
    # The feature branch ralph/WI-1234 must exist after a fresh claim.
    branches = _git(fake_repo, "branch", "--list", "ralph/WI-1234").strip()
    assert branches != "", "ralph/WI-1234 branch should be created on claim"


def test_iterate_once_runs_ralph_when_current_occupied(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    # Two iterations: first claims, second spawns Ralph (returns partial).
    iterate_once(cfg_for_repo)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "ran_partial"
    assert result.pbi_id == "WI-1234"
    # PBI stays in current/.
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()


def test_iterate_once_moves_to_pending_pr_when_pr_created(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("pr_created", pr_url="https://example/pr/1"),
    )
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "ran_pr_created"
    assert result.pr_url == "https://example/pr/1"
    assert (fake_repo / ".ralph" / "pending-pr" / "WI-1234").is_dir()
    assert not (fake_repo / ".ralph" / "current" / "WI-1234").exists()


def test_iterate_once_moves_to_blocked_when_stuck(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)
    # Simulate Ralph writing STUCK.md before exit.
    pbi_dir = fake_repo / ".ralph" / "current" / "WI-1234"

    def _stuck_spawn(cfg: ExecutorConfig, pbi: object) -> ClaudeOutcome:
        (pbi_dir / "STUCK.md").write_text("# stuck\n", encoding="utf-8")
        return ClaudeOutcome(
            kind="stuck",
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.0,
        )

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stuck_spawn)
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "ran_stuck"
    assert (fake_repo / ".ralph" / "blocked" / "WI-1234").is_dir()


def test_iterate_once_treats_error_like_partial(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("error"),
    )
    result = iterate_once(cfg_for_repo)
    assert result.outcome == "ran_error"
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()


def test_partial_outcome_does_not_increment_attempts(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``partial`` outcomes are legitimate multi-step progress
    and must NOT decrement the attempts budget."""
    from ralph_executor.safety.attempts import read_attempts

    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim
    pbi_dir = fake_repo / ".ralph" / "current" / "WI-1234"
    before = read_attempts(pbi_dir)

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)
    iterate_once(cfg_for_repo)
    iterate_once(cfg_for_repo)

    assert read_attempts(pbi_dir) == before, "partial must not increment attempts"


def test_error_outcome_increments_attempts(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``error`` outcomes count as failed iterations and DO
    decrement the budget."""
    from ralph_executor.safety.attempts import read_attempts

    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim
    pbi_dir = fake_repo / ".ralph" / "current" / "WI-1234"
    before = read_attempts(pbi_dir)

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("error"),
    )
    iterate_once(cfg_for_repo)

    assert read_attempts(pbi_dir) == before + 1, "error must increment attempts"


def test_stuck_outcome_increments_attempts(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``stuck`` outcomes also count as failed iterations.

    The PBI is moved to ``blocked/`` by ``handle_stuck`` so we record the
    attempts value AFTER the move (from the new location).
    """
    from ralph_executor.safety.attempts import read_attempts

    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim
    pbi_dir = fake_repo / ".ralph" / "current" / "WI-1234"
    before = read_attempts(pbi_dir)

    def _stuck_spawn(cfg: ExecutorConfig, pbi: object) -> ClaudeOutcome:
        (pbi_dir / "STUCK.md").write_text("# stuck\n", encoding="utf-8")
        return ClaudeOutcome(
            kind="stuck",
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.0,
        )

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stuck_spawn)
    iterate_once(cfg_for_repo)

    blocked_dir = fake_repo / ".ralph" / "blocked" / "WI-1234"
    assert blocked_dir.is_dir(), "stuck should move PBI to blocked/"
    assert read_attempts(blocked_dir) == before + 1, "stuck must increment attempts"


def test_iterate_once_invokes_sweep_stub_when_current_empty(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan 8 fills in the sweep; for v1 the stub must be invoked."""
    called: list[bool] = []

    def _spy_sweep(cfg: ExecutorConfig, source: FilesystemQueueSource) -> None:
        called.append(True)

    monkeypatch.setattr("ralph_executor.loop._run_sweep", _spy_sweep)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)
    assert called == [True], "sweep stub must be invoked when current/ is empty"


def test_iterate_once_invokes_cycle_detector_stub(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan 9 fills in cycle detection; for v1 the stub must be invoked."""
    called: list[bool] = []

    def _spy_check(cfg: ExecutorConfig, source: FilesystemQueueSource) -> bool:
        called.append(True)
        return False

    monkeypatch.setattr("ralph_executor.loop._check_cycle_detector", _spy_check)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)
    assert called == [True]


def test_iterate_once_pulls_ralph_queue_every_iteration(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pull_calls: list[str] = []
    from ralph_executor import git_ops as real_git_ops

    original_pull = real_git_ops.pull

    def _spy_pull(repo: Path, branch: str, remote: str = "origin") -> None:
        pull_calls.append(branch)
        original_pull(repo, branch, remote)

    monkeypatch.setattr("ralph_executor.loop.git_ops.pull", _spy_pull)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)
    assert "ralph-queue" in pull_calls


def test_iterate_once_pulls_main_only_on_fresh_claim(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_inbox(fake_repo)
    pull_calls: list[str] = []
    from ralph_executor import git_ops as real_git_ops

    original_pull = real_git_ops.pull

    def _spy_pull(repo: Path, branch: str, remote: str = "origin") -> None:
        pull_calls.append(branch)
        original_pull(repo, branch, remote)

    monkeypatch.setattr("ralph_executor.loop.git_ops.pull", _spy_pull)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    iterate_once(cfg_for_repo)  # claim → main pulled
    assert "main" in pull_calls

    pull_calls.clear()
    iterate_once(cfg_for_repo)  # current occupied → main NOT pulled
    assert "main" not in pull_calls
    assert "ralph-queue" in pull_calls


def test_run_loop_terminates_when_cycle_detector_trips(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_loop`` raises ``HaltedError`` when the cycle detector trips.

    Plan 9 changed the contract: ``_check_cycle_detector`` returning ``True``
    now causes ``iterate_once`` to raise ``HaltedError`` (after writing the
    META-BUG + sentinel), which ``run_loop`` re-raises immediately.
    """
    from ralph_executor.safety import HaltedError

    def _trip(cfg: ExecutorConfig, source: FilesystemQueueSource) -> bool:
        return True

    monkeypatch.setattr("ralph_executor.loop._check_cycle_detector", _trip)
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )
    # run_loop raises HaltedError on the first iteration where the
    # cycle detector trips -- it never returns normally.
    with pytest.raises(HaltedError):
        list(run_loop(cfg_for_repo, max_iterations=5))


def test_iterate_once_persists_claude_history_writes_on_partial(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: when Claude appends to HISTORY.md during a partial-
    outcome iteration, the executor must commit + push that change on
    ralph-queue. Without _persist_iteration_writes, Claude's edits sit
    dirty in the working tree and get lost on the next iteration's
    branch checkout. Caught by first end-to-end self-host smoke
    (Ralph PR #7 — TEST-001 left HISTORY.md uncommitted on ralph-queue).
    """
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo)  # claim TEST-001 → current/

    history_path = fake_repo / ".ralph" / "current" / "WI-1234" / "HISTORY.md"
    history_before = history_path.read_text(encoding="utf-8")

    def _appending_spawn(cfg: ExecutorConfig, pbi: object) -> ClaudeOutcome:
        # Claude appends an iteration record to HISTORY.md, then exits
        # with partial (multi-step PBI, no PR yet).
        history_path.write_text(
            history_before + "\n## Iteration 1 — partial — picked step 1\n",
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

    head_before_iter = _git(fake_repo, "rev-parse", "HEAD").strip()
    result = iterate_once(cfg_for_repo)
    head_after_iter = _git(fake_repo, "rev-parse", "HEAD").strip()

    assert result.outcome == "ran_partial"
    # The persistence step must have produced a new commit on ralph-queue.
    assert head_after_iter != head_before_iter, (
        "expected _persist_iteration_writes to commit HISTORY.md edits"
    )
    # The PBI directory has no uncommitted changes after persist — local
    # per-checkout state like .ralph/state/events.db (Plan 9 event log)
    # is intentionally NOT staged, so untracked state/ may remain.
    pbi_status = _git(fake_repo, "status", "--porcelain", ".ralph/current/WI-1234").strip()
    assert pbi_status == "", f"PBI dir dirty after persist: {pbi_status!r}"
    # HISTORY.md on disk contains the appended iteration record.
    assert "## Iteration 1" in history_path.read_text(encoding="utf-8")
    # Commit message names the PBI.
    last_msg = _git(fake_repo, "log", "-1", "--pretty=%s").strip()
    assert "WI-1234" in last_msg


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

    def _appending_spawn(cfg: ExecutorConfig, pbi: object) -> ClaudeOutcome:
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


def test_run_sweep_skips_when_bot_author_email_empty(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without bot_author_email set, sweep must skip with a WARNING that
    still mentions the legacy env-var name so operators grepping logs see
    the same anchor."""
    from dataclasses import replace

    from ralph_executor.loop import _run_sweep

    # Ensure no inherited env can satisfy the sweep — only cfg matters.
    monkeypatch.delenv("RALPH_ADO_AUTHOR_EMAIL", raising=False)

    cfg = replace(cfg_for_repo, bot_author_email="")
    source = FilesystemQueueSource(cfg)
    with caplog.at_level("WARNING", logger="ralph_executor.loop"):
        _run_sweep(cfg, source)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("bot_author_email" in m for m in msgs), msgs
    assert any("RALPH_ADO_AUTHOR_EMAIL" in m for m in msgs), msgs


def test_run_sweep_passes_cfg_values_to_sweep_config(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SweepConfig must be constructed from cfg fields, not from os.environ."""
    from dataclasses import replace

    from ralph_executor.loop import _run_sweep

    # If _run_sweep regressed to reading env, this poisoned env would
    # cause SweepConfig to be built with the env value rather than cfg's.
    monkeypatch.setenv("RALPH_ADO_AUTHOR_EMAIL", "WRONG@example.com")
    monkeypatch.setenv("RALPH_STALE_DAYS", "999")

    captured: dict[str, object] = {}

    class _SpySweepConfig:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "ralph_executor.sweep.runner.SweepConfig",
        _SpySweepConfig,
    )

    # Stub out the actual sweep run so we don't need a real PR skill on disk.
    # Must return a SweepResult-shaped object — _run_sweep logs .pbis_scanned /
    # .actions / .errors after the call.
    from types import SimpleNamespace

    monkeypatch.setattr(
        "ralph_executor.sweep.run",
        lambda ctx: SimpleNamespace(pbis_scanned=0, actions=[], errors=[]),
    )
    # Bypass the scripts-path check.
    monkeypatch.setattr(
        "ralph_executor.loop._pr_skill_scripts_path",
        lambda cfg: fake_repo,
    )

    cfg = replace(cfg_for_repo, bot_author_email="ralph@x.test", stale_days=5)
    _run_sweep(cfg, FilesystemQueueSource(cfg))

    assert captured["ralph_author_email"] == "ralph@x.test"
    assert captured["stale_threshold"] == timedelta(days=5)
    assert captured["max_attempts"] == cfg.max_attempts


def test_run_sweep_does_not_read_env_for_promoted_knobs(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a regression where someone re-adds os.environ.get for these
    two names inside _run_sweep."""
    import ralph_executor.loop as loop_mod

    src = Path(loop_mod.__file__).read_text(encoding="utf-8")
    assert 'os.environ.get("RALPH_ADO_AUTHOR_EMAIL"' not in src
    assert 'os.environ.get("RALPH_STALE_DAYS"' not in src


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


# ----------------------------------------------------------------------
# Task 9 — worktree-mode integration tests (`cfg.use_worktrees=True`).
# The shared ``cfg_for_repo`` fixture defaults to legacy single-checkout
# (use_worktrees=False); tests below derive a worktree-mode config so
# both branches stay covered. The helpers tests live in
# ``test_worktree.py`` — these focus on the loop integration.
# ----------------------------------------------------------------------


@pytest.fixture
def cfg_for_repo_worktree(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
) -> Iterator[ExecutorConfig]:
    """Worktree-mode variant of ``cfg_for_repo`` with best-effort cleanup.

    Pytest's ``tmp_path`` cleanup trips on Windows when ``.ralph-work/``
    still holds git-registered worktrees; force-remove them on teardown
    so the next test (and the harness) start clean.
    """
    cfg = dataclasses.replace(cfg_for_repo, use_worktrees=True)
    try:
        yield cfg
    finally:
        for entry in list_worktrees(fake_repo):
            path = entry.get("path")
            if isinstance(path, str) and Path(path).resolve() != fake_repo.resolve():
                subprocess.run(
                    ["git", "-C", str(fake_repo), "worktree", "remove", "--force", path],
                    check=False,
                    capture_output=True,
                )
        subprocess.run(
            ["git", "-C", str(fake_repo), "worktree", "prune"],
            check=False,
            capture_output=True,
        )


def test_claim_with_worktrees_creates_queue_and_work_trees(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worktree-mode claim materialises both the long-lived queue tree
    and a per-PBI work tree without touching the primary checkout's
    branch."""
    _populate_inbox(fake_repo)
    primary_before = _git(fake_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("partial"),
    )

    result = iterate_once(cfg_for_repo_worktree)

    assert result.outcome == "claimed"
    queue_wt = queue_worktree_path(fake_repo)
    work_wt = work_worktree_path(fake_repo, "WI-1234")
    # Queue worktree is on ralph-queue with the PBI moved into current/.
    assert queue_wt.is_dir()
    assert (queue_wt / ".ralph" / "current" / "WI-1234").is_dir()
    # Work worktree exists on the feature branch.
    assert work_wt.is_dir()
    work_branch = _git(work_wt, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert work_branch == "ralph/WI-1234"
    # Primary checkout's branch is untouched.
    primary_after = _git(fake_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert primary_after == primary_before


def test_persist_iteration_writes_worktree_mode_commits_from_queue_tree(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In worktree mode ``_persist_iteration_writes`` commits and pushes
    from the queue worktree — the primary checkout is never branch-swapped
    and the new commit lands on ``origin/ralph-queue`` via the queue
    worktree's push."""
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo_worktree)  # claim — creates worktrees

    queue_wt = queue_worktree_path(fake_repo)
    pbi_dir_in_queue = queue_wt / ".ralph" / "current" / "WI-1234"
    history_path = pbi_dir_in_queue / "HISTORY.md"
    history_before = history_path.read_text(encoding="utf-8")

    def _appending_spawn(
        cfg: ExecutorConfig,
        pbi: object,
        *,
        cwd: Path | None = None,
        pbi_dir: Path | None = None,
    ) -> ClaudeOutcome:
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
    head_before = _git(queue_wt, "rev-parse", "HEAD").strip()
    primary_before = _git(fake_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()

    result = iterate_once(cfg_for_repo_worktree)

    assert result.outcome == "ran_partial"
    head_after = _git(queue_wt, "rev-parse", "HEAD").strip()
    assert head_after != head_before, "queue worktree must produce a new commit"
    # Primary's HEAD is unchanged — no branch swap on the primary.
    primary_after = _git(fake_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert primary_after == primary_before
    # The push landed on origin/ralph-queue.
    _git(fake_repo, "fetch", "origin")
    remote_head = _git(fake_repo, "rev-parse", "origin/ralph-queue").strip()
    assert remote_head == head_after
    # No dirty files left in the queue worktree's PBI dir.
    pbi_status = _git(queue_wt, "status", "--porcelain", ".ralph/current/WI-1234").strip()
    assert pbi_status == "", f"queue PBI dir dirty after persist: {pbi_status!r}"


def test_terminal_outcome_removes_work_tree(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the iteration ends in a terminal outcome (pr_created here),
    the per-PBI work worktree is torn down. The queue worktree persists
    across PBIs."""
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo_worktree)  # claim → worktrees created

    work_wt = work_worktree_path(fake_repo, "WI-1234")
    queue_wt = queue_worktree_path(fake_repo)
    assert work_wt.is_dir(), "precondition: work worktree exists after claim"

    monkeypatch.setattr(
        "ralph_executor.loop.spawn_claude_p",
        _stub_spawn("pr_created", pr_url="https://example/pr/9"),
    )
    result = iterate_once(cfg_for_repo_worktree)

    assert result.outcome == "ran_pr_created"
    assert not work_wt.exists(), "work worktree should be removed on pr_created"
    # Queue worktree persists.
    assert queue_wt.is_dir()
    # ``ralph/WI-1234`` ref is preserved — pending-pr PBIs need it.
    feature_ref = _git(fake_repo, "branch", "--list", "ralph/WI-1234").strip()
    assert "ralph/WI-1234" in feature_ref


# ``test_legacy_single_checkout_path_still_works_when_flag_false``: the
# entire test_loop.py suite above uses ``cfg_for_repo`` (use_worktrees=
# False) and exercises claim → spawn → persist → terminal-outcome moves
# end-to-end. The legacy single-checkout path therefore has dense
# regression coverage already; no dedicated test added here.


def test_event_log_lives_in_queue_worktree_under_worktree_mode(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In worktree mode every ``open_log`` call must target the queue
    worktree, not the primary checkout. Otherwise events (notably
    ``PBI_OPENED`` from ``move_inbox_to_current``) silently land in a
    stray ``.ralph/state/events.db`` in the primary checkout — never
    committed/pushed with the queue branch and invisible to the cycle
    detector after a process restart."""
    _populate_inbox(fake_repo)
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn("partial"))

    iterate_once(cfg_for_repo_worktree)

    queue_wt = queue_worktree_path(fake_repo)
    queue_db = queue_wt / ".ralph" / "state" / "events.db"
    primary_db = fake_repo / ".ralph" / "state" / "events.db"
    assert queue_db.is_file(), f"event log must live in the queue worktree; expected at {queue_db}"
    assert not primary_db.exists(), (
        f"primary checkout should never get a stray events.db; found {primary_db}"
    )
    # And the PBI_OPENED event from move_inbox_to_current actually lives there.
    event_log = open_log(queue_wt)
    try:
        events = event_log.recent(window=timedelta(hours=1), now=datetime.now(tz=UTC))
    finally:
        event_log.close()
    pbi_opened = [e for e in events if e.kind == EventType.PBI_OPENED]
    assert pbi_opened, "PBI_OPENED event missing from queue-worktree event log"


def test_stuck_blocked_move_targets_queue_worktree(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In worktree mode `handle_stuck` must operate against the queue
    worktree, not the primary checkout. ``pbi.path`` lives under
    ``.ralph-work/queue/.ralph/current/<id>`` so calling
    ``move_to_blocked`` with ``repo=cfg.repo_path`` raises ValueError
    (``parts[0] == '.ralph-work'`` fails the ``parts[0] != '.ralph'``
    guard in ``ralph_executor/safety/stuck.py::move_to_blocked``).
    The blocked PBI must end up in the queue worktree's ``.ralph/blocked/``,
    where the next ``git push`` from the queue worktree will commit it."""
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo_worktree)  # claim → worktrees created

    queue_wt = queue_worktree_path(fake_repo)
    pbi_dir_in_queue = queue_wt / ".ralph" / "current" / "WI-1234"

    def _stuck_spawn(
        cfg: ExecutorConfig,
        pbi: object,
        *,
        cwd: Path | None = None,
        pbi_dir: Path | None = None,
    ) -> ClaudeOutcome:
        (pbi_dir_in_queue / "STUCK.md").write_text("# stuck\n", encoding="utf-8")
        return ClaudeOutcome(
            kind="stuck",
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.0,
        )

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stuck_spawn)
    result = iterate_once(cfg_for_repo_worktree)

    assert result.outcome == "ran_stuck"
    # Lands in queue worktree, not primary.
    assert (queue_wt / ".ralph" / "blocked" / "WI-1234").is_dir()
    assert not (fake_repo / ".ralph" / "blocked" / "WI-1234").exists()


def test_max_attempts_blocked_move_targets_queue_worktree(
    cfg_for_repo_worktree: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In worktree mode the max-attempts blocked move must target the
    queue worktree's ``.ralph/blocked/``. Otherwise the PBI is moved to
    the primary checkout's working tree (on `main`, no `.ralph/`),
    landing outside git tracking and silently disappearing from the
    queue system."""
    # `max_attempts()` reads RALPH_MAX_ATTEMPTS from the env, not from cfg.
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "1")
    _populate_inbox(fake_repo)
    iterate_once(cfg_for_repo_worktree)  # claim

    queue_wt = queue_worktree_path(fake_repo)

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn("error"))
    # First error: attempts 0 -> 1 (== limit, allowed).
    iterate_once(cfg_for_repo_worktree)
    # Second error: attempts 1 -> 2 (> limit), triggers max-attempts move.
    result = iterate_once(cfg_for_repo_worktree)

    assert result.outcome == "ran_stuck"
    assert (queue_wt / ".ralph" / "blocked" / "WI-1234").is_dir(), (
        "max-attempts blocked move must target the queue worktree"
    )
    assert not (fake_repo / ".ralph" / "blocked" / "WI-1234").exists(), (
        "primary checkout must never receive blocked PBI dirs in worktree mode"
    )
