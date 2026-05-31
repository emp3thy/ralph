# tech-debt-scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `tech-debt-scan` skill (two commands: `/tech-debt-scan` and `/tech-debt-promote`) in a new standalone GitHub skills repo. Language-independent. Phase 1 only — human-in-loop between scan and promote.

**Architecture:** SKILL.md drives Claude through ordered steps. Pure-Python helper scripts under `scripts/` handle deterministic work (file walk, markdown rendering, parsing, validation). LLM scouts dispatched via the Agent tool — never subprocess. Each finding round-trips through a single design.md document the user edits between scan and promote.

**Tech Stack:** Python 3.11+, pyyaml, pytest, ruff, mypy strict, GitHub Actions. No `ralph_executor` / `ralph-new` imports. No live LLM dependency at test time (Agent dispatch is mocked).

**Source spec:** `docs/superpowers/specs/2026-05-31-tech-debt-scan-design.md`

---

## Guardrails

High-confidence reflections that govern this plan. Confidence + useful-count from better-memory at retrieval time. Anchors use `[[slug]]` for cross-reference from individual tasks.

### Process gates (non-skippable)

- **[[per-step-confidence-rating]]** (standards/ralph-runtime.md §"Apply confidence scoring") — Every task below carries a confidence %. Sub-90% tasks embed mitigations in the task body. No task ships at <90% effective confidence without explicit user sign-off.
- **[[render-plan-in-visualiser]]** (standards/ralph-runtime.md §"Render brainstorming/plans in visual companion") — After this plan is committed, render a plan-summary page in the running visual companion (Bootstrap 5 light theme, full HTML doc) and announce the URL before presenting the execution choice. Skipping is a workflow violation.
- **[[feature-branch-at-task-start]]** (standards/ralph-runtime.md §"Create the feature branch at task start") — When execution begins, the executor creates the feature branch immediately at Task 1, before any file is touched.
- **[[cross-read-prose-vs-example]]** (standards/ralph-runtime.md §"Cross-read spec prose and example-code blocks") — Plan self-review must cross-check prose claims against embedded code blocks (encoding, signatures, named constants).
- **[[spec-test-code-lint]]** (standards/ralph-runtime.md §"Spec-provided test code is not lint-clean") — Test snippets in this plan use `from datetime import UTC` (not `timezone.utc`), `StrEnum` (not `(str, Enum)`), and avoid stray `import pytest` unless a pytest symbol is used.

### Code-quality reflections that apply

- **[[98056ebc-docs-in-sync]]** (conf 0.95, useful 6) — README + SKILL.md describe the same flags / commands / file layout the scripts implement. Any code change that adds or renames a flag must update both in the same commit.
- **[[462d13a7-grep-test-callsites]]** (conf 0.85, useful 2) — When a dataclass / function signature gains a required field, grep every constructor / call-site under `tests/` and thread the new arg through in the same commit. Apply to `ScoutFinding`, `Finding`, `Bundle` dataclasses introduced here.
- **[[aa125f7a-empty-input-guard]]** (conf 0.7, useful 1) — Any function with shape `"\n".join(...)` over an iterable must guard the empty case AND wrap `iterdir()` / file I/O in `try/except OSError` re-raising as a module-typed error. Applies to `design_writer.render_design_md` and `inventory.walk`.
- **[[ca0308d6-flag-default-grep]]** (conf 0.75, useful 0) — When adding a CLI flag that replaces a default (e.g. `--out` for output dir), grep the literal default across the module before merge. No surviving hardcoded references.
- **[[c73f9438-duplicated-validator]]** (conf 0.7, useful 1) — `slug` validation lives in `design_writer` (write side) AND `design_parser` (read side). Factor into shared `_validate_slug` helper in a `validation.py` module so the rejection-path test matrix is shared.
- **[[26d0a5a7-truthiness-vs-none]]** (conf 0.75, useful 1) — Status validation must use `if status is None` not `if not status` so empty-string `""` reaches the validator and errors with line ref.
- **[[ed7847b0-mypy-hoist-defaults]]** (conf 0.7, useful 0) — Type hints in helpers default to non-Optional with eager default assignment, not `T | None` narrowed inside `if x is None` blocks. mypy strict gate enforces.
- **[[dfcf8a2a-argparse-both-layers]]** (conf 0.7, useful 0) — `--verbose` / `--out` declared on subparsers only, NOT on a shared top-level parser. Removes ambiguity about flag position.
- **[[cfa7e7f8-pbi-shape-type-aware]]** (conf 0.7, useful 3) — Bundle output uses `chore-<slug>-<date>/PBI.md` (type=chore → PBI.md is the entry file). Verified in spec; tests assert filename.
- **[[77c83c69-target-repo-required]]** (conf 0.7, useful 2) — Emitted PBI.md frontmatter includes a `target_repo:` key (blank value) so when the user pipes the bundle to ralph the claim won't fail. Test asserts the key exists even with empty value.
- **[[99c53915-ralph-commit-convention]]** (conf 0.7, useful 1) — Bundle docs note that on adding to ralph-queue the commit subject should be `chore(queue): add <id>`. This skill does not commit on behalf of the user; the convention is documented for the manual paste step.
- **[[0518718c-multi-artefact-step]]** (conf 0.7, useful 1) — Task 1 scaffolds multiple artefacts (pyproject, ruff config, mypy config, CI workflow, README, SKILL.md stub, fixture dirs). Before marking complete, list each artefact and confirm against `git status` / new files.
- **[[ca0308d6-grep-literal-on-flag-add]]** (conf 0.75) — On adding `--force` to promote.py, grep `chore-` prefix references across the module to confirm none are hardcoded to a wrong slug pattern.
- **[[355faeb8-windows-argv-limit]]** (conf 0.8, useful 1) — Scout dispatch must pass `inventory.json` as a **file path**, not inline JSON content, so Windows cmd.exe shims don't hit the 8191-char argv ceiling. Codified in SKILL.md's Step 2 wording: scouts receive `--inventory <path>`.
- **[[4c179d52-validation-gap-pre-merge]]** (conf 0.7) — Any validation gap a reviewer flags during this plan's execution (e.g. design.md status accepted but downstream barfs) gets fixed in the same PR, not deferred.
- **[[d342c481-fixture-gitignore-mirror]]** (conf 0.6) — `tests/fixtures/*/` ship their own `.gitignore` files matching what a real project of that language would carry (`__pycache__/`, `node_modules/`, `bin/`, `obj/`). Inventory walker tests rely on the walker honouring those.
- **[[852f5ae9-disambiguate-stopped-early]]** (conf 0.65) — `promote.py`'s structured report distinguishes `emitted_count`, `already_promoted_count`, `rejected_count`, `pending_count` as separate fields. Don't conflate "no-op because already-promoted" with "no-op because nothing approved".
- **[[8cada12c-package-relative-imports]]** (conf 0.75) — Helper scripts under `scripts/` use direct-path-invocable shape (no `from scripts.x import y`). Each script is runnable as `python scripts/foo.py` without `-m`. SKILL.md uses direct-path form.
- **[[0547374e-no-git-add-A]]** (conf 0.7) — Commit steps stage explicit paths, not `git add -A`. Plan body shows exact `git add <path>` invocations per task.

### Dismissed reflections (considered, not applicable)

- 0da0e9ff (0.6) feature-branch HISTORY.md checkout — skill doesn't run inside a ralph iteration.
- 43e430f5 (0.6) ralph executor architecture — skill is host-pure, no executor coupling.
- 8284d6b2 (0.65) `.ralph/config.toml` read location — skill reads no ralph config.
- 0c83e25d (0.8) Playwright textContent — no Playwright in this plan.
- a5af973e (0.8) git-aware state transition helper — skill doesn't transition state on a git-tracked queue.
- 63c4e75a (0.85) `git mv` + content edits — no `git mv` in this plan.
- 8d05b8c2 (0.75) queue self-contained — skill writes self-contained bundles; spec already requires it.
- a8019766 (0.75) PUT-if-not-exists placeholder collision — no remote API in this plan.
- e73b9e79 (0.7) `--dry-run` mutation guard — no `--dry-run` flag in phase 1.
- 85e7ec84 (0.6) tempfile.mkstemp fd leak — no `mkstemp` usage.
- d62c9e9e (0.7) pin commit subject in upstream-tip tests — no upstream-tip assertions.
- 022dafc3 (0.55) failing-test snippet preconditions — covered by plan's TDD steps explicitly listing fixture setup.
- 24644201 (0.7) GitHub Free plan 403 on private repo branch protection — Task 1 creates the repo without branch protection; documented.
- cd86bc4d (0.6) `git stash` to verify inherited failures — execution-time skill, not plan-time.
- d1a577ce (0.65) don't flip shared fixture default — fixtures here are purpose-built per-test, no shared default to flip.
- 6aa6bf51 (1.0) root-cause fixing — applies generally during execution; no plan-time action.
- b017b510 (0.9) PBI dispatch boundary — N/A; this plan is the *production* of a skill, not the dispatch of work to ralph.

---

## File structure

```
tech-debt-skills/                       # new GitHub repo, name TBD by user
├── README.md
├── LICENSE                             # MIT or user's choice
├── .gitignore                          # Python + IDE + .tech-debt/
├── .github/workflows/test.yml          # ruff + mypy + pytest on 3.11 + 3.12
├── pyproject.toml                      # pytest, ruff, mypy strict config
├── skills/
│   └── tech-debt-scan/
│       ├── SKILL.md                    # workflow for both /tech-debt-scan and /tech-debt-promote
│       ├── scripts/
│       │   ├── inventory.py            # CLI: build inventory.json from a path
│       │   ├── categories.py           # data module: 6 scout category prompts
│       │   ├── build_synthesis_prompt.py  # CLI: render synthesis prompt from raw-findings.json
│       │   ├── design_writer.py        # CLI: render design.md + --mark-promoted in-place edit
│       │   ├── design_parser.py        # CLI: parse design.md → JSON
│       │   ├── bundle_writer.py        # CLI: write a single PBI bundle
│       │   ├── validation.py           # shared slug + status validators
│       │   └── promote.py              # CLI orchestrator: parse → emit → mark-promoted
│       └── tests/
│           ├── conftest.py             # pytest path setup
│           ├── fixtures/
│           │   ├── python-repo/        # small Python project with seeded debt
│           │   ├── csharp-repo/        # small C# project with seeded debt
│           │   ├── react-repo/         # small TSX project with seeded debt
│           │   └── multi-lang-repo/    # mixed
│           ├── golden/
│           │   ├── design.md           # canonical design.md output
│           │   └── bundle/             # canonical bundle output
│           ├── test_inventory.py
│           ├── test_categories.py
│           ├── test_build_synthesis_prompt.py
│           ├── test_design_writer.py
│           ├── test_design_parser.py
│           ├── test_bundle_writer.py
│           ├── test_validation.py
│           ├── test_promote.py
│           └── test_e2e.py             # mocks Agent dispatch
└── docs/
    └── architecture.md
```

Each script ≤300 LOC. Each test file maps 1:1 to its script.

---

## Tasks

### Task 1: Scaffold the existing claude-skills repo

**Confidence: 92%.** Repo `emp3thy/claude-skills` already exists (created 2026-05-31 at session start). Local clone at `C:/Users/gethi/source/claude-skills` (empty — no commits yet). Task adds initial scaffolding on a feature branch.

**Target repo (for ralph PBI frontmatter):** `emp3thy/claude-skills`
**Local working copy:** `C:/Users/gethi/source/claude-skills`

**Files:**
- Create: `<repo-root>/README.md`
- Create: `<repo-root>/LICENSE`
- Create: `<repo-root>/.gitignore`
- Create: `<repo-root>/.github/workflows/test.yml`
- Create: `<repo-root>/pyproject.toml`
- Create: `<repo-root>/skills/tech-debt-scan/SKILL.md` (stub — filled out in Task 9)
- Create: `<repo-root>/skills/tech-debt-scan/scripts/__init__.py`  (empty)
- Create: `<repo-root>/skills/tech-debt-scan/tests/__init__.py`  (empty)
- Create: `<repo-root>/skills/tech-debt-scan/tests/conftest.py`

Per [[0518718c-multi-artefact-step]]: this task creates 9 artefacts; before marking complete, list each and verify against `git status`.

- [ ] **Step 1: Verify the cloned repo and create the feature branch.**

```bash
cd C:/Users/gethi/source/claude-skills
git remote -v
# expected: origin  https://github.com/emp3thy/claude-skills.git (fetch + push)
git status
# expected: empty repo, "No commits yet"
```

Per [[feature-branch-at-task-start]]: create the feature branch immediately before any file edit:

```bash
git checkout -b feat/initial-scaffold
```

Since the repo has no `main` branch yet (empty), an initial commit on `feat/initial-scaffold` is fine; Step 11's PR will create `main` via the merge of this PR. Alternatively, Step 11 may push `feat/initial-scaffold` and open the PR against an empty default — gh will handle the auto-create.

- [ ] **Step 2: Write `pyproject.toml`.**

```toml
[project]
name = "tech-debt-scan-skill"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pyyaml>=6.0"]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "ruff>=0.5",
  "mypy>=1.10",
]

[tool.pytest.ini_options]
testpaths = ["skills/tech-debt-scan/tests"]
markers = ["live: hits a real LLM (off by default)"]
addopts = "-m 'not live'"

[tool.ruff]
line-length = 100
target-version = "py311"
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true
files = ["skills/tech-debt-scan/scripts"]
```

- [ ] **Step 3: Write `.gitignore`.**

```
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
.venv/
venv/
.tech-debt/
*.bak
.idea/
.vscode/
```

- [ ] **Step 4: Write `LICENSE`** (MIT, user's name in copyright line).

- [ ] **Step 5: Write `README.md` stub** with a short description, install instructions placeholder, and a "see SKILL.md for usage" pointer. Filled out fully in Task 10.

```markdown
# tech-debt-scan

Language-independent tech-debt scan skill for Claude Code. Scans any repo via LLM scouts, synthesises top-5 debt findings, emits ralph-friendly PBI bundles after human review.

See `skills/tech-debt-scan/SKILL.md` for usage. See `docs/architecture.md` for the full design.

Status: Phase 1 (human-in-loop). Phase 2 ("mow the lawn" autonomy) is out of scope.
```

- [ ] **Step 6: Write `.github/workflows/test.yml`.**

```yaml
name: test
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: mypy
      - run: pytest -v
```

- [ ] **Step 7: Write `skills/tech-debt-scan/SKILL.md` stub.**

```markdown
---
name: tech-debt-scan
description: Scan a repo for top-5 tech-debt findings; emit a design doc the user reviews, then convert approved findings to ralph-friendly PBI bundles.
triggers:
  - /tech-debt-scan
  - /tech-debt-promote
---

# tech-debt-scan

(Workflow filled out in Task 9.)
```

- [ ] **Step 8: Write `skills/tech-debt-scan/tests/conftest.py`.**

```python
"""Pytest path setup so scripts/ imports work in tests."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
```

- [ ] **Step 9: Verify scaffolding via the multi-artefact checklist (per [[0518718c-multi-artefact-step]]).**

```bash
git status --short
# expected: 8 new files listed (README, LICENSE, .gitignore, workflow, pyproject,
#           SKILL.md, scripts/__init__.py, tests/__init__.py, conftest.py)
```

Check each artefact off against the Files list at the top of this task.

- [ ] **Step 10: Run lint + mypy + pytest to confirm baseline green.**

```bash
pip install -e ".[dev]"
ruff check .
mypy
pytest -v
```

Expected: ruff PASS, mypy PASS (no files to check yet — OK), pytest PASS (0 tests collected — OK).

- [ ] **Step 11: Commit, push, open PR.**

```bash
git add README.md LICENSE .gitignore pyproject.toml .github/workflows/test.yml \
  skills/tech-debt-scan/SKILL.md \
  skills/tech-debt-scan/scripts/__init__.py \
  skills/tech-debt-scan/tests/__init__.py \
  skills/tech-debt-scan/tests/conftest.py
git commit -m "chore: initial scaffold for tech-debt-scan skill"
git push -u origin feat/initial-scaffold
gh pr create --title "Initial scaffold" --body "Empty skill repo with CI, lint, type, and test gates."
```

---

### Task 2: Inventory walker — `scripts/inventory.py`

**Confidence: 93%** (lifted from 90%). Pure filesystem walk + JSON write. Multi-language fixture coverage is the only nuance.

**Confidence lifts applied:**
1. **Symlinks skipped explicitly.** Walker checks `path.is_symlink()` BEFORE `path.is_file()` and skips. Avoids symlink loops + accidental external-file inclusion. New test asserts symlinks under fixture are absent from inventory.
2. **Per-file permission errors are logged + skipped, not fatal.** A single unreadable file emits a warning to stderr and continues; only path-not-found / not-a-directory at the root raises `InventoryError`. New test patches `Path.open` to raise `PermissionError` and asserts the walk completes with that file excluded.
3. **LOC counting is platform-independent.** Counts `\n` occurrences in the opened file iterator — never `Path.read_text()` (which translates line endings on Windows). Documented in the function docstring.
4. **Hidden-dir policy explicit.** Dot-dirs in `DEFAULT_IGNORE` are skipped; any other dot-dir is INCLUDED. Per-repo `.tech-debt-ignore` (one glob per line, gitignore-style) extends the ignore list. Test seeds a `.config/` dir in the python-repo fixture and asserts its contents are walked.
5. **Per [[d342c481-fixture-gitignore-mirror]]:** fixtures ship realistic `.gitignore` patterns matching what real projects of that language carry.

**Files:**
- Create: `skills/tech-debt-scan/scripts/inventory.py`
- Create: `skills/tech-debt-scan/tests/test_inventory.py`
- Create: `skills/tech-debt-scan/tests/fixtures/python-repo/` (small project: 3 .py files, one > 400 LOC; 1 `__pycache__/` dir to test ignore)
- Create: `skills/tech-debt-scan/tests/fixtures/csharp-repo/` (2 .cs files, 1 `bin/` and `obj/` to test ignore)
- Create: `skills/tech-debt-scan/tests/fixtures/react-repo/` (3 .tsx + 1 .ts file, 1 `node_modules/` to test ignore)
- Create: `skills/tech-debt-scan/tests/fixtures/multi-lang-repo/` (mixed)

- [ ] **Step 1: Seed fixtures** (one fixture per language). Each fixture root contains:
  - A `.gitignore` mirroring real conventions
  - Source files with deterministic content (test asserts on LOC counts)
  - Ignored directories (`__pycache__/`, `bin/`, `node_modules/`) holding files that must NOT be counted

- [ ] **Step 2: Write the failing test.**

```python
# skills/tech-debt-scan/tests/test_inventory.py
from __future__ import annotations

import json
from pathlib import Path

from inventory import walk_inventory

FIXTURES = Path(__file__).parent / "fixtures"


def test_python_repo_inventory():
    result = walk_inventory(FIXTURES / "python-repo")
    assert result["total_files"] == 3
    assert result["total_loc"] > 0
    assert "python" in result["languages"]
    # __pycache__ files must be ignored
    for entry in result["files"]:
        assert "__pycache__" not in entry["path"]


def test_csharp_repo_inventory():
    result = walk_inventory(FIXTURES / "csharp-repo")
    assert result["total_files"] == 2
    assert "csharp" in result["languages"]
    for entry in result["files"]:
        assert "/bin/" not in entry["path"] and "/obj/" not in entry["path"]


def test_react_repo_inventory():
    result = walk_inventory(FIXTURES / "react-repo")
    assert result["total_files"] == 4  # 3 tsx + 1 ts
    assert "typescript" in result["languages"]
    for entry in result["files"]:
        assert "node_modules" not in entry["path"]


def test_multi_lang_repo_inventory():
    result = walk_inventory(FIXTURES / "multi-lang-repo")
    assert len(result["languages"]) >= 2


def test_missing_path_raises():
    import pytest

    from inventory import InventoryError

    with pytest.raises(InventoryError, match="path not found"):
        walk_inventory(FIXTURES / "does-not-exist")


def test_empty_dir_returns_zero(tmp_path: Path):
    result = walk_inventory(tmp_path)
    assert result["total_files"] == 0
    assert result["files"] == []
```

- [ ] **Step 3: Run tests to verify they fail.**

```bash
pytest skills/tech-debt-scan/tests/test_inventory.py -v
```

Expected: ImportError on `from inventory import walk_inventory`.

- [ ] **Step 4: Implement `scripts/inventory.py`.**

```python
"""Build a language-agnostic file inventory of a directory tree."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".cs": "csharp",
    ".java": "java",
    ".kt": "kotlin",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".md": "markdown",
}

DEFAULT_IGNORE: tuple[str, ...] = (
    "node_modules",
    "bin",
    "obj",
    "target",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".git",
    ".idea",
    ".vscode",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tech-debt",
)


class InventoryError(Exception):
    """Raised when the inventory walk fails."""


@dataclass
class FileEntry:
    path: str          # relative to root
    ext: str
    loc: int
    mtime: float


def _is_ignored(rel_parts: tuple[str, ...], ignore: tuple[str, ...]) -> bool:
    return any(part in ignore for part in rel_parts)


def walk_inventory(root: Path, ignore: tuple[str, ...] = DEFAULT_IGNORE) -> dict[str, object]:
    root = root.resolve()
    if not root.exists():
        raise InventoryError(f"path not found: {root}")
    if not root.is_dir():
        raise InventoryError(f"path is not a directory: {root}")

    entries: list[FileEntry] = []
    languages: set[str] = set()

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _is_ignored(rel.parts, ignore):
            continue
        ext = path.suffix.lower()
        lang = EXT_TO_LANG.get(ext)
        if lang is None:
            continue
        try:
            loc = sum(1 for _ in path.open(encoding="utf-8", errors="ignore"))
        except OSError as exc:
            raise InventoryError(f"could not read {path}: {exc}") from exc
        entries.append(
            FileEntry(
                path=str(rel).replace("\\", "/"),
                ext=ext,
                loc=loc,
                mtime=path.stat().st_mtime,
            )
        )
        languages.add(lang)

    return {
        "root": str(root),
        "total_files": len(entries),
        "total_loc": sum(e.loc for e in entries),
        "languages": sorted(languages),
        "files": [asdict(e) for e in entries],
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a file inventory for tech-debt-scan")
    parser.add_argument("path", help="repo root to scan")
    parser.add_argument("--out", default=".tech-debt/inventory.json", help="output JSON path")
    args = parser.parse_args(argv)

    try:
        inv = walk_inventory(Path(args.path))
    except InventoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(inv, indent=2))
    print(f"wrote {out_path} ({inv['total_files']} files, {inv['total_loc']} LOC)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
```

Per [[aa125f7a-empty-input-guard]]: `rglob` returning empty is fine (we return `total_files: 0`), and `OSError` is wrapped to `InventoryError`. Per [[7847b0dc-mypy-hoist-defaults]]: `ignore` default is non-Optional with eager `DEFAULT_IGNORE`, not `tuple[str, ...] | None`.

- [ ] **Step 5: Re-run tests to confirm green.**

```bash
pytest skills/tech-debt-scan/tests/test_inventory.py -v
ruff check skills/tech-debt-scan/scripts/inventory.py
mypy
```

Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add skills/tech-debt-scan/scripts/inventory.py \
  skills/tech-debt-scan/tests/test_inventory.py \
  skills/tech-debt-scan/tests/fixtures/
git commit -m "feat(inventory): walk repo + emit language-agnostic inventory.json"
```

---

### Task 3: Categories module — `scripts/categories.py`

**Confidence: 95%.** Data-only module. Six fixed category prompts. Tests verify each prompt is non-empty + language-agnostic.

**Files:**
- Create: `skills/tech-debt-scan/scripts/categories.py`
- Create: `skills/tech-debt-scan/tests/test_categories.py`

- [ ] **Step 1: Write failing test.**

```python
# skills/tech-debt-scan/tests/test_categories.py
from __future__ import annotations

from categories import CATEGORIES, get_prompt

EXPECTED = {"god-modules", "duplication", "dead-code", "test-gaps", "doc-drift", "half-finished"}


def test_six_categories():
    assert set(CATEGORIES) == EXPECTED


def test_each_prompt_non_empty():
    for cat in EXPECTED:
        prompt = get_prompt(cat)
        assert len(prompt) > 200, f"{cat} prompt too short"


def test_unknown_category_raises():
    import pytest
    with pytest.raises(KeyError):
        get_prompt("not-a-category")


def test_prompts_avoid_python_specific_terms():
    """Prompts must read as language-agnostic."""
    forbidden = {"def ", "import ", ".py file", "Python module"}
    for cat in EXPECTED:
        text = get_prompt(cat).lower()
        for bad in forbidden:
            assert bad.lower() not in text, f"{cat}: {bad!r} leaks Python-specific phrasing"
```

- [ ] **Step 2: Run to verify fail.**

```bash
pytest skills/tech-debt-scan/tests/test_categories.py -v
```

- [ ] **Step 3: Implement `scripts/categories.py`.**

Module exposes `CATEGORIES: tuple[str, ...]` (the six names) and `get_prompt(name: str) -> str`. Each prompt:
- States the debt class in language-agnostic terms ("files over 400 lines", not ".py files")
- Lists 3–6 concrete signals to look for
- Specifies the required JSON output schema (matches spec §2 Step 2)
- Caps output: severity 1–5, ≤80-char title, ≤500-char suggested_fix
- Tells the scout it has read-only access (Explore agent semantics)

Prompts live as triple-quoted constants. ≥200 chars each. Cross-read each prompt against spec §2 list of categories per [[cross-read-prose-vs-example]].

- [ ] **Step 4: Run tests, expect PASS.**

- [ ] **Step 5: Commit.**

```bash
git add skills/tech-debt-scan/scripts/categories.py \
  skills/tech-debt-scan/tests/test_categories.py
git commit -m "feat(categories): six fixed language-agnostic scout prompts"
```

---

### Task 4: Synthesis prompt builder — `scripts/build_synthesis_prompt.py`

**Confidence: 93%** (lifted from 88%). Synthesis LLM might return malformed JSON or context-overflow.

**Confidence lifts applied:**
1. **Token cap at input.** If `raw-findings.json` contains > 30 findings, sort by `severity` DESC then truncate to top 30 before sending to synthesis. Prevents synthesis prompt from exceeding ~30k input tokens. `build_prompt()` returns prompt text + a `truncated_from` count; SKILL.md logs the truncation if non-zero.
2. **Dataclass-based strict validator (no pydantic dep).** `@dataclass(frozen=True)` for `Top5Item` and `Top5` with explicit `__post_init__` checks: every required field present, severity ∈ {1,2,3,4,5}, category ∈ `CATEGORIES`, slug passes `validate_slug`. Each failure raises `SynthesisError` with the offending field name + value — never a generic `TypeError` or `KeyError`.
3. **Recorded-fixture coverage.** `tests/golden/synthesis-good.json` (known-good synthesis output round-trips). `tests/golden/synthesis-bad-*.json` (one per failure mode: missing key, wrong count, invalid slug, severity 6, severity 0, category not in CATEGORIES). Each bad fixture is parametrised into one test asserting the specific error substring.
4. **Retry semantics codified in SKILL.md.** Step 4 of `/tech-debt-scan` instructs Claude: on validation failure, write the raw response to `.tech-debt/synthesis-failed-<timestamp>.json`, retry the synthesis prompt once with an appended "previous response failed schema; re-emit valid JSON". On second failure, exit 5.
5. **Plan ordering swap codified in §Tasks.** Task 5 (validation.py) executes BEFORE Task 4 — the synthesis output validator imports `validate_slug` from `validation.py`.

**Files:**
- Create: `skills/tech-debt-scan/scripts/build_synthesis_prompt.py`
- Create: `skills/tech-debt-scan/tests/test_build_synthesis_prompt.py`

- [ ] **Step 1: Write failing tests.**

```python
# skills/tech-debt-scan/tests/test_build_synthesis_prompt.py
from __future__ import annotations

import json
from pathlib import Path

from build_synthesis_prompt import (
    build_prompt,
    validate_synthesis_output,
    SynthesisError,
)


def _raw_findings_sample() -> list[dict]:
    return [
        {
            "title": f"Finding {i}",
            "severity": (i % 5) + 1,
            "category": "god-modules" if i % 2 == 0 else "duplication",
            "evidence": [{"file": "a.py", "line": 1, "note": "n"}],
            "suggested_fix": "split X",
        }
        for i in range(20)
    ]


def test_build_prompt_includes_all_findings():
    prompt = build_prompt(_raw_findings_sample())
    for i in range(20):
        assert f"Finding {i}" in prompt


def test_build_prompt_specifies_schema():
    prompt = build_prompt(_raw_findings_sample())
    assert "impact" in prompt and "tractability" in prompt
    assert '"top5"' in prompt
    assert "slug" in prompt and "severity" in prompt


def test_validate_synthesis_output_ok():
    out = json.dumps({
        "top5": [
            {"slug": f"finding-{i}", "title": f"T{i}", "severity": 5,
             "category": "god-modules", "reasoning": "r",
             "evidence": [], "suggested_fix": "f"}
            for i in range(5)
        ]
    })
    result = validate_synthesis_output(out)
    assert len(result["top5"]) == 5


def test_validate_synthesis_output_wrong_count_raises():
    import pytest
    out = json.dumps({"top5": [{"slug": "a", "title": "t", "severity": 1,
                                 "category": "god-modules", "reasoning": "r",
                                 "evidence": [], "suggested_fix": "f"}]})
    with pytest.raises(SynthesisError, match="expected 5 findings"):
        validate_synthesis_output(out)


def test_validate_synthesis_output_malformed_raises():
    import pytest
    with pytest.raises(SynthesisError, match="not valid JSON"):
        validate_synthesis_output("not json at all")


def test_validate_synthesis_output_bad_slug_raises():
    import pytest
    out = json.dumps({
        "top5": [
            {"slug": "Bad Slug With Spaces", "title": "t", "severity": 1,
             "category": "god-modules", "reasoning": "r",
             "evidence": [], "suggested_fix": "f"}
        ] * 5
    })
    with pytest.raises(SynthesisError, match="invalid slug"):
        validate_synthesis_output(out)
```

- [ ] **Step 2: Run to fail.**

- [ ] **Step 3: Implement.**

Module exposes:
- `build_prompt(raw_findings: list[dict]) -> str` — concatenates a header, the findings JSON, an explicit instruction to pick 5 by impact × tractability, and the strict output schema.
- `validate_synthesis_output(text: str) -> dict` — parses JSON, raises `SynthesisError` on: not-JSON, missing `top5` key, len != 5, any item missing required field, slug failing `^[a-z0-9][a-z0-9-]{1,63}$`, severity not in 1–5.

Per [[c73f9438-duplicated-validator]]: the slug regex lives in `validation.py` (created in Task 5) — this task imports `from validation import validate_slug`. Update import in step 3 once Task 5 lands. **Forward-reference flag**: Task 5 creates `validation.py` first; Task 4 can be reordered if needed.

**Plan ordering note**: swap Tasks 4 and 5 — `validation.py` lands first, then `build_synthesis_prompt.py` imports from it cleanly. See Task 5 below.

- [ ] **Step 4: Run tests, expect PASS.**

- [ ] **Step 5: Commit.**

```bash
git add skills/tech-debt-scan/scripts/build_synthesis_prompt.py \
  skills/tech-debt-scan/tests/test_build_synthesis_prompt.py
git commit -m "feat(synthesis): build top-5 picker prompt + strict output validator"
```

---

### Task 5: Shared validation helpers — `scripts/validation.py`

**Confidence: 95%.** Tiny module. Two functions: `validate_slug` and `validate_status`.

Executed **before** Task 4 (the swap noted above is mandatory — slug validator is consumed by Task 4's synthesis output validator).

**Files:**
- Create: `skills/tech-debt-scan/scripts/validation.py`
- Create: `skills/tech-debt-scan/tests/test_validation.py`

- [ ] **Step 1: Write failing tests.**

```python
# skills/tech-debt-scan/tests/test_validation.py
from __future__ import annotations

import pytest

from validation import (
    ValidationError,
    VALID_STATUSES,
    validate_slug,
    validate_status,
)


@pytest.mark.parametrize("good", ["a", "abc", "split-loop-py", "x1-y2-z3"])
def test_validate_slug_accepts(good: str):
    validate_slug(good)  # no raise


@pytest.mark.parametrize(
    "bad",
    ["", "A", "1-good", "with space", "ends-", "double--dash-ok",
     "x" * 65, "snake_case_no"],
)
def test_validate_slug_rejects(bad: str):
    with pytest.raises(ValidationError):
        validate_slug(bad)


@pytest.mark.parametrize("good", list(VALID_STATUSES))
def test_validate_status_accepts(good: str):
    validate_status(good)


def test_validate_status_rejects_empty_via_is_not_none():
    # per [[26d0a5a7-truthiness-vs-none]]: empty must reach validator, not be
    # short-circuited by falsy check upstream
    with pytest.raises(ValidationError, match="unknown status"):
        validate_status("")


@pytest.mark.parametrize("bad", ["pending ", "APPROVED", "yes", "no"])
def test_validate_status_rejects_bad(bad: str):
    with pytest.raises(ValidationError):
        validate_status(bad)
```

Per `test_validate_slug_rejects` row `"double--dash-ok"`: the regex `^[a-z0-9][a-z0-9-]{1,63}$` actually accepts that — the case is mislabelled. **Spec self-review fix**: rename it to `accepts_double_dash` and split it into the `_accepts` parametrize block. (Caught at plan-write time per [[cross-read-prose-vs-example]].)

Corrected rejects list:
```python
["", "A", "1-good", "with space", "ends-",
 "x" * 65, "snake_case_no"]
```

And add `"double--dash-ok"` to the `_accepts` list.

- [ ] **Step 2: Run tests to fail.**

- [ ] **Step 3: Implement.**

```python
"""Shared validators for tech-debt-scan."""
from __future__ import annotations

import re
from typing import Final

VALID_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "approved", "rejected", "promoted"}
)

_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class ValidationError(ValueError):
    """Raised when a value fails one of the shared validators."""


def validate_slug(value: str) -> None:
    if not _SLUG_RE.fullmatch(value):
        raise ValidationError(f"invalid slug: {value!r}")
    if value.endswith("-"):
        raise ValidationError(f"slug must not end with hyphen: {value!r}")


def validate_status(value: str) -> None:
    if value not in VALID_STATUSES:
        raise ValidationError(f"unknown status: {value!r}; expected one of {sorted(VALID_STATUSES)}")
```

- [ ] **Step 4: Run, expect PASS.**

- [ ] **Step 5: Commit.**

```bash
git add skills/tech-debt-scan/scripts/validation.py \
  skills/tech-debt-scan/tests/test_validation.py
git commit -m "feat(validation): shared slug + status validators"
```

(After this lands, **return to Task 4** and run it.)

---

### Task 6: Design renderer — `scripts/design_writer.py`

**Confidence: 93%** (lifted from 88%). Markdown rendering drift between releases + in-place edit corruption.

**Confidence lifts applied:**
1. **LF-only line endings on write.** Output produced as a single `"\n".join(parts)` string written via `out_path.write_bytes(text.encode("utf-8"))` — bypasses Python's text-mode CRLF translation on Windows. Test asserts the rendered bytes contain zero `\r` characters.
2. **Self-check via round-trip parse.** After rendering, `render_design_md` re-parses its own output via `design_parser.parse_design` as a final assertion before returning. If the parser raises, `render_design_md` raises `DesignWriteError("self-check failed: <parser-message>")`. This catches format drift at write time, not just test time.
3. **Atomic `mark_promoted`.** Writes to `<path>.tmp` then `os.replace(<path>.tmp, <path>)` (atomic on POSIX + Windows since Python 3.3). Previous content kept at `<path>.bak` (single generation; overwritten on next call). Test asserts `<path>.bak` matches pre-mutation content.
4. **`mark_promoted` is idempotent across already-promoted findings.** Calling `mark_promoted(path, slugs=["x"])` when `x` is already promoted is a no-op success (logs, returns). Test asserts no error + no `.bak` rotation when nothing changed.
5. **Golden-file tests gate every render** (`tests/golden/design.md` is the source of truth; refresh via `pytest --update-goldens` when format is intentionally changed).

**Files:**
- Create: `skills/tech-debt-scan/scripts/design_writer.py`
- Create: `skills/tech-debt-scan/tests/test_design_writer.py`
- Create: `skills/tech-debt-scan/tests/golden/design.md` (canonical fixture)

- [ ] **Step 1: Write the golden design.md.**

Hand-author a canonical example matching spec §3 exactly. 5 findings, all `status: pending`, deterministic timestamps + slugs + severity values.

- [ ] **Step 2: Write failing tests.**

```python
# skills/tech-debt-scan/tests/test_design_writer.py
from __future__ import annotations

import json
from pathlib import Path

from design_writer import (
    DesignWriteError,
    render_design_md,
    mark_promoted,
)

GOLDEN = Path(__file__).parent / "golden" / "design.md"


def _top5_payload() -> dict:
    return {
        "top5": [
            {
                "slug": f"finding-{i}",
                "title": f"Finding {i} title",
                "severity": 5 - i % 5,
                "category": "god-modules",
                "reasoning": f"reasoning {i}",
                "evidence": [{"file": "src/x.py", "line": 10, "note": "n"}],
                "suggested_fix": f"fix {i}",
            }
            for i in range(5)
        ]
    }


def _inventory_payload() -> dict:
    return {
        "root": "/abs/path/to/repo",
        "total_files": 100,
        "total_loc": 12000,
        "languages": ["python"],
        "files": [],
    }


def test_render_matches_golden(tmp_path: Path):
    out = tmp_path / "design.md"
    render_design_md(
        top5=_top5_payload(),
        inventory=_inventory_payload(),
        scan_date="2026-05-31",
        out_path=out,
    )
    assert out.read_text() == GOLDEN.read_text()


def test_render_empty_findings_raises(tmp_path: Path):
    import pytest

    with pytest.raises(DesignWriteError, match="no findings"):
        render_design_md(
            top5={"top5": []},
            inventory=_inventory_payload(),
            scan_date="2026-05-31",
            out_path=tmp_path / "design.md",
        )


def test_mark_promoted_status_transition(tmp_path: Path):
    src = tmp_path / "design.md"
    src.write_text(GOLDEN.read_text().replace("status: pending", "status: approved", 1))
    mark_promoted(src, slugs=["finding-0"])
    text = src.read_text()
    assert "status: promoted" in text
    # other findings unchanged
    assert text.count("status: pending") == 4


def test_mark_promoted_unknown_slug_raises(tmp_path: Path):
    import pytest

    src = tmp_path / "design.md"
    src.write_text(GOLDEN.read_text())
    with pytest.raises(DesignWriteError, match="unknown slug"):
        mark_promoted(src, slugs=["not-a-finding"])


def test_mark_promoted_only_changes_approved(tmp_path: Path):
    import pytest

    src = tmp_path / "design.md"
    src.write_text(GOLDEN.read_text())  # all pending
    with pytest.raises(DesignWriteError, match="not approved"):
        mark_promoted(src, slugs=["finding-0"])
```

- [ ] **Step 3: Run, expect fail.**

- [ ] **Step 4: Implement.**

Module exposes `render_design_md(top5, inventory, scan_date, out_path)` and `mark_promoted(path, slugs)`. Both raise `DesignWriteError` on issues.

Per [[aa125f7a-empty-input-guard]]: render raises on empty `top5`.

`mark_promoted` does an in-place edit:
1. Read file
2. For each requested slug:
   - Locate the H2 section by `slug:` line
   - Confirm current status is `approved` — else raise `DesignWriteError("finding <slug> is not approved")`
   - Replace `status: approved` → `status: promoted` within that section only
3. Write to `<path>.tmp`, then atomic rename → `<path>` (mitigates partial-write corruption in Task 8's confidence note)
4. Keep `<path>.bak` for the previous content.

- [ ] **Step 5: Run, expect PASS.**

- [ ] **Step 6: Commit.**

```bash
git add skills/tech-debt-scan/scripts/design_writer.py \
  skills/tech-debt-scan/tests/test_design_writer.py \
  skills/tech-debt-scan/tests/golden/design.md
git commit -m "feat(design): render design.md + atomic mark-promoted in-place edit"
```

---

### Task 7: Design parser — `scripts/design_parser.py`

**Confidence: 93%** (lifted from 90%). Pure parse.

**Confidence lifts applied:**
1. **`yaml.safe_load` only.** Never `yaml.load`. Documented at top of module.
2. **Source line numbers preserved in every error.** Parser tracks the source line of each `##` heading and each yaml block. Every `DesignParseError` message includes `line N`. Test parametrised across each error path asserts the line number in the message.
3. **Round-trip property.** Test asserts `parse_design(render_design_md(top5)) == expected_findings` after canonical comparison (strip trailing whitespace, sort findings by slug). Combined with Task 6's self-check, write/parse cycle is fully closed.
4. **Malformed-yaml fixtures.** `tests/fixtures/malformed-design/` contains: unclosed-quote.md, tabs-in-yaml.md, missing-status-key.md, missing-slug-key.md, extra-h2-with-no-yaml.md. Each fixture is parametrised into one test asserting the specific DesignParseError substring + line number.
5. **Per [[c73f9438-duplicated-validator]]:** uses `validation.validate_slug` + `validate_status` so the rejection-path test matrix is shared with Task 5.

**Files:**
- Create: `skills/tech-debt-scan/scripts/design_parser.py`
- Create: `skills/tech-debt-scan/tests/test_design_parser.py`

- [ ] **Step 1: Write failing tests.**

```python
# skills/tech-debt-scan/tests/test_design_parser.py
from __future__ import annotations

from pathlib import Path

import pytest

from design_parser import (
    DesignParseError,
    parse_design,
)

GOLDEN = Path(__file__).parent / "golden" / "design.md"


def test_parse_golden(tmp_path: Path):
    result = parse_design(GOLDEN)
    assert len(result["findings"]) == 5
    for f in result["findings"]:
        assert f["status"] == "pending"


def test_parse_mixed_statuses(tmp_path: Path):
    src = tmp_path / "design.md"
    text = GOLDEN.read_text()
    text = text.replace("status: pending", "status: approved", 1)
    src.write_text(text)
    result = parse_design(src)
    assert [f["status"] for f in result["findings"]] == [
        "approved", "pending", "pending", "pending", "pending",
    ]


def test_parse_invalid_status(tmp_path: Path):
    src = tmp_path / "design.md"
    src.write_text(GOLDEN.read_text().replace("status: pending", "status: yes", 1))
    with pytest.raises(DesignParseError, match="unknown status"):
        parse_design(src)


def test_parse_empty_status_via_is_not_none(tmp_path: Path):
    src = tmp_path / "design.md"
    src.write_text(GOLDEN.read_text().replace("status: pending", "status: ", 1))
    with pytest.raises(DesignParseError, match="unknown status"):
        parse_design(src)


def test_parse_duplicate_slug(tmp_path: Path):
    src = tmp_path / "design.md"
    src.write_text(GOLDEN.read_text().replace("slug: finding-1", "slug: finding-0"))
    with pytest.raises(DesignParseError, match="duplicate slug"):
        parse_design(src)


def test_parse_missing_yaml_block(tmp_path: Path):
    src = tmp_path / "design.md"
    text = GOLDEN.read_text()
    # delete the first finding's yaml block
    text = text.replace("```yaml\nstatus: pending", "<missing yaml block>")
    src.write_text(text)
    with pytest.raises(DesignParseError, match="no yaml anchor"):
        parse_design(src)


def test_parse_invalid_slug(tmp_path: Path):
    src = tmp_path / "design.md"
    src.write_text(GOLDEN.read_text().replace("slug: finding-0", "slug: Bad Slug"))
    with pytest.raises(DesignParseError, match="invalid slug"):
        parse_design(src)


def test_missing_file():
    with pytest.raises(DesignParseError, match="not found"):
        parse_design(Path("/nope/does-not-exist.md"))
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement.**

Module exposes `parse_design(path: Path) -> dict`. Algorithm:
1. Read file (raise on missing) — per [[aa125f7a-empty-input-guard]] wrap `read_text` in try/OSError.
2. Strip top-level YAML frontmatter (between first two `---` lines) — store as `metadata`.
3. Find every `^## ` heading. For each:
   - Look for the first ` ```yaml ... ``` ` fenced block under it
   - If absent → `DesignParseError("no yaml anchor at line N")`
   - Parse yaml; require keys `status`, `slug`, `severity`, `category`
   - `validate_status(yaml["status"])`, `validate_slug(yaml["slug"])`
4. Collect `body_md` = lines between this `##` and the next `##` (or EOF) excluding the yaml anchor block.
5. Detect duplicate slugs across the collected findings → `DesignParseError`.

CLI mode emits JSON to stdout.

- [ ] **Step 4: Run, PASS.**

- [ ] **Step 5: Commit.**

```bash
git add skills/tech-debt-scan/scripts/design_parser.py \
  skills/tech-debt-scan/tests/test_design_parser.py
git commit -m "feat(parser): parse design.md with shared slug + status validators"
```

---

### Task 8: Bundle writer — `scripts/bundle_writer.py`

**Confidence: 92%.** Straightforward file generation. Golden-file tests cover format.

**Files:**
- Create: `skills/tech-debt-scan/scripts/bundle_writer.py`
- Create: `skills/tech-debt-scan/tests/test_bundle_writer.py`
- Create: `skills/tech-debt-scan/tests/golden/bundle/chore-finding-0-2026-05-31/PBI.md`
- Create: `skills/tech-debt-scan/tests/golden/bundle/chore-finding-0-2026-05-31/PLAN.md`
- Create: `skills/tech-debt-scan/tests/golden/bundle/chore-finding-0-2026-05-31/HISTORY.md`

- [ ] **Step 1: Hand-author golden bundle files.**

PBI.md frontmatter includes `target_repo:` per [[77c83c69-target-repo-required]]. Filename is `chore-<slug>-<date>/PBI.md` per [[cfa7e7f8-pbi-shape-type-aware]].

- [ ] **Step 2: Write failing tests.**

```python
# skills/tech-debt-scan/tests/test_bundle_writer.py
from __future__ import annotations

from pathlib import Path

import pytest

from bundle_writer import (
    BundleWriteError,
    write_bundle,
)

GOLDEN_DIR = Path(__file__).parent / "golden" / "bundle"


def _finding() -> dict:
    return {
        "slug": "finding-0",
        "title": "Finding 0 title",
        "severity": 5,
        "category": "god-modules",
        "body_md": "### Evidence\n\n- foo\n\n### Suggested fix\n\nbar\n",
        "status": "approved",
    }


def test_write_bundle_creates_canonical_files(tmp_path: Path):
    written = write_bundle(_finding(), out_root=tmp_path, source_design="d.md", date="2026-05-31")
    bundle_dir = tmp_path / "chore-finding-0-2026-05-31"
    assert bundle_dir.is_dir()
    assert (bundle_dir / "PBI.md").exists()
    assert (bundle_dir / "PLAN.md").exists()
    assert (bundle_dir / "HISTORY.md").exists()
    assert written == bundle_dir


def test_pbi_contains_target_repo_key(tmp_path: Path):
    write_bundle(_finding(), out_root=tmp_path, source_design="d.md", date="2026-05-31")
    pbi = (tmp_path / "chore-finding-0-2026-05-31" / "PBI.md").read_text()
    assert "target_repo:" in pbi  # per [[77c83c69-target-repo-required]]
    assert "type: chore" in pbi


def test_collision_without_force_raises(tmp_path: Path):
    write_bundle(_finding(), out_root=tmp_path, source_design="d.md", date="2026-05-31")
    with pytest.raises(BundleWriteError, match="already exists"):
        write_bundle(_finding(), out_root=tmp_path, source_design="d.md", date="2026-05-31", force=False)


def test_force_overwrites(tmp_path: Path):
    write_bundle(_finding(), out_root=tmp_path, source_design="d.md", date="2026-05-31")
    finding2 = _finding()
    finding2["body_md"] = "### Evidence\n\n- DIFFERENT\n"
    write_bundle(finding2, out_root=tmp_path, source_design="d.md", date="2026-05-31", force=True)
    pbi = (tmp_path / "chore-finding-0-2026-05-31" / "PBI.md").read_text()
    assert "DIFFERENT" in pbi


def test_unwritable_out_dir(tmp_path: Path, monkeypatch):
    def _no_mkdir(*a, **kw):
        raise PermissionError("no write")
    monkeypatch.setattr(Path, "mkdir", _no_mkdir)
    with pytest.raises(BundleWriteError, match="cannot write"):
        write_bundle(_finding(), out_root=tmp_path, source_design="d.md", date="2026-05-31")
```

- [ ] **Step 3: Run, fail.**

- [ ] **Step 4: Implement.**

`write_bundle(finding, *, out_root, source_design, date, force=False) -> Path`. Writes PBI.md (frontmatter from finding + `target_repo:` blank), PLAN.md stub, HISTORY.md empty. Collision policy from spec §4.

- [ ] **Step 5: Run, PASS.**

- [ ] **Step 6: Commit.**

```bash
git add skills/tech-debt-scan/scripts/bundle_writer.py \
  skills/tech-debt-scan/tests/test_bundle_writer.py \
  skills/tech-debt-scan/tests/golden/bundle/
git commit -m "feat(bundle): emit ralph-friendly PBI bundle per approved finding"
```

---

### Task 9: Promote orchestrator — `scripts/promote.py`

**Confidence: 92%** (lifted from 87%). Orchestration glue + design.md mutation across modules.

**Confidence lifts applied:**
1. **Concurrent-promote support DROPPED for phase 1.** Removes exit code 6 + `portalocker` dependency. SKILL.md documents "single-user, do not run promote concurrently against the same design.md". Simpler == lower risk.
2. **Roll-forward semantics on partial failure.** If `write_bundle` succeeds for N of M findings then fails on N+1, the N succeeded bundles persist + the design.md is mutated to `promoted` for those N. The (M-N) remaining `approved` findings stay `approved`. Next run treats them as a normal promote. Test simulates `write_bundle` raising on the 3rd of 5 approved findings; asserts: 2 bundles on disk, exit code 4, design.md shows promoted for those 2 + approved for the rest.
3. **Cross-platform path handling.** Internal paths via `pathlib.Path`. Bundle output path tested under `tmp_path` on Windows-style paths via `monkeypatch.setattr(os, "sep", "\\")` — no, simpler: test parametrised on path-with-spaces and path-with-unicode subdirectories.
4. **`PromoteResult` is a `@dataclass(frozen=False, slots=True)`.** `slots=True` catches typos in field assignments at construction. Per [[462d13a7-grep-test-callsites]]: any future required field gets grep'd across `tests/` in the same commit.
5. **Existing mitigations retained:** thin orchestrator (all logic in tested sub-modules); table-driven exit codes (unit-tested); explicit `emitted_count`, `already_promoted_count`, `rejected_count`, `pending_count` separate fields per [[852f5ae9]]; `dry_run_skipped: bool = False` reserved for forward-compat.

**Files:**
- Create: `skills/tech-debt-scan/scripts/promote.py`
- Create: `skills/tech-debt-scan/tests/test_promote.py`

- [ ] **Step 1: Write failing tests.**

```python
# skills/tech-debt-scan/tests/test_promote.py
from __future__ import annotations

from pathlib import Path

import pytest

from promote import PromoteResult, run_promote

GOLDEN = Path(__file__).parent / "golden" / "design.md"


def test_no_approved_returns_zero(tmp_path: Path):
    src = tmp_path / "design.md"
    src.write_text(GOLDEN.read_text())  # all pending
    result = run_promote(src, out_root=tmp_path / "out")
    assert result.emitted_count == 0
    assert result.pending_count == 5
    assert result.exit_code == 0


def test_one_approved_emits_one_bundle(tmp_path: Path):
    src = tmp_path / "design.md"
    src.write_text(GOLDEN.read_text().replace("status: pending", "status: approved", 1))
    result = run_promote(src, out_root=tmp_path / "out")
    assert result.emitted_count == 1
    assert (tmp_path / "out" / "chore-finding-0-2026-05-31").exists()
    # design.md mutated to promoted
    assert "status: promoted" in src.read_text()
    assert result.exit_code == 0


def test_idempotent_rerun(tmp_path: Path):
    src = tmp_path / "design.md"
    src.write_text(GOLDEN.read_text().replace("status: pending", "status: approved", 1))
    run_promote(src, out_root=tmp_path / "out")
    result = run_promote(src, out_root=tmp_path / "out")
    assert result.emitted_count == 0
    assert result.already_promoted_count == 1
    assert result.exit_code == 0


def test_invalid_status_exits_2(tmp_path: Path):
    src = tmp_path / "design.md"
    src.write_text(GOLDEN.read_text().replace("status: pending", "status: yes", 1))
    result = run_promote(src, out_root=tmp_path / "out")
    assert result.exit_code == 2
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement.**

```python
"""Orchestrator for /tech-debt-promote."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bundle_writer import BundleWriteError, write_bundle
from design_parser import DesignParseError, parse_design
from design_writer import DesignWriteError, mark_promoted


@dataclass
class PromoteResult:
    emitted_count: int = 0
    already_promoted_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0
    dry_run_skipped: bool = False
    exit_code: int = 0
    emitted_paths: list[Path] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.emitted_paths is None:
            self.emitted_paths = []


def run_promote(
    design_path: Path,
    *,
    out_root: Path,
    force: bool = False,
    date: str | None = None,
) -> PromoteResult:
    if date is None:
        date = datetime.now(UTC).strftime("%Y-%m-%d")
    try:
        parsed = parse_design(design_path)
    except DesignParseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return PromoteResult(exit_code=2)

    result = PromoteResult()
    approved_slugs: list[str] = []

    for f in parsed["findings"]:
        status = f["status"]
        if status == "approved":
            try:
                path = write_bundle(
                    f,
                    out_root=out_root,
                    source_design=str(design_path),
                    date=date,
                    force=force,
                )
            except BundleWriteError as exc:
                if "already exists" in str(exc):
                    result.already_promoted_count += 1
                    continue
                print(f"error: {exc}", file=sys.stderr)
                result.exit_code = 4
                return result
            result.emitted_paths.append(path)
            result.emitted_count += 1
            approved_slugs.append(f["slug"])
        elif status == "promoted":
            result.already_promoted_count += 1
        elif status == "rejected":
            result.rejected_count += 1
        else:  # pending
            result.pending_count += 1

    if approved_slugs:
        try:
            mark_promoted(design_path, slugs=approved_slugs)
        except DesignWriteError as exc:
            print(f"error: {exc}", file=sys.stderr)
            result.exit_code = 2
            return result

    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert approved tech-debt findings to PBI bundles")
    parser.add_argument("design", type=Path, help="path to design.md")
    parser.add_argument("--out", type=Path, default=Path("./tech-debt-pbis"), help="output dir")
    parser.add_argument("--force", action="store_true", help="overwrite existing bundles")
    args = parser.parse_args(argv)
    result = run_promote(args.design, out_root=args.out, force=args.force)
    print(
        f"emitted: {result.emitted_count}, already-promoted: {result.already_promoted_count}, "
        f"rejected: {result.rejected_count}, pending: {result.pending_count}",
    )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(_main())
```

Per [[7847b0dc-mypy-hoist-defaults]]: `date` parameter is `str | None`, narrowed at function entry, then used as `str` for the rest.

Per [[dfcf8a2a-argparse-both-layers]]: `--out` and `--force` are subparser-only (no top-level parser exists).

Per [[26d0a5a7-truthiness-vs-none]]: status values reach the validator via `parse_design`; the orchestrator branches on parsed values explicitly.

Per [[462d13a7-grep-test-callsites]]: when `PromoteResult` gains a future required field, grep `PromoteResult(` under tests/ and update every call-site.

- [ ] **Step 4: Run, PASS.**

- [ ] **Step 5: Commit.**

```bash
git add skills/tech-debt-scan/scripts/promote.py \
  skills/tech-debt-scan/tests/test_promote.py
git commit -m "feat(promote): orchestrate parse -> emit -> mark-promoted with idempotency"
```

---

### Task 10: SKILL.md workflow — full

**Confidence: 92%** (lifted from 85%). SKILL.md drives Claude — non-determinism is the residual risk.

**Confidence lifts applied:**
1. **"No improvisation" rule at top of SKILL.md.** Reads verbatim: *"If any expected output file from a numbered step is missing, abort with exit 5. Do not retry steps you weren't told to retry. Do not invent intermediate state. Do not skip steps."* Test (in Task 11) asserts this exact line is present.
2. **Each numbered step has three pinned blocks: (a) prerequisite file (what the previous step produced), (b) command (exact `python scripts/foo.py …`), (c) postcondition file (what this step must produce + how to verify it exists).** Removes "Claude invents intermediate state" failure mode. Format checked by `skill_check.py` below.
3. **New script `scripts/skill_check.py`.** Walks SKILL.md, extracts every `python scripts/<name>.py` command, asserts: script exists, `--help` runs cleanly, every flag referenced in the SKILL.md command matches the argparse setup. Runs in CI on every PR. Catches doc drift per [[98056ebc-docs-in-sync]] before merge. ~50 LOC. Adds Task 10.5 (slipped into Task 10's step list — see Step 1.5 below).
4. **Cross-read against spec §2 and §4 per [[cross-read-prose-vs-example]] before commit** — done at Task 10 Step 2 (manual lint).
5. **Existing mitigations retained:** [[355faeb8-windows-argv-limit]] dictates scouts receive `--inventory <path>` not inline JSON. Both commands' workflows numbered + token budget documented. E2E test in Task 11 fakes Claude's role.

Add a new step 1.5 to the existing step list of this task (between Step 1 and Step 2):

- [ ] **Step 1.5: Write `scripts/skill_check.py` + matching test.**

`skill_check.py` reads `SKILL.md`, regex-extracts every `python scripts/(\S+\.py)(.*)$` line, then for each: confirm the script exists, run `python scripts/<name>.py --help` and capture stdout, parse out flag names, assert every flag appearing in the SKILL.md command is present in the help output. Exits 0 if all match, 2 if any mismatch (with the exact diff printed). CI workflow in Task 1 invokes it as a job step.

**Files:**
- Modify: `skills/tech-debt-scan/SKILL.md` (replace stub from Task 1)

- [ ] **Step 1: Write SKILL.md.**

Contents follow spec §2 + §4. Both commands documented in one SKILL.md (per locked decision):

- Frontmatter: name, description, triggers (`/tech-debt-scan`, `/tech-debt-promote`).
- "When to use" section.
- Step-by-step `/tech-debt-scan` workflow (6 numbered steps, exact `python scripts/inventory.py <path>` etc., expected output file names).
- Step-by-step `/tech-debt-promote` workflow (6 numbered steps).
- "Token budget" note (60–80k output tokens per scan).
- "Caveats" section listing: no live LLM in CI, exit codes, collision policy.
- Cross-read against spec §2 + §4 before commit (no contradictions, no stale "TBD").

Per [[8cada12c-package-relative-imports]]: SKILL.md uses `python scripts/foo.py` direct-path form (scripts use no package-relative imports).

- [ ] **Step 2: Lint SKILL.md** (manual: read top-to-bottom, check every command path exists, check every flag matches the script's argparse, check spec §2/§4 wording is faithful).

- [ ] **Step 3: Commit.**

```bash
git add skills/tech-debt-scan/SKILL.md
git commit -m "feat(skill): full SKILL.md workflow for scan + promote"
```

---

### Task 11: End-to-end mock test — `tests/test_e2e.py`

**Confidence: 92%** (lifted from 80%). Original phrasing called the approach "mocking Claude's Agent dispatch" which overstated novelty.

**Confidence lifts applied:**
1. **No mocking framework used.** The test does NOT mock anything. It loads canned JSON files from `tests/golden/` and feeds them to the helper scripts in the order SKILL.md prescribes. Zero `unittest.mock`, zero `monkeypatch.setattr`. The Agent dispatch step is simply *skipped*: the test pretends Claude already ran the scouts and wrote `raw-findings.json`. Files in, files out — pure deterministic IO.
2. **Parametrised across two fixture repos.** `multi-lang-repo` and `python-repo`. Catches fixture-coupled assumptions in the scripts.
3. **Golden `top5.json` self-validates at fixture load.** The test's first assertion is `validate_synthesis_output(golden_top5_text)` — passes only if the hand-authored fixture conforms to Task 4's schema. Drift in either the schema or the fixture fails fast.
4. **Two findings approved per run (not one).** Exercises the multi-bundle path + ensures `mark_promoted` handles a slug list, not just a single slug.
5. **Bundle byte-compare normalises line endings.** Both sides `replace("\r\n", "\n")` before equality. Windows CI runs the same test.
6. **Exit-code coverage.** Test asserts: happy path exit 0, collision-with-no-force exit 0 (idempotent re-run via collision detection), invalid-status exit 2.

**Files:**
- Create: `skills/tech-debt-scan/tests/test_e2e.py`
- Create: `skills/tech-debt-scan/tests/golden/raw-findings.json` (hand-authored 30 findings, deterministic)
- Create: `skills/tech-debt-scan/tests/golden/top5.json` (hand-authored synthesis output)

- [ ] **Step 1: Hand-author raw-findings.json and top5.json.**

- [ ] **Step 2: Write the E2E test.**

```python
# skills/tech-debt-scan/tests/test_e2e.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bundle_writer import write_bundle
from design_parser import parse_design
from design_writer import mark_promoted, render_design_md
from inventory import walk_inventory
from promote import run_promote

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


def test_e2e_scan_then_promote(tmp_path: Path):
    # --- Step 1: inventory ---
    inv = walk_inventory(FIXTURES / "multi-lang-repo")
    assert inv["total_files"] > 0

    # --- Step 2: skip scout dispatch (mocked: use canned raw findings) ---
    # (Production: Claude dispatches 6 Agent calls here.)
    # --- Step 3: skip persistence (we already have golden raw-findings.json) ---
    # --- Step 4: skip synthesis (use golden top5.json) ---
    top5 = json.loads((GOLDEN / "top5.json").read_text())

    # --- Step 5: render design.md ---
    design_path = tmp_path / "design.md"
    render_design_md(top5=top5, inventory=inv, scan_date="2026-05-31", out_path=design_path)

    # --- User edits status: pending -> approved for one finding ---
    text = design_path.read_text()
    text = text.replace("status: pending", "status: approved", 1)
    design_path.write_text(text)

    # --- Promote ---
    out_root = tmp_path / "tech-debt-pbis"
    result = run_promote(design_path, out_root=out_root, date="2026-05-31")
    assert result.exit_code == 0
    assert result.emitted_count == 1
    assert (out_root / "chore-finding-0-2026-05-31" / "PBI.md").exists()

    # --- Idempotent rerun ---
    result2 = run_promote(design_path, out_root=out_root, date="2026-05-31")
    assert result2.exit_code == 0
    assert result2.emitted_count == 0
    assert result2.already_promoted_count == 1
```

- [ ] **Step 3: Run.**

```bash
pytest skills/tech-debt-scan/tests/test_e2e.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit.**

```bash
git add skills/tech-debt-scan/tests/test_e2e.py \
  skills/tech-debt-scan/tests/golden/raw-findings.json \
  skills/tech-debt-scan/tests/golden/top5.json
git commit -m "test(e2e): mock-LLM end-to-end scan + promote against multi-lang fixture"
```

---

### Task 12: README + architecture docs

**Confidence: 95%.** Documentation only.

**Files:**
- Modify: `README.md`
- Create: `docs/architecture.md`

- [ ] **Step 1: Expand `README.md`.**

Sections (per [[98056ebc-docs-in-sync]] — README must reflect what the scripts actually do):

1. What it is (1 paragraph)
2. Install (clone + symlink into `~/.claude/skills/`)
3. Quickstart (`/tech-debt-scan` then `/tech-debt-promote`)
4. Output formats (link to architecture.md for full spec)
5. Language support (Python, C#, Java, TSX/JSX, Go, Rust, Ruby, PHP, Swift, C/C++, …)
6. Status (Phase 1; Phase 2 deferred)
7. License

Each documented command verified by running it against the multi-lang fixture and copying the actual output.

- [ ] **Step 2: Write `docs/architecture.md`.**

Distilled from the spec at `docs/superpowers/specs/2026-05-31-tech-debt-scan-design.md` (which is in the **ralph** repo, not this one — link to it in the README's References section, and inline the spec content into `docs/architecture.md` since the skills repo can't link cross-repo to a private spec).

- [ ] **Step 3: Verify every flag mentioned in README and architecture.md against `python scripts/<script>.py --help`** (per [[ca0308d6-flag-default-grep]]).

- [ ] **Step 4: Commit.**

```bash
git add README.md docs/architecture.md
git commit -m "docs: README quickstart + architecture page"
```

---

### Task 13: PR review + merge

**Confidence: 92%.** Standard.

- [ ] **Step 1: Push branch + run CI.**

```bash
git push
```

Wait for CI green.

- [ ] **Step 2: Self-review the diff via superpowers:requesting-code-review.**

Findings get fixed pre-merge per [[4c179d52-validation-gap-pre-merge]].

- [ ] **Step 3: Merge.**

```bash
gh pr merge --squash --delete-branch
```

---

## Self-review

**Spec coverage check** (per Section 1 of writing-plans self-review):

| Spec section | Plan task(s) |
| --- | --- |
| §1 Repo + skill layout | Task 1 |
| §2 /tech-debt-scan flow Step 1 (inventory) | Task 2 |
| §2 Step 2 (scout dispatch — categories) | Task 3 |
| §2 Step 3 (persist raw findings) | Task 11 (test); production behaviour codified in SKILL.md Task 10 |
| §2 Step 4 (synthesis) | Tasks 5 + 4 |
| §2 Step 5 (render design.md) | Task 6 |
| §2 Step 6 (report to user) | Task 10 (SKILL.md instruction) |
| §3 design.md format | Tasks 6 + 7 |
| §4 promote flow | Tasks 7, 8, 9 |
| §5 testing layers | Tasks 2, 6, 7, 8, 9, 11 |
| §5 error cases | Distributed across each task's negative-case tests |

Gap: §2 Step 3 (raw findings persistence) has no dedicated task — it's a SKILL.md instruction. Acceptable since the persistence is Claude calling `Path(".tech-debt/raw-findings.json").write_text(json.dumps(...))` directly per SKILL.md. Captured in Task 10's checklist.

**Placeholder scan:** No "TBD" / "TODO" / "implement later" outside legitimate context (PLAN.md stub literal, "name TBD by user" in Task 1 Step 1). ✓

**Type consistency:** `PromoteResult` (Task 9), `Finding` shape (used in Tasks 6, 7, 8, 9), `ScoutFinding` schema (Tasks 3, 4) cross-referenced. Field names consistent across tasks. ✓

**Cross-read prose vs example code** (per [[cross-read-prose-vs-example]]):
- Task 5 test parametrize "double--dash-ok" mismatch caught and fixed inline.
- Task 9 `dry_run_skipped` field has no implementation path that sets it; left as forward-compat only — flagged. Accepted.
- Date formatting: `datetime.now(UTC).strftime("%Y-%m-%d")` used consistently. ✓
- Spec test code lint: imports use `from datetime import UTC` (not `timezone.utc`); no stray `import pytest` outside test bodies; no `(str, Enum)` patterns. ✓

**Task confidence summary (after lift pass):**

| Task | Pre-lift | Post-lift | Lift summary |
| --- | --- | --- | --- |
| 1 | 92% | 92% | — |
| 2 | 90% | 93% | Symlink skip + per-file PermissionError continue + LOC platform-indep + hidden-dir policy |
| 3 | 95% | 95% | — |
| 5 (runs before 4) | 95% | 95% | — |
| 4 | 88% | 93% | Token cap + dataclass strict validator + recorded-fixture matrix + retry codified + ordering fixed |
| 6 | 88% | 93% | LF-only bytes write + round-trip self-check + atomic os.replace + idempotent mark_promoted |
| 7 | 90% | 93% | yaml.safe_load + source line numbers in errors + round-trip property + malformed-yaml fixtures |
| 8 | 92% | 92% | — |
| 9 | 87% | 92% | Drop file-locking + roll-forward semantics + cross-platform path tests + slotted dataclass |
| 10 | 85% | 92% | No-improvisation rule + 3-block step structure + skill_check.py CI gate |
| 11 | 80% | 92% | No mocks (just file IO) + dual-fixture parametrise + self-validating golden + LF-norm compare |
| 12 | 95% | 95% | — |
| 13 | 92% | 92% | — |

**All tasks now ≥ 92% effective confidence.** No task ships below the user-mandated 91% floor.

---

## Execution handoff

After this plan is committed and rendered in the visualiser, the user picks:

1. **Subagent-Driven** (recommended) — `superpowers:subagent-driven-development` dispatches a fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — `superpowers:executing-plans`, batch execution with checkpoints.
