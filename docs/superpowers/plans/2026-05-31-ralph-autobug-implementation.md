# ralph autobug v1 — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the ralph executor or its Claude subprocess crashes, automatically file a deduplicated bug PBI to the ralph queue capturing the crash, so future iterations work the fix without operator hand-filing.

**Architecture:** A new `ralph_executor/autobug/` package mirrors the shape of `ralph_executor/sweep/`. Two public functions (`detect_python_crash`, `detect_subprocess_crash`) are called from three hook sites (`cli.py` outer try/except, `claude_spawn` error classifier, internal `loop.py` wires). Each emission flows through signature hashing → recursion guard → dedup lookup → rate-limit check → BUG.md/REPRODUCE.md composition → queue write + push. All emission is no-op-safe: a failure inside autobug never propagates back to the executor.

**Tech Stack:** Python 3.12, pytest, hashlib (stdlib SHA-256), existing `ralph_executor.git_ops` + `queue.movements` helpers, existing `prompt/0N-*/*.md` topic-folder prompt system. No new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-05-31-ralph-autobug-design.md` (commit `afcd813`).

---

## Phase 1 — Package scaffolding and signatures

### Task 1: Scaffold the `autobug` package

**Files:**
- Create: `ralph_executor/autobug/__init__.py`
- Create: `ralph_executor/autobug/context.py`
- Create: `tests/executor/autobug/__init__.py`
- Create: `tests/executor/autobug/conftest.py`
- Create: `tests/executor/autobug/test_context.py`

- [ ] **Step 1: Write the failing test for `Context` shape**

`tests/executor/autobug/test_context.py`:

```python
"""Tests for autobug.context shapes."""
from datetime import datetime, timezone
from pathlib import Path

from ralph_executor.autobug.context import Context, DedupResult


def test_context_is_frozen_dataclass():
    ctx = Context(
        queue_root=Path("/tmp/q"),
        state_dir=Path("/tmp/q/.ralph/state"),
        env={"FOO": "bar"},
        now=datetime(2026, 5, 31, tzinfo=timezone.utc),
        ralph_sha="abc1234",
        bot_author_email="bot@example.com",
        triggering_pbi_id="WI-1",
    )
    assert ctx.queue_root == Path("/tmp/q")
    # frozen — mutation must raise
    import dataclasses
    try:
        ctx.queue_root = Path("/tmp/other")  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("Context must be a frozen dataclass")


def test_context_triggering_pbi_id_optional():
    ctx = Context(
        queue_root=Path("/tmp/q"),
        state_dir=Path("/tmp/q/.ralph/state"),
        env={},
        now=datetime(2026, 5, 31, tzinfo=timezone.utc),
        ralph_sha="abc1234",
        bot_author_email="bot@example.com",
    )
    assert ctx.triggering_pbi_id is None


def test_dedup_result_kind_values():
    new = DedupResult(kind="new")
    assert new.existing_pbi_id is None
    bump = DedupResult(kind="bump_existing", existing_pbi_id="autobug-abc-001")
    assert bump.existing_pbi_id == "autobug-abc-001"
    reopen = DedupResult(kind="reopen_regression", existing_pbi_id="autobug-abc-001")
    assert reopen.kind == "reopen_regression"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/executor/autobug/test_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ralph_executor.autobug'`

- [ ] **Step 3: Write minimal `__init__.py` + `context.py`**

`ralph_executor/autobug/__init__.py`:

```python
"""Autobug: capture executor and Claude-subprocess crashes as bug PBIs.

See ``docs/superpowers/specs/2026-05-31-ralph-autobug-design.md`` for
the design rationale and contracts.

Public API (added in later tasks):
- detect_python_crash
- detect_subprocess_crash
"""
```

`ralph_executor/autobug/context.py`:

```python
"""Immutable shapes carried through the autobug call chain."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Context:
    """Frozen snapshot of the executor state needed for autobug emission.

    Built once at hook-site call time. Never mutated. ``now`` is passed
    in (not read from ``datetime.now()``) so tests can freeze time.
    """

    queue_root: Path
    state_dir: Path
    env: Mapping[str, str]
    now: datetime
    ralph_sha: str
    bot_author_email: str
    triggering_pbi_id: str | None = None


@dataclass(frozen=True)
class DedupResult:
    """Outcome of a dedup.lookup() call.

    ``kind="new"`` → emit a fresh PBI.
    ``kind="bump_existing"`` → update ``existing_pbi_id`` in place (bump occurrences).
    ``kind="reopen_regression"`` → emit a fresh PBI with ``regression_of=existing_pbi_id``.
    """

    kind: Literal["new", "bump_existing", "reopen_regression"]
    existing_pbi_id: str | None = None
```

`tests/executor/autobug/__init__.py`:

```python
```

`tests/executor/autobug/conftest.py`:

```python
"""Shared fixtures for autobug tests.

Populated incrementally as later tasks need fixtures.
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/executor/autobug/test_context.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/__init__.py ralph_executor/autobug/context.py tests/executor/autobug/
git commit -m "feat(autobug): scaffold package with Context + DedupResult dataclasses"
```

---

### Task 2: Python-crash signature

**Files:**
- Create: `ralph_executor/autobug/signature.py`
- Create: `tests/executor/autobug/test_signature.py`

- [ ] **Step 1: Write failing tests for `python_crash_signature`**

`tests/executor/autobug/test_signature.py`:

```python
"""Tests for signature hashing."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ralph_executor.autobug import signature


def _raise_in(filename_fragment: str, func_name: str):
    """Helper that raises KeyError from a stack frame we can identify by filename."""
    raise KeyError(f"missing key in {filename_fragment}::{func_name}")


def test_python_crash_signature_is_sha256_hex():
    try:
        _raise_in("ralph_executor/loop.py", "claim_next_pbi")
    except KeyError as exc:
        sig = signature.python_crash_signature(exc)
    assert isinstance(sig, str)
    assert len(sig) == 64
    int(sig, 16)  # hex


def test_python_crash_signature_deterministic():
    sigs = []
    for _ in range(2):
        try:
            raise ValueError("same call site")
        except ValueError as exc:
            sigs.append(signature.python_crash_signature(exc))
    assert sigs[0] == sigs[1]


def test_python_crash_signature_differs_by_exception_class():
    try:
        raise ValueError("x")
    except ValueError as exc:
        sig_v = signature.python_crash_signature(exc)
    try:
        raise KeyError("x")
    except KeyError as exc:
        sig_k = signature.python_crash_signature(exc)
    assert sig_v != sig_k


def test_python_crash_signature_differs_by_line():
    try:
        raise ValueError("first")
    except ValueError as exc:
        sig_a = signature.python_crash_signature(exc)
    try:
        raise ValueError("second")
    except ValueError as exc:
        sig_b = signature.python_crash_signature(exc)
    assert sig_a != sig_b
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/executor/autobug/test_signature.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ralph_executor.autobug.signature'`

- [ ] **Step 3: Implement `python_crash_signature`**

`ralph_executor/autobug/signature.py`:

```python
"""Stable signatures for Python and subprocess crashes.

The signature is the dedup key: two crashes with the same signature are
treated as the same bug. See the spec's Signature computation section
for the rationale.
"""

from __future__ import annotations

import hashlib
import types
from pathlib import PurePath

_RALPH_PKG = "ralph_executor"


def _is_user_frame(filename: str) -> bool:
    """True if the frame lives under ralph_executor/ (not stdlib/site-packages)."""
    parts = PurePath(filename).parts
    return _RALPH_PKG in parts


def _walk_frames(tb: types.TracebackType | None):
    """Yield FrameSummary-like (filename, name, lineno) from a traceback in order."""
    while tb is not None:
        f = tb.tb_frame
        yield (f.f_code.co_filename, f.f_code.co_name, tb.tb_lineno)
        tb = tb.tb_next


def _two_deepest_user_frames(
    tb: types.TracebackType | None,
) -> tuple[tuple[str, str, int], tuple[str, str, int] | None]:
    """Return (throw, caller) frames, both under ralph_executor/.

    Walks the full traceback, keeps every user frame, returns the last
    two (deepest in the call stack). If only one user frame exists,
    caller is None. If none exist, falls back to the deepest stdlib
    frame as throw with caller=None.
    """
    all_frames = list(_walk_frames(tb))
    user = [f for f in all_frames if _is_user_frame(f[0])]
    if len(user) >= 2:
        return user[-1], user[-2]
    if len(user) == 1:
        return user[-1], None
    if all_frames:
        return all_frames[-1], None
    return ("<unknown>", "<unknown>", 0), None


def python_crash_signature(exc: BaseException) -> str:
    """SHA-256 hex of '<ExcClass>:<throw>|<caller>'."""
    throw, caller = _two_deepest_user_frames(exc.__traceback__)
    throw_part = f"{throw[0]}:{throw[1]}:{throw[2]}"
    caller_part = (
        f"{caller[0]}:{caller[1]}:{caller[2]}" if caller is not None else "<none>"
    )
    payload = f"{type(exc).__qualname__}:{throw_part}|{caller_part}"
    return hashlib.sha256(payload.encode()).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/executor/autobug/test_signature.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/signature.py tests/executor/autobug/test_signature.py
git commit -m "feat(autobug): python_crash_signature with throw+caller frames"
```

---

### Task 3: Subprocess-crash signature + `_normalise`

**Files:**
- Modify: `ralph_executor/autobug/signature.py`
- Modify: `tests/executor/autobug/test_signature.py`

- [ ] **Step 1: Write failing tests for `subprocess_crash_signature` and `_normalise`**

Append to `tests/executor/autobug/test_signature.py`:

```python
# --- subprocess signature tests ---

def test_subprocess_crash_signature_deterministic():
    sig_a = signature.subprocess_crash_signature(1, "boom\n")
    sig_b = signature.subprocess_crash_signature(1, "boom\n")
    assert sig_a == sig_b


def test_subprocess_crash_signature_uses_exit_code():
    sig_1 = signature.subprocess_crash_signature(1, "boom")
    sig_2 = signature.subprocess_crash_signature(2, "boom")
    assert sig_1 != sig_2


def test_subprocess_crash_signature_uses_last_nonempty_line():
    sig_a = signature.subprocess_crash_signature(1, "warning x\nERROR boom")
    sig_b = signature.subprocess_crash_signature(1, "info y\nwarning z\nERROR boom\n\n")
    assert sig_a == sig_b


def test_subprocess_crash_signature_normalises_paths():
    sig_a = signature.subprocess_crash_signature(1, "FileNotFoundError: /tmp/abc/foo")
    sig_b = signature.subprocess_crash_signature(1, "FileNotFoundError: /tmp/xyz/foo")
    assert sig_a == sig_b


def test_subprocess_crash_signature_normalises_uuid():
    sig_a = signature.subprocess_crash_signature(1, "req 11111111-1111-1111-1111-111111111111 failed")
    sig_b = signature.subprocess_crash_signature(1, "req 22222222-2222-2222-2222-222222222222 failed")
    assert sig_a == sig_b


def test_subprocess_crash_signature_normalises_iso_timestamp():
    sig_a = signature.subprocess_crash_signature(1, "2026-05-31T10:00:00 boom")
    sig_b = signature.subprocess_crash_signature(1, "2026-05-31T11:00:00 boom")
    assert sig_a == sig_b


def test_subprocess_crash_signature_normalises_sha():
    sig_a = signature.subprocess_crash_signature(1, "fatal: bad object abc1234")
    sig_b = signature.subprocess_crash_signature(1, "fatal: bad object def5678")
    assert sig_a == sig_b


def test_subprocess_crash_signature_distinguishes_different_messages():
    sig_a = signature.subprocess_crash_signature(1, "FileNotFoundError: /tmp/foo")
    sig_b = signature.subprocess_crash_signature(1, "PermissionError: denied")
    assert sig_a != sig_b


def test_normalise_preserves_tokens_inline():
    out = signature._normalise("error /tmp/foo at line 42")
    assert "<PATH>" in out
    assert ":line <N>" in out or "at line <N>" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/executor/autobug/test_signature.py -v`
Expected: FAIL — `subprocess_crash_signature` not defined, `_normalise` not defined.

- [ ] **Step 3: Implement `_normalise` and `subprocess_crash_signature`**

Append to `ralph_executor/autobug/signature.py`:

```python
# --- subprocess crashes ---

import re

_NORMALISE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 1. UUIDs
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "<UUID>"),
    # 2. ISO timestamps
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"), "<TS>"),
    # 3. Epoch seconds (10+ digits)
    (re.compile(r"\b\d{10,}\b"), "<EPOCH>"),
    # 4. Absolute paths (Windows + POSIX). Match a drive-letter or leading slash, then non-space.
    (re.compile(r"(?:[A-Za-z]:\\|/)[^\s:]+"), "<PATH>"),
    # 5. Per-PBI branch names
    (re.compile(r"ralph/[A-Z0-9][A-Z0-9_-]*"), "<BRANCH>"),
    # 6. PBI IDs (WI-N + autobug-*)
    (re.compile(r"\b(?:WI-\d+|autobug-[a-f0-9]+-\d+)\b"), "<PBI>"),
    # 7. Git commit SHAs (7-40 hex)
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "<SHA>"),
    # 8. Hex memory addresses
    (re.compile(r"0x[0-9a-f]+"), "<ADDR>"),
    # 9. Line-number suffixes
    (re.compile(r"(?::line |at line )\d+"), lambda m: m.group(0).split()[0] + " <N>"),
]


def _normalise(line: str) -> str:
    """Apply normalisation patterns in declaration order.

    Order is load-bearing: UUIDs / timestamps / paths / branches must run
    BEFORE the 7-40-hex SHA pattern, which would otherwise eat their
    leading hex.
    """
    out = line
    for pat, repl in _NORMALISE_PATTERNS:
        if callable(repl):
            out = pat.sub(repl, out)
        else:
            out = pat.sub(repl, out)
    return out


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def subprocess_crash_signature(exit_code: int, stderr: str) -> str:
    """SHA-256 hex of '<exit_code>:<normalised last stderr line>'."""
    last_line = _last_nonempty_line(stderr)
    normalised = _normalise(last_line)
    payload = f"{exit_code}:{normalised}"
    return hashlib.sha256(payload.encode()).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/executor/autobug/test_signature.py -v`
Expected: PASS (13 tests total now)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/signature.py tests/executor/autobug/test_signature.py
git commit -m "feat(autobug): subprocess_crash_signature + _normalise pattern set"
```

---

### Task 4: Signature corpus regression-test scaffold

**Files:**
- Create: `tests/executor/autobug/test_signature_corpus.py`
- Create: `tests/executor/autobug/fixtures/stderr_corpus/synthetic_oom/sample.txt`
- Create: `tests/executor/autobug/fixtures/stderr_corpus/synthetic_filenotfound/sample.txt`
- Create: `tests/executor/autobug/fixtures/stderr_corpus/synthetic_filenotfound/sample2.txt`

- [ ] **Step 1: Write the corpus fixtures**

`tests/executor/autobug/fixtures/stderr_corpus/synthetic_oom/sample.txt`:

```
Killed (OOM)
exit code: 137
```

`tests/executor/autobug/fixtures/stderr_corpus/synthetic_filenotfound/sample.txt`:

```
2026-05-31T10:00:00 Traceback (most recent call last):
  File "/tmp/run-aaa/script.py", line 42, in main
FileNotFoundError: /tmp/run-aaa/missing
```

`tests/executor/autobug/fixtures/stderr_corpus/synthetic_filenotfound/sample2.txt`:

```
2026-05-31T11:30:00 Traceback (most recent call last):
  File "/tmp/run-bbb/script.py", line 42, in main
FileNotFoundError: /tmp/run-bbb/missing
```

- [ ] **Step 2: Write failing corpus regression test**

`tests/executor/autobug/test_signature_corpus.py`:

```python
"""Real-stderr corpus regression test.

Bucket directories under fixtures/stderr_corpus/ each contain one or
more .txt files representing distinct captures of the SAME underlying
bug. The test asserts:

  1. All files within a bucket hash to the same signature.
  2. Files across different buckets hash to different signatures.

The shipped fixtures are synthetic placeholders. Operators run
``scripts/build_autobug_corpus.py`` to extract real stderr blobs from
local events.db files and curate them into buckets — see the spec's
Rollout posture section.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.autobug import signature

FIXTURES = Path(__file__).parent / "fixtures" / "stderr_corpus"


def _read_buckets() -> dict[str, list[str]]:
    """Return ``{bucket_name: [stderr_text, ...]}`` for every non-empty bucket."""
    out: dict[str, list[str]] = {}
    if not FIXTURES.is_dir():
        return out
    for bucket in sorted(FIXTURES.iterdir()):
        if not bucket.is_dir():
            continue
        files = sorted(bucket.glob("*.txt"))
        if not files:
            continue
        out[bucket.name] = [f.read_text(encoding="utf-8") for f in files]
    return out


def _sig_for(text: str) -> str:
    # exit_code=1 is a stand-in; the corpus exercises the normaliser, not the exit
    return signature.subprocess_crash_signature(1, text)


@pytest.mark.parametrize("bucket,blobs", list(_read_buckets().items()))
def test_bucket_collapses_to_one_signature(bucket: str, blobs: list[str]) -> None:
    if len(blobs) < 2:
        pytest.skip(f"bucket {bucket!r} has only one sample; nothing to collapse")
    sigs = {_sig_for(b) for b in blobs}
    assert len(sigs) == 1, (
        f"bucket {bucket!r} produced {len(sigs)} signatures; "
        f"expected 1. Tune the normaliser pattern set."
    )


def test_buckets_have_distinct_signatures() -> None:
    buckets = _read_buckets()
    if len(buckets) < 2:
        pytest.skip("need >=2 buckets to assert cross-bucket distinction")
    seen: dict[str, str] = {}
    for name, blobs in buckets.items():
        for blob in blobs:
            sig = _sig_for(blob)
            if sig in seen and seen[sig] != name:
                raise AssertionError(
                    f"bucket {name!r} collides with bucket {seen[sig]!r} on signature {sig!r}"
                )
            seen[sig] = name
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/executor/autobug/test_signature_corpus.py -v`
Expected: PASS — the `synthetic_filenotfound` bucket has two blobs whose paths/timestamps normalise away, so they collapse to one signature. The `synthetic_oom` bucket has only one sample → that parametrise entry skips. Cross-bucket distinction passes because OOM has a different exit-code-independent shape than FileNotFoundError.

- [ ] **Step 4: Commit**

```bash
git add tests/executor/autobug/test_signature_corpus.py tests/executor/autobug/fixtures/
git commit -m "feat(autobug): signature corpus regression test + synthetic fixtures"
```

---

## Phase 2 — Stateful helpers (dedup + fuses)

### Task 5: Recursion guard

**Files:**
- Create: `ralph_executor/autobug/fuses.py`
- Create: `tests/executor/autobug/test_fuses.py`

- [ ] **Step 1: Write failing tests**

`tests/executor/autobug/test_fuses.py`:

```python
"""Tests for autobug.fuses (recursion guard + rate limit + rollup)."""
from __future__ import annotations

from ralph_executor.autobug import fuses


def test_recursion_check_passes_when_depth_absent():
    assert fuses.recursion_check({}) is True


def test_recursion_check_passes_when_depth_zero():
    assert fuses.recursion_check({"RALPH_AUTOBUG_DEPTH": "0"}) is True


def test_recursion_check_blocks_when_depth_one():
    assert fuses.recursion_check({"RALPH_AUTOBUG_DEPTH": "1"}) is False


def test_recursion_check_blocks_when_depth_higher():
    assert fuses.recursion_check({"RALPH_AUTOBUG_DEPTH": "5"}) is False


def test_recursion_check_treats_invalid_value_as_blocked():
    # Fail-safe: a garbage env value should NOT mean "allow recursion".
    assert fuses.recursion_check({"RALPH_AUTOBUG_DEPTH": "not-a-number"}) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/executor/autobug/test_fuses.py -v`
Expected: FAIL — `ralph_executor.autobug.fuses` not found.

- [ ] **Step 3: Implement `recursion_check`**

`ralph_executor/autobug/fuses.py`:

```python
"""Three independent fuses on autobug emission.

- recursion_check: bounded by RALPH_AUTOBUG_DEPTH env var
- rate_check: bounded by a sliding window of emission timestamps
- rollup: append-only suppressed-log writer for over-cap emissions
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

log = logging.getLogger(__name__)


def recursion_check(env: Mapping[str, str]) -> bool:
    """True = allowed to emit; False = recursion guard tripped.

    Reads ``RALPH_AUTOBUG_DEPTH`` from the env. Any non-integer value is
    treated as "tripped" — fail-safe.
    """
    raw = env.get("RALPH_AUTOBUG_DEPTH", "0")
    try:
        depth = int(raw)
    except ValueError:
        log.warning("RALPH_AUTOBUG_DEPTH=%r is not an int; treating as guard tripped", raw)
        return False
    return depth < 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/executor/autobug/test_fuses.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/fuses.py tests/executor/autobug/test_fuses.py
git commit -m "feat(autobug): recursion_check fuse via RALPH_AUTOBUG_DEPTH"
```

---

### Task 6: Rate-limit fuse + rollup

**Files:**
- Modify: `ralph_executor/autobug/fuses.py`
- Modify: `tests/executor/autobug/test_fuses.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/executor/autobug/test_fuses.py`:

```python
# --- rate_check ---

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".ralph" / "state"
    d.mkdir(parents=True)
    return d


def _now(year: int = 2026, month: int = 5, day: int = 31, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_rate_check_passes_when_log_missing(state_dir: Path):
    cfg = fuses.RateLimitConfig()
    assert fuses.rate_check(state_dir, cfg, _now()) is True


def test_rate_check_passes_under_cap(state_dir: Path):
    log_path = state_dir / "autobug-emissions.log"
    now = _now()
    log_path.write_text("\n".join((now - timedelta(minutes=1)).isoformat() for _ in range(4)))
    cfg = fuses.RateLimitConfig(max_writes=5, window=timedelta(minutes=10))
    assert fuses.rate_check(state_dir, cfg, now) is True


def test_rate_check_blocks_at_cap(state_dir: Path):
    log_path = state_dir / "autobug-emissions.log"
    now = _now()
    log_path.write_text("\n".join((now - timedelta(minutes=1)).isoformat() for _ in range(5)))
    cfg = fuses.RateLimitConfig(max_writes=5, window=timedelta(minutes=10))
    assert fuses.rate_check(state_dir, cfg, now) is False


def test_rate_check_ignores_old_entries_outside_window(state_dir: Path):
    log_path = state_dir / "autobug-emissions.log"
    now = _now()
    old = (now - timedelta(hours=2)).isoformat()
    fresh = (now - timedelta(minutes=1)).isoformat()
    log_path.write_text("\n".join([old] * 10 + [fresh] * 2))
    cfg = fuses.RateLimitConfig(max_writes=5, window=timedelta(minutes=10))
    assert fuses.rate_check(state_dir, cfg, now) is True


def test_rate_check_appends_emission(state_dir: Path):
    """Caller is expected to record emissions; rate_check doesn't write."""
    # rate_check is a pure query; emission recording is record_emission
    cfg = fuses.RateLimitConfig()
    fuses.record_emission(state_dir, _now())
    fuses.record_emission(state_dir, _now() + timedelta(seconds=1))
    log_path = state_dir / "autobug-emissions.log"
    assert log_path.read_text().count("\n") >= 2


def test_rate_check_skips_malformed_lines(state_dir: Path):
    log_path = state_dir / "autobug-emissions.log"
    now = _now()
    fresh = (now - timedelta(minutes=1)).isoformat()
    log_path.write_text(f"garbage\n\n{fresh}\nmore garbage\n{fresh}\n")
    cfg = fuses.RateLimitConfig(max_writes=5, window=timedelta(minutes=10))
    # Two valid fresh entries < cap of 5
    assert fuses.rate_check(state_dir, cfg, now) is True


# --- rollup ---

def test_rollup_appends_line(state_dir: Path):
    fuses.rollup(state_dir, "abc123", "rate-limited new", _now())
    sup = (state_dir / "autobug-suppressed.log").read_text()
    assert "abc123" in sup
    assert "rate-limited new" in sup
    assert "2026" in sup


def test_rollup_creates_log_if_missing(state_dir: Path):
    sup_path = state_dir / "autobug-suppressed.log"
    assert not sup_path.exists()
    fuses.rollup(state_dir, "abc123", "msg", _now())
    assert sup_path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/executor/autobug/test_fuses.py -v`
Expected: FAIL — `RateLimitConfig`, `rate_check`, `record_emission`, `rollup` not defined.

- [ ] **Step 3: Implement rate-limit fuse + rollup**

Append to `ralph_executor/autobug/fuses.py`:

```python
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class RateLimitConfig:
    max_writes: int = 5
    window: timedelta = field(default_factory=lambda: timedelta(minutes=10))


_EMISSIONS_LOG = "autobug-emissions.log"
_SUPPRESSED_LOG = "autobug-suppressed.log"
_TRUNCATE_AFTER_LINES = 1000


def _parse_iso(line: str) -> datetime | None:
    s = line.strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _read_timestamps_after(log_path: Path, cutoff: datetime) -> list[datetime]:
    if not log_path.is_file():
        return []
    out: list[datetime] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        ts = _parse_iso(line)
        if ts is not None and ts >= cutoff:
            out.append(ts)
    return out


def rate_check(state_dir: Path, cfg: RateLimitConfig, now: datetime) -> bool:
    """True = under cap; False = over cap, caller should rollup."""
    log_path = state_dir / _EMISSIONS_LOG
    cutoff = now - cfg.window
    recent = _read_timestamps_after(log_path, cutoff)
    # Inline truncation: if the file has grown well past the window,
    # rewrite keeping only fresh entries.
    if log_path.is_file():
        full_lines = log_path.read_text(encoding="utf-8").splitlines()
        if len(full_lines) > _TRUNCATE_AFTER_LINES:
            log_path.write_text(
                "\n".join(ts.isoformat() for ts in recent) + ("\n" if recent else ""),
                encoding="utf-8",
            )
    return len(recent) < cfg.max_writes


def record_emission(state_dir: Path, now: datetime) -> None:
    """Append one emission timestamp to the rolling log."""
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / _EMISSIONS_LOG
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(now.isoformat() + "\n")


def rollup(state_dir: Path, signature: str, message: str, now: datetime) -> None:
    """Append a suppressed-emission line so the operator can audit drops."""
    state_dir.mkdir(parents=True, exist_ok=True)
    sup_path = state_dir / _SUPPRESSED_LOG
    line = f"{now.isoformat()}\t{signature}\t{message}\n"
    with sup_path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    log.warning("autobug suppressed (signature=%s): %s", signature, message)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/executor/autobug/test_fuses.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/fuses.py tests/executor/autobug/test_fuses.py
git commit -m "feat(autobug): rate_check + record_emission + rollup"
```

---

### Task 7: Dedup lookup

**Files:**
- Create: `ralph_executor/autobug/dedup.py`
- Create: `tests/executor/autobug/test_dedup.py`

- [ ] **Step 1: Write failing tests**

`tests/executor/autobug/test_dedup.py`:

```python
"""Tests for autobug.dedup lookup."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ralph_executor.autobug import dedup
from ralph_executor.autobug.context import DedupResult


def _now() -> datetime:
    return datetime(2026, 5, 31, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def queue_root(tmp_path: Path) -> Path:
    root = tmp_path / "queue" / ".ralph"
    for state in ("inbox", "current", "pending-pr", "done"):
        (root / state).mkdir(parents=True)
    return root


def _write_pbi(
    queue_root: Path,
    state: str,
    pbi_id: str,
    signature: str,
    closed_at: datetime | None = None,
) -> Path:
    pbi_dir = queue_root / state / pbi_id
    pbi_dir.mkdir(parents=True)
    closed_line = f"closed_at: {closed_at.isoformat()}\n" if closed_at else ""
    (pbi_dir / "BUG.md").write_text(
        f"---\n"
        f"id: {pbi_id}\n"
        f"type: bug\n"
        f"severity: high\n"
        f"signature: {signature}\n"
        f"{closed_line}"
        f"---\n",
        encoding="utf-8",
    )
    return pbi_dir


def test_lookup_returns_new_when_no_match(queue_root: Path):
    result = dedup.lookup("abc123", queue_root, _now())
    assert result == DedupResult(kind="new")


def test_lookup_returns_bump_existing_when_in_inbox(queue_root: Path):
    _write_pbi(queue_root, "inbox", "autobug-abc-001", "abc123")
    result = dedup.lookup("abc123", queue_root, _now())
    assert result.kind == "bump_existing"
    assert result.existing_pbi_id == "autobug-abc-001"


def test_lookup_returns_bump_existing_when_in_current(queue_root: Path):
    _write_pbi(queue_root, "current", "autobug-abc-001", "abc123")
    result = dedup.lookup("abc123", queue_root, _now())
    assert result.kind == "bump_existing"


def test_lookup_returns_bump_existing_when_in_pending_pr(queue_root: Path):
    _write_pbi(queue_root, "pending-pr", "autobug-abc-001", "abc123")
    result = dedup.lookup("abc123", queue_root, _now())
    assert result.kind == "bump_existing"


def test_lookup_returns_reopen_regression_when_in_recent_done(queue_root: Path):
    closed = _now() - timedelta(days=5)
    _write_pbi(queue_root, "done", "autobug-abc-001", "abc123", closed_at=closed)
    result = dedup.lookup("abc123", queue_root, _now())
    assert result.kind == "reopen_regression"
    assert result.existing_pbi_id == "autobug-abc-001"


def test_lookup_returns_new_when_done_match_is_older_than_window(queue_root: Path):
    closed = _now() - timedelta(days=40)
    _write_pbi(queue_root, "done", "autobug-abc-001", "abc123", closed_at=closed)
    result = dedup.lookup("abc123", queue_root, _now())
    assert result.kind == "new"


def test_lookup_returns_new_when_done_has_no_closed_at(queue_root: Path):
    # Defensive: a done PBI without closed_at can't be windowed; treat as fresh.
    _write_pbi(queue_root, "done", "autobug-abc-001", "abc123")
    result = dedup.lookup("abc123", queue_root, _now())
    assert result.kind == "new"


def test_lookup_ignores_non_autobug_pbis(queue_root: Path):
    # A human-authored PBI without signature: must not match.
    pbi_dir = queue_root / "inbox" / "WI-1234"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "BUG.md").write_text(
        "---\nid: WI-1234\ntype: bug\nseverity: normal\n---\n", encoding="utf-8"
    )
    result = dedup.lookup("abc123", queue_root, _now())
    assert result.kind == "new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/executor/autobug/test_dedup.py -v`
Expected: FAIL — `ralph_executor.autobug.dedup` not found.

- [ ] **Step 3: Implement `lookup`**

`ralph_executor/autobug/dedup.py`:

```python
"""Scan queue state for a matching autobug signature."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from ralph_executor.autobug.context import DedupResult

_SIG_RE = re.compile(r"^signature:\s*([0-9a-f]+)\s*$", re.MULTILINE)
_CLOSED_AT_RE = re.compile(r"^closed_at:\s*([^\s].*?)\s*$", re.MULTILINE)
_ID_RE = re.compile(r"^id:\s*([^\s].*?)\s*$", re.MULTILINE)
_DEFAULT_DONE_WINDOW = timedelta(days=30)


def _read_frontmatter(bug_md: Path) -> str | None:
    """Return the frontmatter block (between --- fences) of BUG.md, or None."""
    try:
        text = bug_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    return text[4:end]


def _scan_one(pbi_dir: Path, target_sig: str) -> tuple[str, datetime | None] | None:
    """If ``pbi_dir/BUG.md`` has signature==target_sig, return (id, closed_at)."""
    bug = pbi_dir / "BUG.md"
    if not bug.is_file():
        return None
    fm = _read_frontmatter(bug)
    if fm is None:
        return None
    m_sig = _SIG_RE.search(fm)
    if not m_sig or m_sig.group(1) != target_sig:
        return None
    m_id = _ID_RE.search(fm)
    pbi_id = m_id.group(1) if m_id else pbi_dir.name
    m_closed = _CLOSED_AT_RE.search(fm)
    closed_at: datetime | None = None
    if m_closed:
        try:
            closed_at = datetime.fromisoformat(m_closed.group(1))
        except ValueError:
            closed_at = None
    return (pbi_id, closed_at)


def _find_by_signature(state_dir: Path, signature: str) -> tuple[str, datetime | None] | None:
    if not state_dir.is_dir():
        return None
    for child in sorted(state_dir.iterdir()):
        if not child.is_dir():
            continue
        hit = _scan_one(child, signature)
        if hit is not None:
            return hit
    return None


def lookup(
    signature: str,
    queue_root: Path,
    now: datetime,
    done_window: timedelta = _DEFAULT_DONE_WINDOW,
) -> DedupResult:
    """Find a matching autobug PBI; return what to do about it."""
    for state in ("inbox", "current", "pending-pr"):
        hit = _find_by_signature(queue_root / state, signature)
        if hit is not None:
            return DedupResult(kind="bump_existing", existing_pbi_id=hit[0])
    done_hit = _find_by_signature(queue_root / "done", signature)
    if done_hit is not None:
        pbi_id, closed_at = done_hit
        if closed_at is not None and (now - closed_at) <= done_window:
            return DedupResult(kind="reopen_regression", existing_pbi_id=pbi_id)
    return DedupResult(kind="new")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/executor/autobug/test_dedup.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/dedup.py tests/executor/autobug/test_dedup.py
git commit -m "feat(autobug): dedup.lookup over inbox/current/pending-pr + done(30d)"
```

---

## Phase 3 — Composer (defensive BUG.md / REPRODUCE.md)

### Task 8: Defensive `_safe` wrapper + section_failed metric

**Files:**
- Create: `ralph_executor/autobug/compose.py`
- Create: `tests/executor/autobug/test_compose.py`

- [ ] **Step 1: Write failing tests**

`tests/executor/autobug/test_compose.py`:

```python
"""Tests for autobug.compose (defensive composer)."""
from __future__ import annotations

import logging

from ralph_executor.autobug import compose


def test_safe_returns_fn_result_on_success():
    assert compose._safe(lambda: "ok", "fallback") == "ok"


def test_safe_returns_fallback_on_exception(caplog):
    caplog.set_level(logging.WARNING, logger="ralph_executor.autobug.compose")

    def boom():
        raise ValueError("nope")

    assert compose._safe(boom, "fallback", section="env") == "fallback"


def test_safe_emits_section_failed_log_line(caplog):
    caplog.set_level(logging.WARNING, logger="ralph_executor.autobug.compose")

    def boom():
        raise ValueError("nope")

    compose._safe(boom, "fallback", section="env")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("autobug.compose.section_failed" in m and "section=env" in m for m in msgs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/executor/autobug/test_compose.py -v`
Expected: FAIL — `ralph_executor.autobug.compose` not found.

- [ ] **Step 3: Implement `_safe`**

`ralph_executor/autobug/compose.py`:

```python
"""Defensive composer for BUG.md + REPRODUCE.md.

Every section is wrapped in ``_safe``: if the section builder raises,
the section is replaced with a placeholder and a structured metric is
emitted. This guarantees compose never raises even if context fields
are missing or corrupted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


def _safe(fn: Callable[[], T], fallback: T, *, section: str = "unknown") -> T:
    """Run fn() and return its result; on any exception return fallback.

    Emits a structured WARNING line keyed by section so operators can
    alert on persistent silent degradation.
    """
    try:
        return fn()
    except Exception as inner:
        log.warning(
            "autobug.compose.section_failed{section=%s} reason=%r",
            section,
            inner,
        )
        return fallback
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/executor/autobug/test_compose.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/compose.py tests/executor/autobug/test_compose.py
git commit -m "feat(autobug): defensive _safe wrapper with section_failed metric"
```

---

### Task 9: Frontmatter builder

**Files:**
- Modify: `ralph_executor/autobug/compose.py`
- Modify: `tests/executor/autobug/test_compose.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/executor/autobug/test_compose.py`:

```python
# --- build_frontmatter ---

from datetime import datetime, timezone
from pathlib import Path

from ralph_executor.autobug.context import Context


def _ctx(tmp_path: Path) -> Context:
    return Context(
        queue_root=tmp_path / "queue",
        state_dir=tmp_path / "queue" / ".ralph" / "state",
        env={},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=timezone.utc),
        ralph_sha="51cc97a",
        bot_author_email="ralph-bot@example.com",
        triggering_pbi_id="WI-247",
    )


def test_build_frontmatter_includes_required_bug_fields(tmp_path):
    fm = compose.build_frontmatter(
        pbi_id="autobug-a3f2c9-001",
        signature="a3f2c9d8e1bc",
        trigger_kind="python_crash",
        severity="critical",
        target_repo="https://github.com/example/ralph",
        ctx=_ctx(tmp_path),
    )
    assert "id: autobug-a3f2c9-001" in fm
    assert "type: bug" in fm
    assert "severity: critical" in fm
    assert "status: inbox" in fm
    assert "attempts: 0" in fm
    assert "created_at: 2026-05-31T14:23:01+00:00" in fm
    assert "updated_at: 2026-05-31T14:23:01+00:00" in fm
    assert "target_repo: https://github.com/example/ralph" in fm


def test_build_frontmatter_includes_autobug_extras(tmp_path):
    fm = compose.build_frontmatter(
        pbi_id="autobug-a3f2c9-001",
        signature="a3f2c9d8e1bc",
        trigger_kind="python_crash",
        severity="critical",
        target_repo="https://github.com/example/ralph",
        ctx=_ctx(tmp_path),
    )
    assert "signature: a3f2c9d8e1bc" in fm
    assert "trigger_kind: python_crash" in fm
    assert "occurrences: 1" in fm
    assert "first_seen: 2026-05-31T14:23:01+00:00" in fm
    assert "last_seen: 2026-05-31T14:23:01+00:00" in fm
    assert "regression_of: null" in fm
    assert "triggering_pbi: WI-247" in fm
    assert "ralph_sha: 51cc97a" in fm


def test_build_frontmatter_handles_no_triggering_pbi(tmp_path):
    ctx = _ctx(tmp_path)
    ctx_no_pbi = Context(
        queue_root=ctx.queue_root, state_dir=ctx.state_dir, env=ctx.env, now=ctx.now,
        ralph_sha=ctx.ralph_sha, bot_author_email=ctx.bot_author_email,
        triggering_pbi_id=None,
    )
    fm = compose.build_frontmatter(
        pbi_id="autobug-a3f2c9-001",
        signature="a3f2c9d8e1bc",
        trigger_kind="python_crash",
        severity="critical",
        target_repo="https://github.com/example/ralph",
        ctx=ctx_no_pbi,
    )
    assert "triggering_pbi: null" in fm


def test_build_frontmatter_supports_regression_of(tmp_path):
    fm = compose.build_frontmatter(
        pbi_id="autobug-a3f2c9-002",
        signature="a3f2c9d8e1bc",
        trigger_kind="python_crash",
        severity="critical",
        target_repo="https://github.com/example/ralph",
        ctx=_ctx(tmp_path),
        regression_of="autobug-a3f2c9-001",
    )
    assert "regression_of: autobug-a3f2c9-001" in fm
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/executor/autobug/test_compose.py -v -k frontmatter`
Expected: FAIL — `build_frontmatter` not defined.

- [ ] **Step 3: Implement `build_frontmatter`**

Append to `ralph_executor/autobug/compose.py`:

```python
from ralph_executor.autobug.context import Context


def build_frontmatter(
    *,
    pbi_id: str,
    signature: str,
    trigger_kind: str,
    severity: str,
    target_repo: str,
    ctx: Context,
    regression_of: str | None = None,
) -> str:
    """Build the YAML frontmatter block (between --- fences) for a fresh autobug PBI.

    Includes BOTH the parser-required fields and the autobug-specific
    extras. Line-based output — preserved across moves by movements'
    line-based _rewrite_status.
    """
    now_iso = ctx.now.isoformat()
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
    ]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/executor/autobug/test_compose.py -v`
Expected: PASS (all tests, including the 4 frontmatter ones)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/compose.py tests/executor/autobug/test_compose.py
git commit -m "feat(autobug): build_frontmatter — required bug fields + autobug extras"
```

---

### Task 10: `build_bug_md` and `build_reproduce_md`

**Files:**
- Modify: `ralph_executor/autobug/compose.py`
- Modify: `tests/executor/autobug/test_compose.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/executor/autobug/test_compose.py`:

```python
# --- build_bug_md ---

def test_build_bug_md_includes_title_and_stacktrace(tmp_path):
    try:
        raise ValueError("test crash")
    except ValueError as exc:
        body = compose.build_bug_md(
            exc=exc, trigger_kind="python_crash", ctx=_ctx(tmp_path)
        )
    assert body.startswith("# autobug")
    assert "ValueError" in body
    assert "test crash" in body
    assert "Stacktrace" in body


def test_build_bug_md_includes_env_snapshot(tmp_path):
    try:
        raise ValueError("x")
    except ValueError as exc:
        body = compose.build_bug_md(
            exc=exc, trigger_kind="python_crash", ctx=_ctx(tmp_path)
        )
    assert "Environment" in body
    assert "Python" in body  # version string
    assert "51cc97a" in body  # ralph_sha


def test_build_bug_md_includes_triggering_pbi_ref(tmp_path):
    try:
        raise ValueError("x")
    except ValueError as exc:
        body = compose.build_bug_md(
            exc=exc, trigger_kind="python_crash", ctx=_ctx(tmp_path)
        )
    assert "WI-247" in body


def test_build_bug_md_never_raises_on_bad_context(tmp_path):
    # Pass a context whose env_snapshot would explode (e.g., a Mapping that raises on iter)
    class BadEnv:
        def __iter__(self):
            raise RuntimeError("nope")

        def get(self, *a, **kw):
            raise RuntimeError("nope")
    ctx = Context(
        queue_root=tmp_path, state_dir=tmp_path, env=BadEnv(),  # type: ignore[arg-type]
        now=datetime(2026, 5, 31, tzinfo=timezone.utc),
        ralph_sha="x", bot_author_email="x",
    )
    try:
        raise ValueError("x")
    except ValueError as exc:
        body = compose.build_bug_md(exc=exc, trigger_kind="python_crash", ctx=ctx)
    # Body must still compose — failed sections become placeholders.
    assert "ValueError" in body or "<traceback unavailable>" in body


# --- build_reproduce_md ---

def test_build_reproduce_md_has_autobug_note(tmp_path):
    try:
        raise ValueError("test crash")
    except ValueError as exc:
        body = compose.build_reproduce_md(
            exc=exc, trigger_kind="python_crash", exit_code=None,
            ctx=_ctx(tmp_path), pbi_id="autobug-a3f2c9-001",
        )
    assert "AUTOBUG NOTE" in body
    assert "starting point" in body.lower() or "not a confirmed" in body.lower()
    assert "ValueError" in body


def test_build_reproduce_md_for_subprocess_includes_exit_code(tmp_path):
    body = compose.build_reproduce_md(
        exc=None, trigger_kind="subprocess_crash", exit_code=137,
        ctx=_ctx(tmp_path), pbi_id="autobug-a3f2c9-001",
    )
    assert "subprocess_crash" in body
    assert "137" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/executor/autobug/test_compose.py -v`
Expected: FAIL — `build_bug_md` and `build_reproduce_md` not defined.

- [ ] **Step 3: Implement `build_bug_md` and `build_reproduce_md`**

Append to `ralph_executor/autobug/compose.py`:

```python
import os
import platform
import sys
import traceback


def _short_title(exc: BaseException) -> str:
    cls = type(exc).__qualname__
    msg = str(exc).splitlines()[0] if str(exc) else ""
    return f"# autobug — {cls}: {msg}" if msg else f"# autobug — {cls}"


def _format_traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _env_snapshot(ctx: Context) -> str:
    bits = [
        f"- OS: {platform.platform()}",
        f"- Python: {sys.version.split()[0]}",
        f"- Ralph SHA: {ctx.ralph_sha}",
    ]
    # safe env var dump — skip anything that looks like a secret
    safe = []
    for k in sorted(ctx.env.keys() if hasattr(ctx.env, "keys") else []):
        if any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")):
            continue
        if k.startswith("RALPH_") or k in ("BETTER_MEMORY_PROJECT",):
            v = ctx.env.get(k, "")
            safe.append(f"  - {k}={v}")
    if safe:
        bits.append("- Ralph-relevant env:")
        bits.extend(safe)
    return "\n".join(bits)


def _pbi_ref(ctx: Context) -> str:
    if ctx.triggering_pbi_id:
        return f"PBI: `{ctx.triggering_pbi_id}` (see `done/{ctx.triggering_pbi_id}/HISTORY.md`)"
    return "PBI: none (crash before claim)"


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body}"


def build_bug_md(*, exc: BaseException, trigger_kind: str, ctx: Context) -> str:
    """Build the BUG.md body. Never raises — each section degrades to a placeholder."""
    title = _safe(lambda: _short_title(exc), "# autobug — unknown crash", section="title")
    parts = [title]
    parts.append(_section("Trigger", trigger_kind))
    parts.append(
        _section(
            "Stacktrace",
            "```\n"
            + _safe(lambda: _format_traceback(exc), "<traceback unavailable>", section="stacktrace")
            + "\n```",
        )
    )
    parts.append(
        _section(
            "Environment",
            _safe(lambda: _env_snapshot(ctx), "<env unavailable>", section="env"),
        )
    )
    parts.append(
        _section(
            "Triggering PBI",
            _safe(lambda: _pbi_ref(ctx), "<no PBI context>", section="pbi_ref"),
        )
    )
    return "\n\n".join(parts) + "\n"


_REPRODUCE_HEADER = (
    "> AUTOBUG NOTE: This bug was auto-captured at crash time. The content\n"
    "> below is what we observed, NOT a confirmed reproduction recipe. Your\n"
    "> iteration's first task is to convert these observations into runnable\n"
    "> reproduction commands and append them to this file. If after\n"
    "> investigation the crash is genuinely non-deterministic (race\n"
    "> condition, OOM under load, network blip), write STUCK.md per the\n"
    "> bug workflow's contradictory-evidence trigger — do not stack\n"
    "> speculative fixes.\n"
)


def build_reproduce_md(
    *,
    exc: BaseException | None,
    trigger_kind: str,
    exit_code: int | None,
    ctx: Context,
    pbi_id: str,
) -> str:
    """Build REPRODUCE.md with observed artefacts and explicit 'starting point' header."""
    parts = [f"# REPRODUCE — {pbi_id}", "", _REPRODUCE_HEADER, "## Observed", ""]
    parts.append(f"Trigger: {trigger_kind}")
    if exit_code is not None:
        parts.append(f"Exit code: {exit_code}")
    if exc is not None:
        parts.append("")
        parts.append("### Stacktrace")
        parts.append("")
        parts.append("```")
        parts.append(_safe(lambda: _format_traceback(exc), "<traceback unavailable>", section="stacktrace"))
        parts.append("```")
    parts.append("")
    parts.append("### Environment snapshot")
    parts.append("")
    parts.append(_safe(lambda: _env_snapshot(ctx), "<env unavailable>", section="env"))
    parts.append("")
    parts.append("## Suggested reproduction approach")
    parts.append("")
    parts.append("- Inspect the throw site in the stacktrace above")
    parts.append("- Reproduce the env conditions captured")
    if ctx.triggering_pbi_id:
        parts.append(f"- See triggering PBI {ctx.triggering_pbi_id} in done/ for context")
    return "\n".join(parts) + "\n"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/executor/autobug/test_compose.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/compose.py tests/executor/autobug/test_compose.py
git commit -m "feat(autobug): build_bug_md + build_reproduce_md with defensive sections"
```

---

## Phase 4 — Emit

### Task 11: `emit.new`, `emit.bump`, `emit.reopen`

**Files:**
- Create: `ralph_executor/autobug/emit.py`
- Create: `tests/executor/autobug/test_emit.py`

- [ ] **Step 1: Write failing tests**

`tests/executor/autobug/test_emit.py`:

```python
"""Tests for autobug.emit (queue write + bump/reopen semantics)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ralph_executor.autobug import emit
from ralph_executor.autobug.context import Context


@pytest.fixture
def ctx(tmp_path: Path) -> Context:
    queue_root = tmp_path / "queue"
    (queue_root / ".ralph" / "inbox").mkdir(parents=True)
    (queue_root / ".ralph" / "state").mkdir(parents=True)
    return Context(
        queue_root=queue_root,
        state_dir=queue_root / ".ralph" / "state",
        env={},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=timezone.utc),
        ralph_sha="51cc97a",
        bot_author_email="ralph-bot@example.com",
        triggering_pbi_id="WI-247",
    )


@pytest.fixture
def fake_git(monkeypatch):
    """Stub out git_ops.commit_all + push_with_rebase so tests don't touch git."""
    from ralph_executor import git_ops
    calls: list[tuple[str, tuple, dict]] = []
    monkeypatch.setattr(git_ops, "commit_all", lambda *a, **kw: calls.append(("commit_all", a, kw)))
    monkeypatch.setattr(git_ops, "push_with_rebase", lambda *a, **kw: calls.append(("push_with_rebase", a, kw)))
    return calls


def test_emit_new_creates_pbi_directory(ctx, fake_git):
    sig = "a" * 64
    pbi_id = emit.new(sig=sig, exc=ValueError("x"), trigger_kind="python_crash",
                     severity="critical", target_repo="https://example.com/ralph", ctx=ctx)
    pbi_dir = ctx.queue_root / ".ralph" / "inbox" / pbi_id
    assert pbi_dir.is_dir()
    assert (pbi_dir / "BUG.md").is_file()
    assert (pbi_dir / "REPRODUCE.md").is_file()
    assert (pbi_dir / "HISTORY.md").is_file()


def test_emit_new_uses_short_signature_in_id(ctx, fake_git):
    sig = "abcdef1234567890" + "0" * 48  # 64 chars total
    pbi_id = emit.new(sig=sig, exc=ValueError("x"), trigger_kind="python_crash",
                     severity="critical", target_repo="https://example.com/ralph", ctx=ctx)
    assert pbi_id.startswith("autobug-abcdef-")
    assert pbi_id.endswith("-001")


def test_emit_new_increments_seq_on_collision(ctx, fake_git):
    sig = "abcdef" + "0" * 58
    pbi_id_1 = emit.new(sig=sig, exc=ValueError("x"), trigger_kind="python_crash",
                       severity="critical", target_repo="https://example.com/ralph", ctx=ctx)
    pbi_id_2 = emit.new(sig=sig, exc=ValueError("x"), trigger_kind="python_crash",
                       severity="critical", target_repo="https://example.com/ralph", ctx=ctx)
    assert pbi_id_1.endswith("-001")
    assert pbi_id_2.endswith("-002")


def test_emit_new_records_emission_in_state_log(ctx, fake_git):
    sig = "a" * 64
    emit.new(sig=sig, exc=ValueError("x"), trigger_kind="python_crash",
             severity="critical", target_repo="https://example.com/ralph", ctx=ctx)
    log_path = ctx.state_dir / "autobug-emissions.log"
    assert log_path.is_file()
    assert "2026" in log_path.read_text()


def test_emit_bump_increments_occurrences(ctx, fake_git):
    sig = "b" * 64
    pbi_id = emit.new(sig=sig, exc=ValueError("orig"), trigger_kind="python_crash",
                     severity="critical", target_repo="https://example.com/ralph", ctx=ctx)
    try:
        raise ValueError("again")
    except ValueError as exc:
        emit.bump(existing_pbi_id=pbi_id, exc=exc, ctx=ctx)
    bug_md = (ctx.queue_root / ".ralph" / "inbox" / pbi_id / "BUG.md").read_text()
    assert "occurrences: 2" in bug_md


def test_emit_bump_appends_trace_section(ctx, fake_git):
    sig = "c" * 64
    pbi_id = emit.new(sig=sig, exc=ValueError("orig"), trigger_kind="python_crash",
                     severity="critical", target_repo="https://example.com/ralph", ctx=ctx)
    try:
        raise ValueError("again")
    except ValueError as exc:
        emit.bump(existing_pbi_id=pbi_id, exc=exc, ctx=ctx)
    bug_md = (ctx.queue_root / ".ralph" / "inbox" / pbi_id / "BUG.md").read_text()
    assert "## Trace 2" in bug_md


def test_emit_bump_does_not_inflate_rate_limit_log(ctx, fake_git):
    sig = "d" * 64
    pbi_id = emit.new(sig=sig, exc=ValueError("x"), trigger_kind="python_crash",
                     severity="critical", target_repo="https://example.com/ralph", ctx=ctx)
    before = (ctx.state_dir / "autobug-emissions.log").read_text().count("\n")
    try:
        raise ValueError("again")
    except ValueError as exc:
        emit.bump(existing_pbi_id=pbi_id, exc=exc, ctx=ctx)
    after = (ctx.state_dir / "autobug-emissions.log").read_text().count("\n")
    assert after == before, "bumps must not consume rate-limit budget"


def test_emit_reopen_creates_fresh_pbi_with_regression_of(ctx, fake_git):
    sig = "e" * 64
    # Pre-stage a done/ PBI
    done_dir = ctx.queue_root / ".ralph" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    old_pbi = "autobug-eeeeee-001"
    (done_dir / old_pbi).mkdir()
    (done_dir / old_pbi / "BUG.md").write_text(
        f"---\nid: {old_pbi}\ntype: bug\nsignature: {sig}\n---\n"
    )
    try:
        raise ValueError("regression")
    except ValueError as exc:
        new_id = emit.reopen(existing_pbi_id=old_pbi, sig=sig, exc=exc,
                            trigger_kind="python_crash", severity="critical",
                            target_repo="https://example.com/ralph", ctx=ctx)
    new_dir = ctx.queue_root / ".ralph" / "inbox" / new_id
    assert new_dir.is_dir()
    fm = (new_dir / "BUG.md").read_text()
    assert f"regression_of: {old_pbi}" in fm
    assert new_id != old_pbi
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/executor/autobug/test_emit.py -v`
Expected: FAIL — `ralph_executor.autobug.emit` not found.

- [ ] **Step 3: Implement `emit.new`, `emit.bump`, `emit.reopen`**

`ralph_executor/autobug/emit.py`:

```python
"""Queue writes for autobug — new / bump / reopen.

Failures inside emit are caught at the call site (detect.py). emit
itself raises on bad inputs so the caller can rollup-and-log.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ralph_executor import git_ops
from ralph_executor.autobug import compose, fuses
from ralph_executor.autobug.context import Context

log = logging.getLogger(__name__)

_INBOX = ".ralph/inbox"


def _short_sig(sig: str) -> str:
    return sig[:6]


def _next_seq(inbox: Path, short_sig: str) -> int:
    """Find the next available seq for a given short-sig in inbox + done."""
    prefix = f"autobug-{short_sig}-"
    seqs: set[int] = set()
    for parent in (inbox, inbox.parent / "done", inbox.parent / "current", inbox.parent / "pending-pr"):
        if not parent.is_dir():
            continue
        for child in parent.iterdir():
            if child.is_dir() and child.name.startswith(prefix):
                try:
                    seqs.add(int(child.name[len(prefix):]))
                except ValueError:
                    pass
    n = 1
    while n in seqs:
        n += 1
    return n


def _commit_and_push(queue_repo: Path, msg: str) -> None:
    git_ops.commit_all(queue_repo, msg)
    git_ops.push_with_rebase(queue_repo, remote="origin", branch="ralph-queue")


def new(
    *,
    sig: str,
    exc: BaseException | None,
    trigger_kind: str,
    severity: str,
    target_repo: str,
    ctx: Context,
    exit_code: int | None = None,
) -> str:
    """Emit a fresh autobug PBI to inbox/. Returns the new PBI id."""
    inbox = ctx.queue_root / _INBOX
    inbox.mkdir(parents=True, exist_ok=True)
    short = _short_sig(sig)
    seq = _next_seq(inbox, short)
    pbi_id = f"autobug-{short}-{seq:03d}"
    pbi_dir = inbox / pbi_id
    pbi_dir.mkdir()

    fm = compose.build_frontmatter(
        pbi_id=pbi_id, signature=sig, trigger_kind=trigger_kind,
        severity=severity, target_repo=target_repo, ctx=ctx,
    )
    body = (
        compose.build_bug_md(exc=exc, trigger_kind=trigger_kind, ctx=ctx)
        if exc is not None
        else f"# autobug — {trigger_kind} (exit {exit_code})\n"
    )
    (pbi_dir / "BUG.md").write_text(fm + "\n" + body, encoding="utf-8")
    (pbi_dir / "REPRODUCE.md").write_text(
        compose.build_reproduce_md(
            exc=exc, trigger_kind=trigger_kind, exit_code=exit_code, ctx=ctx, pbi_id=pbi_id
        ),
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")

    fuses.record_emission(ctx.state_dir, ctx.now)

    queue_repo = ctx.queue_root
    _commit_and_push(queue_repo, f"chore(autobug): emit {pbi_id} (signature {short})")
    return pbi_id


_OCCURRENCES_RE = re.compile(r"^(occurrences:\s*)(\d+)\s*$", re.MULTILINE)
_LAST_SEEN_RE = re.compile(r"^(last_seen:\s*).+$", re.MULTILINE)


def _find_pbi(queue_root: Path, pbi_id: str) -> Path | None:
    for state in ("inbox", "current", "pending-pr"):
        p = queue_root / ".ralph" / state / pbi_id
        if p.is_dir() and (p / "BUG.md").is_file():
            return p
    return None


def bump(*, existing_pbi_id: str, exc: BaseException | None, ctx: Context) -> None:
    """Increment occurrences + last_seen, append a Trace section. No new PBI."""
    pbi_dir = _find_pbi(ctx.queue_root, existing_pbi_id)
    if pbi_dir is None:
        log.warning("autobug.bump: PBI %s not found in any open state", existing_pbi_id)
        return
    bug_md_path = pbi_dir / "BUG.md"
    text = bug_md_path.read_text(encoding="utf-8")

    # Bump occurrences (in frontmatter)
    m = _OCCURRENCES_RE.search(text)
    if m:
        current = int(m.group(2))
        text = _OCCURRENCES_RE.sub(lambda m_: f"{m_.group(1)}{current + 1}", text)
    text = _LAST_SEEN_RE.sub(f"last_seen: {ctx.now.isoformat()}", text)

    # Determine next trace number — count existing ## Trace headings + 1
    trace_n = text.count("\n## Trace ") + 2  # first emit didn't write a Trace section
    trace_body = compose._safe(
        lambda: compose._format_traceback(exc) if exc is not None else "<no exception object>",
        "<traceback unavailable>",
        section="bump_trace",
    )
    appendix = f"\n## Trace {trace_n} — {ctx.now.isoformat()}\n\n```\n{trace_body}\n```\n"
    text = text + appendix
    bug_md_path.write_text(text, encoding="utf-8")

    # Bumps do NOT call record_emission — they don't consume rate-limit budget.
    _commit_and_push(ctx.queue_root, f"chore(autobug): bump {existing_pbi_id} (occurrence {current + 1 if m else '?'})")


def reopen(
    *,
    existing_pbi_id: str,
    sig: str,
    exc: BaseException | None,
    trigger_kind: str,
    severity: str,
    target_repo: str,
    ctx: Context,
    exit_code: int | None = None,
) -> str:
    """Emit a fresh PBI cross-linked to existing_pbi_id via regression_of."""
    inbox = ctx.queue_root / _INBOX
    inbox.mkdir(parents=True, exist_ok=True)
    short = _short_sig(sig)
    seq = _next_seq(inbox, short)
    pbi_id = f"autobug-{short}-{seq:03d}"
    pbi_dir = inbox / pbi_id
    pbi_dir.mkdir()

    fm = compose.build_frontmatter(
        pbi_id=pbi_id, signature=sig, trigger_kind=trigger_kind,
        severity=severity, target_repo=target_repo, ctx=ctx,
        regression_of=existing_pbi_id,
    )
    body = (
        compose.build_bug_md(exc=exc, trigger_kind=trigger_kind, ctx=ctx)
        if exc is not None
        else f"# autobug — {trigger_kind} (exit {exit_code}) regression of {existing_pbi_id}\n"
    )
    (pbi_dir / "BUG.md").write_text(fm + "\n" + body, encoding="utf-8")
    (pbi_dir / "REPRODUCE.md").write_text(
        compose.build_reproduce_md(
            exc=exc, trigger_kind=trigger_kind, exit_code=exit_code, ctx=ctx, pbi_id=pbi_id
        ),
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text(
        f"# HISTORY — {pbi_id}\n\nRegression of {existing_pbi_id}; original closed in done/.\n",
        encoding="utf-8",
    )
    fuses.record_emission(ctx.state_dir, ctx.now)
    _commit_and_push(ctx.queue_root, f"chore(autobug): reopen {existing_pbi_id} as {pbi_id}")
    return pbi_id
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/executor/autobug/test_emit.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/emit.py tests/executor/autobug/test_emit.py
git commit -m "feat(autobug): emit.new + emit.bump + emit.reopen"
```

---

## Phase 5 — Orchestration and public API

### Task 12: `detect.py` orchestration

**Files:**
- Create: `ralph_executor/autobug/detect.py`
- Create: `tests/executor/autobug/test_detect.py`

- [ ] **Step 1: Write failing tests**

`tests/executor/autobug/test_detect.py`:

```python
"""Tests for autobug.detect orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ralph_executor.autobug import detect
from ralph_executor.autobug.context import Context, DedupResult


@pytest.fixture
def ctx(tmp_path: Path) -> Context:
    queue_root = tmp_path / "queue"
    for state in ("inbox", "current", "pending-pr", "done"):
        (queue_root / ".ralph" / state).mkdir(parents=True)
    (queue_root / ".ralph" / "state").mkdir(parents=True)
    return Context(
        queue_root=queue_root,
        state_dir=queue_root / ".ralph" / "state",
        env={},
        now=datetime(2026, 5, 31, 14, 0, tzinfo=timezone.utc),
        ralph_sha="51cc97a",
        bot_author_email="bot@example.com",
        triggering_pbi_id=None,
    )


@pytest.fixture(autouse=True)
def stub_git(monkeypatch):
    from ralph_executor import git_ops
    monkeypatch.setattr(git_ops, "commit_all", lambda *a, **kw: None)
    monkeypatch.setattr(git_ops, "push_with_rebase", lambda *a, **kw: None)


def test_detect_python_crash_emits_new_when_no_match(ctx):
    try:
        raise ValueError("x")
    except ValueError as exc:
        detect.detect_python_crash(exc, ctx, target_repo="https://example.com/ralph")
    assert any((ctx.queue_root / ".ralph" / "inbox").iterdir())


def test_detect_python_crash_recursion_guard_blocks(ctx):
    ctx_guarded = Context(
        queue_root=ctx.queue_root, state_dir=ctx.state_dir,
        env={"RALPH_AUTOBUG_DEPTH": "1"}, now=ctx.now,
        ralph_sha=ctx.ralph_sha, bot_author_email=ctx.bot_author_email,
        triggering_pbi_id=ctx.triggering_pbi_id,
    )
    try:
        raise ValueError("x")
    except ValueError as exc:
        detect.detect_python_crash(exc, ctx_guarded, target_repo="https://example.com/ralph")
    assert not list((ctx.queue_root / ".ralph" / "inbox").iterdir())


def test_detect_python_crash_signature_failure_aborts(ctx, monkeypatch):
    from ralph_executor.autobug import signature
    monkeypatch.setattr(signature, "python_crash_signature",
                       lambda exc: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        raise ValueError("x")
    except ValueError as exc:
        detect.detect_python_crash(exc, ctx, target_repo="https://example.com/ralph")
    # No emit, no propagation
    assert not list((ctx.queue_root / ".ralph" / "inbox").iterdir())


def test_detect_python_crash_emit_failure_does_not_propagate(ctx, monkeypatch):
    from ralph_executor.autobug import emit
    monkeypatch.setattr(emit, "new",
                       lambda **kw: (_ for _ in ()).throw(RuntimeError("git push failed")))
    try:
        raise ValueError("x")
    except ValueError as exc:
        detect.detect_python_crash(exc, ctx, target_repo="https://example.com/ralph")
    # Suppressed log appended
    sup = (ctx.state_dir / "autobug-suppressed.log").read_text()
    assert "git push failed" in sup or "emit failed" in sup


def test_detect_subprocess_crash_emits_for_non_zero_exit(ctx):
    detect.detect_subprocess_crash(
        exit_code=137, stderr="Killed (OOM)", command=["claude", "-p"],
        ctx=ctx, target_repo="https://example.com/ralph",
    )
    assert any((ctx.queue_root / ".ralph" / "inbox").iterdir())


def test_detect_aborts_when_bot_author_email_missing(ctx):
    ctx_no_email = Context(
        queue_root=ctx.queue_root, state_dir=ctx.state_dir, env=ctx.env, now=ctx.now,
        ralph_sha=ctx.ralph_sha, bot_author_email="", triggering_pbi_id=None,
    )
    try:
        raise ValueError("x")
    except ValueError as exc:
        detect.detect_python_crash(exc, ctx_no_email, target_repo="https://example.com/ralph")
    assert not list((ctx.queue_root / ".ralph" / "inbox").iterdir())


def test_detect_bumps_when_existing_signature(ctx):
    # First crash creates
    try:
        raise ValueError("same")
    except ValueError as exc:
        detect.detect_python_crash(exc, ctx, target_repo="https://example.com/ralph")
    first = list((ctx.queue_root / ".ralph" / "inbox").iterdir())
    assert len(first) == 1
    # Second crash with same signature must bump, not create
    try:
        raise ValueError("same")
    except ValueError as exc:
        detect.detect_python_crash(exc, ctx, target_repo="https://example.com/ralph")
    second = list((ctx.queue_root / ".ralph" / "inbox").iterdir())
    assert len(second) == 1, "second crash with same signature must bump, not create new PBI"
    bug = (second[0] / "BUG.md").read_text()
    assert "occurrences: 2" in bug
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/executor/autobug/test_detect.py -v`
Expected: FAIL — `ralph_executor.autobug.detect` not found.

- [ ] **Step 3: Implement `detect.py`**

`ralph_executor/autobug/detect.py`:

```python
"""Public API and orchestration for autobug emission.

Both detect_* functions are no-op-safe: every failure is caught,
logged, and the function returns normally. The caller MUST NOT depend
on their return value or wrap them in try/except — though doing so is
harmless.
"""

from __future__ import annotations

import logging

from ralph_executor.autobug import compose, dedup, emit, fuses, signature
from ralph_executor.autobug.context import Context

log = logging.getLogger(__name__)

_RATE_CFG = fuses.RateLimitConfig()


def _abort(ctx: Context, sig: str, reason: str) -> None:
    """Log + rollup; never raises."""
    try:
        fuses.rollup(ctx.state_dir, sig, reason, ctx.now)
    except Exception as inner:
        log.warning("autobug rollup failed: %s", inner)


def detect_python_crash(
    exc: BaseException,
    ctx: Context,
    *,
    target_repo: str,
    severity: str = "critical",
) -> None:
    """Capture a Python exception as an autobug PBI. Never raises."""
    try:
        if not ctx.bot_author_email:
            log.warning("autobug abort: bot_author_email missing")
            return
        try:
            sig = signature.python_crash_signature(exc)
        except Exception as inner:
            log.warning("autobug abort: signature failed: %s", inner)
            return
        if not fuses.recursion_check(ctx.env):
            log.warning("autobug recursion guard tripped, signature=%s", sig)
            return
        result = dedup.lookup(sig, ctx.queue_root, ctx.now)
        try:
            if result.kind == "bump_existing":
                emit.bump(existing_pbi_id=result.existing_pbi_id, exc=exc, ctx=ctx)
                return
            if result.kind == "reopen_regression":
                if not fuses.rate_check(ctx.state_dir, _RATE_CFG, ctx.now):
                    _abort(ctx, sig, "rate-limited reopen")
                    return
                emit.reopen(
                    existing_pbi_id=result.existing_pbi_id, sig=sig, exc=exc,
                    trigger_kind="python_crash", severity=severity,
                    target_repo=target_repo, ctx=ctx,
                )
                return
            # kind == "new"
            if not fuses.rate_check(ctx.state_dir, _RATE_CFG, ctx.now):
                _abort(ctx, sig, "rate-limited new")
                return
            emit.new(
                sig=sig, exc=exc, trigger_kind="python_crash",
                severity=severity, target_repo=target_repo, ctx=ctx,
            )
        except Exception as emit_exc:
            _abort(ctx, sig, f"emit failed: {emit_exc!r}")
    except Exception as outer:
        # Final safety net — autobug must NEVER raise.
        log.error("autobug detect_python_crash final safety net: %r", outer)


def detect_subprocess_crash(
    exit_code: int,
    stderr: str,
    command: list[str],
    ctx: Context,
    *,
    target_repo: str,
    severity: str = "high",
) -> None:
    """Capture a non-zero / timeout subprocess exit as an autobug PBI. Never raises."""
    try:
        if not ctx.bot_author_email:
            log.warning("autobug abort: bot_author_email missing")
            return
        try:
            sig = signature.subprocess_crash_signature(exit_code, stderr)
        except Exception as inner:
            log.warning("autobug abort: signature failed: %s", inner)
            return
        if not fuses.recursion_check(ctx.env):
            log.warning("autobug recursion guard tripped, signature=%s", sig)
            return
        result = dedup.lookup(sig, ctx.queue_root, ctx.now)
        try:
            if result.kind == "bump_existing":
                emit.bump(existing_pbi_id=result.existing_pbi_id, exc=None, ctx=ctx)
                return
            if result.kind == "reopen_regression":
                if not fuses.rate_check(ctx.state_dir, _RATE_CFG, ctx.now):
                    _abort(ctx, sig, "rate-limited reopen")
                    return
                emit.reopen(
                    existing_pbi_id=result.existing_pbi_id, sig=sig, exc=None,
                    trigger_kind="subprocess_crash", severity=severity,
                    target_repo=target_repo, ctx=ctx, exit_code=exit_code,
                )
                return
            if not fuses.rate_check(ctx.state_dir, _RATE_CFG, ctx.now):
                _abort(ctx, sig, "rate-limited new")
                return
            emit.new(
                sig=sig, exc=None, trigger_kind="subprocess_crash",
                severity=severity, target_repo=target_repo, ctx=ctx,
                exit_code=exit_code,
            )
        except Exception as emit_exc:
            _abort(ctx, sig, f"emit failed: {emit_exc!r}")
    except Exception as outer:
        log.error("autobug detect_subprocess_crash final safety net: %r", outer)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/executor/autobug/test_detect.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/detect.py tests/executor/autobug/test_detect.py
git commit -m "feat(autobug): detect orchestration with abort-on-failure semantics"
```

---

### Task 13: Public API exports

**Files:**
- Modify: `ralph_executor/autobug/__init__.py`

- [ ] **Step 1: Add the failing import test**

`tests/executor/autobug/test_public_api.py`:

```python
"""The package re-exports exactly the two public functions and Context."""
import ralph_executor.autobug as ab


def test_exports_detect_python_crash():
    assert callable(ab.detect_python_crash)


def test_exports_detect_subprocess_crash():
    assert callable(ab.detect_subprocess_crash)


def test_exports_context():
    assert ab.Context is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/executor/autobug/test_public_api.py -v`
Expected: FAIL — `module 'ralph_executor.autobug' has no attribute 'detect_python_crash'`.

- [ ] **Step 3: Update `__init__.py`**

`ralph_executor/autobug/__init__.py`:

```python
"""Autobug: capture executor and Claude-subprocess crashes as bug PBIs.

See ``docs/superpowers/specs/2026-05-31-ralph-autobug-design.md`` for
the design rationale and contracts.
"""

from ralph_executor.autobug.context import Context, DedupResult
from ralph_executor.autobug.detect import detect_python_crash, detect_subprocess_crash

__all__ = [
    "Context",
    "DedupResult",
    "detect_python_crash",
    "detect_subprocess_crash",
]
```

- [ ] **Step 4: Run test**

Run: `pytest tests/executor/autobug/test_public_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/autobug/__init__.py tests/executor/autobug/test_public_api.py
git commit -m "feat(autobug): expose public API (detect_* + Context + DedupResult)"
```

---

## Phase 6 — Config

### Task 14: TOML keys for autobug

**Files:**
- Modify: `ralph_executor/config.py`
- Modify: `tests/executor/test_config.py` (or whichever file holds config tests — investigate before editing)

- [ ] **Step 1: Locate the config tests**

Run: `grep -l "ExecutorConfig\|load_config" tests/executor/ -r`
Confirm: a config test file exists. If multiple, append to the most general (e.g. `tests/executor/test_config.py`).

- [ ] **Step 2: Write failing tests for the new config keys**

Append to the located config test file:

```python
# --- autobug config keys ---

def test_load_config_defaults_autobug_enabled_true(tmp_path):
    cfg_path = tmp_path / "ralph.toml"
    cfg_path.write_text('workspace_root = "/tmp"\n')
    cfg = load_config(cfg_path)  # use the project's existing loader
    assert cfg.autobug_enabled is True


def test_load_config_reads_autobug_rate_max(tmp_path):
    cfg_path = tmp_path / "ralph.toml"
    cfg_path.write_text('workspace_root = "/tmp"\nautobug_rate_max = 10\n')
    cfg = load_config(cfg_path)
    assert cfg.autobug_rate_max == 10


def test_load_config_reads_autobug_rate_window_minutes(tmp_path):
    cfg_path = tmp_path / "ralph.toml"
    cfg_path.write_text('workspace_root = "/tmp"\nautobug_rate_window_minutes = 5\n')
    cfg = load_config(cfg_path)
    assert cfg.autobug_rate_window_minutes == 5


def test_load_config_reads_autobug_dedup_done_window_days(tmp_path):
    cfg_path = tmp_path / "ralph.toml"
    cfg_path.write_text('workspace_root = "/tmp"\nautobug_dedup_done_window_days = 14\n')
    cfg = load_config(cfg_path)
    assert cfg.autobug_dedup_done_window_days == 14


def test_load_config_reads_autobug_severity_keys(tmp_path):
    cfg_path = tmp_path / "ralph.toml"
    cfg_path.write_text(
        'workspace_root = "/tmp"\n'
        'autobug_severity_python_crash = "high"\n'
        'autobug_severity_subprocess_crash = "normal"\n'
    )
    cfg = load_config(cfg_path)
    assert cfg.autobug_severity_python_crash == "high"
    assert cfg.autobug_severity_subprocess_crash == "normal"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/executor/test_config.py -v -k autobug`
Expected: FAIL — `ExecutorConfig` has no `autobug_enabled` field.

- [ ] **Step 4: Add fields to `ExecutorConfig` and parse them in `load_config`**

In `ralph_executor/config.py`, add to the `ExecutorConfig` dataclass:

```python
    autobug_enabled: bool = True
    autobug_rate_max: int = 5
    autobug_rate_window_minutes: int = 10
    autobug_dedup_done_window_days: int = 30
    autobug_severity_python_crash: str = "critical"
    autobug_severity_subprocess_crash: str = "high"
```

In `load_config`, after the existing TOML key parsing, add:

```python
    autobug_enabled = bool(data.get("autobug_enabled", True))
    autobug_rate_max = int(data.get("autobug_rate_max", 5))
    autobug_rate_window_minutes = int(data.get("autobug_rate_window_minutes", 10))
    autobug_dedup_done_window_days = int(data.get("autobug_dedup_done_window_days", 30))
    autobug_severity_python_crash = str(data.get("autobug_severity_python_crash", "critical"))
    autobug_severity_subprocess_crash = str(data.get("autobug_severity_subprocess_crash", "high"))
```

Pass these into the `ExecutorConfig(...)` constructor call.

- [ ] **Step 5: Run tests**

Run: `pytest tests/executor/test_config.py -v -k autobug`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add ralph_executor/config.py tests/executor/test_config.py
git commit -m "feat(config): autobug_* TOML keys with documented defaults"
```

---

### Task 15: `bot_author_email` startup validation (concern 3 rollin)

**Files:**
- Modify: `ralph_executor/config.py` (or `cli.py`, depending on where startup logging happens — investigate first)
- Modify: `tests/executor/test_config.py` (or wherever the startup-banner test fits)

- [ ] **Step 1: Investigate where startup logging lives**

Run: `grep -n "log.info\|log.warning" ralph_executor/cli.py | head -20`

Identify: a place at startup that already does `log.info("ralph executor starting")` or similar. If none exists, add one in `cli.py::main` before the loop starts.

- [ ] **Step 2: Write failing test for the one-time validation**

Append to the located test file (likely `tests/executor/test_cli.py`):

```python
def test_startup_warns_once_when_bot_author_email_missing_and_sweep_enabled(caplog, tmp_path):
    cfg_path = tmp_path / "ralph.toml"
    cfg_path.write_text('workspace_root = "/tmp"\n')  # no bot_author_email
    caplog.set_level(logging.WARNING, logger="ralph_executor")
    cfg = load_config(cfg_path)
    cli.validate_startup(cfg)  # the new function under test
    msgs = [r.getMessage() for r in caplog.records]
    assert any("bot_author_email" in m and ("sweep" in m or "autobug" in m) for m in msgs)


def test_startup_no_warning_when_bot_author_email_set(caplog, tmp_path):
    cfg_path = tmp_path / "ralph.toml"
    cfg_path.write_text('workspace_root = "/tmp"\nbot_author_email = "bot@x"\n')
    caplog.set_level(logging.WARNING, logger="ralph_executor")
    cfg = load_config(cfg_path)
    cli.validate_startup(cfg)
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("bot_author_email" in m for m in msgs)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/executor/test_cli.py -v -k validate_startup`
Expected: FAIL — `cli.validate_startup` not defined.

- [ ] **Step 4: Implement `validate_startup`**

In `ralph_executor/cli.py`:

```python
def validate_startup(cfg: ExecutorConfig) -> None:
    """One-time validation called at executor startup.

    Logs WARNINGs for soft misconfigurations that are recoverable but
    would silently disable a feature (e.g. missing bot_author_email
    causes sweep AND autobug to abort per-iteration). The loop still
    starts; the operator gets one banner-shaped warning instead of one
    warning per iteration.
    """
    if not cfg.bot_author_email and (cfg.autobug_enabled or cfg.sweep_enabled):
        log.warning(
            "ralph startup: bot_author_email is not set; sweep AND autobug "
            "will abort on every iteration. Set TOML key 'bot_author_email' "
            "or env RALPH_ADO_AUTHOR_EMAIL."
        )
```

And in `main` (or wherever the loop starts), call `validate_startup(cfg)` once before entering the loop.

- [ ] **Step 5: Run tests**

Run: `pytest tests/executor/test_cli.py -v -k validate_startup`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ralph_executor/cli.py tests/executor/test_cli.py
git commit -m "feat(cli): one-time validate_startup warns on missing bot_author_email"
```

---

## Phase 7 — Wiring (events, claude_spawn, cli, loop)

### Task 16: `autobug_emitted` EventType

**Files:**
- Modify: `ralph_executor/safety/events.py`
- Modify: `tests/safety/test_events.py` (or wherever events tests live — locate first)

- [ ] **Step 1: Locate the events tests**

Run: `grep -l "EventType\." tests/ -r | head -5`

- [ ] **Step 2: Write failing test**

Append to the located test file:

```python
def test_event_type_includes_autobug_emitted():
    from ralph_executor.safety.events import EventType
    assert EventType.AUTOBUG_EMITTED is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest <located test file> -v -k autobug`
Expected: FAIL — `EventType` has no attribute `AUTOBUG_EMITTED`.

- [ ] **Step 4: Add the variant**

In `ralph_executor/safety/events.py`, add to the `EventType` enum:

```python
    AUTOBUG_EMITTED = "autobug_emitted"
```

- [ ] **Step 5: Run test**

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ralph_executor/safety/events.py tests/safety/test_events.py
git commit -m "feat(safety/events): add AUTOBUG_EMITTED EventType variant"
```

---

### Task 17: `claude_spawn` — subprocess crash wire + `RALPH_AUTOBUG_DEPTH` env

**Files:**
- Modify: `ralph_executor/claude_spawn.py`
- Modify: `tests/executor/test_claude_spawn.py`

- [ ] **Step 1: Write failing test for the env-marker injection**

Append to `tests/executor/test_claude_spawn.py`:

```python
def test_build_env_sets_RALPH_AUTOBUG_DEPTH_when_pbi_has_signature(tmp_path):
    """When the claimed PBI is an autobug PBI, the subagent's env must
    carry RALPH_AUTOBUG_DEPTH=1 so its own crashes don't recursively emit."""
    from ralph_executor.claude_spawn import _build_env
    # Construct a PBI dataclass whose frontmatter has signature
    # (use whatever helper the existing tests use to build a PBI)
    pbi = _make_pbi_with_frontmatter(tmp_path, signature="abc123")
    env = _build_env(pbi=pbi, base_env={})
    assert env.get("RALPH_AUTOBUG_DEPTH") == "1"


def test_build_env_does_not_set_RALPH_AUTOBUG_DEPTH_for_normal_pbi(tmp_path):
    from ralph_executor.claude_spawn import _build_env
    pbi = _make_pbi_with_frontmatter(tmp_path)  # no signature
    env = _build_env(pbi=pbi, base_env={})
    assert "RALPH_AUTOBUG_DEPTH" not in env
```

(The implementer must write `_make_pbi_with_frontmatter` as a local test helper that mirrors how existing claude_spawn tests construct PBIs — read the conftest to see the pattern.)

- [ ] **Step 2: Write failing test for the subprocess crash wire**

Append to the same file:

```python
def test_spawn_calls_autobug_detect_subprocess_crash_on_non_zero_exit(monkeypatch, tmp_path):
    calls = []
    from ralph_executor import autobug
    monkeypatch.setattr(autobug, "detect_subprocess_crash",
                       lambda **kw: calls.append(kw))
    # invoke spawn_claude_p with a stubbed subprocess that exits 1
    # ... (use existing test scaffolding for spawn)
    assert calls, "detect_subprocess_crash must be called when subprocess exits non-zero"
    assert calls[0]["exit_code"] == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/executor/test_claude_spawn.py -v -k "autobug or RALPH_AUTOBUG"`
Expected: FAIL

- [ ] **Step 4: Add the marker injection and the subprocess crash wire**

In `ralph_executor/claude_spawn.py`:

In `_build_env` (after the existing env construction), add:

```python
    # Recursion guard: if the claimed PBI is an autobug PBI, mark the
    # subagent's env so any nested crash detection refuses to emit.
    if _pbi_has_signature_frontmatter(pbi):
        env["RALPH_AUTOBUG_DEPTH"] = "1"
```

Add the helper:

```python
import re as _re
_SIG_FM_RE = _re.compile(r"^signature:\s*[0-9a-f]+\s*$", _re.MULTILINE)


def _pbi_has_signature_frontmatter(pbi) -> bool:
    """True if the PBI's entry file (BUG.md or other) has a signature: key in frontmatter."""
    if pbi is None or not hasattr(pbi, "path"):
        return False
    for entry in ("BUG.md", "PBI.md", "FEEDBACK.md"):
        f = pbi.path / entry
        if f.is_file():
            try:
                return bool(_SIG_FM_RE.search(f.read_text(encoding="utf-8")))
            except OSError:
                return False
    return False
```

In the subprocess wait path (where `error` is classified — locate via `grep -n "error" claude_spawn.py | grep -i classify`), wrap the error event emission:

```python
    if exit_code != 0:
        # existing error classification ...
        try:
            from ralph_executor import autobug
            ctx = _build_autobug_context(cfg, pbi)
            autobug.detect_subprocess_crash(
                exit_code=exit_code, stderr=tail_of_stderr, command=argv,
                ctx=ctx, target_repo=_RALPH_REPO_URL_CONST,
            )
        except Exception as inner:
            log.warning("autobug subprocess wire failed: %s", inner)
```

`_build_autobug_context` and `_RALPH_REPO_URL_CONST` are small helpers — the implementer writes them by reading the existing config + env wiring in claude_spawn. `_RALPH_REPO_URL_CONST` defaults to a string read from `cfg.autobug_target_repo` (added in Task 14 if needed — confirm or extend).

- [ ] **Step 5: Run tests**

Run: `pytest tests/executor/test_claude_spawn.py -v`
Expected: PASS (all tests including new ones)

- [ ] **Step 6: Commit**

```bash
git add ralph_executor/claude_spawn.py tests/executor/test_claude_spawn.py
git commit -m "feat(claude_spawn): RALPH_AUTOBUG_DEPTH marker + subprocess crash wire"
```

---

### Task 18: `cli.py` — outer try/except wrapper

**Files:**
- Modify: `ralph_executor/cli.py`
- Modify: `tests/executor/test_cli.py`

- [ ] **Step 1: Write failing test for the outer wrapper**

Append to `tests/executor/test_cli.py`:

```python
def test_outer_handler_reraises_original_after_autobug_fails(monkeypatch, caplog):
    """If autobug itself raises, the original exception must still propagate."""
    from ralph_executor import autobug, cli
    monkeypatch.setattr(autobug, "detect_python_crash",
                       lambda *a, **kw: (_ for _ in ()).throw(TypeError("autobug bug")))
    def boom_loop(cfg):
        raise RuntimeError("original crash")
    monkeypatch.setattr(cli, "run_loop", boom_loop)
    caplog.set_level(logging.ERROR, logger="ralph_executor")
    with pytest.raises(RuntimeError, match="original crash"):
        cli.main_with_autobug_wrapper(cfg=_minimal_cfg())  # the new wrapper
    # AND the autobug failure was logged
    assert any("AUTOBUG FAILED" in r.getMessage() for r in caplog.records)
```

(The implementer writes `_minimal_cfg()` using whatever scaffolding existing cli tests use.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/executor/test_cli.py -v -k outer_handler`
Expected: FAIL — `cli.main_with_autobug_wrapper` not defined.

- [ ] **Step 3: Add the wrapper**

In `ralph_executor/cli.py`:

```python
from ralph_executor import autobug as _autobug


def _build_autobug_context_for_cli(cfg) -> _autobug.Context:
    """Build a Context for top-level crash capture (no triggering PBI)."""
    from datetime import datetime, timezone
    return _autobug.Context(
        queue_root=cfg.workspace_root / "queue",
        state_dir=cfg.workspace_root / "queue" / ".ralph" / "state",
        env=dict(os.environ),
        now=datetime.now(tz=timezone.utc),
        ralph_sha=_resolve_ralph_sha(),  # reuse existing helper if present
        bot_author_email=cfg.bot_author_email,
        triggering_pbi_id=None,
    )


def main_with_autobug_wrapper(cfg) -> None:
    """Run the executor loop wrapped in a top-level autobug try/except.

    Guarantees:
    1. Any unhandled exception from run_loop is captured by autobug.
    2. Even if autobug itself raises, the ORIGINAL exception is re-raised
       (so exit code reflects the real crash, not the autobug failure).
    3. The ORIGINAL traceback is what the operator sees, not autobug's.
    """
    try:
        run_loop(cfg)
    except BaseException as original_exc:
        if cfg.autobug_enabled:
            try:
                ctx = _build_autobug_context_for_cli(cfg)
                _autobug.detect_python_crash(
                    original_exc, ctx,
                    target_repo=getattr(cfg, "autobug_target_repo", ""),
                    severity=cfg.autobug_severity_python_crash,
                )
            except BaseException as autobug_exc:
                log.error(
                    "AUTOBUG FAILED while handling original crash. "
                    "Original: %r. Autobug: %r",
                    original_exc, autobug_exc,
                    exc_info=original_exc,
                )
        raise original_exc
```

And update `main` (or whichever entry point currently calls `run_loop(cfg)`) to call `main_with_autobug_wrapper(cfg)` instead.

- [ ] **Step 4: Run test**

Run: `pytest tests/executor/test_cli.py -v -k outer_handler`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/cli.py tests/executor/test_cli.py
git commit -m "feat(cli): top-level autobug wrapper preserves original exception"
```

---

### Task 19: `loop.py` — internal wires at swallowed-except sites

**Files:**
- Modify: `ralph_executor/loop.py`
- Modify: `tests/executor/test_loop.py`

- [ ] **Step 1: Locate the wire sites**

Run: `grep -n "except.*as.*e:\|except.*as.*exc:" ralph_executor/loop.py`

Identify each `except` block that:
- Catches a real error (not control-flow like `StopIteration`)
- Logs and converts to a local outcome rather than re-raising
- Is OUTSIDE the outer cli wrapper's reach

Common candidates (verify in source):
- `claim_next_pbi` failure path
- Sweep invocation failure
- Post-iteration error classification

- [ ] **Step 2: Write a failing test for one wire site**

Append to `tests/executor/test_loop.py`:

```python
def test_loop_wires_autobug_on_internal_exception(monkeypatch, tmp_path):
    """An exception swallowed inside iterate_once must trigger autobug."""
    calls = []
    from ralph_executor import autobug
    monkeypatch.setattr(autobug, "detect_python_crash",
                       lambda exc, ctx, **kw: calls.append(exc))
    # Force claim_next_pbi (or another wired site) to raise a known exception.
    # Use existing test scaffolding to drive iterate_once with a stubbed queue.
    # ... (implementer fills in based on test_loop.py patterns)
    assert calls, "expected detect_python_crash to be called for the swallowed exception"
```

- [ ] **Step 3: Run test to verify it fails**

Expected: FAIL — `calls` is empty.

- [ ] **Step 4: Add wires at each identified site**

For each wire site in `loop.py`:

```python
except SpecificError as e:
    log.warning("...", e)
    # NEW: autobug wire
    if cfg.autobug_enabled:
        try:
            ctx = _build_autobug_context_for_loop(cfg, current_pbi)
            from ralph_executor import autobug
            autobug.detect_python_crash(e, ctx, target_repo=getattr(cfg, "autobug_target_repo", ""))
        except Exception as autobug_exc:
            log.warning("autobug wire failed: %s", autobug_exc)
    # existing handler continues
```

`_build_autobug_context_for_loop` is a private helper that builds a Context from the cfg + currently-claimed PBI (so `triggering_pbi_id` is populated when known).

- [ ] **Step 5: Run tests**

Run: `pytest tests/executor/test_loop.py -v`
Expected: PASS (including new test)

- [ ] **Step 6: Commit**

```bash
git add ralph_executor/loop.py tests/executor/test_loop.py
git commit -m "feat(loop): wire autobug into internal swallowed-exception sites"
```

---

## Phase 8 — Prompt updates

### Task 20: Bug-workflow signature-aware conditional

**Files:**
- Modify: `prompt/03-workflow/bug/workflow.md`
- Modify: `tests/test_prompt_contents.py`

- [ ] **Step 1: Add a failing prompt-contents test**

Append to `tests/test_prompt_contents.py`:

```python
def test_bug_workflow_documents_autobug_signature_conditional():
    text = Path("prompt/03-workflow/bug/workflow.md").read_text(encoding="utf-8")
    # The amended step 2 must explicitly mention signature-aware behaviour
    assert "signature:" in text
    assert "autobug" in text.lower()
    # And must NOT auto-STUCK autobug PBIs on first read
    assert "first task is to build the reproduction" in text \
        or "first task is to convert these observations" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prompt_contents.py -v -k bug_workflow`
Expected: FAIL — those strings not present.

- [ ] **Step 3: Edit `prompt/03-workflow/bug/workflow.md`**

Find step 2 (Reproduce). After the existing "If you cannot reproduce, write STUCK.md..." sentence, add this paragraph:

```
**If `BUG.md` frontmatter contains `signature: <hex>`** (autobug-emitted),
REPRODUCE.md may contain only observed artefacts, not runnable commands.
Your iteration's first task is to convert these observations into runnable
reproduction commands. The "cannot reproduce → STUCK" rule applies only
after a documented investigation attempt, not on initial read.

**Otherwise** (human-authored bug): the existing rule stands — REPRODUCE.md
must contain runnable commands; if they don't fire, write STUCK.md
immediately.
```

- [ ] **Step 4: Run test**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prompt/03-workflow/bug/workflow.md tests/test_prompt_contents.py
git commit -m "feat(prompt): bug workflow signature-aware conditional for autobug PBIs"
```

---

### Task 21: Memory prompt note about autobug PBIs

**Files:**
- Modify: `prompt/06-memory/memory.md`
- Modify: `tests/test_prompt_contents.py`

- [ ] **Step 1: Add a failing prompt-contents test**

Append to `tests/test_prompt_contents.py`:

```python
def test_memory_section_mentions_autobug_marker():
    text = Path("prompt/06-memory/memory.md").read_text(encoding="utf-8")
    assert "signature:" in text and "autobug" in text.lower()
    assert "RALPH_AUTOBUG_DEPTH" in text
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL

- [ ] **Step 3: Append a one-line note to `prompt/06-memory/memory.md`**

Add to the bottom of the file (or to the most relevant subsection):

```markdown
### Autobug-emitted PBIs

If you see frontmatter `signature: <hex>` on the BUG.md of the PBI
you're working, this is an autobug — it was auto-captured at crash
time. Your spawn env carries `RALPH_AUTOBUG_DEPTH=1` so any further
crash inside your iteration will NOT emit nested autobugs. Focus on
building the reproduction (see bug workflow step 2).
```

- [ ] **Step 4: Run test**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prompt/06-memory/memory.md tests/test_prompt_contents.py
git commit -m "feat(prompt): memory section notes the autobug signature marker"
```

---

## Phase 9 — Integration tests + corpus tooling

### Task 22: Roundtrip integration test (autobug extras survive movements)

**Files:**
- Create: `tests/executor/autobug/integration/__init__.py`
- Create: `tests/executor/autobug/integration/test_roundtrip_preserves_extras.py`

- [ ] **Step 1: Write the integration test**

`tests/executor/autobug/integration/__init__.py`:

```python
```

`tests/executor/autobug/integration/test_roundtrip_preserves_extras.py`:

```python
"""Verify autobug extras survive every queue movement.

Load-bearing for autobug dedup. If a future refactor of
movements._rewrite_status switches to dataclass-roundtrip
serialisation, the autobug-specific frontmatter keys silently
disappear. This test fails loudly when that happens.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ralph_executor.autobug import emit
from ralph_executor.autobug.context import Context
from ralph_executor.queue.filesystem import parse_pbi_directory
from ralph_executor.queue.movements import (
    move_inbox_to_current,
    move_current_to_pending_pr,
)

AUTOBUG_EXTRA_KEYS = (
    "signature",
    "trigger_kind",
    "occurrences",
    "first_seen",
    "last_seen",
    "regression_of",
    "triggering_pbi",
    "ralph_sha",
)


@pytest.fixture
def real_queue(tmp_path):
    """Initialise a real git-backed queue clone (use existing test scaffolding
    pattern from tests/executor/conftest.py — replicate or import)."""
    # ... uses the project's existing fake_repo / queue scaffolding
    ...


@pytest.fixture(autouse=True)
def stub_push(monkeypatch):
    from ralph_executor import git_ops
    monkeypatch.setattr(git_ops, "push_with_rebase", lambda *a, **kw: None)


def _read_frontmatter_keys(bug_md: Path) -> set[str]:
    text = bug_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return set()
    end = text.find("\n---", 4)
    block = text[4:end] if end > 0 else ""
    keys: set[str] = set()
    for line in block.splitlines():
        if ":" in line:
            keys.add(line.split(":", 1)[0].strip())
    return keys


def test_autobug_extras_survive_inbox_to_current_to_pending_pr(real_queue):
    cfg = real_queue.cfg  # adapt to your fixture's surface
    ctx = Context(
        queue_root=cfg.workspace_root / "queue",
        state_dir=cfg.workspace_root / "queue" / ".ralph" / "state",
        env={},
        now=datetime(2026, 5, 31, tzinfo=timezone.utc),
        ralph_sha="testsha",
        bot_author_email="bot@x",
        triggering_pbi_id="WI-1",
    )
    pbi_id = emit.new(
        sig="a" * 64, exc=ValueError("x"), trigger_kind="python_crash",
        severity="critical", target_repo="https://example.com/ralph", ctx=ctx,
    )
    pbi_dir = cfg.workspace_root / "queue" / ".ralph" / "inbox" / pbi_id
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    pbi = move_inbox_to_current(cfg, pbi)
    pbi = move_current_to_pending_pr(cfg, pbi, pr_url="https://example.com/pr/1")
    # After two movements, every autobug extra must still be in the BUG.md frontmatter
    final = cfg.workspace_root / "queue" / ".ralph" / "pending-pr" / pbi_id / "BUG.md"
    keys = _read_frontmatter_keys(final)
    missing = [k for k in AUTOBUG_EXTRA_KEYS if k not in keys]
    assert not missing, f"movements stomped autobug extras: {missing}"
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/executor/autobug/integration/test_roundtrip_preserves_extras.py -v`
Expected: PASS (line-based _rewrite_status preserves keys; if it fails, autobug is broken — investigate before merging).

- [ ] **Step 3: Commit**

```bash
git add tests/executor/autobug/integration/
git commit -m "test(autobug): roundtrip test — autobug extras survive movements"
```

---

### Task 23: Python crash e2e integration test

**Files:**
- Create: `tests/executor/autobug/integration/test_python_crash_e2e.py`

- [ ] **Step 1: Write the e2e test**

```python
"""End-to-end: trigger a Python crash inside run_loop, assert autobug PBI lands in inbox."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def real_queue(...):  # reuse the project's scaffolding fixture
    ...


def test_python_crash_lands_autobug_in_inbox(real_queue, monkeypatch):
    from ralph_executor import cli
    # Force run_loop to raise immediately
    def boom(cfg):
        raise RuntimeError("e2e test crash")
    monkeypatch.setattr(cli, "run_loop", boom)
    # Invoke the wrapper — it should re-raise after writing the autobug PBI
    with pytest.raises(RuntimeError, match="e2e test crash"):
        cli.main_with_autobug_wrapper(real_queue.cfg)
    inbox = real_queue.cfg.workspace_root / "queue" / ".ralph" / "inbox"
    pbis = [p for p in inbox.iterdir() if p.is_dir() and p.name.startswith("autobug-")]
    assert len(pbis) == 1
    bug = (pbis[0] / "BUG.md").read_text(encoding="utf-8")
    assert "RuntimeError" in bug
    assert "e2e test crash" in bug
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/executor/autobug/integration/test_python_crash_e2e.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/executor/autobug/integration/test_python_crash_e2e.py
git commit -m "test(autobug): e2e — python crash lands autobug in inbox"
```

---

### Task 24: Subprocess crash e2e integration test

**Files:**
- Create: `tests/executor/autobug/integration/test_subprocess_crash_e2e.py`

- [ ] **Step 1: Write the e2e test**

```python
"""End-to-end: simulate a non-zero subprocess exit, assert autobug PBI lands."""
from __future__ import annotations

import pytest


def test_subprocess_non_zero_exit_lands_autobug(real_queue, monkeypatch):
    # Use the project's spawn-test scaffolding to stub a claude subprocess
    # that exits 137 with stderr "Killed (OOM)".
    from ralph_executor import claude_spawn
    # ... invoke spawn_claude_p with stubs
    # After the call, assert the inbox has an autobug PBI with trigger_kind=subprocess_crash
    inbox = real_queue.cfg.workspace_root / "queue" / ".ralph" / "inbox"
    pbis = [p for p in inbox.iterdir() if p.is_dir() and p.name.startswith("autobug-")]
    assert len(pbis) == 1
    fm = (pbis[0] / "BUG.md").read_text(encoding="utf-8")
    assert "trigger_kind: subprocess_crash" in fm
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/executor/autobug/integration/test_subprocess_crash_e2e.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/executor/autobug/integration/test_subprocess_crash_e2e.py
git commit -m "test(autobug): e2e — subprocess crash lands autobug in inbox"
```

---

### Task 25: Recursion guard e2e integration test

**Files:**
- Create: `tests/executor/autobug/integration/test_recursion_guard_e2e.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end: when working an autobug PBI, the subagent's env carries
RALPH_AUTOBUG_DEPTH=1 and a crash inside the subagent does NOT emit a nested PBI."""
from __future__ import annotations

import pytest


def test_subagent_env_carries_RALPH_AUTOBUG_DEPTH(real_queue, monkeypatch):
    """Inspect the actual env passed to the subagent's subprocess.Popen call."""
    captured_env: dict[str, str] = {}
    real_popen = None

    def capture_env_popen(argv, *, env=None, **kwargs):
        nonlocal captured_env
        if env:
            captured_env = dict(env)
        return real_popen(argv, env=env, **kwargs) if real_popen else None

    import subprocess as _subprocess
    monkeypatch.setattr(_subprocess, "Popen", capture_env_popen)

    # Claim an autobug PBI (write it directly to current/ for the test)
    # Then drive one iteration of claude_spawn against it
    # ... (use existing spawn-test scaffolding)
    assert captured_env.get("RALPH_AUTOBUG_DEPTH") == "1"


def test_nested_autobug_emission_is_blocked(real_queue, monkeypatch):
    """If detect is called with RALPH_AUTOBUG_DEPTH=1 in env, no PBI is created."""
    from ralph_executor.autobug import detect
    from ralph_executor.autobug.context import Context
    from datetime import datetime, timezone
    ctx = Context(
        queue_root=real_queue.cfg.workspace_root / "queue",
        state_dir=real_queue.cfg.workspace_root / "queue" / ".ralph" / "state",
        env={"RALPH_AUTOBUG_DEPTH": "1"},
        now=datetime(2026, 5, 31, tzinfo=timezone.utc),
        ralph_sha="testsha",
        bot_author_email="bot@x",
    )
    try:
        raise RuntimeError("nested crash")
    except RuntimeError as exc:
        detect.detect_python_crash(exc, ctx, target_repo="https://example.com/ralph")
    inbox = real_queue.cfg.workspace_root / "queue" / ".ralph" / "inbox"
    autobug_pbis = [p for p in inbox.iterdir() if p.name.startswith("autobug-")]
    assert not autobug_pbis, "recursion guard should have blocked emission"
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/executor/autobug/integration/test_recursion_guard_e2e.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/executor/autobug/integration/test_recursion_guard_e2e.py
git commit -m "test(autobug): e2e — recursion guard blocks nested emission"
```

---

### Task 26: Corpus extractor script

**Files:**
- Create: `scripts/build_autobug_corpus.py`

- [ ] **Step 1: Write the script**

`scripts/build_autobug_corpus.py`:

```python
"""Extract stderr blobs from a local events.db into the autobug signature corpus.

Walks every events.db under the user's workspace_root, pulls every
`error`-class event's stderr field, and writes one .txt per blob under
``tests/executor/autobug/fixtures/stderr_corpus/raw/``. The operator
then sorts the raw blobs into bucket subdirectories by root cause
before merging.

Usage:
    uv run python scripts/build_autobug_corpus.py --workspace-root /path/to/workspaces

The script is one-off operator tooling, not a pytest fixture. It does
NOT delete or modify existing buckets — only writes to raw/.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _find_event_dbs(workspace_root: Path) -> list[Path]:
    return list(workspace_root.glob("*/queue/.ralph/events.db"))


def _extract_stderrs(db: Path) -> list[str]:
    out: list[str] = []
    try:
        con = sqlite3.connect(str(db))
        cur = con.execute("SELECT payload FROM events WHERE kind = 'error'")
        for (payload,) in cur:
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            stderr = data.get("stderr") or data.get("tail_of_stderr")
            if stderr:
                out.append(stderr)
        con.close()
    except sqlite3.DatabaseError:
        pass
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace-root", type=Path, required=True)
    p.add_argument(
        "--out",
        type=Path,
        default=Path("tests/executor/autobug/fixtures/stderr_corpus/raw"),
    )
    args = p.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    n = 0
    for db in _find_event_dbs(args.workspace_root):
        for i, stderr in enumerate(_extract_stderrs(db)):
            out_path = args.out / f"{db.parent.parent.name}-{i:04d}.txt"
            out_path.write_text(stderr, encoding="utf-8")
            n += 1
    print(f"wrote {n} blobs to {args.out}")
    print("Sort them into bucket subdirectories by root cause before merging.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke test**

Run: `uv run python scripts/build_autobug_corpus.py --workspace-root /nonexistent 2>&1 || true`
Expected: prints `wrote 0 blobs to ...` (graceful handling of missing source).

- [ ] **Step 3: Commit**

```bash
git add scripts/build_autobug_corpus.py
git commit -m "feat(autobug): scripts/build_autobug_corpus.py — operator corpus extractor"
```

---

## Final verification

After all 26 tasks land:

- [ ] **Run the full test suite**

```bash
uv run pytest tests/executor/autobug/ tests/executor/test_cli.py tests/executor/test_loop.py tests/executor/test_claude_spawn.py tests/executor/test_config.py tests/safety/ tests/test_prompt_contents.py -v
```

Expected: all green.

- [ ] **Confirm autobug can be disabled**

Manually verify: with `autobug_enabled = false` in TOML, none of the wires fire and no PBIs are emitted. (Spot-check via a 1-iteration run against a known-crashing test PBI.)

- [ ] **Update the spec to "Implemented"**

Edit `docs/superpowers/specs/2026-05-31-ralph-autobug-design.md` header:

```diff
- **Status:** Draft, pending user review.
+ **Status:** Implemented (v1) — commits <range>.
```

Commit:

```bash
git add docs/superpowers/specs/2026-05-31-ralph-autobug-design.md
git commit -m "docs(spec): autobug — mark v1 implemented"
```

---

## Self-review

**Spec coverage:**
- Architecture / module layout → Tasks 1-13
- Frontmatter + REPRODUCE.md shape → Tasks 9, 10
- Signature computation (Python + subprocess + corpus) → Tasks 2, 3, 4
- Dedup → Task 7
- Fuses (recursion + rate + rollup) → Tasks 5, 6
- Defensive composer → Tasks 8, 9, 10
- Detection orchestration → Task 12
- Bug workflow amendment → Task 20
- Memory prompt amendment → Task 21
- Integration with cli/loop/claude_spawn/config/events → Tasks 14-19
- Roundtrip safety test → Task 22
- E2E tests → Tasks 23, 24, 25
- Corpus tooling → Task 26
- Concern 3 rollin (bot_author_email validation) → Task 15

All spec sections covered.

**Placeholder scan:**
- The `_make_pbi_with_frontmatter`, `_minimal_cfg`, and `real_queue` fixture references in Tasks 17, 18, 22, 23, 24, 25 ARE deferred to existing project scaffolding — the implementer reads the existing test files for the pattern. This is documented inline; not a TBD on the design.
- No "implement later" / "add appropriate error handling" / "similar to Task N" patterns.

**Type consistency:**
- `Context` fields used identically across all tasks (`queue_root`, `state_dir`, `env`, `now`, `ralph_sha`, `bot_author_email`, `triggering_pbi_id`).
- `DedupResult` field `existing_pbi_id` used consistently.
- `detect_python_crash` signature `(exc, ctx, *, target_repo, severity=...)` consistent across Tasks 12, 18, 19, 23.
- `detect_subprocess_crash` signature `(exit_code, stderr, command, ctx, *, target_repo, severity=...)` consistent across Tasks 12, 17, 24.
- Function names: `build_bug_md`, `build_reproduce_md`, `build_frontmatter`, `_safe` consistent across compose tests + emit.
- Frontmatter field names match the spec verbatim (`signature`, `trigger_kind`, `occurrences`, `first_seen`, `last_seen`, `regression_of`, `triggering_pbi`, `ralph_sha`).

Plan ready for execution.
