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
