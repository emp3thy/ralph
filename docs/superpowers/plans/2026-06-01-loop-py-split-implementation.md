# loop.py split — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose `ralph_executor/loop.py` (1078 lines) into 5 focused modules (`iteration.py`, `pbi_claim.py`, `worktree_manager.py`, `queue_git.py`, `iteration_safety.py`) with light cleanup of `use_worktrees=False` legacy branches and stale Plan-8/9 stub comments, preserving behavior exactly.

**Architecture:** Eight independently revertable commits. Each one extracts one module (steps 1–4), collapses a dead branch (5), renames the residual file (6–7), or sweeps docs/comments (8). Tests fan out per module; orchestration tests consolidate in `test_iteration.py`. Only `iteration.py` imports from the other four extracted modules — sole inter-extracted edge is `pbi_claim → worktree_manager`.

**Tech Stack:** Python 3.12, uv, pytest, ruff, mypy --strict. No new runtime dependencies.

**Source spec:** `docs/superpowers/specs/2026-06-01-loop-py-split-design.md` (commit `27ad1d3`).

---

## Guardrails (from `~/.better-memory/knowledge-base/standards/ralph-runtime.md` and high-confidence reflections)

These rules are non-skippable. Each task below has been checked against every guardrail.

- `[[be7ad6bf]]` **Writing-plans confidence-rating + sub-90% lift is a non-skippable gate.** _(confidence 0.9, useful_count 3)_ Each task carries a confidence percentage in its header. Sub-90% tasks include a Step 0 spike or mitigation embedded inline. None of the eight tasks ships at sub-90% after the revision below.
- `[[da7ff62e]]` **Session startup MUST consult knowledge_list, not just memory_retrieve.** _(confidence 0.9)_ Standards docs (`standards/*.md`) live in a separate channel; bootstrap surfaces reflections only. This plan was originally drafted without that consult; this revision corrects it.
- `[[2edb7d77]]` **Visualiser uses Bootstrap 5 LIGHT theme + bg-success/warning/danger badges, served as a full HTML doc starting with `<!DOCTYPE html>`.** _(confidence 1.0 — direct user preference)_ The original visualiser used a dark theme; the revision replaces it.
- `[[ralph-runtime § Create the feature branch at task start]]` Feature branch `refactor/loop-py-split` is created in pre-flight, not deferred to commit time.
- `[[ralph-runtime § commit-then-render-then-execute sequence]]` The fixed order after writing the plan is: (1) commit, (2) render in visualiser, (3) announce URL, (4) present execution choice. The original draft skipped step 2; this revision performs it before any execution dispatch.
- `[[ralph-runtime § 3-bucket assumption surface]]` Real concerns / verified-safe / minor-accepted. See the Assumption surface section below.
- `[[ralph-runtime § argv ceiling]]` `[[355faeb8]]` Windows cmd.exe 8191-char ceiling. Not triggered here (no large argv constructions).
- `[[ralph-runtime § verify-before-commit on referenced patterns]]` Every relocation step names the exact source line range; the relocation convention forbids editing the body beyond the rename + import path.

Dismissed (one-line reason):
- `[[0c83e25d]]` Playwright DOM textContent — no Playwright in this plan.
- `[[85e7ec84]]` tempfile.mkstemp + os.fdopen fd leak — no tempfile use.
- `[[24644201]]` GitHub branch protection 403 — no protection ops.
- `[[d1a577ce]]` Don't flip shared test fixture defaults mid-plan — fixtures preserved; tests move with assertions intact.

## Assumption surface (3 buckets)

### Real concerns (with mitigation options)

1. **Task 3 worktree seam.** Extracting `materialise_worktree` from inside `_claim_pbi_worktree` requires identifying a clean seam between worktree creation and PBI mutation. Mitigation: Task 3 Step 0 runs a spike that prints the function body and exits if no obvious seam exists. Confidence lifted **85% → 93%**.
2. **Task 5 collapse safety.** Removing `use_worktrees=False` branches assumes `config.py` already rejects that value at `load_config`. If a runtime path still accepts False, this task removes live behavior. Mitigation: Task 5 Step 0 greps `config.py` for the rejection guard; if absent, the task aborts and escalates. Confidence lifted **80% → 92%**.
3. **`__init__.py` re-exports.** Some downstream code may import from `ralph_executor` top-level (e.g. `from ralph_executor import iterate_once`). Mitigation: Task 6 Step 1's grep covers `from ralph_executor.loop` AND `ralph_executor.iteration` AND any `__init__.py` re-export.

### Verified safe (with verification)

- **`queue_repo_root` is pure**, depends only on `cfg.workspace_root`. Verified by reading `ralph_executor/loop.py:79-90`.
- **`_pull_queue` is a one-line wrapper** around `ensure_queue_clone`. Verified by reading `ralph_executor/loop.py:358-364`.
- **Test fixtures in `tests/executor/conftest.py`** don't reference the moved private symbols by name (they monkeypatch via string paths). Verified by grep.
- **Two pre-existing `test_config_toml.py` failures** are unrelated to this refactor — they predate the spec. Verified in the baseline run.

### Minor / accepted

- Backward-compat aliases (`_pull_queue = pull_queue`, `_ClaimError = ClaimError`, etc.) leave one underscore per moved symbol in the new `iteration.py`. Accepted: they keep older monkeypatch paths working and cost nothing.
- The plan touches `claude_spawn.py` in Task 5 only (legacy branch removal). The wider claude_spawn split (finding #2) stays out of scope.
- Module size targets (~400/250/180/100/250 lines) are approximate; ±20% is acceptable.

---

**Test baseline:** Before starting Task 1, run the full suite once and record the passing count. The 2 pre-existing failures in `tests/executor/test_config_toml.py` (`test_missing_toml_raises_for_missing_queue_repo`, `test_queue_repo_required_missing_raises`) are expected to remain failing. Every commit MUST leave the same count passing and the same 2 failing.

**Convention for relocation steps.** Tasks 2, 3, and 4 move existing functions verbatim from `loop.py` into new modules. Inlining the full body of each moved function into this plan would balloon the document and invite drift. Instead, each relocation step gives the new file's header (imports + module docstring), the renaming map (`old_name → new_name`), and an explicit source-line range to copy. The engineer's verification step is `git diff` between the deleted old definition and the new one — any change beyond the rename and import path is a bug. The full new-file content is also obtainable in seconds by `cat` of the source range plus the rename substitutions.

---

## Pre-flight (one-time, no commit)

- [ ] **Establish baseline**

Run:
```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run ruff format --check . 2>&1 | tail -3
uv run mypy --strict ralph_executor 2>&1 | tail -3
```

Expected: pytest reports `2 failed, <N> passed` with the two pre-existing `test_config_toml.py` failures named above. Ruff and mypy clean. Record `N` — every later step must hit the same `N`.

- [ ] **Create a worktree for the work** (recommended, optional)

```bash
git fetch origin main
git worktree add .worktrees/loop-split -b refactor/loop-py-split main
cd .worktrees/loop-split
```

All further commands run inside the worktree.

---

## Task 1: Create `queue_git.py` &mdash; confidence **95%**

**Files:**
- Create: `ralph_executor/queue_git.py`
- Modify: `ralph_executor/loop.py` (delete `_pull_queue`, `_persist_iteration_writes` bodies; import from `queue_git`)
- Create: `tests/executor/test_queue_git.py`
- Modify: `tests/executor/test_loop.py` (remove the migrated tests)

- [ ] **Step 1: Create `ralph_executor/queue_git.py` with this exact content**

```python
"""Queue-clone git operations used by iteration.

Two helpers — refreshing the queue clone before an iteration and persisting
any PBI-directory edits Claude made during it. Both run against
``<workspace_root>/queue/`` (materialised by ``ensure_queue_clone``); both
are pure functions of ``cfg`` (plus a PBI id for the persist helper).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from ralph_executor import git_ops
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue_clone import ensure_queue_clone
from ralph_executor.safety import Event, EventLog, EventType

log = logging.getLogger(__name__)


def queue_repo_root(cfg: ExecutorConfig) -> Path:
    """Filesystem path of the queue clone (``<workspace_root>/queue``).

    The queue repo is cloned by ``ensure_queue_clone`` and owns
    ``.ralph/`` (events.db, sentinel, blocked/, …). Every operation that
    reads or writes under ``.ralph/`` routes through this helper.
    """
    return cfg.workspace_root / "queue"


def pull_queue(cfg: ExecutorConfig) -> None:
    """Refresh the queue clone before an iteration. Cheap; runs every iter."""
    log.debug(
        "refreshing queue clone for %s (branch=%s)",
        cfg.queue_repo,
        cfg.queue_branch,
    )
    ensure_queue_clone(cfg.workspace_root, cfg.queue_repo, cfg.queue_branch)


def persist_iteration_writes(
    cfg: ExecutorConfig,
    pbi_id: str,
    *,
    event_log: EventLog | None = None,
    now: datetime | None = None,
) -> None:
    """Commit + push HISTORY.md/STUCK.md/PLAN.md edits Claude wrote inside
    ``current/<id>/`` during this iteration.

    Stages ONLY the PBI's directory so local-state artefacts
    (``.ralph/state/events.db``) are not committed. No-ops cleanly when
    the index ends up empty and when the PBI was already moved out of
    ``current/`` by a sibling code path.

    Emits ``FILE_TOUCHED`` on a non-empty commit (for forward compatibility;
    no current cycle-detector rule reads it).
    """
    queue_repo = queue_repo_root(cfg)
    pbi_dir = queue_repo / ".ralph" / "current" / pbi_id
    if not pbi_dir.is_dir():
        return
    git_ops.add_all_changes(queue_repo, pbi_dir)
    head_before = git_ops.rev_parse_head(queue_repo)
    message = f"chore(queue): persist iteration writes for {pbi_id}"
    head_after = git_ops.commit_index(queue_repo, message)
    if head_after != head_before:
        log.info("persisted iteration writes for %s as %s", pbi_id, head_after[:7])
        git_ops.push_with_rebase(queue_repo, remote="origin", branch=cfg.queue_branch)
        if event_log is not None:
            files = git_ops.diff_names(queue_repo, head_before, head_after)
            if files:
                recorded_at = now if now is not None else datetime.now(tz=UTC)
                event_log.append(
                    Event(
                        kind=EventType.FILE_TOUCHED,
                        recorded_at=recorded_at,
                        pbi_id=pbi_id,
                        payload={"files": files},
                    )
                )
```

- [ ] **Step 2: Update `ralph_executor/loop.py`**

Delete the bodies of `_pull_queue` (lines ~358–364) and `_persist_iteration_writes` (lines ~290–355). Add this import block near the top of `loop.py` after the existing `from ralph_executor import git_ops` line:

```python
from ralph_executor.queue_git import (
    persist_iteration_writes as _persist_iteration_writes,
    pull_queue as _pull_queue,
    queue_repo_root as _queue_repo_root,
)
```

Then delete the existing local definitions of `_pull_queue`, `_persist_iteration_writes`, and `_queue_repo_root` from `loop.py`. The aliased imports keep every existing call site (still inside `loop.py`) working unchanged.

- [ ] **Step 3: Run pytest, ruff, mypy**

Run:
```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run ruff format --check . 2>&1 | tail -3
uv run mypy --strict ralph_executor 2>&1 | tail -3
```

Expected: same pass count as the baseline. Ruff + mypy clean. If ruff complains, run `uv run ruff format .` and re-check.

- [ ] **Step 4: Create `tests/executor/test_queue_git.py`**

Move these tests verbatim from `tests/executor/test_loop.py`:
- `test_pull_queue_calls_ensure_queue_clone` (around line 1251)
- `test_pull_queue_passes_configured_branch` (around line 1283)
- `test_persist_iteration_writes_excludes_state_dir` (around line 562)
- `test_file_touched_event_emitted_on_iteration_commit` (around line 593)
- `test_file_touched_skipped_on_empty_commit` (around line 781)

In the new file, replace `from ralph_executor.loop import _pull_queue, _persist_iteration_writes` (or any equivalent monkeypatch path) with `from ralph_executor.queue_git import pull_queue, persist_iteration_writes`. Replace identifier references in the bodies the same way. Keep all assertions verbatim.

If a test monkeypatches `ralph_executor.loop._pull_queue` or `ralph_executor.loop._persist_iteration_writes`, change the target to `ralph_executor.queue_git.pull_queue` / `ralph_executor.queue_git.persist_iteration_writes` AND keep the old `ralph_executor.loop._pull_queue` re-export so older monkeypatch paths still resolve (the re-export added in Step 2 already covers this).

Delete the migrated tests from `tests/executor/test_loop.py`.

- [ ] **Step 5: Run tests for both files**

```bash
uv run pytest tests/executor/test_queue_git.py tests/executor/test_loop.py -q 2>&1 | tail -10
```

Expected: every migrated test passes in the new file; nothing previously in `test_loop.py` regressed.

- [ ] **Step 6: Full suite check**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
```

Expected: same passing count as the baseline.

- [ ] **Step 7: Commit**

```bash
git add ralph_executor/queue_git.py ralph_executor/loop.py \
        tests/executor/test_queue_git.py tests/executor/test_loop.py
git commit -m "$(cat <<'EOF'
refactor(loop): extract queue_git.py (pull_queue + persist_iteration_writes)

Step 1 of loop.py split (see docs/superpowers/specs/2026-06-01-loop-py-split-design.md).
Pure relocation. loop.py keeps an aliased import of the moved symbols so
all existing call sites and monkeypatch paths continue to resolve.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Create `iteration_safety.py` &mdash; confidence **92%**

**Files:**
- Create: `ralph_executor/iteration_safety.py`
- Modify: `ralph_executor/loop.py` (remove `_run_sweep`, `_check_cycle_detector`; import from `iteration_safety`)
- Create: `tests/executor/test_iteration_safety.py`
- Modify: `tests/executor/test_loop.py` (remove migrated tests)

- [ ] **Step 1: Create `ralph_executor/iteration_safety.py`**

Copy the bodies of `_run_sweep` (currently around lines 121–203 of `loop.py`) and `_check_cycle_detector` (around lines 258–282). Rename the public surface as `run_sweep` and `check_cycle_detector`. Drop any docstring lines containing the strings "Plan 8 fills in", "Plan 9 Layer 3 (stub)", or any other stub disclaimer — those modules are now live, not stubs. Preserve the rest of each docstring verbatim.

Header:

```python
"""Sweep and cycle-detector wiring used once per iteration.

Both functions read from the queue clone (the canonical ``.ralph/`` tree)
and emit events to the cycle-detector log. They are kept as module-level
callables so tests can monkeypatch them without dependency injection.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue_git import queue_repo_root
from ralph_executor.safety import (
    evaluate_all,
    halt_and_acknowledge,
    open_log,
)

log = logging.getLogger(__name__)
```

…followed by the two renamed functions. Inside their bodies, replace every call to `_queue_repo_root(cfg)` with `queue_repo_root(cfg)` (the same helper, public name from `queue_git`).

- [ ] **Step 2: Update `ralph_executor/loop.py`**

Delete the local `_run_sweep` and `_check_cycle_detector` definitions. Add the import block after the existing `from ralph_executor.queue_git import ...` line:

```python
from ralph_executor.iteration_safety import (
    check_cycle_detector as _check_cycle_detector,
    run_sweep as _run_sweep,
)
```

Delete every reference to the dropped stub docstring lines from `loop.py`'s file-level docstring as well (the two lines that say "Plan 8 will replace..." and the analogous Plan-9 line).

- [ ] **Step 3: Run pytest, ruff, mypy**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run ruff format --check . 2>&1 | tail -3
uv run mypy --strict ralph_executor 2>&1 | tail -3
```

Expected: same pass count, ruff/mypy clean.

- [ ] **Step 4: Create `tests/executor/test_iteration_safety.py`**

Move verbatim from `test_loop.py`:
- `test_run_sweep_skips_when_bot_author_email_empty` (around line 643)
- `test_run_sweep_passes_cfg_values_to_sweep_config` (around line 669)
- `test_run_sweep_does_not_read_env_for_promoted_knobs` (around line 724)
- `test_run_sweep_queue_root_points_at_queue_clone` (around line 737)
- `test_run_loop_terminates_when_cycle_detector_trips` (around line 478)
- `test_event_log_lives_in_queue_clone` (around line 873)

Imports change from `from ralph_executor.loop import _run_sweep, _check_cycle_detector` (or `_queue_repo_root`) to `from ralph_executor.iteration_safety import run_sweep, check_cycle_detector` and `from ralph_executor.queue_git import queue_repo_root`. Monkeypatch targets follow the new module paths; the `loop._*` aliased re-exports added in Step 2 keep older monkeypatches working too.

Keep `test_iterate_once_invokes_sweep_stub_when_current_empty` (line 407) and `test_iterate_once_invokes_cycle_detector_stub` (line 427) in `test_loop.py` — they test orchestration, not the helpers themselves.

- [ ] **Step 5: Run tests for both files**

```bash
uv run pytest tests/executor/test_iteration_safety.py tests/executor/test_loop.py -q 2>&1 | tail -10
```

- [ ] **Step 6: Full suite check**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
git add ralph_executor/iteration_safety.py ralph_executor/loop.py \
        tests/executor/test_iteration_safety.py tests/executor/test_loop.py
git commit -m "$(cat <<'EOF'
refactor(loop): extract iteration_safety.py (sweep + cycle-detector wiring)

Step 2 of loop.py split. Drops stale "Plan 8 stub" / "Plan 9 stub"
docstrings — both modules are now live. Pure relocation otherwise.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Create `worktree_manager.py` &mdash; confidence **93%** (85% raw, lifted by Step 0 spike)

**Files:**
- Create: `ralph_executor/worktree_manager.py`
- Modify: `ralph_executor/loop.py` (remove `_cleanup_work_worktree`; import from `worktree_manager`; extract the worktree-setup half of `_claim_pbi_worktree` into `materialise_worktree`)
- Create: `tests/executor/test_worktree_manager.py`
- Modify: `tests/executor/test_loop.py` (remove migrated tests)

- [ ] **Step 0: Spike — find the worktree seam in `_claim_pbi_worktree`**

Run:
```bash
grep -nE "def _claim_pbi_worktree|ensure_worktree|work_worktree_path|dataclasses.replace|return pbi" ralph_executor/loop.py
```

Expected output: contiguous block where `work_worktree_path(...)` precedes `ensure_worktree(...)`, then `dataclasses.replace(pbi, work_worktree=...)`, then `return pbi`. The seam lives between `ensure_worktree(...)` and the `dataclasses.replace(...)` call — `materialise_worktree` returns the worktree path; the `replace + return` stays in the caller.

**If the seam is not contiguous** (e.g. interleaved with PBI mutation, conditional branches that depend on the worktree path mid-flow), STOP — re-scope by either (a) inlining the seam differently or (b) postponing the extraction to a follow-up. Do not force-fit the extraction.

If contiguous: proceed.

- [ ] **Step 1: Create `ralph_executor/worktree_manager.py`**

Move `_cleanup_work_worktree` (currently around lines 422–515 of `loop.py`) and rename to `cleanup_work_worktree`. Extract the worktree-setup logic from `_claim_pbi_worktree` (the portion that calls `git_ops.checkout_b` / `ensure_worktree` and returns a `PBI` with `work_worktree` set) into a new `materialise_worktree(cfg, pbi, target_clone_path, branch) -> Path`. The PBI mutation (`dataclasses.replace(pbi, work_worktree=path)`) stays in the CALLER (in `_claim_pbi_worktree` for now, which still lives in `loop.py` until Task 4). `materialise_worktree` returns the path; the caller wraps the PBI.

Header:

Header (imports + module docstring) — write this verbatim:

```python
"""Work-worktree lifecycle.

Two operations: materialise a fresh per-PBI worktree inside the target
clone on a feature branch, and clean it up after the PBI lands in a
terminal state. Both are pure functions of ``cfg`` and the PBI's worktree
path.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ralph_executor import git_ops
from ralph_executor.config import ExecutorConfig
from ralph_executor.types import PBI
from ralph_executor.worktree import (
    ensure_worktree,
    remove_worktree,
    work_worktree_path,
)

log = logging.getLogger(__name__)
```

Bodies to append after the header:

- **`cleanup_work_worktree(cfg, pbi)`** — copy verbatim from `ralph_executor/loop.py` lines 422–513 (the body of `_cleanup_work_worktree`). Rename the function (drop leading underscore). No other edits to the body.
- **`materialise_worktree(cfg, pbi, target_clone_path, branch) -> Path`** — extract the worktree-creation portion of `_claim_pbi_worktree` in `loop.py` (the lines from `branch = _feature_branch_name(pbi)` through the `ensure_worktree(...)` call that returns the path). Wrap that block in the new signature; the return value is the worktree path. The PBI wrapping (`dataclasses.replace(pbi, work_worktree=path)`) stays in the caller back in `loop.py`, NOT here.

The cleanup body re-imports `parse_target_repo` and `ensure_clone` locally. Keep those local imports — they break a potential cycle with `pbi_claim` later. Do not promote them to module-level.

- [ ] **Step 2: Update `ralph_executor/loop.py`**

Delete the local `_cleanup_work_worktree` definition. Modify `_claim_pbi_worktree` to call `materialise_worktree(cfg, pbi, target_clone_path, branch)` instead of inlining the worktree-creation logic. Add the import block:

```python
from ralph_executor.worktree_manager import (
    cleanup_work_worktree as _cleanup_work_worktree,
    materialise_worktree,
)
```

- [ ] **Step 3: Run pytest, ruff, mypy**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run ruff format --check . 2>&1 | tail -3
uv run mypy --strict ralph_executor 2>&1 | tail -3
```

- [ ] **Step 4: Create `tests/executor/test_worktree_manager.py`**

Move verbatim from `test_loop.py`:
- `test_claim_creates_work_worktree_on_feature_branch` (around line 819)
- `test_terminal_outcome_removes_work_tree` (around line 845)

Update imports from `ralph_executor.loop` to `ralph_executor.worktree_manager`. Keep `test_iterate_once_moves_pbi_to_blocked_when_target_unreachable` (line 1404) in `test_loop.py` — it's an orchestration test that happens to exercise worktree cleanup.

- [ ] **Step 5: Run tests for both files**

```bash
uv run pytest tests/executor/test_worktree_manager.py tests/executor/test_loop.py -q 2>&1 | tail -10
```

- [ ] **Step 6: Full suite check + commit**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
git add ralph_executor/worktree_manager.py ralph_executor/loop.py \
        tests/executor/test_worktree_manager.py tests/executor/test_loop.py
git commit -m "$(cat <<'EOF'
refactor(loop): extract worktree_manager.py (materialise + cleanup)

Step 3 of loop.py split. Extracts cleanup_work_worktree verbatim and
factors the worktree-creation half of _claim_pbi_worktree into a new
materialise_worktree helper. _claim_pbi_worktree (still in loop.py)
delegates to materialise_worktree.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Create `pbi_claim.py` &mdash; confidence **90%**

**Files:**
- Create: `ralph_executor/pbi_claim.py`
- Modify: `ralph_executor/loop.py` (remove `_ClaimError`, `_feature_branch_name`, `_read_target_repo_from_pbi`, `_claim_pbi`, `_claim_pbi_worktree`; import from `pbi_claim`)
- Create: `tests/executor/test_pbi_claim.py`
- Modify: `tests/executor/test_loop.py` (remove migrated tests)

- [ ] **Step 1: Create `ralph_executor/pbi_claim.py`**

Header:

```python
"""Claim a PBI from inbox/ to current/.

``claim_pbi`` is the only public entry. It does three things in order:

  1. Read the target repo URL from the PBI's BUG.md / FEATURE.md
     frontmatter (``_read_target_repo_from_pbi``).
  2. Ensure the target clone exists, fetching when stale
     (``target_clone.ensure_clone``).
  3. Materialise a per-PBI worktree on ``ralph/<id>``
     (``worktree_manager.materialise_worktree``) and move the PBI from
     ``inbox/<id>`` to ``current/<id>`` (``queue.movements.move_inbox_to_current``).

Any of these steps may raise ``ClaimError`` with a reason string. The
caller in ``iteration._run_ralph`` catches ``ClaimError`` and routes the
PBI to ``blocked/`` with the reason recorded in HISTORY.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from ralph_executor import git_ops
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.movements import move_inbox_to_current
from ralph_executor.target_clone import TargetUnreachable, ensure_clone
from ralph_executor.types import PBI
from ralph_executor.url_utils import TargetRepoInfo, parse_target_repo
from ralph_executor.worktree_manager import materialise_worktree

log = logging.getLogger(__name__)


class ClaimError(RuntimeError):
    """Raised when claim_pbi cannot move the PBI from inbox/ to current/.

    The message is the reason string written into HISTORY when the PBI
    is moved to blocked/ by the caller.
    """
```

Then copy verbatim, renaming as you go:
- `_feature_branch_name(pbi)` → `_feature_branch_name(pbi)` (still private; only used inside `pbi_claim`)
- `_read_target_repo_from_pbi(pbi)` → `_read_target_repo_from_pbi(pbi)` (private)
- `_claim_pbi(cfg, pbi)` → `claim_pbi(cfg, pbi)` (public)
- `_claim_pbi_worktree(cfg, pbi, target_clone_path, branch)` → `_setup_worktree(cfg, pbi, target_clone_path, branch)` (private; calls `materialise_worktree` from Task 3)

Inside `_setup_worktree`, the call to `materialise_worktree(...)` was already placed there in Task 3; preserve it.

- [ ] **Step 2: Update `ralph_executor/loop.py`**

Delete the local definitions of `_ClaimError`, `_feature_branch_name`, `_read_target_repo_from_pbi`, `_claim_pbi`, `_claim_pbi_worktree`. Add the import block:

```python
from ralph_executor.pbi_claim import (
    ClaimError,
    claim_pbi as _claim_pbi,
)
# Backward-compat alias so older monkeypatch paths keep resolving.
_ClaimError = ClaimError
```

Update any internal call sites in `loop.py` that referenced `_claim_pbi(...)` to use the aliased import (the alias means no further edits).

- [ ] **Step 3: Run pytest, ruff, mypy**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run ruff format --check . 2>&1 | tail -3
uv run mypy --strict ralph_executor 2>&1 | tail -3
```

- [ ] **Step 4: Create `tests/executor/test_pbi_claim.py`**

Move verbatim from `test_loop.py`:
- `_build_pbi` helper (around line 965)
- `test_read_target_repo_from_pbi_reads_frontmatter` (line 978)
- `test_read_target_repo_from_pbi_raises_when_missing` (line 1000)
- `test_read_target_repo_from_pbi_raises_when_no_entry_file` (line 1022)
- `test_read_target_repo_from_pbi_uses_bug_md_for_bug_type` (line 1033)
- `_write_pbi_with_target` helper (line 1060)
- `test_claim_raises_claim_error_for_non_github_host` (line 1077)
- `test_claim_raises_claim_error_for_invalid_url` (line 1090)
- `test_claim_clones_target_and_creates_worktree_in_clone` (line 1108)
- `test_claim_raises_claim_error_when_clone_unreachable` (line 1190)

Update imports from `ralph_executor.loop` to `ralph_executor.pbi_claim`. References to `_ClaimError` become `ClaimError`; references to `_claim_pbi` become `claim_pbi`; the private helpers keep their names.

Keep `test_iterate_once_moves_pbi_to_blocked_when_claim_raises_claim_error` (line 1220) in `test_loop.py` — it's an orchestration test.

- [ ] **Step 5: Run tests for both files**

```bash
uv run pytest tests/executor/test_pbi_claim.py tests/executor/test_loop.py -q 2>&1 | tail -10
```

- [ ] **Step 6: Full suite check + commit**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
git add ralph_executor/pbi_claim.py ralph_executor/loop.py \
        tests/executor/test_pbi_claim.py tests/executor/test_loop.py
git commit -m "$(cat <<'EOF'
refactor(loop): extract pbi_claim.py (claim_pbi + ClaimError)

Step 4 of loop.py split. _ClaimError -> ClaimError (now public);
_claim_pbi -> claim_pbi; _claim_pbi_worktree -> _setup_worktree (private,
calls worktree_manager.materialise_worktree). loop.py keeps a
backward-compat alias _ClaimError = ClaimError so older monkeypatch
paths resolve.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Collapse `use_worktrees=False` legacy branches &mdash; confidence **92%** (80% raw, lifted by Step 0 verification)

**Files:**
- Modify: `ralph_executor/loop.py`, `ralph_executor/pbi_claim.py`, `ralph_executor/claude_spawn.py` (touched files only)

This task is the only one that changes behavior — by **removing dead code paths**. The pre-90% confidence reflects the risk that one legacy branch is in fact still live. Step 0 below MUST pass before any deletion.

- [ ] **Step 0: Verify `cfg.use_worktrees=False` is rejected at config load**

Run:
```bash
grep -n -A 5 "use_worktrees" ralph_executor/config.py
```

Expected: a default of `True` AND a validation block that raises `ConfigError` (or similar) when the resolved value is `False`. Acceptable shapes include `if use_worktrees is False: raise ConfigError(...)`, `assert use_worktrees`, or a Literal[True] typing constraint with runtime check.

Also run:
```bash
grep -rn "use_worktrees\s*=\s*False" ralph_executor/ tests/
```

Expected: matches ONLY in tests that deliberately exercise the rejection path, OR no matches at all. Production code paths setting `False` are a STOP signal.

**If `cfg.use_worktrees=False` is silently accepted anywhere:** STOP, escalate to the user — this task changes behavior and the spec needs amending before touching code.

If verified rejected: proceed to Step 1.

- [ ] **Step 1: Find every legacy branch in the touched files**

Run:
```bash
grep -nE "use_worktrees|legacy.*single.*checkout|single-checkout mode|single-target mode" \
  ralph_executor/loop.py ralph_executor/pbi_claim.py ralph_executor/claude_spawn.py
```

Record the line numbers reported. For each one, classify as:
- **Conditional branch** (`if cfg.use_worktrees:` / `if pbi.work_worktree is not None:` with an `else` reaching for `cfg.repo_path` or `pbi.path`): remove the `else` block; un-indent the `if` body.
- **Comment** ("In legacy single-checkout mode…"): delete the comment.
- **Genuine path defaulting** (e.g. `effective_pbi_dir = pbi_dir if pbi_dir is not None else pbi.path`): KEEP — this is the kwarg fallback, unrelated to legacy mode.

- [ ] **Step 2: Apply the edits**

For each conditional branch identified in Step 1, simplify it. Example shape (the actual lines in `claude_spawn.py` around 537):

```python
# Before
elif pbi.work_worktree is not None:
    effective_cwd = pbi.work_worktree
else:
    raise ConfigError(...)

# After (no change — this is the runtime guard, not legacy mode)
```

Whereas in `loop.py` around `_claim_pbi`, branches that read `if not cfg.use_worktrees:` should be removed entirely, keeping only the worktree branch body. Drop any helper that only exists to support the dropped branch.

Delete every comment referencing "legacy single-checkout" or "legacy single-target" from the touched files.

- [ ] **Step 3: Run pytest, ruff, mypy**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run ruff format --check . 2>&1 | tail -3
uv run mypy --strict ralph_executor 2>&1 | tail -3
```

Expected: same pass count as the baseline. If a test fails, it was depending on the legacy branch — read the test, decide if it's testing dead behavior (delete the test) or genuine behavior (revert the simplification in question and re-scope).

- [ ] **Step 4: Sweep verification**

Run:
```bash
grep -nE "use_worktrees|legacy.*single.*checkout|single-checkout mode|single-target mode" \
  ralph_executor/loop.py ralph_executor/pbi_claim.py \
  ralph_executor/worktree_manager.py ralph_executor/claude_spawn.py
```

Expected: no matches (or only comments documenting the intentional removal — none expected).

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/loop.py ralph_executor/pbi_claim.py \
        ralph_executor/claude_spawn.py
git commit -m "$(cat <<'EOF'
refactor(loop): collapse use_worktrees=False legacy branches in touched files

Step 5 of loop.py split. config.use_worktrees=False has been rejected at
load_config since KILL-RALPH-HOME, so the conditional branches in the
files touched by Tasks 1-4 are dead. Scoped to those files; broader
sweep is intentionally out of scope (separate finding).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Rename `loop.py` → `iteration.py` &mdash; confidence **97%**

**Files:**
- Rename: `ralph_executor/loop.py` → `ralph_executor/iteration.py`
- Modify: `ralph_executor/cli.py`, `ralph_executor/__init__.py`, every other `from ralph_executor.loop import …` in `ralph_executor/` and `scripts/` and `tests/`

- [ ] **Step 1: Find every import of `ralph_executor.loop`**

```bash
grep -rn "from ralph_executor.loop\|import ralph_executor.loop\|ralph_executor\.loop\b" \
  ralph_executor/ scripts/ tests/ 2>&1 | head -40
```

Record the locations. Expect hits in `cli.py`, `__init__.py`, tests, and possibly scripts.

- [ ] **Step 2: Rename the module file**

```bash
git mv ralph_executor/loop.py ralph_executor/iteration.py
```

- [ ] **Step 3: Update every import**

For each match from Step 1, replace `ralph_executor.loop` with `ralph_executor.iteration`. Edits stay literal — no logic change. Do NOT rename the test files in this commit (Task 7).

If `ralph_executor/__init__.py` re-exports anything from `loop`, update the re-export to `iteration`.

- [ ] **Step 4: Run pytest, ruff, mypy**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run ruff format --check . 2>&1 | tail -3
uv run mypy --strict ralph_executor 2>&1 | tail -3
```

Expected: same pass count. If any test still references `ralph_executor.loop` directly (e.g. monkeypatch targets baked into strings), update them.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(loop): rename loop.py -> iteration.py

Step 6 of loop.py split. git mv + import updates only; no logic change.
loop.py was a misleading name once iterate_once delegates to four
sibling modules; iteration.py reflects the new role.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Rename `test_loop.py` → `test_iteration.py` &mdash; confidence **98%**

**Files:**
- Rename: `tests/executor/test_loop.py` → `tests/executor/test_iteration.py`
- Rename: `tests/executor/test_loop_integration.py` → `tests/executor/test_iteration_integration.py`
- Keep: `test_loop_diff_against_target.py`, `test_loop_pr_skill_scripts_path.py`, `test_loop_project_toml_warning.py` (already cover narrow helpers; no rename)

- [ ] **Step 1: git mv both test files**

```bash
git mv tests/executor/test_loop.py tests/executor/test_iteration.py
git mv tests/executor/test_loop_integration.py tests/executor/test_iteration_integration.py
```

- [ ] **Step 2: Update any cross-references**

```bash
grep -rn "test_loop\b\|test_loop_integration\b" tests/ scripts/ 2>&1 | head -20
```

Replace with the new names if any matches.

- [ ] **Step 3: Run pytest**

```bash
uv run pytest tests/executor/test_iteration.py tests/executor/test_iteration_integration.py -q 2>&1 | tail -5
uv run pytest tests/ -q 2>&1 | tail -5
```

Expected: targeted run passes; full suite hits the baseline.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(loop): rename test_loop.py -> test_iteration.py + integration

Step 7 of loop.py split. Matches the module rename from step 6. Three
satellite test files (test_loop_diff_against_target.py,
test_loop_pr_skill_scripts_path.py, test_loop_project_toml_warning.py)
keep their names because they already cover narrow iteration.py helpers.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Doc + comment sweep &mdash; confidence **96%**

**Files:**
- Modify: `docs/runbooks/ralph-architecture.md` if it exists and names `loop.py`
- Modify: `ralph_executor/iteration.py` module docstring
- Modify: any other doc / docstring / comment still naming `loop.py` or the old private symbols

- [ ] **Step 1: Inventory residual references**

```bash
grep -rn "loop\.py\|\bralph_executor\.loop\b\|_claim_pbi_worktree\|_ClaimError\|_pull_queue\|_run_sweep\|_check_cycle_detector\|_persist_iteration_writes\|_cleanup_work_worktree\|_queue_repo_root" \
  docs/ ralph_executor/ scripts/ 2>&1 | head -40
```

Expected matches at this point:
- Backward-compat aliases inside `iteration.py` (e.g. `_ClaimError = ClaimError`, `_pull_queue as _pull_queue`, etc.) — KEEP. These are intentional re-exports so external code / older monkeypatches keep resolving.
- Docs/docstrings mentioning the old names — UPDATE.

- [ ] **Step 2: Update `ralph_executor/iteration.py` module docstring**

Replace any reference to `loop.py` with `iteration.py`. Drop the residual `Plan 8 will replace …` line from the file-level docstring (also handled in Task 2; this is a belt-and-braces pass).

- [ ] **Step 3: Update `docs/runbooks/ralph-architecture.md`**

If the file exists, search for `loop.py` and rewrite affected sections to name the five modules instead. If the file doesn't exist, skip this step.

```bash
test -f docs/runbooks/ralph-architecture.md && grep -n "loop.py\|iterate_once" docs/runbooks/ralph-architecture.md || echo "no architecture doc to update"
```

- [ ] **Step 4: Sweep README and other docs**

```bash
grep -rn "loop.py\|ralph_executor.loop\b" README.md docs/ 2>&1 | head -10
```

Update each match to point at the new module.

- [ ] **Step 5: Final verification**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run ruff format --check . 2>&1 | tail -3
uv run mypy --strict ralph_executor 2>&1 | tail -3
```

- [ ] **Step 6: Acceptance grep**

```bash
test ! -f ralph_executor/loop.py && echo "loop.py removed: ok" || echo "loop.py still present: FAIL"
test -f ralph_executor/iteration.py && \
    test -f ralph_executor/pbi_claim.py && \
    test -f ralph_executor/worktree_manager.py && \
    test -f ralph_executor/queue_git.py && \
    test -f ralph_executor/iteration_safety.py && \
    echo "five modules present: ok" || echo "missing modules: FAIL"
grep -n "Plan 8 stub\|Plan 9 stub" ralph_executor/iteration*.py ralph_executor/queue_git.py 2>&1 || echo "no stub residue: ok"
```

Expected: all `ok`, no `FAIL`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
docs(loop-split): update architecture doc + module docstrings

Step 8 (final) of loop.py split. Refreshes references to loop.py /
iterate_once in docs and module docstrings; drops residual "Plan 8 stub"
markers. Backward-compat aliases inside iteration.py are intentional and
preserved.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Final acceptance

After Task 8 commits, run once:

```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run ruff format --check . 2>&1 | tail -3
uv run mypy --strict ralph_executor 2>&1 | tail -3
wc -l ralph_executor/iteration.py ralph_executor/pbi_claim.py \
       ralph_executor/worktree_manager.py ralph_executor/queue_git.py \
       ralph_executor/iteration_safety.py
```

Expected:
- pytest: same pass count as the baseline (the 2 pre-existing `test_config_toml.py` failures remain).
- ruff, mypy: clean.
- All five modules within ±20% of the spec's target sizes (~400, ~250, ~180, ~100, ~250).

Push the branch and open a PR titled `refactor(loop): split loop.py into iteration + 4 sibling modules`. Reference the spec commit (`27ad1d3`) in the description.
