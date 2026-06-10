# loop.py split — implementation plan v2 (refreshed 2026-06-10)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose `ralph_executor/loop.py` (now **1314 lines**, was 1078 at v1) into 5 focused modules (`iteration.py`, `pbi_claim.py`, `worktree_manager.py`, `queue_git.py`, `iteration_safety.py`) with light cleanup of `use_worktrees=False` legacy branches and stale Plan-8/9 stub comments, preserving behavior exactly.

**Architecture:** Eight independently revertable commits. Each one extracts one module (Tasks 1–4), collapses a dead branch (Task 5), renames the residual file (Tasks 6–7), or sweeps docs/comments (Task 8). Tests fan out per module; orchestration tests consolidate in `test_iteration.py`. Only `iteration.py` imports from the other four extracted modules — inter-extracted edges are `pbi_claim → worktree_manager` and `pbi_claim → queue_git` and `iteration_safety → queue_git`.

**Tech Stack:** Python 3.12, uv, pytest, ruff, mypy strict. No new runtime dependencies.

**Provenance:** v1 plan `docs/superpowers/plans/2026-06-01-loop-py-split-implementation.md` (8 tasks); source spec `docs/superpowers/specs/2026-06-01-loop-py-split-design.md` (commit `27ad1d3`); re-validation verdict of 2026-06-10 against current `loop.py@d555348`. **Every code snippet in this v2 was regenerated from the current source — do not copy snippets from v1.**

---

## GUARDRAILS (non-skippable; each task checked against every one)

- `[[0547374e]]` **Stage specific paths.** Every commit step uses `git add <explicit paths>` (or `git mv`, which stages). No `git add -A` / `git add .` anywhere in this plan — v1's Tasks 6–8 used `git add -A`; v2 replaces those with named paths.
- `[[7847b0dc]]` **mypy strict on every task.** Each task's gate runs `uv run mypy ralph_executor` (strict per project config) before commit, not just at the end.
- `[[57bcc63e]]` **NamedTuple/helper local names must match the names the plan prescribes.** Helper names in this plan (`queue_repo_root`, `materialise_worktree`, `pr_skill_scripts_path`, …) are the exact names to use — implementer subagents must not improvise synonyms.
- `[[ffdd01f6]]` **Plan-prescribed signatures must match what the extracted block actually consumes.** Verified at plan-write time: `materialise_worktree` consumes `cfg.main_branch`, a clone root, a PBI id, and a branch name — its v2 signature reflects exactly that (v1's `(cfg, pbi, target_clone_path, branch)` carried a `PBI` it never used). `run_sweep`'s injected-resolver parameter matches the single call its body makes.
- **Monkeypatch-target preservation (claude_spawn split precedent).** Moved symbols that are *called from loop* keep working under loop-level patches via aliased imports (`from X import y as _y`) — the call site resolves the loop module global at call time. Symbols called from *inside a moved function* (`_pr_skill_scripts_path` inside `_run_sweep`) need an explicit injection seam, because a loop-level patch cannot reach another module's global. Tests that exercise a moved function's internals migrate with the function; orchestration tests stay behind and keep loop-level targets. Every patch target in the test tree was enumerated at plan-write time (see per-task lists).
- `[[db26c435]]` **New modules need convention-depth module docstrings and no dead code.** Each new module below ships a full docstring; v1's `worktree_manager.py` header carried a dead `git_ops` import — removed in v2. Every task ends with an unused-import sweep on `loop.py` (named candidates listed per task).
- `[[ralph-runtime § Create the feature branch at task start]]` Branch `refactor/loop-py-split-v2` is created in pre-flight.
- `[[ralph-runtime § Apply confidence scoring]]` Per-task confidence with mitigation-recompute; floor for this plan is **92%** (stricter than the standard's 90%). See the CONFIDENCE section.

Dismissed (one-line reason):
- `[[355faeb8]]` Windows cmd.exe 8191-char argv ceiling — no large argv constructions in this plan.
- Playwright / tempfile / branch-protection reflections — none of those surfaces touched.

---

## EXECUTION ORDER

**T1 → T2 → T5 → T3 → T4 → T6 → T7 → T8.**

Task numbering is kept from v1 for traceability, but **Task 5 executes third, before Task 3**. Rationale: `_cleanup_work_worktree` currently opens with the dead guard `if not cfg.use_worktrees: return` (loop.py:493–494). If Task 3 moved the function first, Task 5 would have to chase the guard into `worktree_manager.py`. Running Task 5 first deletes the guard (and the resume-path gate at loop.py:1040) while everything still lives in `loop.py`, so Task 3 relocates a guard-free body verbatim and Task 4 inherits clean code. Consequence: Task 5's touched-file list shrinks to `loop.py` + `claude_spawn.py` (v1 listed `pbi_claim.py`, which will not exist yet).

Tasks 1 and 2 touch no `use_worktrees` code (their source ranges — 86–102, 133–213, 243–299, 307–372, 400–411 — contain none), so they can safely precede Task 5.

**Line-reference convention:** all `loop.py:NNN` references in this plan are against the current pre-plan file (1314 lines, commit `d555348`). Tasks executed after Task 1 will see shifted line numbers — locate by symbol name (`def _cleanup_work_worktree`, etc.); the cited ranges identify the *content* to move.

---

## CONFIDENCE (floor 92%; mitigations recompute the number)

v1 → v2 delta audit. "Raw" = confidence before in-plan mitigations; "final" = after applying them. No task ships below 92% final.

| Task | v1 rating | v2 raw | Mitigation applied in v2 | v2 final | Delta driver |
|---|---|---|---|---|---|
| T1 queue_git.py | 95% | 96% | Module content regenerated from current source (instance-namespaced clone path, `instance_id` kwarg); migrated-test refs re-verified (1320/1355/617/648/836); `loop.ensure_queue_clone` patch-target drift at test_loop.py:517 found and handled | **96%** | v1 snippets were stale (pre-multi-ralph) — regeneration removes the drift risk |
| T2 iteration_safety.py | 92% | 88% | `_pr_skill_scripts_path` seam fully designed (loop-side wrapper + late-bound default), full regenerated `run_sweep` body inlined, every patch site enumerated (test_loop.py:761/822, test_loop_integration.py:176, cli.py:67, test_loop_pr_skill_scripts_path.py:5) | **93%** | Raw drops vs v1 because the seam adds a behavioral wrapper; mitigations lift it above v1 |
| T3 worktree_manager.py | 93% (85% raw + spike) | 90% | v1's Step-0 spike replaced by a verified seam (read at plan-write time: loop.py:691–698); resume-path patch targets (`loop.ensure_worktree` at test_loop.py:1646/1744) identified — resume path explicitly NOT delegated; T5-first ordering removes the guard question | **94%** | Seam is now known, not spiked; signature fixed per [[ffdd01f6]] |
| T4 pbi_claim.py | 90% | 89% | Import-back list completed against actual resume-path call sites (loop.py:867, 1019–1020, 1092, 1208); `_warn_project_toml` seam decided (moves, with loop re-export); migration list completed (adds 1535 + helper 1515); header regenerated (socket/open_log/datetime, current `move_inbox_to_current` kwargs, entry-probe order) | **93%** | Largest move; v1's header and import-back list were both wrong against current source |
| T5 collapse use_worktrees | 92% (80% raw + Step 0) | 93% | Config gate VERIFIED at plan-write time (config.py:884–891 raises `ConfigError` on `use_worktrees=False`); Step 0 retained as belt-and-braces; runtime guards in claude_spawn (target_info/work_worktree `None` branches) explicitly marked KEEP | **95%** | The v1 risk (gate might not exist) is now a verified fact |
| T6 rename loop→iteration | 97% | 97% | Importer list refreshed and grep-verified (adds cli.py:398 deferred import, autobug e2e test, satellite test files, caplog `logger=` kwargs) | **97%** | Line-number refresh only |
| T7 rename test files | 98% | 97% | Adds `test_loop_autobug_wires.py` to the rename set; Step-2 grep pattern fixed to match underscore-suffixed names | **97%** | Small scope increase, fully enumerated |
| T8 doc sweep | 96% | 96% | Refs refreshed; adds claude_spawn.py:97–110 replica docstring + url_utils.py:5 to the sweep | **96%** | Line-number refresh only |

---

## SEAM DECISIONS (the two cross-module monkeypatch seams)

**Seam 1 — `_pr_skill_scripts_path` (Task 2).** Moves INTO `iteration_safety.py` as public `pr_skill_scripts_path`. `loop.py` re-imports it under the old name (`from ralph_executor.iteration_safety import pr_skill_scripts_path as _pr_skill_scripts_path`), preserving `cli.py:67`'s import and `tests/executor/test_loop_pr_skill_scripts_path.py:5`. Because the function is called from *inside* `run_sweep` (which also moves), a loop-level monkeypatch (`tests/executor/test_loop_integration.py:176` patches `ralph_executor.loop._pr_skill_scripts_path`) would no longer be seen by a plain relocation. Resolution: `iteration_safety.run_sweep` takes a `pr_skill_scripts_path: Callable[[ExecutorConfig], Path] | None = None` keyword; when `None` it late-binds to its own module global (via a call-time self-import, NOT a def-time default — def-time defaults freeze the original and defeat monkeypatching); `loop.py` keeps a thin `def _run_sweep(...)` wrapper that passes its module-global `_pr_skill_scripts_path` through, so patches at the loop level keep intercepting. Orchestration tests that patch `ralph_executor.loop._run_sweep` itself (test_loop.py:461) still hit a module-level `def`.

**Seam 2 — `_warn_project_toml_in_target_clone` (Task 4).** Moves INTO `pbi_claim.py` as public `warn_project_toml_in_target_clone`; `loop.py` re-imports it under the old name for the resume path (loop.py:1070) and for `tests/executor/test_loop_project_toml_warning.py:7`'s import. Chosen over the keep-in-loop-and-inject alternative because: (a) it is not monkeypatched anywhere (verified by grep), so no patch seam is needed; (b) both options change the emitting logger name eventually (Task 6 renames the module to `iteration`, changing `ralph_executor.loop` regardless); (c) the test's `caplog.at_level(..., logger="ralph_executor.loop")` assertions are level-WARNING captures through the root handler and pass either way (verified: no test asserts a positive DEBUG capture or `record.name`). No cycle: `pbi_claim` never imports `loop`.

---

**Test baseline:** Before Task 1, run the full suite once and record the pass/fail counts. Every commit MUST leave the same counts (any pre-existing failures stay failing, nothing new breaks).

**Convention for relocation steps:** large function bodies move verbatim; the plan gives the new file's header (imports + docstring) in full, the renaming map, and the exact current-source line range to copy. Verification is `git diff` between deleted and added definitions — any change beyond the rename map and import paths is a bug. Small or *edited* bodies (where the move is not pure) are inlined in full.

---

## Pre-flight (one-time, no commit)

- [ ] **Create the feature branch (at task start, per guardrail)**

```bash
git fetch origin main
git checkout -b refactor/loop-py-split-v2 origin/main
```

(Optional: do this via `git worktree add .worktrees/loop-split-v2 -b refactor/loop-py-split-v2 main` — but on Windows do not `cd` into the worktree from a long-lived shell you'll later use for `git worktree remove`.)

- [ ] **Establish baseline**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run ruff check ralph_executor 2>&1 | tail -3
uv run mypy ralph_executor 2>&1 | tail -3
```

Record the pytest counts; every later gate must reproduce them.

---

## Task 1: Create `queue_git.py` — confidence **96%**

**Files:**
- Create: `ralph_executor/queue_git.py`
- Modify: `ralph_executor/loop.py` (delete `_queue_repo_root`, `_pull_queue`, `_persist_iteration_writes`; aliased imports)
- Create: `tests/executor/test_queue_git.py`
- Modify: `tests/executor/test_loop.py` (remove migrated tests; retarget one patch)

- [ ] **Step 1: Create `ralph_executor/queue_git.py` with this exact content**

Regenerated from current `loop.py:86-102` (`_queue_repo_root`), `loop.py:400-411` (`_pull_queue`), `loop.py:307-372` (`_persist_iteration_writes`). Note vs v1: `queue_repo_root` returns `cfg.queue_clone_path` (per-instance, multi-ralph) — NOT `cfg.workspace_root / "queue"`; `pull_queue` passes `instance_id=cfg.instance_id`.

```python
"""Queue-clone git operations used by the iteration loop.

Three helpers: resolving the per-instance queue-clone path, refreshing
the queue clone before an iteration, and persisting any PBI-directory
edits Claude made during it. All run against the queue clone at
``<workspace_root>/queue-<instance_id>/`` (materialised by
``ensure_queue_clone``); all are pure functions of ``cfg`` (plus a PBI
id and optional event log for the persist helper).
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
    """Filesystem path of the queue clone for this instance.

    Scope 1 multi-ralph: the queue clone is namespaced per-instance at
    ``<workspace_root>/queue-<instance_id>/``. Delegates to
    :attr:`ExecutorConfig.queue_clone_path` so every module that needs
    the path agrees with the executor's view.

    The queue repo is cloned by ``ensure_queue_clone`` into this path
    and owns ``.ralph/`` (events.db, sentinel, blocked/, …). Every
    operation that reads or writes under ``.ralph/`` — opening the
    event log, moving PBIs to ``.ralph/blocked/``, handling STUCK.md,
    checking/writing the halt sentinel — routes through this helper so
    the side-effects land in the queue clone that gets pushed to
    ``origin/<queue_branch>`` of ``queue_repo``.
    """
    return cfg.queue_clone_path


def pull_queue(cfg: ExecutorConfig) -> None:
    """Refresh the queue clone before an iteration. Cheap; runs every iteration."""
    log.debug(
        "refreshing queue clone for %s (branch=%s)",
        cfg.queue_repo,
        cfg.queue_branch,
    )
    ensure_queue_clone(
        cfg.workspace_root,
        cfg.queue_repo,
        cfg.queue_branch,
        instance_id=cfg.instance_id,
    )


def persist_iteration_writes(
    cfg: ExecutorConfig,
    pbi_id: str,
    *,
    event_log: EventLog | None = None,
    now: datetime | None = None,
) -> None:
    """Commit + push any HISTORY.md/STUCK.md/PLAN.md edits Claude wrote
    inside the current PBI dir during this iteration.

    When the iteration outcome leaves the PBI in current/ (partial /
    error), nothing else moves the directory, so those edits would sit
    uncommitted in the working tree and be lost on the next iteration's
    checkout.

    Stages ONLY the PBI's directory under .ralph/current/<id>/ — not the
    whole .ralph/ tree — so local-state artefacts (e.g.
    .ralph/state/events.db) aren't accidentally committed every
    iteration. No-ops cleanly when the index ends up empty and when the
    PBI was already moved out of current/ by a sibling code path.

    All git operations run against the queue clone (materialised by
    ``pull_queue`` earlier in the iteration). The clone is its own
    working tree on the queue branch; no branch switching is required.

    Emits ``FILE_TOUCHED`` to ``event_log`` when a new commit lands and
    the diff is non-empty. The cycle detector reserves the event for
    future per-iteration rules (no current rule reads it; emit for
    forward compatibility).
    """
    queue_repo = queue_repo_root(cfg)
    pbi_dir = queue_repo / ".ralph" / "current" / pbi_id
    if not pbi_dir.is_dir():
        # PBI was already moved + committed + pushed by handle_stuck or
        # move_current_to_pending_pr — both route through
        # ``movements._move`` which runs ``git_ops.mv`` + ``commit_paths``
        # + ``push_with_rebase`` inside the same call. Nothing remains
        # in current/ for this helper to stage.
        return
    # Use add_all_changes so deletions of tracked files (e.g. Claude
    # removing a resolved STUCK.md) are staged too — bare `git add <dir>`
    # would skip them and leave index + working tree divergent.
    git_ops.add_all_changes(queue_repo, pbi_dir)
    head_before = git_ops.rev_parse_head(queue_repo)
    message = f"chore(queue): persist iteration writes for {pbi_id}"
    head_after = git_ops.commit_index(queue_repo, message)
    if head_after != head_before:
        log.info("persisted iteration writes for %s as %s", pbi_id, head_after[:7])
        # push_with_rebase rebases the local persist commit onto a raced
        # origin/main instead of failing the push outright. The caller
        # (iterate_once) catches PushRebaseConflict and converts it to a
        # recoverable IterationResult so the loop keeps running.
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

Delete the local definitions of `_queue_repo_root` (86–102), `_persist_iteration_writes` (307–372), `_pull_queue` (400–411). Add this import block after the existing `from ralph_executor import git_ops` line:

```python
from ralph_executor.queue_git import (
    persist_iteration_writes as _persist_iteration_writes,
    pull_queue as _pull_queue,
    queue_repo_root as _queue_repo_root,
)
```

The aliased imports keep every loop-internal call site AND every loop-level monkeypatch working unchanged: `tests/executor/test_loop_autobug_wires.py:29/52/79/106` and `tests/executor/autobug/integration/test_python_crash_e2e.py:20` patch `loop._pull_queue`; `tests/executor/test_loop.py:258` patches `loop._persist_iteration_writes`; `cli.py:398` does a deferred `from ralph_executor.loop import _queue_repo_root`. All resolve through the aliases.

Unused-import sweep: `ensure_queue_clone` and `EventLog` become unused in `loop.py` — remove `from ralph_executor.queue_clone import ensure_queue_clone` and drop `EventLog` from the `ralph_executor.safety` import block (keep `Event`, `EventType`, the rest). `uv run ruff check` confirms.

- [ ] **Step 3: Migrate tests → `tests/executor/test_queue_git.py`; fix the drifted patch target**

Move these tests verbatim from `tests/executor/test_loop.py` (current line refs):
- `test_persist_iteration_writes_excludes_state_dir` (line 617)
- `test_file_touched_event_emitted_on_iteration_commit` (line 648)
- `test_file_touched_skipped_on_empty_commit` (line 836)
- `test_pull_queue_calls_ensure_queue_clone` (line 1320)
- `test_pull_queue_passes_configured_branch` (line 1355)

Migration edits:
- The two pull-queue tests use `from ralph_executor import loop` + `monkeypatch.setattr(loop, "ensure_queue_clone", fake_ensure)` + `loop._pull_queue(cfg)`. Change to `from ralph_executor import queue_git`, `monkeypatch.setattr(queue_git, "ensure_queue_clone", fake_ensure)`, `queue_git.pull_queue(cfg)`. Keep the `fake_ensure` signatures verbatim (they already carry the `instance_id` keyword and `timeout` default).
- The three persist/file-touched tests drive `iterate_once` and patch `ralph_executor.loop.spawn_claude_p` — keep those loop-level patch targets as-is (Task 6 updates them mechanically later). COPY (don't move) the shared helpers `_git`, `_populate_inbox`, `_stub_spawn` (test_loop.py:25–64) into the new file; the originals stay because many remaining test_loop.py tests use them.
- Keep all assertions verbatim. Delete the migrated tests from `test_loop.py`.

**Drift fix (stays in `test_loop.py`):** `test_iterate_once_refreshes_queue_clone_every_iteration` (line 491) patches `"ralph_executor.loop.ensure_queue_clone"` at line 517. After this task the call lives in `queue_git` — retarget that patch to `"ralph_executor.queue_git.ensure_queue_clone"`.

- [ ] **Step 4: Gate**

```bash
uv run pytest tests/executor/test_queue_git.py tests/executor/test_loop.py tests/executor/test_loop_autobug_wires.py -q 2>&1 | tail -5
uv run pytest tests/ -q 2>&1 | tail -5
uv run mypy ralph_executor 2>&1 | tail -3
uv run ruff check ralph_executor 2>&1 | tail -3
```

Expected: baseline counts; mypy/ruff clean.

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/queue_git.py ralph_executor/loop.py \
        tests/executor/test_queue_git.py tests/executor/test_loop.py
git commit -m "$(cat <<'EOF'
refactor(loop): extract queue_git.py (queue_repo_root + pull_queue + persist_iteration_writes)

Task 1 of loop.py split v2. Pure relocation regenerated from current
source (per-instance queue clone path, instance_id threading). loop.py
keeps aliased imports so all call sites, cli.py's deferred
_queue_repo_root import, and loop-level monkeypatch paths still resolve.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Create `iteration_safety.py` — confidence **93%**

**Files:**
- Create: `ralph_executor/iteration_safety.py`
- Modify: `ralph_executor/loop.py` (remove `_run_sweep`, `_pr_skill_scripts_path`, `_check_cycle_detector`; add wrapper + aliased imports; drop stub docstrings)
- Create: `tests/executor/test_iteration_safety.py`
- Modify: `tests/executor/test_loop.py` (remove migrated tests)

Current source ranges: `_run_sweep` loop.py:133–213 (event-log open/close try/finally at 181–207, `auto_merge_clean_prs` at 175, `repo_name` at 195); `_pr_skill_scripts_path` loop.py:243–267; `_check_cycle_detector` loop.py:270–299 (`tripped_by_instance=cfg.instance_id` at 297).

- [ ] **Step 1: Create `ralph_executor/iteration_safety.py`**

Full content. `run_sweep`'s body is the current loop.py:133–213 with three deliberate edits beyond the rename map: (1) the injected-resolver seam (see SEAM DECISIONS); (2) the lazy `from ralph_executor.sweep import run as run_sweep` local alias renamed to `sweep_run` so it no longer shadows the enclosing function's own name (the patch target `ralph_executor.sweep.run` is unaffected — the import happens at call time); (3) stub-disclaimer docstring lines ("(Plan 8)") dropped. `check_cycle_detector` and `pr_skill_scripts_path` move verbatim (rename map only).

```python
"""Per-iteration safety wiring: sweep + cycle detector.

Both entry points read from the queue clone (the canonical ``.ralph/``
tree, resolved via ``queue_git.queue_repo_root``) and emit events to
the cycle-detector log. They are module-level callables so tests can
monkeypatch them without dependency injection; the loop module keeps
aliased re-exports (``_run_sweep`` / ``_check_cycle_detector`` /
``_pr_skill_scripts_path``) so loop-level patch targets stay valid.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue_git import queue_repo_root
from ralph_executor.safety import evaluate_all, halt_and_acknowledge, open_log

log = logging.getLogger(__name__)


def pr_skill_scripts_path(cfg: ExecutorConfig) -> Path:
    # body verbatim from loop.py:243-267 (drop the leading underscore
    # from the name; keep the in-function `import ralph_executor` and
    # the full docstring unchanged)
    ...


def run_sweep(
    cfg: ExecutorConfig,
    source: FilesystemQueueSource,
    *,
    pr_skill_scripts_path: Callable[[ExecutorConfig], Path] | None = None,
) -> None:
    """Drive one sweep over ``.ralph/pending-pr/``.

    <keep the rest of the current docstring from loop.py:134-150
    verbatim, minus the "(Plan 8)" marker, then append:>

    ``pr_skill_scripts_path`` is the resolver for the PR-skill scripts
    directory. It is an injectable parameter so the loop module can pass
    its own re-exported global — preserving monkeypatches at
    ``ralph_executor.loop._pr_skill_scripts_path`` — and tests can pass
    a stub directly. When ``None`` it late-binds to this module's
    ``pr_skill_scripts_path`` at call time (a def-time default would
    freeze the original function object and defeat monkeypatching).
    """
    del source  # sweep walks the filesystem directly
    if pr_skill_scripts_path is None:
        # Late-bind through sys.modules so monkeypatches at
        # ``ralph_executor.iteration_safety.pr_skill_scripts_path``
        # are honoured.
        from ralph_executor import iteration_safety as _self

        pr_skill_scripts_path = _self.pr_skill_scripts_path
    if not cfg.bot_author_email:
        log.warning(
            "sweep: bot_author_email is not set (TOML key 'bot_author_email' "
            "or env RALPH_ADO_AUTHOR_EMAIL); skipping sweep this iteration"
        )
        return

    scripts_path = pr_skill_scripts_path(cfg)
    if not scripts_path.is_dir():
        log.warning(
            "sweep: PR-skill scripts directory not found at %s; skipping",
            scripts_path,
        )
        return

    from ralph_executor.sweep import run as sweep_run
    from ralph_executor.sweep.runner import SweepConfig, SweepContext

    sweep_cfg = SweepConfig(
        ralph_author_email=cfg.bot_author_email,
        max_attempts=cfg.max_attempts,
        stale_threshold=timedelta(days=cfg.stale_days),
        now=datetime.now(tz=UTC),
        auto_merge_clean_prs=cfg.auto_merge_clean_prs,
    )
    # Open the event log so the sweep can emit cycle-detector events
    # (Plan 19b: PR_MERGED + PBI_CLOSED on pending-pr → done,
    # PR_GREEN_THEN_RED on green→red CI transitions). Close in a finally
    # so a sweep-side crash never leaks the SQLite handle.
    event_log = open_log(queue_repo_root(cfg))
    try:
        sweep_ctx = SweepContext(
            # ``.ralph/`` lives in the queue clone — same path every
            # read/write in this module routes through ``queue_repo_root``.
            queue_root=queue_repo_root(cfg) / ".ralph",
            ado_pr_scripts_path=scripts_path,
            config=sweep_cfg,
            # The queue clone is the single repo the sweep reads/writes
            # (every PR scanned belongs to a target reachable from the
            # queue's pending-pr index); label the sweep context with
            # its directory name.
            repo_name=queue_repo_root(cfg).name,
            event_log=event_log,
        )
        result = sweep_run(ctx=sweep_ctx)
    finally:
        # Wrap close() so a failure here (e.g. sqlite flush error) does
        # not mask an exception from sweep_run — losing the real cause
        # makes post-mortem debugging much harder. Log close() failures
        # at WARNING and let the original (if any) propagate unchanged.
        try:
            event_log.close()
        except Exception as exc:
            log.warning("sweep: event_log.close() failed: %s", exc)
    log.info(
        "sweep: scanned %d PBIs (actions=%d, errors=%d)",
        result.pbis_scanned,
        len(result.actions),
        len(result.errors),
    )


def check_cycle_detector(cfg: ExecutorConfig, source: FilesystemQueueSource) -> bool:
    # body verbatim from loop.py:270-299 with the rename map applied:
    #   _queue_repo_root -> queue_repo_root  (two call sites: open_log
    #   at 281, halt_and_acknowledge repo= at 294)
    # ``tripped_by_instance=cfg.instance_id`` (line 297) is part of the
    # body — preserve it. ``source`` stays in the signature (interface
    # parity with run_sweep) even though the body doesn't read it; add
    # ``del source`` at the top if ruff ARG001 fires.
    ...
```

(The two `...` bodies are relocations — copy from the cited ranges; everything else above is the literal content to write.)

- [ ] **Step 2: Update `ralph_executor/loop.py`**

Delete the local `_run_sweep`, `_pr_skill_scripts_path`, `_check_cycle_detector` definitions and the `# Stubs for Plans 8 and 9` section banner (loop.py:128–130). Add, after the `queue_git` import block:

```python
from ralph_executor.iteration_safety import (
    check_cycle_detector as _check_cycle_detector,
    pr_skill_scripts_path as _pr_skill_scripts_path,
    run_sweep,
)


def _run_sweep(cfg: ExecutorConfig, source: FilesystemQueueSource) -> None:
    """Delegate to ``iteration_safety.run_sweep``, threading this module's
    ``_pr_skill_scripts_path`` global through the resolver seam.

    Kept as a real ``def`` (not an aliased import) for two reasons:
    tests patch ``ralph_executor.loop._run_sweep`` directly
    (orchestration seam), and tests patch
    ``ralph_executor.loop._pr_skill_scripts_path`` expecting the sweep
    to see the stub — the call-time global lookup here is what makes
    that interception work after the function moved modules.
    """
    run_sweep(cfg, source, pr_skill_scripts_path=_pr_skill_scripts_path)
```

Also in loop.py's file-level docstring: drop the stale stub lines — "(Plan 8 fills in)" (line 15) and the sentence "Plan 8 will replace ``_run_sweep`` with the real sweep implementation." (lines 22–24; keep the monkeypatch sentence that follows if it still reads true, otherwise trim it too).

Patch-target audit (all keep working): `loop._run_sweep` (test_loop.py:461) → real def above; `loop._check_cycle_detector` (test_loop.py:482, 549; tests/safety/test_integration_loop.py:261, 363) → aliased import, call sites in `_iterate_once_inner` resolve the loop global at call time; `loop._pr_skill_scripts_path` (test_loop_integration.py:176) → wrapper passes the loop global; `cli.py:67` import of `_pr_skill_scripts_path` → re-export; `tests/executor/test_loop_pr_skill_scripts_path.py:5` → re-export. `tests/executor/test_loop_diff_against_target.py:268` imports `_run_sweep` from loop and relies on the real scripts dir existing — the wrapper preserves that.

Unused-import sweep in loop.py: `timedelta` (was only used by the two moved functions), `evaluate_all`, `halt_and_acknowledge` become unused — remove them. Keep `open_log` (still used by `_run_ralph` / `_iterate_once_inner` / `_claim_pbi_worktree`).

- [ ] **Step 3: Migrate tests → `tests/executor/test_iteration_safety.py`**

Move verbatim from `test_loop.py` (current line refs):
- `test_run_loop_terminates_when_cycle_detector_trips` (line 533)
- `test_run_sweep_skips_when_bot_author_email_empty` (line 698)
- `test_run_sweep_passes_cfg_values_to_sweep_config` (line 724)
- `test_run_sweep_does_not_read_env_for_promoted_knobs` (line 779)
- `test_run_sweep_queue_root_points_at_queue_clone` (line 792)
- `test_event_log_lives_in_queue_clone` (line 928)

Migration edits:
- `from ralph_executor.loop import _run_sweep` (lines 709, 732, 803) → `from ralph_executor.iteration_safety import run_sweep`; call sites `_run_sweep(...)` → `run_sweep(...)`.
- The monkeypatches of `"ralph_executor.loop._pr_skill_scripts_path"` (lines 761, 822) become explicit parameter injection: delete the patch and pass `pr_skill_scripts_path=lambda _cfg: <the stub dir>` into the `run_sweep` call. (The `"ralph_executor.sweep.run"` patches are unaffected — the lazy import resolves at call time.)
- `caplog.at_level("WARNING", logger="ralph_executor.loop")` (line 716) → `logger="ralph_executor.iteration_safety"`.
- `test_run_loop_terminates_when_cycle_detector_trips` patches `"ralph_executor.loop._check_cycle_detector"` (line 549) and drives `run_loop` — that patch target STAYS at the loop level (the aliased import makes it valid); only the file moves.
- `test_event_log_lives_in_queue_clone` drives `iterate_once` and patches `loop.spawn_claude_p` (line 938) — keep the loop-level target; copy the `_populate_inbox`/`_stub_spawn` helpers in (as in Task 1).

Keep `test_iterate_once_invokes_sweep_stub_when_current_empty` (line 450) and `test_iterate_once_invokes_cycle_detector_stub` (line 470) in `test_loop.py` — they test orchestration seams, not the helpers.

`tests/executor/test_loop_pr_skill_scripts_path.py` is untouched in this task (its loop import keeps resolving via the re-export; Task 6 retargets it).

- [ ] **Step 4: Gate**

```bash
uv run pytest tests/executor/test_iteration_safety.py tests/executor/test_loop.py tests/executor/test_loop_integration.py tests/executor/test_loop_pr_skill_scripts_path.py tests/executor/test_loop_diff_against_target.py tests/safety/test_integration_loop.py -q 2>&1 | tail -5
uv run pytest tests/ -q 2>&1 | tail -5
uv run mypy ralph_executor 2>&1 | tail -3
uv run ruff check ralph_executor 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/iteration_safety.py ralph_executor/loop.py \
        tests/executor/test_iteration_safety.py tests/executor/test_loop.py
git commit -m "$(cat <<'EOF'
refactor(loop): extract iteration_safety.py (sweep + cycle-detector + pr-skill path)

Task 2 of loop.py split v2. run_sweep gains an injectable
pr_skill_scripts_path resolver (late-bound default) and loop keeps a
thin _run_sweep wrapper passing its module global, so monkeypatches at
ralph_executor.loop._pr_skill_scripts_path keep intercepting. Drops the
stale Plan-8/9 stub docstrings; otherwise pure relocation.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 (EXECUTED THIRD): Collapse `use_worktrees=False` legacy branches — confidence **95%**

> Carried over from v1 with line-number refresh and a touched-file-list fix (runs before Tasks 3–4 now, so only `loop.py` and `claude_spawn.py` are touched — `pbi_claim.py`/`worktree_manager.py` don't exist yet and will inherit clean code). Body below is complete; do not consult v1.

**Files:**
- Modify: `ralph_executor/loop.py`, `ralph_executor/claude_spawn.py`

This task is the only one that changes code shape beyond relocation — by removing dead branches. The config gate was VERIFIED at plan-write time: `ralph_executor/config.py:884–891` raises `ConfigError` whenever `use_worktrees` resolves falsy ("use_worktrees=False is no longer supported…"). Step 0 re-confirms as belt-and-braces.

- [ ] **Step 0: Re-verify the config gate**

```bash
grep -n -B 2 -A 8 "if not use_worktrees" ralph_executor/config.py
grep -rn "use_worktrees\s*=\s*False" ralph_executor/ tests/
```

Expected: the `ConfigError` raise at config.py:884–891; `=False` matches only in tests exercising the rejection path (or none). **If `use_worktrees=False` is silently accepted anywhere in production code: STOP and escalate — the spec needs amending.**

- [ ] **Step 1: Inventory the legacy branches**

```bash
grep -nE "use_worktrees|legacy.*single.*checkout|single-checkout mode|single-target mode" \
  ralph_executor/loop.py ralph_executor/claude_spawn.py
```

Expected hits (current pre-plan line refs; Tasks 1–2 will have shifted loop.py — match by content):
- loop.py:487 — docstring sentence "No-op in legacy single-checkout mode or when target_repo / ensure_clone are unavailable (defensive)." in `_cleanup_work_worktree`.
- loop.py:493–494 — `if not cfg.use_worktrees:` / `return` guard in `_cleanup_work_worktree`. **Dead branch — delete.**
- loop.py:626–628 — `_claim_pbi` docstring "The Stage-A single-checkout legacy branch-dance is gone … ``load_config`` rejects ``use_worktrees=False`` outright."
- loop.py:1040 — `if cfg.use_worktrees and info is not None:` in the resume path. **Dead conjunct — collapse.**
- claude_spawn.py:105–108 — `_queue_repo_root_for_spawn` docstring sentences about the proposed `cfg.use_worktrees` branch.
- claude_spawn.py:131 — `_build_argv` docstring "In legacy single-checkout mode it equals ``pbi.path``; …".
- claude_spawn.py:341–350 — the cwd fallback (`elif pbi.work_worktree is not None: … else: raise ConfigError(…)`). **Runtime guard, NOT legacy mode — KEEP the code.**
- claude_spawn.py:389 and 399–403 — comments "None in legacy single-target mode." / "Legacy single-target mode — strip any BETTER_MEMORY_PROJECT…". **The `pbi.target_info is None` branch is LIVE** (the resume path sets `info = None` on malformed frontmatter) — KEEP the code, reword the comments to name the real condition (missing/malformed `target_repo` frontmatter), not "legacy mode".
- claude_spawn.py:437 — `spawn_claude_p` docstring "defaults to ``pbi.path`` for legacy mode." — reword (the default is the kwarg fallback, unrelated to legacy mode).

- [ ] **Step 2: Apply the edits**

In `loop.py`:
- Delete the two-line guard `if not cfg.use_worktrees:` / `return` in `_cleanup_work_worktree` (was 493–494).
- In the same function's docstring, change the line at 487 to: `Defensive no-op when target_repo / ensure_clone are unavailable.`
- In the resume path (was 1040): `if cfg.use_worktrees and info is not None:` → `if info is not None:`. Check the comment block immediately above (1023–1029, "Parse the URL outside the worktree-mode guard…") and reword "the worktree-mode guard" to "the info guard".
- In `_claim_pbi`'s docstring (was 626–628), keep the factual sentence about the queue being its own clone; delete the trailing clause "and ``load_config`` rejects ``use_worktrees=False`` outright" only if the whole sentence is being trimmed — otherwise leave it (it documents why no legacy branch exists). Either way no code changes here.

In `claude_spawn.py`: docstring/comment rewording only, per the inventory above. No code changes.

- [ ] **Step 3: Sweep verification**

```bash
grep -nE "use_worktrees" ralph_executor/loop.py ralph_executor/claude_spawn.py
grep -nE "legacy.*single.*checkout|single-checkout mode|single-target mode" ralph_executor/loop.py ralph_executor/claude_spawn.py
```

Expected: no matches in either file. (`config.py`'s gate and the `use_worktrees` config field itself stay — removing the knob from config is out of scope, same as v1.)

- [ ] **Step 4: Gate**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run mypy ralph_executor 2>&1 | tail -3
uv run ruff check ralph_executor 2>&1 | tail -3
```

Expected: baseline counts. If a test fails it was depending on the dead branch — read it; delete it if it tests dead behavior, otherwise revert that simplification and re-scope.

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/loop.py ralph_executor/claude_spawn.py
git commit -m "$(cat <<'EOF'
refactor(loop): collapse use_worktrees=False legacy branches

Task 5 of loop.py split v2 (executed before the worktree_manager
extraction so the moved cleanup body is already guard-free).
config.load_config has rejected use_worktrees=False since
KILL-RALPH-HOME (config.py:884-891), so the guard in
_cleanup_work_worktree and the resume-path conjunct are dead.
claude_spawn changes are comment/docstring rewording only — the
target_info/work_worktree None branches are live runtime guards.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 (EXECUTED FOURTH): Create `worktree_manager.py` — confidence **94%**

**Files:**
- Create: `ralph_executor/worktree_manager.py`
- Modify: `ralph_executor/loop.py` (remove `_cleanup_work_worktree`; delegate `_claim_pbi_worktree`'s worktree creation; aliased imports)
- Create: `tests/executor/test_worktree_manager.py`
- Modify: `tests/executor/test_loop.py` (remove migrated tests)

Seam status (verified at plan-write time, replacing v1's Step-0 spike): the worktree-creation block inside `_claim_pbi_worktree` is the contiguous pair at loop.py:692–698 — `work_wt = work_worktree_path(clone.clone_root, moved.id)` followed by the `ensure_worktree(...)` call. It consumes exactly `clone.clone_root`, `moved.id`, `branch`, and `cfg.main_branch`. The `replace(moved, …)` PBI mutation (699–704) stays in the caller. Note `_claim_pbi_worktree`'s actual signature is `(cfg, pbi, *, target_url: str, info: TargetRepoInfo)` — v1's wiring text described a positional `(cfg, pbi, target_clone_path, branch)` that never existed; the function computes `branch` and the clone root itself.

- [ ] **Step 1: Create `ralph_executor/worktree_manager.py`**

Header and `materialise_worktree` in full; `cleanup_work_worktree` by relocation:

```python
"""Work-worktree lifecycle.

Two operations: materialise the per-PBI work worktree inside the target
clone on its feature branch, and tear it down after the PBI reaches a
terminal state. The worktree lives at
``<clone-root>/.ralph-work/<PBI-ID>/`` on branch ``ralph/<PBI-ID>``;
cleanup always preserves the feature branch (pending-pr PBIs need it to
keep the PR alive) and tolerates removal failures — an orphan worktree
is operator-recoverable via ``git worktree prune``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ralph_executor.config import ExecutorConfig
from ralph_executor.types import PBI
from ralph_executor.worktree import (
    ensure_worktree,
    remove_worktree,
    work_worktree_path,
)

log = logging.getLogger(__name__)


def materialise_worktree(
    cfg: ExecutorConfig,
    *,
    clone_root: Path,
    pbi_id: str,
    branch: str,
) -> Path:
    """Create (idempotently) the per-PBI work worktree and return its path.

    The worktree is rooted at ``<clone_root>/.ralph-work/<pbi_id>/`` on
    ``branch``, forked from ``origin/<cfg.main_branch>`` when the branch
    is new. ``ensure_worktree`` is a no-op when the worktree already
    exists on the right branch. Raises ``git_ops.GitCommandError`` when
    the base ref is missing — callers pre-flight or catch accordingly.
    """
    work_wt = work_worktree_path(clone_root, pbi_id)
    ensure_worktree(
        clone_root,
        worktree_path=work_wt,
        branch=branch,
        create_branch_from=f"origin/{cfg.main_branch}",
    )
    return work_wt


def cleanup_work_worktree(cfg: ExecutorConfig, pbi: PBI) -> None:
    # body verbatim from current loop.py:469-560, MINUS the
    # ``if not cfg.use_worktrees: return`` guard (was 493-494) and with
    # the docstring line (was 487) already reworded — both done by
    # Task 5, which runs before this task. Rename map: drop the leading
    # underscore. KEEP the function-local imports of
    # ``url_utils.parse_target_repo`` and ``target_clone.ensure_clone``
    # — they break a potential import cycle with pbi_claim; do not
    # promote them to module level.
    ...
```

Note vs v1: no `git_ops` import in the header — neither body uses it (v1's header carried it dead, violating [[db26c435]]).

- [ ] **Step 2: Update `ralph_executor/loop.py`**

Delete the local `_cleanup_work_worktree` definition. Add the import block:

```python
from ralph_executor.worktree_manager import (
    cleanup_work_worktree as _cleanup_work_worktree,
    materialise_worktree,
)
```

In `_claim_pbi_worktree` (signature unchanged: `(cfg, pbi, *, target_url: str, info: TargetRepoInfo)`), replace the worktree-creation pair (current 692–698):

```python
    branch = _feature_branch_name(moved)
    work_wt = work_worktree_path(clone.clone_root, moved.id)
    ensure_worktree(
        clone.clone_root,
        worktree_path=work_wt,
        branch=branch,
        create_branch_from=f"origin/{cfg.main_branch}",
    )
```

with:

```python
    branch = _feature_branch_name(moved)
    work_wt = materialise_worktree(
        cfg,
        clone_root=clone.clone_root,
        pbi_id=moved.id,
        branch=branch,
    )
```

The trailing `return replace(moved, target_repo=target_url, target_info=info, work_worktree=work_wt)` stays exactly as is.

**Do NOT delegate the resume path** (`_iterate_once_inner`, current 1071–1094) to `materialise_worktree`, even though the dance is identical: `test_iterate_once_resume_self_heals_missing_work_worktree` (test_loop.py:1579, spy at 1639–1646) and `test_iterate_once_resume_demotes_to_blocked_when_worktree_cannot_be_created` (test_loop.py:1671, patch at 1744) monkeypatch `ralph_executor.loop.ensure_worktree` and must keep intercepting. Loop therefore KEEPS its `ensure_worktree` and `work_worktree_path` imports; only `remove_worktree` becomes unused — remove it from loop.py's `ralph_executor.worktree` import.

Patch-target audit: `"ralph_executor.loop._cleanup_work_worktree"` (tests/executor/test_loop_diff_against_target.py:109, 172, 242) keeps working — the call sites in `_run_ralph` resolve the loop-module alias at call time.

- [ ] **Step 3: Migrate tests → `tests/executor/test_worktree_manager.py`**

Move verbatim from `test_loop.py` (current line refs):
- `test_claim_creates_work_worktree_on_feature_branch` (line 874)
- `test_terminal_outcome_removes_work_tree` (line 900)

Both drive `iterate_once` and patch `"ralph_executor.loop.spawn_claude_p"` — keep those loop-level targets (Task 6 retargets them mechanically). Imports needed in the new file: `iterate_once` (from `ralph_executor.loop` for now), `work_worktree_path` (from `ralph_executor.worktree`), plus COPIES of the `_git`, `_populate_inbox`, `_stub_spawn` helpers (test_loop.py:25–64). Keep all assertions verbatim. Delete the migrated tests from `test_loop.py`.

Keep `test_iterate_once_moves_pbi_to_blocked_when_target_unreachable` (line 1479) in `test_loop.py` — orchestration test that happens to exercise cleanup.

- [ ] **Step 4: Gate**

```bash
uv run pytest tests/executor/test_worktree_manager.py tests/executor/test_loop.py tests/executor/test_loop_diff_against_target.py -q 2>&1 | tail -5
uv run pytest tests/ -q 2>&1 | tail -5
uv run mypy ralph_executor 2>&1 | tail -3
uv run ruff check ralph_executor 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/worktree_manager.py ralph_executor/loop.py \
        tests/executor/test_worktree_manager.py tests/executor/test_loop.py
git commit -m "$(cat <<'EOF'
refactor(loop): extract worktree_manager.py (materialise + cleanup)

Task 3 of loop.py split v2 (runs after Task 5, so the moved cleanup
body is already guard-free). materialise_worktree's signature matches
exactly what the extracted block consumes (clone_root/pbi_id/branch +
cfg.main_branch). The resume path keeps its direct ensure_worktree call
so loop-level monkeypatches in the self-heal tests keep intercepting.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 (EXECUTED FIFTH): Create `pbi_claim.py` — confidence **93%**

**Files:**
- Create: `ralph_executor/pbi_claim.py`
- Modify: `ralph_executor/loop.py` (remove `_ClaimError`, `_ENTRY_FILENAMES`, `_feature_branch_name`, `_read_target_repo_from_pbi`, `_warn_project_toml_in_target_clone`, `_claim_pbi`, `_claim_pbi_worktree`; import back)
- Create: `tests/executor/test_pbi_claim.py`
- Modify: `tests/executor/test_loop.py` (remove migrated tests)
- Untouched but load-bearing: `tests/executor/test_loop_project_toml_warning.py` (its loop import survives via re-export — Seam 2)

Current source ranges: `_warn_project_toml_in_target_clone` 216–241; `_feature_branch_name` 414–415; `_ClaimError` 418–425; `_ENTRY_FILENAMES` 428 (probe order `("PBI.md", "BUG.md", "FEEDBACK.md")`); `_read_target_repo_from_pbi` 431–466; `_claim_pbi` 603–640; `_claim_pbi_worktree` 643–704 (signature `(cfg, pbi, *, target_url: str, info: TargetRepoInfo)`; `is_branch_remote` pre-flight at 674–678; `move_inbox_to_current` with `event_log=`, `now=`, `instance_id=cfg.instance_id`, `hostname=socket.gethostname()` at 681–688; `materialise_worktree` delegation present after Task 3).

- [ ] **Step 1: Create `ralph_executor/pbi_claim.py`**

Header in full (regenerated against what the bodies actually consume — v1's header was missing `socket`, `open_log`, `datetime`, `queue_repo_root`, and `TYPE_CHECKING TargetRepoInfo`, and carried module-level imports the bodies do locally):

```python
"""Claim a PBI from inbox/ into current/.

``claim_pbi`` is the public entry. In order:

  1. ``read_target_repo_from_pbi`` — read ``target_repo`` from the PBI
     entry-file frontmatter (probing ``PBI.md``, ``BUG.md``,
     ``FEEDBACK.md`` — same order as pbi_reader's discovery).
  2. ``parse_target_repo`` + host gate — only ``github.com`` is
     supported; failures raise ``ClaimError``.
  3. ``_claim_pbi_worktree`` — ensure the target clone
     (``target_clone.ensure_clone``), warn on legacy project TOML,
     pre-flight ``origin/<main_branch>``, move the PBI inbox/ ->
     current/ in the queue clone (``move_inbox_to_current``), and
     materialise the per-PBI work worktree on ``ralph/<id>``
     (``worktree_manager.materialise_worktree``).

Any step may raise ``ClaimError`` with a reason string. The caller in
the loop module catches it and routes the PBI to ``blocked/`` with the
reason recorded in HISTORY.md. ``feature_branch_name`` and
``read_target_repo_from_pbi`` are also consumed by the loop's resume
path, which re-derives target identity for PBIs already in current/.
"""

from __future__ import annotations

import logging
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ralph_executor import git_ops
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.movements import move_inbox_to_current
from ralph_executor.queue_git import queue_repo_root
from ralph_executor.safety import open_log
from ralph_executor.types import PBI
from ralph_executor.worktree_manager import materialise_worktree

if TYPE_CHECKING:
    from ralph_executor.url_utils import TargetRepoInfo

log = logging.getLogger(__name__)
```

Then the bodies, relocated with this rename map (verbatim otherwise; keep the function-local imports of `parse_target_repo` inside `claim_pbi`, and of `dataclasses.replace` + `target_clone as tc_mod` inside `_claim_pbi_worktree` — they are part of the moved bodies):

| old (loop.py) | new (pbi_claim.py) | source range |
|---|---|---|
| `_warn_project_toml_in_target_clone` | `warn_project_toml_in_target_clone` (public) | 216–241 |
| `_feature_branch_name` | `feature_branch_name` (public — resume path consumes it) | 414–415 |
| `_ClaimError` | `ClaimError` (public) | 418–425 |
| `_ENTRY_FILENAMES` | `_ENTRY_FILENAMES` (stays private) | 428 |
| `_read_target_repo_from_pbi` | `read_target_repo_from_pbi` (public — resume path consumes it) | 431–466 |
| `_claim_pbi` | `claim_pbi` (public) | 603–640 |
| `_claim_pbi_worktree` | `_claim_pbi_worktree` (stays private; name + keyword signature unchanged) | 643–704 |

Inside the moved bodies also substitute: `_ClaimError` → `ClaimError`; `_read_target_repo_from_pbi` → `read_target_repo_from_pbi`; `_feature_branch_name` → `feature_branch_name`; `_warn_project_toml_in_target_clone` → `warn_project_toml_in_target_clone`; `_queue_repo_root` → `queue_repo_root` (one site, the `open_log(...)` at 679); `_claim_pbi_worktree` call in `claim_pbi` keeps its name. The `materialise_worktree(cfg, clone_root=..., pbi_id=..., branch=...)` call (installed by Task 3) moves as-is.

- [ ] **Step 2: Update `ralph_executor/loop.py` — the import-back list**

Delete the seven moved definitions. Add:

```python
from ralph_executor.pbi_claim import (
    ClaimError,
    claim_pbi as _claim_pbi,
    feature_branch_name as _feature_branch_name,
    read_target_repo_from_pbi as _read_target_repo_from_pbi,
    warn_project_toml_in_target_clone as _warn_project_toml_in_target_clone,
)

# Backward-compat alias so older except clauses / monkeypatch / import
# paths keep resolving (exception classes alias cleanly).
_ClaimError = ClaimError
```

Every name in that list is load-bearing in the residual loop module — this is the v1 gap the verdict flagged:
- `_feature_branch_name`: `_run_ralph`'s diff at loop.py:867 and the resume path's `ensure_worktree(branch=...)` at loop.py:1092.
- `_read_target_repo_from_pbi` + `_ClaimError`: resume path `try/except` at loop.py:1019–1020.
- `_ClaimError`: `except _ClaimError as exc:` in `_iterate_once_inner` at loop.py:1208 (and the docstring of `_move_to_blocked_with_reason`).
- `_warn_project_toml_in_target_clone`: resume path at loop.py:1070 (and the re-export keeps `tests/executor/test_loop_project_toml_warning.py:7` importing from loop — Seam 2).
- `_claim_pbi`: call at loop.py:1186; patched at test_loop.py:309.

Unused-import sweep in loop.py: `yaml`, the `TYPE_CHECKING`/`TargetRepoInfo` block (loop.py:44–45 — was only used by `_claim_pbi_worktree`'s signature), and `move_inbox_to_current` (drop from the `queue.movements` import, keep the other movement imports) all become unused — remove them. `socket` STAYS (used by `run_loop` at loop.py:1277); `open_log`, `git_ops`, `ensure_worktree`, `work_worktree_path` stay (resume path / `_run_ralph`).

- [ ] **Step 3: Migrate tests → `tests/executor/test_pbi_claim.py`**

Move from `test_loop.py` (current line refs):
- `_build_pbi` helper (line 1020) — move if nothing left in test_loop.py uses it; grep before deleting.
- `test_read_target_repo_from_pbi_reads_frontmatter` (line 1033)
- `test_read_target_repo_from_pbi_raises_when_missing` (line 1055)
- `test_read_target_repo_from_pbi_raises_when_no_entry_file` (line 1077)
- `test_read_target_repo_from_pbi_uses_bug_md_for_bug_type` (line 1088)
- `_write_pbi_with_target` helper (line 1115) — COPY, don't move: the keeper tests at 1289/1479 still need it in test_loop.py.
- `test_claim_raises_claim_error_for_non_github_host` (line 1132)
- `test_claim_raises_claim_error_for_invalid_url` (line 1145)
- `test_claim_clones_target_and_creates_worktree_in_clone` (line 1163)
- `test_claim_raises_claim_error_when_clone_unreachable` (line 1259)
- **NEW vs v1:** `_init_empty_target_clone` helper (line 1515) and `test_iterate_once_moves_pbi_to_blocked_when_target_origin_main_missing` (line 1535) — it exercises `_claim_pbi_worktree`'s `is_branch_remote` pre-flight (the claim-atomicity contract), so it belongs with the claim tests.

Migration edits:
- `from ralph_executor.loop import _claim_pbi, _ClaimError, _read_target_repo_from_pbi` (at 1035, 1057, 1079, 1090, 1136, 1149, 1171, 1265) → `from ralph_executor.pbi_claim import ClaimError, claim_pbi, read_target_repo_from_pbi`; identifier substitutions to match (`_ClaimError` → `ClaimError`, etc.). `pytest.raises(_ClaimError, ...)` → `pytest.raises(ClaimError, ...)`.
- `from ralph_executor.loop import _pull_queue` (at 1224, 1265) → `from ralph_executor.queue_git import pull_queue`; call sites updated.
- The `monkeypatch.setattr("ralph_executor.loop.ensure_worktree", _fake_ensure_worktree)` at line 1232 (inside `test_claim_clones_target_and_creates_worktree_in_clone`) → retarget to `"ralph_executor.worktree_manager.ensure_worktree"` — the claim path's call now lives inside `materialise_worktree`.
- `test_iterate_once_moves_pbi_to_blocked_when_target_origin_main_missing` drives `iterate_once` — keep importing `iterate_once` (from `ralph_executor.loop` for now) and keep its loop-level patch targets; only the file moves.
- Keep all assertions verbatim. Delete moved tests from `test_loop.py`.

Keep `test_iterate_once_moves_pbi_to_blocked_when_claim_raises_claim_error` (line 1289) in `test_loop.py` — pure orchestration (claim → blocked routing).

- [ ] **Step 4: Gate**

```bash
uv run pytest tests/executor/test_pbi_claim.py tests/executor/test_loop.py tests/executor/test_loop_project_toml_warning.py -q 2>&1 | tail -5
uv run pytest tests/ -q 2>&1 | tail -5
uv run mypy ralph_executor 2>&1 | tail -3
uv run ruff check ralph_executor 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/pbi_claim.py ralph_executor/loop.py \
        tests/executor/test_pbi_claim.py tests/executor/test_loop.py
git commit -m "$(cat <<'EOF'
refactor(loop): extract pbi_claim.py (claim_pbi + ClaimError + frontmatter read)

Task 4 of loop.py split v2. _ClaimError -> ClaimError, _claim_pbi ->
claim_pbi; feature_branch_name / read_target_repo_from_pbi /
warn_project_toml_in_target_clone go public because the loop's resume
path consumes them — loop imports all five back under their old
underscore names so resume-path call sites, except clauses, and
existing monkeypatch/import paths keep resolving.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Rename `loop.py` → `iteration.py` — confidence **97%**

> Carried over from v1 with the importer list refreshed against current source (verdict item). Body below is complete; do not consult v1.

**Files:**
- Rename: `ralph_executor/loop.py` → `ralph_executor/iteration.py`
- Modify: every importer / string patch target / caplog logger kwarg naming `ralph_executor.loop`

- [ ] **Step 1: Inventory (grep is authoritative — Tasks 1–5 moved some of these)**

```bash
grep -rn "ralph_executor\.loop\|ralph_executor import loop" ralph_executor/ scripts/ tests/ docs/
```

Known current importers / targets (pre-plan line refs; the migrated copies created in Tasks 1–4 carry the same loop-level strings and are caught by the grep):
- `ralph_executor/__init__.py:15–20` — `from ralph_executor.loop import (IterationOutcome, IterationResult, iterate_once, run_loop)`.
- `ralph_executor/cli.py:67` — `from ralph_executor.loop import _pr_skill_scripts_path, iterate_once, run_loop`; **and the deferred import at cli.py:398** — `from ralph_executor.loop import _queue_repo_root` inside the reconcile path (easy to miss; it's runtime-only).
- `tests/executor/test_loop.py` — module docstring (line 1), import block (line 14), and every `"ralph_executor.loop.*"` monkeypatch string (~30 sites: `spawn_claude_p`, `_run_sweep`, `_check_cycle_detector`, `_run_ralph`, `_claim_pbi`, `_persist_iteration_writes`, `ensure_queue_clone`→already retargeted in T1, `ensure_worktree`, …).
- `tests/executor/test_loop_integration.py:21` + string targets at 101, 126, 157, 176, 185, 342, 405, 431, 470, 493.
- `tests/executor/test_loop_autobug_wires.py:10` (`from ralph_executor import autobug, loop`) + attr patches at 29, 52, 79, 106.
- `tests/executor/test_loop_diff_against_target.py` — imports at 58, 127, 194, 268; string targets at 75, 93, 108–109, 138, 156, 171–172, 208, 226, 241–242; caplog `logger="ralph_executor.loop"` at 174, 244.
- `tests/executor/test_loop_pr_skill_scripts_path.py:5`.
- `tests/executor/test_loop_project_toml_warning.py:7` + caplog `logger=` kwargs at 19, 35, 48, 68.
- `tests/executor/test_multi_ralph_integration.py:32` (import) and 279 (string target `"ralph_executor.loop.spawn_claude_p"`).
- `tests/safety/test_integration_loop.py:22` (import), 247/313/361 (`from ralph_executor import loop as loop_module`).
- `tests/executor/autobug/integration/test_python_crash_e2e.py` (imports `loop`; patch at line 20).
- New files from Tasks 1–4 (`test_queue_git.py`, `test_iteration_safety.py`, `test_worktree_manager.py`, `test_pbi_claim.py`) — they kept loop-level orchestration targets by design; update them now.
- Docstring-only mentions (`ralph_executor/url_utils.py:5`, `ralph_executor/claude_spawn.py:103`) are Task 8's job — skip here.

- [ ] **Step 2: Rename and update**

```bash
git mv ralph_executor/loop.py ralph_executor/iteration.py
```

Then for every match from Step 1: `ralph_executor.loop` → `ralph_executor.iteration`, `from ralph_executor import loop` → `from ralph_executor import iteration` (adjust local aliases — `loop_module`, `loop` — or keep them as `... import iteration as loop_module` for minimal diff; pick one style and apply consistently). Update caplog `logger="ralph_executor.loop"` kwargs to `"ralph_executor.iteration"` where the asserted log line is emitted by the residual module (the project-TOML tests assert on `pbi_claim`-emitted records after Task 4 — set those to `"ralph_executor.pbi_claim"`). Do NOT rename any test files in this commit (Task 7).

- [ ] **Step 3: Gate**

```bash
grep -rn "ralph_executor\.loop" ralph_executor/ scripts/ tests/ && echo "RESIDUE: FAIL" || echo "no residue: ok"
uv run pytest tests/ -q 2>&1 | tail -5
uv run mypy ralph_executor 2>&1 | tail -3
uv run ruff check ralph_executor 2>&1 | tail -3
```

- [ ] **Step 4: Commit**

```bash
git add ralph_executor/iteration.py ralph_executor/__init__.py ralph_executor/cli.py tests/
git commit -m "$(cat <<'EOF'
refactor(loop): rename loop.py -> iteration.py

Task 6 of loop.py split v2. git mv + import/patch-target/logger-kwarg
updates only; no logic change. Includes cli.py's deferred
_queue_repo_root import at the reconcile path.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Rename loop test files — confidence **97%**

**Files:**
- Rename: `tests/executor/test_loop.py` → `tests/executor/test_iteration.py`
- Rename: `tests/executor/test_loop_integration.py` → `tests/executor/test_iteration_integration.py`
- Rename: `tests/executor/test_loop_autobug_wires.py` → `tests/executor/test_iteration_autobug_wires.py` (**new vs v1** — it tests the `iterate_once` autobug fuse, an iteration.py concern; renaming keeps the convention consistent)
- Keep: `test_loop_diff_against_target.py`, `test_loop_pr_skill_scripts_path.py`, `test_loop_project_toml_warning.py` — narrow helper-focused satellites; renaming them buys nothing and churns history. (Optional follow-up outside this plan: fold them into the per-module test files.)

- [ ] **Step 1: git mv the three files**

```bash
git mv tests/executor/test_loop.py tests/executor/test_iteration.py
git mv tests/executor/test_loop_integration.py tests/executor/test_iteration_integration.py
git mv tests/executor/test_loop_autobug_wires.py tests/executor/test_iteration_autobug_wires.py
```

- [ ] **Step 2: Update cross-references**

v1's pattern (`test_loop\b`) missed underscore-suffixed names (`_` is a word character, so `\b` never fires inside `test_loop_autobug_wires`). Fixed pattern:

```bash
grep -rn "test_loop" tests/ scripts/ docs/ ralph_executor/
```

Replace references to the three renamed files with the new names; references to the three kept satellites stay.

- [ ] **Step 3: Gate**

```bash
uv run pytest tests/executor/test_iteration.py tests/executor/test_iteration_integration.py tests/executor/test_iteration_autobug_wires.py -q 2>&1 | tail -5
uv run pytest tests/ -q 2>&1 | tail -5
uv run mypy ralph_executor 2>&1 | tail -3
uv run ruff check ralph_executor 2>&1 | tail -3
```

- [ ] **Step 4: Commit**

```bash
git add tests/executor/test_iteration.py tests/executor/test_iteration_integration.py \
        tests/executor/test_iteration_autobug_wires.py
git commit -m "$(cat <<'EOF'
refactor(loop): rename loop test files to test_iteration*

Task 7 of loop.py split v2. Matches the module rename; adds
test_loop_autobug_wires.py to the rename set (v1 missed it). Three
satellite files keep their names (narrow helper coverage).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Doc + comment sweep — confidence **96%**

> Carried over from v1 with line refs refreshed and two additions (claude_spawn replica docstring, url_utils docstring). Body below is complete; do not consult v1.

**Files:**
- Modify: `ralph_executor/iteration.py` module docstring
- Modify: `ralph_executor/claude_spawn.py` (replica docstring at current 97–110)
- Modify: `ralph_executor/url_utils.py` (docstring line 5)
- Modify: `docs/runbooks/ralph-architecture.md` if it exists and names `loop.py`; README + other docs as found

- [ ] **Step 1: Inventory residual references**

```bash
grep -rn "loop\.py\|ralph_executor\.loop\b\|_claim_pbi_worktree\|_ClaimError\|_pull_queue\|_run_sweep\|_check_cycle_detector\|_persist_iteration_writes\|_cleanup_work_worktree\|_queue_repo_root\|_pr_skill_scripts_path\|_warn_project_toml" \
  docs/ ralph_executor/ scripts/ README.md
```

Expected matches:
- The aliased imports / wrapper / `_ClaimError = ClaimError` alias inside `iteration.py` — KEEP (intentional re-exports; the monkeypatch-preservation strategy depends on them).
- `cli.py`'s `_pr_skill_scripts_path` / `_queue_repo_root` imports — KEEP (they import the re-exported names; optionally retarget to the new modules, but that is a behavior-neutral choice — if retargeting `cli.py:398` to `queue_git.queue_repo_root`, also retarget the `ralph_executor.cli._pr_skill_scripts_path` patches in `tests/executor/test_cli_validate_startup.py:71` and `tests/executor/test_cli_reconcile.py:255`? No — those patch `cli`'s own attribute and are unaffected. Keep cli as-is unless trivially clean).
- `ralph_executor/claude_spawn.py:97–110` — `_queue_repo_root_for_spawn`'s docstring says its body "must stay in sync with ``ralph_executor.loop._queue_repo_root``". UPDATE to name `ralph_executor.queue_git.queue_repo_root` as the source of truth.
- `ralph_executor/url_utils.py:5` — docstring names `ralph_executor.loop._claim_pbi`. UPDATE to `ralph_executor.pbi_claim.claim_pbi`.
- Docs/README mentions of `loop.py` — UPDATE to name the five modules.

- [ ] **Step 2: Update `ralph_executor/iteration.py` module docstring**

Replace any self-reference to `loop.py` with `iteration.py`; confirm the Plan-8/9 stub lines are gone (Task 2 removed them — belt-and-braces re-check); add one sentence naming the four sibling modules (`queue_git`, `iteration_safety`, `worktree_manager`, `pbi_claim`) and the re-export convention.

- [ ] **Step 3: Architecture doc + README**

```bash
test -f docs/runbooks/ralph-architecture.md && grep -n "loop.py\|iterate_once" docs/runbooks/ralph-architecture.md || echo "no architecture doc to update"
grep -rn "loop\.py\|ralph_executor\.loop\b" README.md docs/ | head -20
```

Rewrite affected sections to name the five modules. Skip files that don't exist.

- [ ] **Step 4: Final verification + acceptance**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run mypy ralph_executor 2>&1 | tail -3
uv run ruff check ralph_executor 2>&1 | tail -3
test ! -f ralph_executor/loop.py && echo "loop.py removed: ok" || echo "loop.py still present: FAIL"
test -f ralph_executor/iteration.py && test -f ralph_executor/pbi_claim.py && \
    test -f ralph_executor/worktree_manager.py && test -f ralph_executor/queue_git.py && \
    test -f ralph_executor/iteration_safety.py && echo "five modules present: ok" || echo "missing modules: FAIL"
grep -n "Plan 8 stub\|Plan 9 stub\|Plan 8 fills in\|Plan 8 will replace" ralph_executor/iteration*.py ralph_executor/queue_git.py ralph_executor/pbi_claim.py ralph_executor/worktree_manager.py || echo "no stub residue: ok"
wc -l ralph_executor/iteration.py ralph_executor/pbi_claim.py ralph_executor/worktree_manager.py \
       ralph_executor/queue_git.py ralph_executor/iteration_safety.py
```

Expected: all `ok`, no `FAIL`; baseline test counts; module sizes within ±20% of the spec targets (~400/250/180/100/250 — iteration.py will land higher than the spec's 400 because the file grew ~240 lines since the spec; that is acceptable, the targets were set against the 1078-line file).

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/iteration.py ralph_executor/claude_spawn.py ralph_executor/url_utils.py \
        docs/ README.md
git commit -m "$(cat <<'EOF'
docs(loop-split): sweep residual loop.py references

Task 8 (final) of loop.py split v2. Updates module docstrings
(iteration.py, claude_spawn replica note, url_utils), architecture
docs, and README. Backward-compat aliases inside iteration.py are
intentional and preserved.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Final acceptance

After Task 8, run the Task 8 Step 4 block once more from a clean shell. Push `refactor/loop-py-split-v2` and open a PR titled `refactor(loop): split loop.py into iteration + 4 sibling modules`. Reference the spec (`27ad1d3`), the v1 plan, and this v2 in the description.
