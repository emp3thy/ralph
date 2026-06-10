# Consolidate duplicated skill-script helpers (tech-debt: skill-script-helper-duplication)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** One definition each for the git wrapper, entry-file resolution, entry-file constants, and timestamp helper — in `scripts/queue_writer.py` — imported by ralph-triage, ralph-promote, and ralph-recover; `queue_writer`'s own subprocess call routed through `ralph_executor.subprocess_utils.run_text`.

**Architecture:** `scripts/queue_writer.py` is already the shared layer (all three skills import 11–13 symbols from it via their existing `sys.path` bootstrap). Add the four missing helpers there as public symbols, then delete the per-skill copies and import. Behavior-identical: bodies move verbatim; the only signature delta is that skills' `_git -> None` callers now call `run_git -> str` and discard the return value (same subprocess semantics, same `QueueWriterError`).

**Guardrails:** Stage specific paths [[0547374e]]. Cross-cutting tests: `tests/skills/test_supervisor_skills_smoke.py` exercises cancel/promote/triage together — run it at every task boundary [[94f87716]]. Suite green at every task [[campaign plan T9]].

**Out of scope (verified non-duplicates):**
- `skills/ralph-new/scripts/new.py:_git` — different contract by design: returns stdout, no error catching, `CalledProcessError` propagates to caller. Unifying would change error behavior.
- `_enforce_claim_ownership` (promote.py:146, cancel.py:113) — near-copies but NOT verbatim: skill-prefixed error messages ("ralph-promote:" vs "ralph-cancel:") and per-skill `_ClaimGuardError` classes. Consolidation changes observable messages — out of behavior-identical scope; candidate for a future finding.
- `skills/ralph-new/scripts/validate.py:_ALLOWED_TYPES` — validator-domain constant, not an entry-file map.

**Confidence:** T1 93% — additive code + direct unit tests; `run_text` injects exactly the kwargs `_run_git` passes today (`text=True, encoding="utf-8", errors="replace"`), so the swap is behavior-identical. T2–T4 93% each — mechanical delete+import; per-skill suites (`tests/skills/test_ralph_{triage,promote,recover}.py`) plus the supervisor smoke test lock behavior; call sites enumerated by investigator scan 2026-06-10. T5 95% — verification + PR flow proven. Floor 92% met.

---

### Task 1: Add shared helpers to queue_writer (TDD)

**Files:**
- Modify: `scripts/queue_writer.py`
- Test: `tests/scripts/test_queue_writer.py` (or the existing queue_writer test file if one exists — locate with `git grep -l "queue_writer" tests/` and extend it rather than creating a parallel file)

- [ ] **Step 1: Write failing tests** for the new public surface:

```python
def test_entry_file_constants():
    from scripts.queue_writer import ENTRY_FILE_BY_TYPE, ENTRY_FILES
    assert ENTRY_FILE_BY_TYPE == {"feature": "PBI.md", "bug": "BUG.md", "pr-feedback": "FEEDBACK.md"}
    assert ENTRY_FILES == ("PBI.md", "BUG.md", "FEEDBACK.md")

def test_resolve_entry_file_finds_each_type(tmp_path):
    from scripts.queue_writer import resolve_entry_file
    for name in ("PBI.md", "BUG.md", "FEEDBACK.md"):
        d = tmp_path / name.lower().replace(".md", "")
        d.mkdir()
        (d / name).write_text("x", encoding="utf-8")
        assert resolve_entry_file(d).name == name

def test_resolve_entry_file_missing_raises(tmp_path):
    import pytest
    from scripts.queue_writer import QueueWriterError, resolve_entry_file
    with pytest.raises(QueueWriterError, match="no entry file"):
        resolve_entry_file(tmp_path)

def test_now_iso_shape():
    from scripts.queue_writer import now_iso
    val = now_iso()
    assert val.endswith("+00:00") and "T" in val and "." not in val

def test_run_git_success_and_failure(tmp_path):
    import pytest
    from scripts.queue_writer import QueueWriterError, run_git
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    assert "true" in run_git(tmp_path, "rev-parse", "--is-inside-work-tree")
    with pytest.raises(QueueWriterError, match="git nonsense-cmd failed"):
        run_git(tmp_path, "nonsense-cmd")
```

- [ ] **Step 2: Run tests, verify FAIL** (ImportError on the new names).

- [ ] **Step 3: Implement in `scripts/queue_writer.py`:**
  - Add near the existing constants:

```python
ENTRY_FILE_BY_TYPE = {
    "feature": "PBI.md",
    "bug": "BUG.md",
    "pr-feedback": "FEEDBACK.md",
}
ENTRY_FILES = tuple(ENTRY_FILE_BY_TYPE.values())
```

  - Add after `_run_git` (line ~62):

```python
run_git = _run_git  # public alias; skills discard the str return when they only need side effects


def resolve_entry_file(pbi_dir: Path) -> Path:
    for candidate in ENTRY_FILES:
        path = pbi_dir / candidate
        if path.is_file():
            return path
    raise QueueWriterError(f"no entry file (PBI.md, BUG.md, or FEEDBACK.md) found in {pbi_dir}")
```

  - Rename `_now_iso` (line ~438) to `now_iso` and update its internal call sites in queue_writer (grep `_now_iso` within the file); keep `_now_iso = now_iso` alias only if external code references it (grep first — investigator scan says none do).
  - Rewrite `_run_git`'s body to use `run_text`:

```python
from ralph_executor.subprocess_utils import run_text

def _run_git(repo: Path, *args: str) -> str:
    try:
        result = run_text(["git", *args], cwd=str(repo), check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise QueueWriterError(f"git {' '.join(args)} failed ({exc.returncode}): {stderr}") from exc
    return result.stdout.strip()
```

    (`run_text` injects `text=True, encoding="utf-8", errors="replace"` — identical to the kwargs being removed. Note the alias assignment `run_git = _run_git` must appear AFTER the def it aliases.)
  - Add the new names to the module's public export list if `__all__` exists.

- [ ] **Step 4: Run new tests + module suite** — `uv run pytest tests/ -k queue_writer -q` then `uv run pytest tests/skills tests/scripts -q`. All green.

- [ ] **Step 5: Commit** — `git add scripts/queue_writer.py <testfile> && git commit -m "refactor(queue_writer): add shared entry-file, git, timestamp helpers"`

### Task 2: ralph-triage imports shared helpers

**Files:**
- Modify: `skills/ralph-triage/scripts/triage.py`

- [ ] **Step 1:** Delete local `ENTRY_FILE_BY_TYPE` (lines 41–45), `_now_iso` (130–131), `_resolve_entry_file` (134–139), `_git` (142–155). Extend the existing `from scripts.queue_writer import (...)` block (lines 24–37) with `ENTRY_FILE_BY_TYPE, now_iso, resolve_entry_file, run_git`. Update call sites: `_now_iso()` → `now_iso()`, `_resolve_entry_file(` → `resolve_entry_file(`, `_git(` → `run_git(`. Remove now-unused `subprocess` / `datetime` imports if nothing else uses them (check with grep before deleting).
- [ ] **Step 2:** `uv run pytest tests/skills/test_ralph_triage.py tests/skills/test_supervisor_skills_smoke.py -q` — green.
- [ ] **Step 3:** Commit — `git add skills/ralph-triage/scripts/triage.py && git commit -m "refactor(ralph-triage): use shared queue_writer helpers"`

### Task 3: ralph-promote imports shared helpers

**Files:**
- Modify: `skills/ralph-promote/scripts/promote.py`

- [ ] **Step 1:** Same operation: delete `ENTRY_FILE_BY_TYPE` (45–49), `_now_iso` (142–143), `_resolve_entry_file` (169–174), `_git` (177–190); extend the queue_writer import; update call sites; prune dead imports. Do NOT touch `_enforce_claim_ownership`.
- [ ] **Step 2:** `uv run pytest tests/skills/test_ralph_promote.py tests/skills/test_supervisor_skills_smoke.py -q` — green.
- [ ] **Step 3:** Commit — `git add skills/ralph-promote/scripts/promote.py && git commit -m "refactor(ralph-promote): use shared queue_writer helpers"`

### Task 4: ralph-recover imports shared helpers

**Files:**
- Modify: `skills/ralph-recover/scripts/recover.py`

- [ ] **Step 1:** Delete local `ENTRY_FILES` (line 52), `_resolve_entry_file` (145–150), `_git` (153–166); extend the queue_writer import with `ENTRY_FILES, resolve_entry_file, run_git`; update call sites; prune dead imports. Do NOT touch `_read_claim_audit`.
- [ ] **Step 2:** `uv run pytest tests/skills/test_ralph_recover.py tests/skills/test_supervisor_skills_smoke.py -q` — green.
- [ ] **Step 3:** Commit — `git add skills/ralph-recover/scripts/recover.py && git commit -m "refactor(ralph-recover): use shared queue_writer helpers"`

### Task 5: Full gates, PR

- [ ] **Step 1:** `uv run pytest -q` (full suite), `uv run ruff check .`, `uv run mypy ralph_executor` — all green.
- [ ] **Step 2:** Duplication check: `git grep -n "_resolve_entry_file\|ENTRY_FILE_BY_TYPE\|_now_iso" skills/` → only call-site-free results expected (no local definitions remain in triage/promote/recover).
- [ ] **Step 3:** Docs sweep: `git grep -rn "ENTRY_FILE_BY_TYPE\|_resolve_entry_file" docs/ README.md` — update any hits; "docs unaffected" if none.
- [ ] **Step 4:** Push + PR per campaign plan Task 10; bot-watch to merge.
