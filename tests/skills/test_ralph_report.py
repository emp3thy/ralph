"""Tests for the ``ralph-report`` skill.

The skill lives at ``skills/ralph-report/scripts/*.py``. Because the
directory name is kebab-case (not a valid Python identifier), each
script module is loaded via :func:`importlib.util.spec_from_file_location`
with a synthetic top-level name — the same pattern that
``tests/skills/test_ralph_status.py`` uses for ``ralph-status``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
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


def test_snapshot_load_reads_all_state_folders(tmp_path: Path, snapshot: ModuleType) -> None:
    repo = tmp_path / "repo"
    queue_wt = repo / ".ralph-work" / "queue"
    (queue_wt / ".ralph").mkdir(parents=True)
    for state in ("current", "inbox", "pending-pr", "blocked", "done"):
        (queue_wt / ".ralph" / state).mkdir()

    _write_pbi(queue_wt / ".ralph" / "current", "PBI-CURRENT")
    _write_pbi(queue_wt / ".ralph" / "inbox", "PBI-INBOX-1", severity="high")
    _write_pbi(queue_wt / ".ralph" / "inbox", "PBI-INBOX-2", severity="normal")
    _write_pbi(queue_wt / ".ralph" / "blocked", "BUG-BLOCKED", pbi_type="bug")

    snap = snapshot.load_snapshot(repo_path=repo)

    assert [r.pbi_id for r in snap.current] == ["PBI-CURRENT"]
    assert sorted(r.pbi_id for r in snap.inbox) == ["PBI-INBOX-1", "PBI-INBOX-2"]
    assert snap.pending_pr == []
    assert [r.pbi_id for r in snap.blocked] == ["BUG-BLOCKED"]
    assert snap.meta_cycle_sentinels == []


def test_snapshot_collects_meta_cycle_sentinels(tmp_path: Path, snapshot: ModuleType) -> None:
    repo = tmp_path / "repo"
    blocked = repo / ".ralph-work" / "queue" / ".ralph" / "blocked"
    blocked.mkdir(parents=True)
    (blocked / "META-cycle-20260527T221018Z.md").write_text("body\n", encoding="utf-8")
    (blocked / "README.md").write_text("ignored\n", encoding="utf-8")

    snap = snapshot.load_snapshot(repo_path=repo)

    assert [s.filename for s in snap.meta_cycle_sentinels] == ["META-cycle-20260527T221018Z.md"]


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


def _init_bare_with_queue(tmp_path: Path) -> Path:
    """Seed a bare repo with a populated ``ralph-queue`` branch.

    Returns the path to a fresh clone that has NO queue worktree, so
    ``load_snapshot`` is forced through the git-show fallback.
    """
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True)
    _git(seed, "checkout", "-b", "ralph-queue")
    inbox = seed / ".ralph" / "inbox" / "PBI-FALLBACK"
    inbox.mkdir(parents=True)
    (inbox / "PBI.md").write_text(
        """---
id: PBI-FALLBACK
type: feature
status: inbox
severity: normal
attempts: 0
created_at: 2026-05-28T00:00:00+00:00
updated_at: 2026-05-28T00:00:00+00:00
target_repo: https://github.com/example/test
---

# Fallback PBI
""",
        encoding="utf-8",
    )
    (inbox / "HISTORY.md").write_text("<!-- history -->\n", encoding="utf-8")
    blocked = seed / ".ralph" / "blocked"
    blocked.mkdir(parents=True)
    (blocked / "META-cycle-20260528T000000Z.md").write_text("sentinel\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed")
    _git(seed, "push", "origin", "ralph-queue")

    consumer = tmp_path / "consumer"
    subprocess.run(["git", "clone", str(bare), str(consumer)], check=True)
    _git(consumer, "fetch", "origin", "ralph-queue")
    return consumer


def test_snapshot_falls_back_to_git_show_when_no_queue_worktree(
    tmp_path: Path, snapshot: ModuleType
) -> None:
    consumer = _init_bare_with_queue(tmp_path)
    snap = snapshot.load_snapshot(repo_path=consumer)
    assert [r.pbi_id for r in snap.inbox] == ["PBI-FALLBACK"]
    assert [s.filename for s in snap.meta_cycle_sentinels] == ["META-cycle-20260528T000000Z.md"]
    assert snap.current == []
    assert snap.done == []


def test_snapshot_returns_empty_when_neither_worktree_nor_queue_ref(
    tmp_path: Path, snapshot: ModuleType
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    snap = snapshot.load_snapshot(repo_path=repo)
    assert snap.current == []
    assert snap.inbox == []
    assert snap.pending_pr == []
    assert snap.blocked == []
    assert snap.done == []
    assert snap.meta_cycle_sentinels == []


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
        # Real verbatim subject from origin/ralph-queue that the loose
        # ``add (?P<id>[A-Za-z0-9_-]+)`` regex would false-match on
        # ``target_repo``. The tighter ``[A-Z][A-Z0-9-]*`` rejects it.
        (
            "chore(queue): add target_repo + set status=inbox "
            "on BUG-CLAIM-RACE BUG.md (followup to ba7f862)"
        ),
        # Real verbatim subject — archive operations are not in the kept grammar.
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

    events = git_walker.walk_log(repo_path=consumer, since="365.days.ago")
    kinds = [e.kind for e in events]
    assert "added" in kinds
    assert "claimed" in kinds
    assert "cycle_trip" in kinds
    # persist-iteration subjects must be filtered out
    assert all(e.kind != "persist" for e in events)
    # The added/claimed PBI ids carry through
    added_ids = [e.pbi_id for e in events if e.kind == "added"]
    assert added_ids == ["EXECUTOR-EXIT-WHEN-IDLE-DEFAULT"]


def test_walk_log_returns_empty_when_ref_absent(tmp_path: Path, git_walker: ModuleType) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True)
    assert git_walker.walk_log(repo_path=repo) == []


def test_render_page_contains_each_section_heading(
    tmp_path: Path, snapshot: ModuleType, render: ModuleType
) -> None:
    repo = tmp_path / "repo"
    queue_wt = repo / ".ralph-work" / "queue"
    (queue_wt / ".ralph").mkdir(parents=True)
    for state in ("current", "inbox", "pending-pr", "blocked", "done"):
        (queue_wt / ".ralph" / state).mkdir()
    _write_pbi(queue_wt / ".ralph" / "current", "PBI-CUR")
    _write_pbi(queue_wt / ".ralph" / "inbox", "PBI-IN")

    snap = snapshot.load_snapshot(repo_path=repo)
    html = render.render_page(
        repo_path=repo,
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
    # Bootstrap is loaded via <link rel=stylesheet href=...>, not <script src=...>.
    # The plan's verbatim assertion used ``src=`` which would never match the
    # ``<link href=...>`` element — drop the attribute and assert the CDN URL.
    assert "cdn.jsdelivr.net/npm/bootstrap@5" in html


def test_render_page_renders_timeline_event_labels(
    tmp_path: Path,
    snapshot: ModuleType,
    git_walker: ModuleType,
    render: ModuleType,
) -> None:
    repo = tmp_path / "repo"
    queue_wt = repo / ".ralph-work" / "queue"
    (queue_wt / ".ralph").mkdir(parents=True)
    for state in ("current", "inbox", "pending-pr", "blocked", "done"):
        (queue_wt / ".ralph" / state).mkdir()

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
    snap = snapshot.load_snapshot(repo_path=repo)
    html = render.render_page(repo_path=repo, snapshot=snap, events=events, now=now)

    # Done panel surfaces the shipped event (filtered from the full set).
    assert "PBI-SHIPPED-A" in html
    assert "30m ago" in html  # _format_age — 30 min between when and now
    # Timeline shows ALL three with their human labels.
    assert "cycle-trip" in html
    assert "META-cycle-20260528T110000Z" in html
    assert "PR opened" in html
    assert "PBI-OPENED-B" in html
    # The cycle_trip kind keeps its dramatic CSS class.
    assert "text-danger fw-bold" in html


def test_render_page_falls_back_when_pbi_row_is_error(
    tmp_path: Path, snapshot: ModuleType, render: ModuleType
) -> None:
    """A malformed PBI under .ralph/current surfaces as PBIRowError; the
    renderer must still emit a row (with the directory name + error
    message) rather than raise.
    """
    repo = tmp_path / "repo"
    cur = repo / ".ralph-work" / "queue" / ".ralph" / "current" / "PBI-BROKEN"
    cur.mkdir(parents=True)
    # No PBI.md / BUG.md / FEEDBACK.md → enumerate_state returns PBIRowError.
    (cur / "HISTORY.md").write_text("<!-- broken -->\n", encoding="utf-8")

    snap = snapshot.load_snapshot(repo_path=repo)
    html = render.render_page(
        repo_path=repo,
        snapshot=snap,
        events=[],
        now=datetime(2026, 5, 28, 10, 42, 13, tzinfo=UTC),
    )

    assert "PBI-BROKEN" in html
    assert "text-danger small" in html


@pytest.fixture(scope="module")
def server_mod() -> ModuleType:
    # ``server.py`` itself loads its siblings (snapshot, git_walker, render)
    # via importlib at import time, so this single ``_load`` call is enough.
    return _load("ralph_report_server", "server.py")


def test_server_serves_html_and_health(tmp_path: Path, server_mod: ModuleType) -> None:
    import urllib.error
    import urllib.request

    repo = tmp_path / "repo"
    queue_wt = repo / ".ralph-work" / "queue"
    (queue_wt / ".ralph").mkdir(parents=True)
    for state in ("current", "inbox", "pending-pr", "blocked", "done"):
        (queue_wt / ".ralph" / state).mkdir()
    _write_pbi(queue_wt / ".ralph" / "current", "PBI-CUR")

    handle = server_mod.start_server(repo_path=repo, port=0, idle_seconds=60)
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
    repo = tmp_path / "repo"
    (repo / ".ralph-work" / "queue" / ".ralph").mkdir(parents=True)

    handle = server_mod.start_server(repo_path=repo, port=0, idle_seconds=1)
    try:
        # No request → idle timer fires after ~1s and shuts the server down.
        handle.thread.join(timeout=5)
        assert not handle.thread.is_alive()
    finally:
        handle.shutdown()


@pytest.fixture(scope="module")
def report_mod() -> ModuleType:
    # report.py imports server.py via the same importlib loader; the
    # sibling-cache key (``ralph_report_server``) is shared with the
    # ``server_mod`` fixture, so loading order between the two does not
    # matter.
    return _load("ralph_report_report", "report.py")


def test_report_main_rejects_missing_repo_dir(tmp_path: Path, report_mod: ModuleType) -> None:
    rc = report_mod.main(["--repo", str(tmp_path / "does-not-exist")])
    assert rc == 2


def test_report_main_rejects_non_git_dir(tmp_path: Path, report_mod: ModuleType) -> None:
    not_git = tmp_path / "not-git"
    not_git.mkdir()
    rc = report_mod.main(["--repo", str(not_git)])
    assert rc == 2


def test_report_main_writes_server_info_and_stops(tmp_path: Path, report_mod: ModuleType) -> None:
    import json
    import os
    import threading
    import time

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)

    rc_holder: dict[str, int] = {}

    def runner() -> None:
        rc_holder["rc"] = report_mod.main(
            ["--repo", str(repo), "--port", "0", "--idle-seconds", "1"]
        )

    th = threading.Thread(target=runner, daemon=True, name="ralph-report-runner")
    th.start()

    info_dir = repo.resolve() / ".ralph-work" / "report"
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

    th.join(timeout=10)
    assert not th.is_alive(), "report.main did not return after idle timeout"
    assert rc_holder.get("rc") == 0
    assert (info_dir / "server-stopped").is_file()


def test_end_to_end_renders_all_panels(tmp_path: Path, server_mod: ModuleType) -> None:
    """Drive the full stack: real git init → queue worktree → server → fetch / → assert panels."""
    import urllib.request

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    (repo / "README.md").write_text("seed", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed")

    queue_wt = repo / ".ralph-work" / "queue"
    (queue_wt / ".ralph").mkdir(parents=True)
    for state in ("current", "inbox", "pending-pr", "blocked", "done"):
        (queue_wt / ".ralph" / state).mkdir()
    _write_pbi(queue_wt / ".ralph" / "current", "PBI-WORKING-ON")
    _write_pbi(queue_wt / ".ralph" / "inbox", "PBI-QUEUED")

    handle = server_mod.start_server(repo_path=repo, port=0, idle_seconds=5)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/") as resp:
            body = resp.read().decode("utf-8")
        assert resp.status == 200
    finally:
        handle.shutdown()
        handle.thread.join(timeout=5)

    for marker in ("Current", "Blocked", "Inbox", "Pending PR", "Done", "Activity timeline"):
        assert marker in body, f"missing section heading: {marker}"
    assert "PBI-WORKING-ON" in body
    assert "PBI-QUEUED" in body
