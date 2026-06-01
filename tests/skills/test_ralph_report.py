"""Tests for the ``ralph-report`` skill.

The skill lives at ``skills/ralph-report/scripts/*.py``. Because the
directory name is kebab-case (not a valid Python identifier), each
script module is loaded via :func:`importlib.util.spec_from_file_location`
with a synthetic top-level name — the same pattern that
``tests/skills/test_ralph_status.py`` uses for ``ralph-status``.

Post BUG-RALPH-REPORT-UNIFY-DATA-SOURCE: snapshot / git_walker / server
all take ``queue_clone`` (the operator queue clone at
``<workspace_root>/queue/``) directly. ``report.main`` resolves the
operator clone via ``--workspace`` / ``--queue-repo`` /
``--queue-branch`` and writes ``server-info`` to
``<workspace_root>/report/``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "skills" / "ralph-report" / "scripts"


def _load(name: str, file_name: str) -> ModuleType:
    path = SCRIPTS_DIR / file_name
    assert path.is_file(), f"missing skill script at {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def snapshot() -> ModuleType:
    return _load("ralph_report_snapshot", "snapshot.py")


@pytest.fixture(scope="module")
def git_walker() -> ModuleType:
    return _load("ralph_report_git_walker", "git_walker.py")


@pytest.fixture(scope="module")
def render() -> ModuleType:
    return _load("ralph_report_render", "render.py")


def _write_pbi(
    state_dir: Path,
    pbi_id: str,
    *,
    pbi_type: str = "feature",
    severity: str = "normal",
    title: str = "test pbi",
    target_repo: str = "https://github.com/example/test",
) -> None:
    pbi_dir = state_dir / pbi_id
    pbi_dir.mkdir(parents=True)
    entry = pbi_dir / ("BUG.md" if pbi_type == "bug" else "PBI.md")
    entry.write_text(
        f"""---
id: {pbi_id}
type: {pbi_type}
status: {state_dir.name}
severity: {severity}
attempts: 0
created_at: 2026-05-28T00:00:00+00:00
updated_at: 2026-05-28T00:00:00+00:00
target_repo: {target_repo}
---

# {title}
""",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("<!-- history -->\n", encoding="utf-8")


def _build_queue_clone(root: Path) -> Path:
    """Create an empty queue-clone directory tree (no real git history)."""
    queue_clone = root / "queue"
    (queue_clone / ".ralph").mkdir(parents=True)
    for state in ("current", "inbox", "pending-pr", "blocked", "done"):
        (queue_clone / ".ralph" / state).mkdir()
    return queue_clone


def test_snapshot_load_reads_all_state_folders(tmp_path: Path, snapshot: ModuleType) -> None:
    queue_clone = _build_queue_clone(tmp_path)

    _write_pbi(queue_clone / ".ralph" / "current", "PBI-CURRENT")
    _write_pbi(queue_clone / ".ralph" / "inbox", "PBI-INBOX-1", severity="high")
    _write_pbi(queue_clone / ".ralph" / "inbox", "PBI-INBOX-2", severity="normal")
    _write_pbi(queue_clone / ".ralph" / "blocked", "BUG-BLOCKED", pbi_type="bug")

    snap = snapshot.load_snapshot(queue_clone=queue_clone)

    assert [r.pbi_id for r in snap.current] == ["PBI-CURRENT"]
    assert sorted(r.pbi_id for r in snap.inbox) == ["PBI-INBOX-1", "PBI-INBOX-2"]
    assert snap.pending_pr == []
    assert [r.pbi_id for r in snap.blocked] == ["BUG-BLOCKED"]
    assert snap.meta_cycle_sentinels == []


def test_snapshot_collects_meta_cycle_sentinels(tmp_path: Path, snapshot: ModuleType) -> None:
    queue_clone = tmp_path / "queue"
    blocked = queue_clone / ".ralph" / "blocked"
    blocked.mkdir(parents=True)
    (blocked / "META-cycle-20260527T221018Z.md").write_text("body\n", encoding="utf-8")
    (blocked / "README.md").write_text("ignored\n", encoding="utf-8")

    snap = snapshot.load_snapshot(queue_clone=queue_clone)

    assert [s.filename for s in snap.meta_cycle_sentinels] == ["META-cycle-20260527T221018Z.md"]


def test_snapshot_returns_empty_when_ralph_dir_missing(
    tmp_path: Path, snapshot: ModuleType
) -> None:
    queue_clone = tmp_path / "queue"
    queue_clone.mkdir()
    snap = snapshot.load_snapshot(queue_clone=queue_clone)
    assert snap.current == []
    assert snap.inbox == []
    assert snap.pending_pr == []
    assert snap.blocked == []
    assert snap.done == []
    assert snap.meta_cycle_sentinels == []


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout


@pytest.mark.parametrize(
    "subject,expected_kind,expected_id",
    [
        (
            "chore(queue): add EXECUTOR-EXIT-WHEN-IDLE-DEFAULT",
            "added",
            "EXECUTOR-EXIT-WHEN-IDLE-DEFAULT",
        ),
        (
            "chore(ralph-queue): move EXECUTOR-EXIT-WHEN-IDLE-DEFAULT from inbox to current",
            "claimed",
            "EXECUTOR-EXIT-WHEN-IDLE-DEFAULT",
        ),
        (
            "feat(ralph-queue): move RALPH-MULTI-REPO-CHECKOUT from current to pending-pr",
            "pr_opened",
            "RALPH-MULTI-REPO-CHECKOUT",
        ),
        (
            "chore(ralph-queue): move RALPH-MULTI-REPO-CHECKOUT from pending-pr to done",
            "shipped",
            "RALPH-MULTI-REPO-CHECKOUT",
        ),
        (
            "chore(ralph-queue): move STAGE-B-PLAN-13 from current to blocked",
            "blocked",
            "STAGE-B-PLAN-13",
        ),
    ],
)
def test_parse_commit_subject_known_patterns(
    subject: str, expected_kind: str, expected_id: str, git_walker: ModuleType
) -> None:
    event = git_walker.parse_commit_subject(subject)
    assert event is not None
    assert event.kind == expected_kind
    assert event.pbi_id == expected_id


@pytest.mark.parametrize(
    "subject",
    [
        "chore(queue): persist iteration writes for EXECUTOR-EXIT-WHEN-IDLE-DEFAULT",
        "docs(spec): drop ralph-status 'risk'; surface skill in README",
        "fix(cycle-detector): raise WHACK_A_MOLE_MIN_OPENS from 3 to 12",
        (
            "chore(queue): add target_repo + set status=inbox "
            "on BUG-CLAIM-RACE BUG.md (followup to ba7f862)"
        ),
        "chore(queue): archive STAGE-B-PLAN-13 + META-cycle artifacts",
    ],
)
def test_parse_commit_subject_filtered_or_unrelated(subject: str, git_walker: ModuleType) -> None:
    assert git_walker.parse_commit_subject(subject) is None


def test_walk_log_picks_up_scripted_commits(tmp_path: Path, git_walker: ModuleType) -> None:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True)
    _git(seed, "checkout", "-b", "ralph-queue")
    (seed / "x.txt").write_text("x", encoding="utf-8")
    _git(seed, "add", "x.txt")
    _git(
        seed,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-m",
        "chore(queue): add EXECUTOR-EXIT-WHEN-IDLE-DEFAULT",
    )
    (seed / "y.txt").write_text("y", encoding="utf-8")
    _git(seed, "add", "y.txt")
    _git(
        seed,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-m",
        "chore(ralph-queue): move EXECUTOR-EXIT-WHEN-IDLE-DEFAULT from inbox to current",
    )
    (seed / "z.txt").write_text("z", encoding="utf-8")
    _git(seed, "add", "z.txt")
    _git(
        seed,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-m",
        "chore(queue): persist iteration writes for EXECUTOR-EXIT-WHEN-IDLE-DEFAULT",
    )
    blocked_dir = seed / ".ralph" / "blocked"
    blocked_dir.mkdir(parents=True)
    (blocked_dir / "META-cycle-20260530T120000Z.md").write_text("trip\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(
        seed,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-m",
        "chore(queue): cycle-detector trip",
    )
    _git(seed, "push", "origin", "ralph-queue")
    consumer = tmp_path / "consumer"
    subprocess.run(["git", "clone", str(bare), str(consumer)], check=True)
    _git(consumer, "fetch", "origin", "ralph-queue")

    events = git_walker.walk_log(queue_clone=consumer, since="365.days.ago")
    kinds = [e.kind for e in events]
    assert "added" in kinds
    assert "claimed" in kinds
    assert "cycle_trip" in kinds
    # persist-iteration commits must NOT emit any event. Only 4 commits
    # were scripted (add / claim / persist / cycle-trip), and persist is
    # the only one that should be filtered out — so we expect exactly 3
    # events, none of them attributable to the persist commit.
    assert len(events) == 3
    assert set(kinds) == {"added", "claimed", "cycle_trip"}
    added_ids = [e.pbi_id for e in events if e.kind == "added"]
    assert added_ids == ["EXECUTOR-EXIT-WHEN-IDLE-DEFAULT"]


def test_walk_log_returns_empty_when_ref_absent(tmp_path: Path, git_walker: ModuleType) -> None:
    queue_clone = tmp_path / "queue"
    queue_clone.mkdir()
    subprocess.run(["git", "init", str(queue_clone)], check=True)
    assert git_walker.walk_log(queue_clone=queue_clone) == []


def test_render_page_contains_each_section_heading(
    tmp_path: Path, snapshot: ModuleType, render: ModuleType
) -> None:
    queue_clone = _build_queue_clone(tmp_path)
    _write_pbi(queue_clone / ".ralph" / "current", "PBI-CUR")
    _write_pbi(queue_clone / ".ralph" / "inbox", "PBI-IN")

    snap = snapshot.load_snapshot(queue_clone=queue_clone)
    html = render.render_page(
        queue_clone=queue_clone,
        snapshot=snap,
        events=[],
        now=datetime(2026, 5, 28, 10, 42, 13, tzinfo=UTC),
    )

    assert html.startswith("<!DOCTYPE html>")
    for marker in ("Current", "Blocked", "Inbox", "Pending PR", "Done", "Activity timeline"):
        assert marker in html, f"missing section heading: {marker}"
    assert "PBI-CUR" in html
    assert "PBI-IN" in html
    assert "F5" in html
    assert "cdn.jsdelivr.net/npm/bootstrap@5" in html


def test_render_page_renders_timeline_event_labels(
    tmp_path: Path,
    snapshot: ModuleType,
    git_walker: ModuleType,
    render: ModuleType,
) -> None:
    queue_clone = _build_queue_clone(tmp_path)

    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
    when = datetime(2026, 5, 28, 11, 30, 0, tzinfo=UTC)
    events = [
        git_walker.TimelineEvent(
            kind="shipped", pbi_id="PBI-SHIPPED-A", commit_sha="abc1234", when=when
        ),
        git_walker.TimelineEvent(
            kind="cycle_trip",
            pbi_id="META-cycle-20260528T110000Z",
            commit_sha="def5678",
            when=when,
        ),
        git_walker.TimelineEvent(
            kind="pr_opened", pbi_id="PBI-OPENED-B", commit_sha="9876543", when=when
        ),
    ]
    snap = snapshot.load_snapshot(queue_clone=queue_clone)
    html = render.render_page(queue_clone=queue_clone, snapshot=snap, events=events, now=now)

    assert "PBI-SHIPPED-A" in html
    assert "30m ago" in html
    assert "cycle-trip" in html
    assert "META-cycle-20260528T110000Z" in html
    assert "PR opened" in html
    assert "PBI-OPENED-B" in html
    assert "text-danger fw-bold" in html


def test_render_page_falls_back_when_pbi_row_is_error(
    tmp_path: Path, snapshot: ModuleType, render: ModuleType
) -> None:
    """A malformed PBI under .ralph/current surfaces as PBIRowError; the
    renderer must still emit a row (with the directory name + error
    message) rather than raise.
    """
    queue_clone = tmp_path / "queue"
    cur = queue_clone / ".ralph" / "current" / "PBI-BROKEN"
    cur.mkdir(parents=True)
    (cur / "HISTORY.md").write_text("<!-- broken -->\n", encoding="utf-8")

    snap = snapshot.load_snapshot(queue_clone=queue_clone)
    html = render.render_page(
        queue_clone=queue_clone,
        snapshot=snap,
        events=[],
        now=datetime(2026, 5, 28, 10, 42, 13, tzinfo=UTC),
    )

    assert "PBI-BROKEN" in html
    assert "text-danger small" in html


@pytest.fixture(scope="module")
def server_mod() -> ModuleType:
    return _load("ralph_report_server", "server.py")


def test_server_serves_html_and_health(tmp_path: Path, server_mod: ModuleType) -> None:
    import urllib.error
    import urllib.request

    queue_clone = _build_queue_clone(tmp_path)
    _write_pbi(queue_clone / ".ralph" / "current", "PBI-CUR")

    handle = server_mod.start_server(queue_clone=queue_clone, port=0, idle_seconds=60)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/") as resp:
            body = resp.read().decode("utf-8")
            assert resp.status == 200
        assert "PBI-CUR" in body
        assert body.startswith("<!DOCTYPE html>")

        with urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/health") as health:
            assert health.status == 200
            payload = health.read().decode("utf-8")
            assert '"ok": true' in payload

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/nope")
        assert excinfo.value.code == 404
    finally:
        handle.shutdown()
        handle.thread.join(timeout=5)


def test_server_idle_timer_shuts_down(tmp_path: Path, server_mod: ModuleType) -> None:
    queue_clone = tmp_path / "queue"
    (queue_clone / ".ralph").mkdir(parents=True)

    handle = server_mod.start_server(queue_clone=queue_clone, port=0, idle_seconds=1)
    try:
        handle.thread.join(timeout=5)
        assert not handle.thread.is_alive()
    finally:
        handle.shutdown()


# ----------------------------------------------------------------------
# report.main CLI tests use a bare queue remote + an empty workspace,
# mirroring tests/skills/test_ralph_status.py so the resolver chain is
# exercised end-to-end with no network.
# ----------------------------------------------------------------------


def _configure_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "ralph-report@example.com")
    _git(repo, "config", "user.name", "ralph-report")


def _bare_queue(tmp_path: Path) -> tuple[Path, str]:
    """Build a bare queue remote seeded with an empty ralph-queue branch."""
    bare = tmp_path / "queue.git"
    seed = tmp_path / "queue-seed"
    workspace = tmp_path / "ws"
    subprocess.run(["git", "init", "--bare", "-b", "ralph-queue", str(bare)], check=True)
    subprocess.run(["git", "init", "-b", "ralph-queue", str(seed)], check=True)
    _configure_identity(seed)
    _git(seed, "commit", "--allow-empty", "-m", "chore: initial ralph-queue")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-u", "origin", "ralph-queue")
    return workspace, str(bare)


def _seed_pbi_into_queue(
    bare_url: str,
    tmp_path: Path,
    state: str,
    pbi_id: str,
    *,
    entry_file: str = "PBI.md",
) -> None:
    work = tmp_path / f"seed-{pbi_id}-{state}"
    subprocess.run(["git", "clone", bare_url, str(work)], check=True, capture_output=True)
    _configure_identity(work)
    pbi_dir = work / ".ralph" / state / pbi_id
    pbi_dir.mkdir(parents=True)
    (pbi_dir / entry_file).write_text(
        textwrap.dedent(f"""\
            ---
            id: {pbi_id}
            type: {"bug" if entry_file == "BUG.md" else "feature"}
            status: {state}
            severity: normal
            attempts: 0
            created_at: 2026-05-28T00:00:00+00:00
            updated_at: 2026-05-28T00:00:00+00:00
            target_repo: https://github.com/example/test
            ---

            # seed PBI {pbi_id}
            """),
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _git(work, "add", f".ralph/{state}/{pbi_id}")
    _git(work, "commit", "-m", f"chore(queue): add {pbi_id}")
    _git(work, "push", "origin", "ralph-queue")


@pytest.fixture(scope="module")
def report_mod() -> ModuleType:
    return _load("ralph_report_report", "report.py")


def test_report_main_exits_2_when_queue_repo_missing(
    tmp_path: Path, report_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``--queue-repo`` flag AND no TOML entry → exit 2 with the
    ``queue_repo not configured`` error from ``resolve_queue_repo``.
    """
    # Point HOME at an empty temp dir so the resolver can't pick up the
    # caller's real ~/.ralph/config.toml.
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "fake-home"))
    rc = report_mod.main(["--workspace", str(tmp_path / "ws")])
    assert rc == 2


def test_report_main_writes_server_info_and_stops(tmp_path: Path, report_mod: ModuleType) -> None:
    """End-to-end: resolve via flags → spin up server → server-info lands
    under ``<workspace>/report/`` → idle timer fires → server-stopped
    sentinel is dropped.
    """
    import json
    import os
    import threading
    import time

    workspace, queue_repo = _bare_queue(tmp_path)

    rc_holder: dict[str, int] = {}

    def runner() -> None:
        rc_holder["rc"] = report_mod.main(
            [
                "--workspace",
                str(workspace),
                "--queue-repo",
                queue_repo,
                "--port",
                "0",
                "--idle-seconds",
                "1",
            ]
        )

    th = threading.Thread(target=runner, daemon=True, name="ralph-report-runner")
    th.start()

    info_dir = workspace.resolve() / "report"
    info_path = info_dir / "server-info"

    deadline = time.monotonic() + 5.0
    while not info_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert info_path.exists(), "server-info was not written within 5s"

    payload = json.loads(info_path.read_text(encoding="utf-8"))
    assert payload["host"] == "127.0.0.1"
    assert isinstance(payload["port"], int)
    assert payload["port"] > 0
    assert payload["pid"] == os.getpid()
    assert payload["url"].startswith("http://127.0.0.1:")
    assert payload["url"].endswith(str(payload["port"]))
    assert payload["started_at"].endswith("+00:00")
    assert payload["workspace_root"] == str(workspace.resolve())
    assert Path(payload["queue_clone"]).resolve() == (workspace / "queue").resolve()

    th.join(timeout=10)
    assert not th.is_alive(), "report.main did not return after idle timeout"
    assert rc_holder.get("rc") == 0
    assert (info_dir / "server-stopped").is_file()


def test_report_and_status_enumerate_identical_pbi_ids(
    tmp_path: Path,
    snapshot: ModuleType,
) -> None:
    """ralph-report's snapshot must enumerate the same per-state PBI ids
    as ``ralph-status`` given the same operator queue clone.

    Regression test for BUG-RALPH-REPORT-UNIFY-DATA-SOURCE — the bug
    that motivated the unification was the two readers diverging because
    they were pointed at different trees. This test guarantees they
    can't drift again so long as they both consume ``enumerate_state``
    against the same path.
    """
    from scripts.pbi_reader import enumerate_state

    workspace, queue_repo = _bare_queue(tmp_path)
    _seed_pbi_into_queue(queue_repo, tmp_path, "inbox", "PBI-INBOX-A")
    _seed_pbi_into_queue(queue_repo, tmp_path, "inbox", "PBI-INBOX-B")
    _seed_pbi_into_queue(queue_repo, tmp_path, "current", "PBI-CURRENT")
    _seed_pbi_into_queue(queue_repo, tmp_path, "pending-pr", "PBI-PENDING")
    _seed_pbi_into_queue(queue_repo, tmp_path, "blocked", "BUG-BLOCKED", entry_file="BUG.md")

    # Acquire the queue clone the way ralph-status does — this is the
    # SAME path ralph-report must read after the unification.
    from scripts.queue_writer import (
        acquire_queue_clone,
        resolve_queue_branch,
        resolve_queue_repo,
        resolve_workspace_root,
    )

    workspace_root = resolve_workspace_root(workspace)
    queue_clone = acquire_queue_clone(
        workspace_root,
        resolve_queue_repo(queue_repo),
        resolve_queue_branch(None),
    )

    # ralph-report path
    snap = snapshot.load_snapshot(queue_clone=queue_clone)
    report_view = {
        "current": sorted(r.pbi_id for r in snap.current),
        "inbox": sorted(r.pbi_id for r in snap.inbox),
        "pending-pr": sorted(r.pbi_id for r in snap.pending_pr),
        "blocked": sorted(r.pbi_id for r in snap.blocked),
        "done": sorted(r.pbi_id for r in snap.done),
    }

    # ralph-status path (direct enumerate_state, the same call status.py
    # makes per state inside ``_collect_rows``).
    status_view = {
        state: sorted(r.pbi_id for r in enumerate_state(queue_clone, state))
        for state in ("current", "inbox", "pending-pr", "blocked", "done")
    }

    assert report_view == status_view
    # And it actually matches the seeded data — not two empty dicts.
    assert report_view == {
        "current": ["PBI-CURRENT"],
        "inbox": ["PBI-INBOX-A", "PBI-INBOX-B"],
        "pending-pr": ["PBI-PENDING"],
        "blocked": ["BUG-BLOCKED"],
        "done": [],
    }
