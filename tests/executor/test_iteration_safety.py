"""Tests for ``ralph_executor.iteration_safety``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import ClaudeOutcome
from ralph_executor.config import ExecutorConfig
from ralph_executor.iteration import iterate_once, run_loop
from ralph_executor.iteration_safety import run_sweep
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.safety.events import EventType, open_log
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

    monkeypatch.setattr("ralph_executor.iteration._check_cycle_detector", _trip)
    monkeypatch.setattr(
        "ralph_executor.iteration.spawn_claude_p",
        _stub_spawn("partial"),
    )
    # run_loop raises HaltedError on the first iteration where the
    # cycle detector trips -- it never returns normally.
    with pytest.raises(HaltedError):
        list(run_loop(cfg_for_repo, max_iterations=5))


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

    # Ensure no inherited env can satisfy the sweep — only cfg matters.
    monkeypatch.delenv("RALPH_ADO_AUTHOR_EMAIL", raising=False)

    cfg = replace(cfg_for_repo, bot_author_email="")
    source = FilesystemQueueSource(cfg)
    with caplog.at_level("WARNING", logger="ralph_executor.iteration_safety"):
        run_sweep(cfg, source)

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

    # If run_sweep regressed to reading env, this poisoned env would
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
    # Must return a SweepResult-shaped object — run_sweep logs .pbis_scanned /
    # .actions / .errors after the call.
    from types import SimpleNamespace

    monkeypatch.setattr(
        "ralph_executor.sweep.run",
        lambda ctx: SimpleNamespace(pbis_scanned=0, actions=[], errors=[]),
    )

    cfg = replace(
        cfg_for_repo,
        bot_author_email="ralph@x.test",
        stale_days=5,
        auto_merge_clean_prs=True,
    )
    # Bypass the scripts-path check via the injectable resolver seam.
    run_sweep(cfg, FilesystemQueueSource(cfg), pr_skill_scripts_path=lambda _cfg: fake_repo)

    assert captured["ralph_author_email"] == "ralph@x.test"
    assert captured["stale_threshold"] == timedelta(days=5)
    assert captured["max_attempts"] == cfg.max_attempts
    assert captured["auto_merge_clean_prs"] is True


def test_run_sweep_does_not_read_env_for_promoted_knobs(
    cfg_for_repo: ExecutorConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a regression where someone re-adds os.environ.get for these
    two names inside run_sweep."""
    import ralph_executor.iteration_safety as iteration_safety_mod

    src = Path(iteration_safety_mod.__file__).read_text(encoding="utf-8")
    assert 'os.environ.get("RALPH_ADO_AUTHOR_EMAIL"' not in src
    assert 'os.environ.get("RALPH_STALE_DAYS"' not in src


def test_run_sweep_queue_root_points_at_queue_clone(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep's ``queue_root`` must point at the queue clone's
    ``.ralph/`` (where pending-pr/ actually lives). After the queue-repo
    split there is only one queue path: ``<workspace_root>/queue/.ralph/``.
    """
    from dataclasses import replace

    captured: dict[str, object] = {}

    class _SpySweepContext:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "ralph_executor.sweep.runner.SweepContext",
        _SpySweepContext,
    )
    from types import SimpleNamespace

    monkeypatch.setattr(
        "ralph_executor.sweep.run",
        lambda ctx: SimpleNamespace(pbis_scanned=0, actions=[], errors=[]),
    )

    cfg = replace(cfg_for_repo, bot_author_email="ralph@x.test")
    run_sweep(cfg, FilesystemQueueSource(cfg), pr_skill_scripts_path=lambda _cfg: fake_repo)

    expected = fake_repo / ".ralph"
    assert captured["queue_root"] == expected, (
        f"sweep queue_root must point at the queue clone's .ralph/; "
        f"got {captured['queue_root']!r}, expected {expected!r}"
    )


def test_event_log_lives_in_queue_clone(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every ``open_log`` call targets the queue clone. The
    ``PBI_OPENED`` event from ``move_inbox_to_current`` must land in
    ``<queue-clone>/.ralph/state/events.db`` — that's the file the cycle
    detector reads on subsequent process restarts."""
    _populate_inbox(fake_repo)
    monkeypatch.setattr("ralph_executor.iteration.spawn_claude_p", _stub_spawn("partial"))

    iterate_once(cfg_for_repo)

    queue_db = fake_repo / ".ralph" / "state" / "events.db"
    assert queue_db.is_file(), f"event log must live in the queue clone; expected at {queue_db}"
    event_log = open_log(fake_repo)
    try:
        events = event_log.recent(window=timedelta(hours=1), now=datetime.now(tz=UTC))
    finally:
        event_log.close()
    pbi_opened = [e for e in events if e.kind == EventType.PBI_OPENED]
    assert pbi_opened, "PBI_OPENED event missing from queue-clone event log"
