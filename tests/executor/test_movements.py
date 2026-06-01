"""Tests for ``ralph_executor.queue.movements``."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.config import ExecutorConfig
from ralph_executor.git_ops import PushRebaseConflict
from ralph_executor.queue.claim import CLAIM_FILENAME, read_claim
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue.movements import (
    QueueMovementError,
    UncommittedSource,
    move_current_to_blocked,
    move_current_to_pending_pr,
    move_inbox_to_current,
)
from ralph_executor.safety.cycle_detector import (
    SignalKind,
    evaluate_same_file_thrashing,
)
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


def _populate_inbox(fake_repo: Path, pbi_id: str = "WI-1234") -> Path:
    pbi_dir = write_sample_pbi(fake_repo, pbi_id=pbi_id)
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", f"inbox: {pbi_id}")
    _git(fake_repo, "push", "origin", "main")
    return pbi_dir


def test_move_inbox_to_current_relocates_directory(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    moved = move_inbox_to_current(cfg_for_repo, pbi)
    assert moved.status == "current"
    assert moved.path == fake_repo / ".ralph" / "current" / "WI-1234"
    assert not (fake_repo / ".ralph" / "inbox" / "WI-1234").exists()
    assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()


def test_move_inbox_to_current_rewrites_status(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    moved = move_inbox_to_current(cfg_for_repo, pbi)
    entry = (moved.path / "PBI.md").read_text(encoding="utf-8")
    assert "status: current" in entry
    assert "status: inbox" not in entry


def test_move_inbox_to_current_pushes_commit(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _populate_inbox(fake_repo)
    remote_before = _git(fake_repo, "ls-remote", "origin", "main").split()[0]
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    move_inbox_to_current(cfg_for_repo, pbi)
    remote_after = _git(fake_repo, "ls-remote", "origin", "main").split()[0]
    assert remote_before != remote_after


def test_move_current_to_pending_pr(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    pbi = move_inbox_to_current(cfg_for_repo, pbi)
    moved = move_current_to_pending_pr(cfg_for_repo, pbi)
    assert moved.status == "pending-pr"
    assert moved.path == fake_repo / ".ralph" / "pending-pr" / "WI-1234"
    assert not (fake_repo / ".ralph" / "current" / "WI-1234").exists()
    entry = (moved.path / "PBI.md").read_text(encoding="utf-8")
    assert "status: pending-pr" in entry


def test_move_current_to_blocked(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    pbi = move_inbox_to_current(cfg_for_repo, pbi)
    moved = move_current_to_blocked(cfg_for_repo, pbi)
    assert moved.status == "blocked"
    assert moved.path == fake_repo / ".ralph" / "blocked" / "WI-1234"
    entry = (moved.path / "PBI.md").read_text(encoding="utf-8")
    assert "status: blocked" in entry


def test_move_from_wrong_state_raises(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    with pytest.raises(QueueMovementError, match="must be in current"):
        move_current_to_pending_pr(cfg_for_repo, pbi)


def test_pbi_opened_event_emitted_on_claim(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    now = datetime.now(tz=UTC)
    event_log = open_log(fake_repo)
    try:
        move_inbox_to_current(cfg_for_repo, pbi, event_log=event_log, now=now)
        events = event_log.recent(window=timedelta(hours=1), now=now)
    finally:
        event_log.close()
    opened = [ev for ev in events if ev.kind == EventType.PBI_OPENED]
    assert len(opened) == 1
    assert opened[0].pbi_id == "WI-1234"
    assert opened[0].payload == {}


def test_move_inbox_to_current_emits_no_event_when_event_log_omitted(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    move_inbox_to_current(cfg_for_repo, pbi)
    now = datetime.now(tz=UTC)
    event_log = open_log(fake_repo)
    try:
        events = event_log.recent(window=timedelta(hours=1), now=now)
    finally:
        event_log.close()
    assert [ev for ev in events if ev.kind == EventType.PBI_OPENED] == []


def test_same_file_thrashing_trips_after_ten_distinct_prs_touching_one_file(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """End-to-end wiring: ten PR_CREATED events through ``move_current_to_pending_pr``
    sharing a single file trip ``evaluate_same_file_thrashing``.

    This is the integration counterpart to the pure-function detector tests
    in ``tests/safety/test_cycle_detector.py`` -- it exercises the real
    emission site against the real ``EventLog``.
    """
    target_file = "src/auth/handler.py"
    for i in range(10):
        pbi_dir = write_sample_pbi(fake_repo, pbi_id=f"WI-2{i:03d}")
        _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
        _git(fake_repo, "commit", "-m", f"inbox: WI-2{i:03d}")
    _git(fake_repo, "push", "origin", "main")

    now = datetime.now(tz=UTC)
    event_log = open_log(fake_repo)
    try:
        for i in range(10):
            pbi_id = f"WI-2{i:03d}"
            source = FilesystemQueueSource(cfg_for_repo)
            inbox = [p for p in source.inbox_pbis() if p.id == pbi_id]
            assert inbox, f"expected {pbi_id} in inbox before claim"
            claimed = move_inbox_to_current(cfg_for_repo, inbox[0])
            move_current_to_pending_pr(
                cfg_for_repo,
                claimed,
                event_log=event_log,
                pr_url=f"https://example.com/pr/{pbi_id}",
                touched_files=[target_file],
                now=now - timedelta(hours=20 - i * 2),
            )
        events = event_log.recent(window=timedelta(hours=24), now=now)
    finally:
        event_log.close()

    pr_created = [ev for ev in events if ev.kind == EventType.PR_CREATED]
    assert len(pr_created) == 10
    assert {ev.pbi_id for ev in pr_created} == {f"WI-2{i:03d}" for i in range(10)}

    signal = evaluate_same_file_thrashing(events, now)
    assert signal is not None
    assert signal.kind == SignalKind.SAME_FILE_THRASHING
    assert target_file in signal.description


def test_move_raises_uncommitted_source_when_dir_not_in_index(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """Regression: BUG-CLAIM-RACE-UNCOMMITTED-INBOX-DIR.

    An external writer can create the inbox dir + entry file on disk but
    have not yet run ``git commit`` on the queue branch by the time the
    executor's next sweep picks the dir up. Without the guard, ``git mv``
    fails with ``fatal: source directory is empty`` and ``GitCommandError``
    propagates out of ``_claim_pbi`` and crashes the loop.

    With the guard, ``_move`` raises ``UncommittedSource(pbi_id)`` so
    ``iterate_once`` can convert it to a recoverable iteration outcome.
    """
    # Write the inbox dir + entry file on disk but DO NOT git add / commit.
    pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-RACE")
    assert pbi_dir.is_dir()
    # Parse it directly through the queue source so we get a real PBI
    # dataclass (the source walks the filesystem without a tracked-status
    # filter, exactly as production does).
    source = FilesystemQueueSource(cfg_for_repo)
    candidates = [p for p in source.inbox_pbis() if p.id == "WI-RACE"]
    assert candidates, "uncommitted dir must still be visible to the queue source"
    with pytest.raises(UncommittedSource) as excinfo:
        move_inbox_to_current(cfg_for_repo, candidates[0])
    assert excinfo.value.pbi_id == "WI-RACE"
    # The dir is left in place so the next iteration can retry after the
    # external writer's commit lands.
    assert pbi_dir.is_dir()


def test_move_pushes_to_configured_queue_branch(
    cfg_for_repo: ExecutorConfig, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_move's final push_with_rebase targets cfg.queue_branch, not hardcoded 'main'."""
    import dataclasses

    from ralph_executor.queue import movements as movements_mod

    _populate_inbox(fake_repo)
    # Override the conftest's queue_branch="main" so the assertion is
    # meaningful. ExecutorConfig is frozen so use dataclasses.replace.
    # We also stub push_with_rebase since the bare remote only has main.
    cfg = dataclasses.replace(cfg_for_repo, queue_branch="ralph-queue")
    source = FilesystemQueueSource(cfg)
    pbi = source.inbox_pbis()[0]

    pushed_branches: list[str] = []

    def fake_push(repo: Path, *, remote: str, branch: str) -> None:
        pushed_branches.append(branch)

    monkeypatch.setattr(movements_mod.git_ops, "push_with_rebase", fake_push)

    move_inbox_to_current(cfg, pbi)

    assert pushed_branches == ["ralph-queue"]


def test_move_inbox_to_current_survives_concurrent_remote_advance(
    cfg_for_repo: ExecutorConfig, fake_repo: Path, tmp_path: Path
) -> None:
    """A concurrent push to origin/main must not crash the move.

    Without push_with_rebase the second push is rejected as non-FF and
    GitCommandError propagates. With it, the helper fetches + rebases
    onto the racing commit and the move's push succeeds.
    """
    _populate_inbox(fake_repo)

    # Simulate a concurrent writer: clone the bare remote, add a commit
    # touching an unrelated file on main, push it back.
    bare_remote = tmp_path / "queue.git"
    racer = tmp_path / "racer"
    subprocess.run(
        ["git", "clone", "--branch", "main", str(bare_remote), str(racer)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(racer), "config", "user.email", "r@x"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(racer), "config", "user.name", "r"],
        check=True,
        capture_output=True,
    )
    (racer / "race.txt").write_text("from-racer\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(racer), "add", "race.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(racer), "commit", "-m", "race: concurrent commit"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(racer), "push", "origin", "main"],
        check=True,
        capture_output=True,
    )

    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    moved = move_inbox_to_current(cfg_for_repo, pbi)
    assert moved.status == "current"

    # The racing commit is now part of main's history.
    log = _git(fake_repo, "log", "--format=%s", "main", "-5").splitlines()
    assert "race: concurrent commit" in log
    # And the move's own commit landed on top of (or rebased above) it.
    assert any("move WI-1234 from inbox to current" in line for line in log)


# ---------------------------------------------------------------------------
# Task 9: claim path writes CLAIM.json atomically with the move + pins
# the commit subject + rolls back on push-rebase conflict.
# ---------------------------------------------------------------------------


def test_claim_writes_claim_json_and_pins_commit_subject(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """When ``instance_id`` + ``hostname`` are provided, ``move_inbox_to_current``
    writes ``CLAIM.json`` into ``current/<id>/`` AND pins the commit
    subject to ``chore(queue): claim <id> for <instance_id>``.

    The ``commit_all`` call inside ``_move`` runs ``git add -A`` before
    committing, so the brand-new ``CLAIM.json`` lands in the SAME commit
    as the ``git mv`` rename + the ``_rewrite_status`` frontmatter edit.
    Single-commit atomicity is the load-bearing invariant — a partial
    state on the queue branch would be visible to a second ralph as
    "claimed without owner" or "no claim but in current/".
    """
    pbi_dir = _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    pinned_when = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    moved = move_inbox_to_current(
        cfg_for_repo,
        pbi,
        instance_id="ralph-a",
        hostname="box-alpha",
        claimed_at=pinned_when,
    )

    # CLAIM.json present with the expected content (Claim round-trip).
    claim_path = moved.path / CLAIM_FILENAME
    assert claim_path.is_file()
    claim = read_claim(claim_path)
    assert claim.instance_id == "ralph-a"
    assert claim.hostname == "box-alpha"
    assert claim.claimed_at == pinned_when.isoformat()

    # JSON shape: only the three documented keys, hostname is the literal
    # operator-provided string (no double-encoding regression).
    raw = json.loads(claim_path.read_text(encoding="utf-8"))
    assert sorted(raw.keys()) == ["claimed_at", "hostname", "instance_id"]
    assert raw["hostname"] == "box-alpha"

    # Source dir is gone from inbox/.
    assert not pbi_dir.exists()

    # Commit subject is the pinned claim subject.
    subject = _git(fake_repo, "log", "-1", "--format=%s", "main").strip()
    assert subject == "chore(queue): claim WI-1234 for ralph-a"

    # Atomic: the SINGLE claim commit touches the moved entry file AND
    # CLAIM.json. ``git diff-tree --name-only`` of HEAD enumerates the
    # paths in the commit.
    files_in_commit = sorted(
        line
        for line in _git(
            fake_repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
        ).splitlines()
        if line.strip()
    )
    assert ".ralph/current/WI-1234/CLAIM.json" in files_in_commit
    assert ".ralph/current/WI-1234/PBI.md" in files_in_commit
    # Only the moved PBI's inbox path is referenced — no unrelated
    # inbox/ paths get pulled into the claim commit.
    stray_inbox = [
        p
        for p in files_in_commit
        if p.startswith(".ralph/inbox/") and not p.startswith(".ralph/inbox/WI-1234")
    ]
    assert stray_inbox == []


def test_claim_requires_hostname_when_instance_id_set(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """``hostname`` is required whenever ``instance_id`` is provided —
    the claim marker carries both, so passing one without the other is
    a caller bug and surfaces as ``QueueMovementError``."""
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    with pytest.raises(QueueMovementError, match="hostname is required"):
        move_inbox_to_current(cfg_for_repo, pbi, instance_id="ralph-a")


def test_claim_loses_rebase_race_cleanly(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PushRebaseConflict`` from the claim push must roll back the
    local clone to ``origin/<queue_branch>`` and re-raise.

    Without the rollback the loser's local clone diverges from origin:
    it has a local claim commit + working tree that moved the PBI into
    ``current/<id>/``, while origin's tip has the winner's claim of the
    same PBI. The next iteration's ``_pull_queue`` (ff-only pull) would
    then fail. Rolling back to ``origin/<branch>`` drops the local
    commit + resets the working tree, leaving no leftover
    ``current/<id>/`` on the loser.

    Stubs ``push_with_rebase`` to raise ``PushRebaseConflict`` AFTER the
    local commit has already been made by ``commit_all`` — exactly the
    state ``push_with_rebase``'s real implementation leaves behind when
    it ``rebase --abort``s.
    """
    from ralph_executor import git_ops as git_ops_mod

    pbi_dir = _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]

    def fake_push(repo: Path, *, remote: str, branch: str) -> None:
        raise PushRebaseConflict(("inbox/WI-1234/PBI.md",))

    monkeypatch.setattr(git_ops_mod, "push_with_rebase", fake_push)

    with pytest.raises(PushRebaseConflict):
        move_inbox_to_current(
            cfg_for_repo,
            pbi,
            instance_id="ralph-b",
            hostname="box-beta",
        )

    # Local rollback: working tree reset to origin/<queue_branch>, so the
    # PBI is back in inbox/ and there's no leftover current/<id>/ or
    # CLAIM.json on the losing clone.
    assert pbi_dir.is_dir(), "inbox/<id>/ must be restored after rollback"
    current_dir = fake_repo / ".ralph" / "current" / "WI-1234"
    assert not current_dir.exists(), "no leftover current/<id>/ on the losing clone"
    # Local HEAD matches origin/<queue_branch> — no diverged commit
    # would otherwise break the next iteration's ff-only pull.
    local_head = _git(fake_repo, "rev-parse", "HEAD").strip()
    origin_head = _git(fake_repo, "rev-parse", f"origin/{cfg_for_repo.queue_branch}").strip()
    assert local_head == origin_head


def test_claim_uses_now_when_claimed_at_omitted(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """``claimed_at`` defaults to ``datetime.now(UTC).isoformat()`` when
    the caller does not pin it. The resulting string must be ISO-8601
    UTC and parseable back to a tz-aware datetime."""
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]

    before = datetime.now(tz=UTC)
    moved = move_inbox_to_current(
        cfg_for_repo,
        pbi,
        instance_id="ralph-a",
        hostname="box-alpha",
    )
    after = datetime.now(tz=UTC)

    claim = read_claim(moved.path / CLAIM_FILENAME)
    parsed = datetime.fromisoformat(claim.claimed_at)
    assert parsed.tzinfo is not None, "claimed_at must be tz-aware"
    assert before - timedelta(seconds=1) <= parsed <= after + timedelta(seconds=1)


def test_claim_omitted_instance_id_keeps_legacy_subject_and_no_claim_file(
    cfg_for_repo: ExecutorConfig, fake_repo: Path
) -> None:
    """When ``instance_id`` is NOT provided, ``move_inbox_to_current``
    falls back to the legacy commit subject (per the back-compat shim
    consumed by the older test fixtures that don't thread the multi-ralph
    args) AND does not write ``CLAIM.json``. Guards against accidentally
    coupling the helper to the Task-9 claim path for every caller."""
    _populate_inbox(fake_repo)
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]

    moved = move_inbox_to_current(cfg_for_repo, pbi)

    assert not (moved.path / CLAIM_FILENAME).exists()
    subject = _git(fake_repo, "log", "-1", "--format=%s", "main").strip()
    assert subject == "chore(ralph-queue): move WI-1234 from inbox to current"
