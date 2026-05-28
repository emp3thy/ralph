# EXECUTOR-QUEUE-REPO-SPLIT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the executor's queue-as-a-branch model with a queue-as-its-own-repo model. The executor clones `queue_repo` into `$RALPH_WORKSPACE/queue/`, reads/writes `.ralph/` there, and pushes back to the queue repo's `main`. A one-shot `migrate-queue` subcommand bootstraps the new queue repo from the current state. The `queue_branch` config knob is removed.

**Architecture:** Mirrors the `target_clone.py` pattern already used for multi-repo target clones — a small `queue_clone.py` module that idempotently clones-on-first-call and fetch-pulls-on-subsequent-call. The loop driver swaps `_pull_queue` to call it; all `git push` sites that referenced `cfg.queue_branch` now push to `main`. No backward compatibility — single operator, no rollback path needed.

**Tech Stack:** Python 3.12, `subprocess` for git, pytest for tests, `tomllib` for TOML parsing. Existing dependencies — no new third-party libraries.

**Spec reference:** `docs/superpowers/specs/2026-05-28-queue-repo-split-design.md`

---

## File map

**Create:**
- `ralph_executor/queue_clone.py` — `ensure_queue_clone(workspace_root, queue_repo) -> Path`
- `ralph_executor/migrate_queue.py` — the `migrate-queue` subcommand implementation
- `tests/executor/test_queue_clone.py`
- `tests/executor/test_migrate_queue.py`

**Modify:**
- `ralph_executor/config.py` — add `queue_repo`; remove `queue_branch`; remove `DEFAULT_QUEUE_BRANCH`; remove `queue_branch` from `_TOML_KNOWN_KEYS`; add `queue_repo` to known keys; validation in `load_config`.
- `ralph_executor/loop.py` — `_queue_repo_root`, `_pull_queue`, `_persist_iteration_writes`, `_claim_pbi*`, `_run_ralph` (every push site that referenced `cfg.queue_branch`).
- `ralph_executor/queue/movements.py` — `_move` passes `"main"` to `git_ops.push`, not `cfg.queue_branch`.
- `ralph_executor/cli.py` — add `migrate-queue` subcommand parser + dispatch; remove `--queue-branch` CLI flag; add `--queue-repo` CLI flag.
- `ralph_executor/setup_cmds.py` — `init` prompts for `queue_repo` after `workspace_root`; smoke-clone validation.
- `tests/executor/conftest.py` — fixtures that mocked `queue_branch` now mock `queue_repo`.
- `tests/executor/test_config.py`, `tests/executor/test_config_toml.py`, `tests/executor/test_cli.py`, `tests/executor/test_setup_cmds.py`, `tests/test_setup_ralph_queue_github.py`, `tests/safety/test_integration_loop.py` — every test that constructs an `ExecutorConfig` with `queue_branch="ralph-queue"`.

**Delete (cleanup):**
- Any `_ensure_on_queue_branch` references in `loop.py` (no callers after the refactor).
- `DEFAULT_QUEUE_BRANCH` constant in `config.py`.

---

## Task 0: Spike — enumerate every `queue_branch` reference

**Confidence: 95%** — mechanical grep.

**Why:** Get an exact list before editing so no call site is missed. The grep from plan-write time showed 25 files; production code and tests are mixed. Each non-doc file needs a deliberate handling decision.

**Files:**
- (read-only)

- [ ] **Step 1: Run grep**

```bash
uv run python -c "import subprocess; subprocess.run(['rg', '-l', 'queue_branch'], check=True)"
```

Or simpler:

```bash
grep -rl "queue_branch\|cfg\.queue_branch" ralph_executor scripts skills tests | sort
```

- [ ] **Step 2: Categorise each hit**

Write the list to scratch (a paste in the implementation PR is fine). Categorise each file as one of:
- **CONFIG**: defines the field or default → remove (Task 1).
- **LOOP**: production code that uses `cfg.queue_branch` for push/pull → swap to `"main"` or `cfg.queue_repo` (Tasks 3–6).
- **CLI**: defines a `--queue-branch` flag → remove (Task 7).
- **TEST**: constructs `ExecutorConfig(queue_branch=...)` → swap to `queue_repo=...` (Task 9).
- **DOC**: spec / plan / README mentioning the old model → out of scope of this PBI (PBI 2 handles docs).

- [ ] **Step 3: Sanity check — no other surfaces**

Confirm no `RALPH_QUEUE_BRANCH` env reads remain in non-doc code:

```bash
grep -rl "RALPH_QUEUE_BRANCH" ralph_executor scripts skills tests
```

Any hit is a production-code grep target for Task 1.

No commit for Task 0 — it's a discovery step.

---

## Task 1: ExecutorConfig — add `queue_repo`, remove `queue_branch`

**Confidence: 95%** — mechanical dataclass edit; existing config_toml machinery handles the new key the same way.

**Files:**
- Modify: `ralph_executor/config.py`
- Test: `tests/executor/test_config.py`, `tests/executor/test_config_toml.py`

- [ ] **Step 1: Write the failing test (config.py field present, queue_branch absent)**

Append to `tests/executor/test_config.py`:

```python
def test_executor_config_has_queue_repo_field():
    """Sanity: queue_repo field exists on the dataclass."""
    from dataclasses import fields
    from ralph_executor.config import ExecutorConfig

    names = {f.name for f in fields(ExecutorConfig)}
    assert "queue_repo" in names
    assert "queue_branch" not in names


def test_load_config_rejects_missing_queue_repo(tmp_path):
    """queue_repo is required. Missing → ConfigError."""
    from ralph_executor.config import ConfigError, load_config

    (tmp_path / ".ralph" / "config.toml").parent.mkdir(parents=True)
    (tmp_path / ".ralph" / "config.toml").write_text("main_branch = 'main'\n", encoding="utf-8")

    try:
        load_config(tmp_path)
    except ConfigError as exc:
        assert "queue_repo" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_load_config_accepts_queue_repo(tmp_path):
    from ralph_executor.config import load_config

    (tmp_path / ".ralph" / "config.toml").parent.mkdir(parents=True)
    (tmp_path / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/emp3thy/ralph-queue"\n',
        encoding="utf-8",
    )

    cfg = load_config(tmp_path)
    assert cfg.queue_repo == "https://github.com/emp3thy/ralph-queue"
```

- [ ] **Step 2: Run the tests; verify they FAIL**

```bash
uv run pytest tests/executor/test_config.py::test_executor_config_has_queue_repo_field tests/executor/test_config.py::test_load_config_rejects_missing_queue_repo tests/executor/test_config.py::test_load_config_accepts_queue_repo -v
```

Expected: 3 failures (AttributeError or similar — the field doesn't exist yet).

- [ ] **Step 3: Edit `ralph_executor/config.py`**

In the constants block near line 40:
- Delete: `DEFAULT_QUEUE_BRANCH = "ralph-queue"`

In `_TOML_KNOWN_KEYS` (line 76):
- Remove `"queue_branch",`
- Add `"queue_repo",`

In the `ExecutorConfig` dataclass (line 121):
- Remove the `queue_branch: str` field.
- Add a `queue_repo: str` field. Place it before `main_branch` for ordering parity with the source's-of-truth grouping (queue first, target later). The field has no default — required.

In `load_config`:
- Remove any read of `RALPH_QUEUE_BRANCH` from env and any TOML-key handler for `queue_branch`.
- Add a `queue_repo` resolver: read `queue_repo` from TOML; if absent, raise `ConfigError("queue_repo not configured. Add 'queue_repo = \"<url>\"' to your config.toml or pass --queue-repo.")`.
- Validate format with `parse_target_repo` from `ralph_executor.url_utils` — same validator multi-repo PBI introduced. Bad value → `ConfigError(f"queue_repo {value!r} is not a valid HTTPS URL: {exc}")`.

- [ ] **Step 4: Run the tests; verify they PASS**

```bash
uv run pytest tests/executor/test_config.py::test_executor_config_has_queue_repo_field tests/executor/test_config.py::test_load_config_rejects_missing_queue_repo tests/executor/test_config.py::test_load_config_accepts_queue_repo -v
```

Expected: 3 passes.

- [ ] **Step 5: Update existing config tests that pass `queue_branch=`**

Run:

```bash
grep -rln "queue_branch" tests/
```

For each file, swap `queue_branch="ralph-queue"` → `queue_repo="https://github.com/example/queue"` in every `ExecutorConfig(...)` call. The URL doesn't have to be real for unit tests; use the example domain consistently.

- [ ] **Step 6: Full config-test run**

```bash
uv run pytest tests/executor/test_config.py tests/executor/test_config_toml.py -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add ralph_executor/config.py tests/executor/test_config.py tests/executor/test_config_toml.py
git commit -m "config(executor): replace queue_branch with queue_repo"
```

---

## Task 2: `queue_clone.py` — clone-on-first, pull-on-subsequent

**Confidence: 90%** — mirrors `target_clone.py`. The `main` (not `ralph-queue`) default branch is the only conceptual delta.

**Mitigation:** Before writing, read `ralph_executor/target_clone.py` end-to-end so the new module follows the same error-handling, exception-type, and timeout conventions.

**Files:**
- Create: `ralph_executor/queue_clone.py`
- Test: `tests/executor/test_queue_clone.py`

- [ ] **Step 1: Read the existing pattern**

```bash
uv run python -c "import pathlib; print(pathlib.Path('ralph_executor/target_clone.py').read_text())"
```

Note: function signature, exception class, error message style, `git fetch && git pull` flag usage. Copy the structural shape.

- [ ] **Step 2: Write the failing tests**

`tests/executor/test_queue_clone.py`:

```python
"""Tests for queue_clone.ensure_queue_clone using local bare git repos."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph_executor.queue_clone import QueueCloneError, ensure_queue_clone


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_bare_remote(tmp_path: Path) -> Path:
    """Build a bare repo with an initial commit on main, return path as URL."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "--initial-branch=main")

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    (seed / "README.md").write_text("queue\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "origin", "main")
    return bare


def test_first_call_clones(tmp_path: Path) -> None:
    remote = _make_bare_remote(tmp_path)
    workspace = tmp_path / "ws"

    path = ensure_queue_clone(workspace, f"file://{remote}")

    assert path == workspace / "queue"
    assert (path / ".git").exists()
    assert (path / "README.md").read_text(encoding="utf-8") == "queue\n"


def test_second_call_fetches_and_pulls(tmp_path: Path) -> None:
    remote = _make_bare_remote(tmp_path)
    workspace = tmp_path / "ws"

    # First call clones.
    path = ensure_queue_clone(workspace, f"file://{remote}")

    # Push a new commit to remote from a third checkout.
    push_src = tmp_path / "push_src"
    _git(tmp_path, "clone", str(remote), str(push_src))
    (push_src / "new.md").write_text("new\n", encoding="utf-8")
    _git(push_src, "add", ".")
    _git(push_src, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "second")
    _git(push_src, "push", "origin", "main")

    # Second call: must fetch and ff-pull.
    ensure_queue_clone(workspace, f"file://{remote}")
    assert (path / "new.md").exists()


def test_bad_url_raises_queue_clone_error(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    with pytest.raises(QueueCloneError) as exc:
        ensure_queue_clone(workspace, "file:///definitely/not/a/repo")
    assert "queue" in str(exc.value).lower()
```

- [ ] **Step 3: Run; expect FAIL (module doesn't exist)**

```bash
uv run pytest tests/executor/test_queue_clone.py -v
```

Expected: `ModuleNotFoundError: No module named 'ralph_executor.queue_clone'`.

- [ ] **Step 4: Implement `ralph_executor/queue_clone.py`**

```python
"""Idempotent clone of the queue repo into the workspace.

Mirrors ``target_clone.ensure_clone`` — clone on first call, fetch +
ff-only pull on subsequent calls. The queue repo's default branch is
``main`` (not ``ralph-queue``); branch-swapping is irrelevant because
the queue clone never leaves ``main``.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class QueueCloneError(RuntimeError):
    """Raised when the queue clone cannot be created or refreshed."""


def _run_git(repo: Path | None, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    argv = ["git", *(["-C", str(repo)] if repo is not None else []), *args]
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def ensure_queue_clone(workspace_root: Path, queue_repo: str, *, timeout: float = 120.0) -> Path:
    """Ensure ``<workspace_root>/queue`` is a clone of ``queue_repo`` on ``main``.

    On first call: ``git clone <queue_repo> <workspace_root>/queue``.
    On subsequent calls: ``git fetch origin`` then ``git pull --ff-only origin main``.

    Returns the path to the clone. Raises ``QueueCloneError`` with a message
    pointing at ``gh auth login`` on auth-related failures.
    """
    workspace_root.mkdir(parents=True, exist_ok=True)
    dest = workspace_root / "queue"

    if not (dest / ".git").exists():
        log.info("cloning queue %s -> %s", queue_repo, dest)
        result = _run_git(None, "clone", queue_repo, str(dest), timeout=timeout)
        if result.returncode != 0:
            raise QueueCloneError(
                f"git clone of queue repo {queue_repo!r} failed (exit {result.returncode}): "
                f"{result.stderr.strip()}\n"
                f"If this is an auth problem, run `gh auth login`."
            )
        return dest

    log.info("refreshing queue clone at %s", dest)
    fetch = _run_git(dest, "fetch", "origin", timeout=timeout)
    if fetch.returncode != 0:
        raise QueueCloneError(
            f"git fetch in queue clone {dest} failed (exit {fetch.returncode}): "
            f"{fetch.stderr.strip()}"
        )
    pull = _run_git(dest, "pull", "--ff-only", "origin", "main", timeout=timeout)
    if pull.returncode != 0:
        raise QueueCloneError(
            f"git pull --ff-only in queue clone {dest} failed (exit {pull.returncode}): "
            f"{pull.stderr.strip()}\n"
            f"If main was force-pushed remotely, resolve manually."
        )
    return dest
```

- [ ] **Step 5: Run; expect PASS**

```bash
uv run pytest tests/executor/test_queue_clone.py -v
```

Expected: 3 passes.

- [ ] **Step 6: Commit**

```bash
git add ralph_executor/queue_clone.py tests/executor/test_queue_clone.py
git commit -m "feat(executor): ensure_queue_clone — idempotent queue repo clone"
```

---

## Task 3: `loop._queue_repo_root` and `_pull_queue` refactor

**Confidence: 95%** — both helpers are small, single-purpose.

**Files:**
- Modify: `ralph_executor/loop.py`
- Test: `tests/executor/test_loop.py` (existing) — assert `_pull_queue` calls `ensure_queue_clone`.

- [ ] **Step 1: Add a failing test**

Append to `tests/executor/test_loop.py` (or create the file if absent):

```python
def test_pull_queue_calls_ensure_queue_clone(monkeypatch, tmp_path):
    """_pull_queue must delegate to queue_clone.ensure_queue_clone."""
    from ralph_executor import loop
    from ralph_executor.config import ExecutorConfig

    calls: list[tuple[Path, str]] = []

    def fake_ensure(workspace_root, queue_repo, *, timeout=120.0):
        calls.append((workspace_root, queue_repo))
        return workspace_root / "queue"

    monkeypatch.setattr(loop, "ensure_queue_clone", fake_ensure)

    cfg = _make_test_cfg(  # existing test helper; if not present, build a minimal ExecutorConfig
        workspace_root=tmp_path,
        queue_repo="https://github.com/example/q",
    )
    loop._pull_queue(cfg)

    assert calls == [(tmp_path, "https://github.com/example/q")]
```

If `_make_test_cfg` doesn't exist in this file, build the cfg inline using `ExecutorConfig(...)` with the new `queue_repo` arg.

- [ ] **Step 2: Run; expect FAIL**

```bash
uv run pytest tests/executor/test_loop.py::test_pull_queue_calls_ensure_queue_clone -v
```

- [ ] **Step 3: Edit `ralph_executor/loop.py`**

Add to the imports block:

```python
from ralph_executor.queue_clone import ensure_queue_clone
```

Replace `_queue_repo_root`:

```python
def _queue_repo_root(cfg: ExecutorConfig) -> Path:
    """Filesystem path of the queue clone. Always under workspace_root/queue."""
    return cfg.workspace_root / "queue"
```

Replace `_pull_queue`:

```python
def _pull_queue(cfg: ExecutorConfig) -> None:
    log.debug("refreshing queue clone for %s", cfg.queue_repo)
    ensure_queue_clone(cfg.workspace_root, cfg.queue_repo)
```

Delete `_ensure_on_queue_branch` (callers go away in Task 4).

- [ ] **Step 4: Run the test; expect PASS**

```bash
uv run pytest tests/executor/test_loop.py::test_pull_queue_calls_ensure_queue_clone -v
```

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/loop.py tests/executor/test_loop.py
git commit -m "feat(loop): _pull_queue uses ensure_queue_clone"
```

---

## Task 4: Remove `_ensure_on_queue_branch` callers + `queue_branch` push references

**Confidence: 88%** — mechanical but spans many call sites in loop.py.

**Mitigation:** Use Task 0's enumerated list. Edit each call site, then run the full test suite locally to catch any remaining reference.

**Files:**
- Modify: `ralph_executor/loop.py`

- [ ] **Step 1: Enumerate every `_ensure_on_queue_branch` and `cfg.queue_branch` reference inside loop.py**

```bash
grep -n "_ensure_on_queue_branch\|cfg\.queue_branch" ralph_executor/loop.py
```

- [ ] **Step 2: Delete every `_ensure_on_queue_branch(cfg)` line**

The queue clone is permanently on `main`; nothing to swap. Each call becomes a no-op delete.

- [ ] **Step 3: Replace every `git_ops.push(queue_repo, cfg.queue_branch)` with `git_ops.push(queue_repo, "main")`**

- [ ] **Step 4: Run the full executor + loop test suite**

```bash
uv run pytest tests/executor/ tests/safety/ -v
```

Failures are expected for tests that still construct `ExecutorConfig(queue_branch=...)`. Those get fixed in Task 9.

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/loop.py
git commit -m "fix(loop): drop queue branch swapping and push to main"
```

---

## Task 5: `_claim_pbi` updates for queue clone layout

**Confidence: 88%** — claim already worktree-aware after multi-repo PBI; needs path-resolution review.

**Mitigation:** Before editing, read the full current `_claim_pbi` and `_claim_pbi_worktree`. Confirm `pbi.path` resolves against the queue clone (`<workspace_root>/queue/.ralph/inbox/<id>/`) — the multi-repo PBI's `parse_pbi_directory` likely already does this, but verify.

**Files:**
- Modify: `ralph_executor/loop.py`

- [ ] **Step 1: Read current `_claim_pbi` family**

```bash
grep -n "^def _claim_pbi" ralph_executor/loop.py
```

Read both functions (lines `_claim_pbi` and `_claim_pbi_worktree`). Confirm:
- Source path of the PBI = queue clone's `.ralph/inbox/<id>/` (via `pbi.path`).
- Destination = queue clone's `.ralph/current/<id>/`.
- Per-PBI worktree creation under target clone — unchanged.

- [ ] **Step 2: Edit any path computation that assumed `<repo_path>/.ralph-work/queue/`**

Replace with `cfg.workspace_root / "queue"`. Use `_queue_repo_root(cfg)` where the helper already covers it.

- [ ] **Step 3: Run the claim-related tests**

```bash
uv run pytest tests/executor/ -k "claim or pbi" -v
```

Make passes (some failures are expected for fixture-related issues, which Task 9 handles).

- [ ] **Step 4: Commit**

```bash
git add ralph_executor/loop.py
git commit -m "fix(loop): _claim_pbi reads queue clone under workspace_root/queue"
```

---

## Task 6: `queue/movements.py` — push `"main"` instead of `cfg.queue_branch`

**Confidence: 95%** — single-line edit.

**Files:**
- Modify: `ralph_executor/queue/movements.py`

- [ ] **Step 1: Identify the line**

```bash
grep -n "cfg\.queue_branch\|queue_branch" ralph_executor/queue/movements.py
```

- [ ] **Step 2: Replace `cfg.queue_branch` with the literal `"main"`** at every hit.

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/executor/test_movements.py -v
```

Expected: tests pass except where fixtures still use `queue_branch=` in their `ExecutorConfig` — handled in Task 9.

- [ ] **Step 4: Commit**

```bash
git add ralph_executor/queue/movements.py
git commit -m "fix(movements): push moves to main, not queue_branch"
```

---

## Task 7: CLI — add `--queue-repo` flag + `migrate-queue` subcommand

**Confidence: 80%** — new subcommand, new file copy logic.

**Mitigation:** Implement the file-filter helper first with its own unit test (independent of the CLI wiring). Then layer the subcommand on top. Refuse the "target empty" check via `git ls-remote --heads` (no commits → empty output for `main`), avoiding a gh-API roundtrip.

**Files:**
- Create: `ralph_executor/migrate_queue.py` (the file-filter helper + the subcommand handler)
- Modify: `ralph_executor/cli.py`
- Test: `tests/executor/test_migrate_queue.py`

- [ ] **Step 1: Write the failing test for the file-filter helper**

`tests/executor/test_migrate_queue.py`:

```python
"""Tests for ralph_executor.migrate_queue."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph_executor.migrate_queue import (
    MigrateQueueError,
    copy_queue_tree_filtered,
    main as migrate_main,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _seed_source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / ".ralph" / "inbox" / "WI-1").mkdir(parents=True)
    (src / ".ralph" / "inbox" / "WI-1" / "PBI.md").write_text("---\nid: WI-1\n---\n", encoding="utf-8")
    (src / ".ralph" / "current").mkdir(parents=True)
    (src / ".ralph" / "pending-pr").mkdir(parents=True)
    (src / ".ralph" / "blocked" / "WI-9").mkdir(parents=True)
    (src / ".ralph" / "blocked" / "WI-9" / "PBI.md").write_text("---\nid: WI-9\n---\n", encoding="utf-8")
    (src / ".ralph" / "blocked" / "META-cycle-20260101T000000Z.md").write_text(
        "stale sentinel residue\n", encoding="utf-8"
    )
    (src / ".ralph" / "done" / "WI-old").mkdir(parents=True)
    (src / ".ralph" / "done" / "WI-old" / "PBI.md").write_text("---\nid: WI-old\n---\n", encoding="utf-8")
    (src / ".ralph" / "state").mkdir()
    (src / ".ralph" / "state" / "events.db").write_bytes(b"local-only")
    (src / ".ralph" / "config.toml").write_text("# example\n", encoding="utf-8")
    return src


def test_copy_filtered_excludes_done(tmp_path: Path) -> None:
    src = _seed_source(tmp_path)
    dst = tmp_path / "dst"

    counts = copy_queue_tree_filtered(src, dst)

    assert (dst / ".ralph" / "inbox" / "WI-1" / "PBI.md").exists()
    assert (dst / ".ralph" / "blocked" / "WI-9" / "PBI.md").exists()
    assert not (dst / ".ralph" / "done").exists()
    assert not (dst / ".ralph" / "blocked" / "META-cycle-20260101T000000Z.md").exists()
    assert not (dst / ".ralph" / "state" / "events.db").exists()
    assert (dst / ".ralph" / "config.toml").exists()
    assert (dst / ".gitignore").read_text(encoding="utf-8") == ".ralph/state/\n"
    assert counts == {"inbox": 1, "current": 0, "pending-pr": 0, "blocked": 1, "archive": 0}


def test_migrate_main_pushes_to_empty_target(tmp_path: Path) -> None:
    src = _seed_source(tmp_path)

    # Bare remote with main as the default branch but ZERO commits.
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "--initial-branch=main")

    rc = migrate_main(["--source", str(src), "--target", f"file://{bare}"])
    assert rc == 0

    # Clone bare remote and verify contents.
    clone = tmp_path / "verify"
    subprocess.run(["git", "clone", str(bare), str(clone)], check=True, capture_output=True)
    assert (clone / ".ralph" / "inbox" / "WI-1" / "PBI.md").exists()
    assert not (clone / ".ralph" / "done").exists()


def test_migrate_refuses_nonempty_target(tmp_path: Path) -> None:
    src = _seed_source(tmp_path)
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "--initial-branch=main")
    # Push a seed commit.
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    (seed / "x").write_text("x", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "origin", "main")

    with pytest.raises(MigrateQueueError) as exc:
        migrate_main(["--source", str(src), "--target", f"file://{bare}"])
    assert "not empty" in str(exc.value).lower()
```

- [ ] **Step 2: Run; expect FAIL (module missing)**

- [ ] **Step 3: Implement `ralph_executor/migrate_queue.py`**

```python
"""migrate-queue subcommand: bootstrap emp3thy/ralph-queue from an existing
.ralph/ tree. One-shot. Refuses a non-empty target.

Exclusions per spec section 3:
  - .ralph/done/      (whole dir)
  - .ralph/blocked/META-cycle-*.md
  - .ralph/state/     (gitignored, local-only)
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_STATE_DIRS = ("inbox", "current", "pending-pr", "blocked", "archive")


class MigrateQueueError(RuntimeError):
    """Raised on any migration failure."""


def _run_git(repo: Path | None, *args: str) -> subprocess.CompletedProcess[str]:
    argv = ["git", *(["-C", str(repo)] if repo is not None else []), *args]
    return subprocess.run(argv, capture_output=True, text=True)


def copy_queue_tree_filtered(source: Path, dest: Path) -> dict[str, int]:
    """Copy source/.ralph/ into dest/.ralph/ with the spec's exclusions.

    Returns per-state-folder PBI counts (subdir count under each state)."""
    src_root = source / ".ralph"
    if not (src_root / "inbox").is_dir():
        raise MigrateQueueError(
            f"source {source} is not a valid queue tree (missing .ralph/inbox/)"
        )
    dst_root = dest / ".ralph"
    dst_root.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {state: 0 for state in _STATE_DIRS}

    for state in _STATE_DIRS:
        src_state = src_root / state
        dst_state = dst_root / state
        dst_state.mkdir(parents=True, exist_ok=True)

        if state == "done":
            # Not reached — done is excluded entirely by not appearing in _STATE_DIRS.
            continue

        if not src_state.is_dir():
            continue

        for child in sorted(src_state.iterdir()):
            if state == "blocked" and child.is_file() and child.name.startswith("META-cycle-") and child.suffix == ".md":
                continue  # exclude stale META sentinels
            if child.is_dir():
                shutil.copytree(child, dst_state / child.name)
                counts[state] += 1
            elif child.is_file():
                shutil.copy2(child, dst_state / child.name)

    # Carry .ralph/config.toml if present.
    cfg_src = src_root / "config.toml"
    if cfg_src.is_file():
        shutil.copy2(cfg_src, dst_root / "config.toml")

    # Skeleton .gitignore for state/.
    (dest / ".gitignore").write_text(".ralph/state/\n", encoding="utf-8")

    return counts


def _target_is_empty(target_url: str) -> bool:
    """Return True if the target repo has zero refs (empty repo)."""
    result = _run_git(None, "ls-remote", "--heads", target_url)
    if result.returncode != 0:
        raise MigrateQueueError(
            f"could not list refs on target {target_url!r}: {result.stderr.strip()}"
        )
    return not result.stdout.strip()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ralph-executor migrate-queue")
    parser.add_argument("--source", required=True, type=Path,
                        help="Path to the existing .ralph/ tree's parent dir.")
    parser.add_argument("--target", required=True,
                        help="HTTPS URL of the (empty) new queue repo.")
    args = parser.parse_args(argv)

    src: Path = args.source.resolve()
    target: str = args.target

    if not (src / ".ralph" / "inbox").is_dir():
        raise MigrateQueueError(
            f"source {src} does not contain .ralph/inbox/ — wrong path?"
        )
    if not _target_is_empty(target):
        raise MigrateQueueError(
            f"target {target!r} is not empty (has existing refs); refusing to overwrite"
        )

    with tempfile.TemporaryDirectory(prefix="ralph-migrate-queue-") as stage_str:
        stage = Path(stage_str)
        counts = copy_queue_tree_filtered(src, stage)

        # git init, commit, push.
        for cmd in (
            ("init", "--initial-branch=main"),
            ("-c", "user.email=ralph@local", "-c", "user.name=ralph", "add", "."),
            ("-c", "user.email=ralph@local", "-c", "user.name=ralph",
             "commit", "-m", "chore: bootstrap ralph-queue from existing state"),
            ("remote", "add", "origin", target),
            ("push", "origin", "main"),
        ):
            result = _run_git(stage, *cmd)
            if result.returncode != 0:
                raise MigrateQueueError(
                    f"git {' '.join(cmd)} failed: {result.stderr.strip()}"
                )

    print("migrate-queue: success.\n")
    print("PBI counts pushed:")
    for state in _STATE_DIRS:
        print(f"  {state:11s} {counts[state]}")
    print()
    print("Next steps (operator runs manually):")
    print(f"  1. Add to ~/.ralph/config.toml:")
    print(f'         queue_repo = "{target}"')
    print(f"  2. Delete the old ralph-queue branch:")
    print(f'         gh api -X DELETE repos/<owner>/<repo>/git/refs/heads/ralph-queue')
    print(f"  3. Remove the stale local queue worktree if any:")
    print(f'         git worktree remove <path>/.ralph-work/queue')
    print()
    print("Cycle-detector events.db is local-only; the new queue clone starts blind.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests; expect PASS**

```bash
uv run pytest tests/executor/test_migrate_queue.py -v
```

- [ ] **Step 5: Wire `migrate-queue` into the CLI**

In `ralph_executor/cli.py`:
- Add an `argparse` subcommand `migrate-queue` whose handler imports and calls `ralph_executor.migrate_queue.main(remaining_argv)`.
- Add a `--queue-repo` top-level flag (one-shot override of TOML) handled in the same `_apply_overrides` machinery as other flags. Take care: only the top-level / default subcommand consumes `--queue-repo`; the migrate subcommand uses its own `--target`.

- [ ] **Step 6: Smoke test the CLI**

```bash
uv run ralph-executor migrate-queue --help
```

Expected: usage text mentions `--source` and `--target`.

- [ ] **Step 7: Commit**

```bash
git add ralph_executor/migrate_queue.py ralph_executor/cli.py tests/executor/test_migrate_queue.py
git commit -m "feat(cli): migrate-queue subcommand + --queue-repo flag"
```

---

## Task 8: `init` prompt for `queue_repo`

**Confidence: 90%** — small UX edit to `setup_cmds.py`.

**Files:**
- Modify: `ralph_executor/setup_cmds.py`
- Test: `tests/executor/test_setup_cmds.py`

- [ ] **Step 1: Add a failing test**

In `tests/executor/test_setup_cmds.py`, add a test asserting that running `init` with a stubbed-in input stream writes `queue_repo = "<url>"` into the TOML config file. Use the same pattern as the existing `ralph_home` prompt test.

- [ ] **Step 2: Run; expect FAIL**

- [ ] **Step 3: Edit `setup_cmds.py`**

In the `init` flow, after the existing `workspace_root` prompt, add a `queue_repo` prompt with:
- Default: empty (force the operator to set it deliberately).
- Validation: `parse_target_repo` from `url_utils`. Bad input → reprompt.
- Smoke test: try a `git ls-remote` against the URL. On failure, print a warning but accept the value anyway (the operator may be on a flaky network).

Append `queue_repo = "<url>"` to the written `~/.ralph/config.toml`.

- [ ] **Step 4: Run; expect PASS**

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/setup_cmds.py tests/executor/test_setup_cmds.py
git commit -m "feat(init): prompt for queue_repo, smoke clone for validation"
```

---

## Task 9: Sweep remaining tests

**Confidence: 90%** — every test fixture that constructed `ExecutorConfig(queue_branch=...)` needs updating.

**Files:**
- Modify: `tests/executor/conftest.py`, `tests/executor/test_cli.py`, `tests/test_queue_writer.py`, `tests/safety/test_integration_loop.py`, `tests/test_setup_ralph_queue_github.py`, and any others surfaced by grep.

- [ ] **Step 1: Enumerate**

```bash
grep -rln "queue_branch" tests/
```

- [ ] **Step 2: For each hit, swap to `queue_repo="https://github.com/example/queue"`**

Any test that explicitly tested branch-swapping logic on the queue should be deleted (the logic is gone).

- [ ] **Step 3: Run the full test suite**

```bash
uv run pytest -x -q
```

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: migrate all fixtures from queue_branch to queue_repo"
```

---

## Task 10: Lint, type, format, full suite

**Confidence: 95%** — gate.

- [ ] **Step 1: ruff check + format**

```bash
uv run ruff check .
uv run ruff format --check .
```

Fix any complaints.

- [ ] **Step 2: mypy**

```bash
uv run mypy ralph_executor scripts skills tests
```

Fix any complaints.

- [ ] **Step 3: Full pytest**

```bash
uv run pytest -q
```

Expected: all green.

- [ ] **Step 4: Open the PR**

```bash
git push -u origin ralph/EXECUTOR-QUEUE-REPO-SPLIT
```

Use the `pr` skill's create-pr op or `gh pr create`. PR description points at this plan and the spec.

---

## Done criteria

- Executor reads `queue_repo` from TOML, clones to `$RALPH_WORKSPACE/queue/`, and uses it for every queue operation.
- `migrate-queue` subcommand bootstraps a new queue repo with the spec's exclusions.
- `init` prompts for `queue_repo`.
- `queue_branch` no longer exists anywhere in production code, tests, or fixtures.
- Full lint + type + test suite green.
- PR opened from `ralph/EXECUTOR-QUEUE-REPO-SPLIT` against `main`.
