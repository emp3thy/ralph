"""Two-instance integration tests for the multi-ralph design.

Builds a bare queue origin and two namespaced clones at
``queue-ralph-a/`` / ``queue-ralph-b/`` under distinct workspace roots,
then drives ``iterate_once`` on each ``ExecutorConfig`` to confirm:

* T1 — two ralphs claim distinct inbox PBIs from one shared queue
* T2 — claim race: the loser sees the winner's CLAIM after pull and
  ends up with no own claim of its own
* T4 — a halt sentinel on one instance's queue clone halts THAT
  instance only; the other keeps running (per-instance halt isolation)
* T5 — the workspace lockfile blocks a second acquire (unit-style)
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from ralph_executor.claim import read_claim
from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import iterate_once
from ralph_executor.queue_path import queue_clone_path
from ralph_executor.safety.halt import HaltedError
from tests.executor.conftest import PROMPT_ROOT, write_sample_pbi


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


def _seed_origin(tmp_path: Path, *, inbox_pbis: list[str]) -> Path:
    """Init a bare origin on ``main`` with .ralph skeleton + given inbox PBIs.

    Returns the bare repo path. The seed clone is discarded after the
    initial push so subsequent two-instance state stays in the
    per-instance clones only.
    """
    bare = tmp_path / "queue.git"
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(seed)],
        check=True,
        capture_output=True,
    )
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "Test User")
    (seed / ".gitignore").write_text(".ralph/state/\n.ralph-work/\n", encoding="utf-8")
    _git(seed, "add", ".gitignore")
    _git(seed, "commit", "-m", "chore: gitignore")
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (seed / ".ralph" / sub).mkdir(parents=True)
        (seed / ".ralph" / sub / ".gitkeep").write_text("", encoding="utf-8")
    shutil.copytree(PROMPT_ROOT, seed / "prompt")
    _git(seed, "add", ".ralph", "prompt")
    _git(seed, "commit", "-m", "chore(queue): bootstrap")
    for pbi_id in inbox_pbis:
        write_sample_pbi(seed, pbi_id=pbi_id, where="inbox")
        _git(seed, "add", f".ralph/inbox/{pbi_id}")
        _git(seed, "commit", "-m", f"inbox: {pbi_id}")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-u", "origin", "main")
    return bare


def _clone_namespaced(bare: Path, *, ws_root: Path, instance_id: str) -> Path:
    """Clone the bare into ``<ws_root>/queue-<instance_id>``."""
    dest = queue_clone_path(ws_root, instance_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", str(bare), str(dest)],
        check=True,
        capture_output=True,
    )
    _git(dest, "config", "user.email", "test@example.com")
    _git(dest, "config", "user.name", "Test User")
    return dest


def _make_cfg(
    *,
    clone: Path,
    bare: Path,
    ws_root: Path,
    instance_id: str,
    claude_binary: Path,
) -> ExecutorConfig:
    return ExecutorConfig(
        repo_path=clone,
        queue_repo=f"file://{bare.as_posix()}",
        queue_branch="main",
        main_branch="main",
        max_attempts=3,
        log_level=20,
        iteration_sleep_seconds=0.0,
        claude_binary=str(claude_binary),
        claude_permission_mode="bypassPermissions",
        anthropic_api_key="fake-key",
        git_host="github",
        gh_owner="",
        ado_org_url="",
        ado_project="",
        halt_webhook="",
        pr_check_poll_max_attempts=6,
        pr_check_poll_interval_seconds=30.0,
        use_worktrees=True,
        bot_author_email="",
        stale_days=3,
        bash_max_timeout_ms=900_000,
        workspace_root=ws_root,
        claude_session_timeout_seconds=1200,
        same_file_min_prs=10,
        same_file_window_hours=24.0,
        instance_id=instance_id,
    )


@pytest.fixture
def two_ralphs(
    tmp_path: Path,
    fake_claude_binary: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[ExecutorConfig, ExecutorConfig, Path]]:
    """Two ExecutorConfigs (ralph-a, ralph-b) sharing one bare queue origin.

    Seeds the origin with two inbox PBIs (WI-1, WI-2) and two namespaced
    clones at ``<tmp>/wsa/queue-ralph-a`` and ``<tmp>/wsb/queue-ralph-b``.
    Overrides the conftest ``target_clone.ensure_clone`` monkeypatch with
    a per-workspace minimal clone factory (the conftest stub lazily
    requests ``fake_repo`` whose tmp_path layout collides with this
    fixture's).
    """
    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    def _ensure_target_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        clone_root = workspace_root / "clones" / info.owner / info.name
        if not (clone_root / ".git").is_dir():
            clone_root.mkdir(parents=True, exist_ok=True)
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

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _ensure_target_clone)

    bare = _seed_origin(tmp_path, inbox_pbis=["WI-1", "WI-2"])
    ws_a = tmp_path / "wsa"
    ws_b = tmp_path / "wsb"
    clone_a = _clone_namespaced(bare, ws_root=ws_a, instance_id="ralph-a")
    clone_b = _clone_namespaced(bare, ws_root=ws_b, instance_id="ralph-b")
    cfg_a = _make_cfg(
        clone=clone_a,
        bare=bare,
        ws_root=ws_a,
        instance_id="ralph-a",
        claude_binary=fake_claude_binary,
    )
    cfg_b = _make_cfg(
        clone=clone_b,
        bare=bare,
        ws_root=ws_b,
        instance_id="ralph-b",
        claude_binary=fake_claude_binary,
    )
    yield cfg_a, cfg_b, bare


# ----------------------------------------------------------------------
# T1 — two ralphs claim distinct PBIs
# ----------------------------------------------------------------------


def test_T1_two_ralphs_claim_distinct_pbis(
    two_ralphs: tuple[ExecutorConfig, ExecutorConfig, Path],
) -> None:
    """ralph-a + ralph-b draining one origin land on distinct PBIs.

    Each ``iterate_once`` pulls, picks the lowest-id inbox PBI not yet
    in current/, claims it (post_mv writes CLAIM.json owned by THIS
    instance), and pushes. ralph-b's pull picks up ralph-a's claim
    commit, so its current_pbi() filter skips WI-1 and pick_next falls
    through to WI-2.
    """
    cfg_a, cfg_b, _ = two_ralphs
    result_a = iterate_once(cfg_a)
    result_b = iterate_once(cfg_b)
    assert result_a.outcome == "claimed"
    assert result_b.outcome == "claimed"

    qa = queue_clone_path(cfg_a.workspace_root, cfg_a.instance_id) / ".ralph" / "current"
    qb = queue_clone_path(cfg_b.workspace_root, cfg_b.instance_id) / ".ralph" / "current"
    own_a = {p.name: read_claim(p) for p in qa.iterdir() if p.is_dir()}
    own_b = {p.name: read_claim(p) for p in qb.iterdir() if p.is_dir()}

    a_ids = {pid for pid, c in own_a.items() if c and c.instance_id == "ralph-a"}
    b_ids = {pid for pid, c in own_b.items() if c and c.instance_id == "ralph-b"}
    assert a_ids == {result_a.pbi_id}
    assert b_ids == {result_b.pbi_id}
    assert a_ids.isdisjoint(b_ids), (
        f"ralph-a and ralph-b must claim disjoint PBIs (a={a_ids}, b={b_ids})"
    )


# ----------------------------------------------------------------------
# T2 — claim race: only one PBI in inbox; one ralph wins, the other idles
# ----------------------------------------------------------------------


def _strip_inbox_to_one(bare: Path, tmp_path: Path, *, keep: str) -> None:
    """Mutate the bare origin so only ``keep`` remains under inbox/."""
    scratch = tmp_path / "strip"
    subprocess.run(
        ["git", "clone", str(bare), str(scratch)],
        check=True,
        capture_output=True,
    )
    _git(scratch, "config", "user.email", "test@example.com")
    _git(scratch, "config", "user.name", "Test User")
    inbox = scratch / ".ralph" / "inbox"
    for entry in inbox.iterdir():
        if entry.is_dir() and entry.name != keep:
            shutil.rmtree(entry)
            _git(scratch, "rm", "-r", f".ralph/inbox/{entry.name}")
    _git(scratch, "commit", "-m", f"chore(queue): strip inbox to {keep}")
    _git(scratch, "push", "origin", "main")


def test_T2_claim_race_loser_idles(
    two_ralphs: tuple[ExecutorConfig, ExecutorConfig, Path],
    tmp_path: Path,
) -> None:
    """When one PBI is in inbox, the first claimer wins; loser idles.

    ralph-a iterates first, claims WI-1 and pushes the move +
    CLAIM.json. ralph-b's iterate pulls that commit, current_pbi
    filters out WI-1 (foreign claim), pick_next finds the inbox empty
    → outcome "idle". After pull, ralph-b's local clone reflects the
    winner's claim at current/WI-1/CLAIM.json.
    """
    cfg_a, cfg_b, bare = two_ralphs
    _strip_inbox_to_one(bare, tmp_path, keep="WI-1")
    result_a = iterate_once(cfg_a)
    result_b = iterate_once(cfg_b)
    assert result_a.outcome == "claimed"
    assert result_a.pbi_id == "WI-1"
    assert result_b.outcome == "idle"
    assert result_b.pbi_id is None

    qb = queue_clone_path(cfg_b.workspace_root, cfg_b.instance_id) / ".ralph" / "current"
    own_b_dirs = [
        p
        for p in qb.iterdir()
        if p.is_dir() and read_claim(p) is not None and read_claim(p).instance_id == "ralph-b"  # type: ignore[union-attr]
    ]
    assert own_b_dirs == [], "loser must hold no own claim"

    foreign = qb / "WI-1"
    assert foreign.is_dir(), "loser's pull must surface the winner's PBI dir"
    foreign_claim = read_claim(foreign)
    assert foreign_claim is not None
    assert foreign_claim.instance_id == "ralph-a"


# ----------------------------------------------------------------------
# T4 — halt sentinel on one instance halts THAT instance only
# ----------------------------------------------------------------------


def test_T4_halt_sentinel_is_per_instance(
    two_ralphs: tuple[ExecutorConfig, ExecutorConfig, Path],
) -> None:
    """A halt sentinel in ralph-b's clone halts ralph-b only; ralph-a runs.

    ``.ralph/state/halted`` is gitignored — it never propagates via the
    queue branch. This locks the per-instance halt design: writing the
    sentinel locally to ralph-b's queue clone causes ralph-b's
    iterate_once to raise HaltedError, while ralph-a (no sentinel)
    continues to drain.
    """
    cfg_a, cfg_b, _ = two_ralphs
    sentinel = (
        queue_clone_path(cfg_b.workspace_root, cfg_b.instance_id) / ".ralph" / "state" / "halted"
    )
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(
        "meta_bug_id: META-test\n"
        "halted_at: 2026-05-31T12:00:00+00:00\n"
        "acknowledged_by:\n"
        "acknowledged_at:\n",
        encoding="utf-8",
    )
    with pytest.raises(HaltedError):
        iterate_once(cfg_b)
    result_a = iterate_once(cfg_a)
    assert result_a.outcome == "claimed", (
        f"ralph-a must not be affected by ralph-b's halt; got {result_a.outcome!r}"
    )


# ----------------------------------------------------------------------
# T5 — workspace lockfile blocks second acquire (kept here per plan)
# ----------------------------------------------------------------------


def test_T5_lockfile_blocks_second_process(tmp_path: Path) -> None:
    """Two WorkspaceLock instances on the same path cannot both hold it."""
    from ralph_executor.lockfile import LockHeldError, WorkspaceLock

    path = tmp_path / "queue-ralph-a" / ".ralph.lock"
    first = WorkspaceLock(path, instance_id="ralph-a")
    second = WorkspaceLock(path, instance_id="ralph-a")
    first.acquire()
    try:
        with pytest.raises(LockHeldError):
            second.acquire()
    finally:
        first.release()
