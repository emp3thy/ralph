# `ralph-executor` Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is the largest plan in the Ralph v1 set — the executor itself. Do not skip TDD steps; the executor is the single critical-path artifact and the safety / sweep plans (8, 9) build on top of it.

**Goal:** Ship the `ralph_executor` Python package — the loop driver that holds at most one PBI in `.ralph/current/` at a time, spawns `claude -p` against it, classifies the outcome, juggles the three branches (`ralph-queue`, `main`, `ralph/<PBI-ID>`), and respects multi-step PBI semantics (a PBI stays in `current/` across iterations until Ralph creates the PR or writes STUCK.md). The executor is the runtime substrate that Plans 8 (sweep) and 9 (safety controls) extend; this plan deliberately leaves those extension points as **typed stubs** with explicit `# Stub — Plan 8 fills this in` / `# Stub — Plan 9 fills this in` markers, never silent gaps.

**Architecture:** A Python package `ralph_executor/` with:

- `types.py` — the canonical typed shapes (`PBIType`, `PBIStatus`, `Severity`, `PBI` dataclass) referenced by Plans 8, 9, 10 per the orchestrator's [Cross-plan integration points](./2026-05-24-00-orchestrator.md#cross-plan-integration-points).
- `config.py` — `ExecutorConfig` dataclass + `load_config()` that reads `RALPH_*` / `ANTHROPIC_API_KEY` environment variables, validates the working tree, and produces an immutable configuration object passed through the rest of the package.
- `git_ops.py` — thin subprocess wrappers around the git binary (`fetch`, `checkout`, `pull`, `current_branch`, `commit_all`, `push`, `mv` via `git mv`, `is_branch_present`). Every git call goes through `_run_git` so tests can monkeypatch one place.
- `queue/filesystem.py` — `FilesystemQueueSource` implementing the canonical queue-source contract: read PBIs from the `.ralph/<state>/` directories on the `ralph-queue` checkout, parse their frontmatter, return `PBI` dataclasses, sorted by priority lane.
- `queue/movements.py` — atomic folder-move operations between queue states (`move_inbox_to_current`, `move_current_to_pending_pr`, `move_current_to_blocked`). Every move uses `git mv` + commit + push on `ralph-queue` and updates the entry file's frontmatter (`status:` and `updated_at:`).
- `claude_spawn.py` — `spawn_claude_p` runs `claude -p` as a subprocess against the current PBI, captures stdout/stderr, and `classify_outcome` inspects the combined output + on-disk side effects (`STUCK.md` present? PR URL printed? branch advanced?) to produce a typed `ClaudeOutcome`.
- `loop.py` — `iterate_once(cfg)` runs one iteration of the loop (the algorithm described in the spec's "Iteration model"); `run_loop(cfg)` repeatedly calls `iterate_once` until interrupted. The sweep step and the cycle-detector hook are typed stubs that Plans 8 and 9 implement.
- `cli.py` — `main(argv)` entry point: argparse, load config, run loop, handle KeyboardInterrupt cleanly.
- `__init__.py` — re-exports the public API (`PBI`, `ExecutorConfig`, `iterate_once`, `run_loop`, `main`).

The tests mirror the package: every module gets a `tests/executor/test_<module>.py`. Git is mocked through a single `fake_git` fixture that intercepts subprocess invocations. `claude -p` is mocked through a fake-claude script (a tiny Python script written into `tmp_path` and invoked as the binary) so the executor's spawn-and-classify logic is exercised end-to-end without ever calling real Claude.

**Tech Stack:** Python 3.12+, `uv`, `pytest`, `pyyaml`, ruff, mypy strict. No new runtime dependencies beyond Plan 1's `pyyaml` and Plan 2's `requests`. The `claude` CLI is the binary the executor spawns; tests fake it via a stand-in script.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Modify only to add the `ralph-executor` console script entry point under `[project.scripts]`. No new dependencies. |
| `ralph_executor/__init__.py` | Replace the Plan 1 stub with a small re-export surface so callers write `from ralph_executor import PBI, ExecutorConfig, iterate_once, run_loop`. |
| `ralph_executor/types.py` | Canonical typed shapes shared with Plans 8, 9, 10: `PBIType`, `PBIStatus`, `Severity` (Literal aliases) plus the frozen `PBI` dataclass. |
| `ralph_executor/config.py` | `ExecutorConfig` dataclass; `load_config()` resolves env vars (`RALPH_REPO_PATH`, `RALPH_QUEUE_BRANCH`, `RALPH_MAIN_BRANCH`, `RALPH_MAX_ATTEMPTS`, `RALPH_LOG_LEVEL`, `RALPH_ITERATION_SLEEP_SECONDS`, `RALPH_CLAUDE_BINARY`, `ANTHROPIC_API_KEY`); validates the repo path is a git working tree. |
| `ralph_executor/git_ops.py` | Subprocess wrappers: `_run_git`, `current_branch`, `fetch`, `pull`, `checkout`, `checkout_new`, `branch_exists`, `is_branch_remote`, `commit_all`, `push`, `mv`, `add`, `rev_parse_head`. |
| `ralph_executor/queue/__init__.py` | Empty package marker. |
| `ralph_executor/queue/filesystem.py` | `FilesystemQueueSource` class: `current_pbi()`, `inbox_pbis()`, `pick_next()`, plus shared frontmatter parsing (`parse_pbi_directory`). Sorts by severity lane (`PR-feedback type` > `critical` > `high` > `normal` > `low`) and within each lane by `created_at`. |
| `ralph_executor/queue/movements.py` | Atomic folder moves: `move_inbox_to_current`, `move_current_to_pending_pr`, `move_current_to_blocked`. Each one performs `git mv` → frontmatter rewrite (`status:`, `updated_at:`) → `git commit` → `git push`. |
| `ralph_executor/claude_spawn.py` | `spawn_claude_p(cfg, pbi) -> ClaudeOutcome`; the `ClaudeOutcome` dataclass has `kind: Literal["pr_created", "partial", "stuck", "error"]`, the PR URL (when `kind="pr_created"`), the raw stdout/stderr captures, the exit code, and the duration. Classification reads stdout for the `PR created: <url>` marker the PROMPT.md prescribes, checks for `STUCK.md` in the PBI directory, and falls back to "partial" when neither is present and Ralph exited zero. |
| `ralph_executor/loop.py` | The iteration model: `iterate_once`, `run_loop`, plus the named stub functions `_run_sweep` (Plan 8) and `_check_cycle_detector` (Plan 9). |
| `ralph_executor/cli.py` | `main(argv)` — argparse, config loading, KeyboardInterrupt handling. |
| `tests/executor/__init__.py` | Empty package marker. |
| `tests/executor/conftest.py` | Shared fixtures: `fake_repo` (tempfile-backed bare + worktree pair with `main` and `ralph-queue`), `sample_pbi` (writes a feature PBI into `.ralph/inbox/WI-1234/`), `fake_claude_binary` (writes a Python stand-in into `tmp_path/bin/claude` and sets it executable), `cfg_for_repo` (builds an `ExecutorConfig` against the fake repo + fake claude). |
| `tests/executor/test_types.py` | Sanity checks on the `PBI` dataclass and the Literal aliases. |
| `tests/executor/test_config.py` | `load_config` accepts the documented env vars; rejects missing required ones; rejects invalid repo paths. |
| `tests/executor/test_git_ops.py` | Each git helper invokes the right argv and surfaces errors as `GitCommandError`. |
| `tests/executor/test_filesystem_queue.py` | Frontmatter parsing + `current_pbi` / `inbox_pbis` / `pick_next` over a populated fake repo, including severity-lane ordering. |
| `tests/executor/test_movements.py` | Each `move_*` helper performs `git mv`, updates frontmatter, commits, pushes; PBI lands in the destination folder and the entry file's `status:` is updated. |
| `tests/executor/test_claude_spawn.py` | `spawn_claude_p` invokes the fake binary, captures output, and `classify_outcome` returns the right `ClaudeOutcome.kind` for each of the four cases. |
| `tests/executor/test_loop.py` | Full iteration scenarios: `current` empty → claim from inbox; `current` occupied → spawn Ralph; PR-created outcome → move to pending-pr; stuck outcome → move to blocked; partial outcome → PBI stays in current. Plan-8 and Plan-9 stub call sites are asserted via spies. |
| `tests/executor/test_cli.py` | `main` returns 0 on KeyboardInterrupt, parses `--once`, `--repo`, `--log-level`. |

---

## Task 1 — Toolchain wiring + `ralph-executor` console script

**Files**
- Modify: `pyproject.toml`
- Create: `tests/executor/__init__.py`

**Steps**

- [ ] 1. Confirm prerequisite plans are merged. The executor depends on:
  - Plan 1 (workspace + samples) — provides the validator and the sample PBI fixtures.
  - Plan 2 (ralph-queue branch + `scripts.ado_client`) — not directly imported here, but the test repo bootstrap in `conftest.py` mirrors the layout Plan 2's runbook produces.
  - Plan 5 (ado-pr skill) — `PROMPT.md` references the skill, but the executor itself does NOT call the skill. The skill is invoked by Claude inside the spawned session.
  - Plan 6 (PROMPT.md) — the executor reads `prompt/PROMPT.md` and passes its content as part of the `claude -p` invocation.
  Run:
  ```
  uv run pytest tests/test_workspace_samples.py tests/test_ado_client.py tests/test_setup_ralph_queue.py tests/skills/test_ralph_add.py -v
  ```
  Expected: every test passes. If anything fails, STOP.

- [ ] 2. Modify `pyproject.toml` to add the `ralph-executor` console script entry. The `[project]` table already exists from Plan 1; append (or merge) the `[project.scripts]` section so it reads exactly:
  ```toml
  [project.scripts]
  ralph-executor = "ralph_executor.cli:main"
  ```
  Do not change any other section. After editing, re-sync:
  ```
  uv sync
  ```
  Expected: `uv` resolves with no errors. The console script is now installable into the venv but it imports a module that does not exist yet (`ralph_executor.cli`), so it cannot be invoked yet.

- [ ] 3. Create `tests/executor/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 4. Confirm ruff + mypy still pass on the empty subtree:
  ```
  uv run ruff check pyproject.toml tests/executor
  uv run mypy ralph_executor scripts tests
  ```
  Expected: ruff: `All checks passed!`. Mypy: `Success: no issues found in N source files`. (`ralph_executor/__init__.py` is still the Plan 1 one-line stub; tests/executor is empty.)

- [ ] 5. Commit:
  ```
  git add pyproject.toml tests/executor/__init__.py
  git commit -m "chore(executor): scaffold ralph-executor console script + tests/executor package"
  ```
  Expected: commit succeeds.

---

## Task 2 — Canonical types (`ralph_executor/types.py`)

**Files**
- Create: `tests/executor/test_types.py`
- Create: `ralph_executor/types.py`

**Steps**

- [ ] 1. Write the failing test at `tests/executor/test_types.py`:
  ```python
  """Tests for ``ralph_executor.types``.

  The types are the shared cross-plan contract — Plans 8, 9, and 10 import
  the same module. These tests pin the spelling and the immutability.
  """
  from __future__ import annotations

  from datetime import datetime, timezone
  from pathlib import Path

  import pytest

  from ralph_executor.types import (
      PBI,
      Severity,
      PBIStatus,
      PBIType,
  )


  def _make_pbi(**overrides: object) -> PBI:
      base: dict[str, object] = {
          "id": "WI-1234",
          "type": "feature",
          "status": "inbox",
          "severity": "normal",
          "attempts": 0,
          "created_at": datetime(2026, 5, 24, 9, 15, tzinfo=timezone.utc),
          "updated_at": datetime(2026, 5, 24, 9, 15, tzinfo=timezone.utc),
          "path": Path("/tmp/ralph/inbox/WI-1234"),
      }
      base.update(overrides)
      return PBI(**base)  # type: ignore[arg-type]


  def test_pbi_constructs_with_canonical_fields() -> None:
      pbi = _make_pbi()
      assert pbi.id == "WI-1234"
      assert pbi.type == "feature"
      assert pbi.status == "inbox"
      assert pbi.severity == "normal"
      assert pbi.attempts == 0
      assert pbi.path == Path("/tmp/ralph/inbox/WI-1234")


  def test_pbi_is_frozen() -> None:
      pbi = _make_pbi()
      with pytest.raises(Exception):  # FrozenInstanceError is dataclasses.FrozenInstanceError
          pbi.id = "WI-9999"  # type: ignore[misc]


  def test_pbi_equality_uses_all_fields() -> None:
      a = _make_pbi()
      b = _make_pbi()
      assert a == b
      c = _make_pbi(id="WI-5555")
      assert a != c


  def test_literal_aliases_are_exported() -> None:
      # Reaching the names is the assertion — mypy enforces the literal values.
      assert PBIType is not None
      assert PBIStatus is not None
      assert Severity is not None
  ```

- [ ] 2. Run the failing test:
  ```
  uv run pytest tests/executor/test_types.py -v
  ```
  Expected: `ModuleNotFoundError: No module named 'ralph_executor.types'`.

- [ ] 3. Implement `ralph_executor/types.py` with the exact content below. The literals match the orchestrator's [Cross-plan integration points](./2026-05-24-00-orchestrator.md#cross-plan-integration-points) verbatim:
  ```python
  """Shared typed shapes for the executor and its extensions.

  Plans 8 (sweep), 9 (safety controls), and 10 (supervisor skills) import
  ``PBI`` from this module rather than re-defining it locally. The Literal
  aliases mirror the canonical schema in
  ``docs/superpowers/plans/2026-05-24-00-orchestrator.md``.
  """
  from __future__ import annotations

  from dataclasses import dataclass
  from datetime import datetime
  from pathlib import Path
  from typing import Literal

  PBIType = Literal["feature", "bug", "pr-feedback"]
  PBIStatus = Literal["inbox", "current", "pending-pr", "done", "blocked", "archive"]
  Severity = Literal["critical", "high", "normal", "low"]


  @dataclass(frozen=True)
  class PBI:
      """A Product Backlog Item as it lives on disk under ``.ralph/<state>/``.

      ``path`` is the absolute path to the PBI's directory in the queue
      checkout — i.e. ``<repo>/.ralph/<status>/<id>/``.
      """

      id: str
      type: PBIType
      status: PBIStatus
      severity: Severity
      attempts: int
      created_at: datetime
      updated_at: datetime
      path: Path
  ```

- [ ] 4. Re-run the test; it must pass:
  ```
  uv run pytest tests/executor/test_types.py -v
  ```
  Expected: all four tests pass.

- [ ] 5. Run ruff + mypy:
  ```
  uv run ruff check ralph_executor/types.py tests/executor/test_types.py
  uv run mypy ralph_executor/types.py tests/executor/test_types.py
  ```
  Expected: both report success.

- [ ] 6. Commit:
  ```
  git add ralph_executor/types.py tests/executor/test_types.py
  git commit -m "feat(executor): add canonical PBI types shared with sweep/safety/supervisor plans"
  ```
  Expected: commit succeeds.

---

## Task 3 — Executor configuration (`ralph_executor/config.py`)

**Files**
- Create: `tests/executor/test_config.py`
- Create: `ralph_executor/config.py`

**Steps**

- [ ] 1. Write the failing test at `tests/executor/test_config.py`:
  ```python
  """Tests for ``ralph_executor.config``."""
  from __future__ import annotations

  import logging
  from pathlib import Path

  import pytest

  from ralph_executor.config import (
      ExecutorConfig,
      ConfigError,
      load_config,
  )


  @pytest.fixture
  def git_repo(tmp_path: Path) -> Path:
      repo = tmp_path / "repo"
      repo.mkdir()
      (repo / ".git").mkdir()
      return repo


  @pytest.fixture
  def env_minimal(
      monkeypatch: pytest.MonkeyPatch, git_repo: Path
  ) -> Path:
      monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))
      monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
      for var in (
          "RALPH_QUEUE_BRANCH",
          "RALPH_MAIN_BRANCH",
          "RALPH_MAX_ATTEMPTS",
          "RALPH_LOG_LEVEL",
          "RALPH_ITERATION_SLEEP_SECONDS",
          "RALPH_CLAUDE_BINARY",
      ):
          monkeypatch.delenv(var, raising=False)
      return git_repo


  def test_load_config_uses_defaults(env_minimal: Path) -> None:
      cfg = load_config()
      assert cfg.repo_path == env_minimal
      assert cfg.queue_branch == "ralph-queue"
      assert cfg.main_branch == "main"
      assert cfg.max_attempts == 3
      assert cfg.log_level == logging.INFO
      assert cfg.iteration_sleep_seconds == 30.0
      assert cfg.claude_binary == "claude"
      assert cfg.anthropic_api_key == "fake-key"


  def test_load_config_overrides_via_env(
      monkeypatch: pytest.MonkeyPatch, env_minimal: Path
  ) -> None:
      monkeypatch.setenv("RALPH_QUEUE_BRANCH", "custom-queue")
      monkeypatch.setenv("RALPH_MAIN_BRANCH", "trunk")
      monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "5")
      monkeypatch.setenv("RALPH_LOG_LEVEL", "DEBUG")
      monkeypatch.setenv("RALPH_ITERATION_SLEEP_SECONDS", "0.5")
      monkeypatch.setenv("RALPH_CLAUDE_BINARY", "/usr/local/bin/claude")
      cfg = load_config()
      assert cfg.queue_branch == "custom-queue"
      assert cfg.main_branch == "trunk"
      assert cfg.max_attempts == 5
      assert cfg.log_level == logging.DEBUG
      assert cfg.iteration_sleep_seconds == 0.5
      assert cfg.claude_binary == "/usr/local/bin/claude"


  def test_load_config_missing_repo_path(
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      monkeypatch.delenv("RALPH_REPO_PATH", raising=False)
      monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
      with pytest.raises(ConfigError, match="RALPH_REPO_PATH"):
          load_config()


  def test_load_config_missing_anthropic_key(
      monkeypatch: pytest.MonkeyPatch, git_repo: Path
  ) -> None:
      monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))
      monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
      with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
          load_config()


  def test_load_config_repo_path_not_a_directory(
      monkeypatch: pytest.MonkeyPatch, tmp_path: Path
  ) -> None:
      missing = tmp_path / "nope"
      monkeypatch.setenv("RALPH_REPO_PATH", str(missing))
      monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
      with pytest.raises(ConfigError, match="not a directory"):
          load_config()


  def test_load_config_repo_path_not_a_git_repo(
      monkeypatch: pytest.MonkeyPatch, tmp_path: Path
  ) -> None:
      plain = tmp_path / "plain"
      plain.mkdir()
      monkeypatch.setenv("RALPH_REPO_PATH", str(plain))
      monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
      with pytest.raises(ConfigError, match="not a git repository"):
          load_config()


  def test_load_config_invalid_max_attempts(
      monkeypatch: pytest.MonkeyPatch, env_minimal: Path
  ) -> None:
      monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "not-a-number")
      with pytest.raises(ConfigError, match="RALPH_MAX_ATTEMPTS"):
          load_config()


  def test_load_config_invalid_log_level(
      monkeypatch: pytest.MonkeyPatch, env_minimal: Path
  ) -> None:
      monkeypatch.setenv("RALPH_LOG_LEVEL", "VERBOSE")
      with pytest.raises(ConfigError, match="RALPH_LOG_LEVEL"):
          load_config()


  def test_executor_config_is_frozen(env_minimal: Path) -> None:
      cfg = load_config()
      with pytest.raises(Exception):
          cfg.queue_branch = "other"  # type: ignore[misc]
  ```

- [ ] 2. Run the failing test:
  ```
  uv run pytest tests/executor/test_config.py -v
  ```
  Expected: `ModuleNotFoundError: No module named 'ralph_executor.config'`.

- [ ] 3. Implement `ralph_executor/config.py` with the exact content below:
  ```python
  """Executor configuration loaded from environment variables.

  All runtime knobs live on the immutable ``ExecutorConfig`` dataclass so
  the loop driver and the queue source can be tested with hand-rolled
  config objects without any environment dependency.
  """
  from __future__ import annotations

  import logging
  import os
  from dataclasses import dataclass
  from pathlib import Path

  DEFAULT_QUEUE_BRANCH = "ralph-queue"
  DEFAULT_MAIN_BRANCH = "main"
  DEFAULT_MAX_ATTEMPTS = 3
  DEFAULT_LOG_LEVEL = "INFO"
  DEFAULT_ITERATION_SLEEP_SECONDS = 30.0
  DEFAULT_CLAUDE_BINARY = "claude"

  _VALID_LOG_LEVEL_NAMES = frozenset(
      {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
  )


  class ConfigError(RuntimeError):
      """Raised when ``load_config`` cannot resolve a valid configuration."""


  @dataclass(frozen=True)
  class ExecutorConfig:
      """Immutable runtime configuration for the executor.

      Created once at process start and passed through every public entry
      point of the loop driver, queue source, and claude-spawn helpers.
      """

      repo_path: Path
      queue_branch: str
      main_branch: str
      max_attempts: int
      log_level: int
      iteration_sleep_seconds: float
      claude_binary: str
      anthropic_api_key: str


  def _require_env(name: str) -> str:
      value = os.environ.get(name, "").strip()
      if not value:
          raise ConfigError(
              f"environment variable {name} is required"
          )
      return value


  def _parse_int(name: str, default: int) -> int:
      raw = os.environ.get(name)
      if raw is None or raw.strip() == "":
          return default
      try:
          return int(raw)
      except ValueError as exc:
          raise ConfigError(
              f"{name}={raw!r} is not an integer: {exc}"
          ) from exc


  def _parse_float(name: str, default: float) -> float:
      raw = os.environ.get(name)
      if raw is None or raw.strip() == "":
          return default
      try:
          return float(raw)
      except ValueError as exc:
          raise ConfigError(
              f"{name}={raw!r} is not a float: {exc}"
          ) from exc


  def _parse_log_level(name: str, default: str) -> int:
      raw = (os.environ.get(name) or default).strip().upper()
      if raw not in _VALID_LOG_LEVEL_NAMES:
          raise ConfigError(
              f"{name}={raw!r} not in {sorted(_VALID_LOG_LEVEL_NAMES)}"
          )
      return logging.getLevelName(raw)  # type: ignore[return-value]


  def _validate_repo_path(path: Path) -> Path:
      if not path.exists():
          raise ConfigError(f"RALPH_REPO_PATH={path} not a directory: does not exist")
      if not path.is_dir():
          raise ConfigError(f"RALPH_REPO_PATH={path} not a directory")
      if not (path / ".git").exists():
          raise ConfigError(
              f"RALPH_REPO_PATH={path} not a git repository (no .git/ entry)"
          )
      return path.resolve()


  def load_config() -> ExecutorConfig:
      """Read environment variables and produce a validated ``ExecutorConfig``."""
      repo_raw = _require_env("RALPH_REPO_PATH")
      anthropic_key = _require_env("ANTHROPIC_API_KEY")
      repo_path = _validate_repo_path(Path(repo_raw))
      queue_branch = os.environ.get("RALPH_QUEUE_BRANCH") or DEFAULT_QUEUE_BRANCH
      main_branch = os.environ.get("RALPH_MAIN_BRANCH") or DEFAULT_MAIN_BRANCH
      max_attempts = _parse_int("RALPH_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)
      log_level = _parse_log_level("RALPH_LOG_LEVEL", DEFAULT_LOG_LEVEL)
      sleep_seconds = _parse_float(
          "RALPH_ITERATION_SLEEP_SECONDS", DEFAULT_ITERATION_SLEEP_SECONDS
      )
      claude_binary = (
          os.environ.get("RALPH_CLAUDE_BINARY") or DEFAULT_CLAUDE_BINARY
      )
      return ExecutorConfig(
          repo_path=repo_path,
          queue_branch=queue_branch,
          main_branch=main_branch,
          max_attempts=max_attempts,
          log_level=log_level,
          iteration_sleep_seconds=sleep_seconds,
          claude_binary=claude_binary,
          anthropic_api_key=anthropic_key,
      )
  ```

- [ ] 4. Re-run the test; it must pass:
  ```
  uv run pytest tests/executor/test_config.py -v
  ```
  Expected: all nine tests pass.

- [ ] 5. Run ruff + mypy:
  ```
  uv run ruff check ralph_executor/config.py tests/executor/test_config.py
  uv run mypy ralph_executor/config.py tests/executor/test_config.py
  ```
  Expected: both report success.

- [ ] 6. Commit:
  ```
  git add ralph_executor/config.py tests/executor/test_config.py
  git commit -m "feat(executor): add ExecutorConfig + load_config env-driven loader"
  ```
  Expected: commit succeeds.

---

## Task 4 — Shared test fixtures (`tests/executor/conftest.py`)

**Files**
- Create: `tests/executor/conftest.py`

**Steps**

- [ ] 1. The remaining test modules all need the same scaffolding: a real, local git repo (bare + worktree pair, with `main` and `ralph-queue` branches), a sample PBI inside `.ralph/inbox/`, a stand-in claude binary, and an `ExecutorConfig` pointing at all of those. Putting them in `conftest.py` keeps each test module focused on its module's behaviour.

  Write `tests/executor/conftest.py` with the exact content below:
  ```python
  """Shared fixtures for ``ralph_executor`` tests.

  Every test that touches the queue or the loop needs a local git repo
  with both ``main`` and ``ralph-queue`` plus a stand-in for the
  ``claude`` binary. Building those in one place keeps each test focused.
  """
  from __future__ import annotations

  import os
  import stat
  import subprocess
  from collections.abc import Iterator
  from pathlib import Path
  from textwrap import dedent

  import pytest

  from ralph_executor.config import ExecutorConfig


  def _git(cwd: Path, *args: str) -> str:
      result = subprocess.run(
          ["git", *args],
          cwd=str(cwd),
          check=True,
          capture_output=True,
          text=True,
      )
      return result.stdout


  @pytest.fixture
  def fake_repo(tmp_path: Path) -> Iterator[Path]:
      """Initialise a local bare + worktree pair with main and ralph-queue."""
      bare = tmp_path / "remote.git"
      work = tmp_path / "work"

      subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
      subprocess.run(["git", "init", str(work)], check=True, capture_output=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main commit")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")
      _git(work, "checkout", "-b", "ralph-queue")
      # The queue branch starts with an empty ``.ralph/`` tree.
      (work / ".ralph" / "inbox").mkdir(parents=True)
      (work / ".ralph" / "current").mkdir()
      (work / ".ralph" / "pending-pr").mkdir()
      (work / ".ralph" / "done").mkdir()
      (work / ".ralph" / "blocked").mkdir()
      # Git ignores empty directories; drop ``.gitkeep`` files.
      for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
          (work / ".ralph" / sub / ".gitkeep").write_text("", encoding="utf-8")
      _git(work, "add", ".ralph")
      _git(work, "commit", "-m", "chore(queue): bootstrap .ralph/ tree")
      _git(work, "push", "-u", "origin", "ralph-queue")
      _git(work, "checkout", "main")
      yield work


  def write_sample_pbi(
      repo: Path,
      *,
      pbi_id: str = "WI-1234",
      pbi_type: str = "feature",
      severity: str = "normal",
      created_at: str = "2026-05-24T09:15:00+00:00",
      where: str = "inbox",
  ) -> Path:
      """Write a minimal feature PBI directory into ``.ralph/<where>/<pbi_id>``.

      Returns the absolute path to the new directory. Assumes the repo is
      currently on ``ralph-queue`` (callers checkout before calling).
      """
      pbi_dir = repo / ".ralph" / where / pbi_id
      pbi_dir.mkdir(parents=True, exist_ok=True)
      if pbi_type == "bug":
          entry_name = "BUG.md"
          sibling = "REPRODUCE.md"
      elif pbi_type == "pr-feedback":
          entry_name = "FEEDBACK.md"
          sibling = "PR-LINK.md"
      else:
          entry_name = "PBI.md"
          sibling = "PLAN.md"

      status = where if where in {"inbox", "current", "pending-pr", "done", "blocked"} else "inbox"
      frontmatter = dedent(
          f"""\
          ---
          id: {pbi_id}
          type: {pbi_type}
          status: {status}
          severity: {severity}
          attempts: 0
          created_at: {created_at}
          updated_at: {created_at}
          ---

          # {pbi_id} sample body
          """
      )
      (pbi_dir / entry_name).write_text(frontmatter, encoding="utf-8")
      (pbi_dir / sibling).write_text(f"# {sibling}\n", encoding="utf-8")
      if pbi_type == "pr-feedback":
          (pbi_dir / "ORIGINAL.md").write_text("# original\n", encoding="utf-8")
      (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
      return pbi_dir


  @pytest.fixture
  def sample_pbi(fake_repo: Path) -> Path:
      """Write a minimal feature PBI into ``.ralph/inbox/WI-1234`` and commit it."""
      _git(fake_repo, "checkout", "ralph-queue")
      pbi_dir = write_sample_pbi(fake_repo)
      _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
      _git(fake_repo, "commit", "-m", "feat(ralph-queue): add WI-1234")
      _git(fake_repo, "push", "origin", "ralph-queue")
      _git(fake_repo, "checkout", "main")
      return pbi_dir


  @pytest.fixture
  def fake_claude_binary(tmp_path: Path) -> Path:
      """Write a stand-in ``claude`` script that echoes its argv to stdout.

      The default script returns exit code 0 with empty stdout — tests
      override its content by writing a new script at the same path.
      """
      bin_dir = tmp_path / "bin"
      bin_dir.mkdir()
      script = bin_dir / "claude"
      script.write_text(
          "#!/usr/bin/env python3\n"
          "import sys\n"
          "sys.exit(0)\n",
          encoding="utf-8",
      )
      script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
      return script


  def write_claude_script(path: Path, body: str) -> None:
      """Overwrite the stand-in ``claude`` script with a custom Python body."""
      shebang = "#!/usr/bin/env python3\n"
      path.write_text(shebang + body, encoding="utf-8")
      path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


  @pytest.fixture
  def cfg_for_repo(
      fake_repo: Path, fake_claude_binary: Path
  ) -> ExecutorConfig:
      """Build an ExecutorConfig pointing at the fake repo + fake claude."""
      return ExecutorConfig(
          repo_path=fake_repo,
          queue_branch="ralph-queue",
          main_branch="main",
          max_attempts=3,
          log_level=20,  # logging.INFO
          iteration_sleep_seconds=0.0,
          claude_binary=str(fake_claude_binary),
          anthropic_api_key="fake-key",
      )


  @pytest.fixture(autouse=True)
  def _claude_path_for_subprocess(
      monkeypatch: pytest.MonkeyPatch, fake_claude_binary: Path
  ) -> None:
      """Prepend the stand-in ``claude`` to PATH for any spawn under test."""
      monkeypatch.setenv("PATH", f"{fake_claude_binary.parent}{os.pathsep}{os.environ.get('PATH', '')}")
  ```

- [ ] 2. Verify the fixtures load without test errors by collecting an empty test:
  ```
  uv run pytest tests/executor/ --collect-only -q
  ```
  Expected: collection succeeds (no `ImportError`); only `test_types.py` and `test_config.py` items are listed. The conftest is parsed but no fixtures are instantiated yet.

- [ ] 3. Run ruff + mypy on the new conftest:
  ```
  uv run ruff check tests/executor/conftest.py
  uv run mypy tests/executor/conftest.py
  ```
  Expected: both report success.

- [ ] 4. Commit:
  ```
  git add tests/executor/conftest.py
  git commit -m "test(executor): add shared fixtures (fake_repo, sample_pbi, fake_claude_binary, cfg_for_repo)"
  ```
  Expected: commit succeeds.

---

## Task 5 — Git operations (`ralph_executor/git_ops.py`)

**Files**
- Create: `tests/executor/test_git_ops.py`
- Create: `ralph_executor/git_ops.py`

**Steps**

- [ ] 1. Write the failing test at `tests/executor/test_git_ops.py`:
  ```python
  """Tests for ``ralph_executor.git_ops``.

  Each helper is exercised against the ``fake_repo`` fixture (a real local
  bare + worktree pair). The tests assert observable git state — refs,
  HEAD, commit shas — rather than mocking subprocess.
  """
  from __future__ import annotations

  import subprocess
  from pathlib import Path

  import pytest

  from ralph_executor import git_ops
  from ralph_executor.git_ops import GitCommandError


  def _git(cwd: Path, *args: str) -> str:
      return subprocess.run(
          ["git", *args],
          cwd=str(cwd),
          check=True,
          capture_output=True,
          text=True,
      ).stdout


  def test_current_branch_returns_active_branch(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "main")
      assert git_ops.current_branch(fake_repo) == "main"
      _git(fake_repo, "checkout", "ralph-queue")
      assert git_ops.current_branch(fake_repo) == "ralph-queue"


  def test_branch_exists_true_for_local_and_remote(fake_repo: Path) -> None:
      assert git_ops.branch_exists(fake_repo, "main") is True
      assert git_ops.branch_exists(fake_repo, "ralph-queue") is True
      assert git_ops.branch_exists(fake_repo, "nope") is False


  def test_is_branch_remote_for_origin(fake_repo: Path) -> None:
      assert git_ops.is_branch_remote(fake_repo, "main") is True
      assert git_ops.is_branch_remote(fake_repo, "ralph-queue") is True
      assert git_ops.is_branch_remote(fake_repo, "nope") is False


  def test_checkout_switches_branch(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "main")
      git_ops.checkout(fake_repo, "ralph-queue")
      assert git_ops.current_branch(fake_repo) == "ralph-queue"


  def test_checkout_unknown_branch_raises(fake_repo: Path) -> None:
      with pytest.raises(GitCommandError):
          git_ops.checkout(fake_repo, "definitely-not-a-branch")


  def test_checkout_new_creates_branch_off_head(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "main")
      git_ops.checkout_new(fake_repo, "ralph/WI-9999")
      assert git_ops.current_branch(fake_repo) == "ralph/WI-9999"
      assert git_ops.branch_exists(fake_repo, "ralph/WI-9999") is True


  def test_fetch_does_not_modify_working_tree(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "main")
      before = _git(fake_repo, "rev-parse", "HEAD").strip()
      git_ops.fetch(fake_repo)
      after = _git(fake_repo, "rev-parse", "HEAD").strip()
      assert before == after


  def test_pull_is_a_noop_when_nothing_to_pull(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      before = _git(fake_repo, "rev-parse", "HEAD").strip()
      git_ops.pull(fake_repo, "ralph-queue")
      after = _git(fake_repo, "rev-parse", "HEAD").strip()
      assert before == after


  def test_commit_all_creates_a_commit(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      (fake_repo / "scratch.txt").write_text("hello", encoding="utf-8")
      before = _git(fake_repo, "rev-parse", "HEAD").strip()
      sha = git_ops.commit_all(fake_repo, "test: add scratch")
      after = _git(fake_repo, "rev-parse", "HEAD").strip()
      assert sha == after
      assert before != after


  def test_commit_all_no_changes_returns_head(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      head = _git(fake_repo, "rev-parse", "HEAD").strip()
      sha = git_ops.commit_all(fake_repo, "test: no changes")
      assert sha == head


  def test_push_advances_remote_ref(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      (fake_repo / "scratch2.txt").write_text("y", encoding="utf-8")
      git_ops.commit_all(fake_repo, "test: scratch2")
      git_ops.push(fake_repo, "ralph-queue")
      remote_sha = _git(fake_repo, "ls-remote", "origin", "ralph-queue").split()[0]
      local_sha = _git(fake_repo, "rev-parse", "HEAD").strip()
      assert local_sha == remote_sha


  def test_mv_moves_a_file(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      src = fake_repo / "src.txt"
      src.write_text("x", encoding="utf-8")
      git_ops.add(fake_repo, src)
      git_ops.commit_all(fake_repo, "test: add src")
      dst = fake_repo / "subdir" / "dst.txt"
      git_ops.mv(fake_repo, src, dst)
      assert not src.exists()
      assert dst.is_file()


  def test_rev_parse_head_returns_sha(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "main")
      sha = git_ops.rev_parse_head(fake_repo)
      assert len(sha) == 40
      assert all(c in "0123456789abcdef" for c in sha)


  def test_git_command_error_carries_argv_and_stderr(tmp_path: Path) -> None:
      with pytest.raises(GitCommandError) as excinfo:
          git_ops.current_branch(tmp_path)  # tmp_path is not a git repo
      assert "git" in str(excinfo.value).lower()
  ```

- [ ] 2. Run the failing test:
  ```
  uv run pytest tests/executor/test_git_ops.py -v
  ```
  Expected: `ModuleNotFoundError: No module named 'ralph_executor.git_ops'`.

- [ ] 3. Implement `ralph_executor/git_ops.py` with the exact content below:
  ```python
  """Thin subprocess wrappers around the ``git`` binary.

  Every helper goes through ``_run_git`` so tests have a single point to
  intercept (the conftest uses real subprocess against a local fake repo,
  but Plans 8 and 9 may want to spy on calls via monkeypatch).
  """
  from __future__ import annotations

  import logging
  import subprocess
  from pathlib import Path

  log = logging.getLogger(__name__)


  class GitCommandError(RuntimeError):
      """Raised when a git invocation exits non-zero."""

      def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
          super().__init__(
              f"git command {argv!r} exited {returncode}: {stderr.strip()}"
          )
          self.argv = argv
          self.returncode = returncode
          self.stderr = stderr


  def _run_git(
      repo: Path,
      *args: str,
      check: bool = True,
      capture: bool = True,
  ) -> subprocess.CompletedProcess[str]:
      argv = ["git", *args]
      log.debug("git: cwd=%s argv=%s", repo, argv)
      result = subprocess.run(
          argv,
          cwd=str(repo),
          check=False,
          capture_output=capture,
          text=True,
      )
      if check and result.returncode != 0:
          raise GitCommandError(argv, result.returncode, result.stderr)
      return result


  def current_branch(repo: Path) -> str:
      """Return the name of the currently checked-out branch."""
      return _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


  def fetch(repo: Path, remote: str = "origin") -> None:
      """Run ``git fetch <remote>``."""
      _run_git(repo, "fetch", remote)


  def pull(repo: Path, branch: str, remote: str = "origin") -> None:
      """Run ``git pull --ff-only <remote> <branch>`` against the current checkout."""
      _run_git(repo, "pull", "--ff-only", remote, branch)


  def checkout(repo: Path, branch: str) -> None:
      """Run ``git checkout <branch>`` (must already exist)."""
      _run_git(repo, "checkout", branch)


  def checkout_new(repo: Path, branch: str) -> None:
      """Run ``git checkout -b <branch>`` off the current HEAD."""
      _run_git(repo, "checkout", "-b", branch)


  def branch_exists(repo: Path, branch: str) -> bool:
      """Return True if ``branch`` exists either locally or on ``origin``."""
      local = _run_git(
          repo, "branch", "--list", branch, check=False
      ).stdout.strip()
      if local:
          return True
      remote = _run_git(
          repo, "branch", "-r", "--list", f"origin/{branch}", check=False
      ).stdout.strip()
      return bool(remote)


  def is_branch_remote(repo: Path, branch: str) -> bool:
      """Return True if ``origin/<branch>`` exists."""
      remote = _run_git(
          repo, "branch", "-r", "--list", f"origin/{branch}", check=False
      ).stdout.strip()
      return bool(remote)


  def add(repo: Path, path: Path) -> None:
      """Run ``git add <path>``."""
      _run_git(repo, "add", str(path.relative_to(repo)))


  def commit_all(repo: Path, message: str) -> str:
      """Stage tracked changes and commit. Returns the new HEAD sha.

      If there is nothing to commit, returns the current HEAD sha unchanged.
      """
      _run_git(repo, "add", "-A")
      status = _run_git(repo, "status", "--porcelain").stdout.strip()
      if not status:
          return rev_parse_head(repo)
      _run_git(repo, "commit", "-m", message)
      return rev_parse_head(repo)


  def push(repo: Path, branch: str, remote: str = "origin") -> None:
      """Run ``git push <remote> <branch>``."""
      _run_git(repo, "push", remote, branch)


  def mv(repo: Path, src: Path, dst: Path) -> None:
      """Run ``git mv <src> <dst>``, creating ``dst``'s parent dirs if needed."""
      dst.parent.mkdir(parents=True, exist_ok=True)
      _run_git(
          repo,
          "mv",
          str(src.relative_to(repo)),
          str(dst.relative_to(repo)),
      )


  def rev_parse_head(repo: Path) -> str:
      """Return the 40-char sha of the current HEAD."""
      return _run_git(repo, "rev-parse", "HEAD").stdout.strip()
  ```

- [ ] 4. Re-run the test; it must pass:
  ```
  uv run pytest tests/executor/test_git_ops.py -v
  ```
  Expected: all 14 tests pass.

- [ ] 5. Run ruff + mypy:
  ```
  uv run ruff check ralph_executor/git_ops.py tests/executor/test_git_ops.py
  uv run mypy ralph_executor/git_ops.py tests/executor/test_git_ops.py
  ```
  Expected: both report success.

- [ ] 6. Commit:
  ```
  git add ralph_executor/git_ops.py tests/executor/test_git_ops.py
  git commit -m "feat(executor): add git_ops subprocess wrappers (fetch/pull/checkout/commit/push/mv)"
  ```
  Expected: commit succeeds.

---

## Task 6 — Filesystem queue source (`ralph_executor/queue/filesystem.py`)

**Files**
- Create: `ralph_executor/queue/__init__.py`
- Create: `tests/executor/test_filesystem_queue.py`
- Create: `ralph_executor/queue/filesystem.py`

**Steps**

- [ ] 1. Create the queue package marker. Write `ralph_executor/queue/__init__.py` containing exactly:
  ```python
  """Queue source + folder movement primitives for the executor."""
  ```

- [ ] 2. Write the failing test at `tests/executor/test_filesystem_queue.py`:
  ```python
  """Tests for ``ralph_executor.queue.filesystem``."""
  from __future__ import annotations

  import subprocess
  from datetime import datetime, timezone
  from pathlib import Path

  import pytest

  from ralph_executor.config import ExecutorConfig
  from ralph_executor.queue.filesystem import (
      FilesystemQueueSource,
      QueueError,
      parse_pbi_directory,
  )

  from tests.executor.conftest import write_sample_pbi


  def _git(cwd: Path, *args: str) -> str:
      return subprocess.run(
          ["git", *args],
          cwd=str(cwd),
          check=True,
          capture_output=True,
          text=True,
      ).stdout


  def test_parse_pbi_directory_reads_feature(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-1234", pbi_type="feature")
      pbi = parse_pbi_directory(pbi_dir, status="inbox")
      assert pbi.id == "WI-1234"
      assert pbi.type == "feature"
      assert pbi.status == "inbox"
      assert pbi.severity == "normal"
      assert pbi.attempts == 0
      assert pbi.path == pbi_dir
      assert pbi.created_at == datetime(2026, 5, 24, 9, 15, tzinfo=timezone.utc)


  def test_parse_pbi_directory_reads_bug(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      pbi_dir = write_sample_pbi(
          fake_repo, pbi_id="BUG-1", pbi_type="bug", severity="critical"
      )
      pbi = parse_pbi_directory(pbi_dir, status="inbox")
      assert pbi.type == "bug"
      assert pbi.severity == "critical"


  def test_parse_pbi_directory_reads_pr_feedback(fake_repo: Path) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      pbi_dir = write_sample_pbi(
          fake_repo,
          pbi_id="PR-feedback-WI-1234-r1",
          pbi_type="pr-feedback",
          severity="high",
      )
      pbi = parse_pbi_directory(pbi_dir, status="inbox")
      assert pbi.type == "pr-feedback"
      assert pbi.severity == "high"


  def test_parse_pbi_directory_missing_entry_file_raises(
      fake_repo: Path,
  ) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      empty = fake_repo / ".ralph" / "inbox" / "NO-FILES"
      empty.mkdir(parents=True)
      with pytest.raises(QueueError, match="no entry file"):
          parse_pbi_directory(empty, status="inbox")


  def test_current_pbi_returns_none_when_empty(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      source = FilesystemQueueSource(cfg_for_repo)
      assert source.current_pbi() is None


  def test_current_pbi_returns_the_one_entry(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      write_sample_pbi(fake_repo, pbi_id="WI-42", where="current")
      _git(fake_repo, "add", ".ralph/current/WI-42")
      _git(fake_repo, "commit", "-m", "current: WI-42")
      source = FilesystemQueueSource(cfg_for_repo)
      pbi = source.current_pbi()
      assert pbi is not None
      assert pbi.id == "WI-42"
      assert pbi.status == "current"


  def test_current_pbi_raises_when_more_than_one(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      write_sample_pbi(fake_repo, pbi_id="WI-1", where="current")
      write_sample_pbi(fake_repo, pbi_id="WI-2", where="current")
      _git(fake_repo, "add", ".ralph/current")
      _git(fake_repo, "commit", "-m", "two in current")
      source = FilesystemQueueSource(cfg_for_repo)
      with pytest.raises(QueueError, match="more than one"):
          source.current_pbi()


  def test_inbox_pbis_returns_all_in_priority_order(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      # Low-priority feature, normal feature, critical bug,
      # high pr-feedback. Expected order: pr-feedback, critical,
      # normal feature, low feature.
      write_sample_pbi(
          fake_repo, pbi_id="WI-low", pbi_type="feature", severity="low",
          created_at="2026-05-20T00:00:00+00:00",
      )
      write_sample_pbi(
          fake_repo, pbi_id="WI-normal", pbi_type="feature", severity="normal",
          created_at="2026-05-21T00:00:00+00:00",
      )
      write_sample_pbi(
          fake_repo, pbi_id="BUG-crit", pbi_type="bug", severity="critical",
          created_at="2026-05-22T00:00:00+00:00",
      )
      write_sample_pbi(
          fake_repo,
          pbi_id="PR-feedback-WI-1-r1",
          pbi_type="pr-feedback",
          severity="high",
          created_at="2026-05-23T00:00:00+00:00",
      )
      _git(fake_repo, "add", ".ralph/inbox")
      _git(fake_repo, "commit", "-m", "four pbis")
      source = FilesystemQueueSource(cfg_for_repo)
      pbis = source.inbox_pbis()
      assert [p.id for p in pbis] == [
          "PR-feedback-WI-1-r1",
          "BUG-crit",
          "WI-normal",
          "WI-low",
      ]


  def test_pick_next_returns_highest_priority(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      write_sample_pbi(
          fake_repo, pbi_id="WI-low", pbi_type="feature", severity="low",
          created_at="2026-05-20T00:00:00+00:00",
      )
      write_sample_pbi(
          fake_repo,
          pbi_id="PR-feedback-WI-1-r1",
          pbi_type="pr-feedback",
          severity="high",
          created_at="2026-05-23T00:00:00+00:00",
      )
      _git(fake_repo, "add", ".ralph/inbox")
      _git(fake_repo, "commit", "-m", "two pbis")
      source = FilesystemQueueSource(cfg_for_repo)
      pick = source.pick_next()
      assert pick is not None
      assert pick.id == "PR-feedback-WI-1-r1"


  def test_pick_next_returns_none_when_inbox_empty(
      cfg_for_repo: ExecutorConfig,
  ) -> None:
      source = FilesystemQueueSource(cfg_for_repo)
      assert source.pick_next() is None


  def test_inbox_pbis_age_tiebreak_within_same_lane(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      write_sample_pbi(
          fake_repo,
          pbi_id="WI-younger",
          pbi_type="feature",
          severity="normal",
          created_at="2026-05-22T00:00:00+00:00",
      )
      write_sample_pbi(
          fake_repo,
          pbi_id="WI-older",
          pbi_type="feature",
          severity="normal",
          created_at="2026-05-20T00:00:00+00:00",
      )
      _git(fake_repo, "add", ".ralph/inbox")
      _git(fake_repo, "commit", "-m", "age tiebreak")
      source = FilesystemQueueSource(cfg_for_repo)
      pbis = source.inbox_pbis()
      assert [p.id for p in pbis] == ["WI-older", "WI-younger"]
  ```

- [ ] 3. Run the failing test:
  ```
  uv run pytest tests/executor/test_filesystem_queue.py -v
  ```
  Expected: `ModuleNotFoundError: No module named 'ralph_executor.queue.filesystem'`.

- [ ] 4. Implement `ralph_executor/queue/filesystem.py` with the exact content below:
  ```python
  """Filesystem-backed queue source.

  Reads PBI directories from ``.ralph/<state>/`` on the ``ralph-queue``
  checkout. Parses the YAML frontmatter of the type-appropriate entry file
  and returns ``PBI`` dataclasses. Sorts the inbox by priority lane, then
  by ``created_at`` within the lane.
  """
  from __future__ import annotations

  from collections.abc import Mapping
  from datetime import datetime
  from pathlib import Path
  from typing import Any, get_args, cast

  import yaml

  from ralph_executor.config import ExecutorConfig
  from ralph_executor.types import PBI, PBIStatus, PBIType, Severity

  ENTRY_FILE_BY_TYPE: Mapping[str, str] = {
      "feature": "PBI.md",
      "bug": "BUG.md",
      "pr-feedback": "FEEDBACK.md",
  }

  # Severity lane ordering for non-pr-feedback PBIs (lower = higher priority).
  _SEVERITY_RANK: Mapping[str, int] = {
      "critical": 0,
      "high": 1,
      "normal": 2,
      "low": 3,
  }

  # PR-feedback PBIs always take priority over plain severity ordering, per
  # the spec's "Priority lanes" section.
  _PR_FEEDBACK_LANE_RANK = -1


  class QueueError(RuntimeError):
      """Raised when the queue layout is malformed."""


  def _split_frontmatter(text: str) -> tuple[str, str] | None:
      if not text.startswith("---"):
          return None
      lines = text.splitlines()
      if not lines or lines[0].strip() != "---":
          return None
      for idx in range(1, len(lines)):
          if lines[idx].strip() == "---":
              return "\n".join(lines[1:idx]), "\n".join(lines[idx + 1 :])
      return None


  def _detect_entry_file(pbi_dir: Path) -> tuple[str, str] | None:
      """Return ``(entry_filename, pbi_type)`` if exactly one entry file exists."""
      for pbi_type, entry_name in ENTRY_FILE_BY_TYPE.items():
          if (pbi_dir / entry_name).is_file():
              return entry_name, pbi_type
      return None


  def _coerce_datetime(value: Any, field: str, pbi_dir: Path) -> datetime:
      if isinstance(value, datetime):
          return value
      if isinstance(value, str):
          try:
              return datetime.fromisoformat(value)
          except ValueError as exc:
              raise QueueError(
                  f"{pbi_dir}/{field}={value!r} is not ISO-8601: {exc}"
              ) from exc
      raise QueueError(
          f"{pbi_dir}/{field} must be a datetime or ISO-8601 string, "
          f"got {type(value).__name__}"
      )


  def parse_pbi_directory(pbi_dir: Path, *, status: str) -> PBI:
      """Parse the entry file of ``pbi_dir`` into a ``PBI`` dataclass.

      The ``status`` argument is the canonical state name (one of
      ``inbox``/``current``/``pending-pr``/``done``/``blocked``/``archive``);
      the caller knows it because it just read ``.ralph/<status>/`` from
      disk. The frontmatter's ``status`` field is also validated against
      this value when both are present.
      """
      if not pbi_dir.is_dir():
          raise QueueError(f"not a directory: {pbi_dir}")

      detected = _detect_entry_file(pbi_dir)
      if detected is None:
          raise QueueError(
              f"{pbi_dir}: no entry file (expected one of "
              f"{sorted(ENTRY_FILE_BY_TYPE.values())})"
          )
      entry_name, detected_type = detected
      entry = pbi_dir / entry_name

      text = entry.read_text(encoding="utf-8")
      split = _split_frontmatter(text)
      if split is None:
          raise QueueError(
              f"{entry}: missing YAML frontmatter block"
          )
      try:
          fm_any: Any = yaml.safe_load(split[0])
      except yaml.YAMLError as exc:
          raise QueueError(f"{entry}: invalid YAML frontmatter: {exc}") from exc
      if not isinstance(fm_any, Mapping):
          raise QueueError(
              f"{entry}: frontmatter must be a YAML mapping, "
              f"got {type(fm_any).__name__}"
          )

      try:
          pbi_id = str(fm_any["id"]).strip()
          declared_type = str(fm_any["type"]).strip()
          severity_raw = str(fm_any["severity"]).strip()
          attempts = int(fm_any["attempts"])
          created_at = _coerce_datetime(fm_any["created_at"], "created_at", pbi_dir)
          updated_at = _coerce_datetime(fm_any["updated_at"], "updated_at", pbi_dir)
      except KeyError as exc:
          raise QueueError(f"{entry}: missing required field {exc}") from exc

      if declared_type != detected_type:
          raise QueueError(
              f"{entry}: frontmatter type={declared_type!r} disagrees with "
              f"on-disk entry file {entry_name!r} (type={detected_type!r})"
          )

      if declared_type not in get_args(PBIType):
          raise QueueError(
              f"{entry}: type={declared_type!r} not in {get_args(PBIType)}"
          )
      if severity_raw not in get_args(Severity):
          raise QueueError(
              f"{entry}: severity={severity_raw!r} not in {get_args(Severity)}"
          )
      if status not in get_args(PBIStatus):
          raise QueueError(
              f"caller passed status={status!r} not in {get_args(PBIStatus)}"
          )

      return PBI(
          id=pbi_id,
          type=cast(PBIType, declared_type),
          status=cast(PBIStatus, status),
          severity=cast(Severity, severity_raw),
          attempts=attempts,
          created_at=created_at,
          updated_at=updated_at,
          path=pbi_dir,
      )


  def _lane_rank(pbi: PBI) -> tuple[int, int, datetime]:
      """Sort key for inbox PBIs: lane, severity, then created_at."""
      lane: int
      if pbi.type == "pr-feedback":
          lane = _PR_FEEDBACK_LANE_RANK
      else:
          lane = _SEVERITY_RANK[pbi.severity]
      return (lane, _SEVERITY_RANK[pbi.severity], pbi.created_at)


  class FilesystemQueueSource:
      """Reads PBI directories from ``.ralph/<state>/`` on disk."""

      def __init__(self, config: ExecutorConfig) -> None:
          self._config = config

      @property
      def _root(self) -> Path:
          return self._config.repo_path / ".ralph"

      def _list_pbis(self, state: str) -> list[PBI]:
          state_dir = self._root / state
          if not state_dir.is_dir():
              return []
          pbis: list[PBI] = []
          for child in sorted(state_dir.iterdir()):
              if not child.is_dir():
                  continue
              # Skip `.gitkeep` etc. by checking for at least one entry file.
              if _detect_entry_file(child) is None:
                  continue
              pbis.append(parse_pbi_directory(child, status=state))
          return pbis

      def current_pbi(self) -> PBI | None:
          """Return the single PBI in ``current/``, or None.

          Raises ``QueueError`` if more than one PBI is present —
          ``current/`` is the single-focus folder; anything else violates
          the executor's invariants.
          """
          pbis = self._list_pbis("current")
          if not pbis:
              return None
          if len(pbis) > 1:
              ids = sorted(p.id for p in pbis)
              raise QueueError(
                  f"current/ contains more than one PBI: {ids}"
              )
          return pbis[0]

      def inbox_pbis(self) -> list[PBI]:
          """Return all inbox PBIs sorted by priority lane + created_at."""
          return sorted(self._list_pbis("inbox"), key=_lane_rank)

      def pick_next(self) -> PBI | None:
          """Return the highest-priority inbox PBI, or None if inbox is empty."""
          ordered = self.inbox_pbis()
          return ordered[0] if ordered else None

      def pending_pr_pbis(self) -> list[PBI]:
          """Return all PBIs in pending-pr/ (used by Plan 8's sweep)."""
          return self._list_pbis("pending-pr")

      def blocked_pbis(self) -> list[PBI]:
          """Return all PBIs in blocked/ (used by Plans 9 / 10)."""
          return self._list_pbis("blocked")

      def done_pbis(self) -> list[PBI]:
          """Return all PBIs in done/ (used by Plan 9's cycle detector)."""
          return self._list_pbis("done")
  ```

- [ ] 5. Re-run the test; it must pass:
  ```
  uv run pytest tests/executor/test_filesystem_queue.py -v
  ```
  Expected: all 11 tests pass.

- [ ] 6. Run ruff + mypy:
  ```
  uv run ruff check ralph_executor/queue tests/executor/test_filesystem_queue.py
  uv run mypy ralph_executor/queue tests/executor/test_filesystem_queue.py
  ```
  Expected: both report success.

- [ ] 7. Commit:
  ```
  git add ralph_executor/queue/__init__.py ralph_executor/queue/filesystem.py tests/executor/test_filesystem_queue.py
  git commit -m "feat(executor): add FilesystemQueueSource with priority-lane sorting"
  ```
  Expected: commit succeeds.

---

## Task 7 — Queue movements (`ralph_executor/queue/movements.py`)

**Files**
- Create: `tests/executor/test_movements.py`
- Create: `ralph_executor/queue/movements.py`

**Steps**

- [ ] 1. Write the failing test at `tests/executor/test_movements.py`:
  ```python
  """Tests for ``ralph_executor.queue.movements``."""
  from __future__ import annotations

  import subprocess
  from pathlib import Path

  import pytest

  from ralph_executor.config import ExecutorConfig
  from ralph_executor.queue.filesystem import FilesystemQueueSource
  from ralph_executor.queue.movements import (
      QueueMovementError,
      move_current_to_blocked,
      move_current_to_pending_pr,
      move_inbox_to_current,
  )

  from tests.executor.conftest import write_sample_pbi


  def _git(cwd: Path, *args: str) -> str:
      return subprocess.run(
          ["git", *args],
          cwd=str(cwd),
          check=True,
          capture_output=True,
          text=True,
      ).stdout


  def _populate_inbox(fake_repo: Path, pbi_id: str = "WI-1234") -> Path:
      _git(fake_repo, "checkout", "ralph-queue")
      pbi_dir = write_sample_pbi(fake_repo, pbi_id=pbi_id)
      _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
      _git(fake_repo, "commit", "-m", f"inbox: {pbi_id}")
      _git(fake_repo, "push", "origin", "ralph-queue")
      return pbi_dir


  def test_move_inbox_to_current_relocates_directory(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _populate_inbox(fake_repo)
      source = FilesystemQueueSource(cfg_for_repo)
      pbi = source.inbox_pbis()[0]
      moved = move_inbox_to_current(cfg_for_repo, pbi)
      assert moved.status == "current"
      assert moved.path == fake_repo / ".ralph" / "current" / "WI-1234"
      assert not (fake_repo / ".ralph" / "inbox" / "WI-1234").exists()
      assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()


  def test_move_inbox_to_current_rewrites_status(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _populate_inbox(fake_repo)
      source = FilesystemQueueSource(cfg_for_repo)
      pbi = source.inbox_pbis()[0]
      moved = move_inbox_to_current(cfg_for_repo, pbi)
      entry = (moved.path / "PBI.md").read_text(encoding="utf-8")
      assert "status: current" in entry
      assert "status: inbox" not in entry


  def test_move_inbox_to_current_pushes_commit(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _populate_inbox(fake_repo)
      remote_before = _git(fake_repo, "ls-remote", "origin", "ralph-queue").split()[0]
      source = FilesystemQueueSource(cfg_for_repo)
      pbi = source.inbox_pbis()[0]
      move_inbox_to_current(cfg_for_repo, pbi)
      remote_after = _git(fake_repo, "ls-remote", "origin", "ralph-queue").split()[0]
      assert remote_before != remote_after


  def test_move_current_to_pending_pr(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _populate_inbox(fake_repo)
      source = FilesystemQueueSource(cfg_for_repo)
      pbi = source.inbox_pbis()[0]
      pbi = move_inbox_to_current(cfg_for_repo, pbi)
      moved = move_current_to_pending_pr(cfg_for_repo, pbi)
      assert moved.status == "pending-pr"
      assert moved.path == fake_repo / ".ralph" / "pending-pr" / "WI-1234"
      assert not (fake_repo / ".ralph" / "current" / "WI-1234").exists()
      entry = (moved.path / "PBI.md").read_text(encoding="utf-8")
      assert "status: pending-pr" in entry


  def test_move_current_to_blocked(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _populate_inbox(fake_repo)
      source = FilesystemQueueSource(cfg_for_repo)
      pbi = source.inbox_pbis()[0]
      pbi = move_inbox_to_current(cfg_for_repo, pbi)
      moved = move_current_to_blocked(cfg_for_repo, pbi)
      assert moved.status == "blocked"
      assert moved.path == fake_repo / ".ralph" / "blocked" / "WI-1234"
      entry = (moved.path / "PBI.md").read_text(encoding="utf-8")
      assert "status: blocked" in entry


  def test_move_from_wrong_state_raises(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _populate_inbox(fake_repo)
      source = FilesystemQueueSource(cfg_for_repo)
      pbi = source.inbox_pbis()[0]
      with pytest.raises(QueueMovementError, match="must be in current"):
          move_current_to_pending_pr(cfg_for_repo, pbi)


  def test_move_uses_branch_from_config(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> None:
      _populate_inbox(fake_repo)
      _git(fake_repo, "checkout", "main")  # the move helper must switch us back
      source = FilesystemQueueSource(cfg_for_repo)
      pbi = source.inbox_pbis()[0]
      move_inbox_to_current(cfg_for_repo, pbi)
      branch = _git(fake_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
      assert branch == "ralph-queue"
  ```

- [ ] 2. Run the failing test:
  ```
  uv run pytest tests/executor/test_movements.py -v
  ```
  Expected: `ModuleNotFoundError: No module named 'ralph_executor.queue.movements'`.

- [ ] 3. Implement `ralph_executor/queue/movements.py` with the exact content below:
  ```python
  """Atomic folder moves between ``.ralph/`` states.

  Each helper:
    1. Switches the working tree to the queue branch.
    2. Validates the PBI is currently in the expected source state.
    3. ``git mv``s the directory to the destination state folder.
    4. Rewrites the entry file's frontmatter (``status:``, ``updated_at:``).
    5. Commits + pushes the queue branch.

  The result is a new ``PBI`` dataclass reflecting the new on-disk state.
  """
  from __future__ import annotations

  import logging
  from datetime import datetime, timezone
  from pathlib import Path

  from ralph_executor import git_ops
  from ralph_executor.config import ExecutorConfig
  from ralph_executor.queue.filesystem import (
      ENTRY_FILE_BY_TYPE,
      parse_pbi_directory,
  )
  from ralph_executor.types import PBI, PBIStatus

  log = logging.getLogger(__name__)


  class QueueMovementError(RuntimeError):
      """Raised when a folder move violates a precondition."""


  def _now_iso() -> str:
      return (
          datetime.now(tz=timezone.utc)
          .replace(microsecond=0)
          .isoformat()
      )


  def _rewrite_status(entry_file: Path, new_status: PBIStatus) -> None:
      text = entry_file.read_text(encoding="utf-8")
      lines = text.splitlines(keepends=True)
      if not lines or lines[0].strip() != "---":
          raise QueueMovementError(
              f"{entry_file}: no opening '---' fence to rewrite"
          )
      end = -1
      for idx in range(1, len(lines)):
          if lines[idx].strip() == "---":
              end = idx
              break
      if end < 0:
          raise QueueMovementError(
              f"{entry_file}: no closing '---' fence to rewrite"
          )
      rewrote_status = False
      rewrote_updated = False
      now = _now_iso()
      for idx in range(1, end):
          stripped = lines[idx].lstrip()
          if stripped.startswith("status:"):
              lines[idx] = f"status: {new_status}\n"
              rewrote_status = True
          elif stripped.startswith("updated_at:"):
              lines[idx] = f"updated_at: {now}\n"
              rewrote_updated = True
      if not rewrote_status:
          # Insert before the closing fence.
          lines.insert(end, f"status: {new_status}\n")
          end += 1
      if not rewrote_updated:
          lines.insert(end, f"updated_at: {now}\n")
      entry_file.write_text("".join(lines), encoding="utf-8")


  def _move(
      cfg: ExecutorConfig,
      pbi: PBI,
      *,
      expected_state: PBIStatus,
      target_state: PBIStatus,
      commit_prefix: str,
  ) -> PBI:
      if pbi.status != expected_state:
          raise QueueMovementError(
              f"PBI {pbi.id} must be in {expected_state}, found in {pbi.status}"
          )
      git_ops.checkout(cfg.repo_path, cfg.queue_branch)

      src = cfg.repo_path / ".ralph" / expected_state / pbi.id
      dst = cfg.repo_path / ".ralph" / target_state / pbi.id
      if not src.is_dir():
          raise QueueMovementError(
              f"source path {src} does not exist on the queue branch"
          )
      if dst.exists():
          raise QueueMovementError(
              f"destination {dst} already exists; refusing to overwrite"
          )

      git_ops.mv(cfg.repo_path, src, dst)

      entry_name = ENTRY_FILE_BY_TYPE[pbi.type]
      _rewrite_status(dst / entry_name, target_state)

      git_ops.commit_all(
          cfg.repo_path,
          f"{commit_prefix}: move {pbi.id} from {expected_state} to {target_state}",
      )
      git_ops.push(cfg.repo_path, cfg.queue_branch)
      return parse_pbi_directory(dst, status=target_state)


  def move_inbox_to_current(cfg: ExecutorConfig, pbi: PBI) -> PBI:
      """Claim a PBI from inbox into the single-focus current folder."""
      return _move(
          cfg,
          pbi,
          expected_state="inbox",
          target_state="current",
          commit_prefix="chore(ralph-queue)",
      )


  def move_current_to_pending_pr(cfg: ExecutorConfig, pbi: PBI) -> PBI:
      """Promote a PBI whose PR was created from current to pending-pr."""
      return _move(
          cfg,
          pbi,
          expected_state="current",
          target_state="pending-pr",
          commit_prefix="feat(ralph-queue)",
      )


  def move_current_to_blocked(cfg: ExecutorConfig, pbi: PBI) -> PBI:
      """Demote a stuck PBI from current to blocked."""
      return _move(
          cfg,
          pbi,
          expected_state="current",
          target_state="blocked",
          commit_prefix="chore(ralph-queue)",
      )
  ```

- [ ] 4. Re-run the test; it must pass:
  ```
  uv run pytest tests/executor/test_movements.py -v
  ```
  Expected: all seven tests pass.

- [ ] 5. Run ruff + mypy:
  ```
  uv run ruff check ralph_executor/queue/movements.py tests/executor/test_movements.py
  uv run mypy ralph_executor/queue/movements.py tests/executor/test_movements.py
  ```
  Expected: both report success.

- [ ] 6. Commit:
  ```
  git add ralph_executor/queue/movements.py tests/executor/test_movements.py
  git commit -m "feat(executor): add atomic queue movements (inbox->current, current->pending-pr, current->blocked)"
  ```
  Expected: commit succeeds.

---

## Task 8 — Claude spawn + outcome classification (`ralph_executor/claude_spawn.py`)

**Files**
- Create: `tests/executor/test_claude_spawn.py`
- Create: `ralph_executor/claude_spawn.py`

**Steps**

- [ ] 1. Write the failing test at `tests/executor/test_claude_spawn.py`:
  ```python
  """Tests for ``ralph_executor.claude_spawn``."""
  from __future__ import annotations

  import subprocess
  from pathlib import Path

  import pytest

  from ralph_executor.claude_spawn import (
      ClaudeOutcome,
      classify_outcome,
      spawn_claude_p,
  )
  from ralph_executor.config import ExecutorConfig
  from ralph_executor.queue.filesystem import FilesystemQueueSource
  from ralph_executor.queue.movements import move_inbox_to_current

  from tests.executor.conftest import write_claude_script, write_sample_pbi


  def _git(cwd: Path, *args: str) -> str:
      return subprocess.run(
          ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
      ).stdout


  def _setup_current_pbi(
      cfg_for_repo: ExecutorConfig, fake_repo: Path
  ) -> object:
      _git(fake_repo, "checkout", "ralph-queue")
      write_sample_pbi(fake_repo, pbi_id="WI-1234")
      _git(fake_repo, "add", ".ralph/inbox/WI-1234")
      _git(fake_repo, "commit", "-m", "inbox: WI-1234")
      _git(fake_repo, "push", "origin", "ralph-queue")
      source = FilesystemQueueSource(cfg_for_repo)
      pbi = source.inbox_pbis()[0]
      return move_inbox_to_current(cfg_for_repo, pbi)


  def test_spawn_invokes_claude_with_pbi_context(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      fake_claude_binary: Path,
  ) -> None:
      pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
      write_claude_script(
          fake_claude_binary,
          "import sys\n"
          "print('argv=' + ' '.join(sys.argv[1:]))\n"
          "sys.exit(0)\n",
      )
      outcome = spawn_claude_p(cfg_for_repo, pbi)
      assert outcome.exit_code == 0
      assert "argv=" in outcome.stdout
      # The spawned process should at least see ``-p`` somewhere in argv.
      assert "-p" in outcome.stdout


  def test_classify_pr_created_when_stdout_contains_marker(tmp_path: Path) -> None:
      pbi_dir = tmp_path / "WI-1"
      pbi_dir.mkdir()
      outcome = classify_outcome(
          pbi_dir=pbi_dir,
          stdout=(
              "Did the work.\n"
              "PR created: https://dev.azure.com/example/_git/repo/pullrequest/4711\n"
          ),
          stderr="",
          exit_code=0,
          duration_seconds=1.0,
      )
      assert outcome.kind == "pr_created"
      assert outcome.pr_url == (
          "https://dev.azure.com/example/_git/repo/pullrequest/4711"
      )


  def test_classify_stuck_when_stuck_md_present(tmp_path: Path) -> None:
      pbi_dir = tmp_path / "WI-2"
      pbi_dir.mkdir()
      (pbi_dir / "STUCK.md").write_text("# stuck\n", encoding="utf-8")
      outcome = classify_outcome(
          pbi_dir=pbi_dir,
          stdout="",
          stderr="",
          exit_code=0,
          duration_seconds=0.5,
      )
      assert outcome.kind == "stuck"
      assert outcome.pr_url is None


  def test_classify_partial_when_nothing_special(tmp_path: Path) -> None:
      pbi_dir = tmp_path / "WI-3"
      pbi_dir.mkdir()
      outcome = classify_outcome(
          pbi_dir=pbi_dir,
          stdout="Did some work but not done yet.\n",
          stderr="",
          exit_code=0,
          duration_seconds=2.0,
      )
      assert outcome.kind == "partial"


  def test_classify_error_when_exit_code_nonzero(tmp_path: Path) -> None:
      pbi_dir = tmp_path / "WI-4"
      pbi_dir.mkdir()
      outcome = classify_outcome(
          pbi_dir=pbi_dir,
          stdout="",
          stderr="boom\n",
          exit_code=137,
          duration_seconds=0.1,
      )
      assert outcome.kind == "error"


  def test_stuck_takes_precedence_over_pr_created(tmp_path: Path) -> None:
      # If Ralph wrote STUCK.md before exiting, that's the truth even if
      # the stdout contains a stale PR-created line from an earlier step.
      pbi_dir = tmp_path / "WI-5"
      pbi_dir.mkdir()
      (pbi_dir / "STUCK.md").write_text("# stuck mid-run\n", encoding="utf-8")
      outcome = classify_outcome(
          pbi_dir=pbi_dir,
          stdout="PR created: https://example/pullrequest/1\n",
          stderr="",
          exit_code=0,
          duration_seconds=0.3,
      )
      assert outcome.kind == "stuck"


  def test_spawn_records_stdout_stderr_and_exit_code(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      fake_claude_binary: Path,
  ) -> None:
      pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
      write_claude_script(
          fake_claude_binary,
          "import sys\n"
          "sys.stdout.write('hello-stdout\\n')\n"
          "sys.stderr.write('hello-stderr\\n')\n"
          "sys.exit(2)\n",
      )
      outcome = spawn_claude_p(cfg_for_repo, pbi)
      assert outcome.exit_code == 2
      assert "hello-stdout" in outcome.stdout
      assert "hello-stderr" in outcome.stderr
      assert outcome.duration_seconds >= 0


  def test_spawn_simulates_pr_creation(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      fake_claude_binary: Path,
  ) -> None:
      pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
      write_claude_script(
          fake_claude_binary,
          "print('PR created: https://example/pullrequest/9999')\n",
      )
      outcome = spawn_claude_p(cfg_for_repo, pbi)
      assert outcome.kind == "pr_created"
      assert outcome.pr_url == "https://example/pullrequest/9999"


  def test_spawn_simulates_stuck(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      fake_claude_binary: Path,
  ) -> None:
      pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
      write_claude_script(
          fake_claude_binary,
          "from pathlib import Path\n"
          "import sys, os\n"
          "# The PBI dir is passed via the RALPH_PBI_DIR env var by the spawner.\n"
          "pbi_dir = Path(os.environ['RALPH_PBI_DIR'])\n"
          "(pbi_dir / 'STUCK.md').write_text('# stuck\\n')\n"
          "sys.exit(0)\n",
      )
      outcome = spawn_claude_p(cfg_for_repo, pbi)
      assert outcome.kind == "stuck"
  ```

- [ ] 2. Run the failing test:
  ```
  uv run pytest tests/executor/test_claude_spawn.py -v
  ```
  Expected: `ModuleNotFoundError: No module named 'ralph_executor.claude_spawn'`.

- [ ] 3. Implement `ralph_executor/claude_spawn.py` with the exact content below:
  ```python
  """Spawn ``claude -p`` against the current PBI and classify the outcome.

  The spawner sets ``RALPH_PBI_DIR`` in the subprocess environment so the
  spawned Claude session can locate its working PBI without having to
  parse argv. ``classify_outcome`` then maps the (stdout, stderr,
  exit_code, on-disk effects) tuple to one of four typed outcomes:

    * ``pr_created`` — stdout contains the ``PR created: <url>`` marker.
    * ``stuck``      — Ralph wrote STUCK.md into the PBI directory.
    * ``partial``    — Ralph exited zero with neither marker (multi-step
                       PBI: stay in current/, run again next iteration).
    * ``error``      — Non-zero exit code with no STUCK.md (transient
                       failure; loop driver currently treats this the
                       same as ``partial`` but the explicit kind makes
                       Plan 9's safety controls easier to wire later).

  STUCK.md takes precedence over a PR-created marker because Ralph may
  have produced a stale stdout line from an earlier step before writing
  STUCK.md just before exit.
  """
  from __future__ import annotations

  import logging
  import os
  import re
  import subprocess
  import time
  from dataclasses import dataclass
  from pathlib import Path
  from typing import Literal

  from ralph_executor.config import ExecutorConfig
  from ralph_executor.types import PBI

  log = logging.getLogger(__name__)

  OutcomeKind = Literal["pr_created", "stuck", "partial", "error"]

  _PR_URL_RE = re.compile(r"PR created:\s*(\S+)", re.IGNORECASE)
  _STUCK_FILENAME = "STUCK.md"


  @dataclass(frozen=True)
  class ClaudeOutcome:
      """Result of a single ``claude -p`` invocation against the current PBI."""

      kind: OutcomeKind
      pr_url: str | None
      stdout: str
      stderr: str
      exit_code: int
      duration_seconds: float


  def _build_argv(cfg: ExecutorConfig, pbi: PBI) -> list[str]:
      """Compose the argv passed to the ``claude`` binary.

      ``-p`` puts Claude in non-interactive print mode. The PBI directory
      path is forwarded both as an argument (the standing PROMPT.md tells
      Ralph to read it) and via the ``RALPH_PBI_DIR`` environment variable
      (which test stand-ins use to locate the PBI on disk).
      """
      argv = [
          cfg.claude_binary,
          "-p",
          (
              "Read ./prompt/PROMPT.md and work the PBI in "
              f"{pbi.path}. Follow the standing instructions."
          ),
      ]
      return argv


  def spawn_claude_p(cfg: ExecutorConfig, pbi: PBI) -> ClaudeOutcome:
      """Run ``claude -p`` against the PBI and return a classified outcome."""
      argv = _build_argv(cfg, pbi)
      env = os.environ.copy()
      env["RALPH_PBI_DIR"] = str(pbi.path)
      env.setdefault("ANTHROPIC_API_KEY", cfg.anthropic_api_key)
      log.info("spawning %s for PBI %s", argv[0], pbi.id)
      start = time.monotonic()
      result = subprocess.run(
          argv,
          cwd=str(cfg.repo_path),
          env=env,
          check=False,
          capture_output=True,
          text=True,
      )
      duration = time.monotonic() - start
      return classify_outcome(
          pbi_dir=pbi.path,
          stdout=result.stdout,
          stderr=result.stderr,
          exit_code=result.returncode,
          duration_seconds=duration,
      )


  def classify_outcome(
      *,
      pbi_dir: Path,
      stdout: str,
      stderr: str,
      exit_code: int,
      duration_seconds: float,
  ) -> ClaudeOutcome:
      """Map the raw (stdout, stderr, exit, on-disk) tuple to a typed outcome.

      Precedence (highest first):
        1. STUCK.md present on disk → ``stuck``
        2. Exit code non-zero       → ``error``
        3. stdout matches PR-created marker → ``pr_created``
        4. Otherwise                → ``partial``
      """
      stuck_present = (pbi_dir / _STUCK_FILENAME).is_file()
      pr_match = _PR_URL_RE.search(stdout)

      if stuck_present:
          return ClaudeOutcome(
              kind="stuck",
              pr_url=None,
              stdout=stdout,
              stderr=stderr,
              exit_code=exit_code,
              duration_seconds=duration_seconds,
          )
      if exit_code != 0:
          return ClaudeOutcome(
              kind="error",
              pr_url=None,
              stdout=stdout,
              stderr=stderr,
              exit_code=exit_code,
              duration_seconds=duration_seconds,
          )
      if pr_match:
          return ClaudeOutcome(
              kind="pr_created",
              pr_url=pr_match.group(1).strip(),
              stdout=stdout,
              stderr=stderr,
              exit_code=exit_code,
              duration_seconds=duration_seconds,
          )
      return ClaudeOutcome(
          kind="partial",
          pr_url=None,
          stdout=stdout,
          stderr=stderr,
          exit_code=exit_code,
          duration_seconds=duration_seconds,
      )
  ```

- [ ] 4. Re-run the test; it must pass:
  ```
  uv run pytest tests/executor/test_claude_spawn.py -v
  ```
  Expected: all nine tests pass.

- [ ] 5. Run ruff + mypy:
  ```
  uv run ruff check ralph_executor/claude_spawn.py tests/executor/test_claude_spawn.py
  uv run mypy ralph_executor/claude_spawn.py tests/executor/test_claude_spawn.py
  ```
  Expected: both report success.

- [ ] 6. Commit:
  ```
  git add ralph_executor/claude_spawn.py tests/executor/test_claude_spawn.py
  git commit -m "feat(executor): add claude -p spawn + outcome classifier"
  ```
  Expected: commit succeeds.

---

## Task 9 — Loop driver (`ralph_executor/loop.py`)

**Files**
- Create: `tests/executor/test_loop.py`
- Create: `ralph_executor/loop.py`

**Steps**

- [ ] 1. Write the failing test at `tests/executor/test_loop.py`:
  ```python
  """Tests for ``ralph_executor.loop``."""
  from __future__ import annotations

  import subprocess
  from pathlib import Path

  import pytest

  from ralph_executor.claude_spawn import ClaudeOutcome
  from ralph_executor.config import ExecutorConfig
  from ralph_executor.loop import (
      IterationOutcome,
      IterationResult,
      iterate_once,
      run_loop,
  )
  from ralph_executor.queue.filesystem import FilesystemQueueSource

  from tests.executor.conftest import write_sample_pbi


  def _git(cwd: Path, *args: str) -> str:
      return subprocess.run(
          ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
      ).stdout


  def _populate_inbox(fake_repo: Path, pbi_id: str = "WI-1234", severity: str = "normal") -> None:
      _git(fake_repo, "checkout", "ralph-queue")
      write_sample_pbi(fake_repo, pbi_id=pbi_id, severity=severity)
      _git(fake_repo, "add", f".ralph/inbox/{pbi_id}")
      _git(fake_repo, "commit", "-m", f"inbox: {pbi_id}")
      _git(fake_repo, "push", "origin", "ralph-queue")
      _git(fake_repo, "checkout", "main")


  def _stub_spawn(outcome_kind: str, pr_url: str | None = None) -> object:
      def _fake_spawn(cfg: ExecutorConfig, pbi: object) -> ClaudeOutcome:
          return ClaudeOutcome(
              kind=outcome_kind,  # type: ignore[arg-type]
              pr_url=pr_url,
              stdout="",
              stderr="",
              exit_code=0,
              duration_seconds=0.01,
          )

      return _fake_spawn


  def test_iterate_once_idle_when_no_work(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      monkeypatch.setattr(
          "ralph_executor.loop.spawn_claude_p",
          _stub_spawn("partial"),
      )
      result = iterate_once(cfg_for_repo)
      assert result.outcome == "idle"
      assert result.pbi_id is None


  def test_iterate_once_claims_inbox_pbi_when_current_empty(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      _populate_inbox(fake_repo)
      monkeypatch.setattr(
          "ralph_executor.loop.spawn_claude_p",
          _stub_spawn("partial"),
      )
      result = iterate_once(cfg_for_repo)
      assert result.outcome == "claimed"
      assert result.pbi_id == "WI-1234"
      # After claim, the PBI should be on disk under current/, not inbox/.
      assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()
      assert not (fake_repo / ".ralph" / "inbox" / "WI-1234").exists()
      # The feature branch ralph/WI-1234 must exist after a fresh claim.
      branches = _git(fake_repo, "branch", "--list", "ralph/WI-1234").strip()
      assert branches != "", "ralph/WI-1234 branch should be created on claim"


  def test_iterate_once_runs_ralph_when_current_occupied(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      _populate_inbox(fake_repo)
      # Two iterations: first claims, second spawns Ralph (returns partial).
      iterate_once(cfg_for_repo)
      monkeypatch.setattr(
          "ralph_executor.loop.spawn_claude_p",
          _stub_spawn("partial"),
      )
      result = iterate_once(cfg_for_repo)
      assert result.outcome == "ran_partial"
      assert result.pbi_id == "WI-1234"
      # PBI stays in current/.
      assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()


  def test_iterate_once_moves_to_pending_pr_when_pr_created(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      _populate_inbox(fake_repo)
      iterate_once(cfg_for_repo)  # claim
      monkeypatch.setattr(
          "ralph_executor.loop.spawn_claude_p",
          _stub_spawn("pr_created", pr_url="https://example/pr/1"),
      )
      result = iterate_once(cfg_for_repo)
      assert result.outcome == "ran_pr_created"
      assert result.pr_url == "https://example/pr/1"
      assert (fake_repo / ".ralph" / "pending-pr" / "WI-1234").is_dir()
      assert not (fake_repo / ".ralph" / "current" / "WI-1234").exists()


  def test_iterate_once_moves_to_blocked_when_stuck(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      _populate_inbox(fake_repo)
      iterate_once(cfg_for_repo)
      # Simulate Ralph writing STUCK.md before exit.
      pbi_dir = fake_repo / ".ralph" / "current" / "WI-1234"

      def _stuck_spawn(cfg: ExecutorConfig, pbi: object) -> ClaudeOutcome:
          (pbi_dir / "STUCK.md").write_text("# stuck\n", encoding="utf-8")
          return ClaudeOutcome(
              kind="stuck",
              pr_url=None,
              stdout="",
              stderr="",
              exit_code=0,
              duration_seconds=0.0,
          )

      monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _stuck_spawn)
      result = iterate_once(cfg_for_repo)
      assert result.outcome == "ran_stuck"
      assert (fake_repo / ".ralph" / "blocked" / "WI-1234").is_dir()


  def test_iterate_once_treats_error_like_partial(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      _populate_inbox(fake_repo)
      iterate_once(cfg_for_repo)
      monkeypatch.setattr(
          "ralph_executor.loop.spawn_claude_p",
          _stub_spawn("error"),
      )
      result = iterate_once(cfg_for_repo)
      assert result.outcome == "ran_error"
      assert (fake_repo / ".ralph" / "current" / "WI-1234").is_dir()


  def test_iterate_once_invokes_sweep_stub_when_current_empty(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      """Plan 8 fills in the sweep; for v1 the stub must be invoked."""
      called: list[bool] = []

      def _spy_sweep(cfg: ExecutorConfig, source: FilesystemQueueSource) -> None:
          called.append(True)

      monkeypatch.setattr("ralph_executor.loop._run_sweep", _spy_sweep)
      monkeypatch.setattr(
          "ralph_executor.loop.spawn_claude_p",
          _stub_spawn("partial"),
      )
      iterate_once(cfg_for_repo)
      assert called == [True], "sweep stub must be invoked when current/ is empty"


  def test_iterate_once_invokes_cycle_detector_stub(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      """Plan 9 fills in cycle detection; for v1 the stub must be invoked."""
      called: list[bool] = []

      def _spy_check(cfg: ExecutorConfig, source: FilesystemQueueSource) -> bool:
          called.append(True)
          return False

      monkeypatch.setattr(
          "ralph_executor.loop._check_cycle_detector", _spy_check
      )
      monkeypatch.setattr(
          "ralph_executor.loop.spawn_claude_p",
          _stub_spawn("partial"),
      )
      iterate_once(cfg_for_repo)
      assert called == [True]


  def test_iterate_once_pulls_ralph_queue_every_iteration(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      pull_calls: list[str] = []
      from ralph_executor import git_ops as real_git_ops

      original_pull = real_git_ops.pull

      def _spy_pull(repo: Path, branch: str, remote: str = "origin") -> None:
          pull_calls.append(branch)
          original_pull(repo, branch, remote)

      monkeypatch.setattr("ralph_executor.loop.git_ops.pull", _spy_pull)
      monkeypatch.setattr(
          "ralph_executor.loop.spawn_claude_p",
          _stub_spawn("partial"),
      )
      iterate_once(cfg_for_repo)
      assert "ralph-queue" in pull_calls


  def test_iterate_once_pulls_main_only_on_fresh_claim(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      _populate_inbox(fake_repo)
      pull_calls: list[str] = []
      from ralph_executor import git_ops as real_git_ops

      original_pull = real_git_ops.pull

      def _spy_pull(repo: Path, branch: str, remote: str = "origin") -> None:
          pull_calls.append(branch)
          original_pull(repo, branch, remote)

      monkeypatch.setattr("ralph_executor.loop.git_ops.pull", _spy_pull)
      monkeypatch.setattr(
          "ralph_executor.loop.spawn_claude_p",
          _stub_spawn("partial"),
      )
      iterate_once(cfg_for_repo)  # claim → main pulled
      assert "main" in pull_calls

      pull_calls.clear()
      iterate_once(cfg_for_repo)  # current occupied → main NOT pulled
      assert "main" not in pull_calls
      assert "ralph-queue" in pull_calls


  def test_run_loop_terminates_when_iterate_returns_halt(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      """``run_loop`` honours a halt signal from the cycle-detector stub."""

      def _trip(cfg: ExecutorConfig, source: FilesystemQueueSource) -> bool:
          return True

      monkeypatch.setattr("ralph_executor.loop._check_cycle_detector", _trip)
      monkeypatch.setattr(
          "ralph_executor.loop.spawn_claude_p",
          _stub_spawn("partial"),
      )
      results = list(run_loop(cfg_for_repo, max_iterations=5))
      assert any(r.outcome == "halted" for r in results)
      # The halt must terminate run_loop early.
      assert len(results) < 5
  ```

- [ ] 2. Run the failing test:
  ```
  uv run pytest tests/executor/test_loop.py -v
  ```
  Expected: `ModuleNotFoundError: No module named 'ralph_executor.loop'`.

- [ ] 3. Implement `ralph_executor/loop.py` with the exact content below:
  ```python
  """Loop driver — the heart of the executor.

  Algorithm (matches the spec's "Iteration model"):

    1. ``git pull ralph-queue`` (every iteration, cheap, keeps the queue
       in sync).
    2. Check ``current/``.
       a. If occupied: spawn ``claude -p`` against that PBI.
          * pr_created → move PBI to pending-pr/.
          * stuck      → move PBI to blocked/.
          * partial / error → PBI stays in current/ (multi-step).
       b. If empty: run the sweep stub (Plan 8 fills in), then pick the
          highest-priority inbox PBI. If picked, ``git pull main``, claim
          the PBI into current/, and create the per-PBI feature branch
          ``ralph/<PBI-ID>`` off main.
    3. Invoke the cycle-detector stub (Plan 9 fills in). If it returns
       True, ``run_loop`` halts.

  Plan 8 will replace ``_run_sweep`` with the real sweep implementation.
  Plan 9 will replace ``_check_cycle_detector`` with the real detector
  and add STUCK.md attempt-counter handling. Both replacements happen via
  ``monkeypatch`` in tests and via plain import overrides in production;
  the loop itself stays untouched.
  """
  from __future__ import annotations

  import logging
  import time
  from collections.abc import Iterator
  from dataclasses import dataclass
  from typing import Literal

  from ralph_executor import git_ops
  from ralph_executor.claude_spawn import ClaudeOutcome, spawn_claude_p
  from ralph_executor.config import ExecutorConfig
  from ralph_executor.queue.filesystem import FilesystemQueueSource
  from ralph_executor.queue.movements import (
      move_current_to_blocked,
      move_current_to_pending_pr,
      move_inbox_to_current,
  )
  from ralph_executor.types import PBI

  log = logging.getLogger(__name__)

  IterationOutcome = Literal[
      "idle",
      "claimed",
      "ran_partial",
      "ran_error",
      "ran_pr_created",
      "ran_stuck",
      "halted",
  ]


  @dataclass(frozen=True)
  class IterationResult:
      """What happened during a single ``iterate_once`` call."""

      outcome: IterationOutcome
      pbi_id: str | None
      pr_url: str | None = None


  # ----------------------------------------------------------------------
  # Stubs for Plans 8 and 9
  # ----------------------------------------------------------------------

  def _run_sweep(cfg: ExecutorConfig, source: FilesystemQueueSource) -> None:
      """Stub — Plan 8 fills this in.

      In Plan 8 this will iterate ``source.pending_pr_pbis()`` and call
      ``ado-pr show``/``ado-pr read-threads`` to detect PR state changes.
      In v1 (this plan) it is intentionally a no-op so the loop's
      single-PBI focus discipline is testable without Plan 8.
      """
      log.debug("sweep stub invoked (Plan 8 will replace this)")


  def _check_cycle_detector(
      cfg: ExecutorConfig, source: FilesystemQueueSource
  ) -> bool:
      """Stub — Plan 9 fills this in.

      Returns ``True`` if a global cycle has tripped and the loop should
      halt. Plan 9 will replace this with the real detector (signature
      recurrence, whack-a-mole rate, same-file thrashing, etc.). In v1
      the stub always returns ``False``.
      """
      log.debug("cycle-detector stub invoked (Plan 9 will replace this)")
      return False


  # ----------------------------------------------------------------------
  # One iteration
  # ----------------------------------------------------------------------

  def _ensure_on_queue_branch(cfg: ExecutorConfig) -> None:
      if git_ops.current_branch(cfg.repo_path) != cfg.queue_branch:
          git_ops.checkout(cfg.repo_path, cfg.queue_branch)


  def _pull_queue(cfg: ExecutorConfig) -> None:
      log.debug("pulling %s", cfg.queue_branch)
      _ensure_on_queue_branch(cfg)
      git_ops.pull(cfg.repo_path, cfg.queue_branch)


  def _pull_main(cfg: ExecutorConfig) -> None:
      log.debug("pulling %s", cfg.main_branch)
      git_ops.checkout(cfg.repo_path, cfg.main_branch)
      git_ops.pull(cfg.repo_path, cfg.main_branch)


  def _feature_branch_name(pbi: PBI) -> str:
      return f"ralph/{pbi.id}"


  def _claim_pbi(cfg: ExecutorConfig, pbi: PBI) -> PBI:
      """Move PBI into current/ and create the per-PBI feature branch.

      Sequence (matches the spec's "Branch dance"):
        1. ``move_inbox_to_current`` (commits + pushes on ralph-queue).
        2. ``git pull main``.
        3. ``git checkout -b ralph/<PBI-ID>`` off main.
        4. Leave the working tree on the feature branch — that's where
           Ralph edits code in the same iteration.
      """
      moved = move_inbox_to_current(cfg, pbi)
      _pull_main(cfg)
      branch = _feature_branch_name(moved)
      if git_ops.branch_exists(cfg.repo_path, branch):
          # Multi-step PBI re-enters after a previous claim was rolled
          # back — checkout the existing branch rather than create.
          git_ops.checkout(cfg.repo_path, branch)
      else:
          git_ops.checkout_new(cfg.repo_path, branch)
      return moved


  def _run_ralph(cfg: ExecutorConfig, pbi: PBI) -> tuple[ClaudeOutcome, IterationResult]:
      """Spawn ``claude -p`` against the current PBI and classify the result.

      Multi-step PBI discipline: ``partial`` and ``error`` outcomes leave
      the PBI in ``current/``; ``pr_created`` promotes to ``pending-pr/``;
      ``stuck`` demotes to ``blocked/``.
      """
      outcome = spawn_claude_p(cfg, pbi)
      log.info("PBI %s outcome=%s exit=%d", pbi.id, outcome.kind, outcome.exit_code)
      if outcome.kind == "pr_created":
          move_current_to_pending_pr(cfg, pbi)
          return outcome, IterationResult(
              outcome="ran_pr_created", pbi_id=pbi.id, pr_url=outcome.pr_url
          )
      if outcome.kind == "stuck":
          move_current_to_blocked(cfg, pbi)
          return outcome, IterationResult(outcome="ran_stuck", pbi_id=pbi.id)
      if outcome.kind == "error":
          return outcome, IterationResult(outcome="ran_error", pbi_id=pbi.id)
      return outcome, IterationResult(outcome="ran_partial", pbi_id=pbi.id)


  def iterate_once(cfg: ExecutorConfig) -> IterationResult:
      """Run a single iteration of the loop and return the outcome.

      Idempotent in the no-work case: if current/ is empty and the inbox
      is empty, the iteration is a no-op and returns ``IterationResult("idle")``.
      """
      _pull_queue(cfg)
      source = FilesystemQueueSource(cfg)

      current = source.current_pbi()
      if current is not None:
          # Current occupied → just run Ralph on it.
          _outcome, result = _run_ralph(cfg, current)
          if _check_cycle_detector(cfg, source):
              return IterationResult(outcome="halted", pbi_id=current.id)
          return result

      # Current empty → sweep (Plan 8 stub), pick next, claim if any.
      _run_sweep(cfg, source)

      picked = source.pick_next()
      if picked is None:
          # Nothing to do; run the cycle-detector check anyway so a
          # globally-tripped cycle can halt the loop.
          if _check_cycle_detector(cfg, source):
              return IterationResult(outcome="halted", pbi_id=None)
          return IterationResult(outcome="idle", pbi_id=None)

      log.info("claiming PBI %s", picked.id)
      claimed = _claim_pbi(cfg, picked)
      if _check_cycle_detector(cfg, source):
          return IterationResult(outcome="halted", pbi_id=claimed.id)
      return IterationResult(outcome="claimed", pbi_id=claimed.id)


  # ----------------------------------------------------------------------
  # Run forever (with an optional iteration cap for tests)
  # ----------------------------------------------------------------------

  def run_loop(
      cfg: ExecutorConfig, *, max_iterations: int | None = None
  ) -> Iterator[IterationResult]:
      """Run iterations until interrupted or ``max_iterations`` reached.

      Yields each ``IterationResult`` so callers (and tests) can observe
      progress. ``max_iterations`` is primarily for tests; in production
      callers pass ``None`` and the loop runs until KeyboardInterrupt.
      """
      count = 0
      while True:
          try:
              result = iterate_once(cfg)
          except KeyboardInterrupt:
              log.info("interrupted")
              return
          yield result
          if result.outcome == "halted":
              log.warning("halt signalled — exiting run_loop")
              return
          count += 1
          if max_iterations is not None and count >= max_iterations:
              return
          # Sleep only between iterations that found nothing to do.
          if result.outcome == "idle":
              time.sleep(cfg.iteration_sleep_seconds)
  ```

- [ ] 4. Re-run the test; it must pass:
  ```
  uv run pytest tests/executor/test_loop.py -v
  ```
  Expected: all 11 tests pass.

- [ ] 5. Run ruff + mypy:
  ```
  uv run ruff check ralph_executor/loop.py tests/executor/test_loop.py
  uv run mypy ralph_executor/loop.py tests/executor/test_loop.py
  ```
  Expected: both report success.

- [ ] 6. Commit:
  ```
  git add ralph_executor/loop.py tests/executor/test_loop.py
  git commit -m "feat(executor): add loop driver (iterate_once, run_loop) with Plan 8/9 stub seams"
  ```
  Expected: commit succeeds.

---

## Task 10 — CLI entry point (`ralph_executor/cli.py`) + public re-exports

**Files**
- Create: `tests/executor/test_cli.py`
- Create: `ralph_executor/cli.py`
- Modify: `ralph_executor/__init__.py`

**Steps**

- [ ] 1. Write the failing test at `tests/executor/test_cli.py`:
  ```python
  """Tests for ``ralph_executor.cli``."""
  from __future__ import annotations

  import logging
  from pathlib import Path

  import pytest

  from ralph_executor import cli
  from ralph_executor.config import ExecutorConfig
  from ralph_executor.loop import IterationResult


  def test_main_runs_one_iteration_with_once_flag(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      calls: list[ExecutorConfig] = []

      def _fake_iterate(cfg: ExecutorConfig) -> IterationResult:
          calls.append(cfg)
          return IterationResult(outcome="idle", pbi_id=None)

      monkeypatch.setattr(cli, "iterate_once", _fake_iterate)
      monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)

      exit_code = cli.main(["--once"])
      assert exit_code == 0
      assert calls == [cfg_for_repo]


  def test_main_handles_keyboard_interrupt_cleanly(
      cfg_for_repo: ExecutorConfig, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      def _explode(cfg: ExecutorConfig) -> IterationResult:
          raise KeyboardInterrupt()

      monkeypatch.setattr(cli, "iterate_once", _explode)
      monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)

      exit_code = cli.main(["--once"])
      assert exit_code == 0  # graceful exit


  def test_main_uses_repo_flag_to_override_env(
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      observed: list[str] = []

      def _capture(cfg: ExecutorConfig) -> IterationResult:
          observed.append(str(cfg.repo_path))
          return IterationResult(outcome="idle", pbi_id=None)

      monkeypatch.setattr(cli, "iterate_once", _capture)

      def _fake_load() -> ExecutorConfig:
          return cfg_for_repo

      monkeypatch.setattr(cli, "load_config", _fake_load)

      exit_code = cli.main(["--once", "--repo", str(fake_repo)])
      assert exit_code == 0
      assert observed == [str(fake_repo)]


  def test_main_log_level_flag(
      cfg_for_repo: ExecutorConfig, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      monkeypatch.setattr(cli, "load_config", lambda: cfg_for_repo)
      monkeypatch.setattr(
          cli,
          "iterate_once",
          lambda cfg: IterationResult(outcome="idle", pbi_id=None),
      )
      cli.main(["--once", "--log-level", "DEBUG"])
      assert logging.getLogger("ralph_executor").level == logging.DEBUG


  def test_main_exits_2_on_config_error(
      monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
  ) -> None:
      from ralph_executor.config import ConfigError

      def _explode() -> ExecutorConfig:
          raise ConfigError("RALPH_REPO_PATH is required")

      monkeypatch.setattr(cli, "load_config", _explode)
      exit_code = cli.main(["--once"])
      assert exit_code == 2
      assert "RALPH_REPO_PATH" in capsys.readouterr().err


  def test_public_reexports_are_stable() -> None:
      """The names listed below are imported by Plans 8, 9, 10."""
      from ralph_executor import (
          PBI,
          ExecutorConfig,
          IterationResult,
          iterate_once,
          run_loop,
          main,
      )

      assert PBI is not None
      assert ExecutorConfig is not None
      assert IterationResult is not None
      assert callable(iterate_once)
      assert callable(run_loop)
      assert callable(main)
  ```

- [ ] 2. Run the failing test:
  ```
  uv run pytest tests/executor/test_cli.py -v
  ```
  Expected: `ModuleNotFoundError: No module named 'ralph_executor.cli'` and `ImportError` on the re-exports test.

- [ ] 3. Implement `ralph_executor/cli.py` with the exact content below:
  ```python
  """Command-line entry point for ``ralph-executor``.

  ``ralph-executor [--once] [--repo PATH] [--log-level LEVEL]``

  * ``--once``       — run a single iteration and exit. Useful for tests,
                       for the Coder workspaces task-pod model, and for
                       smoke-testing local setups.
  * ``--repo PATH``  — override ``RALPH_REPO_PATH`` for this run.
  * ``--log-level``  — override ``RALPH_LOG_LEVEL`` for this run.
  """
  from __future__ import annotations

  import argparse
  import dataclasses
  import logging
  import sys
  from collections.abc import Sequence

  from ralph_executor.config import ConfigError, ExecutorConfig, load_config
  from ralph_executor.loop import IterationResult, iterate_once, run_loop

  _VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


  def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          prog="ralph-executor",
          description=(
              "Run the Ralph per-repo autonomous coding loop. By default "
              "iterates until interrupted; use --once for a single iteration."
          ),
      )
      parser.add_argument(
          "--once",
          action="store_true",
          help="Run a single iteration and exit.",
      )
      parser.add_argument(
          "--repo",
          help="Override RALPH_REPO_PATH for this run.",
      )
      parser.add_argument(
          "--log-level",
          choices=_VALID_LOG_LEVELS,
          help="Override RALPH_LOG_LEVEL for this run.",
      )
      return parser.parse_args(list(argv))


  def _configure_logging(level: int) -> None:
      logging.basicConfig(
          format="%(asctime)s %(levelname)s %(name)s: %(message)s",
          level=level,
          force=True,
      )
      logging.getLogger("ralph_executor").setLevel(level)


  def _apply_overrides(
      cfg: ExecutorConfig, args: argparse.Namespace
  ) -> ExecutorConfig:
      changes: dict[str, object] = {}
      if args.repo:
          from pathlib import Path

          changes["repo_path"] = Path(args.repo).resolve()
      if args.log_level:
          changes["log_level"] = logging.getLevelName(args.log_level)
      if not changes:
          return cfg
      return dataclasses.replace(cfg, **changes)


  def main(argv: Sequence[str] | None = None) -> int:
      args = _parse_args(argv if argv is not None else sys.argv[1:])

      try:
          cfg = load_config()
      except ConfigError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2

      cfg = _apply_overrides(cfg, args)
      _configure_logging(cfg.log_level)

      log = logging.getLogger(__name__)
      log.info(
          "ralph-executor starting (repo=%s queue=%s main=%s)",
          cfg.repo_path,
          cfg.queue_branch,
          cfg.main_branch,
      )

      try:
          if args.once:
              result = iterate_once(cfg)
              log.info(
                  "single iteration finished: outcome=%s pbi=%s",
                  result.outcome,
                  result.pbi_id,
              )
              return 0
          for result in run_loop(cfg):
              log.info(
                  "iteration outcome=%s pbi=%s", result.outcome, result.pbi_id
              )
              if result.outcome == "halted":
                  log.warning("loop halted — exiting")
                  return 0
          return 0
      except KeyboardInterrupt:
          log.info("interrupted; exiting cleanly")
          return 0
      except Exception:  # noqa: BLE001 — top-level safety net
          log.exception("unhandled exception in ralph-executor")
          return 1


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] 4. Replace `ralph_executor/__init__.py` (currently the Plan 1 one-line stub) with the exact content below. This is the public surface Plans 8, 9, 10 import from:
  ```python
  """Public surface of the Ralph executor package.

  Re-exports the stable names that Plans 8 (sweep), 9 (safety controls),
  and 10 (supervisor skills) import. The orchestrator's
  "Cross-plan integration points" section is the contract; keep these
  names stable.
  """
  from ralph_executor.config import ConfigError, ExecutorConfig, load_config
  from ralph_executor.loop import (
      IterationOutcome,
      IterationResult,
      iterate_once,
      run_loop,
  )
  from ralph_executor.cli import main
  from ralph_executor.types import PBI, PBIStatus, PBIType, Severity

  __all__ = [
      "ConfigError",
      "ExecutorConfig",
      "IterationOutcome",
      "IterationResult",
      "PBI",
      "PBIStatus",
      "PBIType",
      "Severity",
      "iterate_once",
      "load_config",
      "main",
      "run_loop",
  ]
  ```

- [ ] 5. Re-run the test; it must pass:
  ```
  uv run pytest tests/executor/test_cli.py -v
  ```
  Expected: all six tests pass.

- [ ] 6. Sanity-check the console script is now wired end-to-end. Run:
  ```
  uv run ralph-executor --help
  ```
  Expected: argparse help text mentioning `--once`, `--repo`, `--log-level`.

- [ ] 7. Run ruff + mypy:
  ```
  uv run ruff check ralph_executor/cli.py ralph_executor/__init__.py tests/executor/test_cli.py
  uv run mypy ralph_executor tests/executor/test_cli.py
  ```
  Expected: both report success.

- [ ] 8. Commit:
  ```
  git add ralph_executor/cli.py ralph_executor/__init__.py tests/executor/test_cli.py
  git commit -m "feat(executor): add CLI entry point and public re-exports for downstream plans"
  ```
  Expected: commit succeeds.

---

## Task 11 — Full executor suite + toolchain pass

**Files**
- (none — toolchain-only step)

**Steps**

- [ ] 1. Run the entire executor test subtree:
  ```
  uv run pytest tests/executor/ -v
  ```
  Expected: every test in every executor test module passes. Approximate count: 4 (types) + 9 (config) + 14 (git_ops) + 11 (filesystem queue) + 7 (movements) + 9 (claude_spawn) + 11 (loop) + 6 (cli) = ~71 tests.

- [ ] 2. Run the full repo suite to confirm nothing earlier in the repo regressed:
  ```
  uv run pytest
  ```
  Expected: all tests pass.

- [ ] 3. Run the full lint + type-check gate:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  ```
  Expected: each command exits 0.

- [ ] 4. Confirm the commit history is clean and conventional:
  ```
  git log --oneline -20
  ```
  Expected: ten Plan-7 commits visible (one per task that produced files), each with a conventional-commit prefix:
  - `chore(executor): scaffold ralph-executor console script + tests/executor package`
  - `feat(executor): add canonical PBI types shared with sweep/safety/supervisor plans`
  - `feat(executor): add ExecutorConfig + load_config env-driven loader`
  - `test(executor): add shared fixtures (fake_repo, sample_pbi, fake_claude_binary, cfg_for_repo)`
  - `feat(executor): add git_ops subprocess wrappers (fetch/pull/checkout/commit/push/mv)`
  - `feat(executor): add FilesystemQueueSource with priority-lane sorting`
  - `feat(executor): add atomic queue movements (inbox->current, current->pending-pr, current->blocked)`
  - `feat(executor): add claude -p spawn + outcome classifier`
  - `feat(executor): add loop driver (iterate_once, run_loop) with Plan 8/9 stub seams`
  - `feat(executor): add CLI entry point and public re-exports for downstream plans`

---

## Verification

This is the orchestrator's Plan 7 verification gate. Plans 8 (sweep) and 9 (safety controls) are blocked until this passes.

- [ ] 1. Run the executor test selection (the gate command from the orchestrator):
  ```
  uv run pytest tests/executor/ -v
  ```
  Expected: every test passes; exit 0.

- [ ] 2. Run mypy strict against the executor package — the orchestrator's gate explicitly calls this out:
  ```
  uv run mypy ralph_executor
  ```
  Expected: `Success: no issues found in N source files`.

- [ ] 3. Confirm the public surface used by downstream plans imports cleanly without invoking any other module. Run:
  ```
  uv run python -c "from ralph_executor import PBI, ExecutorConfig, IterationResult, iterate_once, run_loop, main; print('ok')"
  ```
  Expected: `ok`.

- [ ] 4. Confirm both the Plan-8 and Plan-9 stub seams are present and importable, so downstream plans have well-defined attachment points. Run:
  ```
  uv run python -c "from ralph_executor.loop import _run_sweep, _check_cycle_detector; print('plan8_stub', _run_sweep.__doc__ is not None); print('plan9_stub', _check_cycle_detector.__doc__ is not None)"
  ```
  Expected:
  ```
  plan8_stub True
  plan9_stub True
  ```
  Both stubs MUST carry docstrings explicitly tagged "Plan 8 fills this in" / "Plan 9 fills this in" (asserted by reading the docstring strings during Plan 8/9 development).

- [ ] 5. Smoke-test the CLI end-to-end against the conftest's `fake_repo` shape. From the repo root:
  ```
  uv run python -c "
  import subprocess, tempfile, os, stat
  from pathlib import Path
  tmp = Path(tempfile.mkdtemp())
  bare = tmp / 'bare.git'
  work = tmp / 'work'
  subprocess.run(['git', 'init', '--bare', str(bare)], check=True, capture_output=True)
  subprocess.run(['git', 'init', str(work)], check=True, capture_output=True)
  for cmd in (['config', 'user.email', 't@e'], ['config', 'user.name', 'T'],
              ['commit', '--allow-empty', '-m', 'init'], ['branch', '-M', 'main'],
              ['remote', 'add', 'origin', str(bare)], ['push', '-u', 'origin', 'main'],
              ['checkout', '-b', 'ralph-queue'], ['commit', '--allow-empty', '-m', 'q'],
              ['push', '-u', 'origin', 'ralph-queue'], ['checkout', 'main']):
      subprocess.run(['git', *cmd], cwd=str(work), check=True, capture_output=True)
  fake = tmp / 'bin' / 'claude'
  fake.parent.mkdir()
  fake.write_text('#!/usr/bin/env python3\nimport sys; sys.exit(0)\n')
  fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
  env = dict(os.environ)
  env['RALPH_REPO_PATH'] = str(work)
  env['ANTHROPIC_API_KEY'] = 'fake'
  env['RALPH_CLAUDE_BINARY'] = str(fake)
  env['RALPH_ITERATION_SLEEP_SECONDS'] = '0'
  env['PATH'] = str(fake.parent) + os.pathsep + env.get('PATH', '')
  rc = subprocess.run(['uv', 'run', 'ralph-executor', '--once', '--log-level', 'WARNING'],
                      env=env, capture_output=True, text=True)
  print('exit', rc.returncode)
  print('stdout', rc.stdout[-200:])
  print('stderr', rc.stderr[-200:])
  assert rc.returncode == 0
  "
  ```
  Expected: exit code 0 from the `ralph-executor --once` invocation. The script tears down its own tempfile tree on exit.

If steps 1-5 all pass, Plan 7 is complete. Plans 8 (sweep) and 9 (safety controls) can be dispatched.

### Plan-7 invariants downstream plans rely on

Document — and do not violate — these contracts in any follow-up work:

1. **Single-PBI focus.** `FilesystemQueueSource.current_pbi()` returns `None` or a single PBI. More than one entry under `.ralph/current/` is a hard error.
2. **PBI stays in current/ across partial iterations.** Plans 8 and 9 must not move PBIs out of current/ for reasons other than `pr_created` (→ pending-pr/) or `stuck` / cycle-trip (→ blocked/).
3. **Queue branch pulled every iteration; main pulled only on fresh claim.** This is the spec's invariant; the loop encodes it. Plans 8 and 9 must not introduce additional `main` pulls inside an iteration.
4. **Stubs are import-overridable, not call-graph-rewritable.** Plans 8 and 9 replace `_run_sweep` and `_check_cycle_detector` by module-attribute override (or by explicit replacement of the stub function body) — they do NOT rewrite `iterate_once`. The seams are the contract.
5. **PBI type and PBIStatus literal aliases are frozen.** Plans 8, 9, 10 import `PBIType`, `PBIStatus`, `Severity` from `ralph_executor.types`. Adding values requires a coordinated update across all plans + a migration of existing queue PBIs.
6. **`PR created: <url>` is the agreed PR-creation marker on stdout.** Plan 6 (PROMPT.md) must instruct Ralph to emit exactly this string after a successful `ado-pr create-pr`. Plan 5 (`ado-pr` skill) already prints the PR URL on success; the prompt is responsible for prefixing it with the marker.
