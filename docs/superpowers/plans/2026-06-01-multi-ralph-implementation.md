# multi-ralph (Scope 1) — Implementation Plan v5

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Scope 1 multi-ralph (per-instance identity, namespaced workspace, CLAIM.json ownership marker, workspace lockfile) on ralph's current main (post-KILL-RALPH-HOME, post-#64, post-#65). Drop-in for N=1 single-ralph operators; correct for N≥2 across hosts.

**Architecture:** Per-instance namespaced queue clone at `<workspace_root>/queue-<instance-id>/`. Resolved `instance_id` (CLI → env → TOML → sanitised hostname). Atomic `git mv inbox/<id> current/<id> + CLAIM.json + commit + push_with_rebase` claim; `current_pbi()` filters by `instance_id`. OS-level workspace lockfile. New `ralph-recover` operator skill. No new service, no heartbeat, no centralised coordination.

**Tech Stack:** Python 3.12, pytest, ruff (lint **AND** format), mypy strict, GitHub Actions. POSIX `fcntl.flock` + Windows `msvcrt.locking` for the workspace lockfile. No new third-party deps.

**Source spec:** `docs/superpowers/specs/2026-05-31-multi-ralph-design.md`
**Supersedes:** `docs/superpowers/plans/2026-05-31-multi-ralph-implementation.md` (v4, stale — branched at `8a23d6a` which predates KILL-RALPH-HOME). v4 was implemented in PR #63, closed 2026-06-01 due to 11-file conflict surface.

**Target baseline:** `main @ 1c80417` (post-#65). Tip evolves; tasks reference structural points, not commit SHAs.

---

## Guardrails

High-confidence reflections + standards-doc rules that govern this plan. Anchors use `[[slug]]` for cross-reference.

### Process gates (non-skippable)

- **[[per-step-confidence-rating]]** (standards/ralph-runtime.md §"Apply confidence scoring") — Every task carries a %. Sub-91% tasks lift via concrete additional mitigations in the task body, not as a footnote. Every task in this plan ships at ≥91% effective confidence.
- **[[render-plan-in-visualiser]]** (standards/ralph-runtime.md) — After plan commit, render a plan-summary page in the running visualiser (Bootstrap 5 light theme, full HTML doc) and announce URL before presenting execution choice.
- **[[feature-branch-at-task-start]]** (standards/ralph-runtime.md) — When execution begins, create `feat/multi-ralph-scope-1` (or per-task branch under subagent-driven mode) before any file edit.
- **[[cross-read-prose-vs-example]]** (standards/ralph-runtime.md) — Plan self-review cross-reads embedded code against surrounding prose.
- **[[spec-test-code-lint]]** (standards/ralph-runtime.md) — Test snippets use `from datetime import UTC`, no stray `import pytest`, no `(str, Enum)`. ruff `format --check` AND `check` are both run before commit per session-learned [[ruff-two-gates]].

### Code-quality reflections that apply

- **[[ruff-two-gates]]** (2026-06-01, this session, id `9bb7e8c6`) — ruff has TWO gates: `ruff check` AND `ruff format --check`. Local passing `check` does NOT mean CI passes — run both before commit. Every commit step in this plan runs `ruff format` then `ruff check`.
- **[[shell-python-c-env-vars]]** (2026-06-01, this session, id `357be141`) — Shell wrappers passing operator paths into `python -c "..."` via bash interpolation are vulnerable to single-quote breakage and code injection. Pass via env vars + `os.environ`. Applies if any task here adds shell wrappers; no current task does, but `ralph-recover` is operator-invoked and gets reviewed for this pattern.
- **[[lift-pattern-not-footnote]]** (2026-05-31, id `29446bd6`) — Mitigations for sub-91% tasks MUST recompute effective confidence to ≥91%. Listing a mitigation while leaving the % at 80% violates the lift rule.
- **[[98056ebc-docs-in-sync]]** (conf 0.95) — README + runbook docs ship in same PR as code that changes user-visible CLI flags, config keys, or workspace layout. Tasks 16–17 are mandatory.
- **[[462d13a7-grep-test-callsites]]** (conf 0.85) — Adding a required field to `ExecutorConfig` requires grepping every `ExecutorConfig(` call-site under `tests/`. Adding a required arg to `ensure_queue_clone()` requires grepping every `ensure_queue_clone(` call-site. Both apply in Tasks 1 and 6. Concrete grep commands listed in those tasks.
- **[[355faeb8-windows-argv-limit]]** (conf 0.8) — Windows cmd.exe 8191-char argv ceiling: avoid passing large strings via subprocess. Not triggered by anything in this plan, but the lockfile module uses `msvcrt.locking` not subprocess so it's not an issue.
- **[[0547374e-no-git-add-A]]** (conf 0.7) — Every commit step stages explicit paths. No `git add -A` while ralph is co-running on the same repo. Ralph executor itself doesn't run during this plan's implementation (the plan IS what builds the executor changes), so the co-running risk is low, but the rule is observed for muscle memory.
- **[[26d0a5a7-truthiness-vs-none]]** (conf 0.75) — `--instance-id ""` must reach the validator, not be short-circuited by `if args.instance_id:`. Use `if args.instance_id is not None:` in `_apply_overrides`.
- **[[ca0308d6-flag-default-grep]]** (conf 0.75) — `instance_id` default is the sanitised hostname; grep the literal `"queue"` (old workspace-rooted subdir name) across the module to ensure no surviving hardcoded references after Task 6 lands.
- **[[aa125f7a-empty-input-guard]]** (conf 0.7) — Lockfile module must guard `Path` resolution + wrap I/O in try/except, re-raising as `LockfileError`.
- **[[77c83c69-target-repo-required]]** (conf 0.7) — Tests that construct PBIs with `target_repo` must continue to do so; multi-ralph doesn't change the PBI frontmatter shape. Cross-check in Task 9 tests.
- **[[c73f9438-duplicated-validator]]** (conf 0.7) — `instance_id` regex validator is single-sourced in `config.py`; CLI `_apply_overrides`, env var loader, and TOML loader all call it. Test matrix in Task 1; CLI/env/TOML tests assert the SAME error message comes out of each path.
- **[[dfcf8a2a-argparse-both-layers]]** (conf 0.7) — `--instance-id` declared on the top-level parser only (not on subparsers). All subcommands inherit; no two-level ambiguity.
- **[[8cada12c-package-relative-imports]]** (conf 0.75) — `ralph-recover` skill scripts are runnable as `python skills/ralph-recover/scripts/recover.py` direct-path. Imports from `scripts.queue_writer` (the existing skill-side helper) are tested explicitly.
- **[[d342c481-fixture-gitignore-mirror]]** (conf 0.6) — Fixtures that seed a queue clone ship `.gitignore` covering `.ralph/state/`, `.ralph-work/`, AND now `.ralph.lock` so `git add -A` in test helpers doesn't stage the lockfile.
- **[[d62c9e9e-pin-commit-subject]]** (conf 0.7) — Integration tests T1–T5 (Task 16) assert upstream tip advanced by pinning the exact expected commit subject (`chore(queue): claim <id> for <instance-id>`), not just `tip_before != tip_after`.
- **[[852f5ae9-disambiguate-stopped-early]]** (conf 0.65) — `ralph-recover` result JSON has explicit `dry_run_skipped: bool = False` field reserved for forward-compat even though there's no `--dry-run` in Scope 1.

### Dismissed (considered, N/A)

- 0c83e25d (0.8) Playwright textContent — no Playwright.
- 63c4e75a (0.85) `git mv` + content edits — the claim path's `git mv inbox/<id> current/<id> + write CLAIM.json + git add CLAIM.json + commit` MUST stage CLAIM.json explicitly because newly-written files inside the moved directory are not auto-staged by `git mv`. Captured in Task 9's step list.
- a8019766 (0.75) PUT-if-not-exists placeholder collision — no remote API.
- e73b9e79 (0.7) `--dry-run` mutation guard — no `--dry-run` in Scope 1.
- 85e7ec84 (0.6) tempfile.mkstemp fd leak — no `mkstemp` usage.
- cd86bc4d (0.6) `git stash` to verify inherited failures — execution-time rule.
- d1a577ce (0.65) don't flip shared fixture default — `queue_branch` fixture default doesn't change here. `instance_id` fixture is purpose-built per-test from the start (Task 1).
- b017b510 (0.9) PBI dispatch boundary — N/A; this plan IS the production of changes ralph will execute on its own queue.

---

## File structure

```
ralph_executor/
├── config.py                       # + instance_id field, resolution chain, validator
├── cli.py                          # + --instance-id flag on top-level parser
├── user_config.py                  # + instance_id TOML key
├── setup_cmds.py                   # + init prompt for instance_id (default = sanitised hostname)
├── queue_clone.py                  # ensure_queue_clone gains instance_id arg; legacy queue/ rename
├── loop.py                         # _claim_pbi writes CLAIM.json; META-BUG tripped_by_instance
├── lockfile.py                     # NEW — POSIX + Windows OS-exclusive lockfile
└── queue/
    └── filesystem.py               # current_pbi() filters by instance_id; CLAIM.json IO helpers

skills/
├── ralph-status/scripts/status.py  # + OWNER column from CLAIM.json
├── ralph-cancel/scripts/cancel.py  # + refuse on foreign CLAIM.json
├── ralph-promote/scripts/promote.py # + refuse cross-instance ownership
└── ralph-recover/                  # NEW skill
    ├── SKILL.md
    └── scripts/
        ├── __init__.py
        └── recover.py

tests/
├── executor/
│   ├── test_config.py                # + instance_id resolution matrix
│   ├── test_cli.py                   # + --instance-id flag tests
│   ├── test_user_config.py           # + instance_id TOML round-trip
│   ├── test_setup_cmds.py            # + init prompt covers instance_id
│   ├── test_queue_clone.py           # + namespaced path + legacy rename
│   ├── test_loop.py                  # + claim path CLAIM.json contents
│   ├── test_lockfile.py              # NEW
│   ├── test_multi_ralph_integration.py # NEW — T1–T5 cross-instance scenarios
│   └── queue/
│       └── test_filesystem.py        # + current_pbi() filter cases
├── skills/
│   ├── test_ralph_status.py          # + OWNER column
│   ├── test_ralph_cancel.py          # + refuse foreign CLAIM
│   ├── test_ralph_promote.py         # + refuse cross-instance
│   └── test_ralph_recover.py         # NEW

docs/runbooks/ralph-setup.md         # + "Running multiple ralphs"
README.md                            # + multi-ralph section + upgrade guidance
```

---

## Tasks

### Task 1: `instance_id` field on `ExecutorConfig` + resolution chain + validator

**Confidence: 95%.** Mechanical addition to a well-defined config layer.

**Files:**
- Modify: `ralph_executor/config.py` (add field, validator, env-var loader, TOML loader)
- Modify: `tests/executor/test_config.py`
- Modify: `tests/executor/test_config_toml.py`
- Per [[462d13a7-grep-test-callsites]]: also grep + thread the new field through every `ExecutorConfig(` call-site under `tests/`

- [ ] **Step 1: Grep all ExecutorConfig call-sites.**

```bash
grep -rn 'ExecutorConfig(' tests/
```

Record the file:line list — Tasks 1–7 may need to update each fixture. Expected sites include `tests/executor/conftest.py::fake_config`, `tests/safety/conftest.py`, `tests/safety/test_cycle_detector.py::_cfg`, `tests/safety/test_integration_loop.py`.

- [ ] **Step 2: Write failing test — instance_id required, validator regex.**

```python
# tests/executor/test_config.py — add to existing file
from __future__ import annotations

import pytest

from ralph_executor.config import ConfigError, ExecutorConfig, validate_instance_id


def test_validate_instance_id_accepts_simple():
    validate_instance_id("ralph-a")
    validate_instance_id("box1")
    validate_instance_id("a")
    validate_instance_id("a" * 63)  # max length


@pytest.mark.parametrize(
    "bad",
    ["", "A", "1ralph", "-ralph", "ralph!", "a" * 64, "ralph.a", "ralph a"],
)
def test_validate_instance_id_rejects(bad: str):
    with pytest.raises(ConfigError, match="instance_id"):
        validate_instance_id(bad)


def test_executor_config_requires_instance_id(tmp_path):
    with pytest.raises(TypeError):
        # exact arg list depends on current ExecutorConfig signature;
        # the point is that instance_id is now a required kwarg
        ExecutorConfig(
            queue_repo="https://example.com/q.git",
            queue_branch="ralph-queue",
            workspace_root=tmp_path,
        )
```

- [ ] **Step 3: Run, verify fail.**

```bash
uv run pytest tests/executor/test_config.py::test_validate_instance_id_accepts_simple -v
```

Expected: ImportError on `validate_instance_id`.

- [ ] **Step 4: Implement validator + field.**

```python
# ralph_executor/config.py — add near other validators
import re
from typing import Final

_INSTANCE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def validate_instance_id(value: str) -> None:
    """Validate a resolved instance_id.

    Applied AFTER hostname sanitisation. Empty/missing handled by the resolver,
    not by this validator.
    """
    if not _INSTANCE_ID_RE.fullmatch(value):
        raise ConfigError(
            f"instance_id {value!r} must match {_INSTANCE_ID_RE.pattern} "
            f"(filesystem-safe lowercase, 1-63 chars, starts with alnum)"
        )


def sanitise_hostname(hostname: str) -> str:
    """Lowercase + dot/non-allowed-char replacement for default instance_id."""
    lowered = hostname.lower()
    return re.sub(r"[^a-z0-9_-]", "-", lowered)


def resolve_instance_id(
    *, cli_value: str | None, env_value: str | None, toml_value: str | None,
    hostname: str,
) -> str:
    """First-match-wins resolver. Returns validated instance_id."""
    for candidate in (cli_value, env_value, toml_value):
        if candidate is not None:
            validate_instance_id(candidate)
            return candidate
    # Fall back to hostname.
    candidate = sanitise_hostname(hostname)
    if not candidate:
        raise ConfigError(
            "could not derive instance_id from hostname; set instance_id in "
            "~/.ralph/config.toml or pass --instance-id"
        )
    validate_instance_id(candidate)
    return candidate
```

Add `instance_id: str` as a required field on `ExecutorConfig`.

Per [[7847b0dc-mypy-hoist-defaults]]: declare `instance_id: str` non-Optional. The resolver returns `str`, not `str | None`.

- [ ] **Step 5: Thread through ExecutorConfig call-sites (the grep from Step 1).**

For each test fixture site, pass a hard-coded `instance_id="test-ralph"` or per-test override. Per [[d1a577ce]]: do NOT flip a shared fixture's default mid-way; add a new mandatory keyword and update each call-site explicitly.

- [ ] **Step 6: Run all tests, expect green.**

```bash
uv run ruff format --check ralph_executor/config.py tests/executor/test_config.py
uv run ruff check ralph_executor/config.py tests/executor/test_config.py
uv run mypy
uv run pytest tests/executor/test_config.py tests/executor/test_config_toml.py -v
```

- [ ] **Step 7: Commit.**

```bash
git add ralph_executor/config.py tests/executor/test_config.py tests/executor/test_config_toml.py \
  tests/executor/conftest.py tests/safety/conftest.py \
  tests/safety/test_cycle_detector.py tests/safety/test_integration_loop.py
git commit -m "feat(config): instance_id field + resolution chain + regex validator"
```

---

### Task 2: `--instance-id` CLI flag

**Confidence: 95%.** Per [[dfcf8a2a-argparse-both-layers]]: top-level parser only.

**Files:**
- Modify: `ralph_executor/cli.py` (`_build_parser` + `_apply_overrides`)
- Modify: `tests/executor/test_cli.py`

- [ ] **Step 1: Write failing test.**

```python
# tests/executor/test_cli.py — additions
def test_instance_id_flag_overrides_config(monkeypatch, tmp_path):
    # arrange: TOML has instance_id=foo; CLI passes --instance-id bar
    # assert: resolved cfg.instance_id == "bar"
    ...


def test_instance_id_empty_string_rejected(monkeypatch, tmp_path):
    # per [[26d0a5a7-truthiness-vs-none]]: --instance-id "" must error,
    # not silently fall through to env/TOML/hostname
    with pytest.raises(SystemExit):
        cli_main(["--instance-id", "", "run", "--once"])


def test_instance_id_invalid_chars_rejected(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        cli_main(["--instance-id", "BAD CHARS", "run", "--once"])
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement.**

Add `--instance-id NAME` to the top-level parser. In `_apply_overrides`, **use `if args.instance_id is not None:` not truthiness** so empty-string reaches the validator. Call `validate_instance_id` and raise `ConfigError` on failure; argparse converts to exit code 2.

- [ ] **Step 4: Run, PASS.**

- [ ] **Step 5: Commit.**

```bash
git add ralph_executor/cli.py tests/executor/test_cli.py
git commit -m "feat(cli): --instance-id flag on top-level parser"
```

---

### Task 3: TOML `instance_id` key + `RALPH_INSTANCE_ID` env var

**Confidence: 95%.** Mirrors the existing TOML loader pattern.

**Files:**
- Modify: `ralph_executor/user_config.py`
- Modify: `ralph_executor/config.py` (env var read inside `load_config`)
- Modify: `tests/executor/test_user_config.py`
- Modify: `tests/executor/test_config_toml.py`

- [ ] **Step 1: Failing tests.**

Tests assert:
- `instance_id` key in `~/.ralph/config.toml` is loaded into resolution chain
- `RALPH_INSTANCE_ID=foo` env var is read
- Empty-string in TOML rejected (reaches validator)
- Resolution order: CLI > env > TOML > hostname

- [ ] **Step 2–4: TDD impl.**

In `load_config`: read `os.environ.get("RALPH_INSTANCE_ID")` (not `os.environ["RALPH_INSTANCE_ID"]`). Call `resolve_instance_id` with all four candidates.

- [ ] **Step 5: Commit.**

```bash
git add ralph_executor/user_config.py ralph_executor/config.py \
  tests/executor/test_user_config.py tests/executor/test_config_toml.py
git commit -m "feat(config): TOML instance_id key + RALPH_INSTANCE_ID env var"
```

---

### Task 4: `setup_cmds` init prompts for `instance_id`

**Confidence: 93%** (lifted from 88%). Risk: interactive-prompt mocking patterns vary across existing init tests.

**Confidence lifts applied:**
1. **Reuse existing `_prompt` helper.** `setup_cmds.py` already has a tested `_prompt(question, default)` helper. Add `instance_id` prompt via that helper; default = sanitised hostname. No new prompting machinery.
2. **Default applied only on empty input.** Per [[7847b0dc-mypy-hoist-defaults]]: declare default at top of function, overwrite only on non-empty user input.
3. **Test parametrises both interactive AND `--instance-id <name>` path.** Confirms init writes the value to TOML in both cases.

**Files:**
- Modify: `ralph_executor/setup_cmds.py`
- Modify: `tests/executor/test_setup_cmds.py`

- [ ] **Step 1–5: TDD as above.**

- [ ] **Step 6: Commit.**

```bash
git add ralph_executor/setup_cmds.py tests/executor/test_setup_cmds.py
git commit -m "feat(setup): init prompts for instance_id (default = sanitised hostname)"
```

---

### Task 5: Workspace lockfile module — `ralph_executor/lockfile.py`

**Confidence: 92%** (lifted from 85%). Risk: cross-platform locking primitives differ between POSIX and Windows.

**Confidence lifts applied:**
1. **Platform dispatch via `sys.platform`, not `os.name`.** `sys.platform == "win32"` selects `msvcrt`; everything else uses `fcntl`. Documented in module docstring + tested via platform-gated parametrisation.
2. **Test runs on whatever platform CI is on.** GitHub Actions matrix already runs ubuntu-latest (POSIX) AND ubuntu is the only CI lane today. Windows path is unit-tested via a `pytest.mark.skipif(sys.platform != "win32", reason="...")` test that runs on local Windows but is skipped in Linux CI. The POSIX path is the load-bearing one for production deployments.
3. **Lock file path is `<workspace>/queue-<instance>/.ralph.lock`.** Inside the queue clone but outside `.ralph/` so `.gitignore` already covers it (verified in Task 6 fixture gitignore mirror per [[d342c481]]).
4. **`LockfileError` for all failure modes.** Wraps `OSError`, `BlockingIOError`, `PermissionError`. Module never leaks platform-specific exception types.
5. **Lock is released on process exit by the OS.** No `__exit__` cleanup needed for crash recovery; we still implement `release()` for clean shutdown and test it.
6. **JSON payload is informational only.** The lock IS the OS lock, not the JSON. Tests assert this: a corrupted/empty JSON payload doesn't break the lock semantics.

**Files:**
- Create: `ralph_executor/lockfile.py`
- Create: `tests/executor/test_lockfile.py`

- [ ] **Step 1: Failing tests.**

```python
# tests/executor/test_lockfile.py
from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

import pytest

from ralph_executor.lockfile import LockfileError, WorkspaceLockfile


def test_acquire_creates_lockfile(tmp_path: Path):
    lock = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-a", hostname="box-a")
    lock.acquire()
    try:
        info = json.loads((tmp_path / ".ralph.lock").read_text())
        assert info["instance_id"] == "ralph-a"
        assert info["hostname"] == "box-a"
        assert info["pid"] > 0
    finally:
        lock.release()


def test_second_acquire_same_process_raises(tmp_path: Path):
    a = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-a", hostname="box-a")
    b = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-b", hostname="box-a")
    a.acquire()
    try:
        with pytest.raises(LockfileError, match="another ralph already running"):
            b.acquire()
    finally:
        a.release()


def test_release_allows_reacquire(tmp_path: Path):
    a = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-a", hostname="box-a")
    a.acquire()
    a.release()
    b = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-a", hostname="box-a")
    b.acquire()
    b.release()


def test_corrupted_payload_does_not_break_lock(tmp_path: Path):
    """If the JSON payload is malformed (e.g. partial prior crash), the lock
    semantics still work — the OS lock is the source of truth, not the JSON."""
    (tmp_path / ".ralph.lock").write_text("not json")
    a = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-a", hostname="box-a")
    a.acquire()
    a.release()


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl-only test")
def test_cross_process_contention_posix(tmp_path: Path):
    """Spawn a second process; second acquire fails with LockfileError."""
    lock_path = tmp_path / ".ralph.lock"
    a = WorkspaceLockfile(lock_path, instance_id="ralph-a", hostname="box-a")
    a.acquire()
    try:
        proc = multiprocessing.Process(
            target=_attempt_acquire, args=(str(lock_path), "ralph-b", "box-a")
        )
        proc.start()
        proc.join(timeout=5)
        assert proc.exitcode == 1  # second acquire should fail
    finally:
        a.release()


def _attempt_acquire(path: str, instance_id: str, hostname: str) -> None:
    """Helper run in subprocess; exits 1 on LockfileError, 0 on unexpected success."""
    try:
        lock = WorkspaceLockfile(Path(path), instance_id=instance_id, hostname=hostname)
        lock.acquire()
    except LockfileError:
        sys.exit(1)
    sys.exit(0)
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement.**

```python
# ralph_executor/lockfile.py
"""OS-level exclusive workspace lockfile.

Each ralph instance holds an exclusive lock on
``<workspace>/queue-<instance-id>/.ralph.lock`` from startup to process exit.
The OS releases the lock on process death (clean or crash); no stale-lock
recovery logic is needed.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class LockfileError(RuntimeError):
    """Raised when the workspace lockfile cannot be acquired or released."""


class WorkspaceLockfile:
    """Cross-platform exclusive lockfile."""

    def __init__(self, path: Path, *, instance_id: str, hostname: str) -> None:
        self._path = path
        self._instance_id = instance_id
        self._hostname = hostname
        self._fd: int | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            raise LockfileError(f"could not open lockfile {self._path}: {exc}") from exc

        try:
            if sys.platform == "win32":
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError, OSError) as exc:
            existing = self._read_payload_safely()
            os.close(self._fd)
            self._fd = None
            raise LockfileError(
                f"another ralph already running on this workspace: {existing or '<no payload>'}"
            ) from exc

        payload = {
            "instance_id": self._instance_id,
            "hostname": self._hostname,
            "pid": os.getpid(),
            "started_at": datetime.now(UTC).isoformat(),
        }
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, 0)
        os.write(self._fd, json.dumps(payload).encode())

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def _read_payload_safely(self) -> str | None:
        try:
            return self._path.read_text()
        except OSError:
            return None
```

- [ ] **Step 4: Run, PASS.**

- [ ] **Step 5: Commit.**

```bash
git add ralph_executor/lockfile.py tests/executor/test_lockfile.py
git commit -m "feat(lockfile): cross-platform workspace exclusive lock"
```

---

### Task 6: `queue_clone` path becomes `queue-<instance-id>/` + legacy rename

**Confidence: 92%** (lifted from 87%). Risk: tests calling `ensure_queue_clone(workspace_root, queue_repo, queue_branch)` break on signature change.

**Confidence lifts applied:**
1. **Grep `ensure_queue_clone(` across `tests/` BEFORE landing the signature change.** Per [[462d13a7-grep-test-callsites]]. Command listed in Step 1. Every call-site updated in the same commit.
2. **Legacy rename idempotency tested.** Three cases: (a) only `queue/` exists → rename; (b) only `queue-<id>/` exists → no-op; (c) both exist → `QueueCloneError` (loud refusal, don't merge).
3. **Pull-forward guardrail per [[462d13a7]]:** when a subagent flags "pre-existing test failure" while implementing this task, grep test fixtures for `queue_clone_path` references — they may now be off by a directory name. Fix in this task, not deferred.
4. **`instance_id` arg is keyword-only.** Forces every call-site to spell out the argument; no silent positional mis-binding.

**Files:**
- Modify: `ralph_executor/queue_clone.py`
- Modify: `tests/executor/test_queue_clone.py`
- Modify: every test that calls `ensure_queue_clone(`

- [ ] **Step 1: Grep call-sites.**

```bash
grep -rn 'ensure_queue_clone(' ralph_executor/ tests/
```

- [ ] **Step 2: Failing tests.**

```python
# tests/executor/test_queue_clone.py — additions
def test_namespaced_path(tmp_path, monkeypatch, fake_remote):
    dest = ensure_queue_clone(
        workspace_root=tmp_path,
        queue_repo=fake_remote,
        queue_branch="ralph-queue",
        instance_id="ralph-a",
    )
    assert dest == tmp_path / "queue-ralph-a"
    assert (dest / ".git").is_dir()


def test_legacy_queue_renamed_to_namespaced(tmp_path, fake_remote):
    # arrange: legacy clone at workspace_root/queue/
    legacy = tmp_path / "queue"
    _clone_into(legacy, fake_remote)
    # act
    dest = ensure_queue_clone(
        workspace_root=tmp_path,
        queue_repo=fake_remote,
        queue_branch="ralph-queue",
        instance_id="ralph-a",
    )
    # assert
    assert dest == tmp_path / "queue-ralph-a"
    assert not legacy.exists()
    assert (dest / ".git").is_dir()


def test_both_paths_exist_refuses(tmp_path, fake_remote):
    legacy = tmp_path / "queue"
    namespaced = tmp_path / "queue-ralph-a"
    _clone_into(legacy, fake_remote)
    _clone_into(namespaced, fake_remote)
    with pytest.raises(QueueCloneError, match="both legacy queue/ and queue-ralph-a/ exist"):
        ensure_queue_clone(
            workspace_root=tmp_path,
            queue_repo=fake_remote,
            queue_branch="ralph-queue",
            instance_id="ralph-a",
        )
```

- [ ] **Step 3: Run, fail.**

- [ ] **Step 4: Implement.**

`ensure_queue_clone` now takes `instance_id: str` as a keyword-only arg. Dest is `workspace_root / f"queue-{instance_id}"`. Before first clone, if `workspace_root/queue/` exists AND `workspace_root/queue-<id>/` does NOT exist, atomically rename `queue/` → `queue-<id>/`. If both exist, raise `QueueCloneError`.

- [ ] **Step 5: Thread new kwarg through all callers.**

Loop's `_pull_queue` must pass `cfg.instance_id`. Every fixture that constructs `ExecutorConfig` already has `instance_id` (Task 1). Every test that calls `ensure_queue_clone` directly needs the kwarg.

- [ ] **Step 6: Run, PASS.**

- [ ] **Step 7: Commit.**

```bash
git add ralph_executor/queue_clone.py ralph_executor/loop.py \
  tests/executor/test_queue_clone.py
git commit -m "feat(queue_clone): namespaced path queue-<instance-id>/ + legacy rename"
```

---

### Task 7: Acquire workspace lockfile at executor startup

**Confidence: 93%** (lifted from 88%). Risk: lockfile lifecycle interacts with loop signal handling.

**Confidence lifts applied:**
1. **Acquire BEFORE `iterate_once` begins; release on clean exit.** OS handles crash exit. Lifecycle is `acquire → run loop → release on clean exit; OS releases on crash`. Documented in `loop.run_loop`.
2. **`atexit.register(lock.release)` for clean shutdown.** Belt-and-braces; OS would handle it anyway but `atexit` makes the JSON payload disappear which is cleaner for the next operator.
3. **Test asserts release happens on `SystemExit` from within the loop** (`atexit` runs).
4. **Lockfile path is `<workspace>/queue-<instance>/.ralph.lock`.** Same dir as the queue clone, but the lockfile is NOT inside `.git/` and not in `.ralph/state/`. `.gitignore` in Task 8 adds `.ralph.lock` explicitly.

**Files:**
- Modify: `ralph_executor/loop.py` (or wherever `run_loop`/`main` lives)
- Modify: `tests/executor/test_loop_integration.py`

- [ ] **Step 1: Failing test.**

```python
def test_lockfile_acquired_at_startup(tmp_path, fake_config):
    cfg = fake_config(workspace_root=tmp_path, instance_id="ralph-a")
    # ... arrange minimal queue ...
    run_loop(cfg, max_iterations=0)  # don't iterate; just startup/teardown
    assert (tmp_path / "queue-ralph-a" / ".ralph.lock").exists()


def test_second_startup_same_workspace_refuses(tmp_path, fake_config):
    cfg_a = fake_config(workspace_root=tmp_path, instance_id="ralph-a")
    cfg_b = fake_config(workspace_root=tmp_path, instance_id="ralph-a")  # same instance
    # acquire from a thread / subprocess; second startup must raise
    ...
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement.**

```python
# ralph_executor/loop.py — pseudo
def run_loop(cfg: ExecutorConfig, ...) -> int:
    lock_path = cfg.workspace_root / f"queue-{cfg.instance_id}" / ".ralph.lock"
    lock = WorkspaceLockfile(lock_path, instance_id=cfg.instance_id, hostname=socket.gethostname())
    lock.acquire()
    atexit.register(lock.release)
    try:
        return _run_loop_body(cfg, ...)
    finally:
        lock.release()
```

Per [[aa125f7a-empty-input-guard]]: ensure `LockfileError` propagates with a useful message; don't swallow.

- [ ] **Step 4–5: Run + commit.**

```bash
git add ralph_executor/loop.py tests/executor/test_loop_integration.py
git commit -m "feat(loop): acquire workspace lockfile at startup + release on exit"
```

---

### Task 8: `CLAIM.json` IO helpers

**Confidence: 95%.** Tiny module of `read_claim` + `write_claim` + schema constants.

**Files:**
- Modify: `ralph_executor/queue/filesystem.py` (or new submodule `claim.py`)
- Modify: `tests/executor/queue/test_filesystem.py`

- [ ] **Step 1–5: TDD.**

```python
# claim helpers
@dataclass(frozen=True, slots=True)
class Claim:
    instance_id: str
    claimed_at: str  # ISO-8601 UTC
    hostname: str

    def to_json(self) -> str:
        return json.dumps({
            "instance_id": self.instance_id,
            "claimed_at": self.claimed_at,
            "hostname": self.hostname,
        }, indent=2)

    @classmethod
    def from_path(cls, path: Path) -> Claim:
        data = json.loads(path.read_text())
        return cls(
            instance_id=data["instance_id"],
            claimed_at=data["claimed_at"],
            hostname=data["hostname"],
        )
```

Tests cover: write/read round-trip; missing required key raises `ClaimError`; unicode hostname round-trips.

- [ ] **Step 6: Commit.**

```bash
git add ralph_executor/queue/filesystem.py tests/executor/queue/test_filesystem.py
git commit -m "feat(claim): CLAIM.json read/write helpers"
```

---

### Task 9: `_claim_pbi` writes CLAIM.json atomically with the move

**Confidence: 93%** (lifted from 88%). Risk: `git mv` + new-file-in-moved-dir interaction (per [[63c4e75a]]).

**Confidence lifts applied:**
1. **Order: `git mv` first, then `write CLAIM.json` into the new location, then `git add CLAIM.json`, then commit.** Per [[63c4e75a]]: `git mv DIR NEWDIR` only moves index-tracked files; CLAIM.json is brand-new so MUST be staged separately. Captured as a test assertion.
2. **Single commit subject pinned** per [[d62c9e9e]]: `chore(queue): claim <id> for <instance-id>`. Test asserts the exact subject after push.
3. **Push-rebase loser drops the claim cleanly.** Test simulates a second writer winning; first writer's `push_with_rebase` raises `PushRebaseConflict`; existing loop handler rolls back; assert: no leftover `current/<id>/` on the first writer's clone after retry.

**Files:**
- Modify: `ralph_executor/loop.py::_claim_pbi`
- Modify: `tests/executor/test_loop.py`

- [ ] **Step 1: Failing tests.**

```python
def test_claim_writes_claim_json_and_commits_one_subject(...):
    # arrange: a fake remote with inbox/<id>/
    # act: claim
    # assert: current/<id>/CLAIM.json exists with our instance_id
    # assert: latest commit subject == "chore(queue): claim <id> for ralph-a"
    # assert: commit touches both the moved files AND CLAIM.json


def test_claim_loses_rebase_race_cleanly(...):
    # arrange: simulate origin advancing between our local commit and push
    # act: claim
    # assert: PushRebaseConflict raised, current/<id>/ not on our local clone
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement.**

```python
# ralph_executor/loop.py::_claim_pbi (rough)
def _claim_pbi(self, pbi: PBI) -> None:
    src = self._queue_root / "inbox" / pbi.id
    dst = self._queue_root / "current" / pbi.id
    git_ops.mv(self._queue_root, src, dst)
    claim = Claim(
        instance_id=self._cfg.instance_id,
        claimed_at=datetime.now(UTC).isoformat(),
        hostname=socket.gethostname(),
    )
    claim_path = dst / "CLAIM.json"
    claim_path.write_text(claim.to_json())
    git_ops.add(self._queue_root, claim_path)
    git_ops.commit(
        self._queue_root,
        subject=f"chore(queue): claim {pbi.id} for {self._cfg.instance_id}",
    )
    push_with_rebase(self._queue_root, branch=self._cfg.queue_branch)
```

- [ ] **Step 4–5: Run + commit.**

```bash
git add ralph_executor/loop.py tests/executor/test_loop.py
git commit -m "feat(claim): atomic mv + CLAIM.json + commit for claim path"
```

---

### Task 10: `current_pbi()` filters by `instance_id`

**Confidence: 95%.** Tight contract change in `FilesystemQueueSource.current_pbi()`.

**Files:**
- Modify: `ralph_executor/queue/filesystem.py`
- Modify: `tests/executor/queue/test_filesystem.py`

- [ ] **Step 1–5: TDD.**

Tests cover:
- One own claim → returns that PBI
- One own + one foreign claim → returns own
- Only foreign claims → returns None
- Two own claims → raises `QueueError("multiple own claims")` (hard invariant)
- No `CLAIM.json` in a `current/<id>/` dir → raises `QueueError("malformed claim")`

- [ ] **Step 6: Commit.**

```bash
git add ralph_executor/queue/filesystem.py tests/executor/queue/test_filesystem.py
git commit -m "feat(filesystem): current_pbi filters by instance_id"
```

---

### Task 11: META-BUG carries `tripped_by_instance`

**Confidence: 95%.** One-line addition to the halt-sentinel writer.

**Files:**
- Modify: `ralph_executor/loop.py` (META-BUG writer)
- Modify: tests covering the halt path

- [ ] **Step 1–5: TDD.**

Test asserts META-BUG.md frontmatter contains `tripped_by_instance: ralph-a` matching `cfg.instance_id`.

- [ ] **Step 6: Commit.**

```bash
git add ralph_executor/loop.py tests/executor/test_loop.py
git commit -m "feat(halt): META-BUG carries tripped_by_instance"
```

---

### Task 12: `ralph-status` OWNER column

**Confidence: 92%** (lifted from 88%). Risk: human-readable formatting + JSON output both need updating consistently.

**Confidence lifts applied:**
1. **Both `--json` and table outputs updated in the same commit.** Test asserts both.
2. **`OWNER` cell shows `—` (em-dash) when the PBI is not in `current/` OR when `CLAIM.json` is missing.** Test parametrised over (in-current/in-inbox/missing-CLAIM) × (own/foreign/none).
3. **JSON key is `owner` (snake-case), not `OWNER`.** Consistent with other JSON keys in status output.

**Files:**
- Modify: `skills/ralph-status/scripts/status.py`
- Modify: `tests/skills/test_ralph_status.py`

- [ ] **Step 1–5: TDD.**

- [ ] **Step 6: Commit.**

```bash
git add skills/ralph-status/scripts/status.py tests/skills/test_ralph_status.py
git commit -m "feat(ralph-status): OWNER column from CLAIM.json instance_id"
```

---

### Task 13: `ralph-cancel` refuses foreign CLAIM

**Confidence: 95%.** Single guard in `cancel.py`.

**Files:**
- Modify: `skills/ralph-cancel/scripts/cancel.py`
- Modify: `tests/skills/test_ralph_cancel.py`

- [ ] **Step 1–5: TDD.**

Tests:
- Own CLAIM → cancel proceeds
- Foreign CLAIM → exit 3 with message `ralph-cancel: cannot cancel PBI claimed by '<other>'; use ralph-recover`
- Missing CLAIM.json → exit 3 with `ralph-cancel: PBI in current/ but no CLAIM.json`

- [ ] **Step 6: Commit.**

```bash
git add skills/ralph-cancel/scripts/cancel.py tests/skills/test_ralph_cancel.py
git commit -m "feat(ralph-cancel): refuse on foreign CLAIM.json"
```

---

### Task 14: `ralph-promote` refuses cross-instance ownership

**Confidence: 95%.** Mirror of Task 13 in `promote.py`.

**Files:**
- Modify: `skills/ralph-promote/scripts/promote.py`
- Modify: `tests/skills/test_ralph_promote.py`

- [ ] **Step 1–5: TDD.**

- [ ] **Step 6: Commit.**

```bash
git add skills/ralph-promote/scripts/promote.py tests/skills/test_ralph_promote.py
git commit -m "feat(ralph-promote): refuse cross-instance ownership transfer"
```

---

### Task 15: `ralph-recover` skill (new)

**Confidence: 93%** (lifted from 88%). Risk: orchestration glue across queue git operations.

**Confidence lifts applied:**
1. **All git operations route through existing `git_ops` / `movements` helpers per [[a5af973e]] — NEVER raw `shutil.move`.** Test asserts a single commit subject lands on origin after recover.
2. **Refuses to run when halt sentinel active** — Test asserts exit 4 with `ralph-recover: halt sentinel active`.
3. **Attempt counter reset on `--to inbox`.** Per spec — an orphaned claim is not a failed attempt. Test asserts `.ralph/attempts/<id>` is cleared.
4. **`HISTORY.md` append** uses the existing `commit_all`-style helper, not raw file write — keeps the recover commit atomic with the move.
5. **Result schema includes `dry_run_skipped: bool = False`** per [[852f5ae9]] for forward-compat.
6. **No `--force` flag** per spec — operator invocation is itself the deliberate action.

**Files:**
- Create: `skills/ralph-recover/SKILL.md`
- Create: `skills/ralph-recover/scripts/__init__.py`
- Create: `skills/ralph-recover/scripts/recover.py`
- Create: `tests/skills/test_ralph_recover.py`

- [ ] **Step 1: Failing tests (8 cases).**

- Happy path: `--to inbox` from `current/<id>/` with foreign CLAIM → moved, CLAIM.json deleted, HISTORY appended, attempt counter reset, single commit pushed
- Happy path: `--to blocked` → moved, CLAIM.json deleted, HISTORY appended, attempt counter NOT reset
- Refuses: PBI not in `current/`
- Refuses: destination already contains a directory with the same id
- Refuses: halt sentinel active
- Stderr audit: prints existing `CLAIM.json` content before move
- Commit subject pinned: `chore(queue): recover <id> from <instance>`
- No `CLAIM.json` in destination

- [ ] **Step 2–4: TDD impl.**

- [ ] **Step 5: SKILL.md.**

Two-line frontmatter (name, description); workflow section with the two `--to inbox|blocked` invocations + required env vars + audit notes.

- [ ] **Step 6: Commit.**

```bash
git add skills/ralph-recover/ tests/skills/test_ralph_recover.py
git commit -m "feat(ralph-recover): manual claim-recovery skill"
```

---

### Task 16: Cross-instance integration tests (T1–T5)

**Confidence: 92%** (lifted from 80%). Risk: cross-process / cross-instance interleaving is non-trivial to fake.

**Confidence lifts applied:**
1. **No mocking framework. Pure file IO + two `ExecutorConfig` instances sharing a single bare `queue_repo` fixture.** Drive `iterate_once` directly — no subprocess.
2. **Each test pins the commit subject after `push_with_rebase` per [[d62c9e9e]].** Catches "moved but didn't commit" bugs.
3. **T5 lockfile contention test gated `@pytest.mark.skipif(sys.platform != "win32", reason="cross-process subprocess test requires posix")`-style only where genuinely needed.** POSIX is the load-bearing CI lane.
4. **Fixture `.gitignore` per [[d342c481]] covers `.ralph/state/`, `.ralph-work/`, `.ralph.lock`** so test helpers' `git add -A` doesn't accidentally stage runtime state.
5. **Per [[022dafc3-failing-test-fixture-precond]]**: each test seeds the queue with the exact inbox / current state it asserts on; no test depends on previous-test state.

**Files:**
- Create: `tests/executor/test_multi_ralph_integration.py`

- [ ] **Step 1: Implement 5 tests (T1–T5).**

T1 — Two distinct PBIs, two distinct instances. Both `current/<id>/CLAIM.json` correct after parallel `iterate_once`.

T2 — One inbox PBI, two instances racing. Push-rebase loser observes `PushRebaseConflict`; both clones converge with exactly one own-claim, one foreign-claim view.

T3 — ralph-a's PBI promotes to `pending-pr/`. ralph-b's sweep iteration runs on the same `pending-pr/`. ralph-b does NOT process ralph-a's PR (foreign claim ignored). Assert: no extra commits, no PR comments by ralph-b.

T4 — ralph-a writes halt sentinel. ralph-b's next `iterate_once` raises `HaltedError`. Assert: META-BUG.md `tripped_by_instance: ralph-a`.

T5 — Two `WorkspaceLockfile` acquisitions on the same path. Second raises `LockfileError` with the JSON payload from the first.

- [ ] **Step 2: Run, PASS.**

- [ ] **Step 3: Commit.**

```bash
git add tests/executor/test_multi_ralph_integration.py
git commit -m "test(multi-ralph): cross-instance integration scenarios T1-T5"
```

---

### Task 17: Docs — README + `docs/runbooks/ralph-setup.md`

**Confidence: 95%.** Docs-only.

Per [[98056ebc-docs-in-sync]]: docs ship in same PR.

**Files:**
- Modify: `README.md` — add "Running multiple ralphs" section + upgrade procedure
- Modify: `docs/runbooks/ralph-setup.md` — `instance_id` TOML key, `--instance-id` flag, lockfile semantics, `ralph-recover` usage

- [ ] **Step 1: Update README.**

New section after the existing "Running ralph" section. Walks through:
- N=1 default (hostname becomes instance_id; transparent)
- N≥2 across hosts: each host sets `instance_id` explicitly in TOML or via `RALPH_INSTANCE_ID`
- Same-host concurrent: refused by lockfile; documented
- Upgrade procedure: drain `current/` before upgrading; legacy `queue/` auto-rename on first startup with new binary

Per [[ca0308d6-flag-default-grep]]: when documenting `instance_id` defaults, copy values directly from `config.py` constants — don't paraphrase.

- [ ] **Step 2: Update runbook.**

Add "Running multiple ralphs" subsection. Cover `ralph-recover` workflow.

- [ ] **Step 3: Verify every flag in docs against argparse `--help`** (per [[98056ebc]] + the now-mandatory `skill_check`-style discipline).

- [ ] **Step 4: Commit.**

```bash
git add README.md docs/runbooks/ralph-setup.md
git commit -m "docs(multi-ralph): Running multiple ralphs + upgrade procedure"
```

---

### Task 18: PR review + merge

**Confidence: 93%.** Standard squash-merge after CI green.

- [ ] **Step 1: Push branch + open PR.**

```bash
git push -u origin feat/multi-ralph-scope-1
gh pr create --title "Multi-ralph Scope 1: per-instance identity + lockfile + ralph-recover" --body "$(cat <<'EOF'
## Summary
- Per-instance namespaced queue clone `<workspace>/queue-<instance-id>/`
- `instance_id` resolution chain: CLI → env → TOML → sanitised hostname
- CLAIM.json marker per claimed PBI; `current_pbi()` filters by instance_id
- OS-level workspace lockfile (POSIX + Windows)
- New `ralph-recover` operator skill for stuck-claim recovery
- META-BUG carries `tripped_by_instance`
- Legacy `<workspace>/queue/` auto-renamed to `queue-<id>/` on first startup

## Test plan
- [ ] All existing unit/integration tests green
- [ ] New cross-instance integration tests T1–T5 green
- [ ] Lockfile contention test passes on Linux CI
- [ ] `ruff check` AND `ruff format --check` both green (CI runs both)
- [ ] `mypy --strict` green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for CI green.**

If `ruff format --check` fails per [[ruff-two-gates]] — run `uv run ruff format .` locally, commit, push.

- [ ] **Step 3: Address BugBot findings as they land.**

Per [[4c179d52-validation-gap-pre-merge]]: any validation gap a reviewer flags gets fixed pre-merge, not deferred.

- [ ] **Step 4: Merge.**

```bash
gh pr merge --squash --delete-branch
```

---

## Self-review

**Spec coverage check:**

| Spec section | Plan task(s) |
| --- | --- |
| §Identity resolution | T1, T2, T3 |
| §Workspace layout | T6 |
| §Concurrency surface | T16 (T2 race test) |
| §Claim protocol | T8, T9 |
| §CLAIM.json schema | T8 |
| §current_pbi() semantics | T10 |
| §pick_next / dependency graph | (unchanged; no task needed) |
| §Sweep | T16 (T3 sweep isolation test) |
| §Halt sentinel | T11, T16 (T4 halt test) |
| §Workspace lockfile | T5, T7, T16 (T5 contention test) |
| §Skill-level guards | T12, T13, T14 |
| §`ralph-recover` skill | T15 |
| §Upgrade procedure | T6 (legacy rename), T17 (docs) |
| §Testing § Unit | T1, T5, T8, T9, T10, T15 |
| §Testing § Integration | T16 (T1–T5) |
| §Testing § Skill-level | T12, T13, T14, T15 |
| §Rollout | (no separate task; release-notes drafted in T17's PR description) |

No gaps.

**Placeholder scan:** No "TBD"/"TODO"/"implement later". `...` ellipsis in pseudo-code blocks is intentional and labelled as illustrative.

**Type consistency:** `ExecutorConfig.instance_id: str` (non-Optional after Task 1). `WorkspaceLockfile`, `Claim`, `LockfileError`, `ClaimError`, `QueueCloneError` (existing). `validate_instance_id`, `sanitise_hostname`, `resolve_instance_id` defined in T1. All consistent across tasks.

**Cross-read prose vs example code:**
- `instance_id` regex `^[a-z0-9][a-z0-9_-]{0,62}$` (T1 prose) matches the test `"a" * 63` (1 leading alnum + 62 follow-on, total 63). ✓
- Test `validate_instance_id("a" * 63)` passes the validator (length matches the `{0,62}` follow-on bound for a total of 63 chars). ✓
- Commit subject pinned in T9 prose AND test: `chore(queue): claim <id> for <instance-id>`. ✓
- `from datetime import UTC` used in lockfile.py (T5) and claim helpers (T8). ✓
- No `(str, Enum)` patterns. ✓

**Task confidence summary:**

| Task | Confidence |
| --- | --- |
| 1 | 95% |
| 2 | 95% |
| 3 | 95% |
| 4 | 93% |
| 5 | 92% |
| 6 | 92% |
| 7 | 93% |
| 8 | 95% |
| 9 | 93% |
| 10 | 95% |
| 11 | 95% |
| 12 | 92% |
| 13 | 95% |
| 14 | 95% |
| 15 | 93% |
| 16 | 92% |
| 17 | 95% |
| 18 | 93% |

**Minimum: 92%. All tasks ≥ 91%. No task below the user-mandated floor.**

---

## Execution handoff

After this plan is committed and rendered in the visualiser, the user picks:

1. **Ralph PBI (hands-off)** — file as feature PBI on ralph-queue with `target_repo: emp3thy/ralph`. Ralph executes autonomously.
2. **Subagent-driven** — fresh subagent per task in this session, two-stage review between tasks.
3. **Inline execution** — batch with checkpoints via `superpowers:executing-plans`.
