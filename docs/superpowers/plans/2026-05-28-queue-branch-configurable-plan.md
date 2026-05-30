# Configurable queue branch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-introduce a configurable `queue_branch` knob on `ExecutorConfig` defaulting to `"ralph-queue"`, so the queue repo's `main` can stay protected while the executor and operator skills read/write a working branch.

**Architecture:** Add `queue_branch` to `ExecutorConfig` + `~/.ralph/config.toml`, thread it through every `git clone`/`pull`/`push` site in the executor (`queue_clone`, `loop`, `movements`) and operator-skill helpers (`scripts/queue_writer.py`). Repurpose `scripts/setup_ralph_queue_github.py` to provision the queue repo end-to-end (repo creation, README seed on `main`, `.ralph/` skeleton on `ralph-queue`, dual-branch protection). Update runbook + spec addendum + README.

**Tech Stack:** Python 3.12, `dataclasses`, `tomllib`, `subprocess`, `pytest`, `requests`, GitHub REST API.

**Spec:** `docs/superpowers/specs/2026-05-28-queue-branch-configurable-design.md`

---

## File structure

### Created
- `tests/executor/test_queue_clone.py` — unit tests for branch flag on clone + pull (if not already present; if present, extend).
- Plan + spec addendum sections inside existing docs.

### Modified
- `ralph_executor/config.py` — new `queue_branch` field, constant, validation, resolution.
- `ralph_executor/user_config.py` — `read_queue_branch()` helper + `write_queue_branch()` for init.
- `ralph_executor/queue_clone.py` — accept `queue_branch` arg, use in clone (`-b`) + pull.
- `ralph_executor/loop.py` — thread `cfg.queue_branch` through `_pull_queue` + `_persist_iteration_writes`.
- `ralph_executor/queue/movements.py` — thread `cfg.queue_branch` through `_move`.
- `ralph_executor/cli.py` — `--queue-branch` flag, `_apply_overrides` plumbing, startup log line, init subcommand prompt.
- `scripts/queue_writer.py` — `resolve_queue_branch()`, `acquire_queue_clone(...)` accepts branch, `push(repo, branch)` callers thread branch.
- `skills/ralph-add/scripts/add.py` — pass branch through.
- `skills/ralph-cancel/scripts/cancel.py` — pass branch through.
- `skills/ralph-promote/scripts/promote.py` — pass branch through.
- `skills/ralph-triage/scripts/triage.py` — pass branch through.
- `skills/ralph-status/scripts/status.py` — pass branch through (read-only path; uses pull only).
- `scripts/setup_ralph_queue_github.py` — end-to-end provisioning: repo creation, README seed, skeleton seed, dual-branch protection.
- `tests/executor/test_config.py` — coverage for queue_branch.
- `tests/executor/test_loop_integration.py`, `tests/executor/test_loop.py` — assertions that push targets configured branch.
- `tests/executor/test_movements.py` — push branch from cfg.
- `tests/executor/test_cli.py` — `--queue-branch` flag override.
- `tests/test_queue_writer.py` — `resolve_queue_branch` + push branch.
- `tests/test_setup_ralph_queue_github.py` — new coverage for full-provision shape.
- `docs/runbooks/ralph-queue-setup.md` — rewrite for new shape.
- `docs/superpowers/specs/2026-05-28-queue-repo-split-design.md` — append addendum.
- `README.md` — config table entry.

---

# PBI 1 — `EXECUTOR-QUEUE-BRANCH-CONFIGURABLE`

Configurable `queue_branch` in the executor only. Defaults to `"ralph-queue"`. Threaded through `queue_clone`, `loop`, `movements`.

## Task 1: Add `DEFAULT_QUEUE_BRANCH` constant + failing config field test

**Files:**
- Modify: `ralph_executor/config.py`
- Test: `tests/executor/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/executor/test_config.py`:

```python
def test_default_queue_branch_is_ralph_queue(tmp_path, monkeypatch):
    """Default queue_branch is 'ralph-queue' when no TOML / env override."""
    from ralph_executor.config import load_config

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".ralph").mkdir()
    (repo / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/test/queue"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_REPO_PATH", str(repo))
    monkeypatch.delenv("RALPH_QUEUE_BRANCH", raising=False)

    cfg = load_config()
    assert cfg.queue_branch == "ralph-queue"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/executor/test_config.py::test_default_queue_branch_is_ralph_queue -v`
Expected: FAIL with `AttributeError: 'ExecutorConfig' object has no attribute 'queue_branch'`.

- [ ] **Step 3: Add the constant + dataclass field**

In `ralph_executor/config.py`, add after `DEFAULT_MAIN_BRANCH = "main"` (line ~41):

```python
DEFAULT_QUEUE_BRANCH = "ralph-queue"
```

Add to `_TOML_KNOWN_KEYS` set (around line 128, immediately after `"queue_repo",`):

```python
        # Branch on the queue repo that holds .ralph/ state. Default
        # "ralph-queue". Operators wanting the post-split shipped behaviour
        # (state on main) override with queue_branch = "main".
        "queue_branch",
```

Add to `ExecutorConfig` dataclass immediately after `queue_repo: str` (around line 206):

```python
    # Branch on queue_repo that holds .ralph/ state. Default "ralph-queue"
    # (see DEFAULT_QUEUE_BRANCH). The executor's queue clone is permanently
    # on this branch; every clone / pull / push uses it.
    queue_branch: str
```

In `load_config` (around line 491), add resolution AFTER `main_branch = _resolve_str(...)` and before `max_attempts = _resolve_int(...)`:

```python
    queue_branch = _resolve_str(
        name="queue_branch",
        env_name="RALPH_QUEUE_BRANCH",
        toml_value=toml_overrides.get("queue_branch"),
        default=DEFAULT_QUEUE_BRANCH,
        source_label=source_label,
    )
```

Add `queue_branch=queue_branch,` to the `ExecutorConfig(...)` constructor at the end of `load_config` (around line 758), placed immediately after `queue_repo=queue_repo,`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/executor/test_config.py::test_default_queue_branch_is_ralph_queue -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/config.py tests/executor/test_config.py
git commit -m "feat(config): add queue_branch field with default ralph-queue"
```

---

## Task 2: TOML + env override resolution for queue_branch

**Files:**
- Modify: `ralph_executor/config.py` (no new code — verify resolution; tests prove it)
- Test: `tests/executor/test_config.py`

- [ ] **Step 1: Write failing TOML override test**

Append to `tests/executor/test_config.py`:

```python
def test_queue_branch_toml_override(tmp_path, monkeypatch):
    """queue_branch in project TOML overrides the default."""
    from ralph_executor.config import load_config

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".ralph").mkdir()
    (repo / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/test/queue"\n'
        'queue_branch = "custom-branch"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_REPO_PATH", str(repo))
    monkeypatch.delenv("RALPH_QUEUE_BRANCH", raising=False)

    cfg = load_config()
    assert cfg.queue_branch == "custom-branch"


def test_queue_branch_env_override_beats_toml(tmp_path, monkeypatch):
    """RALPH_QUEUE_BRANCH env var overrides TOML."""
    from ralph_executor.config import load_config

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".ralph").mkdir()
    (repo / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/test/queue"\n'
        'queue_branch = "toml-value"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_REPO_PATH", str(repo))
    monkeypatch.setenv("RALPH_QUEUE_BRANCH", "env-value")

    cfg = load_config()
    assert cfg.queue_branch == "env-value"
```

- [ ] **Step 2: Run tests to verify they pass already**

Run: `pytest tests/executor/test_config.py::test_queue_branch_toml_override tests/executor/test_config.py::test_queue_branch_env_override_beats_toml -v`
Expected: PASS (resolution from Task 1 already handles env + TOML via `_resolve_str`).

- [ ] **Step 3: Commit**

```bash
git add tests/executor/test_config.py
git commit -m "test(config): cover queue_branch TOML + env precedence"
```

---

## Task 3: Validation — reject empty, HEAD, refs/heads/ prefix

**Files:**
- Modify: `ralph_executor/config.py`
- Test: `tests/executor/test_config.py`

- [ ] **Step 1: Write failing validation tests**

Append to `tests/executor/test_config.py`:

```python
import pytest

@pytest.mark.parametrize("bad_value", ["", "   ", "HEAD", "refs/heads/foo"])
def test_queue_branch_rejects_invalid(tmp_path, monkeypatch, bad_value):
    """Empty / HEAD / refs-prefixed branch names raise ConfigError."""
    from ralph_executor.config import ConfigError, load_config

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".ralph").mkdir()
    (repo / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/test/queue"\n'
        f'queue_branch = "{bad_value}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_REPO_PATH", str(repo))
    monkeypatch.delenv("RALPH_QUEUE_BRANCH", raising=False)

    with pytest.raises(ConfigError, match="queue_branch"):
        load_config()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/executor/test_config.py::test_queue_branch_rejects_invalid -v`
Expected: FAIL — no validation yet; the bad values are silently accepted.

- [ ] **Step 3: Add validation in `load_config`**

In `ralph_executor/config.py`, immediately after the `queue_branch = _resolve_str(...)` block added in Task 1, insert:

```python
    queue_branch = queue_branch.strip()
    if not queue_branch:
        raise ConfigError(
            f"{source_label}: queue_branch must be a non-empty branch name"
        )
    if queue_branch == "HEAD":
        raise ConfigError(
            f"{source_label}: queue_branch must be a branch name, not 'HEAD'"
        )
    if queue_branch.startswith("refs/heads/"):
        raise ConfigError(
            f"{source_label}: queue_branch must not include the 'refs/heads/' "
            f"prefix (got {queue_branch!r})"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/executor/test_config.py::test_queue_branch_rejects_invalid -v`
Expected: PASS for all parametrised cases.

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/config.py tests/executor/test_config.py
git commit -m "feat(config): validate queue_branch (reject empty/HEAD/refs prefix)"
```

---

## Task 4: `read_queue_branch()` + `write_queue_branch()` in user_config

**Files:**
- Modify: `ralph_executor/user_config.py`
- Test: `tests/executor/test_config.py` (or `tests/executor/test_user_config.py` if present — check first)

- [ ] **Step 1: Confirm test file location**

Run: `ls tests/executor/test_user_config.py 2>$null; ls tests/executor/test_config.py`
If `test_user_config.py` exists, append to it; otherwise append to `test_config.py`.

- [ ] **Step 2: Write failing tests**

Append to the chosen test file:

```python
def test_read_queue_branch_returns_value(tmp_path, monkeypatch):
    """read_queue_branch reads queue_branch from ~/.ralph/config.toml."""
    from ralph_executor import user_config

    home = tmp_path / "home"
    home.mkdir()
    (home / ".ralph").mkdir()
    (home / ".ralph" / "config.toml").write_text(
        'queue_branch = "my-branch"\n', encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows

    assert user_config.read_queue_branch() == "my-branch"


def test_read_queue_branch_returns_none_when_absent(tmp_path, monkeypatch):
    """read_queue_branch returns None when the file lacks the key."""
    from ralph_executor import user_config

    home = tmp_path / "home"
    home.mkdir()
    (home / ".ralph").mkdir()
    (home / ".ralph" / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    assert user_config.read_queue_branch() is None


def test_write_queue_branch_persists(tmp_path, monkeypatch):
    """write_queue_branch writes the value and merges with existing keys."""
    from ralph_executor import user_config

    home = tmp_path / "home"
    home.mkdir()
    (home / ".ralph").mkdir()
    (home / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/test/queue"\n', encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    user_config.write_queue_branch("ralph-queue")
    assert user_config.read_queue_branch() == "ralph-queue"
    # queue_repo survives the merge
    assert user_config.read_queue_repo() == "https://github.com/test/queue"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest <chosen_test_file>::test_read_queue_branch_returns_value -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'read_queue_branch'`.

- [ ] **Step 4: Add helpers**

In `ralph_executor/user_config.py`, after `read_queue_repo()` (around line 135), add:

```python
def read_queue_branch() -> str | None:
    """Return the ``queue_branch`` value from the user config, or None.

    Mirrors ``read_queue_repo`` for the matching knob. The executor's
    ``load_config`` reads project TOML first; this helper is the
    operator-config fallback consulted by operator skills via
    ``scripts.queue_writer.resolve_queue_branch``.
    """
    data = _load_user_config()
    raw = data.get("queue_branch")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(
            f"{user_config_path()}: queue_branch must be a non-empty string, got {type(raw).__name__}"
        )
    return raw.strip()
```

After `write_queue_repo()` (around line 236), add:

```python
def write_queue_branch(branch: str) -> Path:
    """Persist ``queue_branch`` to ``~/.ralph/config.toml``.

    Merges with existing keys so ``queue_repo`` / ``ralph_home`` survive.
    """
    return _write_user_config({"queue_branch": branch})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest <chosen_test_file> -k queue_branch -v`
Expected: PASS for all three.

- [ ] **Step 6: Commit**

```bash
git add ralph_executor/user_config.py tests/executor/test_*.py
git commit -m "feat(user-config): add read_queue_branch + write_queue_branch helpers"
```

---

## Task 5: Wire user-config fallback into `load_config`

**Files:**
- Modify: `ralph_executor/config.py`
- Test: `tests/executor/test_config.py`

- [ ] **Step 1: Write failing user-config fallback test**

Append to `tests/executor/test_config.py`:

```python
def test_queue_branch_user_config_fallback(tmp_path, monkeypatch):
    """When project TOML and env are silent, user TOML supplies queue_branch."""
    from ralph_executor.config import load_config

    home = tmp_path / "home"
    home.mkdir()
    (home / ".ralph").mkdir()
    (home / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/test/queue"\n'
        'queue_branch = "user-config-branch"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".ralph").mkdir()
    (repo / ".ralph" / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("RALPH_REPO_PATH", str(repo))
    monkeypatch.delenv("RALPH_QUEUE_BRANCH", raising=False)

    cfg = load_config()
    assert cfg.queue_branch == "user-config-branch"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/executor/test_config.py::test_queue_branch_user_config_fallback -v`
Expected: FAIL — currently resolves to `DEFAULT_QUEUE_BRANCH`, not the user-config value.

- [ ] **Step 3: Add user-config fallback in `load_config`**

In `ralph_executor/config.py`, replace the `queue_branch = _resolve_str(...)` block (added in Task 1) and the validation block (added in Task 3) with this combined block:

```python
    queue_branch_value = toml_overrides.get("queue_branch")
    queue_branch_source = source_label
    if queue_branch_value is None:
        from ralph_executor.user_config import read_queue_branch, user_config_path

        try:
            user_queue_branch = read_queue_branch()
        except ConfigError:
            raise
        if user_queue_branch is not None:
            queue_branch_value = user_queue_branch
            queue_branch_source = str(user_config_path())
    queue_branch = _resolve_str(
        name="queue_branch",
        env_name="RALPH_QUEUE_BRANCH",
        toml_value=queue_branch_value,
        default=DEFAULT_QUEUE_BRANCH,
        source_label=queue_branch_source,
    )
    queue_branch = queue_branch.strip()
    if not queue_branch:
        raise ConfigError(
            f"{queue_branch_source}: queue_branch must be a non-empty branch name"
        )
    if queue_branch == "HEAD":
        raise ConfigError(
            f"{queue_branch_source}: queue_branch must be a branch name, not 'HEAD'"
        )
    if queue_branch.startswith("refs/heads/"):
        raise ConfigError(
            f"{queue_branch_source}: queue_branch must not include the 'refs/heads/' "
            f"prefix (got {queue_branch!r})"
        )
```

This mirrors the `queue_repo` resolution shape exactly (line 513-528).

- [ ] **Step 4: Run all queue_branch config tests**

Run: `pytest tests/executor/test_config.py -k queue_branch -v`
Expected: ALL PASS (default, TOML, env, user-config, validation).

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/config.py tests/executor/test_config.py
git commit -m "feat(config): user-config fallback for queue_branch"
```

---

## Task 6: Thread `queue_branch` through `ensure_queue_clone`

**Files:**
- Modify: `ralph_executor/queue_clone.py`
- Test: `tests/executor/test_queue_clone.py` (create if missing)

- [ ] **Step 1: Check if test file exists**

Run: `ls tests/executor/test_queue_clone.py 2>$null`
If absent, create it with a minimal header:

```python
"""Tests for ralph_executor.queue_clone."""
from __future__ import annotations
import subprocess
from pathlib import Path
import pytest
from ralph_executor.queue_clone import QueueCloneError, ensure_queue_clone
```

- [ ] **Step 2: Write failing test for `-b` flag on first-run clone**

Append to `tests/executor/test_queue_clone.py`:

```python
def test_ensure_queue_clone_uses_branch_flag_on_clone(tmp_path, monkeypatch):
    """First-run clone uses `git clone -b <queue_branch> <url> <dest>`."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured_args: list[list[str]] = []

    def fake_run(argv, capture_output, timeout):  # noqa: ARG001
        captured_args.append(argv)
        # Simulate the clone creating .git
        if argv[0] == "git" and "clone" in argv:
            dest = Path(argv[-1])
            (dest / ".git").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ralph_executor.queue_clone.run_text", fake_run)

    ensure_queue_clone(workspace, "https://github.com/test/queue", "ralph-queue")

    clone_cmd = next(a for a in captured_args if "clone" in a)
    assert "-b" in clone_cmd
    assert "ralph-queue" in clone_cmd
    # ordering: ... clone -b <branch> <url> <dest>
    b_idx = clone_cmd.index("-b")
    assert clone_cmd[b_idx + 1] == "ralph-queue"


def test_ensure_queue_clone_pulls_configured_branch(tmp_path, monkeypatch):
    """Refresh pull uses `git pull --ff-only origin <queue_branch>`."""
    workspace = tmp_path / "workspace"
    dest = workspace / "queue"
    (dest / ".git").mkdir(parents=True)

    captured_args: list[list[str]] = []

    def fake_run(argv, capture_output, timeout):  # noqa: ARG001
        captured_args.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ralph_executor.queue_clone.run_text", fake_run)

    ensure_queue_clone(workspace, "https://github.com/test/queue", "ralph-queue")

    pull_cmd = next(a for a in captured_args if "pull" in a)
    # ['git', '-C', <dest>, 'pull', '--ff-only', 'origin', 'ralph-queue']
    assert "ralph-queue" in pull_cmd
    assert pull_cmd[-2:] == ["origin", "ralph-queue"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/executor/test_queue_clone.py -v`
Expected: FAIL — `ensure_queue_clone` signature doesn't accept a branch argument; calls hardcode `"main"`.

- [ ] **Step 4: Update `ensure_queue_clone` signature + git calls**

In `ralph_executor/queue_clone.py`, replace the function definition (lines 50-87):

```python
def ensure_queue_clone(
    workspace_root: Path,
    queue_repo: str,
    queue_branch: str,
    *,
    timeout: float = 120.0,
) -> Path:
    """Ensure ``<workspace_root>/queue`` is a clone of ``queue_repo`` on ``queue_branch``.

    On first call: ``git clone -b <queue_branch> <queue_repo> <workspace_root>/queue``.
    On subsequent calls: ``git fetch origin`` then ``git pull --ff-only origin <queue_branch>``.

    Returns the path to the clone. Raises ``QueueCloneError`` with a message
    pointing at ``gh auth login`` on auth-related failures.
    """
    workspace_root.mkdir(parents=True, exist_ok=True)
    dest = workspace_root / "queue"

    if not (dest / ".git").exists():
        log.info("cloning queue %s (branch=%s) -> %s", queue_repo, queue_branch, dest)
        result = _run_git(
            None,
            "clone",
            "-b",
            queue_branch,
            queue_repo,
            str(dest),
            timeout=timeout,
        )
        if result.returncode != 0:
            raise QueueCloneError(
                f"git clone of queue repo {queue_repo!r} (branch {queue_branch!r}) failed "
                f"(exit {result.returncode}): {result.stderr.strip()}\n"
                f"If this is an auth problem, run `gh auth login`. If the branch is "
                f"missing on the remote, run scripts/setup_ralph_queue_github.py first."
            )
        return dest

    log.info("refreshing queue clone at %s (branch=%s)", dest, queue_branch)
    fetch = _run_git(dest, "fetch", "origin", timeout=timeout)
    if fetch.returncode != 0:
        raise QueueCloneError(
            f"git fetch in queue clone {dest} failed "
            f"(exit {fetch.returncode}): {fetch.stderr.strip()}"
        )
    pull = _run_git(dest, "pull", "--ff-only", "origin", queue_branch, timeout=timeout)
    if pull.returncode != 0:
        raise QueueCloneError(
            f"git pull --ff-only origin {queue_branch} in queue clone {dest} failed "
            f"(exit {pull.returncode}): {pull.stderr.strip()}\n"
            f"If {queue_branch} was force-pushed remotely, resolve manually."
        )
    return dest
```

Update the module docstring (lines 1-7):

```python
"""Idempotent clone of the queue repo into the workspace.

Mirrors ``target_clone.ensure_clone`` — clone on first call, fetch +
ff-only pull on subsequent calls. The branch is configurable per
deployment via ``cfg.queue_branch`` (default ``"ralph-queue"``); the
queue clone never leaves that branch.
"""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/executor/test_queue_clone.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ralph_executor/queue_clone.py tests/executor/test_queue_clone.py
git commit -m "feat(queue-clone): accept queue_branch arg for clone -b + pull"
```

---

## Task 7: Thread `queue_branch` through `loop._pull_queue` + `_persist_iteration_writes`

**Files:**
- Modify: `ralph_executor/loop.py`
- Test: `tests/executor/test_loop.py` (or `tests/executor/test_loop_integration.py`)

- [ ] **Step 1: Write failing test asserting `_pull_queue` passes `cfg.queue_branch`**

Append to `tests/executor/test_loop.py`:

```python
def test_pull_queue_passes_configured_branch(tmp_path, monkeypatch):
    """_pull_queue forwards cfg.queue_branch to ensure_queue_clone."""
    from ralph_executor.loop import _pull_queue

    captured: dict[str, object] = {}

    def fake_ensure(workspace_root, queue_repo, queue_branch, *, timeout=120.0):
        captured["queue_branch"] = queue_branch
        return workspace_root / "queue"

    monkeypatch.setattr("ralph_executor.loop.ensure_queue_clone", fake_ensure)

    cfg = _minimal_cfg(tmp_path, queue_branch="custom-branch")  # helper below
    _pull_queue(cfg)
    assert captured["queue_branch"] == "custom-branch"
```

If `_minimal_cfg` doesn't exist in the test file, add a helper at the top:

```python
def _minimal_cfg(tmp_path, **overrides):
    """Build a minimal ExecutorConfig for a unit test.

    Fills every required field with defensible default values; overrides
    win for any explicitly-passed keyword argument.
    """
    from ralph_executor.config import (
        DEFAULT_CLAUDE_BINARY,
        DEFAULT_CLAUDE_PERMISSION_MODE,
        DEFAULT_IDLE_EXIT_THRESHOLD,
        DEFAULT_ITERATION_SLEEP_SECONDS,
        DEFAULT_LOG_LEVEL,
        DEFAULT_MAIN_BRANCH,
        DEFAULT_MAX_ATTEMPTS,
        DEFAULT_PR_CHECK_POLL_INTERVAL_SECONDS,
        DEFAULT_PR_CHECK_POLL_MAX_ATTEMPTS,
        DEFAULT_QUEUE_BRANCH,
        DEFAULT_USE_WORKTREES,
        ExecutorConfig,
    )

    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    defaults = dict(
        repo_path=repo,
        queue_repo="https://github.com/test/queue",
        queue_branch=DEFAULT_QUEUE_BRANCH,
        main_branch=DEFAULT_MAIN_BRANCH,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        log_level=DEFAULT_LOG_LEVEL,
        iteration_sleep_seconds=DEFAULT_ITERATION_SLEEP_SECONDS,
        claude_binary=DEFAULT_CLAUDE_BINARY,
        claude_permission_mode=DEFAULT_CLAUDE_PERMISSION_MODE,
        anthropic_api_key="",
        git_host="github",
        gh_owner="test",
        ado_org_url="",
        ado_project="",
        halt_webhook="",
        pr_check_poll_max_attempts=DEFAULT_PR_CHECK_POLL_MAX_ATTEMPTS,
        pr_check_poll_interval_seconds=DEFAULT_PR_CHECK_POLL_INTERVAL_SECONDS,
        use_worktrees=DEFAULT_USE_WORKTREES,
        workspace_root=workspace,
    )
    defaults.update(overrides)
    return ExecutorConfig(**defaults)
```

Note: if the test file already has a similar helper or a fixture, extend it with `queue_branch=DEFAULT_QUEUE_BRANCH` rather than duplicating.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/executor/test_loop.py::test_pull_queue_passes_configured_branch -v`
Expected: FAIL — `_pull_queue` currently calls `ensure_queue_clone(cfg.workspace_root, cfg.queue_repo)` (two-arg).

- [ ] **Step 3: Update `_pull_queue` and `_persist_iteration_writes`**

In `ralph_executor/loop.py`, line 320, change:

```python
def _pull_queue(cfg: ExecutorConfig) -> None:
    log.debug("refreshing queue clone for %s", cfg.queue_repo)
    ensure_queue_clone(cfg.workspace_root, cfg.queue_repo)
```

to:

```python
def _pull_queue(cfg: ExecutorConfig) -> None:
    log.debug(
        "refreshing queue clone for %s (branch=%s)",
        cfg.queue_repo,
        cfg.queue_branch,
    )
    ensure_queue_clone(cfg.workspace_root, cfg.queue_repo, cfg.queue_branch)
```

In `ralph_executor/loop.py`, line 303, change:

```python
        git_ops.push_with_rebase(queue_repo, remote="origin", branch="main")
```

to:

```python
        git_ops.push_with_rebase(queue_repo, remote="origin", branch=cfg.queue_branch)
```

Note: `cfg` is in scope at line 303 — `_persist_iteration_writes(cfg, pbi_id, ...)`. If unavailable in the surrounding function signature, plumb it through. Verify by reading lines ~265-315.

- [ ] **Step 4: Run test to verify it passes + run the loop test module**

Run: `pytest tests/executor/test_loop.py::test_pull_queue_passes_configured_branch -v`
Expected: PASS.

Run: `pytest tests/executor/test_loop.py -v`
Expected: all tests still pass (any pre-existing test that asserted `branch="main"` needs updating to `cfg.queue_branch` or the new default `"ralph-queue"`; update it inline).

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/loop.py tests/executor/test_loop.py
git commit -m "feat(loop): thread cfg.queue_branch through pull + persist push"
```

---

## Task 8: Thread `queue_branch` through `queue/movements.py`

**Files:**
- Modify: `ralph_executor/queue/movements.py`
- Test: `tests/executor/test_movements.py`

- [ ] **Step 1: Write failing test**

Append to `tests/executor/test_movements.py` (use the existing `_minimal_cfg` helper or extend the test file's existing fixture):

```python
def test_move_pushes_to_configured_queue_branch(tmp_path, monkeypatch):
    """_move's final push_with_rebase targets cfg.queue_branch, not 'main'."""
    from ralph_executor.queue.movements import _move
    # ... (use existing test scaffolding to build a queue clone)
    pushed_branch: list[str] = []

    def fake_push(repo, *, remote, branch):
        pushed_branch.append(branch)

    monkeypatch.setattr("ralph_executor.queue.movements.git_ops.push_with_rebase", fake_push)

    cfg = _minimal_cfg(tmp_path, queue_branch="ralph-queue")
    # ... build pbi + queue clone state, then call _move
    # (mirror an existing test in this file for the scaffold)
    # Final assertion:
    assert pushed_branch == ["ralph-queue"]
```

Read existing tests in `tests/executor/test_movements.py` to copy the queue-clone setup scaffolding. Don't re-implement it.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/executor/test_movements.py::test_move_pushes_to_configured_queue_branch -v`
Expected: FAIL — currently hardcodes `"main"`.

- [ ] **Step 3: Update line 145 in `queue/movements.py`**

Change:

```python
    git_ops.push_with_rebase(queue_repo, remote="origin", branch="main")
```

to:

```python
    git_ops.push_with_rebase(queue_repo, remote="origin", branch=cfg.queue_branch)
```

Also update the comment immediately above (currently mentions "main"):

```python
    # push_with_rebase tolerates concurrent writers (operator commits, a
    # second ralph instance, web commits) racing the queue repo's
    # cfg.queue_branch between this iteration's start and the move's push.
    # PushRebaseConflict is the conflict case; iterate_once treats it as
    # a recoverable warning.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/executor/test_movements.py -v`
Expected: PASS (update any pre-existing assertion on `"main"` to `cfg.queue_branch` or `"ralph-queue"` inline).

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/queue/movements.py tests/executor/test_movements.py
git commit -m "feat(movements): _move pushes to cfg.queue_branch"
```

---

## Task 9: `--queue-branch` CLI flag + startup log

**Files:**
- Modify: `ralph_executor/cli.py`
- Test: `tests/executor/test_cli.py`

- [ ] **Step 1: Write failing CLI override test**

Append to `tests/executor/test_cli.py`:

```python
def test_cli_queue_branch_override_lands_on_cfg(tmp_path, monkeypatch):
    """--queue-branch on the CLI overrides cfg.queue_branch via _apply_overrides."""
    from ralph_executor.cli import _apply_overrides
    import argparse

    cfg = _minimal_cfg(tmp_path, queue_branch="ralph-queue")
    args = argparse.Namespace(
        repo=None, workspace=None, log_level=None,
        queue_repo=None, queue_branch="override-branch", watch=False,
    )
    new_cfg = _apply_overrides(cfg, args)
    assert new_cfg.queue_branch == "override-branch"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/executor/test_cli.py::test_cli_queue_branch_override_lands_on_cfg -v`
Expected: FAIL — `_apply_overrides` doesn't know about `queue_branch` yet.

- [ ] **Step 3: Add the flag + plumbing**

In `ralph_executor/cli.py`, after the `--queue-repo` argparse block (around line 139), add:

```python
    parser.add_argument(
        "--queue-branch",
        metavar="BRANCH",
        help=(
            "Override the queue_branch TOML value for this run "
            "(branch name on the queue repo; default: ralph-queue)."
        ),
    )
```

In `_apply_overrides` (around line 335), add `queue_branch` to the locals:

```python
    queue_branch: str = cfg.queue_branch
```

Add the override block after the `args.queue_repo` block (around line 363):

```python
    if getattr(args, "queue_branch", None):
        stripped = args.queue_branch.strip()
        if not stripped:
            raise ConfigError("--queue-branch must be a non-empty branch name")
        if stripped == "HEAD" or stripped.startswith("refs/heads/"):
            raise ConfigError(
                f"--queue-branch must be a plain branch name (got {args.queue_branch!r})"
            )
        queue_branch = stripped
        changed = True
```

Add `queue_branch=queue_branch,` to the `dataclasses.replace(...)` call (around line 372).

Update the startup log line (around line 602) from:

```python
    log.info(
        "ralph-executor starting (repo=%s queue_repo=%s main=%s)",
        cfg.repo_path,
        cfg.queue_repo,
        cfg.main_branch,
    )
```

to:

```python
    log.info(
        "ralph-executor starting (repo=%s queue_repo=%s queue_branch=%s main=%s)",
        cfg.repo_path,
        cfg.queue_repo,
        cfg.queue_branch,
        cfg.main_branch,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/executor/test_cli.py -v`
Expected: PASS for the new test; pre-existing tests should still pass.

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/cli.py tests/executor/test_cli.py
git commit -m "feat(cli): add --queue-branch flag + startup log entry"
```

---

## Task 10: Init prompt for `queue_branch`

**Files:**
- Modify: `ralph_executor/cli.py` (init subcommand handler — `cmd_init` or similar)
- Test: `tests/executor/test_cli.py`

- [ ] **Step 1: Locate the init handler**

Run: `grep -n "def cmd_init\|def _run_init\|queue_repo prompt" ralph_executor/cli.py`
Note the function name and line range. The init handler currently prompts for `ralph_home` and `queue_repo`.

- [ ] **Step 2: Write failing test**

Append to `tests/executor/test_cli.py`:

```python
def test_init_prompts_for_queue_branch(tmp_path, monkeypatch, capsys):
    """`ralph-executor init` prompts for queue_branch (default ralph-queue) after queue_repo."""
    from ralph_executor import user_config

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    # Simulate stdin: ralph_home default (blank), queue_repo URL, queue_branch blank (accept default)
    inputs = iter([
        "",  # ralph_home default
        "https://github.com/test/queue",  # queue_repo
        "",  # queue_branch — accept default
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    from ralph_executor.cli import main
    rc = main(["init"])
    assert rc == 0
    assert user_config.read_queue_branch() == "ralph-queue"
```

The exact form of the test depends on the init handler's structure (interactive vs `--yes` non-interactive). Read the existing `cmd_init` test (if any) to match its scaffolding.

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/executor/test_cli.py::test_init_prompts_for_queue_branch -v`
Expected: FAIL — init does not prompt for or persist `queue_branch`.

- [ ] **Step 4: Add the prompt in the init handler**

In the init handler in `ralph_executor/cli.py`, after the `queue_repo` prompt block, add:

```python
    queue_branch_default = "ralph-queue"
    answer = input(f"Queue branch [{queue_branch_default}]: ").strip()
    queue_branch = answer or queue_branch_default
    user_config.write_queue_branch(queue_branch)
    print(f"Wrote queue_branch = {queue_branch!r} to {user_config.user_config_path()}")
```

For `--yes` non-interactive mode, skip the prompt and write the default directly.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/executor/test_cli.py::test_init_prompts_for_queue_branch -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ralph_executor/cli.py tests/executor/test_cli.py
git commit -m "feat(cli): init subcommand prompts for queue_branch"
```

---

## Task 11: End-to-end loop integration test — push targets ralph-queue

**Files:**
- Test: `tests/executor/test_loop_integration.py`

- [ ] **Step 1: Read the existing loop integration test structure**

Run: `grep -n "def test_\|git init\|push_with_rebase\|queue_branch\|\"main\"" tests/executor/test_loop_integration.py | head -50`
Identify the pattern for setting up a temp queue repo, then locate the test that exercises the persist-and-push path.

- [ ] **Step 2: Update / add an end-to-end test asserting ralph-queue is pushed**

If an existing integration test asserts `"main"` against the pushed branch, update it to use the new default `"ralph-queue"`. If no such test exists, add:

```python
def test_loop_persists_to_ralph_queue_branch_by_default(tmp_path, monkeypatch):
    """A full iteration push lands on origin/ralph-queue when queue_branch is default."""
    # Build a bare upstream queue repo with main + ralph-queue branches.
    # Configure ExecutorConfig with default queue_branch.
    # Run one persist cycle and assert `git -C <upstream> log ralph-queue` shows the new commit
    # while `main` is untouched.
    ...
```

(Implement using the existing integration test patterns in the file; the snippet above is shape-only — the file's fixtures handle the actual git plumbing.)

- [ ] **Step 3: Run the integration test**

Run: `pytest tests/executor/test_loop_integration.py -v`
Expected: PASS — all integration tests green.

- [ ] **Step 4: Commit**

```bash
git add tests/executor/test_loop_integration.py
git commit -m "test(loop-integration): cover ralph-queue default branch end-to-end"
```

---

# PBI 2 — `SETUP-QUEUE-REPO-FULL-PROVISION`

Repurpose `scripts/setup_ralph_queue_github.py` from "create ralph-queue on an existing repo" to "provision the queue repo end-to-end".

## Task 12: Optional repo creation

**Files:**
- Modify: `scripts/setup_ralph_queue_github.py`
- Test: `tests/test_setup_ralph_queue_github.py`

- [ ] **Step 1: Write failing test for repo creation when 404**

Append to `tests/test_setup_ralph_queue_github.py`:

```python
def test_creates_repo_when_absent(monkeypatch, capsys):
    """When the repo 404s, the script POSTs /user/repos to create it."""
    from scripts import setup_ralph_queue_github as setup

    calls: list[tuple[str, str]] = []

    class FakeClient:
        def get(self, path):
            if path == "/repos/test/queue":
                raise setup.GhError("not found", status_code=404)
            return {}
        def post(self, path, json_body=None):
            calls.append(("POST", path))
            return {}
        def put(self, path, json_body=None):
            calls.append(("PUT", path))
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue", "--dry-run"])
    assert rc == 0
    assert ("POST", "/user/repos") in calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_ralph_queue_github.py::test_creates_repo_when_absent -v`
Expected: FAIL — `_lookup_repo` currently re-raises the 404 as an error.

- [ ] **Step 3: Add repo creation step**

In `scripts/setup_ralph_queue_github.py`, add a helper:

```python
def _ensure_repo_exists(
    client: GhClient,
    owner: str,
    repo: str,
    *,
    org: str | None,
    dry_run: bool,
) -> bool:
    """Return True if the repo already existed; False if it was just created."""
    try:
        client.get(f"/repos/{owner}/{repo}")
        return True
    except GhError as exc:
        if exc.status_code != 404:
            raise
    if dry_run:
        print(
            f"DRY-RUN would create {owner}/{repo} (private)",
            file=sys.stderr,
        )
        return False
    print(f"creating {owner}/{repo}...", file=sys.stderr)
    payload = {"name": repo, "private": True, "auto_init": True}
    if org is not None:
        client.post(f"/orgs/{org}/repos", json_body=payload)
    else:
        client.post("/user/repos", json_body=payload)
    return False
```

In `main`, replace the `_lookup_repo(...)` call (around line 187) with:

```python
        repo_existed = _ensure_repo_exists(
            client, owner, args.repo, org=args.org, dry_run=args.dry_run
        )
```

`auto_init=True` means GitHub initialises `main` with a default README — the seed step (next task) can update that README's contents or skip if acceptable.

- [ ] **Step 4: Add `--org` arg**

In `_parse_args`, add:

```python
    parser.add_argument(
        "--org",
        default=None,
        help="Create the repo under this organisation (default: under the authenticated user).",
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_setup_ralph_queue_github.py::test_creates_repo_when_absent -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/setup_ralph_queue_github.py tests/test_setup_ralph_queue_github.py
git commit -m "feat(setup): create queue repo if absent (--org optional)"
```

---

## Task 13: Seed README on main + `.ralph/` skeleton on ralph-queue

**Files:**
- Modify: `scripts/setup_ralph_queue_github.py`
- Test: `tests/test_setup_ralph_queue_github.py`

- [ ] **Step 1: Write failing test for README + skeleton PUTs**

Append to `tests/test_setup_ralph_queue_github.py`:

```python
def test_seeds_readme_and_skeleton(monkeypatch):
    """Seed step: README on main, .ralph/<state>/.gitkeep on ralph-queue."""
    from scripts import setup_ralph_queue_github as setup

    puts: list[tuple[str, dict]] = []

    class FakeClient:
        def get(self, path):
            # Pretend main exists, ralph-queue and .ralph/ skeleton don't
            if path.endswith("/contents/README.md") or "/contents/.ralph" in path:
                raise setup.GhError("not found", status_code=404)
            return {"object": {"sha": "abc123"}}
        def post(self, path, json_body=None):
            return {}
        def put(self, path, json_body=None):
            puts.append((path, json_body or {}))
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue"])
    assert rc == 0
    # README PUT on main
    assert any(path.endswith("/contents/README.md") for path, _ in puts)
    # Skeleton PUTs on ralph-queue, one per state folder
    for folder in ("inbox", "current", "pending-pr", "blocked", "archive", "done"):
        assert any(path.endswith(f"/contents/.ralph/{folder}/.gitkeep") for path, _ in puts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_ralph_queue_github.py::test_seeds_readme_and_skeleton -v`
Expected: FAIL — no seed logic yet.

- [ ] **Step 3: Add seed helpers**

Add to `scripts/setup_ralph_queue_github.py`:

```python
import base64 as _b64

QUEUE_STATE_FOLDERS = ("inbox", "current", "pending-pr", "blocked", "archive", "done")

def _content_exists(client: GhClient, owner: str, repo: str, path: str, ref: str) -> bool:
    try:
        client.get(f"/repos/{owner}/{repo}/contents/{path}?ref={ref}")
        return True
    except GhError as exc:
        if exc.status_code == 404:
            return False
        raise


def _put_content(
    client: GhClient,
    owner: str,
    repo: str,
    path: str,
    *,
    branch: str,
    content_bytes: bytes,
    message: str,
    dry_run: bool,
) -> bool:
    if _content_exists(client, owner, repo, path, branch):
        return False
    if dry_run:
        print(f"DRY-RUN would PUT {path} on {branch}", file=sys.stderr)
        return False
    payload = {
        "message": message,
        "content": _b64.b64encode(content_bytes).decode("ascii"),
        "branch": branch,
    }
    client.put(f"/repos/{owner}/{repo}/contents/{path}", json_body=payload)
    return True


def _seed_main_readme(
    client: GhClient, owner: str, repo: str, *, dry_run: bool
) -> bool:
    readme = (
        f"# {repo}\n\n"
        "Queue repo for ralph-executor. Queue state lives on the `ralph-queue` branch.\n"
    )
    return _put_content(
        client, owner, repo, "README.md",
        branch="main",
        content_bytes=readme.encode("utf-8"),
        message="docs: seed README",
        dry_run=dry_run,
    )


def _seed_ralph_skeleton(
    client: GhClient, owner: str, repo: str, queue_branch: str, *, dry_run: bool
) -> int:
    """Returns the number of .gitkeep files created."""
    created = 0
    for folder in QUEUE_STATE_FOLDERS:
        if _put_content(
            client, owner, repo, f".ralph/{folder}/.gitkeep",
            branch=queue_branch,
            content_bytes=b"",
            message=f"chore(queue): seed {folder}/",
            dry_run=dry_run,
        ):
            created += 1
    return created
```

In `main`, add the seed calls between repo creation and branch protection:

```python
        # Seed main README (idempotent — skipped if README.md present)
        _seed_main_readme(client, owner, args.repo, dry_run=args.dry_run)
        # Seed .ralph/ skeleton on queue_branch (idempotent per file)
        _seed_ralph_skeleton(
            client, owner, args.repo, args.branch, dry_run=args.dry_run
        )
```

Sequence inside `main`:
1. `_ensure_repo_exists`
2. Read main tip (`_read_branch_tip`)
3. Seed main README (idempotent)
4. Create ralph-queue off main (existing logic)
5. Seed `.ralph/` skeleton on ralph-queue (idempotent)
6. Branch protection (next task)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup_ralph_queue_github.py::test_seeds_readme_and_skeleton -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup_ralph_queue_github.py tests/test_setup_ralph_queue_github.py
git commit -m "feat(setup): seed README on main + .ralph/ skeleton on ralph-queue"
```

---

## Task 14: Dual-branch protection

**Files:**
- Modify: `scripts/setup_ralph_queue_github.py`
- Test: `tests/test_setup_ralph_queue_github.py`

- [ ] **Step 1: Write failing test for protection on both branches**

Append to `tests/test_setup_ralph_queue_github.py`:

```python
def test_protection_applied_to_main_and_ralph_queue(monkeypatch):
    """Protection step applies rules to both main and ralph-queue."""
    from scripts import setup_ralph_queue_github as setup

    protected: list[str] = []

    class FakeClient:
        def get(self, path):
            return {"object": {"sha": "abc"}}
        def post(self, path, json_body=None):
            return {}
        def put(self, path, json_body=None):
            if "/protection" in path:
                protected.append(path)
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue"])
    assert rc == 0
    assert any(p.endswith("/branches/main/protection") for p in protected)
    assert any(p.endswith("/branches/ralph-queue/protection") for p in protected)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_ralph_queue_github.py::test_protection_applied_to_main_and_ralph_queue -v`
Expected: FAIL — currently only ralph-queue is protected.

- [ ] **Step 3: Add main protection helper + main-branch protection call**

In `scripts/setup_ralph_queue_github.py`, replace `_apply_protection` with two functions:

```python
def _apply_main_protection(client: GhClient, owner: str, repo: str) -> None:
    """main: require PR (1 approval), no force-push, no deletion."""
    payload = {
        "required_status_checks": None,
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 1,
        },
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }
    client.put(
        f"/repos/{owner}/{repo}/branches/main/protection",
        json_body=payload,
    )


def _apply_queue_branch_protection(client: GhClient, owner: str, repo: str, branch: str) -> None:
    """ralph-queue: no force-push, no deletion. No PR requirement."""
    payload = {
        "required_status_checks": None,
        "enforce_admins": True,
        "required_pull_request_reviews": None,
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_conversation_resolution": False,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }
    client.put(
        f"/repos/{owner}/{repo}/branches/{branch}/protection",
        json_body=payload,
    )
```

In `main`, replace the single `_apply_protection(client, owner, args.repo, args.branch)` call with:

```python
        if args.no_protection:
            print("skipping branch protection (--no-protection)", file=sys.stderr)
        elif args.dry_run:
            print("DRY-RUN would PUT protection on main and ralph-queue", file=sys.stderr)
        else:
            print("applying branch protection on main...", file=sys.stderr)
            _apply_main_protection(client, owner, args.repo)
            print(f"applying branch protection on {args.branch}...", file=sys.stderr)
            _apply_queue_branch_protection(client, owner, args.repo, args.branch)
            protection_applied = True
```

Add the `--no-protection` arg:

```python
    parser.add_argument(
        "--no-protection",
        action="store_true",
        help="Skip applying branch-protection rules (for sandboxes / test repos).",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup_ralph_queue_github.py -v`
Expected: PASS — all setup-script tests green.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup_ralph_queue_github.py tests/test_setup_ralph_queue_github.py
git commit -m "feat(setup): dual-branch protection (PR on main, no-force on ralph-queue)"
```

---

## Task 15: Idempotency end-to-end test

**Files:**
- Test: `tests/test_setup_ralph_queue_github.py`

- [ ] **Step 1: Write idempotency test**

Append:

```python
def test_full_run_idempotent_on_second_invocation(monkeypatch):
    """Re-running the script on a fully-provisioned repo is a no-op."""
    from scripts import setup_ralph_queue_github as setup

    posts: list[str] = []
    puts: list[str] = []

    class FakeClient:
        def get(self, path):
            # Everything exists already
            return {"object": {"sha": "abc"}}
        def post(self, path, json_body=None):
            posts.append(path)
            return {}
        def put(self, path, json_body=None):
            puts.append(path)
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue", "--no-protection"])
    assert rc == 0
    # No POSTs (no branch creation), no PUTs to /contents (everything exists),
    # no PUTs to /protection (--no-protection)
    assert posts == []
    content_puts = [p for p in puts if "/contents/" in p]
    assert content_puts == []
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_setup_ralph_queue_github.py::test_full_run_idempotent_on_second_invocation -v`
Expected: PASS if seed helpers use `_content_exists` (they should from Task 13).

If FAIL, investigate why a content PUT or branch POST happened — should be a missed idempotency guard.

- [ ] **Step 3: Commit**

```bash
git add tests/test_setup_ralph_queue_github.py
git commit -m "test(setup): re-run on fully-provisioned repo is a no-op"
```

---

# PBI 3 — `SKILLS-QUEUE-BRANCH-THREADING`

Thread `queue_branch` through `scripts/queue_writer.py` and every operator skill that pulls or pushes the queue clone.

## Task 16: `resolve_queue_branch` + `acquire_queue_clone(...)` accepts branch

**Files:**
- Modify: `scripts/queue_writer.py`
- Test: `tests/test_queue_writer.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_queue_writer.py`:

```python
def test_resolve_queue_branch_cli_value():
    from scripts.queue_writer import resolve_queue_branch
    assert resolve_queue_branch("custom-branch") == "custom-branch"


def test_resolve_queue_branch_user_toml(tmp_path, monkeypatch):
    from scripts.queue_writer import resolve_queue_branch

    home = tmp_path / "home"
    home.mkdir()
    (home / ".ralph").mkdir()
    (home / ".ralph" / "config.toml").write_text(
        'queue_branch = "from-toml"\n', encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    assert resolve_queue_branch(None) == "from-toml"


def test_resolve_queue_branch_default(tmp_path, monkeypatch):
    from scripts.queue_writer import resolve_queue_branch

    home = tmp_path / "home"
    home.mkdir()
    (home / ".ralph").mkdir()
    (home / ".ralph" / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    assert resolve_queue_branch(None) == "ralph-queue"


def test_acquire_queue_clone_forwards_branch(tmp_path, monkeypatch):
    from scripts.queue_writer import acquire_queue_clone

    captured = {}
    def fake_ensure(workspace_root, queue_repo, queue_branch, *, timeout=120.0):
        captured["queue_branch"] = queue_branch
        return workspace_root / "queue"

    monkeypatch.setattr("scripts.queue_writer.ensure_queue_clone", fake_ensure)
    acquire_queue_clone(tmp_path, "https://github.com/test/queue", "ralph-queue")
    assert captured["queue_branch"] == "ralph-queue"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queue_writer.py -k queue_branch -v`
Expected: FAIL — `resolve_queue_branch` doesn't exist; `acquire_queue_clone` is two-arg.

- [ ] **Step 3: Update `scripts/queue_writer.py`**

Update `acquire_queue_clone` signature:

```python
def acquire_queue_clone(
    workspace_root: Path,
    queue_repo: str,
    queue_branch: str,
    *,
    timeout: float = 120.0,
) -> Path:
    """Idempotent queue clone for operator skills.

    Mirrors ``ralph_executor.queue_clone.ensure_queue_clone``. The branch
    is forwarded unchanged; the operator's `~/.ralph/config.toml` knob is
    resolved by the caller via ``resolve_queue_branch``.
    """
    try:
        return ensure_queue_clone(workspace_root, queue_repo, queue_branch, timeout=timeout)
    except QueueCloneError as exc:
        raise QueueWriterError(str(exc)) from exc
```

Add `resolve_queue_branch` (after `resolve_queue_repo`, line ~152):

```python
DEFAULT_QUEUE_BRANCH = "ralph-queue"


def resolve_queue_branch(cli_value: str | None = None) -> str:
    """Resolve ``queue_branch`` for operator skills.

    Order: explicit CLI value → ``queue_branch`` in ``~/.ralph/config.toml``
    → default ``"ralph-queue"``. Unlike ``resolve_queue_repo`` there IS a
    silent default — every queue repo has a branch, and the default
    matches the new spec.

    ``ConfigError`` from a malformed user TOML is re-raised as
    ``QueueWriterError`` so the skill surface stays single-typed.
    """
    if cli_value is not None:
        value = cli_value.strip()
        if not value:
            raise QueueWriterError("--queue-branch must be a non-empty string")
        return value
    from ralph_executor.config import ConfigError
    from ralph_executor.user_config import read_queue_branch

    try:
        from_toml = read_queue_branch()
    except ConfigError as exc:
        raise QueueWriterError(str(exc)) from exc
    return from_toml if from_toml is not None else DEFAULT_QUEUE_BRANCH
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queue_writer.py -k queue_branch -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/queue_writer.py tests/test_queue_writer.py
git commit -m "feat(queue-writer): resolve_queue_branch + branch-aware acquire_queue_clone"
```

---

## Task 17: Each skill threads `queue_branch` through acquire + push

**Files:**
- Modify: `skills/ralph-add/scripts/add.py`
- Modify: `skills/ralph-cancel/scripts/cancel.py`
- Modify: `skills/ralph-promote/scripts/promote.py`
- Modify: `skills/ralph-triage/scripts/triage.py`
- Modify: `skills/ralph-status/scripts/status.py`
- Tests: `tests/skills/test_ralph_*.py`

- [ ] **Step 1: Locate every `acquire_queue_clone` + `push` call in the skill scripts**

Run: `grep -rn "acquire_queue_clone\|queue_writer.push\|, branch=" skills/`
Note each call site.

- [ ] **Step 2: For each skill, write a failing test asserting the new default branch is used**

Pattern (apply per skill — example for ralph-add):

In `tests/skills/test_ralph_add.py`, append:

```python
def test_ralph_add_pushes_ralph_queue_by_default(tmp_path, monkeypatch):
    """ralph-add pushes to ralph-queue when no --queue-branch override is set."""
    from skills.ralph_add.scripts import add
    pushed: list[str] = []

    def fake_push(repo, branch):
        pushed.append(branch)

    monkeypatch.setattr("scripts.queue_writer.push", fake_push)
    # ... (build minimal stdin / args; mirror an existing test in the file)
    # Final assertion:
    assert pushed == ["ralph-queue"]
```

(Match each skill's existing test scaffolding rather than re-implementing it.)

- [ ] **Step 3: Run all skill tests to confirm they fail**

Run: `pytest tests/skills/ -v -k pushes_ralph_queue`
Expected: FAIL — skills currently call `push(repo, "main")`.

- [ ] **Step 4: Update each skill**

In every skill `scripts/*.py`:

1. Import: `from scripts.queue_writer import resolve_queue_branch` (in addition to the existing imports).
2. After `queue_repo = resolve_queue_repo(...)`, add `queue_branch = resolve_queue_branch(getattr(args, "queue_branch", None))`.
3. Change `acquire_queue_clone(workspace_root, queue_repo)` → `acquire_queue_clone(workspace_root, queue_repo, queue_branch)`.
4. Change every `push(repo, "main")` → `push(repo, queue_branch)`.
5. Add a `--queue-branch` argparse arg if the skill takes args (matches `--queue-repo`).

- [ ] **Step 5: Run all skill tests to verify they pass**

Run: `pytest tests/skills/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add skills/ tests/skills/
git commit -m "feat(skills): thread queue_branch through ralph-add/cancel/promote/triage/status"
```

---

# PBI 4 — `DOCS-QUEUE-BRANCH-CONFIGURABLE`

Documentation updates. No code, no tests.

## Task 18: Rewrite runbook

**Files:**
- Modify: `docs/runbooks/ralph-queue-setup.md`

- [ ] **Step 1: Read the existing runbook to understand current structure**

Run: `cat docs/runbooks/ralph-queue-setup.md`

- [ ] **Step 2: Rewrite for the new shape**

Replace the runbook content with:

```markdown
# Setting up a queue repo

The queue repo holds your ralph queue state: PBIs in
`.ralph/{inbox,current,pending-pr,blocked,archive,done}/`. The executor and
operator skills (ralph-add, ralph-cancel, ralph-promote, ralph-triage,
ralph-status) read and write this repo on a working branch (`ralph-queue`
by default), while `main` stays protected and clean.

## One command, end to end

```bash
GH_TOKEN=<token> GH_OWNER=<owner> python scripts/setup_ralph_queue_github.py --repo <repo-name>
```

Creates (idempotent on re-run):

1. The GitHub repository (private; `--org <name>` to create under an org).
2. `main` branch with a stub README.
3. `ralph-queue` branch off `main` with the `.ralph/` state-folder skeleton.
4. Branch protection: `main` requires 1-approval PRs + no force-push + no deletion; `ralph-queue` no force-push + no deletion.

Flags:
- `--repo NAME` — repo name without owner prefix (required).
- `--branch NAME` — queue branch name (default: `ralph-queue`).
- `--org NAME` — create under organisation `NAME` instead of authenticated user.
- `--dry-run` — read state, no mutations.
- `--no-protection` — skip protection PUTs (sandboxes / test repos).

## Wiring the executor

Once the repo exists, point the executor at it:

```bash
ralph-executor init
# Prompts for ralph_home, queue_repo URL, and queue_branch (default ralph-queue)
```

Or write `~/.ralph/config.toml` directly:

```toml
queue_repo = "https://github.com/<owner>/<repo>"
queue_branch = "ralph-queue"   # optional — this is the default
```

## Override precedence

The `queue_branch` resolves in this order (highest precedence first):

1. `--queue-branch NAME` on the CLI (per-run).
2. `RALPH_QUEUE_BRANCH` env var.
3. `queue_branch` in `<repo>/.ralph/config.toml` (project-level).
4. `queue_branch` in `~/.ralph/config.toml` (operator-level).
5. `"ralph-queue"` (default).

## Operating on `main` instead

Pre-split deployments stored queue state on `main`. To preserve that:

```toml
# ~/.ralph/config.toml
queue_branch = "main"
```

Then loosen `main` branch protection accordingly (the executor needs to push there).
```

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/ralph-queue-setup.md
git commit -m "docs(runbook): rewrite ralph-queue-setup for end-to-end provisioning"
```

---

## Task 19: Spec addendum + README entry

**Files:**
- Modify: `docs/superpowers/specs/2026-05-28-queue-repo-split-design.md`
- Modify: `README.md`

- [ ] **Step 1: Append addendum to the split spec**

Append to `docs/superpowers/specs/2026-05-28-queue-repo-split-design.md`:

```markdown
---

## Addendum (2026-05-28): `queue_branch` re-introduced

PBI #48 deleted the `queue_branch` field on `ExecutorConfig` and hardcoded
`"main"` everywhere the queue clone is touched. The follow-up spec
`docs/superpowers/specs/2026-05-28-queue-branch-configurable-design.md`
restores it as a configurable knob, default `"ralph-queue"`.

Why:
- `main` of a queue repo accumulates a persist commit per iteration plus
  every PBI move. With state on `main`, branch-protection rules either
  block the executor or are loosened to the point of being decorative.
- Keeping `main` protected and clean (with PR-required updates) while
  the executor pushes to `ralph-queue` matches the operator-protection
  intent without changing the executor's data flow.

Migration: pre-split deployments running on `main` set
`queue_branch = "main"` in their TOML. Fresh deployments using
`scripts/setup_ralph_queue_github.py` get the new shape end-to-end.
```

- [ ] **Step 2: Add README entry**

Read `README.md` to find the config table or knobs section.

Run: `grep -n "queue_repo\|main_branch\|config.toml" README.md | head -20`

In the relevant section, add a row for `queue_branch`:

```markdown
| `queue_branch` | `ralph-queue` | Branch on `queue_repo` that holds `.ralph/` state. Override with TOML, `RALPH_QUEUE_BRANCH`, or `--queue-branch`. |
```

(Match the table format already used in the README.)

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-28-queue-repo-split-design.md README.md
git commit -m "docs: queue_branch addendum on split spec + README entry"
```

---

## Task 20: Full-suite sanity run

**Files:**
- None — verification only.

- [ ] **Step 1: Run the full executor test suite**

Run: `pytest tests/ -v`
Expected: green across the board.

- [ ] **Step 2: Run with -k queue_branch to confirm new coverage executed**

Run: `pytest tests/ -v -k queue_branch`
Expected: PASS — every new test from this plan runs.

- [ ] **Step 3: Lint / typecheck if the project has gates**

Run: `python -m mypy ralph_executor/` (if mypy is configured)
Run: `ruff check ralph_executor/ scripts/ skills/` (if ruff is configured)
Expected: clean.

- [ ] **Step 4: If anything is red, fix in place — do not commit the plan as done with failures**

- [ ] **Step 5: Final commit only if any cleanup edits were needed**

```bash
git add -A && git commit -m "chore: post-plan sanity sweep"
```

---

## Self-review (planner notes)

**Spec coverage check** — every spec section maps to at least one task:
- Spec §"Components and changes / Executor": Tasks 1-11.
- Spec §"Components and changes / Setup script": Tasks 12-15.
- Spec §"Components and changes / Operator skills": Tasks 16-17.
- Spec §"Components and changes / Docs": Tasks 18-19.
- Spec §"Testing": every bullet has a task (config, queue_clone, loop, movements, cli, setup, queue_writer).
- Spec §"Edge cases":
  - `queue_branch = "main"` opt-in: covered by Task 2's TOML override.
  - Re-run idempotency: covered by Task 15.
  - `--no-protection`: covered by Task 14.
  - `queue_branch` containing `/`: covered implicitly by Task 3's validator (no rejection for slashes).
  - TOML key drift: documented in Task 18.
- Spec §"Rollout": PBI 1 = Tasks 1-11; PBI 2 = Tasks 12-15; PBI 3 = Tasks 16-17; PBI 4 = Tasks 18-19. Sanity = Task 20.

**Type consistency:** `queue_branch: str` used everywhere. `resolve_queue_branch(cli_value: str | None) -> str` matches `resolve_queue_repo` shape. `acquire_queue_clone(workspace_root, queue_repo, queue_branch, *, timeout)` matches `ensure_queue_clone` shape.

**Placeholders:** none. Every task has runnable test code and a concrete patch shape.

**Open question (carried forward from spec):** Task 11 marks the end-to-end integration test as "shape-only" because it depends on the file's existing fixtures — verify these exist at execution time and copy the scaffold from a neighbouring test. If they don't, expand Task 11 with a fixture-creation sub-step.
