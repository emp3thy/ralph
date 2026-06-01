"""Cross-instance multi-ralph integration tests (Plan v5 Task 16).

T1-T5: realistic iterate_once / movements / lockfile interactions
between two ``ExecutorConfig`` instances sharing a single bare queue
remote. No mocking framework -- pure file IO + ``monkeypatch`` for the
spawn boundary and the target_clone seam.

Per [[d62c9e9e]] each test pins the upstream commit subject after
``push_with_rebase`` lands, not just ``tip_before != tip_after``.
Per [[d342c481]] the fixture ``.gitignore`` covers ``.ralph/state/``,
``.ralph-work/`` AND ``.ralph.lock`` so ``commit_all``'s ``git add -A``
inside ``movements._move`` does not stage runtime state.
"""

from __future__ import annotations

import json
import os
import platform
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import ClaudeOutcome, OutcomeKind
from ralph_executor.config import ExecutorConfig
from ralph_executor.git_ops import PushRebaseConflict
from ralph_executor.lockfile import LockfileError, WorkspaceLockfile
from ralph_executor.loop import iterate_once
from ralph_executor.queue.claim import Claim, read_claim, write_claim
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue.movements import move_inbox_to_current
from ralph_executor.safety import HaltedError, HaltStatus, check_halt_sentinel
from ralph_executor.safety.events import EventType, open_log
from tests.safety.conftest import make_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_fake_claude(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    py_body = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
    if platform.system() == "Windows":
        py = bin_dir / "claude.py"
        py.write_text(py_body, encoding="utf-8")
        cmd = bin_dir / "claude.cmd"
        cmd.write_text(
            f'@"{sys.executable}" "%~dp0claude.py" %*\n',
            encoding="utf-8",
        )
        return cmd
    script = bin_dir / "claude"
    script.write_text(py_body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _init_bare(tmp_path: Path) -> tuple[Path, Path]:
    """Initialise a shared bare remote + seed clone.

    Returns ``(bare_path, seed_clone_path)``. The seed clone is the path
    test helpers push to when they need to mutate the bare's ``main``
    (e.g. seeding an inbox PBI before either ralph instance clones).
    """
    bare = tmp_path / "queue.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(seed)],
        check=True,
        capture_output=True,
    )
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "Test User")
    _git(seed, "commit", "--allow-empty", "-m", "chore: initial main commit")
    # Fixture .gitignore covers all runtime-state paths per [[d342c481]].
    (seed / ".gitignore").write_text(
        ".ralph/state/\n.ralph-work/\n.ralph.lock\n",
        encoding="utf-8",
    )
    _git(seed, "add", ".gitignore")
    _git(seed, "commit", "-m", "chore: gitignore runtime state")
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (seed / ".ralph" / sub).mkdir(parents=True)
        (seed / ".ralph" / sub / ".gitkeep").write_text("", encoding="utf-8")
    _git(seed, "add", ".ralph")
    _git(seed, "commit", "-m", "chore(queue): bootstrap .ralph/ tree")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-u", "origin", "main")
    return bare, seed


def _build_cfg(
    *,
    bare: Path,
    workspace_root: Path,
    instance_id: str,
    fake_claude: Path,
) -> ExecutorConfig:
    return ExecutorConfig(
        queue_repo=f"file://{bare.as_posix()}",
        queue_branch="main",
        main_branch="main",
        max_attempts=3,
        log_level=20,
        iteration_sleep_seconds=0.0,
        claude_binary=str(fake_claude),
        claude_permission_mode="bypassPermissions",
        anthropic_api_key="fake-key",
        git_host="github",
        gh_owner="",
        ado_org_url="",
        ado_project="",
        halt_webhook="",
        pr_check_poll_max_attempts=6,
        pr_check_poll_interval_seconds=30.0,
        instance_id=instance_id,
        use_worktrees=True,
        bot_author_email="",
        stale_days=3,
        bash_max_timeout_ms=900_000,
        workspace_root=workspace_root,
        claude_session_timeout_seconds=1200,
        same_file_min_prs=10,
        same_file_window_hours=24.0,
    )


def _ensure_instance(
    *,
    bare: Path,
    workspace_root: Path,
    instance_id: str,
    fake_claude: Path,
) -> ExecutorConfig:
    """Clone the bare into ``<workspace_root>/queue-<instance_id>/`` + build cfg.

    Mirrors what production ``_pull_queue`` would do on first iteration,
    so subsequent ``iterate_once`` calls only fetch+pull rather than
    cloning fresh.
    """
    workspace_root.mkdir(parents=True, exist_ok=True)
    clone = workspace_root / f"queue-{instance_id}"
    subprocess.run(
        ["git", "clone", str(bare), str(clone)],
        check=True,
        capture_output=True,
    )
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "Test User")
    return _build_cfg(
        bare=bare,
        workspace_root=workspace_root,
        instance_id=instance_id,
        fake_claude=fake_claude,
    )


def _seed_inbox_pbi(
    seed: Path,
    pbi_id: str,
    *,
    created_at: str = "2026-05-24T09:00:00+00:00",
    push: bool = True,
) -> None:
    pbi_dir = seed / ".ralph" / "inbox" / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=True)
    (pbi_dir / "PBI.md").write_text(
        f"---\nid: {pbi_id}\ntype: feature\nstatus: inbox\n"
        f"severity: normal\nattempts: 0\n"
        f"created_at: {created_at}\n"
        f"updated_at: {created_at}\n"
        f"target_repo: https://github.com/test/repo\n---\n\n# {pbi_id}\n",
        encoding="utf-8",
    )
    (pbi_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _git(seed, "add", f".ralph/inbox/{pbi_id}")
    _git(seed, "commit", "-m", f"chore(queue): add {pbi_id}")
    if push:
        _git(seed, "push", "origin", "main")


def _seed_pending_pr_pbi(
    seed: Path,
    pbi_id: str,
    *,
    owner_instance_id: str,
    push: bool = True,
) -> None:
    """Seed a PBI in ``pending-pr/<id>/`` with a CLAIM owned by ``owner``."""
    pbi_dir = seed / ".ralph" / "pending-pr" / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=True)
    (pbi_dir / "PBI.md").write_text(
        f"---\nid: {pbi_id}\ntype: feature\nstatus: pending-pr\n"
        f"severity: normal\nattempts: 0\n"
        f"created_at: 2026-05-24T09:00:00+00:00\n"
        f"updated_at: 2026-05-24T09:00:00+00:00\n"
        f"target_repo: https://github.com/test/repo\n---\n\n# {pbi_id}\n",
        encoding="utf-8",
    )
    (pbi_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    write_claim(
        pbi_dir / "CLAIM.json",
        Claim(
            instance_id=owner_instance_id,
            claimed_at="2026-05-24T09:00:00+00:00",
            hostname=f"{owner_instance_id}-host",
        ),
    )
    _git(seed, "add", f".ralph/pending-pr/{pbi_id}")
    _git(seed, "commit", "-m", f"chore(queue): seed {pbi_id} in pending-pr")
    if push:
        _git(seed, "push", "origin", "main")


def _stub_target_clone(
    monkeypatch: pytest.MonkeyPatch,
    clone_root: Path,
) -> None:
    """Replace ``target_clone.ensure_clone`` so it returns ``clone_root``.

    The claim path's ``ensure_worktree`` will then materialise a
    ``.ralph-work/<pbi-id>/`` worktree inside the queue clone — covered
    by the fixture ``.gitignore`` so it does not contaminate commits.
    """
    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    def _fake(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        return TargetClone(info=info, clone_root=clone_root)

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _fake)


def _stub_spawn(
    monkeypatch: pytest.MonkeyPatch,
    outcome: OutcomeKind = "partial",
) -> None:
    def _fake(
        cfg: ExecutorConfig,  # noqa: ARG001
        pbi: object,  # noqa: ARG001
        *,
        cwd: Path | None = None,  # noqa: ARG001
        pbi_dir: Path | None = None,  # noqa: ARG001
    ) -> ClaudeOutcome:
        return ClaudeOutcome(
            kind=outcome,
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )

    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _fake)


def _read_origin_claim(bare: Path, state: str, pbi_id: str) -> Claim:
    raw = subprocess.run(
        ["git", "-C", str(bare), "show", f"main:.ralph/{state}/{pbi_id}/CLAIM.json"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    data = json.loads(raw)
    return Claim(
        instance_id=data["instance_id"],
        claimed_at=data["claimed_at"],
        hostname=data["hostname"],
    )


def _tip_subject(bare: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(bare), "log", "-1", "--format=%s", "main"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


def _tip_sha(bare: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(bare), "rev-parse", "main"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


# ---------------------------------------------------------------------------
# T1: two distinct PBIs, two distinct instances. Both CLAIM.json correct.
# ---------------------------------------------------------------------------


def test_t1_two_instances_claim_two_distinct_pbis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each instance claims its own PBI; both CLAIM.json files land on origin.

    Pinning the commit subject per [[d62c9e9e]] proves each claim was
    actually committed + pushed — tip-advance alone could be satisfied
    by an empty commit on either iteration.
    """
    bare, seed = _init_bare(tmp_path)
    fake_claude = _make_fake_claude(tmp_path)
    monkeypatch.setenv("PATH", f"{fake_claude.parent}{os.pathsep}{os.environ.get('PATH', '')}")

    _seed_inbox_pbi(seed, "WI-ALPHA")
    _seed_inbox_pbi(seed, "WI-BETA", created_at="2026-05-24T09:01:00+00:00")

    cfg_a = _ensure_instance(
        bare=bare,
        workspace_root=tmp_path / "ws-a",
        instance_id="ralph-a",
        fake_claude=fake_claude,
    )
    cfg_b = _ensure_instance(
        bare=bare,
        workspace_root=tmp_path / "ws-b",
        instance_id="ralph-b",
        fake_claude=fake_claude,
    )

    _stub_spawn(monkeypatch)

    # ralph-a iterates → claims WI-ALPHA (earlier created_at wins lane tie).
    _stub_target_clone(monkeypatch, cfg_a.queue_clone_path)
    r_a = iterate_once(cfg_a)
    assert r_a.outcome == "claimed"
    assert r_a.pbi_id == "WI-ALPHA"
    assert _tip_subject(bare) == "chore(queue): claim WI-ALPHA for ralph-a"

    # ralph-b iterates → _pull_queue pulls ralph-a's commit; current/WI-ALPHA
    # has foreign CLAIM, current_pbi() returns None, pick_next returns
    # WI-BETA (still in inbox), claim path runs.
    _stub_target_clone(monkeypatch, cfg_b.queue_clone_path)
    r_b = iterate_once(cfg_b)
    assert r_b.outcome == "claimed"
    assert r_b.pbi_id == "WI-BETA"
    assert _tip_subject(bare) == "chore(queue): claim WI-BETA for ralph-b"

    # Both CLAIM.json files visible on origin with correct ownership.
    claim_a = _read_origin_claim(bare, "current", "WI-ALPHA")
    claim_b = _read_origin_claim(bare, "current", "WI-BETA")
    assert claim_a.instance_id == "ralph-a"
    assert claim_b.instance_id == "ralph-b"


# ---------------------------------------------------------------------------
# T2: race over one inbox PBI; loser observes PushRebaseConflict + rolls back.
# ---------------------------------------------------------------------------


def test_t2_race_loser_observes_push_conflict_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One inbox PBI, two instances racing. Loser sees PushRebaseConflict.

    Drives ``move_inbox_to_current`` directly on each clone instead of
    going through ``iterate_once`` — that way both clones see WI-RACE
    in inbox at claim time (no in-between ``_pull_queue`` to serialise
    them). After the race ralph-b's clone is reset to origin per the
    Task 9 rollback contract, so ralph-b's ``current_pbi()`` returns
    None (foreign claim) and ralph-a's returns the winner.
    """
    bare, seed = _init_bare(tmp_path)
    fake_claude = _make_fake_claude(tmp_path)
    monkeypatch.setenv("PATH", f"{fake_claude.parent}{os.pathsep}{os.environ.get('PATH', '')}")

    _seed_inbox_pbi(seed, "WI-RACE")

    cfg_a = _ensure_instance(
        bare=bare,
        workspace_root=tmp_path / "ws-a",
        instance_id="ralph-a",
        fake_claude=fake_claude,
    )
    cfg_b = _ensure_instance(
        bare=bare,
        workspace_root=tmp_path / "ws-b",
        instance_id="ralph-b",
        fake_claude=fake_claude,
    )

    src_a = FilesystemQueueSource(cfg_a)
    src_b = FilesystemQueueSource(cfg_b)
    pbi_a = src_a.pick_next()
    pbi_b = src_b.pick_next()
    assert pbi_a is not None and pbi_a.id == "WI-RACE"
    assert pbi_b is not None and pbi_b.id == "WI-RACE"

    # ralph-a wins the race.
    moved_a = move_inbox_to_current(
        cfg_a,
        pbi_a,
        instance_id="ralph-a",
        hostname="host-a",
    )
    assert moved_a.id == "WI-RACE"
    assert _tip_subject(bare) == "chore(queue): claim WI-RACE for ralph-a"

    # ralph-b loses. push_with_rebase fetches origin, sees the conflict
    # on current/WI-RACE/CLAIM.json + the inbox→current rename, raises
    # PushRebaseConflict. move_inbox_to_current's catch resets ralph-b's
    # local clone to origin/main per Task 9.
    with pytest.raises(PushRebaseConflict):
        move_inbox_to_current(
            cfg_b,
            pbi_b,
            instance_id="ralph-b",
            hostname="host-b",
        )

    # Origin tip is unchanged — ralph-b's commit never landed.
    assert _tip_subject(bare) == "chore(queue): claim WI-RACE for ralph-a"

    # ralph-a's view: own claim in current/.
    own_a = src_a.current_pbi()
    assert own_a is not None and own_a.id == "WI-RACE"

    # ralph-b's view: foreign claim in current/, no own PBI, inbox empty.
    own_b = src_b.current_pbi()
    assert own_b is None
    assert src_b.pick_next() is None

    # ralph-b's local CLAIM.json (post reset_hard) carries ralph-a's id.
    claim_b_local = read_claim(
        cfg_b.queue_clone_path / ".ralph" / "current" / "WI-RACE" / "CLAIM.json"
    )
    assert claim_b_local.instance_id == "ralph-a"


# ---------------------------------------------------------------------------
# T3: ralph-b ignores ralph-a's foreign pending-pr PBI; no extra commits.
# ---------------------------------------------------------------------------


def test_t3_ralph_b_iteration_does_not_touch_foreign_pending_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ralph-b's iteration is a no-op when only ralph-a's pending-pr exists.

    Foreign CLAIM in current/ would be filtered out by current_pbi()
    (Task 10). Foreign pending-pr/ entries are not part of the claim
    path at all — and with ``bot_author_email=""`` the sweep is skipped
    too (warning + return). End result: ralph-b advances zero commits
    against ralph-a's PR.
    """
    bare, seed = _init_bare(tmp_path)
    fake_claude = _make_fake_claude(tmp_path)
    monkeypatch.setenv("PATH", f"{fake_claude.parent}{os.pathsep}{os.environ.get('PATH', '')}")

    _seed_pending_pr_pbi(seed, "WI-PENDING", owner_instance_id="ralph-a")

    cfg_b = _ensure_instance(
        bare=bare,
        workspace_root=tmp_path / "ws-b",
        instance_id="ralph-b",
        fake_claude=fake_claude,
    )
    _stub_target_clone(monkeypatch, cfg_b.queue_clone_path)
    _stub_spawn(monkeypatch)

    tip_before = _tip_sha(bare)
    result = iterate_once(cfg_b)

    assert result.outcome == "idle"
    assert _tip_sha(bare) == tip_before, (
        "ralph-b must not advance origin/main while only foreign work is queued"
    )

    # The foreign PBI's CLAIM.json on origin still names ralph-a.
    claim = _read_origin_claim(bare, "pending-pr", "WI-PENDING")
    assert claim.instance_id == "ralph-a"


# ---------------------------------------------------------------------------
# T4: ralph-a halt path carries tripped_by_instance=ralph-a in META-BUG.
# ---------------------------------------------------------------------------


def test_t4_halt_meta_bug_carries_tripped_by_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cycle detector trips → META-BUG.md frontmatter carries cfg.instance_id.

    Scope 1: the halt sentinel at ``.ralph/state/halted`` is gitignored
    and stays local to ralph-a's clone — it does not propagate to
    ralph-b via the bare. This test pins what DOES propagate (the
    META-BUG file with ``tripped_by_instance: ralph-a`` frontmatter,
    written by ``halt_and_acknowledge``) and the local halt behaviour on
    ralph-a's next iteration.
    """
    bare, _ = _init_bare(tmp_path)
    fake_claude = _make_fake_claude(tmp_path)
    monkeypatch.setenv("PATH", f"{fake_claude.parent}{os.pathsep}{os.environ.get('PATH', '')}")

    cfg_a = _ensure_instance(
        bare=bare,
        workspace_root=tmp_path / "ws-a",
        instance_id="ralph-a",
        fake_claude=fake_claude,
    )
    _stub_target_clone(monkeypatch, cfg_a.queue_clone_path)
    _stub_spawn(monkeypatch)

    # Seed two events 1h apart so signature_recurrence trips on the next
    # iteration's check (mirrors tests/safety/test_integration_loop.py).
    log = open_log(cfg_a.queue_clone_path)
    try:
        now = datetime.now(tz=UTC).replace(microsecond=0)
        log.append(
            make_event(
                kind=EventType.PBI_CLOSED,
                recorded_at=now - timedelta(hours=23),
                pbi_id="BUG-old",
                payload={"signature": "ZeroDivisionError @ calc.py:10"},
            )
        )
        log.append(
            make_event(
                kind=EventType.SIGNATURE_OBSERVED,
                recorded_at=now - timedelta(hours=1),
                pbi_id="BUG-new",
                payload={"signature": "ZeroDivisionError @ calc.py:10"},
            )
        )
    finally:
        log.close()

    # iterate_once: empty current → empty inbox → cycle detector fires
    # → halt_and_acknowledge writes META-BUG + sentinel → HaltedError.
    with pytest.raises(HaltedError):
        iterate_once(cfg_a)

    # META-BUG.md lands in ralph-a's local clone under .ralph/blocked/.
    blocked = cfg_a.queue_clone_path / ".ralph" / "blocked"
    meta_bugs = list(blocked.glob("META-cycle-*.md"))
    assert meta_bugs, "halt_and_acknowledge must write a META-cycle-* file"
    meta_text = meta_bugs[0].read_text(encoding="utf-8")
    # Frontmatter-scoped assertion: tripped_by_instance lives inside the
    # leading YAML block (between the first pair of ``---`` fences).
    frontmatter = meta_text.split("---\n", 2)[1]
    assert "tripped_by_instance: ralph-a" in frontmatter

    # Sentinel halts ralph-a's clone locally.
    assert check_halt_sentinel(cfg_a.queue_clone_path) == HaltStatus.HALTED
    with pytest.raises(HaltedError):
        iterate_once(cfg_a)

    # Sentinel is gitignored → never reaches the bare → a fresh ralph-b
    # clone of the same bare sees no local sentinel and is NOT halted by
    # ralph-a's halt. Cross-host coordination is Scope 2.
    cfg_b = _ensure_instance(
        bare=bare,
        workspace_root=tmp_path / "ws-b",
        instance_id="ralph-b",
        fake_claude=fake_claude,
    )
    assert check_halt_sentinel(cfg_b.queue_clone_path) == HaltStatus.RUNNING


# ---------------------------------------------------------------------------
# T5: lockfile contention — second acquire surfaces the first's payload.
# ---------------------------------------------------------------------------


def test_t5_lockfile_second_acquire_carries_first_payload(tmp_path: Path) -> None:
    """A second WorkspaceLockfile.acquire on the same path raises LockfileError.

    Cross-platform behaviour split: POSIX ``fcntl.flock`` is advisory and
    allows in-process reads of the locked file, so the LockfileError's
    message embeds the first holder's JSON payload (``ralph-a`` /
    ``host-a``). Windows ``msvcrt.locking`` is a byte-range exclusive
    lock that blocks even same-process reads — the surfaced payload is
    ``<no payload>`` and only the headline "another ralph already
    running" survives. The cross-process variant covered by
    ``tests/executor/test_lockfile.py::test_cross_process_contention_posix``
    is the load-bearing real-world test on POSIX.
    """
    lock_path = tmp_path / ".ralph.lock"
    first = WorkspaceLockfile(lock_path, instance_id="ralph-a", hostname="host-a")
    first.acquire()
    try:
        second = WorkspaceLockfile(lock_path, instance_id="ralph-b", hostname="host-b")
        with pytest.raises(LockfileError) as exc_info:
            second.acquire()
        msg = str(exc_info.value)
        assert "another ralph already running" in msg
        if sys.platform != "win32":
            # POSIX: payload is readable; identity surfaces in the error.
            assert "ralph-a" in msg
            assert "host-a" in msg
    finally:
        first.release()
