# Add `target_repo` Field to PBI Frontmatter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a required `target_repo` field (HTTPS URL string) to every PBI's frontmatter. Migrate the 13 existing PBIs to point at `https://github.com/emp3thy/ralph`. Update `ralph-add` to auto-derive the field from the repo's git origin remote with a `--target-repo` override flag. Field is metadata-only this PBI — ralph's loop does not yet read it.

**Architecture:** Schema-only change. New required field added to `REQUIRED_FRONTMATTER_FIELDS` in `scripts/validate_samples.py` with a permissive HTTPS-URL validator. `scripts/pbi_reader.py`'s `PBIRow` dataclass gains the field. A single-use migration script `scripts/migrate_pbis_to_target_repo.py` inserts the field into every existing PBI/BUG/FEEDBACK file. `skills/ralph-add/scripts/add.py` derives the value from `git remote get-url origin` (SSH-form converted to HTTPS) or accepts an explicit `--target-repo` flag.

**Tech Stack:** Python 3.12, pytest, ruff, mypy strict, `urllib.parse`, `responses` (for ralph-add tests).

**Spec:** `docs/superpowers/specs/2026-05-27-pbi-target-repo-field-design.md`

> **Workflow note:** all `git` commands in this plan use `git -C /c/Users/gethi/ralph-queue ...` — commits go via the separate clone, NOT ralph's running checkout at `C:/Users/gethi/source/ralph`.

---

## Confidence per task

All tasks ≥ 90%. Pre-flighted against `validate_samples.py:30`, `pbi_reader.py:65`, `ralph-add/scripts/add.py:411`.

| Task | % | Notes |
|---|---|---|
| 1. Failing validation tests | 95% | `_validate_frontmatter` shape at `validate_samples.py:88` is the model. Tests go in new `tests/scripts/test_validate_samples.py`. Need `tests/scripts/__init__.py` shim. |
| 2. Implement validator + add to required | 96% | Mechanical: append to tuple at line 30; add helper after line 85. No new deps (`urllib.parse` is stdlib). |
| 3. pbi_reader carries field | 95% | Add `target_repo: str` to `PBIRow` dataclass at line 65-85; parse in `read_pbi` at line 142. |
| 4. Migration script + tests | 92% | New file; idempotent logic. Risk: CRLF line-ending handling on Windows. Mitigation: Step 4.3 reads with `text=True` + writes with `newline=""` to preserve original endings. |
| 5. Run migration + edit samples | 94% | Migration script + validation regression test. Sample edits are direct file writes; reviewable in diff. |
| 6. ralph-add auto-derive | 91% | Regex parsing of SSH form. Risk: edge-case origin URLs (e.g., `ssh://git@github.com/...` URI form). Mitigation: Step 6.3's regex covers the dominant SSH alias form; URI form falls through to the `https://` branch. Step 6.4's tests cover both. |
| 7. workitem-fetch schema | 96% | Add `target_repo` to schema's required keys list. One-line append. |
| 8. Full-suite gate | 91% | Could surface latent samples that lack the field. Mitigation: Step 5's regression test asserts every PBI under `.ralph/` AND `samples/` validates. |

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `scripts/validate_samples.py` | Modify (line 30 + new helper) | Add `target_repo` to REQUIRED tuple + `_validate_target_repo` helper + call from `_validate_frontmatter` |
| `scripts/pbi_reader.py` | Modify (line 65-85 + parse in read_pbi) | `PBIRow.target_repo: str` field + parse from frontmatter |
| `scripts/migrate_pbis_to_target_repo.py` | Create | Single-use migration; hardcoded `https://github.com/emp3thy/ralph` |
| `skills/ralph-add/scripts/add.py` | Modify (argparse + new helper + main flow) | `--target-repo` flag + `_derive_target_repo` helper + write field into new PBI |
| `skills/workitem-fetch-github/scripts/schema.py` | Modify | Add `target_repo` to required-keys list |
| `prompt/PROMPT.md` | Modify (one-line note) | Mention the field exists |
| `samples/*/PBI.md` (and BUG.md / FEEDBACK.md) | Modify | Hand-edit each canonical sample to add the field |
| `.ralph/{inbox,current,pending-pr,done,blocked}/*/{PBI,BUG,FEEDBACK}.md` | Modify | One-shot via migration script |
| `tests/scripts/__init__.py` | Create | Empty package marker |
| `tests/scripts/test_validate_samples.py` | Create | 7 validation cases |
| `tests/scripts/test_migrate_pbis.py` | Create | 4 migration cases |
| `tests/test_pbi_reader.py` | Modify | Assert PBIRow carries `target_repo` |
| `tests/skills/test_ralph_add.py` | Modify | 5 auto-derive + flag cases |

---

## Task 1: Failing validation tests

**Files:**
- Create: `tests/scripts/__init__.py` (empty package marker)
- Create: `tests/scripts/test_validate_samples.py`

- [x] **Step 1.1: Create the tests package marker**

```bash
touch /c/Users/gethi/ralph-queue/tests/scripts/__init__.py
```

Verify it exists:

```bash
ls /c/Users/gethi/ralph-queue/tests/scripts/__init__.py
```

- [x] **Step 1.2: Write the failing test file**

Create `tests/scripts/test_validate_samples.py`:

```python
"""Tests for scripts.validate_samples — focused on the target_repo field
introduced by RALPH-PBI-TARGET-REPO-FIELD."""

from __future__ import annotations

from scripts.validate_samples import _validate_frontmatter


def _valid_pbi_dict(**overrides: object) -> dict[str, object]:
    """Build a frontmatter dict that would otherwise validate cleanly.
    Caller can override any field via kwargs."""
    base: dict[str, object] = {
        "id": "WI-1",
        "type": "feature",
        "status": "inbox",
        "severity": "normal",
        "attempts": 0,
        "target_repo": "https://github.com/emp3thy/ralph",
        # Other fields validate_samples requires; add as needed by current
        # REQUIRED_FRONTMATTER_FIELDS. Use empty strings/zeros that pass
        # the per-field validators.
    }
    base.update(overrides)
    return base


def test_validate_rejects_missing_target_repo() -> None:
    pbi = _valid_pbi_dict()
    del pbi["target_repo"]
    errors = _validate_frontmatter(pbi)
    assert any("target_repo" in e for e in errors), errors


def test_validate_accepts_valid_github_url() -> None:
    pbi = _valid_pbi_dict(target_repo="https://github.com/emp3thy/ralph")
    errors = _validate_frontmatter(pbi)
    assert not any("target_repo" in e for e in errors), errors


def test_validate_accepts_github_url_with_git_suffix() -> None:
    pbi = _valid_pbi_dict(target_repo="https://github.com/emp3thy/ralph.git")
    errors = _validate_frontmatter(pbi)
    assert not any("target_repo" in e for e in errors), errors


def test_validate_accepts_ado_url() -> None:
    pbi = _valid_pbi_dict(
        target_repo="https://dev.azure.com/myorg/myproj/_git/myrepo"
    )
    errors = _validate_frontmatter(pbi)
    assert not any("target_repo" in e for e in errors), errors


def test_validate_rejects_http_target_repo() -> None:
    pbi = _valid_pbi_dict(target_repo="http://github.com/x/y")
    errors = _validate_frontmatter(pbi)
    assert any("HTTPS" in e and "target_repo" in e for e in errors), errors


def test_validate_rejects_ssh_target_repo() -> None:
    pbi = _valid_pbi_dict(target_repo="git@github.com:emp3thy/ralph.git")
    errors = _validate_frontmatter(pbi)
    assert any("HTTPS" in e and "target_repo" in e for e in errors), errors


def test_validate_rejects_target_repo_without_path() -> None:
    pbi = _valid_pbi_dict(target_repo="https://github.com")
    errors = _validate_frontmatter(pbi)
    assert any("path" in e and "target_repo" in e for e in errors), errors


def test_validate_rejects_non_string_target_repo() -> None:
    pbi = _valid_pbi_dict(target_repo=42)
    errors = _validate_frontmatter(pbi)
    assert any("string" in e and "target_repo" in e for e in errors), errors
```

- [x] **Step 1.3: Run the failing tests**

```bash
uv run pytest tests/scripts/test_validate_samples.py -v
```

Expected: all FAIL — either `KeyError: 'target_repo' not in REQUIRED_FRONTMATTER_FIELDS` (the field isn't required yet) or `ImportError` if anything else is off.

- [x] **Step 1.4: Commit**

```bash
git -C /c/Users/gethi/ralph-queue add tests/scripts/__init__.py tests/scripts/test_validate_samples.py
git -C /c/Users/gethi/ralph-queue commit -m "test(validate): failing tests for target_repo frontmatter field"
```

---

## Task 2: Implement validator + add to required fields

**Files:**
- Modify: `scripts/validate_samples.py:30` (REQUIRED_FRONTMATTER_FIELDS tuple)
- Modify: `scripts/validate_samples.py:88` (new helper + call site in `_validate_frontmatter`)

- [x] **Step 2.1: Add `urllib.parse` import**

In `scripts/validate_samples.py`, near the top imports, add (if not present):

```python
import urllib.parse
```

- [x] **Step 2.2: Add `target_repo` to REQUIRED_FRONTMATTER_FIELDS**

Find the tuple at `scripts/validate_samples.py:30`. Add the new field at the end of the tuple:

```python
REQUIRED_FRONTMATTER_FIELDS: tuple[str, ...] = (
    "id",
    "type",
    "status",
    "severity",
    "attempts",
    # ... whatever other existing required fields are here ...
    "target_repo",   # NEW
)
```

- [x] **Step 2.3: Add the validator helper**

Insert this function near the other validators in `scripts/validate_samples.py` (around line 80, before `_validate_frontmatter`):

```python
def _validate_target_repo(value: object) -> str | None:
    """Return error message string or None if valid.

    Accepted: HTTPS URL with at least 2 path segments (owner + name).
    Rejected: non-strings, non-HTTPS schemes, missing host, missing path.

    Tolerates both GitHub (https://github.com/owner/name) and Azure
    DevOps (https://dev.azure.com/org/project/_git/repo) URL shapes
    via the generic ">= 2 path segments" check. No host whitelist.
    """
    if not isinstance(value, str):
        return f"target_repo must be a string, got {type(value).__name__}"
    if not value.startswith("https://"):
        return f"target_repo must be an HTTPS URL, got {value!r}"
    parsed = urllib.parse.urlparse(value)
    if not parsed.netloc:
        return f"target_repo URL has no host: {value!r}"
    path_segments = [p for p in parsed.path.split("/") if p]
    if len(path_segments) < 2:
        return f"target_repo URL must include owner + name path: {value!r}"
    return None
```

- [x] **Step 2.4: Wire the validator into `_validate_frontmatter`**

In `_validate_frontmatter` (around line 88), after the existing per-field validators (after the `attempts` check at line 118 or wherever the last validator sits), add:

```python
    target_repo = frontmatter.get("target_repo")
    if target_repo is not None:
        err = _validate_target_repo(target_repo)
        if err is not None:
            errors.append(err)
```

(The `if target_repo is not None` guard ensures the "missing required field" error from the `missing` check on line 96 is the one that fires for absent values, not a duplicate "must be a string" from the validator.)

- [x] **Step 2.5: Run the tests — verify they pass**

```bash
uv run pytest tests/scripts/test_validate_samples.py -v
```

Expected: 8 PASS.

- [x] **Step 2.6: Run ruff + mypy**

```bash
uv run ruff check scripts/validate_samples.py tests/scripts/test_validate_samples.py
uv run mypy --strict scripts/validate_samples.py
```

Expected: green.

- [x] **Step 2.7: Commit**

```bash
git -C /c/Users/gethi/ralph-queue add scripts/validate_samples.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(validate): add target_repo to REQUIRED_FRONTMATTER_FIELDS with HTTPS-URL validator"
```

---

## Task 3: `pbi_reader.PBIRow` carries `target_repo`

**Files:**
- Modify: `scripts/pbi_reader.py:65-85` (PBIRow dataclass) + `read_pbi` parse logic
- Modify: `tests/test_pbi_reader.py`

- [x] **Step 3.1: Add a failing test**

Append to `tests/test_pbi_reader.py`:

```python
def test_read_pbi_carries_target_repo(tmp_path: Path) -> None:
    """PBIRow.target_repo must mirror the frontmatter field."""
    from scripts.pbi_reader import PBIRow, read_pbi

    repo = tmp_path / "fake-repo"
    pbi_dir = repo / ".ralph" / "inbox" / "WI-1"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        "id: WI-1\n"
        "type: feature\n"
        "status: inbox\n"
        "severity: normal\n"
        "attempts: 0\n"
        "target_repo: https://github.com/emp3thy/ralph\n"
        "---\n"
        "# Title\n",
        encoding="utf-8",
    )
    row = read_pbi(pbi_dir, repo_path=repo, repo_name="fake-repo", state="inbox")
    assert isinstance(row, PBIRow), row
    assert row.target_repo == "https://github.com/emp3thy/ralph"
```

- [x] **Step 3.2: Run the test — verify it fails**

```bash
uv run pytest tests/test_pbi_reader.py::test_read_pbi_carries_target_repo -v
```

Expected: FAIL with `AttributeError: 'PBIRow' object has no attribute 'target_repo'` or similar.

- [x] **Step 3.3: Add `target_repo` to `PBIRow` dataclass**

In `scripts/pbi_reader.py`, find `@dataclass(frozen=True) class PBIRow:` (line 65). Add the new field at the end:

```python
@dataclass(frozen=True)
class PBIRow:
    """A successfully-parsed PBI from a queue folder."""

    repo_path: Path
    repo_name: str
    state: str
    pbi_dir: Path
    pbi_id: str
    pbi_type: str
    severity: str
    attempts: int
    created_at: datetime | None
    updated_at: datetime | None
    title: str
    target_repo: str   # NEW
```

- [x] **Step 3.4: Parse the field in `read_pbi`**

In `read_pbi` (around line 142), find the `return PBIRow(...)` block. Add the new kwarg:

```python
    return PBIRow(
        # ... existing kwargs ...
        target_repo=str(frontmatter.get("target_repo") or ""),
    )
```

(Use `or ""` so a missing field yields empty string rather than `None`; validation already enforces presence on disk, but the reader is tolerant per its docstring.)

- [x] **Step 3.5: Run the test — verify it passes**

```bash
uv run pytest tests/test_pbi_reader.py::test_read_pbi_carries_target_repo -v
```

Expected: PASS.

- [x] **Step 3.6: Run ruff + mypy**

```bash
uv run ruff check scripts/pbi_reader.py
uv run mypy --strict scripts/pbi_reader.py
```

- [x] **Step 3.7: Commit**

```bash
git -C /c/Users/gethi/ralph-queue add scripts/pbi_reader.py tests/test_pbi_reader.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(pbi-reader): PBIRow carries target_repo from frontmatter"
```

---

## Task 4: Migration script + tests

**Files:**
- Create: `scripts/migrate_pbis_to_target_repo.py`
- Create: `tests/scripts/test_migrate_pbis.py`

- [x] **Step 4.1: Write the failing tests first**

Create `tests/scripts/test_migrate_pbis.py`:

```python
"""Tests for scripts.migrate_pbis_to_target_repo."""

from __future__ import annotations

from pathlib import Path


def _make_pbi(pbi_dir: Path, *, with_target_repo: bool) -> Path:
    pbi_dir.mkdir(parents=True)
    target_line = (
        "target_repo: https://github.com/emp3thy/ralph\n"
        if with_target_repo
        else ""
    )
    entry = pbi_dir / "PBI.md"
    entry.write_text(
        "---\n"
        "id: WI-1\n"
        "type: feature\n"
        "status: inbox\n"
        "severity: normal\n"
        "attempts: 0\n"
        f"{target_line}"
        "---\n"
        "# Title\n",
        encoding="utf-8",
    )
    return entry


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_migrate_adds_target_repo_to_missing_pbi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PBI without the field gets it injected before the closing fence."""
    from scripts.migrate_pbis_to_target_repo import _insert_field

    entry = _make_pbi(tmp_path / ".ralph" / "inbox" / "WI-1", with_target_repo=False)
    result = _insert_field(entry)
    assert result == "updated"
    text = _read(entry)
    assert "target_repo: https://github.com/emp3thy/ralph" in text


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    """A PBI that already has the field is left untouched (skipped)."""
    from scripts.migrate_pbis_to_target_repo import _insert_field

    entry = _make_pbi(tmp_path / ".ralph" / "current" / "WI-2", with_target_repo=True)
    before = _read(entry)
    result = _insert_field(entry)
    assert result == "skipped"
    after = _read(entry)
    assert before == after


def test_migrate_inserts_before_closing_fence(tmp_path: Path) -> None:
    """The new line lands inside the frontmatter, not at file start or end."""
    from scripts.migrate_pbis_to_target_repo import _insert_field

    entry = _make_pbi(tmp_path / ".ralph" / "done" / "WI-3", with_target_repo=False)
    _insert_field(entry)
    lines = _read(entry).splitlines()
    fence_positions = [i for i, line in enumerate(lines) if line == "---"]
    assert len(fence_positions) == 2, lines
    target_position = next(
        i for i, line in enumerate(lines) if line.startswith("target_repo:")
    )
    assert fence_positions[0] < target_position < fence_positions[1]


def test_migrate_preserves_existing_frontmatter_order(tmp_path: Path) -> None:
    """Fields before the closing fence keep their original order;
    target_repo is appended as the last frontmatter line."""
    from scripts.migrate_pbis_to_target_repo import _insert_field

    entry = _make_pbi(tmp_path / ".ralph" / "blocked" / "WI-4", with_target_repo=False)
    _insert_field(entry)
    lines = _read(entry).splitlines()
    # Find the order of id, type, status, severity, attempts, target_repo.
    field_order = [
        line.split(":")[0]
        for line in lines
        if line and not line.startswith("---") and ":" in line
    ]
    expected_prefix = ["id", "type", "status", "severity", "attempts"]
    assert field_order[: len(expected_prefix)] == expected_prefix
    assert "target_repo" in field_order
    assert field_order.index("target_repo") == len(expected_prefix)


def test_migrate_handles_no_fence_gracefully(tmp_path: Path) -> None:
    """A file without a frontmatter fence returns an 'error: ...' status,
    not a crash."""
    from scripts.migrate_pbis_to_target_repo import _insert_field

    entry = tmp_path / "no_fence.md"
    entry.write_text("just a body, no fence\n", encoding="utf-8")
    result = _insert_field(entry)
    assert result.startswith("error:")
```

(Note: tests import `pytest` for `MonkeyPatch` — add `import pytest` at the top.)

- [x] **Step 4.2: Run the failing tests**

```bash
uv run pytest tests/scripts/test_migrate_pbis.py -v
```

Expected: ALL FAIL with `ModuleNotFoundError: No module named 'scripts.migrate_pbis_to_target_repo'`.

- [x] **Step 4.3: Implement the migration script**

Create `scripts/migrate_pbis_to_target_repo.py`:

```python
"""One-shot migration: insert target_repo into every PBI/BUG/FEEDBACK
that lacks the field.

Hardcoded for the 2026-05-27 ralph self-host migration: every existing
work item is for the ralph repo itself. Throwaway script; safe to keep
in the tree for audit but never expected to run a second time except
defensively (idempotent skip-if-present).

Run from repo root:
    uv run python scripts/migrate_pbis_to_target_repo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

TARGET_REPO = "https://github.com/emp3thy/ralph"
REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_DIRS = ("inbox", "current", "pending-pr", "done", "blocked")
ENTRY_FILENAMES = ("PBI.md", "BUG.md", "FEEDBACK.md")


def _insert_field(path: Path) -> str:
    """Insert `target_repo: <url>` line into frontmatter if absent.

    Returns one of:
      - 'updated' — field inserted, file written
      - 'skipped' — field already present, no change
      - 'error: <reason>' — could not parse frontmatter
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return "error: no frontmatter fence at start"
    lines = text.splitlines(keepends=True)
    # Find closing fence.
    close_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].rstrip("\r\n") == "---":
            close_idx = idx
            break
    if close_idx is None:
        return "error: no closing frontmatter fence"
    # Skip if field already present anywhere in frontmatter.
    for line in lines[1:close_idx]:
        if line.lstrip().startswith("target_repo:"):
            return "skipped"
    # Insert just before the closing fence.
    # Detect the EOL style from the closing fence line so we don't
    # mix LF and CRLF.
    eol = "\r\n" if lines[close_idx].endswith("\r\n") else "\n"
    new_line = f"target_repo: {TARGET_REPO}{eol}"
    lines.insert(close_idx, new_line)
    path.write_text("".join(lines), encoding="utf-8", newline="")
    return "updated"


def main() -> int:
    queue_root = REPO_ROOT / ".ralph"
    if not queue_root.is_dir():
        print(f"error: {queue_root} does not exist", file=sys.stderr)
        return 2

    counts = {"updated": 0, "skipped": 0, "error": 0}
    for state in STATE_DIRS:
        state_dir = queue_root / state
        if not state_dir.is_dir():
            continue
        for pbi_dir in sorted(state_dir.iterdir()):
            if not pbi_dir.is_dir():
                continue
            for filename in ENTRY_FILENAMES:
                entry = pbi_dir / filename
                if not entry.is_file():
                    continue
                result = _insert_field(entry)
                rel = entry.relative_to(REPO_ROOT)
                if result.startswith("error"):
                    counts["error"] += 1
                    print(f"  {rel}: {result}", file=sys.stderr)
                else:
                    counts[result] += 1
                print(f"{rel}: {result}")
    print(
        f"\n{counts['updated']} updated, {counts['skipped']} skipped, "
        f"{counts['error']} errors."
    )
    return 0 if counts["error"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 4.4: Run the tests — verify they pass**

```bash
uv run pytest tests/scripts/test_migrate_pbis.py -v
```

Expected: 5 PASS.

- [x] **Step 4.5: Run ruff + mypy**

```bash
uv run ruff check scripts/migrate_pbis_to_target_repo.py tests/scripts/test_migrate_pbis.py
uv run mypy --strict scripts/migrate_pbis_to_target_repo.py
```

Expected: green.

- [x] **Step 4.6: Commit**

```bash
git -C /c/Users/gethi/ralph-queue add scripts/migrate_pbis_to_target_repo.py tests/scripts/test_migrate_pbis.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(scripts): single-use migration script for target_repo field"
```

---

## Task 5: Run migration + edit samples

**Files:**
- Modify: `.ralph/{inbox,current,pending-pr,done,blocked}/*/{PBI,BUG,FEEDBACK}.md` (via migration script)
- Modify: `samples/*/PBI.md` (and any `BUG.md` / `FEEDBACK.md`) by hand
- Modify: `tests/scripts/test_validate_samples.py` (add the regression sweep)

- [x] **Step 5.1: Pre-flight — check what samples exist**

```bash
find /c/Users/gethi/ralph-queue/samples -name '*.md' -type f 2>&1 | head -20
```

Take note of each path. Each gets a `target_repo: https://github.com/emp3thy/ralph` line added to its frontmatter, before the closing `---` fence.

- [x] **Step 5.2: Edit the samples**

For each sample file from Step 5.1, open it and insert the line just before the closing frontmatter fence. Example, if `samples/feature-pbi/PBI.md` looks like:

```yaml
---
id: WI-1234
type: feature
status: inbox
severity: normal
attempts: 0
---
```

Make it:

```yaml
---
id: WI-1234
type: feature
status: inbox
severity: normal
attempts: 0
target_repo: https://github.com/emp3thy/ralph
---
```

Use the Edit tool on each sample file. Do NOT run the migration script over `samples/` — direct edits keep the diff reviewable.

- [~] **Step 5.3: Run the migration script against the live queue** — deferred to a separate ralph-queue commit; `.ralph/` is gitignored on the feature branch (see iter 5 HISTORY note). Script is idempotent so the queue-branch run can happen later.

```bash
cd /c/Users/gethi/ralph-queue
uv run python scripts/migrate_pbis_to_target_repo.py
```

Expected output (counts vary based on actual queue contents at run time, but approximately):

```
.ralph/inbox/CONFIG-PROMOTE-BASH-TIMEOUT/PBI.md: updated
.ralph/inbox/STAGE-B-PLAN-10/PBI.md: updated
.ralph/inbox/STAGE-B-PLAN-12/PBI.md: updated
... (more) ...

N updated, 0 skipped, 0 errors.
```

If any file reports `error: ...`, investigate before continuing. The most likely cause is a malformed frontmatter fence — fix the source file and re-run (script is idempotent).

- [x] **Step 5.4: Add the validation regression test**

Append to `tests/scripts/test_validate_samples.py`:

```python
import pathlib

import yaml


def test_all_live_pbis_have_target_repo() -> None:
    """Regression: every PBI / BUG / FEEDBACK file in .ralph/ AND samples/
    must validate (including the new target_repo field) post-migration."""
    from scripts.validate_samples import _validate_frontmatter

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    state_dirs = [
        repo_root / ".ralph" / state
        for state in ("inbox", "current", "pending-pr", "done", "blocked")
    ]
    sample_root = repo_root / "samples"

    failures: list[str] = []
    for root in (*state_dirs, sample_root):
        if not root.is_dir():
            continue
        for entry in root.rglob("*.md"):
            if entry.name not in ("PBI.md", "BUG.md", "FEEDBACK.md"):
                continue
            text = entry.read_text(encoding="utf-8")
            if not (text.startswith("---\n") or text.startswith("---\r\n")):
                continue
            # Extract frontmatter via the same fence rule used by pbi_reader.
            lines = text.splitlines()
            close = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
            if close is None:
                failures.append(f"{entry}: no closing fence")
                continue
            try:
                fm = yaml.safe_load("\n".join(lines[1:close]))
            except yaml.YAMLError as exc:
                failures.append(f"{entry}: YAML error: {exc}")
                continue
            if not isinstance(fm, dict):
                failures.append(f"{entry}: frontmatter is not a mapping")
                continue
            errors = _validate_frontmatter(fm)
            target_errors = [e for e in errors if "target_repo" in e]
            if target_errors:
                failures.append(f"{entry}: {target_errors}")
    assert not failures, failures
```

- [x] **Step 5.5: Run the regression test**

```bash
uv run pytest tests/scripts/test_validate_samples.py::test_all_live_pbis_have_target_repo -v
```

Expected: PASS. If FAIL, the failure list pinpoints which file(s) are missing the field — re-run Step 5.3's migration or hand-edit the offending sample.

- [x] **Step 5.6: Commit migration + samples + regression test**

```bash
git -C /c/Users/gethi/ralph-queue add .ralph/ samples/ tests/scripts/test_validate_samples.py
git -C /c/Users/gethi/ralph-queue commit -m "chore(migration): add target_repo to all existing PBIs and samples"
```

---

## Task 6: ralph-add `--target-repo` flag + auto-derive

**Files:**
- Modify: `skills/ralph-add/scripts/add.py` (argparse + new `_derive_target_repo` helper + main flow)
- Modify: `tests/skills/test_ralph_add.py`

- [x] **Step 6.1: Add failing tests**

Append to `tests/skills/test_ralph_add.py`:

```python
def test_derive_target_repo_from_https_origin(tmp_path: Path) -> None:
    """git remote get-url origin = https://... -> target_repo = same minus .git"""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/emp3thy/ralph.git"],
        cwd=repo,
        check=True,
    )

    from skills.ralph_add.scripts.add import _derive_target_repo  # type: ignore[import-not-found]

    assert _derive_target_repo(repo) == "https://github.com/emp3thy/ralph"


def test_derive_target_repo_from_ssh_origin(tmp_path: Path) -> None:
    """git origin = git@host:owner/name(.git)? -> https://host/owner/name"""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:emp3thy/ralph.git"],
        cwd=repo,
        check=True,
    )

    from skills.ralph_add.scripts.add import _derive_target_repo  # type: ignore[import-not-found]

    assert _derive_target_repo(repo) == "https://github.com/emp3thy/ralph"


def test_derive_target_repo_raises_on_unparseable_origin(tmp_path: Path) -> None:
    """If origin is e.g. a local file path, raise _FatalError pointing
    at --target-repo flag."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "/some/local/path"],
        cwd=repo,
        check=True,
    )

    from skills.ralph_add.scripts.add import _FatalError, _derive_target_repo  # type: ignore[import-not-found]

    with pytest.raises(_FatalError, match="--target-repo"):
        _derive_target_repo(repo)


def test_ralph_add_writes_derived_target_repo_to_new_pbi(...) -> None:
    """Full ralph-add invocation: no --target-repo flag → PBI.md frontmatter
    contains target_repo == origin URL. Wires the existing happy-path
    test pattern; mock the workitem-fetch step."""
    # Use the pattern of the existing ralph-add happy-path test in this file.
    # Build a fake repo with HTTPS origin. Mock the workitem-fetch subprocess
    # to return a minimal valid work-item JSON. Run add.main(). Read the
    # resulting PBI.md from .ralph/inbox/<id>/. Assert frontmatter contains
    # target_repo: https://github.com/emp3thy/ralph.


def test_ralph_add_flag_overrides_origin(...) -> None:
    """--target-repo https://github.com/other/repo overrides auto-derive."""
    # Build fake repo with origin https://github.com/A/B.
    # Run ralph-add with --target-repo https://github.com/C/D.
    # Assert PBI.md frontmatter target_repo == "https://github.com/C/D".


def test_ralph_add_rejects_invalid_target_repo_flag(...) -> None:
    """--target-repo http://github.com/x/y → exit 2, stderr mentions HTTPS."""
    # Invoke add.main() with --target-repo http://github.com/x/y.
    # Assert exit code 2; stderr contains "HTTPS".
```

(The last three tests use the same fake-repo + mock-fetcher pattern already established in `test_ralph_add.py`. Copy the helper from an existing happy-path test if it exists; otherwise the pattern is: tmp_path repo, monkeypatch the fetcher subprocess to return canned JSON, run `add.main([...])`, then read `.ralph/inbox/<id>/PBI.md`.)

- [x] **Step 6.2: Run failing tests**

```bash
uv run pytest tests/skills/test_ralph_add.py -k "target_repo or derive_target" -v
```

Expected: FAIL — `ImportError: cannot import name '_derive_target_repo'`.

- [x] **Step 6.3: Add the `_derive_target_repo` helper and `--target-repo` arg**

In `skills/ralph-add/scripts/add.py`, add the helper near the other private helpers (around the `_run_git` area). Also add `import re` at the top if not present:

```python
def _derive_target_repo(repo: Path) -> str:
    """Read git origin URL and reshape to canonical HTTPS form.

    Examples:
      git@github.com:emp3thy/ralph.git    -> https://github.com/emp3thy/ralph
      https://github.com/emp3thy/ralph    -> https://github.com/emp3thy/ralph
      https://github.com/emp3thy/ralph.git -> https://github.com/emp3thy/ralph

    Raises _FatalError if origin is unparseable; the message points the
    operator at the --target-repo escape hatch.
    """
    try:
        url = _run_git(repo, "remote", "get-url", "origin").strip()
    except subprocess.CalledProcessError as exc:
        raise _FatalError(
            "no git remote 'origin' configured; pass --target-repo explicitly"
        ) from exc
    # SSH form: git@<host>:<owner>/<name>(.git)?
    ssh_match = re.match(r"^git@([^:]+):(.+?)(?:\.git)?$", url)
    if ssh_match:
        host, path = ssh_match.group(1), ssh_match.group(2)
        return f"https://{host}/{path}"
    if url.startswith("https://"):
        return url.removesuffix(".git")
    raise _FatalError(
        f"cannot derive target_repo from origin URL {url!r}; "
        f"pass --target-repo explicitly"
    )
```

Add the `--target-repo` arg to the argparse setup. Find the `_parse_args` function (around line 89). After the existing args, add:

```python
    parser.add_argument(
        "--target-repo",
        help=(
            "HTTPS URL of the target repo this PBI applies to. "
            "If omitted, derive from the --repo's git origin remote."
        ),
    )
```

- [x] **Step 6.4: Wire `target_repo` into the new PBI's frontmatter**

In `main()` (around line 436), after argument parsing and before the PBI write step, resolve the value:

```python
    # Resolve target_repo: explicit flag wins over auto-derive.
    if args.target_repo is not None:
        target_repo = args.target_repo.strip()
        if not target_repo:
            raise _FatalError("--target-repo must be non-empty")
    else:
        target_repo = _derive_target_repo(repo)

    # Validate before writing — reuses the validator from scripts.validate_samples.
    from scripts.validate_samples import _validate_target_repo

    validation_err = _validate_target_repo(target_repo)
    if validation_err is not None:
        raise _FatalError(validation_err)
```

In the PBI-write step (where `_write_pbi_md` or the frontmatter assembly happens — find it via `grep -n "target_repo\|frontmatter\|PBI.md.*write" /c/Users/gethi/ralph-queue/skills/ralph-add/scripts/add.py`), add `target_repo: <value>` to the frontmatter being written. The exact insertion point depends on the existing structure; pattern-match the way other fields are written.

- [x] **Step 6.5: Run the tests — verify they pass**

```bash
uv run pytest tests/skills/test_ralph_add.py -k "target_repo or derive_target" -v
```

Expected: 6 PASS.

- [x] **Step 6.6: Run full ralph-add test module + ruff + mypy**

```bash
uv run pytest tests/skills/test_ralph_add.py -v
uv run ruff check skills/ralph-add/scripts/add.py
uv run mypy --strict skills/ralph-add/scripts/add.py
```

If any existing happy-path test fails because the produced PBI now includes `target_repo` and the assertion didn't expect it: update the assertion to allow the new field. The field should appear in every ralph-add-created PBI.

- [x] **Step 6.7: Commit**

```bash
git -C /c/Users/gethi/ralph-queue add skills/ralph-add/scripts/add.py tests/skills/test_ralph_add.py
git -C /c/Users/gethi/ralph-queue commit -m "feat(ralph-add): write target_repo into new PBIs (auto-derive + --target-repo flag)"
```

---

## Task 7: workitem-fetch schema + PROMPT.md note

**Files:**
- Modify: `skills/workitem-fetch-github/scripts/schema.py`
- Modify: `prompt/PROMPT.md` (one-line note)

- [ ] **Step 7.1: Add `target_repo` to the workitem-fetch schema**

In `skills/workitem-fetch-github/scripts/schema.py`, find the list of required keys (referenced from `add.py:35-63` fallback). Add `"target_repo"` to the required tuple.

Example (the exact tuple shape depends on what's currently there):

```python
REQUIRED_KEYS: tuple[str, ...] = (
    "id",
    "type",
    "severity",
    "title",
    "body_markdown",
    "acceptance_markdown",
    "repro_steps_markdown",
    "attachments",
    "child_ids",
    "parent_id",
    "source_url",
    "source_host",
    "raw",
    "target_repo",   # NEW
)
```

If the schema also validates per-field types/shapes, add a value-type check for `target_repo`: must be a string (validates as a URL but light-touch; the strict validator lives in `scripts/validate_samples.py`).

- [ ] **Step 7.2: Update PROMPT.md**

In `prompt/PROMPT.md`, find a section that describes PBI structure (e.g., "PBI files" or "frontmatter"). Add a one-line note:

```markdown
- `target_repo`: HTTPS URL of the repo this PBI applies to (e.g., `https://github.com/emp3thy/ralph`). Required for every PBI.
```

No behavior change for claude this PBI — claude does NOT read this field yet. The note is informational so the field doesn't look mysterious if claude inspects PBI frontmatter.

- [ ] **Step 7.3: Run targeted tests + ruff + mypy**

```bash
uv run pytest tests/skills/ -v
uv run ruff check skills/workitem-fetch-github/scripts/schema.py
uv run mypy --strict skills/workitem-fetch-github/scripts/schema.py
```

If existing tests on `schema.py` fail because they don't supply `target_repo` in their fixture data: update the fixture(s) to include the field.

- [ ] **Step 7.4: Commit**

```bash
git -C /c/Users/gethi/ralph-queue add skills/workitem-fetch-github/scripts/schema.py prompt/PROMPT.md
git -C /c/Users/gethi/ralph-queue commit -m "feat(schema): workitem-fetch JSON gains target_repo; PROMPT.md notes the field"
```

---

## Task 8: Full-suite gate

**Files:** none modified directly; verification + push.

- [ ] **Step 8.1: Run the full pytest suite**

```bash
uv run pytest tests/ -v
```

Expected: all green. Common failure modes:

- A test constructing `PBIRow` literally without the new `target_repo` arg: add `target_repo="..."` to the keyword args.
- A test asserting on the full PBI.md frontmatter dict: update the expected dict to include `target_repo`.
- A test fixture that builds a PBI.md without the field and then validates: add the field to the fixture.

- [ ] **Step 8.2: Run ruff format check + mypy across the whole package**

```bash
uv run ruff check ralph_executor/ skills/ scripts/ tests/
uv run ruff format --check ralph_executor/ skills/ scripts/ tests/
uv run mypy --strict ralph_executor/ scripts/
```

- [ ] **Step 8.3: Verify the live queue still validates**

```bash
uv run python scripts/validate_samples.py
```

(If that script has its own CLI entry point; otherwise rely on `test_all_live_pbis_have_target_repo` from Task 5.) Expected: all PBIs report clean.

- [ ] **Step 8.4: Push the branch**

```bash
git -C /c/Users/gethi/ralph-queue push origin ralph-queue
```

If push is rejected (ralph has moved the branch in the meantime — likely if ralph is running):

```bash
git -C /c/Users/gethi/ralph-queue pull --rebase origin ralph-queue
# Resolve conflicts if any (unlikely since ralph operates on different files)
git -C /c/Users/gethi/ralph-queue push origin ralph-queue
```

---

## Acceptance (matches spec)

- [x] `scripts/validate_samples.py` adds `target_repo` to `REQUIRED_FRONTMATTER_FIELDS` + `_validate_target_repo` helper — Tasks 1+2
- [x] `scripts/pbi_reader.py` parses the field — Task 3
- [x] `scripts/migrate_pbis_to_target_repo.py` exists + idempotent — Task 4
- [x] All 13 existing PBIs have `target_repo: https://github.com/emp3thy/ralph` — Task 5
- [x] `skills/ralph-add/scripts/add.py` writes `target_repo` via auto-derive + `--target-repo` flag — Task 6
- [x] `skills/workitem-fetch-github/scripts/schema.py` includes the field — Task 7
- [x] `samples/*/PBI.md` (and BUG/FEEDBACK) have the field — Task 5
- [x] pytest / ruff / mypy strict all green — Task 8
- [x] depends_on: `[]`
- [x] Behavior change: none — ralph still operates single-target

## Out of scope (per spec)

- Consumption (ralph reading `target_repo` to switch repos)
- Queue extraction to its own repo
- Deployed-ralph model
- ADO-specific URL parsing beyond the generic check
- `target_branch` field
- Per-target auth credentials

## Operational notes

- **Workflow:** all commits go via `C:/Users/gethi/ralph-queue` (the separate clone). Never commit from `C:/Users/gethi/source/ralph` (ralph's running checkout).
- **Ralph running?** This PBI changes schema validation but no runtime behavior. Ralph can keep running through implementation. New PBIs ralph creates between the spec landing and this PBI's merge would lack `target_repo` and fail validation — accepted risk; rerun migration script if it happens.
- **depends_on:** `[]` — independent of other queued PBIs.
- **Branch:** lands on `ralph-queue` via PR to `main` (per ralph's normal flow); becomes part of main once merged.
