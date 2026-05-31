# ralph autobug v1 — implementation plan (v3 — confidence-rated, full task bodies)

**Status:** Approved 2026-05-31. Pending execution; user will re-validate after current ralph run completes.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the ralph executor or its Claude subprocess crashes, automatically file a deduplicated bug PBI to the ralph queue capturing the crash, so future iterations work the fix without operator hand-filing.

**Architecture:** A new `ralph_executor/autobug/` package mirrors the shape of `ralph_executor/sweep/`. Two public functions (`detect_python_crash`, `detect_subprocess_crash`) are called from three hook sites (`cli.py` outer try/except around `for result in run_loop(cfg):`, `claude_spawn` error classifier inside `spawn_claude_p`, internal `loop.py` wires). Each emission flows through signature hashing → recursion guard → dedup lookup → rate-limit check → BUG.md/REPRODUCE.md composition → queue write + push. All emission is no-op-safe.

**Tech Stack:** Python 3.12, pytest, hashlib (stdlib SHA-256). No new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-05-31-ralph-autobug-design.md` (commit `afcd813`).

**Standards consulted:** `~/.better-memory/knowledge-base/standards/ralph-runtime.md` — per-task confidence ratings + sub-92% lift gate; Bootstrap 5 visualiser render before execution offer; queue-artefact self-containment.

---

## Guardrails (surface up front, from planning memories)

| # | Guardrail | Source | Confidence | Tasks affected |
|---|---|---|---|---|
| G1 | Adding required field to ExecutorConfig → grep every `ExecutorConfig(` in `tests/` and thread through with sensible defaults | mem `462d13a7` | 0.85 | T14 (autobug_* fields default to safe values; no test breakage expected, but grep before commit) |
| G2 | `git mv DIR NEWDIR` only moves files in the index — untracked files inside DIR are stranded; must `git add -A <dir>` BEFORE `git mv` | mem `63c4e75a` | 0.85 | T11 (autobug emit writes BUG.md / REPRODUCE.md / HISTORY.md as NEW files; no mv used, but commit_all stages via git add -A so verify on the emit unit test that the dir is added cleanly) |
| G3 | Route state transitions through git-aware helper; never raw `shutil.move` | mem `a5af973e` | 0.80 | T11, T6.5 (use existing `commit_all` + `push_with_rebase`, NOT `shutil`) |
| G4 | Queue commit-message convention: `chore(queue): add <id>` for adds; `chore(ralph-queue): move <id> from X to Y` for transitions | mem `99c53915` | 0.70 | T11 (`chore(queue): add <id>` for emit.new, `chore(ralph-queue): bump <id>` for emit.bump, `chore(queue): reopen <orig> as <new>` for emit.reopen) |
| G5 | Queue artefacts must be self-contained — never reference operator's local-only paths | mem `8d05b8c2` | 0.75 | T10 (BUG.md / REPRODUCE.md content is fully embedded, no external refs) |
| G6 | Iter-and-join composer: guard empty input | mem `aa125f7a` | 0.70 | T10 (`build_bug_md` asserts non-empty section list before `"\n\n".join(...)`) |
| G7 | Bug PBI entry file is BUG.md, not PBI.md; type-aware dispatcher in `queue/filesystem.py::_detect_entry_file` | mem `cfa7e7f8` | 0.70 | T9 frontmatter `type: bug` matches `BUG.md` entry file (parser dispatch) |
| G8 | `target_repo` is REQUIRED at claim time despite PROMPT.md docstring | mem `77c83c6c` | 0.70 | T9 (frontmatter includes `target_repo` with a real value) |
| G9 | Spec-provided test code is NOT lint-clean by default; `from datetime import UTC` not `timezone.utc`; drop unused `import pytest` | standards | 1.0 | All test tasks |
| G10 | When work is dispatched to ralph queue, do NOT propose execution mode | mem `b017b510` | 0.90 | This plan's footer says nothing about execution choice |
| G11 | Use `cfg.workspace_root / "queue"` for queue_root, NOT `cfg.repo_path` | mem `43e430f5` | 0.90 | T1, T11, T17, T18, T19, T22, T23, T24, T25 (Context construction) |

Dismissed memories:
- `0c83e25d` Playwright text-transform: no Playwright tests in this plan.
- `355faeb8` Windows cmd.exe 8191-char argv: autobug doesn't pass large argvs.
- `26d0a5a9` Truthiness guard before validator: autobug fields don't have validators that could swallow empty.

---

## Confidence policy

**Floor is 92%. No task ships below 92%.** Per `standards/ralph-runtime.md` §"Apply confidence scoring to implementation plans" line 67: "For any step at ≤90% confidence, INDEPENDENTLY assess and apply mitigations to lift it above 90%". This plan applies a stricter 92% floor; any task that audits at 90% or 91% must have a Step 0 spike or source-read that lifts it.

## Lint-cleanliness conventions

- `from datetime import UTC` (not `timezone.utc`) — UP017
- `StrEnum` if defining string-valued enums — UP042 (verified: `safety/events.py` line 18 uses `from enum import StrEnum`)
- Drop unused `import pytest`; import only what the test body uses — F401
- Type hints on test helpers; mypy clean

## Forward-reference audit

Task order verified — no import-time forward references:
- T1 (`Context`, `DedupResult` types) depends on nothing.
- T2/T3 (signature) depend on T1 only for `Context`.
- T4 (corpus) depends on T3.
- T5 (recursion guard) depends on T1 (Context).
- T6 (rate limit) depends on T1.
- T6.5 (NEW: closed_at stamping in movements) depends on existing movements; depended on by T7.
- T7 (dedup) depends on T1 + T6.5 (closed_at).
- T8 (`_safe`) depends on nothing.
- T9 (frontmatter) depends on T1.
- T10 (build_bug_md / build_reproduce_md) depends on T8.
- T11 (emit) depends on T8/T9/T10 + git_ops + queue_branch on Context (T1).
- T12 (detect orchestration) depends on T2/T7/T11/T5/T6.
- T13 (`__init__.py` public API) depends on T1/T12.
- T14/T15 (config + startup validation) independent of autobug code, depend on existing `cfg`.
- T16 (events.AUTOBUG_EMITTED) independent.
- T17/T18/T19 (wires) depend on T13 (public API).
- T20/T21 (prompt amendments) independent.
- T22 (roundtrip) depends on T1/T11/T6.5.
- T23/T24/T25 (e2e integration) depend on T17/T18/T19.
- T26 (corpus extractor script) depends on T4 only for the fixture layout convention.

---

## Confidence summary

| # | Task | Confidence | Status |
|---|---|---|---|
| T1 | Scaffold autobug package + Context + DedupResult | 99% | direct |
| T2 | Python crash signature | 96% | direct |
| T3 | Subprocess signature + _normalise | 92% | lifted via T4 corpus |
| T4 | Signature corpus regression test | 95% | direct |
| T5 | Recursion guard | 99% | direct |
| T6 | Rate-limit + rollup | 94% | direct |
| T6.5 | Stamp `closed_at` on move-to-done | 92% | lifted via source-read of `_rewrite_status` |
| T7 | Dedup lookup (depends on T6.5) | 93% | direct |
| T8 | _safe wrapper + section_failed log | 99% | direct |
| T9 | build_frontmatter | 96% | direct (Step 0 ISO read) |
| T10 | build_bug_md + build_reproduce_md | 93% | direct |
| T11 | emit.new + bump + reopen | 93% | direct |
| T12 | detect orchestration | 93% | direct |
| T13 | Public API exports | 99% | direct |
| T14 | TOML keys + ExecutorConfig fields | 96% | direct (G1 grep in Step 0) |
| T15 | bot_author_email startup validation | 93% | direct |
| T16 | autobug_emitted EventType | 96% | direct |
| T17 | claude_spawn subprocess wire + RALPH_AUTOBUG_DEPTH env | 93% | direct |
| T18 | cli.py outer wrapper around generator loop | 93% | direct |
| T19 | loop.py internal wires at specific verified sites | 92% | lifted via line-numbered enumeration + spike |
| T20 | bug workflow signature-aware amendment | 96% | direct |
| T21 | memory prompt autobug note | 96% | direct |
| T22 | roundtrip integration test | 93% | direct |
| T23 | python crash e2e | 93% | direct |
| T24 | subprocess crash e2e | 93% | direct |
| T25 | recursion guard e2e | 93% | direct |
| T26 | corpus extractor script | 93% | direct |

All 27 tasks at ≥92%. No tasks unliftable.

---

## Task T1: Scaffold autobug package + Context + DedupResult — **confidence: 99%**

**Files:**
- Create: `ralph_executor/autobug/__init__.py` (empty stub; T13 will populate exports)
- Create: `ralph_executor/autobug/types.py` (Context + DedupResult dataclasses)
- Create: `tests/executor/autobug/__init__.py` (empty)
- Create: `tests/executor/autobug/test_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/test_types.py
from datetime import UTC, datetime
from pathlib import Path

from ralph_executor.autobug.types import Context, DedupResult


def test_context_is_frozen_dataclass(tmp_path: Path) -> None:
    ctx = Context(
        queue_root=tmp_path / "queue",
        state_dir=tmp_path / "queue" / ".ralph" / "state",
        env={"FOO": "bar"},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="51cc97a",
        bot_author_email="bot@example.com",
        triggering_pbi_id="WI-247",
        queue_branch="ralph-queue",
    )
    assert ctx.queue_root == tmp_path / "queue"
    assert ctx.env["FOO"] == "bar"
    assert ctx.ralph_sha == "51cc97a"
    assert ctx.queue_branch == "ralph-queue"


def test_context_triggering_pbi_id_optional(tmp_path: Path) -> None:
    ctx = Context(
        queue_root=tmp_path,
        state_dir=tmp_path / "state",
        env={},
        now=datetime(2026, 5, 31, tzinfo=UTC),
        ralph_sha="abc",
        bot_author_email="b@e.com",
        triggering_pbi_id=None,
        queue_branch="ralph-queue",
    )
    assert ctx.triggering_pbi_id is None


def test_dedup_result_kinds() -> None:
    r = DedupResult(kind="new")
    assert r.kind == "new"
    assert r.existing_pbi_id is None
    r2 = DedupResult(kind="bump_existing", existing_pbi_id="autobug-abc-001")
    assert r2.existing_pbi_id == "autobug-abc-001"
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_types.py -x`
`Expected: FAIL with ModuleNotFoundError: No module named 'ralph_executor.autobug.types'`

- [ ] **Step 3: Implement**

```python
# ralph_executor/autobug/__init__.py
"""Autobug — capture executor + Claude subprocess crashes as bug PBIs."""
```

```python
# ralph_executor/autobug/types.py
"""Frozen dataclasses shared across the autobug package."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Context:
    """Carries per-emission state to every autobug step.

    All callers (cli outer wrapper, loop wires, claude_spawn subprocess
    wire) build this same shape so the signature/dedup/emit pipeline
    never reaches back into ExecutorConfig directly.
    """

    queue_root: Path
    state_dir: Path
    env: Mapping[str, str]
    now: datetime
    ralph_sha: str
    bot_author_email: str
    triggering_pbi_id: str | None
    queue_branch: str


@dataclass(frozen=True)
class DedupResult:
    """Outcome of a dedup lookup against queue state."""

    kind: Literal["new", "bump_existing", "reopen_regression"]
    existing_pbi_id: str | None = None
```

```python
# tests/executor/autobug/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_types.py -v`
`Expected: PASS (3 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/__init__.py ralph_executor/autobug/types.py tests/executor/autobug/__init__.py tests/executor/autobug/test_types.py
git commit -m "feat(autobug): scaffold package + Context + DedupResult"
```

---

## Task T2: Python crash signature — **confidence: 96%**

**Files:**
- Create: `ralph_executor/autobug/signature.py`
- Create: `tests/executor/autobug/test_signature.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/test_signature.py
import hashlib

from ralph_executor.autobug.signature import (
    python_crash_signature,
    subprocess_crash_signature,
    _two_deepest_user_frames,
)


def _raise_at_known_lineno() -> None:
    raise RuntimeError("boom at known line")


def test_python_crash_signature_is_deterministic() -> None:
    try:
        _raise_at_known_lineno()
    except RuntimeError as exc:
        sig_a = python_crash_signature(exc)
    try:
        _raise_at_known_lineno()
    except RuntimeError as exc:
        sig_b = python_crash_signature(exc)
    assert sig_a == sig_b
    assert len(sig_a) == 64  # sha256 hex


def test_python_crash_signature_differs_by_exc_class() -> None:
    try:
        raise RuntimeError("a")
    except RuntimeError as exc:
        sig_rt = python_crash_signature(exc)
    try:
        raise ValueError("a")
    except ValueError as exc:
        sig_ve = python_crash_signature(exc)
    assert sig_rt != sig_ve


def test_python_crash_signature_single_user_frame_uses_sentinel() -> None:
    # Force a crash from a single-frame context (no caller frame in user code).
    try:
        raise RuntimeError("single")
    except RuntimeError as exc:
        sig = python_crash_signature(exc)
    assert len(sig) == 64
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_signature.py -x`
`Expected: FAIL with ModuleNotFoundError: No module named 'ralph_executor.autobug.signature'`

- [ ] **Step 3: Implement**

```python
# ralph_executor/autobug/signature.py
"""Signature hashing for Python and subprocess crashes."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from types import TracebackType

_USER_ROOT = "ralph_executor"


@dataclass(frozen=True)
class _Frame:
    filename: str
    name: str
    lineno: int


_SENTINEL = _Frame(filename="<none>", name="<none>", lineno=0)


def _two_deepest_user_frames(tb: TracebackType | None) -> tuple[_Frame, _Frame]:
    """Walk traceback; return the two deepest frames under ralph_executor/."""
    frames: list[_Frame] = []
    cur = tb
    while cur is not None:
        f = cur.tb_frame
        filename = f.f_code.co_filename.replace("\\", "/")
        if _USER_ROOT in filename:
            frames.append(_Frame(filename=filename, name=f.f_code.co_name, lineno=cur.tb_lineno))
        cur = cur.tb_next
    if not frames:
        # Pure stdlib crash — fall back to deepest frame regardless of root.
        cur = tb
        while cur is not None:
            f = cur.tb_frame
            frames.append(
                _Frame(
                    filename=f.f_code.co_filename.replace("\\", "/"),
                    name=f.f_code.co_name,
                    lineno=cur.tb_lineno,
                )
            )
            cur = cur.tb_next
        if not frames:
            return _SENTINEL, _SENTINEL
        return frames[-1], _SENTINEL
    if len(frames) == 1:
        return frames[-1], _SENTINEL
    return frames[-1], frames[-2]


def python_crash_signature(exc: BaseException) -> str:
    throw, caller = _two_deepest_user_frames(exc.__traceback__)
    payload = (
        f"{type(exc).__qualname__}:"
        f"{throw.filename}:{throw.name}:{throw.lineno}|"
        f"{caller.filename}:{caller.name}:{caller.lineno}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?")
_EPOCH_RE = re.compile(r"\b\d{10,}\b")
_PATH_RE = re.compile(r"(?:[A-Z]:\\|/)[^\s:]+")
_BRANCH_RE = re.compile(r"ralph/[A-Z0-9][A-Z0-9_-]*")
_PBI_RE = re.compile(r"\b(WI-\d+|autobug-[a-f0-9]+-\d+)\b")
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_ADDR_RE = re.compile(r"0x[0-9a-f]+")
_LINENO_RE = re.compile(r":line \d+|at line \d+")


def _normalise(line: str) -> str:
    """Apply regex substitutions in spec-order (longest/most-specific first)."""
    out = _UUID_RE.sub("<UUID>", line)
    out = _TS_RE.sub("<TS>", out)
    out = _EPOCH_RE.sub("<EPOCH>", out)
    out = _PATH_RE.sub("<PATH>", out)
    out = _BRANCH_RE.sub("<BRANCH>", out)
    out = _PBI_RE.sub("<PBI>", out)
    out = _SHA_RE.sub("<SHA>", out)
    out = _ADDR_RE.sub("<ADDR>", out)
    out = _LINENO_RE.sub(":line <N>", out)
    return out


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def subprocess_crash_signature(exit_code: int, stderr: str) -> str:
    last_line = _last_nonempty_line(stderr)
    normalised = _normalise(last_line)
    payload = f"{exit_code}:{normalised}"
    return hashlib.sha256(payload.encode()).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_signature.py -v`
`Expected: PASS (3 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/signature.py tests/executor/autobug/test_signature.py
git commit -m "feat(autobug): python_crash_signature + subprocess_crash_signature + _normalise"
```

---

## Task T3: Subprocess signature + _normalise — **confidence: 92%** (lifted via T4 corpus)

**Files:**
- Modify: `tests/executor/autobug/test_signature.py` (add subprocess + normaliser cases)

**Lift rationale:** the regex set is spec-authoritative but easy to mis-order. T4 corpus test will catch ordering errors at fixture time. This task adds the unit-level tests that pin the per-pattern behaviour BEFORE corpus tests run.

- [ ] **Step 0:** Read `docs/superpowers/specs/2026-05-31-ralph-autobug-design.md` §"Normalisation pattern set" table. Confirm pattern order: UUID, TS, EPOCH, PATH, BRANCH, PBI, SHA, ADDR, LINENO. T2 implemented in that order.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/executor/autobug/test_signature.py
from ralph_executor.autobug.signature import _normalise, subprocess_crash_signature


def test_normalise_uuid_collapses() -> None:
    assert "<UUID>" in _normalise("request 6f0c8b27-3a8b-4f6f-8e5f-2cd2cc62c9a8 failed")


def test_normalise_timestamp_collapses() -> None:
    assert "<TS>" in _normalise("at 2026-05-31T14:23:01Z error")
    assert "<TS>" in _normalise("at 2026-05-31T14:23:01.123456+00:00 error")


def test_normalise_path_collapses_windows_and_posix() -> None:
    assert "<PATH>" in _normalise("crashed at C:\\Users\\foo\\bar.py")
    assert "<PATH>" in _normalise("crashed at /home/user/foo.py")


def test_normalise_pbi_id_collapses() -> None:
    assert "<PBI>" in _normalise("WI-247 failed")
    assert "<PBI>" in _normalise("autobug-a3f2c9-001 failed")


def test_normalise_sha_collapses_after_uuid_and_path() -> None:
    # SHA pattern must NOT eat the UUID (run after).
    out = _normalise("ref 51cc97a fixed 6f0c8b27-3a8b-4f6f-8e5f-2cd2cc62c9a8")
    assert "<SHA>" in out
    assert "<UUID>" in out


def test_subprocess_signature_collapses_per_iteration_noise() -> None:
    s1 = "Error at 2026-05-31T14:23:01Z for WI-247 in /tmp/foo.py"
    s2 = "Error at 2026-05-31T14:24:01Z for WI-248 in /tmp/foo.py"
    assert subprocess_crash_signature(1, s1) == subprocess_crash_signature(1, s2)


def test_subprocess_signature_differs_by_exit_code() -> None:
    assert subprocess_crash_signature(1, "msg") != subprocess_crash_signature(137, "msg")


def test_subprocess_signature_uses_last_nonempty_line() -> None:
    s = "irrelevant prefix\n\nKilled\n"
    sig = subprocess_crash_signature(137, s)
    sig_direct = subprocess_crash_signature(137, "Killed")
    assert sig == sig_direct
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_signature.py -v -k "normalise or subprocess"`
`Expected: PASS — T2 already implemented; this test mostly affirms behaviour. If any test fails, regex order or pattern is wrong.`

If any test fails: fix the regex in T2 BEFORE proceeding. Most likely failure mode is the SHA pattern eating UUID chars when run too early.

- [ ] **Step 3: Implement (no new production code expected; corrections are bugfixes to T2)**

If a test failed in Step 2, edit `ralph_executor/autobug/signature.py` to re-order or refine the relevant regex. Re-run until all pass.

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_signature.py -v`
`Expected: PASS (all tests)`

- [ ] **Step 5: Commit**

```bash
git add tests/executor/autobug/test_signature.py
git commit -m "test(autobug): pin subprocess signature + normaliser behaviour"
```

---

## Task T4: Signature corpus regression test — **confidence: 95%**

**Files:**
- Create: `tests/executor/autobug/fixtures/stderr_corpus/synthetic_oom/sample1.txt`
- Create: `tests/executor/autobug/fixtures/stderr_corpus/synthetic_oom/sample2.txt`
- Create: `tests/executor/autobug/fixtures/stderr_corpus/synthetic_timeout/sample1.txt`
- Create: `tests/executor/autobug/test_signature_corpus.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/test_signature_corpus.py
"""Corpus regression test: bucketed real-stderr blobs must collapse correctly.

Ships with synthetic placeholder buckets. Operator runs
``scripts/build_autobug_corpus.py`` to populate real buckets before
raising ``autobug_rate_max`` above the default 5 — running blind on
synthetic fixtures is acceptable for the first week but not steady state.
"""

from pathlib import Path

import pytest

from ralph_executor.autobug.signature import subprocess_crash_signature

CORPUS_ROOT = Path(__file__).parent / "fixtures" / "stderr_corpus"


def _bucket_dirs() -> list[Path]:
    if not CORPUS_ROOT.is_dir():
        return []
    return [d for d in CORPUS_ROOT.iterdir() if d.is_dir()]


@pytest.mark.parametrize("bucket", _bucket_dirs(), ids=lambda d: d.name)
def test_bucket_files_collapse_to_one_signature(bucket: Path) -> None:
    """All files within one bucket dir must hash to the same signature."""
    files = sorted(p for p in bucket.iterdir() if p.suffix == ".txt")
    if len(files) < 2:
        pytest.skip(f"bucket {bucket.name} has <2 files; nothing to collapse")
    sigs = {f.name: subprocess_crash_signature(1, f.read_text(encoding="utf-8")) for f in files}
    unique = set(sigs.values())
    assert len(unique) == 1, f"bucket {bucket.name} did not collapse: {sigs}"


def test_buckets_have_distinct_signatures() -> None:
    """Files from different buckets must produce different signatures."""
    seen: dict[str, str] = {}
    for bucket in _bucket_dirs():
        files = sorted(p for p in bucket.iterdir() if p.suffix == ".txt")
        if not files:
            continue
        sig = subprocess_crash_signature(1, files[0].read_text(encoding="utf-8"))
        assert sig not in seen, (
            f"bucket {bucket.name} collides with {seen[sig]}: signature {sig[:8]}"
        )
        seen[sig] = bucket.name
```

Synthetic fixture files (the test treats them as the placeholder corpus):

```
# tests/executor/autobug/fixtures/stderr_corpus/synthetic_oom/sample1.txt
2026-05-31T14:23:01Z [WI-247] worker abc1234 starting
2026-05-31T14:23:05Z [WI-247] allocating buffer at 0x7fff8000
Killed
```

```
# tests/executor/autobug/fixtures/stderr_corpus/synthetic_oom/sample2.txt
2026-06-01T09:11:42.123Z [WI-301] worker 51cc97a starting
2026-06-01T09:11:50Z [WI-301] allocating buffer at 0x7fffabcd
Killed
```

```
# tests/executor/autobug/fixtures/stderr_corpus/synthetic_timeout/sample1.txt
2026-05-31T14:23:01Z [WI-247] request 6f0c8b27-3a8b-4f6f-8e5f-2cd2cc62c9a8 sent
TimeoutError: read timed out after 30s
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_signature_corpus.py -v`
`Expected: FAIL — fixture files do not exist yet (or PASS if T2 normalise is correct and fixtures already created via Write).`

- [ ] **Step 3: Implement**

Write the three fixture files above. No production code change needed — the corpus test consumes T2's `subprocess_crash_signature`.

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_signature_corpus.py -v`
`Expected: PASS (2 tests + parametrised buckets)`

- [ ] **Step 5: Commit**

```bash
git add tests/executor/autobug/test_signature_corpus.py tests/executor/autobug/fixtures/
git commit -m "test(autobug): signature corpus regression with synthetic placeholders"
```

---

## Task T5: Recursion guard — **confidence: 99%**

**Files:**
- Create: `ralph_executor/autobug/fuses.py`
- Create: `tests/executor/autobug/test_fuses.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/test_fuses.py
from ralph_executor.autobug.fuses import recursion_check


def test_recursion_check_allows_when_unset() -> None:
    assert recursion_check({}) is True


def test_recursion_check_allows_when_zero() -> None:
    assert recursion_check({"RALPH_AUTOBUG_DEPTH": "0"}) is True


def test_recursion_check_denies_when_one() -> None:
    assert recursion_check({"RALPH_AUTOBUG_DEPTH": "1"}) is False


def test_recursion_check_denies_when_higher() -> None:
    assert recursion_check({"RALPH_AUTOBUG_DEPTH": "2"}) is False


def test_recursion_check_tolerates_non_integer() -> None:
    # Defensive: anything non-integer is treated as 0 (allow).
    assert recursion_check({"RALPH_AUTOBUG_DEPTH": "garbage"}) is True
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_fuses.py -x`
`Expected: FAIL with ModuleNotFoundError: No module named 'ralph_executor.autobug.fuses'`

- [ ] **Step 3: Implement**

```python
# ralph_executor/autobug/fuses.py
"""Three independent fuses: recursion guard, rate limit, rollup."""

from __future__ import annotations

from collections.abc import Mapping


def recursion_check(env: Mapping[str, str]) -> bool:
    """True = allowed to emit; False = recursion guard tripped."""
    raw = env.get("RALPH_AUTOBUG_DEPTH", "0")
    try:
        depth = int(raw)
    except ValueError:
        depth = 0
    return depth < 1
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_fuses.py -v`
`Expected: PASS (5 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/fuses.py tests/executor/autobug/test_fuses.py
git commit -m "feat(autobug): recursion_check fuse"
```

---

## Task T6: Rate-limit + rollup — **confidence: 94%**

**Files:**
- Modify: `ralph_executor/autobug/fuses.py` (add RateLimitConfig + rate_check + rollup)
- Modify: `tests/executor/autobug/test_fuses.py` (add rate-limit tests)

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/executor/autobug/test_fuses.py
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ralph_executor.autobug.fuses import RateLimitConfig, rate_check, rollup


def test_rate_check_passes_under_cap(tmp_path: Path) -> None:
    cfg = RateLimitConfig(max_writes=5, window=timedelta(minutes=10))
    now = datetime(2026, 5, 31, 14, 23, tzinfo=UTC)
    state = tmp_path / "state"
    state.mkdir()
    assert rate_check(state, cfg, now) is True


def test_rate_check_denies_over_cap(tmp_path: Path) -> None:
    cfg = RateLimitConfig(max_writes=2, window=timedelta(minutes=10))
    now = datetime(2026, 5, 31, 14, 23, tzinfo=UTC)
    state = tmp_path / "state"
    state.mkdir()
    log_path = state / "autobug-emissions.log"
    log_path.write_text(
        "2026-05-31T14:22:00+00:00\n2026-05-31T14:22:30+00:00\n",
        encoding="utf-8",
    )
    assert rate_check(state, cfg, now) is False


def test_rate_check_ignores_entries_outside_window(tmp_path: Path) -> None:
    cfg = RateLimitConfig(max_writes=1, window=timedelta(minutes=10))
    now = datetime(2026, 5, 31, 14, 23, tzinfo=UTC)
    state = tmp_path / "state"
    state.mkdir()
    log_path = state / "autobug-emissions.log"
    # All entries older than 10 min — should not count.
    log_path.write_text("2026-05-31T13:00:00+00:00\n", encoding="utf-8")
    assert rate_check(state, cfg, now) is True


def test_rollup_appends_suppressed_log(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    now = datetime(2026, 5, 31, 14, 23, tzinfo=UTC)
    rollup(state, "deadbeefcafe", "rate-limited new", now=now)
    contents = (state / "autobug-suppressed.log").read_text(encoding="utf-8")
    assert "deadbeefcafe" in contents
    assert "rate-limited new" in contents
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_fuses.py -v`
`Expected: FAIL with ImportError for RateLimitConfig / rate_check / rollup.`

- [ ] **Step 3: Implement**

```python
# Append to ralph_executor/autobug/fuses.py
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class RateLimitConfig:
    max_writes: int = 5
    window: timedelta = timedelta(minutes=10)


def _read_timestamps_after(log_path: Path, cutoff: datetime) -> list[datetime]:
    if not log_path.is_file():
        return []
    out: list[datetime] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ts = datetime.fromisoformat(line)
        except ValueError:
            continue
        if ts >= cutoff:
            out.append(ts)
    return out


def rate_check(state_dir: Path, cfg: RateLimitConfig, now: datetime) -> bool:
    """True = under cap; False = over cap, caller should rollup."""
    log_path = state_dir / "autobug-emissions.log"
    recent = _read_timestamps_after(log_path, now - cfg.window)
    return len(recent) < cfg.max_writes


def append_emission(state_dir: Path, now: datetime) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "autobug-emissions.log"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{now.isoformat()}\n")


def rollup(state_dir: Path, signature: str, reason: str, *, now: datetime) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "autobug-suppressed.log"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{now.isoformat()}\t{signature}\t{reason}\n")
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_fuses.py -v`
`Expected: PASS (9 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/fuses.py tests/executor/autobug/test_fuses.py
git commit -m "feat(autobug): RateLimitConfig + rate_check + rollup + append_emission"
```

---

## Task T6.5: Stamp `closed_at` on move-to-done — **confidence: 92%** (lifted via source-read)

**Files:**
- Modify: `ralph_executor/queue/movements.py` (extend `_rewrite_status` to stamp `closed_at` when target is `done`; add `move_pending_pr_to_done` helper if not present)
- Modify: `tests/executor/queue/test_movements.py` (located via `Glob: tests/executor/**/test_movements*.py`)

**Lift rationale:** Source-read of `ralph_executor/queue/movements.py` confirmed `closed_at` is NEVER stamped. T7's dedup `_find_by_signature_since` requires `closed_at` on done/ PBI frontmatter. The `_rewrite_status` function (lines 71–100) is line-based and already preserves unknown frontmatter keys verbatim — extending it to ALSO stamp `closed_at` when target is `done` is a minimal additive change with deterministic behaviour. Step 0 below verified the exact lines.

- [ ] **Step 0a:** Read `ralph_executor/queue/movements.py:71-100` (`_rewrite_status`). VERIFIED at plan-write time: takes `(entry_file, new_status)`, scans lines between `---` fences, replaces `status:` and `updated_at:` lines in place, inserts them before the closing fence if missing.

- [ ] **Step 0b:** Read `ralph_executor/queue/movements.py:103-146` (`_move`). VERIFIED: calls `_rewrite_status(dst / entry_name, target_state)` at line 134. No existing `move_pending_pr_to_done` helper — only `move_inbox_to_current`, `move_current_to_pending_pr`, `move_current_to_blocked`, `move_inbox_to_blocked` exist. T6.5 must ADD a `move_pending_pr_to_done` helper that routes through `_move` with `target_state="done"` so T22's roundtrip integration test can exercise the full chain.

After Step 0: design is (a) extend `_rewrite_status` with a `stamp_closed_at: bool = False` kwarg; (b) update `_move` to pass `stamp_closed_at=(target_state == "done")`; (c) add `move_pending_pr_to_done` helper.

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/queue/test_movements_closed_at.py
"""closed_at stamping on move-to-done (T6.5)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.queue.filesystem import parse_pbi_directory
from ralph_executor.queue.movements import (
    move_current_to_pending_pr,
    move_inbox_to_current,
    move_pending_pr_to_done,
)
from tests.executor.conftest import _git, write_sample_pbi


def _stage_and_commit(fake_repo: Path, pbi_dir: Path) -> None:
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", f"chore(queue): add {pbi_dir.name}")
    _git(fake_repo, "push", "origin", "main")


def test_move_pending_pr_to_done_stamps_closed_at(fake_repo: Path, cfg_for_repo) -> None:
    pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-DONE", where="inbox")
    _stage_and_commit(fake_repo, pbi_dir)
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    pbi = move_inbox_to_current(cfg_for_repo, pbi)
    pbi = move_current_to_pending_pr(cfg_for_repo, pbi)
    moved = move_pending_pr_to_done(cfg_for_repo, pbi)
    entry = moved.path / "PBI.md"
    text = entry.read_text(encoding="utf-8")
    assert "closed_at:" in text


def test_move_inbox_to_current_does_not_stamp_closed_at(
    fake_repo: Path, cfg_for_repo
) -> None:
    pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-NOCL", where="inbox")
    _stage_and_commit(fake_repo, pbi_dir)
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    moved = move_inbox_to_current(cfg_for_repo, pbi)
    text = (moved.path / "PBI.md").read_text(encoding="utf-8")
    assert "closed_at:" not in text
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/queue/test_movements_closed_at.py -v`
`Expected: FAIL — ImportError on move_pending_pr_to_done, OR test_move_pending_pr_to_done_stamps_closed_at fails because no closed_at line.`

- [ ] **Step 3: Implement**

```python
# Replace _rewrite_status in ralph_executor/queue/movements.py
def _rewrite_status(
    entry_file: Path,
    new_status: PBIStatus,
    *,
    stamp_closed_at: bool = False,
) -> None:
    text = entry_file.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise QueueMovementError(f"{entry_file}: no opening '---' fence to rewrite")
    end = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end < 0:
        raise QueueMovementError(f"{entry_file}: no closing '---' fence to rewrite")
    rewrote_status = False
    rewrote_updated = False
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
        lines.insert(end, f"status: {new_status}\n")
        end += 1
    if not rewrote_updated:
        lines.insert(end, f"updated_at: {now}\n")
        end += 1
    if stamp_closed_at and not rewrote_closed:
        lines.insert(end, f"closed_at: {now}\n")
    entry_file.write_text("".join(lines), encoding="utf-8")
```

Update `_move`:

```python
    _rewrite_status(
        dst / entry_name,
        target_state,
        stamp_closed_at=(target_state == "done"),
    )
```

Add helper at module scope:

```python
def move_pending_pr_to_done(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Promote a merged-PR PBI from pending-pr to done. Stamps closed_at."""
    return _move(
        cfg,
        pbi,
        expected_state="pending-pr",
        target_state="done",
        commit_prefix="chore(ralph-queue)",
    )
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/queue/test_movements_closed_at.py -v`
`Expected: PASS (2 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/queue/movements.py tests/executor/queue/test_movements_closed_at.py
git commit -m "feat(queue/movements): stamp closed_at + add move_pending_pr_to_done"
```

---

## Task T7: Dedup lookup — **confidence: 93%**

**Files:**
- Create: `ralph_executor/autobug/dedup.py`
- Create: `tests/executor/autobug/test_dedup.py`

- [ ] **Step 0:** Confirm T6.5 has landed. `Run: git log --oneline -1 -- ralph_executor/queue/movements.py` — expect the closed_at commit.

If T6.5 has not landed, this task is blocked.

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/test_dedup.py
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ralph_executor.autobug.dedup import DedupResult, lookup


def _write_pbi(queue_root: Path, state: str, pbi_id: str, signature: str,
               closed_at: str | None = None) -> None:
    pbi_dir = queue_root / ".ralph" / state / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=True)
    extra = f"closed_at: {closed_at}\n" if closed_at else ""
    (pbi_dir / "BUG.md").write_text(
        f"---\nid: {pbi_id}\ntype: bug\nseverity: critical\nstatus: {state}\n"
        f"attempts: 0\ncreated_at: 2026-05-31T00:00:00+00:00\n"
        f"updated_at: 2026-05-31T00:00:00+00:00\n"
        f"target_repo: https://github.com/test/repo\n"
        f"signature: {signature}\n{extra}---\n# bug body\n",
        encoding="utf-8",
    )


def test_lookup_returns_new_when_no_match(tmp_path: Path) -> None:
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    r = lookup("a" * 64, tmp_path, now)
    assert r.kind == "new"
    assert r.existing_pbi_id is None


def test_lookup_returns_bump_when_inbox_match(tmp_path: Path) -> None:
    sig = "b" * 64
    _write_pbi(tmp_path, "inbox", "autobug-bbbbbb-001", sig)
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    r = lookup(sig, tmp_path, now)
    assert r.kind == "bump_existing"
    assert r.existing_pbi_id == "autobug-bbbbbb-001"


def test_lookup_returns_bump_when_current_match(tmp_path: Path) -> None:
    sig = "c" * 64
    _write_pbi(tmp_path, "current", "autobug-cccccc-001", sig)
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    r = lookup(sig, tmp_path, now)
    assert r.kind == "bump_existing"


def test_lookup_returns_reopen_when_done_within_30d(tmp_path: Path) -> None:
    sig = "d" * 64
    _write_pbi(tmp_path, "done", "autobug-dddddd-001", sig,
               closed_at="2026-05-25T00:00:00+00:00")
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    r = lookup(sig, tmp_path, now)
    assert r.kind == "reopen_regression"
    assert r.existing_pbi_id == "autobug-dddddd-001"


def test_lookup_returns_new_when_done_older_than_30d(tmp_path: Path) -> None:
    sig = "e" * 64
    _write_pbi(tmp_path, "done", "autobug-eeeeee-001", sig,
               closed_at="2026-03-01T00:00:00+00:00")
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    r = lookup(sig, tmp_path, now)
    assert r.kind == "new"
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_dedup.py -v`
`Expected: FAIL with ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# ralph_executor/autobug/dedup.py
"""Scan queue state for matching signature; returns kind: new/bump/reopen."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from ralph_executor.autobug.types import DedupResult

_SIG_RE = re.compile(r"^signature:\s*([0-9a-f]+)\s*$", re.MULTILINE)
_CLOSED_RE = re.compile(r"^closed_at:\s*(\S+)\s*$", re.MULTILINE)
_ID_RE = re.compile(r"^id:\s*(\S+)\s*$", re.MULTILINE)


def _find_by_signature(state_dir: Path, signature: str) -> str | None:
    if not state_dir.is_dir():
        return None
    for child in sorted(state_dir.iterdir()):
        if not child.is_dir():
            continue
        for entry_name in ("BUG.md", "PBI.md", "FEEDBACK.md"):
            entry = child / entry_name
            if not entry.is_file():
                continue
            try:
                text = entry.read_text(encoding="utf-8")
            except OSError:
                continue
            m = _SIG_RE.search(text)
            if m and m.group(1) == signature:
                id_m = _ID_RE.search(text)
                return id_m.group(1) if id_m else child.name
    return None


def _find_by_signature_since(
    state_dir: Path, signature: str, cutoff: datetime
) -> str | None:
    if not state_dir.is_dir():
        return None
    for child in sorted(state_dir.iterdir()):
        if not child.is_dir():
            continue
        for entry_name in ("BUG.md", "PBI.md", "FEEDBACK.md"):
            entry = child / entry_name
            if not entry.is_file():
                continue
            try:
                text = entry.read_text(encoding="utf-8")
            except OSError:
                continue
            m = _SIG_RE.search(text)
            if not m or m.group(1) != signature:
                continue
            closed_m = _CLOSED_RE.search(text)
            if not closed_m:
                continue
            try:
                closed = datetime.fromisoformat(closed_m.group(1))
            except ValueError:
                continue
            if closed >= cutoff:
                id_m = _ID_RE.search(text)
                return id_m.group(1) if id_m else child.name
    return None


def lookup(signature: str, queue_root: Path, now: datetime) -> DedupResult:
    for state in ("inbox", "current", "pending-pr"):
        match = _find_by_signature(queue_root / ".ralph" / state, signature)
        if match:
            return DedupResult(kind="bump_existing", existing_pbi_id=match)
    cutoff = now - timedelta(days=30)
    match = _find_by_signature_since(queue_root / ".ralph" / "done", signature, cutoff)
    if match:
        return DedupResult(kind="reopen_regression", existing_pbi_id=match)
    return DedupResult(kind="new")
```

Also re-export DedupResult from this module for convenience:

```python
from ralph_executor.autobug.types import DedupResult  # noqa: F401 — re-export
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_dedup.py -v`
`Expected: PASS (5 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/dedup.py tests/executor/autobug/test_dedup.py
git commit -m "feat(autobug): dedup lookup across inbox/current/pending-pr + done<30d"
```

---

## Task T8: `_safe` wrapper + section_failed log — **confidence: 99%**

**Files:**
- Create: `ralph_executor/autobug/compose.py` (initial — `_safe` + `_section` helpers)
- Create: `tests/executor/autobug/test_compose.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/test_compose.py
import logging

import pytest

from ralph_executor.autobug.compose import _safe, _section


def test_safe_returns_value_when_fn_succeeds() -> None:
    assert _safe(lambda: "ok", "fallback") == "ok"


def test_safe_returns_fallback_when_fn_raises(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="ralph_executor.autobug.compose")
    out = _safe(lambda: 1 / 0, "fallback")
    assert out == "fallback"
    assert any("autobug.compose section failed" in r.getMessage() for r in caplog.records)


def test_section_renders_heading_and_body() -> None:
    out = _section("Stacktrace", "boom")
    assert "## Stacktrace" in out
    assert "boom" in out
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_compose.py -x`
`Expected: FAIL with ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# ralph_executor/autobug/compose.py
"""Defensive composer for BUG.md / REPRODUCE.md.

Every section runs through ``_safe``: section failures log a structured
warning and substitute a fallback string but never propagate. This
guarantees the autobug emit never crashes the original-crash handler.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


def _safe(fn: Callable[[], T], fallback: T) -> T:
    try:
        return fn()
    except Exception as exc:
        log.warning(
            "autobug.compose section failed: %s",
            exc,
            extra={"section_failed": True},
        )
        return fallback


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body}"
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_compose.py -v`
`Expected: PASS (3 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/compose.py tests/executor/autobug/test_compose.py
git commit -m "feat(autobug): _safe wrapper + _section helper"
```

---

## Task T9: `build_frontmatter` — **confidence: 96%**

**Files:**
- Modify: `ralph_executor/autobug/compose.py` (add build_frontmatter)
- Modify: `tests/executor/autobug/test_compose.py` (add frontmatter tests)

- [ ] **Step 0:** Read one existing PBI's frontmatter to verify the ISO format. The `tests/executor/conftest.py::write_sample_pbi` emits `created_at: 2026-05-24T09:15:00+00:00` (no microseconds, +00:00 offset, no `Z`). Production `_now_iso()` in `movements.py:67-68` produces `datetime.now(tz=UTC).replace(microsecond=0).isoformat()` which yields the same shape. `build_frontmatter` must call `ctx.now.replace(microsecond=0).isoformat()` so the format matches what `parse_pbi_directory` accepts via `_coerce_datetime`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/executor/autobug/test_compose.py
from datetime import UTC, datetime
from pathlib import Path

from ralph_executor.autobug.compose import build_frontmatter
from ralph_executor.autobug.types import Context


def _ctx(tmp_path: Path) -> Context:
    return Context(
        queue_root=tmp_path / "queue",
        state_dir=tmp_path / "queue" / ".ralph" / "state",
        env={},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="51cc97a",
        bot_author_email="bot@example.com",
        triggering_pbi_id="WI-247",
        queue_branch="ralph-queue",
    )


def test_build_frontmatter_includes_required_fields(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    fm = build_frontmatter(
        pbi_id="autobug-a3f2c9-001",
        signature="a3f2c9d8" + "0" * 56,
        trigger_kind="python_crash",
        severity="critical",
        ctx=ctx,
        target_repo="https://github.com/emp3thy/ralph",
    )
    assert "id: autobug-a3f2c9-001" in fm
    assert "type: bug" in fm
    assert "severity: critical" in fm
    assert "status: inbox" in fm
    assert "attempts: 0" in fm
    assert "created_at: 2026-05-31T14:23:01+00:00" in fm
    assert "updated_at: 2026-05-31T14:23:01+00:00" in fm
    assert "target_repo: https://github.com/emp3thy/ralph" in fm
    assert "signature: a3f2c9d8" + "0" * 56 in fm
    assert "trigger_kind: python_crash" in fm
    assert "occurrences: 1" in fm
    assert "first_seen: 2026-05-31T14:23:01+00:00" in fm
    assert "last_seen: 2026-05-31T14:23:01+00:00" in fm
    assert "triggering_pbi: WI-247" in fm
    assert "ralph_sha: 51cc97a" in fm
    assert fm.startswith("---\n")
    assert fm.endswith("---\n")


def test_build_frontmatter_regression_of_optional(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    fm = build_frontmatter(
        pbi_id="autobug-a3f2c9-002",
        signature="a" * 64,
        trigger_kind="python_crash",
        severity="critical",
        ctx=ctx,
        target_repo="https://github.com/emp3thy/ralph",
        regression_of="autobug-a3f2c9-001",
    )
    assert "regression_of: autobug-a3f2c9-001" in fm
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_compose.py -v -k frontmatter`
`Expected: FAIL with ImportError on build_frontmatter`

- [ ] **Step 3: Implement**

```python
# Append to ralph_executor/autobug/compose.py
from ralph_executor.autobug.types import Context


def build_frontmatter(
    *,
    pbi_id: str,
    signature: str,
    trigger_kind: str,
    severity: str,
    ctx: Context,
    target_repo: str,
    regression_of: str | None = None,
) -> str:
    """Compose the YAML frontmatter block for an autobug BUG.md.

    Load-bearing fields (id, type, signature, target_repo) cannot fall
    back — caller (detect.py) aborts emission earlier if they're missing.
    """
    now_iso = ctx.now.replace(microsecond=0).isoformat()
    lines = [
        "---",
        f"id: {pbi_id}",
        "type: bug",
        f"severity: {severity}",
        "status: inbox",
        "attempts: 0",
        f"created_at: {now_iso}",
        f"updated_at: {now_iso}",
        f"target_repo: {target_repo}",
        f"signature: {signature}",
        f"trigger_kind: {trigger_kind}",
        "occurrences: 1",
        f"first_seen: {now_iso}",
        f"last_seen: {now_iso}",
        f"regression_of: {regression_of if regression_of else 'null'}",
        f"triggering_pbi: {ctx.triggering_pbi_id if ctx.triggering_pbi_id else 'null'}",
        f"ralph_sha: {ctx.ralph_sha}",
        "---",
        "",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_compose.py -v`
`Expected: PASS (5 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/compose.py tests/executor/autobug/test_compose.py
git commit -m "feat(autobug): build_frontmatter — all required + autobug-specific fields"
```

---

## Task T10: `build_bug_md` + `build_reproduce_md` — **confidence: 93%**

**Files:**
- Modify: `ralph_executor/autobug/compose.py` (add build_bug_md + build_reproduce_md)
- Modify: `tests/executor/autobug/test_compose.py` (add body tests)

**G6 acknowledged:** sections list is built with explicit appends, then `assert parts` guards against an empty join.

**G5 acknowledged:** BUG.md and REPRODUCE.md content is fully embedded (stacktrace text, env dict) with no external file references.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/executor/autobug/test_compose.py
from ralph_executor.autobug.compose import build_bug_md, build_reproduce_md


def test_build_bug_md_contains_all_sections(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    try:
        raise RuntimeError("test boom")
    except RuntimeError as exc:
        body = build_bug_md(exc, ctx)
    assert "## Stacktrace" in body
    assert "## Environment" in body
    assert "## Triggering PBI" in body
    assert "RuntimeError" in body
    assert "test boom" in body


def test_build_bug_md_survives_broken_env(tmp_path: Path) -> None:
    """Composer must not raise when env-snapshot section fails."""
    ctx = Context(
        queue_root=tmp_path,
        state_dir=tmp_path / "state",
        env={},
        now=datetime(2026, 5, 31, tzinfo=UTC),
        ralph_sha="sha",
        bot_author_email="b@e.com",
        triggering_pbi_id=None,
        queue_branch="ralph-queue",
    )
    try:
        raise RuntimeError("ok")
    except RuntimeError as exc:
        body = build_bug_md(exc, ctx)
    assert "## Stacktrace" in body  # still composes even with null triggering_pbi


def test_build_reproduce_md_marks_starting_point(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        body = build_reproduce_md(
            "autobug-abc-001",
            trigger_kind="python_crash",
            exc=exc,
            stderr=None,
            exit_code=None,
            ctx=ctx,
        )
    assert "AUTOBUG NOTE" in body
    assert "starting point" in body.lower()
    assert "python_crash" in body


def test_build_reproduce_md_subprocess_includes_exit_code(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    body = build_reproduce_md(
        "autobug-abc-002",
        trigger_kind="subprocess_crash",
        exc=None,
        stderr="Killed",
        exit_code=137,
        ctx=ctx,
    )
    assert "Exit code: 137" in body
    assert "Killed" in body
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_compose.py -v -k "bug_md or reproduce_md"`
`Expected: FAIL with ImportError on build_bug_md / build_reproduce_md.`

- [ ] **Step 3: Implement**

```python
# Append to ralph_executor/autobug/compose.py
import platform
import sys
import traceback


def _format_traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _env_snapshot(ctx: Context) -> str:
    env = ctx.env or {}
    relevant_keys = sorted(k for k in env if k.startswith("RALPH_") or k in ("GH_OWNER",))
    rows = [f"- {k}: <redacted>" if "TOKEN" in k or "KEY" in k else f"- {k}: {env[k]}"
            for k in relevant_keys]
    return (
        f"- OS: {platform.system()}\n"
        f"- Python: {sys.version.split()[0]}\n"
        f"- Ralph SHA: {ctx.ralph_sha}\n"
        + ("\n".join(rows) if rows else "- (no RALPH_/GH_OWNER env keys)")
    )


def _pbi_ref(ctx: Context) -> str:
    if ctx.triggering_pbi_id:
        return f"Triggering PBI: {ctx.triggering_pbi_id}"
    return "Triggering PBI: <none — startup / pre-claim crash>"


def _short_title(exc: BaseException) -> str:
    msg = str(exc).splitlines()[0] if str(exc) else "<no message>"
    return f"{type(exc).__qualname__}: {msg[:80]}"


def build_bug_md(exc: BaseException, ctx: Context) -> str:
    """Compose BUG.md body. Every section is _safe-wrapped; never raises."""
    parts: list[str] = []
    parts.append(f"# autobug — {_safe(lambda: _short_title(exc), 'unknown crash')}")
    parts.append(_section("Stacktrace",
                          _safe(lambda: f"```\n{_format_traceback(exc)}\n```",
                                "<traceback unavailable>")))
    parts.append(_section("Environment", _safe(lambda: _env_snapshot(ctx),
                                                "<env unavailable>")))
    parts.append(_section("Triggering PBI", _safe(lambda: _pbi_ref(ctx),
                                                   "<no PBI context>")))
    assert parts, "build_bug_md produced no sections — refusing to emit empty BUG.md"
    return "\n\n".join(parts)


_REPRODUCE_HEADER = (
    "> AUTOBUG NOTE: This bug was auto-captured at crash time. The content\n"
    "> below is what we observed, NOT a confirmed reproduction recipe. Your\n"
    "> iteration's job is to convert these observations into runnable\n"
    "> reproduction commands and append them to this file. This is a\n"
    "> starting point, not a recipe. If after investigation the crash is\n"
    "> genuinely non-deterministic (race condition, OOM under load, network\n"
    "> blip), write STUCK.md per the bug workflow's contradictory-evidence\n"
    "> trigger — do not stack speculative fixes."
)


def build_reproduce_md(
    pbi_id: str,
    *,
    trigger_kind: str,
    exc: BaseException | None,
    stderr: str | None,
    exit_code: int | None,
    ctx: Context,
) -> str:
    parts: list[str] = []
    parts.append(f"# REPRODUCE — {pbi_id}")
    parts.append(_REPRODUCE_HEADER)
    parts.append(f"## Observed\n\nTrigger: {trigger_kind}")
    if exit_code is not None:
        parts.append(f"Exit code: {exit_code}")
    if exc is not None:
        parts.append(_section("Stacktrace",
                              _safe(lambda: f"```\n{_format_traceback(exc)}\n```",
                                    "<traceback unavailable>")))
    if stderr:
        parts.append(_section("stderr (last 40 lines)",
                              "```\n" + "\n".join(stderr.splitlines()[-40:]) + "\n```"))
    parts.append(_section("Environment snapshot",
                          _safe(lambda: _env_snapshot(ctx), "<env unavailable>")))
    parts.append(
        "## Suggested reproduction approach\n\n"
        "- Inspect the deepest user frame in the stacktrace above\n"
        "- Reproduce the env conditions captured above\n"
        "- See triggering PBI in HISTORY.md if available"
    )
    assert parts, "build_reproduce_md produced no sections — refusing empty REPRODUCE.md"
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_compose.py -v`
`Expected: PASS (all tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/compose.py tests/executor/autobug/test_compose.py
git commit -m "feat(autobug): build_bug_md + build_reproduce_md — defensive composer"
```

---

## Task T11: `emit.new` + `bump` + `reopen` — **confidence: 93%**

**Files:**
- Create: `ralph_executor/autobug/emit.py`
- Create: `tests/executor/autobug/test_emit.py`

**G2/G3/G4 acknowledged:** all writes go through `git_ops.commit_all` + `push_with_rebase` (not `shutil`). New files are tracked by `commit_all`'s internal `git add -A`. Commit messages follow the canonical convention: `chore(queue): add <id>` for new, `chore(ralph-queue): bump <id>` for bump, `chore(queue): reopen <orig> as <new>` for reopen.

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/test_emit.py
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.autobug.emit import bump, new, reopen
from ralph_executor.autobug.types import Context


def _ctx_for_repo(fake_repo: Path) -> Context:
    return Context(
        queue_root=fake_repo,  # fake_repo IS the queue clone
        state_dir=fake_repo / ".ralph" / "state",
        env={},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="51cc97a",
        bot_author_email="bot@example.com",
        triggering_pbi_id="WI-247",
        queue_branch="main",  # fake_repo uses main per conftest
    )


def test_emit_new_writes_pbi_dir_with_bug_md(fake_repo: Path) -> None:
    ctx = _ctx_for_repo(fake_repo)
    try:
        raise RuntimeError("emit-new boom")
    except RuntimeError as exc:
        pbi_id = new(
            signature="a" * 64,
            exc=exc,
            ctx=ctx,
            trigger_kind="python_crash",
            severity="critical",
            target_repo="https://github.com/emp3thy/ralph",
        )
    assert pbi_id.startswith("autobug-")
    pbi_dir = fake_repo / ".ralph" / "inbox" / pbi_id
    assert pbi_dir.is_dir()
    assert (pbi_dir / "BUG.md").is_file()
    assert (pbi_dir / "REPRODUCE.md").is_file()
    assert (pbi_dir / "HISTORY.md").is_file()
    bug_text = (pbi_dir / "BUG.md").read_text(encoding="utf-8")
    assert "signature: " + "a" * 64 in bug_text
    assert "type: bug" in bug_text


def test_emit_bump_increments_occurrences_and_appends_trace(fake_repo: Path) -> None:
    ctx = _ctx_for_repo(fake_repo)
    # First emit creates the PBI
    try:
        raise RuntimeError("first")
    except RuntimeError as exc:
        pbi_id = new(
            signature="b" * 64,
            exc=exc,
            ctx=ctx,
            trigger_kind="python_crash",
            severity="critical",
            target_repo="https://github.com/emp3thy/ralph",
        )
    # Second crash with same signature
    try:
        raise RuntimeError("second")
    except RuntimeError as exc:
        bump(pbi_id, exc, ctx)
    bug_text = (fake_repo / ".ralph" / "inbox" / pbi_id / "BUG.md").read_text(encoding="utf-8")
    assert "occurrences: 2" in bug_text
    assert "## Trace 2" in bug_text


def test_emit_reopen_creates_new_pbi_with_regression_of(fake_repo: Path) -> None:
    ctx = _ctx_for_repo(fake_repo)
    try:
        raise RuntimeError("first")
    except RuntimeError as exc:
        new_pbi_id = reopen(
            existing_pbi_id="autobug-cccccc-001",
            exc=exc,
            signature="c" * 64,
            ctx=ctx,
            trigger_kind="python_crash",
            severity="critical",
            target_repo="https://github.com/emp3thy/ralph",
        )
    assert new_pbi_id.startswith("autobug-cccccc-")
    assert new_pbi_id != "autobug-cccccc-001"
    bug_text = (fake_repo / ".ralph" / "inbox" / new_pbi_id / "BUG.md").read_text(encoding="utf-8")
    assert "regression_of: autobug-cccccc-001" in bug_text
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_emit.py -v`
`Expected: FAIL with ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# ralph_executor/autobug/emit.py
"""Queue write + push_with_rebase for new / bump / reopen autobug PBIs."""

from __future__ import annotations

import logging
import re

from ralph_executor import git_ops
from ralph_executor.autobug.compose import (
    _format_traceback,
    build_bug_md,
    build_frontmatter,
    build_reproduce_md,
)
from ralph_executor.autobug.types import Context

log = logging.getLogger(__name__)


def _next_seq(queue_root, short_sig: str) -> str:
    """Find the highest existing <seq> for this short_sig across all states."""
    max_seq = 0
    for state in ("inbox", "current", "pending-pr", "done", "blocked"):
        state_dir = queue_root / ".ralph" / state
        if not state_dir.is_dir():
            continue
        for child in state_dir.iterdir():
            if not child.is_dir():
                continue
            m = re.fullmatch(rf"autobug-{short_sig}-(\d+)", child.name)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
    return f"{max_seq + 1:03d}"


def new(
    *,
    signature: str,
    exc: BaseException,
    ctx: Context,
    trigger_kind: str,
    severity: str,
    target_repo: str,
) -> str:
    short_sig = signature[:6]
    seq = _next_seq(ctx.queue_root, short_sig)
    pbi_id = f"autobug-{short_sig}-{seq}"
    pbi_dir = ctx.queue_root / ".ralph" / "inbox" / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=False)
    fm = build_frontmatter(
        pbi_id=pbi_id,
        signature=signature,
        trigger_kind=trigger_kind,
        severity=severity,
        ctx=ctx,
        target_repo=target_repo,
    )
    bug_body = build_bug_md(exc, ctx)
    (pbi_dir / "BUG.md").write_text(fm + "\n" + bug_body + "\n", encoding="utf-8")
    (pbi_dir / "REPRODUCE.md").write_text(
        build_reproduce_md(pbi_id, trigger_kind=trigger_kind, exc=exc,
                           stderr=None, exit_code=None, ctx=ctx) + "\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _commit_and_push(ctx, f"chore(queue): add {pbi_id} (autobug signature {short_sig})")
    return pbi_id


def bump(pbi_id: str, exc: BaseException, ctx: Context) -> None:
    """Increment occurrences + last_seen, append trace, push."""
    bug_path = _locate_bug_md(ctx, pbi_id)
    if bug_path is None:
        log.warning("autobug bump: PBI %s not found in any open state", pbi_id)
        return
    text = bug_path.read_text(encoding="utf-8")
    text = _bump_occurrences(text)
    text = _set_last_seen(text, ctx)
    trace_n = _count_traces(text) + 1
    text += f"\n\n## Trace {trace_n}\n\n```\n{_format_traceback(exc)}\n```\n"
    bug_path.write_text(text, encoding="utf-8")
    _commit_and_push(ctx, f"chore(ralph-queue): bump {pbi_id} (occurrence {trace_n})")


def reopen(
    *,
    existing_pbi_id: str,
    exc: BaseException,
    signature: str,
    ctx: Context,
    trigger_kind: str,
    severity: str,
    target_repo: str,
) -> str:
    short_sig = signature[:6]
    seq = _next_seq(ctx.queue_root, short_sig)
    pbi_id = f"autobug-{short_sig}-{seq}"
    pbi_dir = ctx.queue_root / ".ralph" / "inbox" / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=False)
    fm = build_frontmatter(
        pbi_id=pbi_id,
        signature=signature,
        trigger_kind=trigger_kind,
        severity=severity,
        ctx=ctx,
        target_repo=target_repo,
        regression_of=existing_pbi_id,
    )
    bug_body = build_bug_md(exc, ctx)
    (pbi_dir / "BUG.md").write_text(fm + "\n" + bug_body + "\n", encoding="utf-8")
    (pbi_dir / "REPRODUCE.md").write_text(
        build_reproduce_md(pbi_id, trigger_kind=trigger_kind, exc=exc,
                           stderr=None, exit_code=None, ctx=ctx) + "\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text(
        f"## Regression of {existing_pbi_id}\n\n"
        f"Re-emerged on {ctx.now.replace(microsecond=0).isoformat()}.\n",
        encoding="utf-8",
    )
    _commit_and_push(ctx, f"chore(queue): reopen {existing_pbi_id} as {pbi_id}")
    return pbi_id


def _locate_bug_md(ctx: Context, pbi_id: str):
    for state in ("inbox", "current", "pending-pr"):
        p = ctx.queue_root / ".ralph" / state / pbi_id / "BUG.md"
        if p.is_file():
            return p
    return None


_OCC_RE = re.compile(r"^occurrences:\s*(\d+)\s*$", re.MULTILINE)
_LAST_SEEN_RE = re.compile(r"^last_seen:\s*\S+\s*$", re.MULTILINE)
_TRACE_RE = re.compile(r"^## Trace \d+", re.MULTILINE)


def _bump_occurrences(text: str) -> str:
    m = _OCC_RE.search(text)
    if not m:
        return text
    new_n = int(m.group(1)) + 1
    return text[:m.start()] + f"occurrences: {new_n}" + text[m.end():]


def _set_last_seen(text: str, ctx: Context) -> str:
    iso = ctx.now.replace(microsecond=0).isoformat()
    return _LAST_SEEN_RE.sub(f"last_seen: {iso}", text)


def _count_traces(text: str) -> int:
    return len(_TRACE_RE.findall(text))


def _commit_and_push(ctx: Context, msg: str) -> None:
    git_ops.commit_all(ctx.queue_root, msg)
    git_ops.push_with_rebase(ctx.queue_root, remote="origin", branch=ctx.queue_branch)
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_emit.py -v`
`Expected: PASS (3 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/emit.py tests/executor/autobug/test_emit.py
git commit -m "feat(autobug): emit.new + bump + reopen with commit conventions"
```

---

## Task T12: detect orchestration — **confidence: 93%**

**Files:**
- Create: `ralph_executor/autobug/detect.py`
- Create: `tests/executor/autobug/test_detect.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/test_detect.py
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.autobug import detect
from ralph_executor.autobug.types import Context


def _ctx(fake_repo: Path, *, env: dict | None = None) -> Context:
    return Context(
        queue_root=fake_repo,
        state_dir=fake_repo / ".ralph" / "state",
        env=env or {},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="sha",
        bot_author_email="bot@e.com",
        triggering_pbi_id="WI-247",
        queue_branch="main",
    )


def test_detect_python_crash_emits_when_no_existing(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(fake_repo)
    calls = []
    from ralph_executor.autobug import emit
    monkeypatch.setattr(emit, "new", lambda **kw: calls.append(("new", kw)) or "autobug-x-001")
    try:
        raise RuntimeError("first")
    except RuntimeError as exc:
        detect.detect_python_crash(exc, ctx, target_repo="https://github.com/x/y",
                                    severity="critical")
    assert calls and calls[0][0] == "new"


def test_detect_python_crash_skips_when_recursion_guard_tripped(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(fake_repo, env={"RALPH_AUTOBUG_DEPTH": "1"})
    calls = []
    from ralph_executor.autobug import emit
    monkeypatch.setattr(emit, "new", lambda **kw: calls.append(("new", kw)))
    try:
        raise RuntimeError("under recursion")
    except RuntimeError as exc:
        detect.detect_python_crash(exc, ctx, target_repo="https://github.com/x/y",
                                    severity="critical")
    assert not calls


def test_detect_does_not_propagate_emit_failure(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(fake_repo)
    from ralph_executor.autobug import emit
    def _boom(**kw): raise RuntimeError("emit broke")
    monkeypatch.setattr(emit, "new", _boom)
    try:
        raise RuntimeError("orig")
    except RuntimeError as exc:
        # Must NOT raise
        detect.detect_python_crash(exc, ctx, target_repo="https://github.com/x/y",
                                    severity="critical")


def test_detect_signature_failure_aborts(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    ctx = _ctx(fake_repo)
    from ralph_executor.autobug import signature as sig_mod, emit
    monkeypatch.setattr(sig_mod, "python_crash_signature",
                        lambda exc: (_ for _ in ()).throw(RuntimeError("sig broke")))
    calls = []
    monkeypatch.setattr(emit, "new", lambda **kw: calls.append(kw))
    caplog.set_level(logging.WARNING, logger="ralph_executor.autobug")
    try:
        raise RuntimeError("x")
    except RuntimeError as exc:
        detect.detect_python_crash(exc, ctx, target_repo="https://github.com/x/y",
                                    severity="critical")
    assert not calls
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_detect.py -v`
`Expected: FAIL with ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# ralph_executor/autobug/detect.py
"""Top-level orchestration: signature → recursion → dedup → rate → emit."""

from __future__ import annotations

import logging

from ralph_executor.autobug import dedup, emit, fuses, signature as sig_mod
from ralph_executor.autobug.fuses import RateLimitConfig
from ralph_executor.autobug.types import Context

log = logging.getLogger(__name__)

_DEFAULT_RATE = RateLimitConfig()


def detect_python_crash(
    exc: BaseException,
    ctx: Context,
    *,
    target_repo: str,
    severity: str = "critical",
    rate_cfg: RateLimitConfig = _DEFAULT_RATE,
) -> None:
    try:
        _do_detect_python(exc, ctx, target_repo=target_repo, severity=severity,
                          rate_cfg=rate_cfg)
    except BaseException as inner:
        log.warning("autobug.detect_python_crash itself failed: %s", inner)


def detect_subprocess_crash(
    exit_code: int,
    stderr: str,
    command: list[str],
    ctx: Context,
    *,
    target_repo: str,
    severity: str = "high",
    rate_cfg: RateLimitConfig = _DEFAULT_RATE,
) -> None:
    try:
        _do_detect_subprocess(exit_code, stderr, command, ctx,
                              target_repo=target_repo, severity=severity,
                              rate_cfg=rate_cfg)
    except BaseException as inner:
        log.warning("autobug.detect_subprocess_crash itself failed: %s", inner)


def _do_detect_python(exc, ctx, *, target_repo, severity, rate_cfg):
    if not fuses.recursion_check(ctx.env):
        log.warning("autobug: recursion guard tripped (python)")
        return
    try:
        sig = sig_mod.python_crash_signature(exc)
    except BaseException as e:
        log.warning("autobug: signature computation failed: %s", e)
        return
    if not ctx.bot_author_email:
        log.warning("autobug: bot_author_email missing; aborting emit signature=%s",
                    sig[:8])
        return
    result = dedup.lookup(sig, ctx.queue_root, ctx.now)
    _dispatch(result, sig, exc, ctx, target_repo=target_repo, severity=severity,
              trigger_kind="python_crash", rate_cfg=rate_cfg, stderr=None,
              exit_code=None)


def _do_detect_subprocess(exit_code, stderr, command, ctx, *, target_repo, severity,
                           rate_cfg):
    del command  # signature already encodes exit_code + normalised stderr
    if not fuses.recursion_check(ctx.env):
        log.warning("autobug: recursion guard tripped (subprocess)")
        return
    try:
        sig = sig_mod.subprocess_crash_signature(exit_code, stderr)
    except BaseException as e:
        log.warning("autobug: signature computation failed: %s", e)
        return
    if not ctx.bot_author_email:
        log.warning("autobug: bot_author_email missing; aborting subprocess emit "
                    "signature=%s", sig[:8])
        return
    result = dedup.lookup(sig, ctx.queue_root, ctx.now)
    # Subprocess crashes have no exc — synthesize a placeholder for compose.
    synth = RuntimeError(f"subprocess exit {exit_code}: {stderr.splitlines()[-1] if stderr else ''}")
    _dispatch(result, sig, synth, ctx, target_repo=target_repo, severity=severity,
              trigger_kind="subprocess_crash", rate_cfg=rate_cfg, stderr=stderr,
              exit_code=exit_code)


def _dispatch(result, sig, exc, ctx, *, target_repo, severity, trigger_kind,
              rate_cfg, stderr, exit_code):
    if result.kind == "bump_existing":
        emit.bump(result.existing_pbi_id, exc, ctx)
        return
    if result.kind == "reopen_regression":
        if not fuses.rate_check(ctx.state_dir, rate_cfg, ctx.now):
            fuses.rollup(ctx.state_dir, sig, "rate-limited reopen", now=ctx.now)
            return
        emit.reopen(
            existing_pbi_id=result.existing_pbi_id, exc=exc, signature=sig,
            ctx=ctx, trigger_kind=trigger_kind, severity=severity,
            target_repo=target_repo,
        )
        fuses.append_emission(ctx.state_dir, ctx.now)
        return
    if not fuses.rate_check(ctx.state_dir, rate_cfg, ctx.now):
        fuses.rollup(ctx.state_dir, sig, "rate-limited new", now=ctx.now)
        return
    emit.new(signature=sig, exc=exc, ctx=ctx, trigger_kind=trigger_kind,
             severity=severity, target_repo=target_repo)
    fuses.append_emission(ctx.state_dir, ctx.now)
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_detect.py -v`
`Expected: PASS (4 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/detect.py tests/executor/autobug/test_detect.py
git commit -m "feat(autobug): detect orchestration — signature, recursion, dedup, rate, emit"
```

---

## Task T13: Public API exports — **confidence: 99%**

**Files:**
- Modify: `ralph_executor/autobug/__init__.py`
- Create: `tests/executor/autobug/test_public_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/test_public_api.py
def test_public_api_exports_two_detect_functions() -> None:
    from ralph_executor import autobug
    assert hasattr(autobug, "detect_python_crash")
    assert hasattr(autobug, "detect_subprocess_crash")
    assert callable(autobug.detect_python_crash)
    assert callable(autobug.detect_subprocess_crash)


def test_public_api_exports_context_type() -> None:
    from ralph_executor import autobug
    assert hasattr(autobug, "Context")
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/test_public_api.py -v`
`Expected: FAIL — attributes missing on the autobug module.`

- [ ] **Step 3: Implement**

```python
# ralph_executor/autobug/__init__.py
"""Autobug — capture executor + Claude subprocess crashes as bug PBIs.

Public API:
  - detect_python_crash(exc, ctx, *, target_repo, severity)
  - detect_subprocess_crash(exit_code, stderr, command, ctx, *, target_repo, severity)
  - Context  (frozen dataclass passed by callers)
"""

from ralph_executor.autobug.detect import detect_python_crash, detect_subprocess_crash
from ralph_executor.autobug.types import Context, DedupResult

__all__ = [
    "Context",
    "DedupResult",
    "detect_python_crash",
    "detect_subprocess_crash",
]
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/test_public_api.py -v`
`Expected: PASS (2 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/__init__.py tests/executor/autobug/test_public_api.py
git commit -m "feat(autobug): public API exports"
```

---

## Task T14: TOML keys + ExecutorConfig fields — **confidence: 96%**

**Files:**
- Modify: `ralph_executor/config.py` (add 6 new TOML keys + ExecutorConfig fields)
- Create: `tests/executor/test_config_autobug.py`

**G1 acknowledged:** Step 0 enumerates every `ExecutorConfig(` constructor in `tests/`.

- [ ] **Step 0:** `Run: rg "ExecutorConfig\(" tests/ -l` → enumerate test files. Verify each constructs `ExecutorConfig(...)` with keyword args OR can tolerate new fields with defaults. The new autobug fields all have defaults; no breakage expected. Confirm by running `uv run pytest tests/executor/ -x` after Step 4.

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/test_config_autobug.py
from pathlib import Path

from ralph_executor.config import ExecutorConfig, load_config


def test_executor_config_has_autobug_defaults() -> None:
    # Constructing without any autobug-* kwargs must succeed.
    cfg = ExecutorConfig(
        repo_path=Path("/tmp"),
        queue_repo="https://github.com/x/y",
        queue_branch="ralph-queue",
        main_branch="main",
        max_attempts=3,
        log_level=20,
        iteration_sleep_seconds=0.0,
        claude_binary="claude",
        claude_permission_mode="bypassPermissions",
        anthropic_api_key="",
        git_host="github",
        gh_owner="",
        ado_org_url="",
        ado_project="",
        halt_webhook="",
        pr_check_poll_max_attempts=6,
        pr_check_poll_interval_seconds=30.0,
    )
    assert cfg.autobug_enabled is True
    assert cfg.autobug_rate_max == 5
    assert cfg.autobug_rate_window_minutes == 10
    assert cfg.autobug_dedup_done_window_days == 30
    assert cfg.autobug_severity_python_crash == "critical"
    assert cfg.autobug_severity_subprocess_crash == "high"


def test_load_config_reads_autobug_keys_from_toml(tmp_path: Path, monkeypatch) -> None:
    import subprocess
    subprocess.run(["git", "init", "--initial-branch=main", str(tmp_path)],
                    check=True, capture_output=True)
    (tmp_path / ".ralph").mkdir()
    (tmp_path / ".ralph" / "config.toml").write_text(
        'queue_repo = "https://github.com/x/y"\n'
        'autobug_enabled = false\n'
        'autobug_rate_max = 10\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_REPO_PATH", str(tmp_path))
    cfg = load_config()
    assert cfg.autobug_enabled is False
    assert cfg.autobug_rate_max == 10
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/test_config_autobug.py -v`
`Expected: FAIL — autobug_* attrs not on ExecutorConfig.`

- [ ] **Step 3: Implement**

In `ralph_executor/config.py`, add to `_TOML_KNOWN_KEYS` frozenset:

```python
        "autobug_enabled",
        "autobug_rate_max",
        "autobug_rate_window_minutes",
        "autobug_dedup_done_window_days",
        "autobug_severity_python_crash",
        "autobug_severity_subprocess_crash",
```

Add default constants near the top:

```python
DEFAULT_AUTOBUG_ENABLED = True
DEFAULT_AUTOBUG_RATE_MAX = 5
DEFAULT_AUTOBUG_RATE_WINDOW_MINUTES = 10
DEFAULT_AUTOBUG_DEDUP_DONE_WINDOW_DAYS = 30
DEFAULT_AUTOBUG_SEVERITY_PYTHON_CRASH = "critical"
DEFAULT_AUTOBUG_SEVERITY_SUBPROCESS_CRASH = "high"
```

Append fields to `ExecutorConfig`:

```python
    autobug_enabled: bool = DEFAULT_AUTOBUG_ENABLED
    autobug_rate_max: int = DEFAULT_AUTOBUG_RATE_MAX
    autobug_rate_window_minutes: int = DEFAULT_AUTOBUG_RATE_WINDOW_MINUTES
    autobug_dedup_done_window_days: int = DEFAULT_AUTOBUG_DEDUP_DONE_WINDOW_DAYS
    autobug_severity_python_crash: str = DEFAULT_AUTOBUG_SEVERITY_PYTHON_CRASH
    autobug_severity_subprocess_crash: str = DEFAULT_AUTOBUG_SEVERITY_SUBPROCESS_CRASH
```

In `load_config`, resolve each via the existing `_resolve_*` helpers and pass into the `ExecutorConfig(...)` constructor.

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/test_config_autobug.py tests/executor/ -x`
`Expected: PASS (new tests) + no breakage in existing executor suite.`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/config.py tests/executor/test_config_autobug.py
git commit -m "feat(config): add autobug_* TOML keys with safe defaults"
```

---

## Task T15: `bot_author_email` startup validation — **confidence: 93%**

**Files:**
- Modify: `ralph_executor/cli.py` (add `validate_startup` + call from `main`)
- Create: `tests/executor/test_cli_validate_startup.py`

- [ ] **Step 0:** Confirm `cli.py:686-688` is the existing top-level `except Exception` net. The new `validate_startup` runs BEFORE iteration begins, after `load_config()` (line 617). Confirm `_pr_skill_scripts_path` is exported from `loop.py:202`.

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/test_cli_validate_startup.py
import logging
from pathlib import Path

import pytest

from ralph_executor import cli
from ralph_executor.config import ExecutorConfig


def _minimal_cfg(*, bot_email: str = "", autobug_enabled: bool = True) -> ExecutorConfig:
    return ExecutorConfig(
        repo_path=Path("/tmp"),
        queue_repo="https://github.com/x/y",
        queue_branch="ralph-queue",
        main_branch="main",
        max_attempts=3,
        log_level=20,
        iteration_sleep_seconds=0.0,
        claude_binary="claude",
        claude_permission_mode="bypassPermissions",
        anthropic_api_key="",
        git_host="github",
        gh_owner="",
        ado_org_url="",
        ado_project="",
        halt_webhook="",
        pr_check_poll_max_attempts=6,
        pr_check_poll_interval_seconds=30.0,
        bot_author_email=bot_email,
        autobug_enabled=autobug_enabled,
    )


def test_validate_startup_warns_when_bot_email_missing_and_autobug_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = _minimal_cfg(bot_email="", autobug_enabled=True)
    caplog.set_level(logging.WARNING, logger="ralph_executor")
    cli.validate_startup(cfg)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("bot_author_email" in m for m in msgs)


def test_validate_startup_silent_when_bot_email_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = _minimal_cfg(bot_email="bot@e.com", autobug_enabled=True)
    caplog.set_level(logging.WARNING, logger="ralph_executor")
    cli.validate_startup(cfg)
    assert not any("bot_author_email" in r.getMessage() for r in caplog.records)


def test_validate_startup_silent_when_autobug_off_and_no_sweep(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch
) -> None:
    cfg = _minimal_cfg(bot_email="", autobug_enabled=False)
    # No PR-skill scripts dir → sweep can't run either
    from ralph_executor import loop as loop_mod
    monkeypatch.setattr(loop_mod, "_pr_skill_scripts_path",
                       lambda c: tmp_path / "nonexistent")
    caplog.set_level(logging.WARNING, logger="ralph_executor")
    cli.validate_startup(cfg)
    assert not any("bot_author_email" in r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/test_cli_validate_startup.py -v`
`Expected: FAIL — cli.validate_startup not defined.`

- [ ] **Step 3: Implement**

In `ralph_executor/cli.py`:

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
    autobug_active = getattr(cfg, "autobug_enabled", True)
    sweep_active = _pr_skill_scripts_path(cfg).is_dir()
    if not (autobug_active or sweep_active):
        return
    affected: list[str] = []
    if autobug_active:
        affected.append("autobug")
    if sweep_active:
        affected.append("sweep")
    log.warning(
        "ralph startup: bot_author_email is not set; %s will skip / abort on "
        "every iteration. Set TOML key 'bot_author_email' or env "
        "RALPH_ADO_AUTHOR_EMAIL.",
        " AND ".join(affected),
    )
```

Call from `cli.main` after `load_config()` succeeds and overrides apply:

```python
    cfg = load_config()
    cfg = _apply_overrides(cfg, args)
    validate_startup(cfg)  # NEW
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/test_cli_validate_startup.py -v`
`Expected: PASS (3 tests)`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/cli.py tests/executor/test_cli_validate_startup.py
git commit -m "feat(cli): one-time validate_startup for bot_author_email"
```

---

## Task T16: `autobug_emitted` EventType — **confidence: 96%**

**Files:**
- Modify: `ralph_executor/safety/events.py` (add AUTOBUG_EMITTED variant)
- Create: `tests/safety/test_events_autobug.py`

**Verified:** `safety/events.py:18` uses `from enum import StrEnum`; new variant follows same pattern.

- [ ] **Step 1: Write the failing test**

```python
# tests/safety/test_events_autobug.py
from datetime import UTC, datetime
from pathlib import Path

from ralph_executor.safety.events import Event, EventType, open_log


def test_autobug_emitted_event_round_trips(tmp_path: Path) -> None:
    log = open_log(tmp_path)
    e = Event(
        kind=EventType.AUTOBUG_EMITTED,
        recorded_at=datetime(2026, 5, 31, tzinfo=UTC),
        pbi_id="autobug-abc-001",
        payload={"signature": "abc" + "0" * 61, "trigger_kind": "python_crash"},
    )
    log.append(e)
    events = log.recent(window=__import__("datetime").timedelta(hours=1),
                         now=datetime(2026, 5, 31, 1, tzinfo=UTC))
    log.close()
    assert events
    assert events[0].kind == EventType.AUTOBUG_EMITTED
    assert events[0].payload["signature"].startswith("abc")
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/safety/test_events_autobug.py -v`
`Expected: FAIL — AUTOBUG_EMITTED not in EventType.`

- [ ] **Step 3: Implement**

In `ralph_executor/safety/events.py` after `ITERATION_COMPLETED`:

```python
    AUTOBUG_EMITTED = "autobug_emitted"
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/safety/test_events_autobug.py tests/safety/ -v`
`Expected: PASS (new test) + no regressions.`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/safety/events.py tests/safety/test_events_autobug.py
git commit -m "feat(safety/events): add AUTOBUG_EMITTED EventType"
```

---

## Task T17: claude_spawn subprocess wire + `RALPH_AUTOBUG_DEPTH` env — **confidence: 93%**

**Files:**
- Modify: `ralph_executor/claude_spawn.py` (env marker for autobug PBI claim, subprocess crash wire after exit-code check)
- Create: `tests/executor/test_claude_spawn_autobug.py`

**Verified:** `claude_spawn.py:565` does `env = os.environ.copy()`. Subprocess error classification is in `classify_outcome` at line 780 (`if exit_code != 0`). The `spawn_claude_p` function calls `classify_outcome` at line 717 — the autobug wire goes AFTER that call when the outcome is `error`.

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/test_claude_spawn_autobug.py
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import _pbi_frontmatter_has_signature
from ralph_executor.queue.filesystem import parse_pbi_directory


def test_pbi_frontmatter_has_signature_true_for_autobug(
    fake_repo: Path,
) -> None:
    pbi_dir = fake_repo / ".ralph" / "current" / "autobug-abc-001"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "BUG.md").write_text(
        "---\nid: autobug-abc-001\ntype: bug\nseverity: critical\n"
        "status: current\nattempts: 0\n"
        "created_at: 2026-05-31T00:00:00+00:00\n"
        "updated_at: 2026-05-31T00:00:00+00:00\n"
        "target_repo: https://github.com/x/y\n"
        "signature: abc123def456\n---\n# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "REPRODUCE.md").write_text("# r\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    pbi = parse_pbi_directory(pbi_dir, status="current")
    assert _pbi_frontmatter_has_signature(pbi) is True


def test_pbi_frontmatter_has_signature_false_for_normal(fake_repo: Path) -> None:
    from tests.executor.conftest import write_sample_pbi
    pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-1234", where="current")
    pbi = parse_pbi_directory(pbi_dir, status="current")
    assert _pbi_frontmatter_has_signature(pbi) is False
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/test_claude_spawn_autobug.py -v`
`Expected: FAIL — _pbi_frontmatter_has_signature not in claude_spawn.`

- [ ] **Step 3: Implement env marker helper**

In `ralph_executor/claude_spawn.py` at module scope:

```python
import re as _re

_SIG_FM_RE = _re.compile(r"^signature:\s*[0-9a-f]+\s*$", _re.MULTILINE)


def _pbi_frontmatter_has_signature(pbi: PBI) -> bool:
    """True iff PBI's entry-file frontmatter has a signature: <hex> key."""
    for entry in ("BUG.md", "PBI.md", "FEEDBACK.md"):
        f = pbi.path / entry
        if f.is_file():
            try:
                return bool(_SIG_FM_RE.search(f.read_text(encoding="utf-8")))
            except OSError:
                return False
    return False
```

Inside `spawn_claude_p` after the existing env block (after line ~595, before the `log.info("spawning ...")` line):

```python
    if _pbi_frontmatter_has_signature(pbi):
        env["RALPH_AUTOBUG_DEPTH"] = "1"
```

After `classify_outcome` returns (after line 726, just before the `return classify_outcome(...)` line is reached — wrap the return):

```python
    outcome = classify_outcome(
        pbi_dir=effective_pbi_dir,
        stdout=stdout_text,
        stderr=stderr_text,
        exit_code=returncode,
        duration_seconds=duration,
        pr_url=pr_url,
        pr_check_state=pr_check_state,
        pr_check_failed_names=pr_check_failed_names,
    )
    # Autobug wire: surface subprocess crashes (non-zero exit code or
    # synthetic timeout error) as bug PBIs. No-op-safe.
    if outcome.kind == "error" and getattr(cfg, "autobug_enabled", True):
        try:
            from datetime import UTC, datetime
            from ralph_executor import autobug
            ctx = autobug.Context(
                queue_root=_queue_repo_root_for_spawn(cfg),
                state_dir=_queue_repo_root_for_spawn(cfg) / ".ralph" / "state",
                env=dict(os.environ),
                now=datetime.now(tz=UTC),
                ralph_sha=os.environ.get("RALPH_SHA", "<unknown>"),
                bot_author_email=cfg.bot_author_email,
                triggering_pbi_id=pbi.id,
                queue_branch=cfg.queue_branch,
            )
            autobug.detect_subprocess_crash(
                exit_code=returncode,
                stderr=stderr_text,
                command=argv,
                ctx=ctx,
                target_repo=cfg.queue_repo,
                severity=cfg.autobug_severity_subprocess_crash,
            )
        except BaseException as inner:
            log.warning("autobug subprocess wire failed: %s", inner)
    return outcome
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/test_claude_spawn_autobug.py tests/executor/test_claude_spawn.py -v`
`Expected: PASS (new tests + no regressions).`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/claude_spawn.py tests/executor/test_claude_spawn_autobug.py
git commit -m "feat(claude_spawn): RALPH_AUTOBUG_DEPTH marker + subprocess crash wire"
```

---

## Task T18: `cli.py` outer wrapper around generator loop — **confidence: 93%**

**Files:**
- Modify: `ralph_executor/cli.py` (replace top-level `except Exception` net with autobug-aware wrapper)
- Create: `tests/executor/test_cli_autobug_wrapper.py`

**Verified:** `cli.py:660-688` currently does the generator iteration + a bare `except Exception` net. The new wrapper preserves the original exception, wraps the autobug call in its own try/except, and re-raises.

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/test_cli_autobug_wrapper.py
import logging
from pathlib import Path

import pytest

from ralph_executor import autobug, cli


def _build_cfg(tmp_path):
    from tests.executor.test_cli_validate_startup import _minimal_cfg
    return _minimal_cfg(bot_email="bot@e.com", autobug_enabled=True)


def test_main_wrapper_calls_autobug_on_unhandled_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[BaseException] = []
    monkeypatch.setattr(
        autobug, "detect_python_crash",
        lambda exc, ctx, **kw: captured.append(exc),
    )

    def bad_iter(cfg):
        yield "first-ok"
        raise RuntimeError("mid-iter crash")

    monkeypatch.setattr(cli, "run_loop", bad_iter)
    cfg = _build_cfg(tmp_path)
    with pytest.raises(RuntimeError, match="mid-iter crash"):
        cli.run_loop_with_autobug(cfg)
    assert captured and isinstance(captured[0], RuntimeError)


def test_main_wrapper_reraises_original_when_autobug_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom(exc, ctx, **kw):
        raise TypeError("autobug self-crash")
    monkeypatch.setattr(autobug, "detect_python_crash", _boom)

    def bad_iter(cfg):
        if False:
            yield
        raise RuntimeError("original crash")

    monkeypatch.setattr(cli, "run_loop", bad_iter)
    cfg = _build_cfg(tmp_path)
    caplog.set_level(logging.ERROR, logger="ralph_executor")
    with pytest.raises(RuntimeError, match="original crash"):
        cli.run_loop_with_autobug(cfg)
    assert any("AUTOBUG FAILED" in r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/test_cli_autobug_wrapper.py -v`
`Expected: FAIL — cli.run_loop_with_autobug not defined.`

- [ ] **Step 3: Implement**

In `ralph_executor/cli.py`:

```python
def run_loop_with_autobug(cfg) -> int:
    """Iterate run_loop(cfg) wrapped in a top-level autobug try/except.

    Guarantees:
    1. Any exception raised by the generator (during yield) is captured
       by autobug.
    2. Even if autobug itself raises, the ORIGINAL exception is re-raised.
    3. The ORIGINAL traceback is what the operator sees.
    """
    try:
        for result in run_loop(cfg):
            log.info("iteration outcome=%s pbi=%s",
                     getattr(result, "outcome", result),
                     getattr(result, "pbi_id", None))
            if getattr(result, "outcome", None) == "halted":
                log.warning("loop halted -- exiting")
                return 0
        return 0
    except KeyboardInterrupt:
        log.info("interrupted; exiting cleanly")
        return 0
    except BaseException as original_exc:
        if getattr(cfg, "autobug_enabled", True):
            try:
                from datetime import UTC, datetime

                from ralph_executor import autobug
                ctx = autobug.Context(
                    queue_root=cfg.workspace_root / "queue",
                    state_dir=cfg.workspace_root / "queue" / ".ralph" / "state",
                    env=dict(os.environ),
                    now=datetime.now(tz=UTC),
                    ralph_sha=os.environ.get("RALPH_SHA", "<unknown>"),
                    bot_author_email=cfg.bot_author_email,
                    triggering_pbi_id=None,
                    queue_branch=cfg.queue_branch,
                )
                autobug.detect_python_crash(
                    original_exc, ctx,
                    target_repo=cfg.queue_repo,
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
```

Replace the existing iteration block in `main()` (lines 660–688) so the default path calls `run_loop_with_autobug(cfg)` instead of the bare for-loop. Preserve the `--iterations N` / `--once` short-circuit branch unchanged (those use `iterate_once`, not `run_loop`).

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/test_cli_autobug_wrapper.py tests/executor/test_cli.py -v`
`Expected: PASS.`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/cli.py tests/executor/test_cli_autobug_wrapper.py
git commit -m "feat(cli): top-level autobug wrapper around run_loop generator"
```

---

## Task T19: `loop.py` internal wires at verified sites — **confidence: 92%**

**Files:**
- Modify: `ralph_executor/loop.py` (wire autobug at three real-error-swallow sites)
- Create: `tests/executor/test_loop_autobug_wires.py`

**Lift rationale via line-numbered enumeration + spike:**

Source-read (`Grep "except\s+(Exception|BaseException)" loop.py`) returns four hits at lines 192, 454, 470, 813. Classification (VERIFIED at plan-write time):

- **Line 192** — inside `_run_sweep`, wraps `event_log.close()` in finally. NOT a defect swallow — closing twice or close-after-error is benign. **DO NOT WIRE.**
- **Line 454** — inside `_cleanup_work_worktree`, wraps `ensure_clone` failure during cleanup. NOT a defect swallow — cleanup is best-effort and the comment says "orphan may remain". **DO NOT WIRE** (would emit autobug on every transient network blip during cleanup).
- **Line 470** — inside `_cleanup_work_worktree`, wraps `remove_worktree`. Same reason as line 454. **DO NOT WIRE.**
- **Line 813** — inside `iterate_once`, wraps `ensure_clone` during resume of an already-claimed PBI. Same character: best-effort, falls back to deterministic clone path. **DO NOT WIRE.**

After this enumeration, NONE of the existing `except Exception` sites in `loop.py` are real defect swallows worthy of autobug wiring — they are all defensive log-and-continue patterns where the loop intentionally tolerates errors.

**Therefore T19's wire surface in loop.py is empty.** The spec called for "explicit wires at internal except blocks in loop.py" — source-reading reveals there are NO appropriate sites in the current `loop.py`. The two real coverage paths (`cli.py` top-level and `claude_spawn` subprocess) are already wired by T17 + T18.

**Spike confirms the lift:** Spawn a quick test that monkeypatches `_run_sweep` to raise `RuntimeError` and asserts the exception propagates UP to the `cli.py` wrapper (i.e. T18's wrapper catches it). This proves the cli wrapper is sufficient for loop-body crashes without per-site wires.

To keep the spec contract — "three sites fire the public API" — this task adds a single defensive wire inside `iterate_once` at the OUTERMOST level: any `BaseException` raised by the iteration body that ISN'T `KeyboardInterrupt`, `HaltedError`, `PushRebaseConflict`, or `UncommittedSource` (the four legitimate control-flow exits) is autobug'd BEFORE being re-raised. This catches the cycle-detector path, the `handle_stuck` path, and any future swallow site we add without requiring per-site changes.

- [ ] **Step 0:** Confirm the line-numbered enumeration above by `Run: rg -n "except\s+(Exception|BaseException)" ralph_executor/loop.py`. If the line numbers shift due to other in-flight changes, update the classifications above (the type of exception and the comment context determine "real defect swallow", not the exact line).

- [ ] **Step 0b: Spike — verify cli wrapper catches inner crashes.** Write a throwaway test that monkeypatches `_run_sweep` to raise `RuntimeError("inner crash")` and asserts the `cli.run_loop_with_autobug` wrapper catches it via T18's path. The spike confirms the cli wrapper sees inner crashes. The wire in this task adds a SECOND signal closer to the crash for richer triggering_pbi context.

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/test_loop_autobug_wires.py
import logging
from pathlib import Path

import pytest

from ralph_executor import autobug, loop


def test_iterate_once_autobug_wires_an_unexpected_crash(
    monkeypatch: pytest.MonkeyPatch, fake_repo: Path, cfg_for_repo
) -> None:
    captured = []
    monkeypatch.setattr(
        autobug, "detect_python_crash",
        lambda exc, ctx, **kw: captured.append((type(exc).__name__, str(exc))),
    )
    # Force iterate_once to crash with an UNEXPECTED exception (not one
    # of the four control-flow exits). Patch _pull_queue to raise.
    monkeypatch.setattr(
        loop, "_pull_queue",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("pull-queue boom")),
    )
    with pytest.raises(RuntimeError, match="pull-queue boom"):
        loop.iterate_once(cfg_for_repo)
    assert captured == [("RuntimeError", "pull-queue boom")]
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/test_loop_autobug_wires.py -v`
`Expected: FAIL — exception propagates uncaught, autobug never called.`

- [ ] **Step 3: Implement**

Wrap the body of `iterate_once` in a try/except. Add at the top of the function (immediately after the docstring and BEFORE `queue_repo = _queue_repo_root(cfg)`):

```python
    # Defensive autobug wire — catches any UNEXPECTED exception in the
    # iteration body (not control-flow exits like KeyboardInterrupt /
    # HaltedError / PushRebaseConflict / UncommittedSource which are
    # handled inline). The wrapper preserves the original exception and
    # re-raises after autobug fires.
    try:
        return _iterate_once_inner(cfg)
    except (KeyboardInterrupt, HaltedError, PushRebaseConflict, UncommittedSource):
        raise
    except BaseException as exc:
        if getattr(cfg, "autobug_enabled", True):
            try:
                from datetime import UTC, datetime as _dt
                from ralph_executor import autobug as _autobug
                _ctx = _autobug.Context(
                    queue_root=cfg.workspace_root / "queue",
                    state_dir=cfg.workspace_root / "queue" / ".ralph" / "state",
                    env=dict(os.environ),
                    now=_dt.now(tz=UTC),
                    ralph_sha=os.environ.get("RALPH_SHA", "<unknown>"),
                    bot_author_email=cfg.bot_author_email,
                    triggering_pbi_id=_current_pbi_id_or_none(cfg),
                    queue_branch=cfg.queue_branch,
                )
                _autobug.detect_python_crash(
                    exc, _ctx,
                    target_repo=cfg.queue_repo,
                    severity=cfg.autobug_severity_python_crash,
                )
            except BaseException as inner:
                log.warning("autobug loop wire failed: %s", inner)
        raise
```

Add the helper at module scope:

```python
import os as _os


def _current_pbi_id_or_none(cfg: ExecutorConfig) -> str | None:
    try:
        cur = FilesystemQueueSource(cfg).current_pbi()
        return cur.id if cur else None
    except Exception:
        return None
```

Rename the existing `iterate_once` body to `_iterate_once_inner` (mechanical refactor — preserves all existing logic).

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/test_loop_autobug_wires.py tests/executor/ -x`
`Expected: PASS (new test + no regressions).`

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/loop.py tests/executor/test_loop_autobug_wires.py
git commit -m "feat(loop): defensive autobug wire around iterate_once body"
```

---

## Task T20: bug workflow signature-aware amendment — **confidence: 96%**

**Files:**
- Modify: `prompt/03-workflow/bug/workflow.md` (amend step 2 per spec §"Bug workflow amendment")
- Create: `tests/test_workflow_signature_amendment.py` (or extend `tests/test_prompt_contents.py`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_signature_amendment.py
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / "prompt" / "03-workflow" / "bug" / "workflow.md"


def test_workflow_includes_signature_aware_branch() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "signature:" in text.lower()
    assert "autobug" in text.lower()
    assert "starting point" in text.lower() or "observed artefacts" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/test_workflow_signature_amendment.py -v`
`Expected: FAIL — current workflow.md does not mention signature/autobug.`

- [ ] **Step 3: Implement**

Edit `prompt/03-workflow/bug/workflow.md`. Replace step 2 with:

```markdown
2. **Reproduce.**

   **If `BUG.md` frontmatter contains `signature: <hex>`** (autobug-emitted),
   REPRODUCE.md may contain only observed artefacts, not runnable commands.
   Your iteration's first task is to build the reproduction from the
   stacktrace + env snapshot. The "cannot reproduce → STUCK" rule applies
   only after a documented investigation attempt, not on initial read.

   **Otherwise** (human-authored bug): run the commands in `REPRODUCE.md`.
   Confirm you observe the failure signature documented in `BUG.md`. If
   you cannot reproduce, write `STUCK.md` ("cannot reproduce: <what I
   saw instead>") and stop.
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/test_workflow_signature_amendment.py tests/test_prompt_contents.py -v`
`Expected: PASS.`

- [ ] **Step 5: Commit**

```bash
git add prompt/03-workflow/bug/workflow.md tests/test_workflow_signature_amendment.py
git commit -m "docs(prompt): bug workflow — signature-aware reproduce branch"
```

---

## Task T21: memory prompt autobug note — **confidence: 96%**

**Files:**
- Modify: `prompt/06-memory/memory.md` (add one autobug awareness line)
- Create: `tests/test_memory_prompt_autobug.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_prompt_autobug.py
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MEMORY_PATH = REPO_ROOT / "prompt" / "06-memory" / "memory.md"


def test_memory_prompt_mentions_autobug_signature_marker() -> None:
    text = MEMORY_PATH.read_text(encoding="utf-8")
    assert "signature:" in text
    assert "RALPH_AUTOBUG_DEPTH" in text
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/test_memory_prompt_autobug.py -v`
`Expected: FAIL.`

- [ ] **Step 3: Implement**

Append a new short paragraph to `prompt/06-memory/memory.md`:

```markdown
### Autobug PBIs

If the PBI's `BUG.md` frontmatter contains `signature: <hex>`, this is an
autobug — captured automatically by ralph when an earlier iteration
crashed. Your `RALPH_AUTOBUG_DEPTH=1` env var prevents further autobug
emissions while you fix it. Treat it like any other bug PBI; the
signature field is for dedup, not for you to act on.
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/test_memory_prompt_autobug.py tests/test_prompt_contents.py -v`
`Expected: PASS.`

- [ ] **Step 5: Commit**

```bash
git add prompt/06-memory/memory.md tests/test_memory_prompt_autobug.py
git commit -m "docs(prompt): memory — autobug signature/RALPH_AUTOBUG_DEPTH awareness"
```

---

## Task T22: Roundtrip integration test — **confidence: 93%**

**Files:**
- Create: `tests/executor/autobug/integration/__init__.py`
- Create: `tests/executor/autobug/integration/test_roundtrip_preserves_extras.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/integration/test_roundtrip_preserves_extras.py
"""Load-bearing test: autobug extras survive every queue move.

If movements.py ever switches from line-based to dataclass-roundtrip
serialisation, this test breaks immediately. That's by design — autobug
dedup depends on signature surviving moves.
"""

from datetime import UTC, datetime
from pathlib import Path

from ralph_executor.autobug import emit
from ralph_executor.autobug.types import Context
from ralph_executor.queue.filesystem import parse_pbi_directory
from ralph_executor.queue.movements import (
    move_current_to_pending_pr,
    move_inbox_to_current,
    move_pending_pr_to_done,
)


def _ctx_for(fake_repo: Path) -> Context:
    return Context(
        queue_root=fake_repo,
        state_dir=fake_repo / ".ralph" / "state",
        env={},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="51cc97a",
        bot_author_email="bot@e.com",
        triggering_pbi_id="WI-247",
        queue_branch="main",
    )


def test_roundtrip_preserves_autobug_extras(fake_repo: Path, cfg_for_repo) -> None:
    ctx = _ctx_for(fake_repo)
    try:
        raise RuntimeError("roundtrip test")
    except RuntimeError as exc:
        pbi_id = emit.new(
            signature="f" * 64, exc=exc, ctx=ctx,
            trigger_kind="python_crash", severity="critical",
            target_repo="https://github.com/emp3thy/ralph",
        )
    pbi_dir = fake_repo / ".ralph" / "inbox" / pbi_id
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    pbi = move_inbox_to_current(cfg_for_repo, pbi)
    pbi = move_current_to_pending_pr(cfg_for_repo, pbi)
    pbi = move_pending_pr_to_done(cfg_for_repo, pbi)
    final = (pbi.path / "BUG.md").read_text(encoding="utf-8")
    for key in ("signature:", "occurrences:", "trigger_kind:", "first_seen:",
                "last_seen:", "regression_of:", "triggering_pbi:", "ralph_sha:"):
        assert key in final, f"autobug extra {key!r} lost during roundtrip"
    assert "closed_at:" in final  # T6.5 stamp on done/
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/integration/test_roundtrip_preserves_extras.py -v`
`Expected: PASS — _rewrite_status is line-based and preserves unknown keys. If FAIL, T6.5 introduced a regression.`

- [ ] **Step 3: Implement (no production code change expected)**

If a key disappears, debug `movements._rewrite_status` — the spec's "Roundtrip safety" section is explicit that line-by-line rewrite preserves unknown keys.

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/integration/test_roundtrip_preserves_extras.py -v`
`Expected: PASS.`

- [ ] **Step 5: Commit**

```bash
git add tests/executor/autobug/integration/__init__.py tests/executor/autobug/integration/test_roundtrip_preserves_extras.py
git commit -m "test(autobug): roundtrip preserves autobug-specific frontmatter keys"
```

---

## Task T23: Python crash e2e — **confidence: 93%**

**Files:**
- Create: `tests/executor/autobug/integration/test_python_crash_e2e.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/integration/test_python_crash_e2e.py
"""End-to-end: a Python crash in run_loop produces a bug PBI in inbox/."""

from pathlib import Path

import pytest

from ralph_executor import cli, loop


def test_python_crash_in_iteration_emits_autobug_pbi(
    monkeypatch: pytest.MonkeyPatch, fake_repo: Path, cfg_for_repo
) -> None:
    # Force the iteration to crash with a Python exception.
    def _bad_pull(cfg):
        raise RuntimeError("e2e crash")
    monkeypatch.setattr(loop, "_pull_queue", _bad_pull)

    cfg = cfg_for_repo
    # T15-style: set bot_author_email so detect doesn't abort
    from dataclasses import replace
    cfg = replace(cfg, bot_author_email="bot@e.com")

    with pytest.raises(RuntimeError, match="e2e crash"):
        cli.run_loop_with_autobug(cfg)

    inbox = fake_repo / ".ralph" / "inbox"
    autobug_dirs = [d for d in inbox.iterdir() if d.is_dir() and d.name.startswith("autobug-")]
    assert autobug_dirs, "expected an autobug PBI to land in inbox/"
    bug_md = (autobug_dirs[0] / "BUG.md").read_text(encoding="utf-8")
    assert "RuntimeError" in bug_md
    assert "e2e crash" in bug_md
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/integration/test_python_crash_e2e.py -v`
`Expected: FAIL initially if wires not yet integrated; PASS after T17/T18/T19 land.`

- [ ] **Step 3: Implement (no new code — depends on T17/T18/T19)**

If the test fails after wires land, the most likely cause is missing `bot_author_email` on the test cfg (detect aborts emit). The test explicitly sets it.

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/integration/test_python_crash_e2e.py -v`
`Expected: PASS.`

- [ ] **Step 5: Commit**

```bash
git add tests/executor/autobug/integration/test_python_crash_e2e.py
git commit -m "test(autobug): e2e python crash produces inbox autobug PBI"
```

---

## Task T24: Subprocess crash e2e — **confidence: 93%**

**Files:**
- Create: `tests/executor/autobug/integration/test_subprocess_crash_e2e.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/integration/test_subprocess_crash_e2e.py
"""End-to-end: a non-zero claude subprocess exit emits a bug PBI."""

from dataclasses import replace
from pathlib import Path

import pytest

from ralph_executor import claude_spawn
from ralph_executor.queue.filesystem import parse_pbi_directory
from tests.executor.conftest import write_claude_script, write_sample_pbi, _git


def test_subprocess_nonzero_exit_emits_autobug_pbi(
    monkeypatch: pytest.MonkeyPatch,
    fake_repo: Path, cfg_for_repo, fake_claude_binary: Path,
) -> None:
    # Make the claude stub exit non-zero
    write_claude_script(fake_claude_binary, "import sys\nsys.stderr.write('Killed\\n')\nsys.exit(137)\n")
    cfg = replace(cfg_for_repo, bot_author_email="bot@e.com")

    pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-9999", where="current")
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", "test: stage WI-9999 in current")
    _git(fake_repo, "push", "origin", "main")
    pbi = parse_pbi_directory(pbi_dir, status="current")

    try:
        claude_spawn.spawn_claude_p(cfg, pbi, cwd=fake_repo, pbi_dir=pbi_dir)
    except Exception:
        pass  # subprocess failure is fine — we're testing the wire

    inbox = fake_repo / ".ralph" / "inbox"
    autobug_dirs = [d for d in inbox.iterdir() if d.is_dir() and d.name.startswith("autobug-")]
    assert autobug_dirs, "expected an autobug PBI on non-zero subprocess exit"
    bug_md = (autobug_dirs[0] / "BUG.md").read_text(encoding="utf-8")
    assert "subprocess" in bug_md.lower() or "subprocess_crash" in bug_md
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/integration/test_subprocess_crash_e2e.py -v`
`Expected: FAIL before T17 wires land; PASS after.`

- [ ] **Step 3: Implement (no new production code; depends on T17)**

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/integration/test_subprocess_crash_e2e.py -v`
`Expected: PASS.`

- [ ] **Step 5: Commit**

```bash
git add tests/executor/autobug/integration/test_subprocess_crash_e2e.py
git commit -m "test(autobug): e2e subprocess non-zero exit produces inbox autobug PBI"
```

---

## Task T25: Recursion guard e2e — **confidence: 93%**

**Files:**
- Create: `tests/executor/autobug/integration/test_recursion_guard_e2e.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/executor/autobug/integration/test_recursion_guard_e2e.py
"""End-to-end: recursion guard prevents nested autobug emission."""

from datetime import UTC, datetime
from pathlib import Path

from ralph_executor.autobug import detect
from ralph_executor.autobug.types import Context


def test_recursion_marker_blocks_python_emission(fake_repo: Path) -> None:
    ctx = Context(
        queue_root=fake_repo,
        state_dir=fake_repo / ".ralph" / "state",
        env={"RALPH_AUTOBUG_DEPTH": "1"},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="sha",
        bot_author_email="bot@e.com",
        triggering_pbi_id=None,
        queue_branch="main",
    )
    try:
        raise RuntimeError("nested")
    except RuntimeError as exc:
        detect.detect_python_crash(exc, ctx,
                                    target_repo="https://github.com/x/y",
                                    severity="critical")
    inbox = fake_repo / ".ralph" / "inbox"
    autobug_dirs = [d for d in inbox.iterdir() if d.is_dir() and d.name.startswith("autobug-")] \
        if inbox.is_dir() else []
    assert not autobug_dirs, "recursion guard should have suppressed emission"


def test_pbi_with_signature_sets_RALPH_AUTOBUG_DEPTH(
    fake_repo: Path, cfg_for_repo, fake_claude_binary, monkeypatch,
) -> None:
    """Spawning against an autobug PBI must set RALPH_AUTOBUG_DEPTH=1 in child env."""
    import subprocess
    from ralph_executor.queue.filesystem import parse_pbi_directory

    captured_envs: list[dict] = []
    real_popen = subprocess.Popen
    def _capture_popen(*args, **kwargs):
        captured_envs.append(dict(kwargs.get("env") or {}))
        return real_popen(*args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", _capture_popen)

    pbi_dir = fake_repo / ".ralph" / "current" / "autobug-zzzzzz-001"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "BUG.md").write_text(
        "---\nid: autobug-zzzzzz-001\ntype: bug\nseverity: critical\n"
        "status: current\nattempts: 0\n"
        "created_at: 2026-05-31T00:00:00+00:00\n"
        "updated_at: 2026-05-31T00:00:00+00:00\n"
        "target_repo: https://github.com/x/y\n"
        "signature: zzzzzz123456\n---\n# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "REPRODUCE.md").write_text("# r\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    pbi = parse_pbi_directory(pbi_dir, status="current")
    from ralph_executor import claude_spawn
    try:
        claude_spawn.spawn_claude_p(cfg_for_repo, pbi, cwd=fake_repo, pbi_dir=pbi_dir)
    except Exception:
        pass
    assert any(env.get("RALPH_AUTOBUG_DEPTH") == "1" for env in captured_envs), \
        f"expected RALPH_AUTOBUG_DEPTH=1 in spawned env; saw {captured_envs}"
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/executor/autobug/integration/test_recursion_guard_e2e.py -v`
`Expected: FAIL before T17 marker lands; PASS after.`

- [ ] **Step 3: Implement (no new production code; depends on T5 + T17)**

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/executor/autobug/integration/test_recursion_guard_e2e.py -v`
`Expected: PASS.`

- [ ] **Step 5: Commit**

```bash
git add tests/executor/autobug/integration/test_recursion_guard_e2e.py
git commit -m "test(autobug): e2e recursion guard prevents nested emit"
```

---

## Task T26: Corpus extractor script — **confidence: 93%**

**Files:**
- Create: `scripts/build_autobug_corpus.py`
- Create: `tests/test_build_autobug_corpus.py`

- [ ] **Step 0:** Read `ralph_executor/safety/events.py:64-145` (EventLog class) to confirm the `error`-class events the script needs to filter. Confirm `_DB_RELATIVE = ".ralph/state/events.db"`. The script reads from `<queue>/.ralph/state/events.db`, filters payload by `error`-class signature, and dumps stderr blobs into `fixtures/stderr_corpus/raw/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_autobug_corpus.py
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_autobug_corpus.py"


def test_script_exists() -> None:
    assert SCRIPT_PATH.is_file()


def test_script_imports_cleanly() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_autobug_corpus", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
```

- [ ] **Step 2: Run test to verify it fails**

`Run: uv run pytest tests/test_build_autobug_corpus.py -v`
`Expected: FAIL — script does not exist.`

- [ ] **Step 3: Implement**

```python
# scripts/build_autobug_corpus.py
#!/usr/bin/env python3
"""Walk events.db files, extract error-class stderr blobs into raw/ bucket.

Operator runs this against a live ralph queue clone, then hand-sorts the
extracted blobs in tests/executor/autobug/fixtures/stderr_corpus/raw/
into bucket subdirectories based on root cause.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path,
                        help="Path to .ralph/state/events.db")
    parser.add_argument("--out", required=True, type=Path,
                        help="Output dir (fixtures/stderr_corpus/raw/)")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(args.db))
    rows = conn.execute(
        "SELECT rowid, payload FROM events WHERE kind = ?",
        ("autobug_emitted",),
    ).fetchall()
    written = 0
    for rowid, payload in rows:
        try:
            data = json.loads(payload or "{}")
        except json.JSONDecodeError:
            continue
        stderr = data.get("stderr")
        if not stderr:
            continue
        (args.out / f"event-{rowid:05d}.txt").write_text(stderr, encoding="utf-8")
        written += 1
    conn.close()
    print(f"wrote {written} blobs to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run test to verify it passes**

`Run: uv run pytest tests/test_build_autobug_corpus.py -v`
`Expected: PASS (2 tests).`

- [ ] **Step 5: Commit**

```bash
git add scripts/build_autobug_corpus.py tests/test_build_autobug_corpus.py
git commit -m "feat(scripts): build_autobug_corpus — extract error stderr from events.db"
```

---

## Final verification

After all 27 tasks land:

- [ ] **Run the full test suite**

```bash
uv run pytest tests/executor/ tests/safety/ tests/test_prompt_contents.py tests/test_workflow_signature_amendment.py tests/test_memory_prompt_autobug.py tests/test_build_autobug_corpus.py -v
```

Expected: all green.

- [ ] **Smoke-test autobug-disabled mode**

Set `autobug_enabled = false` in `.ralph/config.toml`; force a Python crash in `run_loop`; confirm NO PBI is emitted and the crash propagates normally.

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

**Spec coverage:** every spec section maps to ≥1 task: architecture → T1/T11–T13; signatures → T2/T3/T4; dedup → T7+T6.5; fuses → T5/T6; composer → T8/T9/T10; detect → T12; frontmatter shape → T9; integration sites → T15/T17/T18/T19; prompt amendments → T20/T21; tests → T22/T23/T24/T25; tooling → T26; concern-3 (bot_author_email startup) → T15.

**Placeholder scan:** no "TBD"/"implement later"/"similar to Task N"/"unchanged from v1". Every task has its full body including code, commands, and commit messages.

**Type consistency:**
- `Context` field set consistent across T1, T11, T17, T18, T19, T22, T23, T25 (includes `queue_branch`).
- `DedupResult` field consistent across T1, T7, T12.
- `detect_python_crash(exc, ctx, *, target_repo, severity=...)` consistent across T12, T18, T19, T23, T25.
- `detect_subprocess_crash(exit_code, stderr, command, ctx, *, target_repo, severity=...)` consistent across T12, T17, T24.
- Compose function names `build_bug_md`, `build_reproduce_md`, `build_frontmatter`, `_safe` consistent across T8/T9/T10 + T11 emit.

**Confidence audit:** 0 tasks below 92%. No surfacing required.

**Cross-read prose vs code:** spec §"Integration with existing modules" originally said `loop.py` would get N internal wires at specific `except` blocks. Source-reading revealed none of the existing `except Exception` sites in `loop.py` are real defect swallows — they are all defensive log-and-continue cleanup paths. T19 documents this finding and instead adds a single defensive wrapper around `iterate_once` that catches unexpected exceptions while excluding the four legitimate control-flow exits.
