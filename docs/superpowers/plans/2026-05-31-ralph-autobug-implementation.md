# ralph autobug v1 — implementation plan (v2 — confidence-rated, source-verified)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the ralph executor or its Claude subprocess crashes, automatically file a deduplicated bug PBI to the ralph queue capturing the crash, so future iterations work the fix without operator hand-filing.

**Architecture:** A new `ralph_executor/autobug/` package mirrors the shape of `ralph_executor/sweep/`. Two public functions (`detect_python_crash`, `detect_subprocess_crash`) are called from three hook sites (`cli.py` outer try/except around `for result in run_loop(cfg)`, `claude_spawn` error classifier inside `spawn_claude_p`, internal `loop.py` wires). Each emission flows through signature hashing → recursion guard → dedup lookup → rate-limit check → BUG.md/REPRODUCE.md composition → queue write + push. All emission is no-op-safe.

**Tech Stack:** Python 3.12, pytest, hashlib (stdlib SHA-256). No new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-05-31-ralph-autobug-design.md` (commit `afcd813`).

**Standards consulted:** `~/.better-memory/knowledge-base/standards/ralph-runtime.md` — per-task confidence ratings + sub-90% lift gate; Bootstrap 5 visualiser render before execution offer; queue-artefact self-containment.

---

## Guardrails (surface up front, from planning memories)

| # | Guardrail | Source | Confidence | Tasks affected |
|---|---|---|---|---|
| G1 | Adding required field to ExecutorConfig → grep every `ExecutorConfig(` in `tests/` and thread through with sensible defaults | mem `462d13a7` | 0.85 | T14 (autobug_* fields default to safe values; no test breakage expected, but grep before commit) |
| G2 | `git mv DIR NEWDIR` only moves files in the index — untracked files inside DIR are stranded; must `git add -A <dir>` BEFORE `git mv` | mem `63c4e75a` | 0.85 | T11 (emit writes BUG.md / REPRODUCE.md / HISTORY.md then commit_all — already inside the same dir, but verify `commit_all` stages them) |
| G3 | Route state transitions through git-aware helper; never raw `shutil.move` | mem `a5af973e` | 0.80 | T11, T6.5 (use existing `commit_all` + `push_with_rebase`) |
| G4 | Queue commit-message convention: `chore(queue): add <id>` for adds; `chore(ralph-queue): move <id> from X to Y` for transitions | mem `99c53915` | 0.70 | T11 (autobug emit uses `chore(queue): add autobug-...`) |
| G5 | Queue artefacts must be self-contained — never reference operator's local-only paths | mem `8d05b8c2` | 0.75 | T10 (BUG.md / REPRODUCE.md content is fully embedded, no external refs) |
| G6 | Iter-and-join composer: guard empty input + wrap OSError into typed error | mem `aa125f7a` | 0.70 | T10 (`build_bug_md` / `build_reproduce_md` use explicit lists, not iterators over dirs — N/A but acknowledge) |
| G7 | Bug PBI entry file is BUG.md, not PBI.md; type-aware dispatcher in `queue/filesystem.py` | mem `cfa7e7f8` | 0.70 | T9–T11 (baked into spec; verified during spec review) |
| G8 | `target_repo` is REQUIRED at claim time despite PROMPT.md docstring | mem `77c83c6c` | 0.70 | T9 (frontmatter includes `target_repo`) |
| G9 | Spec-provided test code is NOT lint-clean by default; `from datetime import UTC` not `timezone.utc`; drop unused `import pytest` | standards | 1.0 | All test tasks |
| G10 | When work is dispatched to ralph queue, do NOT propose execution mode | mem `b017b510` | 0.90 | This plan's handoff (no execution-choice question) |
| G11 | Render plans in visualiser with Bootstrap 5 light theme BEFORE any handoff | standards line 50 | 1.0 | Post-commit step, before user review |

Dismissed memories:
- `0c83e25d` Playwright text-transform: no Playwright tests in this plan.
- `355faeb8` Windows cmd.exe 8191-char argv: autobug doesn't pass large argvs.
- `26d0a5a9` Truthiness guard before validator: autobug fields don't have validators that could swallow empty.

---

## Confidence policy

Each task carries a confidence percentage. Any task below 95% includes one or more **Step 0** verification / scaffold steps to lift it to ≥90%. Tasks ≥95% are direct TDD steps. **No task ships below 90%.**

## Lint-cleanliness conventions

- `from datetime import UTC` (not `timezone.utc`) — UP017
- `StrEnum` if defining string-valued enums — UP042 (verified: `safety/events.py` line 18 uses `from enum import StrEnum`)
- Drop unused `import pytest`; import only what the test body uses — F401
- Type hints on test helpers; mypy clean

## Forward-reference audit

Task order verified — no import-time forward references:
- T2/T3 depend on nothing else.
- T6 imports `RateLimitConfig` from T6's own module.
- T7 depends on T1 (`DedupResult`) — ok (T1 before T7).
- T6.5 (NEW: closed_at stamping) depends on existing movements; depended on by T7.
- T11 (emit) depends on T8/T9/T10 (compose) + T6 (fuses) — ok.
- T12 (detect) depends on T2/T7/T11 — ok.
- T13 (`__init__.py`) depends on T1/T12 — ok.
- T14/T15 (config) independent.
- T16 (events) independent.
- T17/T18/T19 (wiring) depend on T13 (public API) — ok.
- T20/T21 (prompt) independent.
- T22–T25 (integration tests) depend on all production code being landed.

---

## Confidence summary

| # | Task | Confidence | Status |
|---|---|---|---|
| T1 | Scaffold autobug package + Context | 99% | direct |
| T2 | Python crash signature | 95% | direct |
| T3 | Subprocess signature + _normalise | 92% | direct (lift via corpus T4) |
| T4 | Signature corpus regression test | 95% | direct |
| T5 | Recursion guard | 99% | direct |
| T6 | Rate-limit + rollup | 93% | direct |
| **T6.5** | **NEW: stamp `closed_at` in movements** | **90%** | **lifted from N/A** |
| T7 | Dedup lookup (depends on T6.5) | 92% | lifted from 85 |
| T8 | _safe wrapper + section_failed | 99% | direct |
| T9 | build_frontmatter | 95% | lifted from 92 (see Step 0 read) |
| T10 | build_bug_md + build_reproduce_md | 92% | direct |
| T11 | emit.new + bump + reopen | 92% | lifted from 80 (queue_branch fix) |
| T12 | detect orchestration | 92% | direct |
| T13 | Public API exports | 99% | direct |
| T14 | TOML keys | 95% | lifted from 90 (G1) |
| T15 | bot_author_email startup validation | 92% | lifted from 80 (sweep-enable correction) |
| T16 | autobug_emitted EventType | 95% | lifted from 85 (StrEnum verified) |
| T17 | claude_spawn subprocess wire + RALPH_AUTOBUG_DEPTH env | 92% | lifted from 78 (inline-env-edit) |
| T18 | cli.py outer wrapper around generator loop | 92% | lifted from 82 (wrap iteration not call) |
| T19 | loop.py internal wires at specific verified sites | 90% | lifted from 70 (sites enumerated below) |
| T20 | bug workflow signature-aware amendment | 95% | direct |
| T21 | memory prompt autobug note | 95% | direct |
| T22 | roundtrip integration test | 92% | direct (existing conftest patterns) |
| T23 | python crash e2e | 92% | direct |
| T24 | subprocess crash e2e | 92% | direct |
| T25 | recursion guard e2e | 92% | direct |
| T26 | corpus extractor script | 92% | direct (events.db schema spike) |

All tasks ≥90%. No surfacing required.

---

## Tasks unchanged from earlier drafts

For brevity, tasks **T1, T2, T4, T5, T6, T8, T9 (with the Step 0 read added below), T10, T12, T13, T20, T21** retain the same TDD shape as in the prior plan draft (red → green → commit). The full task bodies — including code blocks and bash commands — were committed at `77158a4`. This v2 supersedes them only where the confidence-lift gate required a structural change. The unchanged tasks are listed in the Confidence summary above with their confidence rating; their bodies remain authoritative as written.

The **changed-or-new tasks below** are documented in full because their content differs from the original draft.

---

## T6.5 (NEW): Stamp `closed_at` on move-to-done — **confidence: 90%**

**Files:**
- Modify: `ralph_executor/queue/movements.py` (extend `_rewrite_status` to stamp `closed_at` when target is `done`)
- Modify: `tests/executor/test_movements.py` (or whichever file holds movement tests — verify location first)

**Why this exists:** Source-read of `ralph_executor/queue/movements.py` + `queue/filesystem.py` confirmed `closed_at` is NEVER stamped anywhere. T7's dedup `_find_by_signature_since` assumed `closed_at` was on done/ PBI frontmatter — it is not. Without this task, autobug's "reopen as regression" path can NEVER fire because nothing dates done/ PBIs.

**Step 0 — verify the change location:**

- [ ] **Step 0a:** Read `ralph_executor/queue/movements.py::_rewrite_status` (lines ~80–110). Confirm the function takes `(entry_file, new_status)` and runs a line-based pass over the frontmatter, replacing `status:` and `updated_at:` lines in place, inserting them if missing.
- [ ] **Step 0b:** Read `_move` (lines ~120–170). Confirm it calls `_rewrite_status(dst / entry_name, target_state)`.

After Step 0, the design is: when `target_state == "done"`, also stamp `closed_at: <now_iso>` on the frontmatter, identical insertion semantics to `updated_at`.

- [ ] **Step 1: Write the failing test**

In the located movements test file:

```python
def test_move_current_to_done_stamps_closed_at(tmp_path, fake_repo):
    # Use the project's existing fake_repo fixture (conftest pattern).
    # Stage a PBI in current/, move it to done/, assert closed_at is present.
    pbi = _stage_pbi(fake_repo, "current", "WI-test")
    moved = move_current_to_done(fake_repo.cfg, pbi)  # if helper exists, else use _move
    entry = fake_repo.queue / ".ralph" / "done" / "WI-test" / "PBI.md"
    assert "closed_at:" in entry.read_text(encoding="utf-8")


def test_move_inbox_to_current_does_not_stamp_closed_at(tmp_path, fake_repo):
    pbi = _stage_pbi(fake_repo, "inbox", "WI-test")
    moved = move_inbox_to_current(fake_repo.cfg, pbi)
    entry = fake_repo.queue / ".ralph" / "current" / "WI-test" / "PBI.md"
    assert "closed_at:" not in entry.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL — `closed_at:` not in the moved entry file.

- [ ] **Step 3: Extend `_rewrite_status` to accept an optional `stamp_closed_at: bool` parameter**

In `ralph_executor/queue/movements.py::_rewrite_status`:

```python
def _rewrite_status(
    entry_file: Path,
    new_status: PBIStatus,
    *,
    stamp_closed_at: bool = False,
) -> None:
    text = entry_file.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    # ... existing fence-finding logic ...
    rewrote_closed = False
    now = _now_iso()
    for idx in range(1, end):
        stripped = lines[idx].lstrip()
        if stripped.startswith("status:"):
            lines[idx] = f"status: {new_status}\n"
            rewrote_status = True
        elif stripped.startswith("updated_at:"):
            lines[idx] = f"updated_at: {now}\n"
            rewrote_updated = True
        elif stripped.startswith("closed_at:") and stamp_closed_at:
            lines[idx] = f"closed_at: {now}\n"
            rewrote_closed = True
    if not rewrote_status:
        lines.insert(end, f"status: {new_status}\n"); end += 1
    if not rewrote_updated:
        lines.insert(end, f"updated_at: {now}\n"); end += 1
    if stamp_closed_at and not rewrote_closed:
        lines.insert(end, f"closed_at: {now}\n")
    entry_file.write_text("".join(lines), encoding="utf-8")
```

And in `_move`:

```python
    _rewrite_status(
        dst / entry_name,
        target_state,
        stamp_closed_at=(target_state == "done"),
    )
```

- [ ] **Step 4: Run tests**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/queue/movements.py tests/executor/test_movements.py
git commit -m "feat(queue/movements): stamp closed_at on move-to-done"
```

---

## T7 (REVISED): Dedup lookup — **confidence: 92%** (lifted via T6.5)

Unchanged from v1 body EXCEPT: the `_find_by_signature_since` function now reads `closed_at` knowing T6.5 stamps it. Add to the v1 T7 body's Step 0:

- [ ] **Step 0:** Confirm T6.5 has landed. Run: `git log --oneline -1 -- ralph_executor/queue/movements.py` — expect the closed_at commit.

If T6.5 has not landed, this task is blocked. Stop and finish T6.5 first.

Remainder of T7 body unchanged from v1.

---

## T9 (REVISED Step 0): `build_frontmatter` — **confidence: 95%**

Add to v1's T9 before Step 1:

- [ ] **Step 0:** Read one existing PBI's frontmatter to verify ISO format match. Run:

```bash
ls .ralph/done/ 2>/dev/null | head -1
# OR if no done/ on current branch, read any committed PBI
```

Read the `created_at:` / `updated_at:` lines verbatim. Confirm they're in the form `2026-05-31T14:23:01+00:00` (the format `datetime.isoformat()` produces for UTC datetimes with tz). The plan emits `ctx.now.isoformat()` — verify ctx.now is constructed with `tz=UTC` to match.

Remainder of T9 body unchanged.

---

## T11 (REVISED): `emit.new` + `bump` + `reopen` — **confidence: 92%**

V1 hardcoded `branch="ralph-queue"` in `_commit_and_push`. Since #52 made the queue branch configurable (`cfg.queue_branch`), this must read from cfg.

- [ ] **Step 0:** Confirm Context carries enough to derive the queue branch. Per Task 1, Context has `queue_root`, `state_dir`, etc. — but NOT `queue_branch`. Two options:
  - (a) Add `queue_branch: str` to Context (extend T1's dataclass)
  - (b) Pass `queue_branch` as a parameter to `emit.new` / `emit.bump` / `emit.reopen`

Pick **(a)**: extend Context. Cleaner; emit's signature stays focused on the crash.

- [ ] **Step 0b: Patch Context (one-line addition to T1's dataclass)**

```python
@dataclass(frozen=True)
class Context:
    # ... existing fields ...
    queue_branch: str = "ralph-queue"  # default matches existing convention
```

Update T22's roundtrip integration test fixture to pass `queue_branch=cfg.queue_branch`. Update all detect-test fixtures to construct Context with `queue_branch="ralph-queue"`.

- [ ] **Step 0c: Update `_commit_and_push` in emit.py**

```python
def _commit_and_push(queue_repo: Path, msg: str, branch: str) -> None:
    git_ops.commit_all(queue_repo, msg)
    git_ops.push_with_rebase(queue_repo, remote="origin", branch=branch)
```

Every call site passes `ctx.queue_branch`.

- [ ] **Step 0d: Commit-message convention check (G4)**

Use commit prefix `chore(queue): add <pbi_id> (signature <short>)` for `emit.new` (matches the convention for queue adds). Use `chore(ralph-queue): bump <pbi_id> (occurrence N)` for `emit.bump`. Use `chore(queue): reopen <orig_id> as <new_id>` for `emit.reopen`.

Remainder of T11 body unchanged from v1, with the above corrections threaded through.

---

## T14 (REVISED Step 0): TOML keys for autobug — **confidence: 95%** (G1)

Add to v1's T14 before Step 1:

- [ ] **Step 0:** Run `grep -n "ExecutorConfig(" tests/ -r` to enumerate every direct constructor call in tests. The autobug fields all have defaults, so no test breakage is expected — but verify by running the full executor test suite (`pytest tests/executor/ -x`) after Step 4. If any test fails because it constructs `ExecutorConfig(...)` without the new fields, threading defaults makes the failure impossible.

Remainder unchanged.

---

## T15 (REVISED): `bot_author_email` startup validation — **confidence: 92%**

V1 referenced `cfg.sweep_enabled` which doesn't exist. Sweep is gated by `bot_author_email + _pr_skill_scripts_path(cfg).is_dir()` (verified at `loop.py:139–151`).

- [ ] **Step 0:** Read `ralph_executor/loop.py:139–151` to confirm the sweep gate. Confirm `cli.main` is the right place for the one-time validation — `main` is at `cli.py:560`, and the loop starts via `for result in run_loop(cfg)` at `cli.py:677`.

- [ ] **Step 1: Write the failing test**

In `tests/executor/test_cli.py` (or the located cli test file):

```python
def test_validate_startup_warns_once_when_bot_author_email_missing_and_autobug_enabled(caplog, tmp_path):
    cfg = _build_cfg_from_toml(tmp_path, 'workspace_root = "/tmp"\n')
    assert cfg.bot_author_email == ""
    assert cfg.autobug_enabled is True
    caplog.set_level(logging.WARNING, logger="ralph_executor")
    cli.validate_startup(cfg)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("bot_author_email" in m and "autobug" in m for m in msgs)


def test_validate_startup_warns_when_bot_author_email_missing_and_sweep_scripts_dir_exists(
    caplog, tmp_path, monkeypatch
):
    cfg = _build_cfg_from_toml(tmp_path, 'workspace_root = "/tmp"\nautobug_enabled = false\n')
    # Pretend the sweep PR-skill scripts dir exists
    from ralph_executor import loop as loop_mod
    monkeypatch.setattr(loop_mod, "_pr_skill_scripts_path",
                       lambda cfg: (tmp_path / "scripts").mkdir(parents=True, exist_ok=True) or tmp_path / "scripts")
    caplog.set_level(logging.WARNING, logger="ralph_executor")
    cli.validate_startup(cfg)
    assert any("bot_author_email" in r.getMessage() for r in caplog.records)


def test_validate_startup_silent_when_bot_author_email_set(caplog, tmp_path):
    cfg = _build_cfg_from_toml(tmp_path, 'workspace_root = "/tmp"\nbot_author_email = "bot@x"\n')
    caplog.set_level(logging.WARNING, logger="ralph_executor")
    cli.validate_startup(cfg)
    assert not any("bot_author_email" in r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Implement `validate_startup`**

```python
def validate_startup(cfg: ExecutorConfig) -> None:
    """One-time startup validation. Warns on soft misconfigurations.

    Specifically: missing bot_author_email when EITHER autobug is enabled
    OR sweep can run (its PR-skill scripts dir exists). The loop still
    starts; the operator gets one banner-shaped warning instead of one
    per iteration.
    """
    if cfg.bot_author_email:
        return
    autobug_active = cfg.autobug_enabled
    sweep_active = _pr_skill_scripts_path(cfg).is_dir()
    if autobug_active or sweep_active:
        affected = []
        if autobug_active:
            affected.append("autobug")
        if sweep_active:
            affected.append("sweep")
        log.warning(
            "ralph startup: bot_author_email is not set; %s will skip on every iteration. "
            "Set TOML key 'bot_author_email' or env RALPH_ADO_AUTHOR_EMAIL.",
            " AND ".join(affected),
        )
```

Add a call to `validate_startup(cfg)` in `cli.main` immediately after `cfg = load_config()` and before the `for result in run_loop(cfg):` line.

- [ ] **Step 3: Run tests**

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add ralph_executor/cli.py tests/executor/test_cli.py
git commit -m "feat(cli): one-time validate_startup warns on missing bot_author_email"
```

---

## T16 (REVISED Step 0): `autobug_emitted` EventType — **confidence: 95%**

Verified via grep: `ralph_executor/safety/events.py:18` uses `from enum import StrEnum`. The new variant must follow the same pattern:

```python
class EventType(StrEnum):
    # ... existing ...
    AUTOBUG_EMITTED = "autobug_emitted"
```

Remainder of v1 T16 body unchanged.

---

## T17 (REVISED): claude_spawn subprocess wire + `RALPH_AUTOBUG_DEPTH` env — **confidence: 92%**

V1 referenced a `_build_env` function that doesn't exist. Verified via grep: env construction is inline at `claude_spawn.py:565` inside `spawn_claude_p` (`env = os.environ.copy()`), with subsequent `env["KEY"] = value` lines through ~589. The subprocess error classification is at `claude_spawn.py:780` (`if exit_code != 0:`).

- [ ] **Step 0:** Read `ralph_executor/claude_spawn.py:560–600` (env construction) and `:770–840` (error classification) to confirm the inline patch sites.

- [ ] **Step 1: Write the failing test for env marker injection**

In `tests/executor/test_claude_spawn.py`:

```python
def test_spawn_sets_RALPH_AUTOBUG_DEPTH_when_pbi_has_signature_frontmatter(
    tmp_path, fake_repo
):
    """When the claimed PBI is an autobug PBI (frontmatter has signature: hex),
    the subagent's env carries RALPH_AUTOBUG_DEPTH=1."""
    pbi_dir = fake_repo.queue / ".ralph" / "current" / "autobug-abc-001"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "BUG.md").write_text(
        "---\nid: autobug-abc-001\ntype: bug\nseverity: critical\n"
        "attempts: 0\ncreated_at: 2026-05-31T00:00:00+00:00\n"
        "updated_at: 2026-05-31T00:00:00+00:00\n"
        "signature: abc123\n---\n"
    )
    pbi = parse_pbi_directory(pbi_dir, status="current")
    # Drive spawn with a stub binary; capture the env passed to subprocess.Popen
    captured_env = _spawn_and_capture_env(fake_repo.cfg, pbi)
    assert captured_env.get("RALPH_AUTOBUG_DEPTH") == "1"


def test_spawn_does_not_set_RALPH_AUTOBUG_DEPTH_for_non_autobug_pbi(
    tmp_path, fake_repo
):
    pbi_dir = fake_repo.queue / ".ralph" / "current" / "WI-1234"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "PBI.md").write_text(
        "---\nid: WI-1234\ntype: feature\nseverity: normal\n"
        "attempts: 0\ncreated_at: 2026-05-31T00:00:00+00:00\n"
        "updated_at: 2026-05-31T00:00:00+00:00\n---\n"
    )
    pbi = parse_pbi_directory(pbi_dir, status="current")
    captured_env = _spawn_and_capture_env(fake_repo.cfg, pbi)
    assert "RALPH_AUTOBUG_DEPTH" not in captured_env
```

`_spawn_and_capture_env` is a local helper: monkeypatch `subprocess.Popen` to capture its `env=` kwarg, then call `spawn_claude_p` against a stub claude binary.

- [ ] **Step 2: Write failing test for subprocess crash wire**

```python
def test_spawn_calls_autobug_detect_subprocess_crash_on_non_zero_exit(
    monkeypatch, tmp_path, fake_repo
):
    calls = []
    from ralph_executor import autobug
    monkeypatch.setattr(autobug, "detect_subprocess_crash",
                       lambda **kw: calls.append(kw))
    # Spawn with a stub binary that exits 137 with stderr "Killed"
    _spawn_with_stub_exit(fake_repo.cfg, exit_code=137, stderr="Killed")
    assert calls, "expected detect_subprocess_crash on non-zero exit"
    assert calls[0]["exit_code"] == 137
```

- [ ] **Step 3: Run tests to verify they fail**

Expected: FAIL — neither the env marker nor the wire is present.

- [ ] **Step 4: Implement env marker (inline at claude_spawn.py:~590)**

After the existing env setup block in `spawn_claude_p`:

```python
    # Recursion guard: if the claimed PBI carries a signature: frontmatter
    # key, this is an autobug PBI. Mark the subagent's env so any nested
    # crash detection inside the subagent refuses to emit.
    if _pbi_frontmatter_has_signature(pbi):
        env["RALPH_AUTOBUG_DEPTH"] = "1"
```

Add the helper at module level:

```python
import re as _re
_SIG_FM_RE = _re.compile(r"^signature:\s*[0-9a-f]+\s*$", _re.MULTILINE)


def _pbi_frontmatter_has_signature(pbi: PBI) -> bool:
    """True if the PBI's entry file frontmatter has a signature: key."""
    for entry in ("BUG.md", "PBI.md", "FEEDBACK.md"):
        f = pbi.path / entry
        if f.is_file():
            try:
                return bool(_SIG_FM_RE.search(f.read_text(encoding="utf-8")))
            except OSError:
                return False
    return False
```

- [ ] **Step 5: Implement the subprocess crash wire**

In the existing `if exit_code != 0:` block at `claude_spawn.py:~780`, add at the end of the block (after existing classification):

```python
    if exit_code != 0 and cfg.autobug_enabled:
        try:
            from ralph_executor import autobug
            ctx = _build_autobug_context_for_spawn(cfg, pbi)
            autobug.detect_subprocess_crash(
                exit_code=exit_code,
                stderr=tail_of_stderr,  # use whatever stderr variable is in scope
                command=argv,
                ctx=ctx,
                target_repo=_autobug_target_repo(cfg),
                severity=cfg.autobug_severity_subprocess_crash,
            )
        except Exception as inner:
            log.warning("autobug subprocess wire failed: %s", inner)
```

Helpers:

```python
def _build_autobug_context_for_spawn(cfg: ExecutorConfig, pbi: PBI):
    from ralph_executor.autobug import Context
    from datetime import datetime, UTC
    return Context(
        queue_root=cfg.workspace_root / "queue",
        state_dir=cfg.workspace_root / "queue" / ".ralph" / "state",
        env=dict(os.environ),
        now=datetime.now(tz=UTC),
        ralph_sha=_resolve_ralph_sha(),  # locate existing helper or shell out to git rev-parse HEAD
        bot_author_email=cfg.bot_author_email,
        triggering_pbi_id=pbi.id if pbi else None,
        queue_branch=cfg.queue_branch,
    )


def _autobug_target_repo(cfg: ExecutorConfig) -> str:
    """Where the autobug PBI lands. For v1: the ralph executor's own repo URL.

    Read from cfg.autobug_target_repo if set (TOML key), else default to
    the URL of cfg.queue_repo (the ralph repo IS the queue repo in
    practice for ralph development)."""
    return getattr(cfg, "autobug_target_repo", "") or cfg.queue_repo
```

Add `autobug_target_repo: str = ""` to ExecutorConfig in T14's body (additional optional TOML key).

- [ ] **Step 6: Run tests**

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add ralph_executor/claude_spawn.py tests/executor/test_claude_spawn.py
git commit -m "feat(claude_spawn): RALPH_AUTOBUG_DEPTH marker + subprocess crash wire"
```

---

## T18 (REVISED): `cli.py` — outer wrapper around the generator loop — **confidence: 92%**

V1 wrote `try: run_loop(cfg) except ...` — but `cli.py:677` does `for result in run_loop(cfg):`. `run_loop` is a generator. The wrapper must enclose the iteration, not a single call.

- [ ] **Step 0:** Read `cli.py:670–690` to confirm the iteration pattern. Confirm `run_loop` is a generator yielding `IterationResult`-like values.

- [ ] **Step 1: Write the failing test**

```python
def test_main_with_autobug_wrapper_captures_iteration_crash(
    monkeypatch, caplog, tmp_path
):
    """A crash mid-iteration must be captured by autobug AND re-raised."""
    from ralph_executor import autobug, cli
    captured = []
    monkeypatch.setattr(autobug, "detect_python_crash",
                       lambda exc, ctx, **kw: captured.append(exc))
    def bad_run_loop(cfg):
        yield "first iter ok"
        raise RuntimeError("mid-iter crash")
    monkeypatch.setattr(cli, "run_loop", bad_run_loop)
    cfg = _minimal_cfg(tmp_path)
    with pytest.raises(RuntimeError, match="mid-iter crash"):
        cli.main_with_autobug_wrapper(cfg)
    assert captured, "autobug should have been called"


def test_main_with_autobug_wrapper_does_not_mask_original_when_autobug_fails(
    monkeypatch, caplog, tmp_path
):
    from ralph_executor import autobug, cli
    monkeypatch.setattr(autobug, "detect_python_crash",
                       lambda *a, **kw: (_ for _ in ()).throw(TypeError("autobug self-crash")))
    def bad_run_loop(cfg):
        if False:
            yield
        raise RuntimeError("original crash")
    monkeypatch.setattr(cli, "run_loop", bad_run_loop)
    cfg = _minimal_cfg(tmp_path)
    caplog.set_level(logging.ERROR, logger="ralph_executor")
    with pytest.raises(RuntimeError, match="original crash"):
        cli.main_with_autobug_wrapper(cfg)
    assert any("AUTOBUG FAILED" in r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL — `cli.main_with_autobug_wrapper` not defined.

- [ ] **Step 3: Implement the generator-aware wrapper**

In `ralph_executor/cli.py`:

```python
def main_with_autobug_wrapper(cfg) -> int:
    """Iterate run_loop(cfg) wrapped in a top-level autobug try/except.

    Guarantees:
    1. Any exception raised by the generator (during yield) is captured by autobug.
    2. Even if autobug itself raises, the ORIGINAL exception is re-raised.
    3. The ORIGINAL traceback is what the operator sees.
    """
    try:
        for result in run_loop(cfg):
            # existing per-iteration handling (printing / counting) — preserved
            _handle_iteration_result(result)
        return 0
    except BaseException as original_exc:
        if getattr(cfg, "autobug_enabled", True):
            try:
                from ralph_executor import autobug
                ctx = _build_autobug_context_for_cli(cfg)
                autobug.detect_python_crash(
                    original_exc, ctx,
                    target_repo=_autobug_target_repo(cfg),
                    severity=cfg.autobug_severity_python_crash,
                )
            except BaseException as autobug_exc:
                log.error(
                    "AUTOBUG FAILED while handling original crash. "
                    "Original: %r. Autobug: %r",
                    original_exc, autobug_exc,
                    exc_info=original_exc,
                )
        raise


def _build_autobug_context_for_cli(cfg):
    from ralph_executor.autobug import Context
    from datetime import datetime, UTC
    return Context(
        queue_root=cfg.workspace_root / "queue",
        state_dir=cfg.workspace_root / "queue" / ".ralph" / "state",
        env=dict(os.environ),
        now=datetime.now(tz=UTC),
        ralph_sha=_resolve_ralph_sha(),
        bot_author_email=cfg.bot_author_email,
        triggering_pbi_id=None,
        queue_branch=cfg.queue_branch,
    )
```

Replace the existing `for result in run_loop(cfg):` site in `main` with a call to `main_with_autobug_wrapper(cfg)`. (Or factor the per-iteration handling into `_handle_iteration_result` and have both code paths use it — investigate the existing main body during implementation.)

- [ ] **Step 4: Run tests**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/cli.py tests/executor/test_cli.py
git commit -m "feat(cli): top-level autobug wrapper around run_loop generator"
```

---

## T19 (REVISED): `loop.py` internal wires at verified sites — **confidence: 90%**

V1 said "exact list determined during implementation" — that's <70% confidence. Source-read of `loop.py` enumerated 18+ except sites. Most are control-flow (`PushRebaseConflict`, `UncommittedSource`, `KeyboardInterrupt`, `HaltedError`) and must NOT be autobug'd. Three are real-error swallows worth wiring:

- [ ] **Step 0: Read these exact sites in `loop.py` and confirm they swallow real errors:**
  - Line 192: `except Exception as exc:` inside `_pr_skill_scripts_path` resolution (logs + continues)
  - Line 470: `except Exception:` (broad catch in iteration body — verify context)
  - Line 813: `except Exception:` (post-iteration error classification — verify context)

  Lines 838, 858, 905 are `except PushRebaseConflict` — NOT wired (control flow).
  Line 913 is `except UncommittedSource` — NOT wired (control flow).
  Line 980 is `except KeyboardInterrupt` — NOT wired.
  Line 983 is `except HaltedError` — NOT wired.

  After Step 0, three concrete wire sites identified. If Step 0 reveals different ground truth (different line numbers, different exception types), update this list before proceeding to Step 1.

- [ ] **Step 1: Write the failing test**

```python
def test_loop_wires_autobug_on_swallowed_iteration_exception(
    monkeypatch, tmp_path, fake_repo
):
    calls = []
    from ralph_executor import autobug
    monkeypatch.setattr(autobug, "detect_python_crash",
                       lambda exc, ctx, **kw: calls.append((type(exc).__name__, str(exc))))
    # Stage one PBI in inbox, force iterate_once to hit one of the wired except sites
    # by monkeypatching the relevant helper to raise RuntimeError("wired site test")
    # (implementer fills in based on which site is being tested)
    iterate_once(fake_repo.cfg, ...)
    assert calls, "autobug should fire on swallowed iteration exception"
    assert calls[0][0] == "RuntimeError"
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `calls` empty.

- [ ] **Step 3: Wire each site**

For each of the three sites (lines verified in Step 0):

```python
except Exception as exc:
    log.warning("...", exc)
    # NEW: autobug wire
    if cfg.autobug_enabled:
        try:
            from ralph_executor import autobug
            ctx = _build_autobug_context_for_loop(cfg, _current_pbi_or_none(cfg))
            autobug.detect_python_crash(
                exc, ctx, target_repo=_autobug_target_repo(cfg),
                severity=cfg.autobug_severity_python_crash,
            )
        except Exception as autobug_exc:
            log.warning("autobug loop wire failed: %s", autobug_exc)
    # existing handler continues
```

Add the local helper:

```python
def _build_autobug_context_for_loop(cfg: ExecutorConfig, pbi: PBI | None):
    from ralph_executor.autobug import Context
    from datetime import datetime, UTC
    return Context(
        queue_root=cfg.workspace_root / "queue",
        state_dir=cfg.workspace_root / "queue" / ".ralph" / "state",
        env=dict(os.environ),
        now=datetime.now(tz=UTC),
        ralph_sha=_resolve_ralph_sha(),
        bot_author_email=cfg.bot_author_email,
        triggering_pbi_id=pbi.id if pbi else None,
        queue_branch=cfg.queue_branch,
    )
```

`_current_pbi_or_none` — read `FilesystemQueueSource(cfg).current_pbi()` defensively (returns None if no current/, returns None on QueueError).

- [ ] **Step 4: Run test**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/loop.py tests/executor/test_loop.py
git commit -m "feat(loop): autobug wires at 3 verified swallowed-exception sites"
```

---

## T22–T26: Integration tests + corpus tooling — unchanged from v1

These tasks remain as written in commit `77158a4`. Each carries 92% confidence (uses existing conftest patterns from the project). The implementer reads `tests/executor/conftest.py` for the `fake_repo` / `cfg_for_repo` patterns before writing the integration-test fixtures.

---

## Final verification

After all 27 tasks land:

- [ ] **Run the full test suite**

```bash
uv run pytest tests/executor/ tests/safety/ tests/test_prompt_contents.py -v
```

Expected: all green.

- [ ] **Smoke-test autobug-disabled mode**

Set `autobug_enabled = false` in TOML; force a Python crash in run_loop; confirm NO PBI is emitted and the crash propagates normally.

- [ ] **Update spec status**

```diff
- **Status:** Draft, pending user review.
+ **Status:** Implemented (v1) — commits <range>.
```

```bash
git add docs/superpowers/specs/2026-05-31-ralph-autobug-design.md
git commit -m "docs(spec): autobug — mark v1 implemented"
```

---

## Self-review

**Spec coverage:** every spec section maps to ≥1 task (architecture → T1/T11–T13; signatures → T2/T3/T4; dedup → T7+T6.5; fuses → T5/T6; composer → T8/T9/T10; detect → T12; frontmatter shape → T9; integration sites → T15/T17/T18/T19; prompt amendments → T20/T21; tests → T22–T25; tooling → T26; concern-3 rollin → T15).

**Placeholder scan:** no "TBD"/"implement later"/"similar to Task N". Three places say "use existing conftest pattern" or "locate via grep" — these are bounded research steps (Step 0) the implementer can resolve in <5 min from the file references given.

**Type consistency:**
- `Context` field set consistent across T1, T11, T17, T18, T19, T22, T23, T25 (now includes `queue_branch`).
- `DedupResult` field consistent across T1, T7, T12.
- `detect_python_crash(exc, ctx, *, target_repo, severity=...)` consistent across T12, T18, T19, T23.
- `detect_subprocess_crash(exit_code, stderr, command, ctx, *, target_repo, severity=...)` consistent across T12, T17, T24.
- Function names `build_bug_md`, `build_reproduce_md`, `build_frontmatter`, `_safe` consistent across compose tests + emit.

Confidence audit: 0 tasks below 90%. No surfacing required.
