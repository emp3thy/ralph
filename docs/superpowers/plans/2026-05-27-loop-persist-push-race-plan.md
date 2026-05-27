# Loop persist push race — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace bare `git push` in queue-mutation paths with `push_with_rebase`, a fetch-rebase-push helper. Make `iterate_once` treat the rebase-conflict case as a recoverable warning instead of a fatal crash. Stop ralph from dying when a concurrent writer (operator, second instance, web commit) advances `origin/ralph-queue` mid-iteration.

**Architecture:** New `push_with_rebase(repo, remote, branch)` in `git_ops.py`, new `PushRebaseConflict` exception. Swap the five push call sites (`move_inbox_to_current`, `move_current_to_pending_pr`, `move_current_to_blocked`, `_persist_iteration_writes`, sweep runner). `iterate_once` catches `PushRebaseConflict` → returns `LoopResult(outcome="push_conflict")`. One-retry on a second-attempt race; crash if that fails too.

**Tech Stack:** Python 3.12, `subprocess.run` via existing `_run_git`. pytest with two-repo git fixtures.

**Spec:** `docs/superpowers/specs/2026-05-27-loop-persist-push-race-design.md`

---

## Confidence per task

All ≥ 90%. Pre-flight: helper sequence verified against `git push --help` / `git rebase --help`; call sites confirmed via grep at `git_ops.push(`.

| Task | % | Notes |
|---|---|---|
| 1. New helper + exception + failing unit tests | 94% | `_run_git` pattern already exposes returncode + stderr. Two-repo fixture is a 10-line helper. |
| 2. Wire `push_with_rebase` into the 5 call sites | 92% | Mechanical replacement of `git_ops.push(queue_repo, cfg.queue_branch)` → `git_ops.push_with_rebase(queue_repo, remote="origin", branch=cfg.queue_branch)`. Risk: 5% for any call site that's slightly different (e.g. sweep's push may use a different remote name; verified to be "origin" in the existing code). |
| 3. `iterate_once` catches `PushRebaseConflict` | 93% | Add an `outcome="push_conflict"` discriminator to `LoopResult`. Existing tests assert on `outcome` strings already; adding a new value is additive. |
| 4. Reproduction recipe + smoke test | 91% | Manual two-terminal repro is the strongest evidence. The smoke test is the same shape as the unit test in Task 1 but exercises the full loop path. |

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `ralph_executor/git_ops.py` | Modify | Add `push_with_rebase` + `PushRebaseConflict` (next to existing `push` / `GitCommandError`). |
| `ralph_executor/queue/movements.py` | Modify | `_move` calls `push_with_rebase` instead of `push`. |
| `ralph_executor/loop.py` | Modify | `_persist_iteration_writes` uses `push_with_rebase`. `iterate_once` catches `PushRebaseConflict` → `LoopResult(outcome="push_conflict")`. |
| `ralph_executor/sweep/runner.py` | Modify | Sweep's per-iteration push uses `push_with_rebase`. |
| `ralph_executor/types.py` (or wherever `LoopResult` lives) | Modify | Extend `outcome` union with `"push_conflict"`. |
| `tests/executor/test_git_ops_push_rebase.py` | Create | Two-repo fixture; cover the 6 scenarios in the spec. |
| `tests/executor/test_movements.py` | Modify | Add: each move helper survives a one-time push race. |
| `tests/executor/test_loop_iteration.py` | Modify | Add: `PushRebaseConflict` from persist path → `outcome="push_conflict"`, loop continues. |

---

## Task 1: New helper + exception + failing unit tests

**Files:**
- Modify: `ralph_executor/git_ops.py`
- Create: `tests/executor/test_git_ops_push_rebase.py`

- [ ] **Step 1.1: Failing test file with two-repo fixture**

```python
"""Tests for git_ops.push_with_rebase."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph_executor.git_ops import (
    GitCommandError,
    PushRebaseConflict,
    push_with_rebase,
)


@pytest.fixture
def two_repos(tmp_path: Path) -> tuple[Path, Path]:
    """Return (local, remote_bare) — both initialised, remote tracked by local."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    local = tmp_path / "local"
    subprocess.run(["git", "clone", str(remote), str(local)], check=True)
    subprocess.run(["git", "-C", str(local), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(local), "config", "user.name", "t"], check=True)
    # Seed with one shared commit on the default branch.
    (local / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(local), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(local), "commit", "-m", "seed"], check=True)
    subprocess.run(["git", "-C", str(local), "push", "origin", "HEAD:main"], check=True)
    subprocess.run(["git", "-C", str(local), "branch", "--set-upstream-to=origin/main"], check=True)
    return local, remote


def _add_commit(repo: Path, path: str, content: str, msg: str) -> None:
    (repo / path).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", path], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", msg], check=True)


def test_push_with_rebase_no_remote_advance(two_repos: tuple[Path, Path]) -> None:
    local, _ = two_repos
    _add_commit(local, "a.txt", "a", "add a")
    push_with_rebase(local, remote="origin", branch="main")
    # Remote now has the local commit.


def test_push_with_rebase_remote_advanced_non_conflicting(
    two_repos: tuple[Path, Path], tmp_path: Path
) -> None:
    local, remote = two_repos
    # Advance the remote independently via a second clone.
    second = tmp_path / "second"
    subprocess.run(["git", "clone", str(remote), str(second)], check=True)
    subprocess.run(["git", "-C", str(second), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(second), "config", "user.name", "t"], check=True)
    _add_commit(second, "remote.txt", "remote", "remote commit")
    subprocess.run(["git", "-C", str(second), "push", "origin", "main"], check=True)
    # Now local makes its own commit on a non-overlapping path.
    _add_commit(local, "local.txt", "local", "local commit")
    push_with_rebase(local, remote="origin", branch="main")
    # Both commits should be on remote main now.


def test_push_with_rebase_conflict_raises(
    two_repos: tuple[Path, Path], tmp_path: Path
) -> None:
    local, remote = two_repos
    second = tmp_path / "second"
    subprocess.run(["git", "clone", str(remote), str(second)], check=True)
    subprocess.run(["git", "-C", str(second), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(second), "config", "user.name", "t"], check=True)
    _add_commit(second, "conflict.txt", "from-remote", "remote conflict")
    subprocess.run(["git", "-C", str(second), "push", "origin", "main"], check=True)
    _add_commit(local, "conflict.txt", "from-local", "local conflict")
    with pytest.raises(PushRebaseConflict) as exc:
        push_with_rebase(local, remote="origin", branch="main")
    assert "conflict.txt" in str(exc.value)


def test_push_with_rebase_network_failure(tmp_path: Path) -> None:
    local = tmp_path / "lonely"
    subprocess.run(["git", "init", str(local)], check=True)
    subprocess.run(["git", "-C", str(local), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(local), "config", "user.name", "t"], check=True)
    (local / "x").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(local), "add", "x"], check=True)
    subprocess.run(["git", "-C", str(local), "commit", "-m", "x"], check=True)
    subprocess.run(["git", "-C", str(local), "remote", "add", "origin", "/nonexistent/path"], check=True)
    with pytest.raises(GitCommandError):
        push_with_rebase(local, remote="origin", branch="main")
```

- [ ] **Step 1.2: Run the failing tests**

```
uv run pytest tests/executor/test_git_ops_push_rebase.py -v
```

Expected: ImportError on `push_with_rebase` / `PushRebaseConflict`.

- [ ] **Step 1.3: Implement helper + exception in `git_ops.py`**

```python
class PushRebaseConflict(RuntimeError):
    """Raised when push_with_rebase aborted a rebase due to conflict markers.

    ``conflict_paths`` lists the files git reported as conflicted before
    abort. Callers (iterate_once) use this for log payload; they do NOT
    auto-resolve.
    """

    def __init__(self, conflict_paths: tuple[str, ...]) -> None:
        super().__init__(f"rebase conflict on: {', '.join(conflict_paths) or '<unknown>'}")
        self.conflict_paths = conflict_paths


def push_with_rebase(
    repo: Path,
    *,
    remote: str,
    branch: str,
) -> None:
    """Fetch ``remote/branch``, rebase HEAD onto it if needed, push.

    Raises PushRebaseConflict if rebase aborts with conflicts;
    GitCommandError on network/auth/other git failures.
    """
    _run_git(repo, "fetch", remote, branch)
    counts = _run_git(
        repo,
        "rev-list",
        "--count",
        "--left-right",
        f"HEAD...{remote}/{branch}",
    )
    ahead_str, behind_str = counts.strip().split()
    behind = int(behind_str)
    if behind > 0:
        try:
            _run_git(repo, "rebase", f"{remote}/{branch}")
        except GitCommandError as err:
            try:
                conflict_paths = tuple(
                    _run_git(repo, "diff", "--name-only", "--diff-filter=U").splitlines()
                )
            finally:
                _run_git(repo, "rebase", "--abort")
            raise PushRebaseConflict(conflict_paths) from err
    try:
        _run_git(repo, "push", remote, branch)
    except GitCommandError as err:
        # One retry — the rebase window may have been raced again.
        _run_git(repo, "fetch", remote, branch)
        _run_git(repo, "rebase", f"{remote}/{branch}")
        _run_git(repo, "push", remote, branch)
```

(`_run_git` returns stdout as a string; if its existing signature differs, adapt the helper to that signature.)

- [ ] **Step 1.4: Run the tests, expect green**

```
uv run pytest tests/executor/test_git_ops_push_rebase.py -v
```

- [ ] **Step 1.5: ruff + mypy + commit**

```
uv run ruff check ralph_executor/git_ops.py tests/executor/test_git_ops_push_rebase.py
uv run mypy --strict ralph_executor/git_ops.py
```

Commit:

```
git add ralph_executor/git_ops.py tests/executor/test_git_ops_push_rebase.py
git commit -m "feat(git_ops): push_with_rebase + PushRebaseConflict"
```

---

## Task 2: Wire `push_with_rebase` into 5 call sites

**Files:**
- Modify: `ralph_executor/queue/movements.py` (`_move`)
- Modify: `ralph_executor/loop.py` (`_persist_iteration_writes`)
- Modify: `ralph_executor/sweep/runner.py` (sweep persistence)
- Modify: `tests/executor/test_movements.py`

- [ ] **Step 2.1: Grep call sites**

```
grep -n "git_ops.push(" ralph_executor/
```

Expected: 5 lines (movements.py, loop.py, sweep/runner.py).

- [ ] **Step 2.2: Replace each `git_ops.push(queue_repo, cfg.queue_branch)` with `git_ops.push_with_rebase(queue_repo, remote="origin", branch=cfg.queue_branch)`**

The remote arg comes from the existing call's first arg's hardcoded `"origin"` (verified by grep). If any call passes a different remote, preserve it.

- [ ] **Step 2.3: Add race-survival tests in `test_movements.py`**

For each `move_*` helper, simulate one race by monkeypatching `git_ops._run_git` to return a "remote advance" on first fetch, then succeed on the subsequent push.

- [ ] **Step 2.4: Run touched tests + ruff + commit**

```
uv run pytest tests/executor/test_movements.py tests/executor/sweep/test_runner.py -v
uv run ruff check ralph_executor/queue/movements.py ralph_executor/loop.py ralph_executor/sweep/runner.py
git add ralph_executor/queue/movements.py ralph_executor/loop.py ralph_executor/sweep/runner.py tests/executor/test_movements.py
git commit -m "feat(queue+loop+sweep): use push_with_rebase to survive concurrent writers"
```

---

## Task 3: `iterate_once` catches `PushRebaseConflict`

**Files:**
- Modify: `ralph_executor/loop.py` (`iterate_once`)
- Modify: `ralph_executor/types.py` if `LoopResult.outcome` is a Literal there
- Modify: `tests/executor/test_loop_iteration.py`

- [ ] **Step 3.1: Locate `LoopResult.outcome` type**

```
grep -n "outcome" ralph_executor/types.py ralph_executor/loop.py | grep -i "literal\|outcome:"
```

Add `"push_conflict"` to the Literal union.

- [ ] **Step 3.2: Wrap the persist call**

In `iterate_once`, wrap the persist + post-iteration push block:

```python
try:
    _persist_iteration_writes(...)
except PushRebaseConflict as err:
    log.warning(
        "iterate_once: push conflict on %s (paths: %s); skipping this iteration's persist, "
        "loop will retry next round",
        cfg.queue_branch,
        ", ".join(err.conflict_paths),
    )
    return LoopResult(outcome="push_conflict", ...)
```

Match the existing `LoopResult` constructor's positional args.

- [ ] **Step 3.3: Failing iteration test**

In `tests/executor/test_loop_iteration.py`, add:

```python
def test_iterate_once_recovers_from_push_conflict(monkeypatch, ...):
    """`iterate_once` returns outcome='push_conflict' when persist push races."""
    # Arrange a normal iteration, then monkeypatch _persist_iteration_writes
    # to raise PushRebaseConflict(("HISTORY.md",)).
    monkeypatch.setattr(
        "ralph_executor.loop._persist_iteration_writes",
        lambda *a, **kw: (_ for _ in ()).throw(PushRebaseConflict(("HISTORY.md",))),
    )
    result = iterate_once(...)
    assert result.outcome == "push_conflict"
```

- [ ] **Step 3.4: Run + commit**

```
uv run pytest tests/executor/test_loop_iteration.py -v
uv run ruff check ralph_executor/loop.py ralph_executor/types.py tests/executor/test_loop_iteration.py
git add ralph_executor/loop.py ralph_executor/types.py tests/executor/test_loop_iteration.py
git commit -m "feat(loop): iterate_once recovers from push conflicts as warning"
```

---

## Task 4: Reproduction recipe + smoke test

**Files:**
- Create: `docs/superpowers/runbooks/2026-05-27-push-race-repro.md` (short manual recipe)

- [ ] **Step 4.1: Write the manual repro**

```markdown
# Manual repro: ralph loop survives concurrent writers

1. Start ralph loop on a real PBI:
   `uv run python -m ralph_executor`
2. While the loop is mid-iteration, in a second terminal:
   `git -C .ralph-work/queue commit --allow-empty -m "race"`
   `git -C .ralph-work/queue push origin ralph-queue`
3. Watch the loop log. Expected: one `WARNING iterate_once: push conflict on ralph-queue (paths: ...)` line; loop continues.
4. Next iteration completes normally; the local persist commit lands on top of the racing commit.
```

- [ ] **Step 4.2: Final commit**

```
git add docs/superpowers/runbooks/2026-05-27-push-race-repro.md
git commit -m "docs(runbook): manual repro for push-race recovery"
```

- [ ] **Step 4.3: Full suite + PR**

```
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy ralph_executor scripts skills tests
```

Open the PR from `ralph/LOOP-PERSIST-PUSH-RACE` against `main`. Bot-watch loop per the standing instruction.

---

## Acceptance Criteria

- `git_ops.push_with_rebase` exists; `PushRebaseConflict` exists.
- All 5 queue-push call sites use `push_with_rebase`.
- `iterate_once` returns `LoopResult(outcome="push_conflict")` on race; loop does not crash.
- pytest / ruff / mypy strict all green.
- Manual repro recipe in `docs/superpowers/runbooks/`.
- Reproduction: in a fresh ralph run, a concurrent push to `origin/ralph-queue` during an iteration produces a warning, not a crash.

---

## depends_on

None.
