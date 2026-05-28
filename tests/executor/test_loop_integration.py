"""Integration tests for the loop ↔ sweep wiring (Plan 8 Task 7).

The unit tests in ``tests/executor/sweep/`` exercise ``run_sweep`` in
isolation. These tests confirm that ``iterate_once`` actually invokes
the sweep when ``current/`` is empty and skips it when occupied — and
that the configuration plumbing (``RALPH_ADO_AUTHOR_EMAIL``, the
PR-skill scripts directory) is honoured.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import ClaudeOutcome
from ralph_executor.config import ExecutorConfig
from ralph_executor.loop import iterate_once
from ralph_executor.sweep.runner import SweepContext, SweepResult
from tests.executor.conftest import write_sample_pbi


def _stub_spawn(outcome_kind: str = "partial") -> object:
    def _fake_spawn(
        cfg: ExecutorConfig,
        pbi: object,
        *,
        cwd: Path | None = None,
        pbi_dir: Path | None = None,
    ) -> ClaudeOutcome:
        return ClaudeOutcome(
            kind=outcome_kind,  # type: ignore[arg-type]
            pr_url=None,
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )

    return _fake_spawn


def _cfg_with_sweep_knobs(
    cfg: ExecutorConfig,
    *,
    bot_author_email: str = "ralph-bot@example.com",
    stale_days: int = 3,
) -> ExecutorConfig:
    """Derive a cfg with the promoted sweep knobs populated.

    The loop reads ``cfg.bot_author_email`` / ``cfg.stale_days`` directly
    (post-CONFIG-PROMOTE-SWEEP-KNOBS); env vars no longer feed into
    ``_run_sweep``.
    """
    return replace(cfg, bot_author_email=bot_author_email, stale_days=stale_days)


def _populate_inbox_via_git(fake_repo: Path, pbi_id: str = "WI-INTEG") -> None:
    """Stage a sample PBI on the queue clone's ``main`` and push to origin."""

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(fake_repo),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    write_sample_pbi(fake_repo, pbi_id=pbi_id)
    _git("add", f".ralph/inbox/{pbi_id}")
    _git("commit", "-m", f"inbox: {pbi_id}")
    _git("push", "origin", "main")


def test_sweep_runs_when_current_is_empty(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``iterate_once`` should invoke the real sweep when current/ is empty."""
    cfg = _cfg_with_sweep_knobs(cfg_for_repo)
    # Stage a PR-skill scripts directory so _run_sweep's guard accepts the path.
    (fake_repo / "skills" / "pr-github" / "scripts").mkdir(parents=True)

    captured: list[SweepContext] = []

    def _spy_run(*, ctx: SweepContext) -> SweepResult:
        captured.append(ctx)
        return SweepResult(pbis_scanned=0)

    # The loop driver imports `run` lazily from `ralph_executor.sweep` inside
    # `_run_sweep`. Patch the resolved name on the runner module so the lazy
    # `from ralph_executor.sweep import run` picks up the spy.
    monkeypatch.setattr("ralph_executor.sweep.run", _spy_run)
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn())

    iterate_once(cfg)

    assert len(captured) == 1, "sweep must run exactly once when current/ is empty"
    ctx = captured[0]
    assert ctx.queue_root == fake_repo / ".ralph"
    assert ctx.ado_pr_scripts_path == fake_repo / "skills" / "pr-github" / "scripts"
    assert ctx.config.ralph_author_email == "ralph-bot@example.com"


def test_sweep_does_not_run_when_current_has_pbi(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``current/`` is occupied the loop spawns Claude, not the sweep."""
    cfg = _cfg_with_sweep_knobs(cfg_for_repo)
    (fake_repo / "skills" / "pr-github" / "scripts").mkdir(parents=True)
    _populate_inbox_via_git(fake_repo)
    # First iteration claims the PBI into current/.
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn())
    iterate_once(cfg)
    assert (fake_repo / ".ralph" / "current" / "WI-INTEG").is_dir()

    captured: list[SweepContext] = []

    def _spy_run(*, ctx: SweepContext) -> SweepResult:
        captured.append(ctx)
        return SweepResult(pbis_scanned=0)

    monkeypatch.setattr("ralph_executor.sweep.run", _spy_run)
    iterate_once(cfg)
    assert captured == [], "sweep must NOT run while current/ is occupied"


def test_sweep_skipped_when_author_email_missing(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``cfg.bot_author_email`` is empty the sweep is skipped with a warning."""
    cfg = _cfg_with_sweep_knobs(cfg_for_repo, bot_author_email="")
    (fake_repo / "skills" / "pr-github" / "scripts").mkdir(parents=True)

    captured: list[SweepContext] = []

    def _spy_run(*, ctx: SweepContext) -> SweepResult:
        captured.append(ctx)
        return SweepResult(pbis_scanned=0)

    monkeypatch.setattr("ralph_executor.sweep.run", _spy_run)
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn())

    iterate_once(cfg)
    assert captured == [], "sweep must skip when cfg.bot_author_email is empty"


def test_sweep_skipped_when_pr_skill_scripts_dir_missing(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the PR-skill scripts directory on disk, the sweep is skipped."""
    cfg = _cfg_with_sweep_knobs(cfg_for_repo)
    # Deliberately do NOT create skills/pr-github/scripts/ or skills/ado-pr/scripts/.

    captured: list[SweepContext] = []

    def _spy_run(*, ctx: SweepContext) -> SweepResult:
        captured.append(ctx)
        return SweepResult(pbis_scanned=0)

    monkeypatch.setattr("ralph_executor.sweep.run", _spy_run)
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn())

    iterate_once(cfg)
    assert captured == [], "sweep must skip when the PR-skill scripts directory is missing"


# ---------------------------------------------------------------------------
# Task 11: queue_branch configurable — assert pushes target ralph-queue.
# ---------------------------------------------------------------------------


def _git_capture(cwd: Path, *args: str) -> str:
    """Run ``git`` in ``cwd`` and return stdout (raises on non-zero)."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def _build_dual_branch_queue(tmp_path: Path) -> tuple[Path, Path]:
    """Build a bare remote with ``main`` AND ``ralph-queue`` plus a queue clone.

    The bare repo has:
      * ``main`` — a single root commit (no ``.ralph/``).
      * ``ralph-queue`` — the ``.ralph/`` skeleton seeded on top of main.

    The local clone is checked out on ``ralph-queue`` (the queue branch
    the loop pulls / pushes against).

    Returns ``(bare_path, clone_path)``.
    """
    bare = tmp_path / "queue.git"
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    clone = workspace / "queue"
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
    _git_capture(seed, "config", "user.email", "test@example.com")
    _git_capture(seed, "config", "user.name", "Test User")
    _git_capture(seed, "commit", "--allow-empty", "-m", "chore: initial main commit")
    _git_capture(seed, "remote", "add", "origin", str(bare))
    # Push main BEFORE seeding ralph-queue so main lands as a single
    # root commit and stays that way.
    _git_capture(seed, "push", "-u", "origin", "main")

    # Now create ralph-queue branched off main and seed the skeleton.
    _git_capture(seed, "checkout", "-b", "ralph-queue")
    (seed / ".gitignore").write_text(".ralph/state/\n", encoding="utf-8")
    _git_capture(seed, "add", ".gitignore")
    _git_capture(seed, "commit", "-m", "chore: gitignore .ralph/state/")
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (seed / ".ralph" / sub).mkdir(parents=True)
        (seed / ".ralph" / sub / ".gitkeep").write_text("", encoding="utf-8")
    _git_capture(seed, "add", ".ralph")
    _git_capture(seed, "commit", "-m", "chore(queue): bootstrap .ralph/ tree")
    _git_capture(seed, "push", "-u", "origin", "ralph-queue")

    # Clone the bare on the ralph-queue branch — mirrors what
    # ensure_queue_clone does for production deployments.
    subprocess.run(
        ["git", "clone", "-b", "ralph-queue", str(bare), str(clone)],
        check=True,
        capture_output=True,
    )
    _git_capture(clone, "config", "user.email", "test@example.com")
    _git_capture(clone, "config", "user.name", "Test User")
    return bare, clone


def test_loop_persists_to_ralph_queue_branch_by_default(
    tmp_path: Path,
    fake_claude_binary: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """A full iteration pushes to ``ralph-queue`` when queue_branch is the default.

    Builds a bare remote with both ``main`` and ``ralph-queue`` branches,
    seeds an inbox PBI on ``ralph-queue``, runs one iteration of the loop
    with ``cfg.queue_branch="ralph-queue"``, and asserts:
      * upstream ``ralph-queue`` tip ADVANCED (claim push landed),
      * upstream ``main`` tip is UNCHANGED.
    """
    bare, clone = _build_dual_branch_queue(tmp_path)

    # Override the autouse ``_fake_ensure_target_clone`` so it routes to
    # our purpose-built queue clone rather than the global ``fake_repo``
    # fixture (which we deliberately did NOT request).
    from ralph_executor.target_clone import TargetClone
    from ralph_executor.url_utils import TargetRepoInfo

    def _fake_ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
        return TargetClone(info=info, clone_root=clone)

    monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _fake_ensure_clone)

    # Seed an inbox PBI on ralph-queue and push it to the bare.
    pbi_id = "WI-QB-1"
    write_sample_pbi(clone, pbi_id=pbi_id)
    _git_capture(clone, "add", f".ralph/inbox/{pbi_id}")
    _git_capture(clone, "commit", "-m", f"inbox: {pbi_id}")
    _git_capture(clone, "push", "origin", "ralph-queue")

    # Snapshot upstream tips BEFORE the iteration.
    ralph_queue_before = _git_capture(bare, "rev-parse", "ralph-queue").strip()
    main_before = _git_capture(bare, "rev-parse", "main").strip()
    assert ralph_queue_before != main_before, "fixture must diverge ralph-queue from main"

    # Build cfg targeting the bare on ralph-queue.
    cfg = ExecutorConfig(
        repo_path=clone,
        queue_repo=f"file://{bare.as_posix()}",
        queue_branch="ralph-queue",
        main_branch="main",
        max_attempts=3,
        log_level=20,
        iteration_sleep_seconds=0.0,
        claude_binary=str(fake_claude_binary),
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
        workspace_root=tmp_path / "ws",
        claude_session_timeout_seconds=1200,
        same_file_min_prs=10,
        same_file_window_hours=24.0,
    )

    # Stub Claude (it isn't spawned on the claim iteration anyway, but
    # keep the path symmetrical with the other tests in this file).
    monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stub_spawn())

    # Drive one iteration: empty current → sweep skipped (no
    # bot_author_email) → pick inbox PBI → claim, which calls
    # ``move_inbox_to_current`` → ``push_with_rebase(branch=cfg.queue_branch)``.
    result = iterate_once(cfg)
    assert result.outcome == "claimed", f"expected claim, got {result.outcome!r}"

    # Assert upstream state: ralph-queue advanced, main unchanged.
    ralph_queue_after = _git_capture(bare, "rev-parse", "ralph-queue").strip()
    main_after = _git_capture(bare, "rev-parse", "main").strip()
    assert ralph_queue_after != ralph_queue_before, (
        "iteration must push the claim commit to upstream ralph-queue"
    )
    assert main_after == main_before, (
        f"main must NOT advance during the iteration (before={main_before}, "
        f"after={main_after})"
    )

    # Tip-advance alone could be satisfied by a bogus push (e.g. an empty
    # commit). Pin the new ralph-queue tip's commit subject to what
    # ``move_inbox_to_current`` produces — i.e. the inbox→current claim
    # commit for this PBI. See ``ralph_executor/queue/movements.py``:
    # ``_move`` formats ``{commit_prefix}: move {pbi.id} from {src} to {dst}``
    # and ``move_inbox_to_current`` sets ``commit_prefix="chore(ralph-queue)"``.
    subject = subprocess.run(
        ["git", "-C", str(bare), "log", "-1", "--format=%s", "ralph-queue"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    expected_subject = f"chore(ralph-queue): move {pbi_id} from inbox to current"
    assert subject == expected_subject, (
        f"ralph-queue tip subject must be the claim commit "
        f"(expected={expected_subject!r}, got={subject!r})"
    )
