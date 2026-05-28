# ralph-report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local read-only HTTP dashboard (`skills/ralph-report/`) showing the current state of a ralph-queue branch (Current → Blocked → Inbox + Pending PR → Done 24h → Activity timeline 24h) sourced from filesystem reads + `git log`.

**Architecture:** Single Python process. stdlib `http.server` + `threading.Timer` for idle auto-exit. Snapshot panels read `.ralph/<state>/` on the queue worktree (with `git -C <repo> ls-tree origin/ralph-queue` + `git show` fallback). Window panels parse `git log --since=24.hours.ago` against a deterministic commit-message grammar. HTML rendered server-side with Bootstrap 5 from CDN. No JS.

**Tech Stack:** Python 3.12 stdlib only, PyYAML (already a dep), Bootstrap 5 (CDN), bash for launcher/stop scripts. Reuses `scripts.pbi_reader.PBIRow / enumerate_state` from the existing `ralph-status` skill.

**Spec:** `docs/superpowers/specs/2026-05-28-ralph-report-design.md`

**Approved mockup:** `.superpowers/brainstorm/109138-1779996990/content/ralph-report-chosen.html`

---

## File Structure

| File | Role | Status |
|---|---|---|
| `skills/ralph-report/SKILL.md` | Skill docs (purpose, invocation, flags) | Create (Task 8) |
| `skills/ralph-report/scripts/__init__.py` | Package marker for relative imports | Create (Task 0) |
| `skills/ralph-report/scripts/snapshot.py` | Read PBI rows from `.ralph/<state>/` per the queue branch | Create (Task 1) |
| `skills/ralph-report/scripts/git_walker.py` | `git log` runner + commit-message grammar parser → timeline events | Create (Task 2) |
| `skills/ralph-report/scripts/render.py` | Build the full HTML page from snapshot + events | Create (Task 3) |
| `skills/ralph-report/scripts/server.py` | `HTTPServer` + idle auto-exit timer + `/` + `/health` routes | Create (Task 4) |
| `skills/ralph-report/scripts/report.py` | `argparse` entry; starts server; writes `server-info` | Create (Task 5) |
| `skills/ralph-report/scripts/start-server.sh` | Background launcher | Create (Task 6) |
| `skills/ralph-report/scripts/stop-server.sh` | PID kill via `server-info` | Create (Task 6) |
| `tests/skills/test_ralph_report.py` | Unit + integration tests | Create (Tasks 1-7) |

Reused (not modified): `scripts/pbi_reader.py` (PBI frontmatter parser shared with `ralph-status`).

---

## Per-Step Confidence Notes

Each task carries a confidence rating. Sub-90% steps embed mitigations directly in the task body.

| Task | Confidence | Mitigation summary |
|---|---|---|
| 0 Scaffold | 99% | n/a |
| 1 snapshot.py (worktree) | 95% | Reuses `pbi_reader.enumerate_state` |
| 1b snapshot.py (git-show fallback) | 80% | Parametrised unit tests against a bare-repo fixture; defer if not needed for the queue-worktree happy path |
| 2 git_walker.py | 88% | Parametrise regex test against verbatim subjects from current `git reflog ralph-queue` output |
| 3 render.py | 90% | Golden-file snapshot test + assertion for every text marker the spec calls out |
| 4 server.py | 85% | Idle timer reset semantics: `threading.Timer.cancel() + new Timer()` on every request — covered by a dedicated test that fakes time |
| 5 report.py | 95% | argparse + side-effect glue; small surface |
| 6 start/stop scripts | 85% | Windows uses Git Bash — keep scripts POSIX; verify both `start-server.sh` and `stop-server.sh` via shellcheck in CI |
| 7 Integration | 88% | Reuse fixture pattern from `tests/skills/test_ralph_status.py` |
| 8 SKILL.md | 99% | n/a |

---

## Task 0: Scaffold

**Files:**
- Create: `skills/ralph-report/scripts/__init__.py`
- Create: `tests/skills/__init__.py` (if missing)

- [ ] **Step 1: Verify `tests/skills/__init__.py` exists**

```bash
ls tests/skills/__init__.py
```
Expected: file exists. If missing, create an empty file:

```bash
touch tests/skills/__init__.py
```

- [ ] **Step 2: Create the script package marker**

Create `skills/ralph-report/scripts/__init__.py` with this content:

```python
"""Scripts for the ralph-report skill (local HTML dashboard of ralph-queue activity)."""
```

- [ ] **Step 3: Commit**

```bash
git add skills/ralph-report/scripts/__init__.py tests/skills/__init__.py
git commit -m "feat(ralph-report): scaffold skill directory"
```

---

## Task 1: snapshot.py — queue worktree path

**Files:**
- Create: `skills/ralph-report/scripts/snapshot.py`
- Create: `tests/skills/test_ralph_report.py`

This task implements the **worktree-mode** read path. The git-show fallback is a separate task (1b) so the happy path lands first.

- [ ] **Step 1: Write the failing test**

Create `tests/skills/test_ralph_report.py`:

```python
"""Tests for the ralph-report skill."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from skills.ralph_report.scripts import snapshot


def _write_pbi(
    state_dir: Path,
    pbi_id: str,
    *,
    pbi_type: str = "feature",
    severity: str = "normal",
    title: str = "test pbi",
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
depends_on: []
---

# {title}
""",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("<!-- history -->\n", encoding="utf-8")


def test_snapshot_load_reads_all_state_folders(tmp_path: Path) -> None:
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/skills/test_ralph_report.py::test_snapshot_load_reads_all_state_folders -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'skills.ralph_report'` (or similar import error — `snapshot` does not yet exist).

- [ ] **Step 3: Write `snapshot.py`**

Create `skills/ralph-report/scripts/snapshot.py`:

```python
"""Read the snapshot panels (current/inbox/pending-pr/blocked) from the ralph-queue worktree.

This module reuses ``scripts.pbi_reader.enumerate_state`` for PBI directory
parsing. It does NOT read the timeline / done-24h data — those come from
``git_walker.py`` because they are commit-history-derived.

The reader prefers the queue worktree at ``<repo>/.ralph-work/queue``.
A ``ls-tree``/``git show`` fallback for the no-worktree case lives in
``_load_via_git_show`` (Task 1b).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# Make ``scripts.pbi_reader`` importable when invoked from the ralph repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.pbi_reader import PBIRow, PBIRowError, enumerate_state  # noqa: E402


@dataclass(frozen=True)
class MetaCycleSentinel:
    """A META-cycle-*.md sentinel file in .ralph/blocked/."""

    filename: str
    path: Path


@dataclass
class Snapshot:
    current: list[PBIRow | PBIRowError] = field(default_factory=list)
    inbox: list[PBIRow | PBIRowError] = field(default_factory=list)
    pending_pr: list[PBIRow | PBIRowError] = field(default_factory=list)
    blocked: list[PBIRow | PBIRowError] = field(default_factory=list)
    done: list[PBIRow | PBIRowError] = field(default_factory=list)
    meta_cycle_sentinels: list[MetaCycleSentinel] = field(default_factory=list)


class SnapshotError(RuntimeError):
    """Raised when the snapshot cannot be loaded at all."""


def _queue_worktree(repo_path: Path) -> Path | None:
    candidate = repo_path / ".ralph-work" / "queue" / ".ralph"
    if candidate.is_dir():
        return repo_path / ".ralph-work" / "queue"
    return None


def _collect_meta_cycle_sentinels(blocked_dir: Path) -> list[MetaCycleSentinel]:
    if not blocked_dir.is_dir():
        return []
    sentinels: list[MetaCycleSentinel] = []
    for child in sorted(blocked_dir.iterdir()):
        if child.is_file() and child.name.startswith("META-cycle-") and child.suffix == ".md":
            sentinels.append(MetaCycleSentinel(filename=child.name, path=child))
    return sentinels


def load_snapshot(*, repo_path: Path) -> Snapshot:
    """Read snapshot panels from the ralph-queue worktree of ``repo_path``."""
    queue_root = _queue_worktree(repo_path)
    if queue_root is None:
        # Fallback path is implemented in Task 1b.
        raise SnapshotError(
            f"queue worktree not found at {repo_path / '.ralph-work' / 'queue'}; "
            "git-show fallback not implemented yet"
        )
    snap = Snapshot(
        current=enumerate_state(queue_root, "current", repo_name=repo_path.name),
        inbox=enumerate_state(queue_root, "inbox", repo_name=repo_path.name),
        pending_pr=enumerate_state(queue_root, "pending-pr", repo_name=repo_path.name),
        blocked=enumerate_state(queue_root, "blocked", repo_name=repo_path.name),
        done=enumerate_state(queue_root, "done", repo_name=repo_path.name),
        meta_cycle_sentinels=_collect_meta_cycle_sentinels(queue_root / ".ralph" / "blocked"),
    )
    return snap
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/skills/test_ralph_report.py::test_snapshot_load_reads_all_state_folders -v
```
Expected: PASS.

- [ ] **Step 5: Add a META-cycle sentinel test**

Append to `tests/skills/test_ralph_report.py`:

```python
def test_snapshot_collects_meta_cycle_sentinels(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    blocked = repo / ".ralph-work" / "queue" / ".ralph" / "blocked"
    blocked.mkdir(parents=True)
    (blocked / "META-cycle-20260527T221018Z.md").write_text("body\n", encoding="utf-8")
    (blocked / "README.md").write_text("ignored\n", encoding="utf-8")

    snap = snapshot.load_snapshot(repo_path=repo)

    assert [s.filename for s in snap.meta_cycle_sentinels] == [
        "META-cycle-20260527T221018Z.md"
    ]
```

Run:

```bash
uv run pytest tests/skills/test_ralph_report.py -v
```
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add skills/ralph-report/scripts/snapshot.py tests/skills/test_ralph_report.py
git commit -m "feat(ralph-report): snapshot.py reads .ralph/* via queue worktree"
```

---

## Task 1b: snapshot.py — git-show fallback (when queue worktree missing)

**Files:**
- Modify: `skills/ralph-report/scripts/snapshot.py`
- Modify: `tests/skills/test_ralph_report.py`

**Confidence: 80%** — interacts with a real git repo via subprocess. Mitigation: parametrised tests against a temporary bare repo fixture.

- [ ] **Step 1: Write the failing test**

Append to `tests/skills/test_ralph_report.py`:

```python
import subprocess


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
    """Initialise a bare repo with a populated `ralph-queue` branch.

    Returns the path to a *non-bare* clone that has NO queue worktree —
    so the snapshot loader is forced down the git-show fallback.
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
depends_on: []
---

# Fallback PBI
""",
        encoding="utf-8",
    )
    (inbox / "HISTORY.md").write_text("<!-- history -->\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed")
    _git(seed, "push", "origin", "ralph-queue")

    consumer = tmp_path / "consumer"
    subprocess.run(["git", "clone", str(bare), str(consumer)], check=True)
    _git(consumer, "fetch", "origin", "ralph-queue")
    return consumer


def test_snapshot_falls_back_to_git_show_when_no_queue_worktree(tmp_path: Path) -> None:
    consumer = _init_bare_with_queue(tmp_path)
    snap = snapshot.load_snapshot(repo_path=consumer)
    assert [r.pbi_id for r in snap.inbox] == ["PBI-FALLBACK"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/skills/test_ralph_report.py::test_snapshot_falls_back_to_git_show_when_no_queue_worktree -v
```
Expected: FAIL with `SnapshotError: queue worktree not found ...`.

- [ ] **Step 3: Implement the fallback**

Edit `skills/ralph-report/scripts/snapshot.py`:

a) Add at the top of the file (below existing imports):

```python
import subprocess
import tempfile
from datetime import datetime
```

b) Replace the `load_snapshot` function with:

```python
def load_snapshot(*, repo_path: Path) -> Snapshot:
    """Read snapshot panels from ``repo_path``.

    Prefers the queue worktree at ``<repo>/.ralph-work/queue``. Falls
    back to ``git -C <repo> show origin/ralph-queue:<path>`` when the
    worktree is absent.
    """
    queue_root = _queue_worktree(repo_path)
    if queue_root is not None:
        return _load_via_worktree(repo_path, queue_root)
    return _load_via_git_show(repo_path)


def _load_via_worktree(repo_path: Path, queue_root: Path) -> Snapshot:
    return Snapshot(
        current=enumerate_state(queue_root, "current", repo_name=repo_path.name),
        inbox=enumerate_state(queue_root, "inbox", repo_name=repo_path.name),
        pending_pr=enumerate_state(queue_root, "pending-pr", repo_name=repo_path.name),
        blocked=enumerate_state(queue_root, "blocked", repo_name=repo_path.name),
        done=enumerate_state(queue_root, "done", repo_name=repo_path.name),
        meta_cycle_sentinels=_collect_meta_cycle_sentinels(queue_root / ".ralph" / "blocked"),
    )


_QUEUE_REF = "origin/ralph-queue"


def _load_via_git_show(repo_path: Path) -> Snapshot:
    """Materialise a temporary worktree of ``origin/ralph-queue`` and read from it.

    Rather than re-implementing the PBI parser on top of ``git show``, we
    create a short-lived staging dir, copy each entry file out of the
    queue ref via ``git show``, then call ``enumerate_state`` against the
    staging tree. The staging dir is discarded when the function returns.
    """
    snap = Snapshot()
    staging = Path(tempfile.mkdtemp(prefix="ralph-report-"))
    try:
        for state in ("current", "inbox", "pending-pr", "blocked", "done"):
            state_paths = _ls_tree_dirs(repo_path, f"{_QUEUE_REF}:.ralph/{state}")
            target_dir = staging / ".ralph" / state
            target_dir.mkdir(parents=True)
            for pbi_id in state_paths:
                _materialise_pbi(repo_path, state, pbi_id, target_dir)
            snap.__dict__[_attr_for_state(state)] = enumerate_state(
                staging, state, repo_name=repo_path.name
            )
        snap.meta_cycle_sentinels = _materialise_meta_sentinels(
            repo_path, staging / ".ralph" / "blocked"
        )
    finally:
        # Best-effort cleanup; ignore Windows-handle quirks.
        _rmtree_quiet(staging)
    return snap


def _attr_for_state(state: str) -> str:
    return {"pending-pr": "pending_pr"}.get(state, state)


def _ls_tree_dirs(repo_path: Path, ref_with_path: str) -> list[str]:
    """Return directory names under ``ref_with_path`` (e.g. ``origin/ralph-queue:.ralph/inbox``)."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-tree", "--name-only", ref_with_path],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        # Folder absent on the queue branch (legitimate for an empty queue).
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _materialise_pbi(repo_path: Path, state: str, pbi_id: str, target_dir: Path) -> None:
    pbi_dir = target_dir / pbi_id
    pbi_dir.mkdir()
    for entry_name in ("PBI.md", "BUG.md", "FEEDBACK.md", "HISTORY.md", "PR-LINK.md", "PLAN.md"):
        ref_path = f"{_QUEUE_REF}:.ralph/{state}/{pbi_id}/{entry_name}"
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "show", ref_path],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            (pbi_dir / entry_name).write_text(proc.stdout, encoding="utf-8")


def _materialise_meta_sentinels(repo_path: Path, target_dir: Path) -> list[MetaCycleSentinel]:
    target_dir.mkdir(parents=True, exist_ok=True)
    sentinels: list[MetaCycleSentinel] = []
    for name in _ls_tree_dirs(repo_path, f"{_QUEUE_REF}:.ralph/blocked"):
        if not (name.startswith("META-cycle-") and name.endswith(".md")):
            continue
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "show", f"{_QUEUE_REF}:.ralph/blocked/{name}"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            continue
        path = target_dir / name
        path.write_text(proc.stdout, encoding="utf-8")
        sentinels.append(MetaCycleSentinel(filename=name, path=path))
    return sentinels


def _rmtree_quiet(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
```

- [ ] **Step 4: Run all snapshot tests**

```bash
uv run pytest tests/skills/test_ralph_report.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/ralph-report/scripts/snapshot.py tests/skills/test_ralph_report.py
git commit -m "feat(ralph-report): snapshot git-show fallback when queue worktree absent"
```

---

## Task 2: git_walker.py — commit-message grammar + 24h log walker

**Files:**
- Create: `skills/ralph-report/scripts/git_walker.py`
- Modify: `tests/skills/test_ralph_report.py`

**Confidence: 88%** — relies on commit-subject regex. Mitigation: parametrised test against verbatim subjects from current `git reflog ralph-queue` output (table embedded below).

- [ ] **Step 1: Write the failing test**

Append to `tests/skills/test_ralph_report.py`:

```python
from skills.ralph_report.scripts import git_walker


@pytest.mark.parametrize(
    "subject,expected_kind,expected_id",
    [
        ("chore(queue): add EXECUTOR-EXIT-WHEN-IDLE-DEFAULT", "added", "EXECUTOR-EXIT-WHEN-IDLE-DEFAULT"),
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
    subject: str, expected_kind: str, expected_id: str
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
    ],
)
def test_parse_commit_subject_filtered_or_unrelated(subject: str) -> None:
    assert git_walker.parse_commit_subject(subject) is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/skills/test_ralph_report.py::test_parse_commit_subject_known_patterns -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `git_walker.py`**

Create `skills/ralph-report/scripts/git_walker.py`:

```python
"""Walk ``git log`` on ralph-queue and extract typed timeline events.

Two surfaces:

* :func:`parse_commit_subject` — pure regex matcher; the unit-test surface.
* :func:`walk_log` — runs ``git log --since=<window> ralph-queue``, parses
  each subject, and emits :class:`TimelineEvent` instances (plus
  cycle-trip events derived from ``--name-only`` output).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

EventKind = Literal[
    "added",
    "claimed",
    "pr_opened",
    "shipped",
    "blocked",
    "cycle_trip",
]


@dataclass(frozen=True)
class TimelineEvent:
    kind: EventKind
    pbi_id: str
    commit_sha: str
    when: datetime


_PATTERNS: tuple[tuple[re.Pattern[str], EventKind], ...] = (
    (re.compile(r"^chore\(queue\): add (?P<id>[A-Za-z0-9][A-Za-z0-9_-]*)\b"), "added"),
    (
        re.compile(
            r"^chore\(ralph-queue\): move (?P<id>\S+) from inbox to current\b"
        ),
        "claimed",
    ),
    (
        re.compile(
            r"^feat\(ralph-queue\): move (?P<id>\S+) from current to pending-pr\b"
        ),
        "pr_opened",
    ),
    (
        re.compile(
            r"^chore\(ralph-queue\): move (?P<id>\S+) from pending-pr to done\b"
        ),
        "shipped",
    ),
    (
        re.compile(
            r"^chore\(ralph-queue\): move (?P<id>\S+) from current to blocked\b"
        ),
        "blocked",
    ),
)


def parse_commit_subject(subject: str) -> TimelineEvent | None:
    """Return a typed event for a known commit subject, else ``None``."""
    for pattern, kind in _PATTERNS:
        m = pattern.match(subject)
        if m is not None:
            return TimelineEvent(
                kind=kind,
                pbi_id=m.group("id"),
                commit_sha="",  # populated by walk_log
                when=datetime.fromtimestamp(0),  # populated by walk_log
            )
    return None


_META_CYCLE_RE = re.compile(r"^\.ralph/blocked/META-cycle-[^/]+\.md$")


def walk_log(
    *,
    repo_path: Path,
    ref: str = "origin/ralph-queue",
    since: str = "24.hours.ago",
) -> list[TimelineEvent]:
    """Return events on ``ref`` from ``since`` to HEAD, newest-first."""
    fmt = "%H%x09%aI%x09%s"
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "log",
            ref,
            f"--since={since}",
            f"--pretty=format:{fmt}",
            "--name-status",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return []
    events: list[TimelineEvent] = []
    current_header: tuple[str, datetime, str] | None = None
    for raw in result.stdout.splitlines():
        if not raw.strip():
            current_header = None
            continue
        if "\t" in raw and len(raw.split("\t", 2)) == 3 and not raw.startswith(("A\t", "M\t", "D\t", "R\t")):
            sha, iso, subject = raw.split("\t", 2)
            try:
                when = datetime.fromisoformat(iso)
            except ValueError:
                current_header = None
                continue
            current_header = (sha, when, subject)
            event = parse_commit_subject(subject)
            if event is not None:
                events.append(
                    TimelineEvent(
                        kind=event.kind,
                        pbi_id=event.pbi_id,
                        commit_sha=sha,
                        when=when,
                    )
                )
            continue
        # name-status row for the current commit
        if current_header is None:
            continue
        parts = raw.split("\t", 1)
        if len(parts) != 2:
            continue
        status, path = parts[0], parts[1]
        if status == "A" and _META_CYCLE_RE.match(path):
            sha, when, _subj = current_header
            events.append(
                TimelineEvent(
                    kind="cycle_trip",
                    pbi_id=Path(path).stem,
                    commit_sha=sha,
                    when=when,
                )
            )
    return events
```

- [ ] **Step 4: Run all walker tests**

```bash
uv run pytest tests/skills/test_ralph_report.py -v -k git_walker or parse_commit
```
Expected: PASS.

- [ ] **Step 5: Add a fixture-repo integration test for `walk_log`**

Append:

```python
def test_walk_log_picks_up_scripted_commits(tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True)
    _git(seed, "checkout", "-b", "ralph-queue")
    (seed / "x.txt").write_text("x", encoding="utf-8")
    _git(seed, "add", "x.txt")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m",
         "chore(queue): add EXECUTOR-EXIT-WHEN-IDLE-DEFAULT")
    (seed / "y.txt").write_text("y", encoding="utf-8")
    _git(seed, "add", "y.txt")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m",
         "chore(ralph-queue): move EXECUTOR-EXIT-WHEN-IDLE-DEFAULT from inbox to current")
    (seed / "z.txt").write_text("z", encoding="utf-8")
    _git(seed, "add", "z.txt")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m",
         "chore(queue): persist iteration writes for EXECUTOR-EXIT-WHEN-IDLE-DEFAULT")
    _git(seed, "push", "origin", "ralph-queue")
    consumer = tmp_path / "consumer"
    subprocess.run(["git", "clone", str(bare), str(consumer)], check=True)
    _git(consumer, "fetch", "origin", "ralph-queue")

    events = git_walker.walk_log(repo_path=consumer, since="365.days.ago")
    kinds = [e.kind for e in events]
    assert "added" in kinds
    assert "claimed" in kinds
    # The persist-iteration commit MUST be filtered out
    assert all(e.kind != "persist" for e in events)
```

Run:

```bash
uv run pytest tests/skills/test_ralph_report.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add skills/ralph-report/scripts/git_walker.py tests/skills/test_ralph_report.py
git commit -m "feat(ralph-report): git_walker.py parses ralph-queue commit grammar"
```

---

## Task 3: render.py — HTML page from snapshot + events

**Files:**
- Create: `skills/ralph-report/scripts/render.py`
- Modify: `tests/skills/test_ralph_report.py`

**Confidence: 90%** — Bootstrap CDN markup is not type-checked. Mitigation: assertion-based tests for every expected text marker.

- [ ] **Step 1: Write the failing test**

Append:

```python
from skills.ralph_report.scripts import render


def test_render_page_contains_each_section_heading(tmp_path: Path) -> None:
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
    assert "F5" in html  # refresh hint
    assert 'src="https://cdn.jsdelivr.net/npm/bootstrap@5' in html
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/skills/test_ralph_report.py::test_render_page_contains_each_section_heading -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'skills.ralph_report.scripts.render'`.

- [ ] **Step 3: Implement `render.py`**

Create `skills/ralph-report/scripts/render.py`:

```python
"""Render the report HTML page from snapshot + timeline data.

Pure function (no I/O). Returns a full HTML document string starting
with ``<!DOCTYPE html>``. Bootstrap 5 is loaded from CDN; all other
styling is inlined in the ``<style>`` block.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from scripts.pbi_reader import PBIRow, PBIRowError

from .git_walker import TimelineEvent
from .snapshot import MetaCycleSentinel, Snapshot

_BOOTSTRAP_CSS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"

_SEVERITY_BADGE_CLASS = {
    "critical": "bg-danger",
    "high": "text-bg-warning",
    "normal": "bg-primary",
    "low": "bg-secondary",
}

_EVENT_LABEL = {
    "added": ("added", "text-primary"),
    "claimed": ("claimed", "text-success"),
    "pr_opened": ("PR opened", "text-purple"),
    "shipped": ("shipped", "text-success fw-bold"),
    "blocked": ("blocked", "text-danger"),
    "cycle_trip": ("cycle-trip", "text-danger fw-bold"),
}


def render_page(
    *,
    repo_path: Path,
    snapshot: Snapshot,
    events: list[TimelineEvent],
    now: datetime,
) -> str:
    cutoff = now - timedelta(hours=24)
    shipped_events = sorted(
        (e for e in events if e.kind == "shipped" and e.when >= cutoff),
        key=lambda e: e.when,
        reverse=True,
    )
    timeline_events = sorted(
        (e for e in events if e.when >= cutoff),
        key=lambda e: e.when,
        reverse=True,
    )
    refresh_iso = now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return _PAGE_TEMPLATE.format(
        bootstrap_css=_BOOTSTRAP_CSS,
        repo_path=escape(str(repo_path)),
        refresh_iso=escape(refresh_iso),
        current_panel=_render_current(snapshot.current),
        blocked_panel=_render_blocked(snapshot.blocked, snapshot.meta_cycle_sentinels),
        inbox_panel=_render_inbox(snapshot.inbox),
        pending_pr_panel=_render_pending_pr(snapshot.pending_pr),
        done_panel=_render_done(snapshot.done, shipped_events, now),
        timeline_panel=_render_timeline(timeline_events, now),
    )


def _render_pbi_row(row: PBIRow | PBIRowError, *, extras: str = "") -> str:
    if isinstance(row, PBIRowError):
        return (
            f'<div class="row-pbi"><span class="id">{escape(row.pbi_dir.name)}</span>'
            f'<span class="text-danger small">{escape(row.message)}</span></div>'
        )
    sev_class = _SEVERITY_BADGE_CLASS.get(row.severity, "bg-secondary")
    return (
        '<div class="row-pbi">'
        f'<span class="id">{escape(row.pbi_id)}</span>'
        f'<span class="badge badge-pill {sev_class}">{escape(row.severity)}</span>'
        f'<span class="badge badge-pill bg-info text-dark">{escape(row.pbi_type)}</span>'
        f'<span class="text-muted small">{escape(row.title)}{extras}</span>'
        '</div>'
    )


def _render_current(rows: list[PBIRow | PBIRowError]) -> str:
    if not rows:
        body = '<div class="empty-state">No current PBI.</div>'
    else:
        body = "".join(_render_pbi_row(r) for r in rows)
    return _panel("Current", len(rows), "panel-current text-success", body)


def _render_blocked(
    rows: list[PBIRow | PBIRowError],
    sentinels: list[MetaCycleSentinel],
) -> str:
    total = len(rows) + len(sentinels)
    pieces: list[str] = [_render_pbi_row(r) for r in rows]
    for s in sentinels:
        pieces.append(
            '<div class="row-pbi">'
            f'<span class="id">{escape(s.filename)}</span>'
            '<span class="badge badge-pill bg-danger">sentinel</span>'
            '<span class="text-muted small">cycle-detector trip</span>'
            '</div>'
        )
    body = "".join(pieces) if pieces else '<div class="empty-state">Nothing blocked.</div>'
    return _panel("Blocked", total, "panel-blocked text-danger", body)


def _render_inbox(rows: list[PBIRow | PBIRowError]) -> str:
    if not rows:
        body = '<div class="empty-state">Inbox is empty.</div>'
    else:
        body = "".join(_render_pbi_row(r) for r in rows)
    return _panel("Inbox", len(rows), "panel-snapshot", body)


def _render_pending_pr(rows: list[PBIRow | PBIRowError]) -> str:
    if not rows:
        body = '<div class="empty-state">No PRs in review.</div>'
    else:
        body = "".join(_render_pbi_row(r) for r in rows)
    return _panel("Pending PR", len(rows), "panel-snapshot", body)


def _render_done(
    rows: list[PBIRow | PBIRowError],
    shipped: list[TimelineEvent],
    now: datetime,
) -> str:
    if not shipped:
        body = '<div class="empty-state">Nothing shipped in the last 24h.</div>'
    else:
        lines: list[str] = []
        for event in shipped:
            age = _format_age(now - event.when)
            lines.append(
                '<div class="row-pbi">'
                f'<span class="id">{escape(event.pbi_id)}</span>'
                '<span class="badge badge-pill bg-success">shipped</span>'
                f'<span class="text-muted small">{age} ago</span>'
                '</div>'
            )
        body = "".join(lines)
    return _panel("Done (last 24h)", len(shipped), "panel-window text-secondary", body)


def _render_timeline(events: list[TimelineEvent], now: datetime) -> str:
    if not events:
        body = '<div class="empty-state">No state-change events in the last 24h.</div>'
    else:
        lines: list[str] = []
        for e in events:
            label, css = _EVENT_LABEL[e.kind]
            hhmm = e.when.astimezone(timezone.utc).strftime("%H:%M")
            lines.append(
                '<div class="timeline-row">'
                f'<span class="t-time">{escape(hhmm)}</span>'
                f'<span class="t-kind {css}">{escape(label)}</span>'
                f'<span>{escape(e.pbi_id)}</span>'
                '</div>'
            )
        body = "".join(lines)
    return _panel(
        "Activity timeline (last 24h, state-changes only)",
        len(events),
        "panel-window text-secondary",
        body,
    )


def _panel(title: str, count: int, classes: str, body: str) -> str:
    return (
        f'<div class="mock {classes} mb-3">'
        f'<h6>{escape(title)} <span class="badge bg-secondary">{count}</span></h6>'
        f'{body}'
        '</div>'
    )


def _format_age(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ralph-report</title>
<link rel="stylesheet" href="{bootstrap_css}">
<style>
  body {{ background:#eef2f6; padding-bottom:3rem; }}
  .container-narrow {{ max-width: 1200px; }}
  .mock {{ background:#fff; border-radius:0.5rem; box-shadow:0 1px 3px rgba(0,0,0,0.08); padding:1rem; }}
  .mock h6 {{ font-weight:600; margin-bottom:0.5rem; }}
  .panel-snapshot {{ border-left:4px solid #0d6efd; }}
  .panel-window   {{ border-left:4px solid #6c757d; }}
  .panel-current  {{ border-left:4px solid #198754; }}
  .panel-blocked  {{ border-left:4px solid #dc3545; }}
  .row-pbi {{ font-size:0.85rem; padding:0.3rem 0.5rem; border-bottom:1px solid #f0f2f5; display:flex; gap:0.5rem; align-items:center; }}
  .row-pbi:last-child {{ border-bottom:none; }}
  .row-pbi .id {{ font-family:monospace; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .badge-pill {{ font-size:0.7rem; padding:0.15rem 0.45rem; }}
  .timeline-row {{ font-size:0.85rem; padding:0.3rem 0.6rem; border-bottom:1px dashed #e9ecef; font-family:monospace; display:flex; gap:0.75rem; }}
  .timeline-row:last-child {{ border-bottom:none; }}
  .timeline-row .t-time {{ color:#6c757d; min-width:48px; }}
  .timeline-row .t-kind {{ min-width:90px; }}
  .empty-state {{ color:#adb5bd; font-style:italic; font-size:0.85rem; padding:0.3rem 0.5rem; }}
  .header-bar {{ display:flex; align-items:center; gap:1rem; margin-bottom:1rem; }}
  .repo-pill {{ background:#212529; color:#fff; padding:0.3rem 0.6rem; border-radius:0.3rem; font-family:monospace; font-size:0.8rem; }}
</style>
</head>
<body>
<div class="container container-narrow py-4">
  <header class="header-bar">
    <h1 class="mb-0 h3">ralph-report</h1>
    <span class="repo-pill">{repo_path}</span>
    <span class="ms-auto text-muted small">refreshed {refresh_iso} &middot; F5 to update</span>
  </header>
  {current_panel}
  {blocked_panel}
  <div class="row g-3 mb-3">
    <div class="col-md-6">{inbox_panel}</div>
    <div class="col-md-6">{pending_pr_panel}</div>
  </div>
  {done_panel}
  {timeline_panel}
</div>
</body>
</html>
"""
```

- [ ] **Step 4: Run all tests**

```bash
uv run pytest tests/skills/test_ralph_report.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/ralph-report/scripts/render.py tests/skills/test_ralph_report.py
git commit -m "feat(ralph-report): render.py emits Bootstrap dashboard HTML"
```

---

## Task 4: server.py — HTTPServer + idle auto-exit

**Files:**
- Create: `skills/ralph-report/scripts/server.py`
- Modify: `tests/skills/test_ralph_report.py`

**Confidence: 85%** — idle timer + thread safety. Mitigation: test resets the timer explicitly with a tiny timeout, asserts shutdown happens on schedule.

- [ ] **Step 1: Write the failing test**

Append:

```python
import threading
import time
import urllib.request

from skills.ralph_report.scripts import server as server_mod


def test_server_serves_html_and_idles_out(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    queue_wt = repo / ".ralph-work" / "queue"
    (queue_wt / ".ralph").mkdir(parents=True)
    for state in ("current", "inbox", "pending-pr", "blocked", "done"):
        (queue_wt / ".ralph" / state).mkdir()
    _write_pbi(queue_wt / ".ralph" / "current", "PBI-CUR")

    handle = server_mod.start_server(repo_path=repo, port=0, idle_seconds=1)
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/")
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "PBI-CUR" in body
        assert "<!DOCTYPE html>" in body

        health = urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/health")
        assert health.status == 200
    finally:
        handle.shutdown()
        handle.thread.join(timeout=5)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/skills/test_ralph_report.py::test_server_serves_html_and_idles_out -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `server.py`**

Create `skills/ralph-report/scripts/server.py`:

```python
"""HTTP server with idle auto-exit for ralph-report.

Routes:
  GET /        → full report HTML (resets idle timer)
  GET /health  → JSON `{"ok": true, "last_refresh": "<iso>"}` (resets idle timer)
  Anything else → 404
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import git_walker, render, snapshot


@dataclass
class ServerHandle:
    port: int
    thread: threading.Thread
    server: ThreadingHTTPServer

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def start_server(
    *,
    repo_path: Path,
    port: int = 0,
    idle_seconds: int = 1800,
    bind_host: str = "127.0.0.1",
) -> ServerHandle:
    """Start the report HTTP server and return a handle.

    ``port=0`` lets the OS pick a free port; the handle exposes the
    chosen value via ``handle.port``. The idle timer fires
    ``server.shutdown()`` after ``idle_seconds`` of no requests; each
    request to ``/`` or ``/health`` resets the timer.
    """
    repo_path = repo_path.resolve()
    handler_factory, reset_timer = _build_handler(repo_path, idle_seconds)
    httpd = ThreadingHTTPServer((bind_host, port), handler_factory)
    actual_port = httpd.server_address[1]

    def serve() -> None:
        try:
            httpd.serve_forever(poll_interval=0.1)
        finally:
            httpd.server_close()

    thread = threading.Thread(target=serve, daemon=True, name="ralph-report-http")
    thread.start()
    reset_timer(lambda: httpd.shutdown())
    return ServerHandle(port=actual_port, thread=thread, server=httpd)


def _build_handler(repo_path: Path, idle_seconds: int):
    state: dict[str, object] = {"timer": None, "shutdown": None}
    lock = threading.Lock()

    def reset_timer(shutdown_cb=None) -> None:
        with lock:
            if shutdown_cb is not None:
                state["shutdown"] = shutdown_cb
            timer = state.get("timer")
            if isinstance(timer, threading.Timer):
                timer.cancel()
            cb = state.get("shutdown")
            if callable(cb):
                t = threading.Timer(idle_seconds, cb)
                t.daemon = True
                t.start()
                state["timer"] = t

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:  # silence default access log
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            if self.path == "/":
                body = self._render_full()
                self._respond(200, "text/html; charset=utf-8", body.encode("utf-8"))
                reset_timer()
            elif self.path == "/health":
                payload = {
                    "ok": True,
                    "last_refresh": datetime.now(tz=UTC).isoformat(),
                }
                self._respond(
                    200,
                    "application/json; charset=utf-8",
                    json.dumps(payload).encode("utf-8"),
                )
                reset_timer()
            else:
                self._respond(404, "text/plain; charset=utf-8", b"not found")

        def _render_full(self) -> str:
            snap = snapshot.load_snapshot(repo_path=repo_path)
            events = git_walker.walk_log(repo_path=repo_path)
            return render.render_page(
                repo_path=repo_path,
                snapshot=snap,
                events=events,
                now=datetime.now(tz=UTC),
            )

        def _respond(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler, reset_timer
```

- [ ] **Step 4: Run all server tests**

```bash
uv run pytest tests/skills/test_ralph_report.py::test_server_serves_html_and_idles_out -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/ralph-report/scripts/server.py tests/skills/test_ralph_report.py
git commit -m "feat(ralph-report): server.py HTTPServer + idle auto-exit"
```

---

## Task 5: report.py — entry point

**Files:**
- Create: `skills/ralph-report/scripts/report.py`

- [ ] **Step 1: Write `report.py`**

Create `skills/ralph-report/scripts/report.py`:

```python
"""Entry point for the ralph-report skill.

Parses CLI args, starts the HTTP server, writes ``.ralph-work/report/server-info``,
and blocks until the server's idle timer fires or the user sends SIGINT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Make ``skills.ralph_report.scripts`` importable when invoked directly.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills.ralph_report.scripts.server import start_server  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ralph-report — local HTML dashboard for ralph-queue activity.")
    parser.add_argument("--repo", required=True, type=Path,
                        help="Path to the ralph repo checkout.")
    parser.add_argument("--port", type=int, default=0,
                        help="Bind port (0 = OS picks). Default: 0.")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="Bind host. Default: 127.0.0.1.")
    parser.add_argument("--idle-seconds", type=int, default=1800,
                        help="Auto-exit after N seconds of no requests. Default: 1800.")
    args = parser.parse_args(argv)

    if not args.repo.is_dir():
        print(f"ralph-report: --repo {args.repo} is not a directory", file=sys.stderr)
        return 2
    if not (args.repo / ".git").exists():
        print(f"ralph-report: --repo {args.repo} is not a git repo", file=sys.stderr)
        return 2

    handle = start_server(
        repo_path=args.repo,
        port=args.port,
        idle_seconds=args.idle_seconds,
        bind_host=args.bind,
    )
    info_dir = args.repo / ".ralph-work" / "report"
    info_dir.mkdir(parents=True, exist_ok=True)
    info_path = info_dir / "server-info"
    info_path.write_text(
        json.dumps(
            {
                "url": f"http://{args.bind}:{handle.port}",
                "host": args.bind,
                "port": handle.port,
                "pid": os.getpid(),
                "started_at": datetime.now(tz=UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"ralph-report listening on http://{args.bind}:{handle.port}")
    print(f"server-info: {info_path}")
    try:
        handle.thread.join()
    except KeyboardInterrupt:
        handle.shutdown()
        handle.thread.join(timeout=5)
    # Mark the server as stopped so stop-server.sh / clients can tell.
    (info_dir / "server-stopped").write_text(
        datetime.now(tz=UTC).isoformat(), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke-test the entry point**

```bash
uv run python -c "from skills.ralph_report.scripts import report; print(report.main.__doc__)"
```
Expected: prints `None` (no error). The import path is valid.

- [ ] **Step 3: Commit**

```bash
git add skills/ralph-report/scripts/report.py
git commit -m "feat(ralph-report): report.py entry point + server-info contract"
```

---

## Task 6: start-server.sh / stop-server.sh

**Files:**
- Create: `skills/ralph-report/scripts/start-server.sh`
- Create: `skills/ralph-report/scripts/stop-server.sh`

**Confidence: 85%** — POSIX bash; Windows uses Git Bash. Avoid bashisms that break on Git Bash for Windows.

- [ ] **Step 1: Create `start-server.sh`**

Create `skills/ralph-report/scripts/start-server.sh`:

```bash
#!/usr/bin/env bash
# Background launcher for ralph-report.
#
# Usage:
#   start-server.sh --repo /path/to/ralph
#
# Writes connection info to <repo>/.ralph-work/report/server-info,
# then exits. The server keeps running until idle for 30 minutes
# OR until stop-server.sh is invoked.

set -euo pipefail

REPO=""
PORT=0
BIND="127.0.0.1"
IDLE=1800

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --bind) BIND="$2"; shift 2;;
    --idle-seconds) IDLE="$2"; shift 2;;
    *) echo "unknown flag: $1" >&2; exit 2;;
  esac
done

if [[ -z "$REPO" ]]; then
  echo "start-server.sh: --repo <path> is required" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPORT_DIR="$REPO/.ralph-work/report"
mkdir -p "$REPORT_DIR"
LOG_FILE="$REPORT_DIR/server.log"
rm -f "$REPORT_DIR/server-stopped"

nohup uv run python "$SCRIPT_DIR/report.py" \
  --repo "$REPO" \
  --port "$PORT" \
  --bind "$BIND" \
  --idle-seconds "$IDLE" \
  > "$LOG_FILE" 2>&1 &

# Wait briefly for server-info to be written by the child.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if [[ -f "$REPORT_DIR/server-info" ]]; then
    break
  fi
  sleep 0.3
done

if [[ ! -f "$REPORT_DIR/server-info" ]]; then
  echo "start-server.sh: server failed to start; see $LOG_FILE" >&2
  exit 1
fi

cat "$REPORT_DIR/server-info"
```

- [ ] **Step 2: Create `stop-server.sh`**

Create `skills/ralph-report/scripts/stop-server.sh`:

```bash
#!/usr/bin/env bash
# Stop the running ralph-report server identified by .ralph-work/report/server-info.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: stop-server.sh <repo-path>" >&2
  exit 2
fi

REPO="$1"
INFO="$REPO/.ralph-work/report/server-info"

if [[ ! -f "$INFO" ]]; then
  echo "stop-server.sh: $INFO not found; server may already be stopped" >&2
  exit 0
fi

PID="$(python -c "import json, sys; print(json.load(open(sys.argv[1]))['pid'])" "$INFO")"

if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "stop-server.sh: sent SIGTERM to pid $PID"
else
  echo "stop-server.sh: pid $PID not running"
fi
```

- [ ] **Step 3: Mark executable**

```bash
chmod +x skills/ralph-report/scripts/start-server.sh skills/ralph-report/scripts/stop-server.sh
```

- [ ] **Step 4: Commit**

```bash
git add skills/ralph-report/scripts/start-server.sh skills/ralph-report/scripts/stop-server.sh
git commit -m "feat(ralph-report): bash launcher + stop scripts"
```

---

## Task 7: Integration test — start, fetch, assert, stop

**Files:**
- Modify: `tests/skills/test_ralph_report.py`

**Confidence: 88%** — reuses the fixture pattern from Tasks 1 and 2.

- [ ] **Step 1: Write the integration test**

Append:

```python
def test_end_to_end_renders_all_panels(tmp_path: Path) -> None:
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
        body = urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/").read().decode("utf-8")
    finally:
        handle.shutdown()
        handle.thread.join(timeout=5)

    for marker in ("Current", "Blocked", "Inbox", "Pending PR", "Done", "Activity timeline"):
        assert marker in body
    assert "PBI-WORKING-ON" in body
    assert "PBI-QUEUED" in body
```

- [ ] **Step 2: Run the full suite**

```bash
uv run pytest tests/skills/test_ralph_report.py -v
```
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/skills/test_ralph_report.py
git commit -m "test(ralph-report): end-to-end render covers every section"
```

---

## Task 8: SKILL.md

**Files:**
- Create: `skills/ralph-report/SKILL.md`

- [ ] **Step 1: Write `SKILL.md`**

Create `skills/ralph-report/SKILL.md`:

```markdown
---
name: ralph-report
description: Local read-only HTML dashboard of ralph-queue activity in a single repo. Renders Current / Blocked / Inbox / Pending PR / Done-24h panels + a 24h activity timeline. Starts a local HTTP server on 127.0.0.1, auto-exits after 30 minutes idle. Source: filesystem reads on the queue worktree + `git log` against `ralph-queue`. Sibling of `ralph-status` (one-shot CLI table).

---

# ralph-report

## What this skill does

A long-running local HTTP server that renders the ralph-queue activity
of a single repo as an HTML dashboard. Read-only. Manual refresh (browser F5).

Five rows, top to bottom:

1. **Current** — the single PBI being worked on (1 max)
2. **Blocked** — all blocked PBIs and META-cycle sentinels
3. **Inbox + Pending PR** — side-by-side: queued PBIs and PRs in review
4. **Done (last 24h)** — PBIs whose done-move commit landed in the last 24h
5. **Activity timeline (last 24h)** — state-changes only (adds, claims, PR opens, ships, blocks, cycle-trips). Persist-iteration commits filtered out.

## When to use it

When you want a glanceable view of "what is ralph doing and what
happened recently" without re-running `ralph-status` every few minutes.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--repo <path>` | yes | Path to the ralph repo checkout. |
| `--port <int>` | no | Bind port. `0` (default) = OS picks. |
| `--bind <host>` | no | Bind host. Default: `127.0.0.1`. |
| `--idle-seconds <int>` | no | Auto-exit after N seconds of no requests. Default: 1800. |

## How it is invoked

```bash
# foreground
uv run python skills/ralph-report/scripts/report.py --repo /path/to/repo

# background (writes URL/pid to <repo>/.ralph-work/report/server-info)
bash skills/ralph-report/scripts/start-server.sh --repo /path/to/repo

# stop
bash skills/ralph-report/scripts/stop-server.sh /path/to/repo
```

## Data sources

- **Snapshot panels (Current / Inbox / Pending PR / Blocked):** filesystem reads of `.ralph/<state>/` on the queue worktree at `<repo>/.ralph-work/queue`. Falls back to `git -C <repo> show origin/ralph-queue:<path>` when the worktree is absent.
- **Done (last 24h) and Activity timeline:** `git log ralph-queue --since=24.hours.ago` parsed against the executor's deterministic commit-message grammar.

## What this skill does NOT do

- It does not write to `ralph-queue` or any other branch.
- It does not call GitHub / ADO. The queue folder is the truth surface.
- It does not aggregate PR check status.
- It does not auto-refresh — refresh the browser with F5.
- It does not aggregate across multiple repos.
```

- [ ] **Step 2: Verify the spec coverage**

Re-read `docs/superpowers/specs/2026-05-28-ralph-report-design.md`. For every non-goal and every requirement in the spec, confirm that:

- The non-goal is reflected in `SKILL.md`'s "What this skill does NOT do" or is explicitly out-of-scope.
- The requirement maps to a task in this plan.

Fix any gaps inline before committing.

- [ ] **Step 3: Commit**

```bash
git add skills/ralph-report/SKILL.md
git commit -m "docs(ralph-report): add SKILL.md"
```

---

## Self-Review

After all tasks are complete, run:

```bash
uv run ruff check skills/ralph-report tests/skills/test_ralph_report.py
uv run ruff format --check skills/ralph-report tests/skills/test_ralph_report.py
uv run mypy skills/ralph-report tests/skills/test_ralph_report.py
uv run pytest tests/skills/test_ralph_report.py -v
```

All four must be clean before opening the PR.

PR should be opened from `ralph/RALPH-REPORT-SKILL` against `main` (or whatever feature branch the executor scopes this work to).
