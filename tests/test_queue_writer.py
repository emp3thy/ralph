"""Unit tests for the shared queue-writer helpers.

These helpers back the four supervisor skills (``ralph-add``,
``ralph-cancel``, ``ralph-promote``, ``ralph-triage``) and own all
filesystem + git mutations on the queue clone's ``main`` branch.

Every test builds a local bare repo + working clone in ``tmp_path`` so
pushes are real (to a real local remote) and there is no network.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

import scripts.queue_writer as qw
from scripts.queue_writer import (
    QueueWriterError,
    acquire_queue_clone,
    append_history,
    commit_paths,
    ensure_git_repo,
    find_pbi_directory,
    push,
    read_frontmatter,
    write_frontmatter,
)


def _git(cwd: Path, *args: str) -> str:
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


@pytest.fixture
def git_repo(tmp_path: Path) -> Iterator[Path]:
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", str(work)], check=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test User")
    _git(work, "commit", "--allow-empty", "-m", "chore: initial main commit")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")
    yield work


def test_ensure_git_repo_accepts_real_repo(git_repo: Path) -> None:
    ensure_git_repo(git_repo)


def test_ensure_git_repo_rejects_non_repo(tmp_path: Path) -> None:
    with pytest.raises(QueueWriterError) as exc:
        ensure_git_repo(tmp_path / "not_a_repo")
    assert "not a git repository" in str(exc.value).lower()


def test_acquire_queue_clone_returns_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """acquire_queue_clone forwards to ensure_queue_clone and returns its Path."""
    seen: dict[str, object] = {}

    def fake_ensure(workspace_root: Path, queue_repo: str, *, timeout: float = 120.0) -> Path:
        seen["workspace"] = workspace_root
        seen["queue_repo"] = queue_repo
        seen["timeout"] = timeout
        return workspace_root / "queue"

    monkeypatch.setattr("scripts.queue_writer.ensure_queue_clone", fake_ensure)

    path = acquire_queue_clone(tmp_path, "https://github.com/example/q")
    assert path == tmp_path / "queue"
    assert seen == {
        "workspace": tmp_path,
        "queue_repo": "https://github.com/example/q",
        "timeout": 120.0,
    }


def test_acquire_queue_clone_forwards_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Custom timeout kwarg threads through to ensure_queue_clone."""
    captured: dict[str, object] = {}

    def fake_ensure(workspace_root: Path, queue_repo: str, *, timeout: float = 120.0) -> Path:
        captured["timeout"] = timeout
        return workspace_root / "queue"

    monkeypatch.setattr("scripts.queue_writer.ensure_queue_clone", fake_ensure)
    acquire_queue_clone(tmp_path, "https://github.com/example/q", timeout=30.0)
    assert captured["timeout"] == 30.0


def test_acquire_queue_clone_wraps_queue_clone_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A QueueCloneError from ensure_queue_clone must be re-raised as
    QueueWriterError so skills only need to handle one exception type
    from this module."""
    from ralph_executor.queue_clone import QueueCloneError

    def fake_ensure(workspace_root: Path, queue_repo: str, *, timeout: float = 120.0) -> Path:
        raise QueueCloneError("git fetch failed (exit 128): could not auth")

    monkeypatch.setattr("scripts.queue_writer.ensure_queue_clone", fake_ensure)

    with pytest.raises(QueueWriterError) as excinfo:
        acquire_queue_clone(tmp_path, "https://github.com/example/q")
    assert "git fetch failed" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, QueueCloneError)


def test_checkout_queue_branch_is_removed() -> None:
    """No compat shim: the old branch-checkout helper must not exist."""
    assert not hasattr(qw, "checkout_queue_branch")


def test_commit_paths_stages_and_records_commit(git_repo: Path) -> None:
    pbi_dir = git_repo / ".ralph" / "inbox" / "WI-1"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "PBI.md").write_text("---\nid: WI-1\n---\n", encoding="utf-8")
    sha = commit_paths(
        git_repo,
        [pbi_dir],
        "chore(queue): add WI-1",
    )
    assert sha
    log = _git(git_repo, "log", "-1", "--pretty=%s").strip()
    assert log == "chore(queue): add WI-1"


def test_push_advances_remote(git_repo: Path) -> None:
    before = _git(git_repo, "ls-remote", "origin", "main").strip()
    pbi_dir = git_repo / ".ralph" / "inbox" / "WI-2"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "PBI.md").write_text("---\nid: WI-2\n---\n", encoding="utf-8")
    commit_paths(git_repo, [pbi_dir], "chore(queue): add WI-2")
    push(git_repo, "main")
    after = _git(git_repo, "ls-remote", "origin", "main").strip()
    assert before != after


def test_find_pbi_directory_scans_state_folders(git_repo: Path) -> None:
    target = git_repo / ".ralph" / "blocked" / "WI-3"
    target.mkdir(parents=True)
    (target / "PBI.md").write_text("---\nid: WI-3\n---\n", encoding="utf-8")
    found = find_pbi_directory(git_repo, "WI-3")
    assert found == target


def test_find_pbi_directory_returns_none_when_missing(git_repo: Path) -> None:
    (git_repo / ".ralph" / "inbox").mkdir(parents=True, exist_ok=True)
    assert find_pbi_directory(git_repo, "WI-NOPE") is None


def test_find_pbi_directory_prefers_current_over_other_states(
    git_repo: Path,
) -> None:
    inbox = git_repo / ".ralph" / "inbox" / "WI-4"
    inbox.mkdir(parents=True)
    (inbox / "PBI.md").write_text("---\nid: WI-4\n---\n", encoding="utf-8")
    current = git_repo / ".ralph" / "current" / "WI-4"
    current.mkdir(parents=True)
    (current / "PBI.md").write_text("---\nid: WI-4\n---\n", encoding="utf-8")
    found = find_pbi_directory(git_repo, "WI-4")
    assert found == current


def test_read_frontmatter_round_trips_through_write(tmp_path: Path) -> None:
    entry = tmp_path / "PBI.md"
    entry.write_text(
        "---\n"
        "id: WI-9\n"
        "type: feature\n"
        "status: inbox\n"
        "severity: normal\n"
        "attempts: 0\n"
        "created_at: 2026-05-24T10:00:00+00:00\n"
        "updated_at: 2026-05-24T10:00:00+00:00\n"
        "---\n"
        "\n"
        "# Body text\n",
        encoding="utf-8",
    )
    fm, body = read_frontmatter(entry)
    assert fm["id"] == "WI-9"
    assert fm["severity"] == "normal"
    assert body.startswith("\n# Body text")

    fm["severity"] = "high"
    fm["updated_at"] = "2026-05-24T11:00:00+00:00"
    write_frontmatter(entry, fm, body)

    fm2, body2 = read_frontmatter(entry)
    assert fm2["severity"] == "high"
    assert fm2["updated_at"] == "2026-05-24T11:00:00+00:00"
    assert fm2["id"] == "WI-9"
    assert body2.startswith("\n# Body text")


def test_read_frontmatter_rejects_file_without_fence(tmp_path: Path) -> None:
    entry = tmp_path / "PBI.md"
    entry.write_text("no frontmatter here\n", encoding="utf-8")
    with pytest.raises(QueueWriterError) as exc:
        read_frontmatter(entry)
    assert "frontmatter" in str(exc.value).lower()


def test_append_history_flattens_embedded_newlines_in_detail(tmp_path: Path) -> None:
    """Regression: BugBot PR #35 round-5 flagged that an ``--note`` from
    a shell with escape-sequence expansion (``$'line1\\nline2'``)
    produced a multi-line HISTORY entry, breaking the one-entry-per-block
    format that the dedup check and human readers rely on. Newlines in
    ``actor``/``action``/``detail`` must be collapsed to spaces."""
    pbi_dir = tmp_path / "WI-LF"
    pbi_dir.mkdir()
    append_history(
        pbi_dir,
        actor="ralph-test",
        action="triage",
        detail="first line\nsecond line\r\nthird line",
    )
    history = (pbi_dir / "HISTORY.md").read_text(encoding="utf-8")
    detail_lines = [line for line in history.splitlines() if line.startswith("- detail:")]
    assert len(detail_lines) == 1, f"expected exactly one detail line; got:\n{history}"
    assert detail_lines[0] == "- detail: first line second line third line"


def test_read_frontmatter_handles_crlf_line_endings(tmp_path: Path) -> None:
    """Regression: BugBot PR #35 flagged that ``_split_frontmatter``
    used ``rstrip("\\n")`` on each line, so under Windows CRLF
    (``"---\\r\\n"``) the fence check compared ``"---\\r"`` against
    ``"---"`` and raised "file does not start with a YAML frontmatter
    fence". Affects any PBI checked out with ``core.autocrlf=true``.
    Fix uses ``rstrip("\\r\\n")``."""
    entry = tmp_path / "PBI.md"
    # Write the file with explicit CRLF so the test fails on the old
    # code regardless of host platform / autocrlf setting.
    entry.write_bytes(
        b"---\r\nid: WI-CRLF\r\ntype: feature\r\nseverity: normal\r\n---\r\n\r\n# Body\r\n"
    )
    fm, body = read_frontmatter(entry)
    assert fm["id"] == "WI-CRLF"
    assert fm["severity"] == "normal"
    assert "# Body" in body


def test_append_history_creates_or_extends_history_md(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-5"
    pbi_dir.mkdir()
    append_history(
        pbi_dir,
        actor="ralph-promote",
        action="promote",
        detail="severity: normal -> high",
    )
    text1 = (pbi_dir / "HISTORY.md").read_text(encoding="utf-8")
    assert "ralph-promote" in text1
    assert "severity: normal -> high" in text1
    append_history(
        pbi_dir,
        actor="ralph-triage",
        action="return-to-inbox",
        detail="reset attempts to 0",
    )
    text2 = (pbi_dir / "HISTORY.md").read_text(encoding="utf-8")
    assert "ralph-promote" in text2
    assert "ralph-triage" in text2
    assert text2.count("---") >= 2
