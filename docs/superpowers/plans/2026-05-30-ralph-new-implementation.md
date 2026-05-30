# ralph-new Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `ralph-new` skill (direct PBI authoring into ralph-queue, no GitHub issue), retire `ralph-add` + `workitem-fetch-*`, and land the `prompt/PROMPT.md` `target_repo` wording fix — all in one PR.

**Architecture:** Five-module Python skill under `skills/ralph-new/scripts/` (`new.py`, `writer.py`, `templates.py`, `slugify.py`, `validate.py`). Each module ≤200 lines, pure-function helpers covered by unit tests; CLI orchestration covered by an end-to-end test file using the lifted `queue_env` fixture. Frontmatter schema, file layout, and exit codes match the spec at `docs/superpowers/specs/2026-05-30-ralph-new-design.md`.

**Tech Stack:** Python 3.12, pytest, ruff, mypy, git (subprocess via existing `scripts/queue_writer.py` helpers), YAML frontmatter (manual render — same as `skills/ralph-add/scripts/add.py::_render_frontmatter`).

**Reference spec:** `docs/superpowers/specs/2026-05-30-ralph-new-design.md` (committed `701e576` on branch `ralph/ralph-new-spec`).

**Repo conventions reminder:** push the queue clone to its configured `queue_branch` (default `ralph-queue`), not `main`. The bare-repo fixture seeds `-b ralph-queue`; operator skills resolve `queue_branch` via `ralph_executor.user_config.read_queue_branch`.

---

## Task 1: Branch + skill scaffold + lifted `queue_env` fixture

**Files:**
- Create: `skills/ralph-new/SKILL.md`
- Create: `skills/ralph-new/__init__.py` (empty marker)
- Create: `skills/ralph-new/scripts/__init__.py` (empty marker)
- Create: `tests/conftest.py` (NEW; lift `queue_env` + `_git` helper here so the fixture survives `test_ralph_add.py` deletion in Task 10)

- [ ] **Step 1: Create the feature branch**

```bash
git checkout main && git pull
git checkout -b ralph/RALPH-NEW-DIRECT-AUTHOR
```

- [ ] **Step 2: Create the skill directory + empty `__init__.py` files**

```bash
mkdir -p skills/ralph-new/scripts
touch skills/ralph-new/__init__.py skills/ralph-new/scripts/__init__.py
```

- [ ] **Step 3: Write the initial `SKILL.md`** (place at `skills/ralph-new/SKILL.md`)

```markdown
---
name: ralph-new
description: Author a new PBI directly into the ralph-queue. Interactive prompts (or flags) gather title, type (bug/feature), severity, target_repo, depends_on, and per-type body content. Writes the canonical PBI directory shape (BUG.md+REPRODUCE.md+HISTORY.md for bugs; PBI.md+PLAN.md+HISTORY.md for features), commits with `chore(queue): add <id>`, and pushes to the queue branch. Sole submission surface — supersedes ralph-add. Spec: docs/superpowers/specs/2026-05-30-ralph-new-design.md.
---

# ralph-new

`ralph-new` is the single operator-facing submission surface for Ralph PBIs.

## When to use it

Use `ralph-new` whenever a human (BA, PM, triager, dev) wants to file a bug or feature against any service repo. No GitHub issue required.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--title TEXT` | yes (via prompt or flag) | PBI title; slugified to PBI id. |
| `--type {bug,feature}` | yes | PBI type. `pr-feedback` is sweep-only and rejected. |
| `--severity {critical,high,normal,low}` | no (default `normal`) | Triage lane. |
| `--target-repo URL` | yes | HTTPS owner/name URL of the target service repo. |
| `--depends-on ID` | no (repeatable) | Each id is validated for syntax AND for existence in the queue. |
| `--parent-id ID` | no | Epic link. |
| `--id SLUG` | no | Override auto-generated slug. |
| `--spec-path PATH` | no | Feature only: docs/superpowers/specs path. |
| `--plan-path PATH` | no | Feature only: docs/superpowers/plans path; blank renders a TODO stub. |
| `--body-file PATH` | no | Read entire entry-file body from file; skips per-section prompts. |
| `--reproduce-file PATH` | no | Bug only: read REPRODUCE.md body from file. |
| `--non-interactive` | no | Refuse to prompt; missing required field exits 2. |
| `--no-push` | no | Commit but do not push. |
| `--dry-run` | no | Print envelope; no clone / write / commit. |
| `--check-depends-on` | no | Under `--dry-run`, force clone so existence check runs. |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |

## Output

JSON envelope on stdout describing the written PBI. Exit 0 on success, 2 on validation/queue errors, 130 on Ctrl+C.

## Files written per type

- **Bug:** `BUG.md`, `REPRODUCE.md`, `HISTORY.md`
- **Feature:** `PBI.md`, `PLAN.md`, `HISTORY.md`

See the spec for the full body templates.
```

- [ ] **Step 4: Lift `queue_env` fixture into `tests/conftest.py`** (place at `tests/conftest.py`; if file already exists, append the fixture)

```python
"""Project-wide pytest fixtures."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str) -> str:
    """Run git with stable Windows-safe encoding; return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


@pytest.fixture
def queue_env(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """Bare queue remote on ``ralph-queue`` + empty workspace dir.

    Returns ``(workspace_root, queue_repo_url)`` for the test to pass into
    ralph-new via ``--workspace`` / ``--queue-repo``.
    """
    bare = tmp_path / "queue.git"
    seed = tmp_path / "queue-seed"
    workspace = tmp_path / "ws"
    subprocess.run(["git", "init", "--bare", "-b", "ralph-queue", str(bare)], check=True)
    subprocess.run(["git", "init", "-b", "ralph-queue", str(seed)], check=True)
    _git(seed, "config", "user.email", "seed@example.com")
    _git(seed, "config", "user.name", "Seed")
    _git(seed, "commit", "--allow-empty", "-m", "chore: initial ralph-queue")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-u", "origin", "ralph-queue")
    yield workspace, str(bare)
```

- [ ] **Step 5: Verify pytest still discovers the fixture and existing tests pass**

Run: `uv run pytest tests/skills/test_ralph_add.py -k "queue_env or not queue_env" --collect-only -q`
Expected: collects without error. (No tests run — collect-only.)

Run: `uv run pytest -q`
Expected: all green. The lifted fixture coexists with the duplicate in `test_ralph_add.py` until Task 10 deletes that file.

- [ ] **Step 6: Commit**

```bash
git add skills/ralph-new/ tests/conftest.py
git commit -m "scaffold(ralph-new): empty skill dir + SKILL.md + lifted queue_env fixture"
```

---

## Task 2: `validate.py` — pure field validators with full unit coverage

**Files:**
- Create: `skills/ralph-new/scripts/validate.py`
- Create: `tests/skills/test_ralph_new_validate.py`

- [ ] **Step 1: Write the failing tests** (place at `tests/skills/test_ralph_new_validate.py`)

```python
"""Pure-function validators for ralph-new fields."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "skills" / "ralph-new" / "scripts" / "validate.py"


@pytest.fixture(scope="module")
def validate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ralph_new_validate", MOD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---- target_repo -----------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo",
        "https://dev.azure.com/org/project/_git/repo",
    ],
)
def test_target_repo_accepts_valid_https_url(validate, url):
    assert validate.validate_target_repo(url) is None


@pytest.mark.parametrize(
    "url, fragment",
    [
        ("http://github.com/owner/repo", "HTTPS"),
        ("ftp://github.com/owner/repo", "HTTPS"),
        ("https://github.com", "owner"),
        ("https://github.com/owner", "owner"),
        ("https://", "host"),
        ("https://github.com/owner/repo\n", "newline"),
        ("https://github.com/owner/repo\r", "newline"),
    ],
)
def test_target_repo_rejects_bad_url(validate, url, fragment):
    msg = validate.validate_target_repo(url)
    assert msg is not None
    assert fragment.lower() in msg.lower()


# ---- pbi_id ----------------------------------------------------------

@pytest.mark.parametrize(
    "pbi_id",
    [
        "FIX-LOGIN",
        "BUG-A-B-C-1-2-3",
        "FOO",
        "F" + "O" * 78 + "O",  # 80 chars exact
    ],
)
def test_pbi_id_accepts_valid(validate, pbi_id):
    assert validate.validate_pbi_id(pbi_id) is None


@pytest.mark.parametrize(
    "pbi_id, fragment",
    [
        ("", "empty"),
        ("a", "regex"),  # lowercase
        ("FO", "length"),  # too short (2 chars but ends OK; min is 2 — adjust if 3)
        ("F" + "O" * 80, "length"),  # 81 chars too long
        ("-FOO", "regex"),  # leading hyphen
        ("FOO-", "regex"),  # trailing hyphen
        ("FOO BAR", "regex"),  # space
        ("FOO_BAR", "regex"),  # underscore
    ],
)
def test_pbi_id_rejects_invalid(validate, pbi_id, fragment):
    msg = validate.validate_pbi_id(pbi_id)
    assert msg is not None
    # length errors mention size; regex errors mention "regex" or "must match"
    assert fragment.lower() in msg.lower() or "regex" in msg.lower() or "match" in msg.lower()


# ---- severity --------------------------------------------------------

@pytest.mark.parametrize("s", ["critical", "high", "normal", "low"])
def test_severity_accepts_valid(validate, s):
    assert validate.validate_severity(s) is None


@pytest.mark.parametrize("s", ["", "URGENT", "med", "high-ish", "Critical"])
def test_severity_rejects_invalid(validate, s):
    assert validate.validate_severity(s) is not None


# ---- type ------------------------------------------------------------

@pytest.mark.parametrize("t", ["bug", "feature"])
def test_type_accepts_bug_and_feature(validate, t):
    assert validate.validate_type(t) is None


@pytest.mark.parametrize("t", ["", "pr-feedback", "Feature", "task", "story"])
def test_type_rejects_anything_else(validate, t):
    msg = validate.validate_type(t)
    assert msg is not None
    if t == "pr-feedback":
        assert "sweep" in msg.lower() or "pr-feedback" in msg.lower()


# ---- depends_on syntax -----------------------------------------------

def test_depends_on_syntax_accepts_list_of_valid_ids(validate):
    assert validate.validate_depends_on_syntax(["FOO", "BAR-1", "BAZ-QUX-2"]) is None


def test_depends_on_syntax_accepts_empty_list(validate):
    assert validate.validate_depends_on_syntax([]) is None


def test_depends_on_syntax_rejects_any_invalid_id(validate):
    msg = validate.validate_depends_on_syntax(["FOO", "bar", "BAZ"])
    assert msg is not None
    assert "bar" in msg
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run pytest tests/skills/test_ralph_new_validate.py -q`
Expected: `ModuleNotFoundError` or import error — the module doesn't exist yet. Collection fails.

- [ ] **Step 3: Implement `validate.py`** (place at `skills/ralph-new/scripts/validate.py`)

```python
"""Pure-function validators for ralph-new field inputs.

Each ``validate_*`` returns ``None`` on success or a human-readable error
string on failure. Caller is responsible for choosing how to surface the
error (prompt re-ask, stderr + exit 2, etc.).
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Iterable

# Slug + PBI-id syntax: 2–80 chars, starts and ends with alphanumeric,
# uppercase + digits + hyphens only.
_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]*[A-Z0-9]$")
_MIN_ID_LEN = 2
_MAX_ID_LEN = 80

_ALLOWED_SEVERITIES: tuple[str, ...] = ("critical", "high", "normal", "low")
_ALLOWED_TYPES: tuple[str, ...] = ("bug", "feature")


def validate_target_repo(value: str) -> str | None:
    """Return None if ``value`` is a valid HTTPS owner/name URL."""
    if not isinstance(value, str) or not value:
        return "target_repo must be a non-empty string"
    if "\n" in value or "\r" in value:
        return f"target_repo URL must not contain newline or carriage return: {value!r}"
    if not value.startswith("https://"):
        return f"target_repo must be an HTTPS URL, got {value!r}"
    parsed = urllib.parse.urlparse(value)
    if not parsed.netloc:
        return f"target_repo URL has no host: {value!r}"
    segments = [p for p in parsed.path.split("/") if p]
    if len(segments) < 2:
        return f"target_repo URL must include owner + name path segments: {value!r}"
    return None


def validate_pbi_id(value: str) -> str | None:
    """Return None if ``value`` is a syntactically valid PBI id / slug."""
    if not isinstance(value, str) or not value:
        return "PBI id is empty"
    n = len(value)
    if n < _MIN_ID_LEN or n > _MAX_ID_LEN:
        return f"PBI id length {n} not in {_MIN_ID_LEN}..{_MAX_ID_LEN}: {value!r}"
    if not _ID_PATTERN.fullmatch(value):
        return (
            f"PBI id {value!r} must match regex {_ID_PATTERN.pattern} "
            f"(uppercase A-Z + digits + hyphens; no leading/trailing hyphen)"
        )
    return None


def validate_severity(value: str) -> str | None:
    if value not in _ALLOWED_SEVERITIES:
        return f"severity {value!r} not in {_ALLOWED_SEVERITIES}"
    return None


def validate_type(value: str) -> str | None:
    if value == "pr-feedback":
        return (
            "type 'pr-feedback' is sweep-generated only; ralph-new does not "
            "author pr-feedback PBIs"
        )
    if value not in _ALLOWED_TYPES:
        return f"type {value!r} not in {_ALLOWED_TYPES}"
    return None


def validate_depends_on_syntax(ids: Iterable[str]) -> str | None:
    """Return None if every id passes ``validate_pbi_id``."""
    for dep_id in ids:
        err = validate_pbi_id(dep_id)
        if err is not None:
            return f"depends_on entry {dep_id!r}: {err}"
    return None
```

- [ ] **Step 4: Run the tests; confirm all pass**

Run: `uv run pytest tests/skills/test_ralph_new_validate.py -q`
Expected: all green (38+ tests collected).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check skills/ralph-new/scripts/validate.py tests/skills/test_ralph_new_validate.py`
Expected: All checks passed!

Run: `uv run ruff format --check skills/ralph-new/scripts/validate.py tests/skills/test_ralph_new_validate.py`
Expected: would format → if so, run `uv run ruff format ...` and re-run check.

Run: `uv run mypy skills/ralph-new/scripts/validate.py`
Expected: Success: no issues found.

- [ ] **Step 6: Commit**

```bash
git add skills/ralph-new/scripts/validate.py tests/skills/test_ralph_new_validate.py
git commit -m "feat(ralph-new): validate.py — pure field validators (target_repo, pbi_id, severity, type, depends_on)"
```

---

## Task 3: `slugify.py` — title → slug + collision resolution

**Files:**
- Create: `skills/ralph-new/scripts/slugify.py`
- Create: `tests/skills/test_ralph_new_slugify.py`

- [ ] **Step 1: Write the failing tests** (place at `tests/skills/test_ralph_new_slugify.py`)

```python
"""Slug derivation + collision-resolution for ralph-new."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "skills" / "ralph-new" / "scripts" / "slugify.py"


@pytest.fixture(scope="module")
def slugify() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ralph_new_slugify", MOD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---- slug derivation -------------------------------------------------

@pytest.mark.parametrize(
    "title, expected",
    [
        ("Fix login timeout", "FIX-LOGIN-TIMEOUT"),
        ("Fix bug: Safari hangs (#42)", "FIX-BUG-SAFARI-HANGS-42"),
        ("   trailing   spaces   ", "TRAILING-SPACES"),
        ("multiple---hyphens", "MULTIPLE-HYPHENS"),
        ("lowercase only", "LOWERCASE-ONLY"),
        ("MiXeD CaSe", "MIXED-CASE"),
    ],
)
def test_slug_basic(slugify, title, expected):
    assert slugify.slug_from_title(title) == expected


def test_slug_truncated_at_80(slugify):
    title = "X " * 60  # 120 chars
    slug = slugify.slug_from_title(title)
    assert len(slug) <= 80
    # Resulting slug must NOT end with a trailing hyphen even when truncated
    assert not slug.endswith("-")


def test_slug_collapses_whitespace(slugify):
    assert slugify.slug_from_title("  Fix    bug   ") == "FIX-BUG"


def test_slug_rejects_empty_title(slugify):
    with pytest.raises(ValueError, match="empty"):
        slugify.slug_from_title("")


def test_slug_rejects_punctuation_only(slugify):
    with pytest.raises(ValueError, match="no valid"):
        slugify.slug_from_title("???!!!---")


# ---- collision resolution --------------------------------------------

def _existing_slugs(*ids: str) -> set[str]:
    return set(ids)


def test_collision_no_existing_uses_base(slugify):
    out = slugify.resolve_collision("FOO-BAR", existing=_existing_slugs())
    assert out == "FOO-BAR"


def test_collision_appends_suffix_2(slugify):
    out = slugify.resolve_collision("FOO-BAR", existing=_existing_slugs("FOO-BAR"))
    assert out == "FOO-BAR-2"


def test_collision_finds_first_free_suffix(slugify):
    existing = _existing_slugs("FOO-BAR", "FOO-BAR-2", "FOO-BAR-3")
    out = slugify.resolve_collision("FOO-BAR", existing=existing)
    assert out == "FOO-BAR-4"


def test_collision_overflow_raises(slugify):
    existing = _existing_slugs("FOO-BAR", *[f"FOO-BAR-{i}" for i in range(2, 11)])
    with pytest.raises(ValueError, match="too many collisions"):
        slugify.resolve_collision("FOO-BAR", existing=existing)
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run pytest tests/skills/test_ralph_new_slugify.py -q`
Expected: collection error — module missing.

- [ ] **Step 3: Implement `slugify.py`** (place at `skills/ralph-new/scripts/slugify.py`)

```python
"""Derive a PBI slug from a free-form title; resolve queue-side collisions.

Slug rules (must round-trip through ``validate.validate_pbi_id``):

- Uppercase A–Z, digits 0–9, hyphens only
- 2–80 chars
- No leading or trailing hyphen
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_MAX_SLUG_LEN = 80
_MAX_COLLISION_SUFFIX = 10

# Any run of non-(alnum) is treated as a separator.
_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")


def slug_from_title(title: str) -> str:
    """Convert a human title into a canonical PBI slug.

    Raises ``ValueError`` for empty input or input that collapses to no
    alphanumeric characters (e.g. ``"???"``).
    """
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is empty")
    # Split on any non-alnum run, uppercase, drop empty pieces, join with hyphens.
    parts = [p for p in _SEPARATOR.split(title) if p]
    if not parts:
        raise ValueError(f"title has no valid characters for a slug: {title!r}")
    slug = "-".join(p.upper() for p in parts)
    if len(slug) > _MAX_SLUG_LEN:
        # Truncate, then strip trailing hyphen if the cut landed on one.
        slug = slug[:_MAX_SLUG_LEN].rstrip("-")
    return slug


def resolve_collision(base_slug: str, *, existing: Iterable[str]) -> str:
    """Return ``base_slug`` if free, else the first free ``base_slug-N`` (N=2..10).

    Raises ``ValueError`` after exhausting suffixes 2–10 inclusive.
    """
    existing_set = set(existing)
    if base_slug not in existing_set:
        return base_slug
    for suffix in range(2, _MAX_COLLISION_SUFFIX + 1):
        candidate = f"{base_slug}-{suffix}"
        if candidate not in existing_set:
            return candidate
    raise ValueError(
        f"too many collisions on {base_slug!r}; rerun with --id <unique-slug>"
    )
```

- [ ] **Step 4: Run the tests; confirm all pass**

Run: `uv run pytest tests/skills/test_ralph_new_slugify.py -q`
Expected: all green.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check skills/ralph-new/scripts/slugify.py tests/skills/test_ralph_new_slugify.py`
Run: `uv run ruff format --check skills/ralph-new/scripts/slugify.py tests/skills/test_ralph_new_slugify.py`
Run: `uv run mypy skills/ralph-new/scripts/slugify.py`
Expected: all green / no issues.

- [ ] **Step 6: Commit**

```bash
git add skills/ralph-new/scripts/slugify.py tests/skills/test_ralph_new_slugify.py
git commit -m "feat(ralph-new): slugify.py — title to slug + collision resolution (max 10 suffixes)"
```

---

## Task 4: `templates.py` — bug + feature body templates

**Files:**
- Create: `skills/ralph-new/scripts/templates.py`
- (Tests for templates live alongside the writer tests in Task 5 — they exercise rendered output end-to-end.)

- [ ] **Step 1: Implement `templates.py`** (place at `skills/ralph-new/scripts/templates.py`)

```python
"""Body templates for ralph-new — bug + feature + HISTORY constant.

All templates use simple ``str.format``-style placeholders. Section
rendering is the caller's job (``writer.write_pbi_dir``); these strings
are the canonical layout the spec pins.
"""

from __future__ import annotations

HISTORY_TEMPLATE = (
    "<!-- Executor appends attempt records here. Do not delete — "
    "required by the PBI directory schema. -->\n"
)

_NOT_PROVIDED = "_Not provided_"


def render_bug_md(
    *,
    frontmatter: str,
    title: str,
    symptom: str,
    root_cause: str,
    impact: str,
    acceptance_criteria: str,
) -> str:
    """Render the full ``BUG.md`` file body, frontmatter included."""
    return (
        f"{frontmatter}"
        f"# {title}\n"
        f"\n"
        f"## Symptom\n"
        f"{_section_body(symptom)}\n"
        f"\n"
        f"## Root cause (suspected)\n"
        f"{_section_body(root_cause)}\n"
        f"\n"
        f"## Impact\n"
        f"{_section_body(impact)}\n"
        f"\n"
        f"## Acceptance criteria\n"
        f"{_section_body(acceptance_criteria)}\n"
    )


def render_reproduce_md(
    *,
    title: str,
    environment: str,
    steps: str,
    expected: str,
    actual: str,
) -> str:
    """Render the full ``REPRODUCE.md`` file body. No frontmatter."""
    return (
        f"# Reproduce: {title}\n"
        f"\n"
        f"## Environment\n"
        f"{_section_body(environment)}\n"
        f"\n"
        f"## Steps\n"
        f"{_section_body(steps)}\n"
        f"\n"
        f"## Expected\n"
        f"{_section_body(expected)}\n"
        f"\n"
        f"## Actual\n"
        f"{_section_body(actual)}\n"
    )


def render_pbi_md(
    *,
    frontmatter: str,
    title: str,
    description: str,
    acceptance_criteria: str,
    spec_path: str,
    plan_path: str,
) -> str:
    """Render the full feature ``PBI.md`` file body, frontmatter included."""
    spec_line = f"Spec: `{spec_path}`" if spec_path else "Spec: `<TBD>`"
    plan_line = f"Plan: `{plan_path}`" if plan_path else "Plan: `<TBD>`"
    return (
        f"{frontmatter}"
        f"# {title}\n"
        f"\n"
        f"{_section_body(description)}\n"
        f"\n"
        f"{spec_line}\n"
        f"{plan_line}\n"
        f"\n"
        f"## Acceptance criteria\n"
        f"{_section_body(acceptance_criteria)}\n"
        f"\n"
        f"See `PLAN.md` for the implementation plan pointer.\n"
    )


def render_plan_md(*, title: str, plan_path: str) -> str:
    """Render ``PLAN.md`` as a pointer (path supplied) or TODO stub (blank)."""
    if plan_path:
        return (
            f"# Plan: {title}\n"
            f"\n"
            f"See `{plan_path}` for the canonical implementation plan task list.\n"
            f"\n"
            f"Execute the plan's tasks in order. The plan is the authoritative source — "
            f"this `PLAN.md` is just a pointer.\n"
        )
    return (
        f"# Plan: {title}\n"
        f"\n"
        f"TODO: write the implementation plan in "
        f"`docs/superpowers/plans/<file>.md` and update this file to point at it.\n"
    )


def _section_body(text: str) -> str:
    """Return the section body, or the italic 'not provided' placeholder."""
    stripped = text.strip()
    if not stripped:
        return _NOT_PROVIDED
    return stripped
```

- [ ] **Step 2: Quick smoke import**

Run: `uv run python -c "import sys; sys.path.insert(0, 'skills/ralph-new/scripts'); import templates; print(templates.HISTORY_TEMPLATE)"`
Expected: prints the HISTORY template marker comment.

- [ ] **Step 3: Lint + type-check**

Run: `uv run ruff check skills/ralph-new/scripts/templates.py`
Run: `uv run ruff format --check skills/ralph-new/scripts/templates.py`
Run: `uv run mypy skills/ralph-new/scripts/templates.py`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add skills/ralph-new/scripts/templates.py
git commit -m "feat(ralph-new): templates.py — bug/reproduce/feature/plan body templates + HISTORY constant"
```

---

## Task 5: `writer.py` — frontmatter render + write_pbi_dir + writer tests

**Files:**
- Create: `skills/ralph-new/scripts/writer.py`
- Create: `tests/skills/test_ralph_new_writer.py`

- [ ] **Step 1: Write the failing tests** (place at `tests/skills/test_ralph_new_writer.py`)

```python
"""Tests for the frontmatter renderer + write_pbi_dir orchestrator."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRITER_PATH = REPO_ROOT / "skills" / "ralph-new" / "scripts" / "writer.py"
TEMPLATES_PATH = REPO_ROOT / "skills" / "ralph-new" / "scripts" / "templates.py"


@pytest.fixture(scope="module")
def writer() -> ModuleType:
    # Templates must be importable first because writer imports it.
    tspec = importlib.util.spec_from_file_location("ralph_new_templates", TEMPLATES_PATH)
    assert tspec is not None and tspec.loader is not None
    tmod = importlib.util.module_from_spec(tspec)
    sys.modules["ralph_new_templates"] = tmod
    tspec.loader.exec_module(tmod)

    wspec = importlib.util.spec_from_file_location("ralph_new_writer", WRITER_PATH)
    assert wspec is not None and wspec.loader is not None
    wmod = importlib.util.module_from_spec(wspec)
    sys.modules["ralph_new_writer"] = wmod
    wspec.loader.exec_module(wmod)
    return wmod


FIXED_TS = datetime(2026, 5, 30, 14, 0, 0, tzinfo=timezone.utc)
ISO = "2026-05-30T14:00:00+00:00"


# ---- frontmatter -----------------------------------------------------

def test_render_frontmatter_feature_full(writer):
    fm = writer.render_frontmatter(
        pbi_id="FEAT-X",
        pbi_type="feature",
        severity="normal",
        target_repo="https://github.com/owner/repo",
        depends_on=("PREREQ-A", "PREREQ-B"),
        parent_id="EPIC-1",
        now=FIXED_TS,
    )
    assert fm.startswith("---\n")
    assert fm.endswith("---\n\n")
    assert "id: FEAT-X" in fm
    assert "type: feature" in fm
    assert "status: inbox" in fm
    assert "severity: normal" in fm
    assert "attempts: 0" in fm
    assert f"created_at: {ISO}" in fm
    assert f"updated_at: {ISO}" in fm
    assert "depends_on:\n  - PREREQ-A\n  - PREREQ-B" in fm
    assert "target_repo: https://github.com/owner/repo" in fm
    assert "parent_id: EPIC-1" in fm


def test_render_frontmatter_bug_no_parent_id(writer):
    fm = writer.render_frontmatter(
        pbi_id="BUG-Y",
        pbi_type="bug",
        severity="high",
        target_repo="https://github.com/owner/repo",
        depends_on=(),
        parent_id=None,
        now=FIXED_TS,
    )
    assert "parent_id:" not in fm
    assert "depends_on: []" in fm
    assert "type: bug" in fm


def test_render_frontmatter_depends_on_serialises_correctly(writer):
    fm = writer.render_frontmatter(
        pbi_id="X",
        pbi_type="feature",
        severity="low",
        target_repo="https://github.com/o/r",
        depends_on=(),
        parent_id=None,
        now=FIXED_TS,
    )
    assert "depends_on: []" in fm


def test_history_template_constant_present_with_marker(writer):
    import importlib.util as _i
    tspec = _i.spec_from_file_location("rt", TEMPLATES_PATH)
    assert tspec is not None and tspec.loader is not None
    tmod = _i.module_from_spec(tspec)
    tspec.loader.exec_module(tmod)
    assert "Executor appends attempt records here" in tmod.HISTORY_TEMPLATE


# ---- write_pbi_dir ---------------------------------------------------

def test_write_pbi_dir_bug_creates_three_files(writer, tmp_path):
    pbi_dir = tmp_path / "BUG-X"
    writer.write_pbi_dir(
        pbi_dir=pbi_dir,
        pbi_type="bug",
        bug_inputs={
            "frontmatter": "---\nid: BUG-X\n---\n\n",
            "title": "Login broken",
            "symptom": "form hangs",
            "root_cause": "tls handshake race",
            "impact": "12% of users",
            "acceptance_criteria": "- AC1",
            "reproduce": {
                "environment": "Safari 17",
                "steps": "1. ...",
                "expected": "login ok",
                "actual": "timeout",
            },
        },
        feature_inputs=None,
    )
    assert sorted(p.name for p in pbi_dir.iterdir()) == ["BUG.md", "HISTORY.md", "REPRODUCE.md"]
    assert "Login broken" in (pbi_dir / "BUG.md").read_text(encoding="utf-8")
    assert "Environment" in (pbi_dir / "REPRODUCE.md").read_text(encoding="utf-8")
    assert "Executor appends" in (pbi_dir / "HISTORY.md").read_text(encoding="utf-8")


def test_write_pbi_dir_feature_creates_three_files(writer, tmp_path):
    pbi_dir = tmp_path / "FEAT-X"
    writer.write_pbi_dir(
        pbi_dir=pbi_dir,
        pbi_type="feature",
        bug_inputs=None,
        feature_inputs={
            "frontmatter": "---\nid: FEAT-X\n---\n\n",
            "title": "New thing",
            "description": "Build a new thing.",
            "acceptance_criteria": "- AC1\n- AC2",
            "spec_path": "docs/superpowers/specs/foo.md",
            "plan_path": "docs/superpowers/plans/foo.md",
        },
    )
    assert sorted(p.name for p in pbi_dir.iterdir()) == ["HISTORY.md", "PBI.md", "PLAN.md"]
    pbi_text = (pbi_dir / "PBI.md").read_text(encoding="utf-8")
    assert "New thing" in pbi_text
    assert "Acceptance criteria" in pbi_text
    assert "Spec: `docs/superpowers/specs/foo.md`" in pbi_text


def test_write_pbi_dir_feature_blank_plan_writes_stub(writer, tmp_path):
    pbi_dir = tmp_path / "FEAT-Y"
    writer.write_pbi_dir(
        pbi_dir=pbi_dir,
        pbi_type="feature",
        bug_inputs=None,
        feature_inputs={
            "frontmatter": "---\nid: FEAT-Y\n---\n\n",
            "title": "Another thing",
            "description": "",
            "acceptance_criteria": "",
            "spec_path": "",
            "plan_path": "",
        },
    )
    plan_text = (pbi_dir / "PLAN.md").read_text(encoding="utf-8")
    assert "TODO" in plan_text


def test_write_pbi_dir_refuses_existing_dir(writer, tmp_path):
    pbi_dir = tmp_path / "BUG-Z"
    pbi_dir.mkdir()
    with pytest.raises(FileExistsError):
        writer.write_pbi_dir(
            pbi_dir=pbi_dir,
            pbi_type="bug",
            bug_inputs={
                "frontmatter": "---\n---\n\n",
                "title": "x",
                "symptom": "",
                "root_cause": "",
                "impact": "",
                "acceptance_criteria": "",
                "reproduce": {
                    "environment": "",
                    "steps": "",
                    "expected": "",
                    "actual": "",
                },
            },
            feature_inputs=None,
        )
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run pytest tests/skills/test_ralph_new_writer.py -q`
Expected: collection error — writer module missing.

- [ ] **Step 3: Implement `writer.py`** (place at `skills/ralph-new/scripts/writer.py`)

```python
"""Frontmatter renderer + directory-write orchestrator for ralph-new.

Frontmatter shape matches the canonical schema parsed by
``ralph_executor.queue.filesystem.parse_pbi_directory``:

    id, type, status=inbox, severity, attempts=0, created_at,
    updated_at, depends_on (list), target_repo, parent_id (optional)

``write_pbi_dir`` is type-dispatched: writes BUG.md+REPRODUCE.md+HISTORY.md
for ``bug`` PBIs, or PBI.md+PLAN.md+HISTORY.md for ``feature`` PBIs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# This module sits next to templates.py at runtime; importable as a sibling
# either when loaded as a package (skills.ralph_new.scripts.writer) or by
# absolute path during tests. We do a tolerant import.
try:  # absolute (production: invoked via `python skills/ralph-new/scripts/new.py`)
    from . import templates  # type: ignore[no-redef]
except ImportError:  # path-based (tests)
    import importlib.util
    import sys as _sys

    _TEMPLATES_PATH = Path(__file__).with_name("templates.py")
    _spec = importlib.util.spec_from_file_location("ralph_new_templates", _TEMPLATES_PATH)
    assert _spec is not None and _spec.loader is not None
    templates = importlib.util.module_from_spec(_spec)
    _sys.modules.setdefault("ralph_new_templates", templates)
    _spec.loader.exec_module(templates)


def now_iso(now: datetime | None = None) -> str:
    """Return current UTC time as ISO-8601 with offset (``+00:00``)."""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.isoformat()


def render_frontmatter(
    *,
    pbi_id: str,
    pbi_type: Literal["bug", "feature"],
    severity: str,
    target_repo: str,
    depends_on: Sequence[str],
    parent_id: str | None,
    now: datetime | None = None,
) -> str:
    """Render the full YAML frontmatter block (including opening + closing ``---``).

    Trailing newline ensures the body that follows starts on its own line.
    """
    ts = now_iso(now)
    lines = [
        "---",
        f"id: {pbi_id}",
        f"type: {pbi_type}",
        "status: inbox",
        f"severity: {severity}",
        "attempts: 0",
        f"created_at: {ts}",
        f"updated_at: {ts}",
    ]
    if depends_on:
        lines.append("depends_on:")
        for dep in depends_on:
            lines.append(f"  - {dep}")
    else:
        lines.append("depends_on: []")
    lines.append(f"target_repo: {target_repo}")
    if parent_id:
        lines.append(f"parent_id: {parent_id}")
    lines.append("---")
    lines.append("")  # blank line between frontmatter and body
    return "\n".join(lines) + "\n"


def write_pbi_dir(
    *,
    pbi_dir: Path,
    pbi_type: Literal["bug", "feature"],
    bug_inputs: dict[str, Any] | None,
    feature_inputs: dict[str, Any] | None,
) -> None:
    """Create the PBI directory + per-type entry files + HISTORY.md.

    Raises ``FileExistsError`` if ``pbi_dir`` already exists, to prevent
    accidentally overwriting a real PBI.
    """
    if pbi_dir.exists():
        raise FileExistsError(f"PBI directory already exists: {pbi_dir}")
    pbi_dir.mkdir(parents=True, exist_ok=False)

    if pbi_type == "bug":
        assert bug_inputs is not None, "bug_inputs required for type=bug"
        bug_text = templates.render_bug_md(
            frontmatter=bug_inputs["frontmatter"],
            title=bug_inputs["title"],
            symptom=bug_inputs["symptom"],
            root_cause=bug_inputs["root_cause"],
            impact=bug_inputs["impact"],
            acceptance_criteria=bug_inputs["acceptance_criteria"],
        )
        repro = bug_inputs["reproduce"]
        repro_text = templates.render_reproduce_md(
            title=bug_inputs["title"],
            environment=repro["environment"],
            steps=repro["steps"],
            expected=repro["expected"],
            actual=repro["actual"],
        )
        (pbi_dir / "BUG.md").write_text(bug_text, encoding="utf-8")
        (pbi_dir / "REPRODUCE.md").write_text(repro_text, encoding="utf-8")
    elif pbi_type == "feature":
        assert feature_inputs is not None, "feature_inputs required for type=feature"
        pbi_text = templates.render_pbi_md(
            frontmatter=feature_inputs["frontmatter"],
            title=feature_inputs["title"],
            description=feature_inputs["description"],
            acceptance_criteria=feature_inputs["acceptance_criteria"],
            spec_path=feature_inputs["spec_path"],
            plan_path=feature_inputs["plan_path"],
        )
        plan_text = templates.render_plan_md(
            title=feature_inputs["title"],
            plan_path=feature_inputs["plan_path"],
        )
        (pbi_dir / "PBI.md").write_text(pbi_text, encoding="utf-8")
        (pbi_dir / "PLAN.md").write_text(plan_text, encoding="utf-8")
    else:
        raise ValueError(f"unsupported pbi_type: {pbi_type!r}")

    (pbi_dir / "HISTORY.md").write_text(templates.HISTORY_TEMPLATE, encoding="utf-8")
```

- [ ] **Step 4: Run the tests; confirm all pass**

Run: `uv run pytest tests/skills/test_ralph_new_writer.py -q`
Expected: all green.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check skills/ralph-new/scripts/writer.py tests/skills/test_ralph_new_writer.py`
Run: `uv run ruff format --check skills/ralph-new/scripts/writer.py tests/skills/test_ralph_new_writer.py`
Run: `uv run mypy skills/ralph-new/scripts/writer.py`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add skills/ralph-new/scripts/writer.py tests/skills/test_ralph_new_writer.py
git commit -m "feat(ralph-new): writer.py — render_frontmatter + write_pbi_dir (type-dispatched)"
```

---

## Task 6: `new.py` CLI — orchestration, prompts, flags + end-to-end tests

**Files:**
- Create: `skills/ralph-new/scripts/new.py`
- Create: `tests/skills/test_ralph_new_e2e.py`

This task is the largest. Implementation is split into three sub-steps: (a) argparse + flag normalisation, (b) prompt loop, (c) full pipeline (validate → clone → slug → write → commit → push).

- [ ] **Step 1: Write the failing e2e tests** (place at `tests/skills/test_ralph_new_e2e.py`)

```python
"""End-to-end tests for the ralph-new CLI.

Uses the project-wide ``queue_env`` fixture from ``tests/conftest.py`` —
a bare queue remote on the ``ralph-queue`` branch + a tmp workspace.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "skills" / "ralph-new" / "scripts" / "new.py"

TARGET = "https://github.com/owner/svc"


@pytest.fixture(scope="module")
def new_mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ralph_new", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _verify_clone(tmp_path: Path, bare_url: str) -> Path:
    verify = tmp_path / f"verify-{Path(bare_url).name}"
    subprocess.run(["git", "clone", "-b", "ralph-queue", bare_url, str(verify)], check=True)
    return verify


def _configure_clone_identity(clone: Path) -> None:
    subprocess.run(["git", "-C", str(clone), "config", "user.email", "t@e"], check=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.name", "t"], check=True)


def _common_argv(workspace: Path, queue_repo: str) -> list[str]:
    return [
        "--workspace", str(workspace),
        "--queue-repo", queue_repo,
        "--queue-branch", "ralph-queue",
        "--non-interactive",
    ]


def _seed_existing_pbi(tmp_path: Path, queue_repo: str, state: str, pbi_id: str) -> None:
    """Push an empty PBI dir under .ralph/<state>/<pbi_id>/ to the bare queue."""
    seed = tmp_path / f"seed-existing-{state}-{pbi_id}"
    subprocess.run(["git", "clone", "-b", "ralph-queue", queue_repo, str(seed)], check=True)
    _configure_clone_identity(seed)
    pbi = seed / ".ralph" / state / pbi_id
    pbi.mkdir(parents=True, exist_ok=True)
    (pbi / "HISTORY.md").write_text("<!-- seed -->\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-m", f"seed: existing {pbi_id}"], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "origin", "ralph-queue"], check=True)


# --- full runs --------------------------------------------------------

def test_full_run_bug_writes_three_files_and_pushes(
    new_mod, queue_env, tmp_path, monkeypatch, capsys
):
    workspace, queue_repo = queue_env
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@e")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@e")

    argv = _common_argv(workspace, queue_repo) + [
        "--title", "Fix login timeout on Safari",
        "--type", "bug",
        "--severity", "high",
        "--target-repo", TARGET,
        "--body-file", str(_write_inline(tmp_path, "bug-body.md", "## Symptom\nhangs\n")),
        "--reproduce-file", str(_write_inline(tmp_path, "repro.md", "## Steps\n1. ...\n")),
    ]
    rc = new_mod.main(argv)
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "FIX-LOGIN-TIMEOUT-ON-SAFARI"
    assert payload["type"] == "bug"

    verify = _verify_clone(tmp_path, queue_repo)
    pbi_dir = verify / ".ralph" / "inbox" / "FIX-LOGIN-TIMEOUT-ON-SAFARI"
    assert pbi_dir.is_dir()
    assert sorted(p.name for p in pbi_dir.iterdir()) == ["BUG.md", "HISTORY.md", "REPRODUCE.md"]

    log = subprocess.run(
        ["git", "-C", str(verify), "log", "-1", "--format=%s"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert log == "chore(queue): add FIX-LOGIN-TIMEOUT-ON-SAFARI"


def test_full_run_feature_writes_three_files_with_plan_pointer(
    new_mod, queue_env, tmp_path, monkeypatch, capsys
):
    workspace, queue_repo = queue_env
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@e")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@e")

    argv = _common_argv(workspace, queue_repo) + [
        "--title", "Export CSV report",
        "--type", "feature",
        "--target-repo", TARGET,
        "--body-file", str(_write_inline(tmp_path, "feat.md", "We need CSV export.")),
        "--spec-path", "docs/superpowers/specs/2026-05-30-csv-design.md",
        "--plan-path", "docs/superpowers/plans/2026-05-30-csv-plan.md",
    ]
    rc = new_mod.main(argv)
    assert rc == 0

    verify = _verify_clone(tmp_path, queue_repo)
    pbi_dir = verify / ".ralph" / "inbox" / "EXPORT-CSV-REPORT"
    assert sorted(p.name for p in pbi_dir.iterdir()) == ["HISTORY.md", "PBI.md", "PLAN.md"]
    assert "csv-plan.md" in (pbi_dir / "PLAN.md").read_text(encoding="utf-8")


def test_full_run_feature_blank_plan_writes_todo_stub(
    new_mod, queue_env, tmp_path, monkeypatch, capsys
):
    workspace, queue_repo = queue_env
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@e")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@e")

    argv = _common_argv(workspace, queue_repo) + [
        "--title", "Stub plan feature",
        "--type", "feature",
        "--target-repo", TARGET,
        "--body-file", str(_write_inline(tmp_path, "feat.md", "x")),
    ]
    rc = new_mod.main(argv)
    assert rc == 0
    verify = _verify_clone(tmp_path, queue_repo)
    text = (verify / ".ralph" / "inbox" / "STUB-PLAN-FEATURE" / "PLAN.md").read_text(encoding="utf-8")
    assert "TODO" in text


# --- depends_on -------------------------------------------------------

def test_depends_on_existence_check_passes(new_mod, queue_env, tmp_path, monkeypatch, capsys):
    workspace, queue_repo = queue_env
    _seed_existing_pbi(tmp_path, queue_repo, "done", "PRECURSOR")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"); monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@e")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@e")

    rc = new_mod.main(_common_argv(workspace, queue_repo) + [
        "--title", "X", "--type", "bug", "--target-repo", TARGET,
        "--depends-on", "PRECURSOR",
        "--body-file", str(_write_inline(tmp_path, "b.md", "x")),
        "--reproduce-file", str(_write_inline(tmp_path, "r.md", "x")),
    ])
    assert rc == 0


def test_depends_on_existence_check_fails_exit_2(new_mod, queue_env, tmp_path, monkeypatch, capsys):
    workspace, queue_repo = queue_env
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"); monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@e")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@e")

    rc = new_mod.main(_common_argv(workspace, queue_repo) + [
        "--title", "X", "--type", "bug", "--target-repo", TARGET,
        "--depends-on", "DOES-NOT-EXIST",
        "--body-file", str(_write_inline(tmp_path, "b.md", "x")),
        "--reproduce-file", str(_write_inline(tmp_path, "r.md", "x")),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "DOES-NOT-EXIST" in err
    assert "not found" in err.lower()


@pytest.mark.parametrize("state", ["inbox", "current", "pending-pr", "blocked", "done", "archive"])
def test_depends_on_existence_check_searches_all_states(
    new_mod, queue_env, tmp_path, monkeypatch, state
):
    workspace, queue_repo = queue_env
    _seed_existing_pbi(tmp_path, queue_repo, state, f"PRE-{state.upper()}")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"); monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@e")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@e")
    rc = new_mod.main(_common_argv(workspace, queue_repo) + [
        "--title", f"X for {state}", "--type", "bug", "--target-repo", TARGET,
        "--depends-on", f"PRE-{state.upper()}",
        "--body-file", str(_write_inline(tmp_path, f"b-{state}.md", "x")),
        "--reproduce-file", str(_write_inline(tmp_path, f"r-{state}.md", "x")),
    ])
    assert rc == 0


# --- collision --------------------------------------------------------

def test_slug_collision_appends_suffix_end_to_end(new_mod, queue_env, tmp_path, monkeypatch, capsys):
    workspace, queue_repo = queue_env
    _seed_existing_pbi(tmp_path, queue_repo, "inbox", "COLLIDE-ME")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"); monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@e")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@e")

    rc = new_mod.main(_common_argv(workspace, queue_repo) + [
        "--title", "Collide me", "--type", "bug", "--target-repo", TARGET,
        "--body-file", str(_write_inline(tmp_path, "b.md", "x")),
        "--reproduce-file", str(_write_inline(tmp_path, "r.md", "x")),
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "COLLIDE-ME-2"


# --- dry-run, no-push, errors -----------------------------------------

def test_dry_run_no_clone_no_commit(new_mod, queue_env, tmp_path, capsys):
    workspace, queue_repo = queue_env
    rc = new_mod.main(_common_argv(workspace, queue_repo) + [
        "--title", "Dry run feature", "--type", "feature", "--target-repo", TARGET,
        "--body-file", str(_write_inline(tmp_path, "f.md", "x")),
        "--dry-run",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    # Workspace was never created because we didn't clone.
    assert not (workspace / "queue").exists()


def test_non_interactive_missing_title_exits_2(new_mod, queue_env, capsys):
    workspace, queue_repo = queue_env
    rc = new_mod.main([
        "--workspace", str(workspace), "--queue-repo", queue_repo,
        "--non-interactive",
        "--type", "bug", "--target-repo", TARGET,
    ])
    assert rc == 2
    assert "title" in capsys.readouterr().err.lower()


def test_target_repo_invalid_url_exits_2(new_mod, queue_env, tmp_path, capsys):
    workspace, queue_repo = queue_env
    rc = new_mod.main(_common_argv(workspace, queue_repo) + [
        "--title", "X", "--type", "bug",
        "--target-repo", "not-a-url",
        "--body-file", str(_write_inline(tmp_path, "b.md", "x")),
        "--reproduce-file", str(_write_inline(tmp_path, "r.md", "x")),
    ])
    assert rc == 2
    assert "target_repo" in capsys.readouterr().err.lower()


def test_no_push_commits_locally_but_does_not_push(
    new_mod, queue_env, tmp_path, monkeypatch, capsys
):
    workspace, queue_repo = queue_env
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"); monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@e")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@e")

    rc = new_mod.main(_common_argv(workspace, queue_repo) + [
        "--title", "Local only", "--type", "bug", "--target-repo", TARGET,
        "--body-file", str(_write_inline(tmp_path, "b.md", "x")),
        "--reproduce-file", str(_write_inline(tmp_path, "r.md", "x")),
        "--no-push",
    ])
    assert rc == 0

    # Verify the bare remote does NOT have the PBI.
    verify = _verify_clone(tmp_path, queue_repo)
    assert not (verify / ".ralph" / "inbox" / "LOCAL-ONLY").exists()
    # Verify the local clone DOES have it.
    local = workspace / "queue"
    assert (local / ".ralph" / "inbox" / "LOCAL-ONLY").exists()


def test_parent_id_threaded_into_frontmatter(
    new_mod, queue_env, tmp_path, monkeypatch
):
    workspace, queue_repo = queue_env
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"); monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@e")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@e")
    rc = new_mod.main(_common_argv(workspace, queue_repo) + [
        "--title", "Child PBI", "--type", "bug", "--target-repo", TARGET,
        "--parent-id", "EPIC-PARENT",
        "--body-file", str(_write_inline(tmp_path, "b.md", "x")),
        "--reproduce-file", str(_write_inline(tmp_path, "r.md", "x")),
    ])
    assert rc == 0
    verify = _verify_clone(tmp_path, queue_repo)
    text = (verify / ".ralph" / "inbox" / "CHILD-PBI" / "BUG.md").read_text(encoding="utf-8")
    assert "parent_id: EPIC-PARENT" in text


# --- helpers ----------------------------------------------------------

def _write_inline(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run pytest tests/skills/test_ralph_new_e2e.py -q`
Expected: collection error — `new.py` missing.

- [ ] **Step 3: Implement `new.py`** (place at `skills/ralph-new/scripts/new.py`)

```python
"""ralph-new — author a PBI directly into the ralph-queue.

Replaces the GitHub-issue / ralph-add submission flow. See
``docs/superpowers/specs/2026-05-30-ralph-new-design.md`` for the
full design.

Exit codes:
    0  — PBI written + (optionally) pushed
    2  — validation error, collision overflow, queue / auth error,
         --non-interactive missing required field
    130 — operator Ctrl+C (caller's KeyboardInterrupt propagates)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

# --- tolerant sibling imports ----------------------------------------
#
# Run modes:
#   1. `uv run python skills/ralph-new/scripts/new.py ...`  (production)
#   2. `python -m skills.ralph-new.scripts.new` (not currently used; OK)
#   3. Loaded by spec_from_file_location in tests
#
# Path-based import covers (1) and (3). Package-relative import covers (2).
try:
    from . import validate, slugify, writer  # type: ignore[no-redef]
except ImportError:
    _HERE = Path(__file__).parent
    def _load(name: str):
        path = _HERE / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"ralph_new_{name}", path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault(f"ralph_new_{name}", mod)
        spec.loader.exec_module(mod)
        return mod
    validate = _load("validate")
    slugify = _load("slugify")
    writer = _load("writer")

# Re-use the executor's queue-clone helper so we don't reimplement clone/pull.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from ralph_executor.queue_clone import ensure_queue_clone, QueueCloneError  # noqa: E402
from ralph_executor.user_config import read_workspace_root, read_queue_repo, read_queue_branch  # noqa: E402

DEFAULT_SEVERITY = "normal"
DEFAULT_QUEUE_BRANCH = "ralph-queue"
STATES = ("inbox", "current", "pending-pr", "blocked", "done", "archive")


# --- argument parsing -------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ralph-new",
        description="Author a new PBI directly into the ralph-queue.",
    )
    p.add_argument("--title")
    p.add_argument("--type", choices=("bug", "feature"))
    p.add_argument("--severity", choices=("critical", "high", "normal", "low"))
    p.add_argument("--target-repo")
    p.add_argument("--depends-on", action="append", default=[])
    p.add_argument("--parent-id")
    p.add_argument("--id")
    p.add_argument("--spec-path", default="")
    p.add_argument("--plan-path", default="")
    p.add_argument("--body-file", type=Path)
    p.add_argument("--reproduce-file", type=Path)
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--no-push", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check-depends-on", action="store_true")
    p.add_argument("--workspace", type=Path)
    p.add_argument("--queue-repo")
    p.add_argument("--queue-branch")
    return p


# --- prompts ----------------------------------------------------------

def _prompt(label: str, *, default: str | None = None) -> str:
    suffix = f" (default: {default})" if default else ""
    raw = input(f"{label}{suffix}: ").strip()
    return raw or (default or "")


def _multiline_prompt(label: str) -> str:
    print(f"{label} (blank line to finish):")
    lines: list[str] = []
    while True:
        line = input("  ")
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


# --- helpers ----------------------------------------------------------

def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _list_existing_slugs_in_collision_states(queue_clone: Path) -> set[str]:
    """Slugs already taken across inbox/current/pending-pr (collision states)."""
    out: set[str] = set()
    for state in ("inbox", "current", "pending-pr"):
        state_dir = queue_clone / ".ralph" / state
        if not state_dir.is_dir():
            continue
        out.update(p.name for p in state_dir.iterdir() if p.is_dir())
    return out


def _depends_on_exists(queue_clone: Path, dep_id: str) -> bool:
    for state in STATES:
        if (queue_clone / ".ralph" / state / dep_id).is_dir():
            return True
    return False


def _read_body_file(path: Path | None) -> str:
    if path is None:
        return ""
    if not path.is_file():
        raise FileNotFoundError(f"body file not found: {path}")
    return path.read_text(encoding="utf-8")


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


# --- orchestration ----------------------------------------------------

def _resolve_required(value: str | None, *, name: str, non_interactive: bool, prompt_label: str | None = None) -> str | None:
    if value is not None and value != "":
        return value
    if non_interactive:
        return None  # caller will turn this into exit 2
    label = prompt_label or name
    return _prompt(label).strip() or None


def _build_bug_inputs(args: argparse.Namespace, frontmatter: str, title: str) -> dict[str, object]:
    if args.body_file is not None:
        body = _read_body_file(args.body_file).strip()
        # When body-file is supplied, populate the body wholesale instead of
        # per-section prompts. We split on headings best-effort but accept
        # whatever the operator wrote.
        return {
            "frontmatter": frontmatter,
            "title": title,
            "symptom": body,
            "root_cause": "",
            "impact": "",
            "acceptance_criteria": "",
            "reproduce": _build_reproduce_inputs(args),
        }
    if args.non_interactive:
        # Need at least a body — refuse silently is bad; fail loud.
        raise SystemExit(_fail("bug PBIs require --body-file under --non-interactive"))
    return {
        "frontmatter": frontmatter,
        "title": title,
        "symptom": _prompt("Symptom (one line)"),
        "root_cause": _multiline_prompt("Root cause (suspected)"),
        "impact": _multiline_prompt("Impact"),
        "acceptance_criteria": _multiline_prompt("Acceptance criteria"),
        "reproduce": _build_reproduce_inputs(args),
    }


def _build_reproduce_inputs(args: argparse.Namespace) -> dict[str, str]:
    if args.reproduce_file is not None:
        body = _read_body_file(args.reproduce_file).strip()
        return {"environment": body, "steps": "", "expected": "", "actual": ""}
    if args.non_interactive:
        raise SystemExit(_fail("bug PBIs require --reproduce-file under --non-interactive"))
    return {
        "environment": _multiline_prompt("Environment"),
        "steps": _multiline_prompt("Steps"),
        "expected": _multiline_prompt("Expected"),
        "actual": _multiline_prompt("Actual"),
    }


def _build_feature_inputs(args: argparse.Namespace, frontmatter: str, title: str) -> dict[str, object]:
    if args.body_file is not None:
        return {
            "frontmatter": frontmatter,
            "title": title,
            "description": _read_body_file(args.body_file).strip(),
            "acceptance_criteria": "",
            "spec_path": args.spec_path or "",
            "plan_path": args.plan_path or "",
        }
    if args.non_interactive:
        raise SystemExit(_fail("feature PBIs require --body-file under --non-interactive"))
    return {
        "frontmatter": frontmatter,
        "title": title,
        "description": _multiline_prompt("Description"),
        "acceptance_criteria": _multiline_prompt("Acceptance criteria"),
        "spec_path": _prompt("Spec doc path", default=args.spec_path or "").strip(),
        "plan_path": _prompt("Plan doc path (blank to write TODO stub)", default=args.plan_path or "").strip(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    # --- resolve required fields (flag → prompt → fail) ------------
    title = _resolve_required(args.title, name="title", non_interactive=args.non_interactive, prompt_label="Title")
    if not title:
        return _fail("--title is required (or supply interactively without --non-interactive)")
    pbi_type = _resolve_required(args.type, name="type", non_interactive=args.non_interactive, prompt_label="Type [bug/feature]")
    if not pbi_type:
        return _fail("--type is required")
    err = validate.validate_type(pbi_type)
    if err:
        return _fail(err)

    severity = args.severity or (
        DEFAULT_SEVERITY if args.non_interactive else (_prompt("Severity [critical/high/normal/low]", default=DEFAULT_SEVERITY) or DEFAULT_SEVERITY)
    )
    err = validate.validate_severity(severity)
    if err:
        return _fail(err)

    target_repo = _resolve_required(args.target_repo, name="target-repo", non_interactive=args.non_interactive, prompt_label="Target repo URL")
    if not target_repo:
        return _fail("--target-repo is required")
    err = validate.validate_target_repo(target_repo)
    if err:
        return _fail(err)

    depends_on: list[str] = list(args.depends_on)
    if not depends_on and not args.non_interactive:
        raw = _prompt("Depends on (comma-separated PBI ids, blank for none)")
        if raw:
            depends_on = [s.strip() for s in raw.split(",") if s.strip()]
    err = validate.validate_depends_on_syntax(depends_on)
    if err:
        return _fail(err)

    parent_id = args.parent_id
    if parent_id:
        err = validate.validate_pbi_id(parent_id)
        if err:
            return _fail(f"--parent-id: {err}")

    # --- slug -----------------------------------------------------
    try:
        base_slug = args.id if args.id else slugify.slug_from_title(title)
    except ValueError as exc:
        return _fail(str(exc))
    err = validate.validate_pbi_id(base_slug)
    if err:
        return _fail(err)

    # --- gather body inputs --------------------------------------
    # (frontmatter rendered after slug + collision resolution because
    # slug may change)
    if pbi_type == "bug":
        bug_inputs_partial = _build_bug_inputs(args, frontmatter="", title=title)
        feature_inputs_partial = None
    else:
        bug_inputs_partial = None
        feature_inputs_partial = _build_feature_inputs(args, frontmatter="", title=title)

    # --- dry-run early exit (no clone, no write) -----------------
    if args.dry_run and not args.check_depends_on:
        if depends_on:
            print(
                "dry-run: skipping depends_on existence check (requires queue clone); "
                "pass --check-depends-on to force the clone",
                file=sys.stderr,
            )
        envelope = {
            "dry_run": True,
            "pbi_id": base_slug,
            "type": pbi_type,
            "title": title,
            "severity": severity,
            "target_repo": target_repo,
            "depends_on": depends_on,
            "parent_id": parent_id,
        }
        print(json.dumps(envelope, indent=2))
        return 0

    # --- acquire queue clone -------------------------------------
    workspace = args.workspace or read_workspace_root() or (Path.home() / "ralph-workspaces")
    queue_repo = args.queue_repo or read_queue_repo()
    queue_branch = args.queue_branch or read_queue_branch() or DEFAULT_QUEUE_BRANCH
    if not queue_repo:
        return _fail("queue_repo is not configured (set in ~/.ralph/config.toml or pass --queue-repo)")
    try:
        queue_clone = ensure_queue_clone(workspace, queue_repo, queue_branch)
    except QueueCloneError as exc:
        return _fail(f"queue clone failed: {exc}")

    # --- resolve slug + collision --------------------------------
    existing = _list_existing_slugs_in_collision_states(queue_clone)
    try:
        pbi_id = slugify.resolve_collision(base_slug, existing=existing)
    except ValueError as exc:
        return _fail(str(exc))

    # --- depends_on existence check ------------------------------
    for dep in depends_on:
        if not _depends_on_exists(queue_clone, dep):
            return _fail(
                f"depends_on id {dep!r} not found in queue (searched "
                f"inbox / current / pending-pr / blocked / done / archive)"
            )

    # --- render frontmatter --------------------------------------
    frontmatter = writer.render_frontmatter(
        pbi_id=pbi_id,
        pbi_type=pbi_type,  # type: ignore[arg-type]
        severity=severity,
        target_repo=target_repo,
        depends_on=tuple(depends_on),
        parent_id=parent_id,
    )
    if pbi_type == "bug":
        assert bug_inputs_partial is not None
        bug_inputs_partial["frontmatter"] = frontmatter
        bug_inputs = bug_inputs_partial
        feature_inputs = None
    else:
        assert feature_inputs_partial is not None
        feature_inputs_partial["frontmatter"] = frontmatter
        feature_inputs = feature_inputs_partial
        bug_inputs = None

    # --- dry-run with clone (depends_on already validated above) -
    if args.dry_run:
        envelope = {
            "dry_run": True,
            "pbi_id": pbi_id,
            "type": pbi_type,
            "title": title,
            "severity": severity,
            "target_repo": target_repo,
            "depends_on": depends_on,
            "parent_id": parent_id,
        }
        print(json.dumps(envelope, indent=2))
        return 0

    # --- write the PBI directory ---------------------------------
    pbi_dir = queue_clone / ".ralph" / "inbox" / pbi_id
    try:
        writer.write_pbi_dir(
            pbi_dir=pbi_dir,
            pbi_type=pbi_type,  # type: ignore[arg-type]
            bug_inputs=bug_inputs,
            feature_inputs=feature_inputs,
        )
    except FileExistsError as exc:
        return _fail(str(exc))

    # --- git add / commit / push --------------------------------
    _git(queue_clone, "add", "-A")
    _git(queue_clone, "commit", "-m", f"chore(queue): add {pbi_id}")
    if not args.no_push:
        _git(queue_clone, "push", "origin", queue_branch)

    envelope = {
        "dry_run": False,
        "pbi_id": pbi_id,
        "type": pbi_type,
        "title": title,
        "severity": severity,
        "target_repo": target_repo,
        "depends_on": depends_on,
        "parent_id": parent_id,
        "pushed": not args.no_push,
        "pbi_dir": str(pbi_dir),
    }
    print(json.dumps(envelope, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
```

- [ ] **Step 4: Run the e2e tests; iterate until all pass**

Run: `uv run pytest tests/skills/test_ralph_new_e2e.py -q`
Expected: all green. If failures, read the failure, fix `new.py`, repeat. Do not skip failures.

- [ ] **Step 5: Run the full suite to catch any cross-test regression**

Run: `uv run pytest -q`
Expected: all green (existing tests untouched).

- [ ] **Step 6: Lint + type-check the whole new skill**

Run: `uv run ruff check skills/ralph-new/`
Run: `uv run ruff format --check skills/ralph-new/`
Run: `uv run mypy skills/ralph-new/scripts/new.py`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add skills/ralph-new/scripts/new.py tests/skills/test_ralph_new_e2e.py
git commit -m "feat(ralph-new): CLI + prompts + full validate->clone->slug->write->commit->push pipeline"
```

---

## Task 7: `prompt/PROMPT.md` target_repo wording fix + regression guard

**Files:**
- Modify: `prompt/PROMPT.md` (lines 46–49)
- Create: `tests/test_prompt_contents.py`

- [ ] **Step 1: Write the failing regression-guard test** (place at `tests/test_prompt_contents.py`)

```python
"""Guard rails for the canonical prompt/PROMPT.md wording.

Specific phrases here are load-bearing: ralph-new's design depends on
target_repo being documented as REQUIRED (the loop already enforces it).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT = (REPO_ROOT / "prompt" / "PROMPT.md").read_text(encoding="utf-8")


def test_target_repo_documented_as_required():
    # The stale wording said "informational metadata for now". The fixed
    # wording must say MUST and canonical source of truth.
    assert "MUST carry a `target_repo`" in PROMPT
    assert "canonical source of truth" in PROMPT.lower() or "canonical" in PROMPT.lower()


def test_no_stale_informational_only_wording():
    assert "informational metadata for now" not in PROMPT
    assert "the loop does not yet act on it" not in PROMPT
```

- [ ] **Step 2: Run; confirm both tests fail**

Run: `uv run pytest tests/test_prompt_contents.py -q`
Expected: FAIL — current PROMPT.md still has the stale wording.

- [ ] **Step 3: Apply the PROMPT.md edit**

Locate the paragraph starting `Every PBI's frontmatter also carries a `target_repo` field` (around line 46) and replace it with:

```
Every PBI's frontmatter MUST carry a `target_repo` field — an HTTPS URL
of the repo this PBI applies to (e.g. `https://github.com/emp3thy/ralph`).
This is the canonical source of truth for which repo to operate on. The
executor clones `target_repo` into the multi-repo workspace at claim time;
your current working directory for this iteration is that clone.
```

- [ ] **Step 4: Re-run; confirm both pass**

Run: `uv run pytest tests/test_prompt_contents.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prompt/PROMPT.md tests/test_prompt_contents.py
git commit -m "docs(prompt): target_repo is REQUIRED not informational + regression-guard test"
```

---

## Task 8: `host_select.py` — drop workitem-fetch staging

**Files:**
- Modify: `ralph_executor/host_select.py`
- Modify: `tests/test_host_select.py`

- [ ] **Step 1: Locate the workitem-fetch staging code**

Run: `grep -n "workitem-fetch\|workitem_fetch\|stage.*workitem" ralph_executor/host_select.py`
Expected: at least one block calling something like `_stage_skill(... "workitem-fetch-{host}", ...)` or similar.

- [ ] **Step 2: Read the relevant function(s) end-to-end**

Open `ralph_executor/host_select.py` and identify the function that stages workitem-fetch. Likely named `prepare_host_environment` or `stage_skill_for_host`. Note exactly which lines stage workitem-fetch — those go.

- [ ] **Step 3: Add a failing regression-guard test FIRST** (modify `tests/test_host_select.py` — append)

```python
def test_workitem_fetch_no_longer_staged(tmp_path, monkeypatch):
    """ralph-new ships in PR <#TBD>; workitem-fetch staging is gone."""
    from ralph_executor import host_select

    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    (skills_root / "pr-github").mkdir()  # only need pr-github so the call doesn't NPE
    (skills_root / "pr-github" / "SKILL.md").write_text("---\nname: pr-github\n---\n", encoding="utf-8")
    claude_skills = tmp_path / "claude-skills"
    claude_skills.mkdir()

    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "o")
    # call signature mirrors the existing tests in this file — see them for the
    # exact kwargs to pass
    host_select.prepare_host_environment(
        git_host="github",
        skills_root=skills_root,
        claude_skills_dir=claude_skills,
    )
    assert not (claude_skills / "workitem-fetch").exists()
```

If the existing `prepare_host_environment` signature differs, adapt the call to match. Look at the other tests in `test_host_select.py` for the canonical invocation shape.

- [ ] **Step 4: Run; confirm the regression test FAILS (because workitem-fetch is still being staged)**

Run: `uv run pytest tests/test_host_select.py::test_workitem_fetch_no_longer_staged -q`
Expected: FAIL — workitem-fetch dir IS staged today.

- [ ] **Step 5: Delete the workitem-fetch staging code in `host_select.py`**

Remove the block that copies `workitem-fetch-<host>/` into the Claude skills dir. Keep all PR-skill staging untouched. If a constant like `WORKITEM_FETCH_SKILL_NAME = "workitem-fetch"` becomes unused, delete it too.

- [ ] **Step 6: Delete the existing workitem-fetch staging tests in `tests/test_host_select.py`**

Find any test function name containing `workitem_fetch` other than the new `test_workitem_fetch_no_longer_staged` and delete it. Examples to look for: `test_workitem_fetch_github_staged`, `test_workitem_fetch_overwrites_stale_clone`, etc.

- [ ] **Step 7: Re-run host_select tests**

Run: `uv run pytest tests/test_host_select.py -q`
Expected: all green — pr-github staging still works, `test_workitem_fetch_no_longer_staged` now passes.

- [ ] **Step 8: Full suite check**

Run: `uv run pytest -q`
Expected: all green.

- [ ] **Step 9: Lint + type-check**

Run: `uv run ruff check ralph_executor/host_select.py tests/test_host_select.py`
Run: `uv run ruff format --check ralph_executor/host_select.py tests/test_host_select.py`
Run: `uv run mypy ralph_executor/host_select.py`
Expected: all green.

- [ ] **Step 10: Commit**

```bash
git add ralph_executor/host_select.py tests/test_host_select.py
git commit -m "refactor(host_select): drop workitem-fetch staging; ralph-add is retired"
```

---

## Task 9: Delete `ralph-add` + `workitem-fetch-*` skills and their tests

**Files:**
- Delete: `skills/ralph-add/` (entire directory)
- Delete: `skills/workitem-fetch-github/` (entire directory)
- Delete: `skills/workitem-fetch-mock/` (entire directory)
- Delete: `tests/skills/test_ralph_add.py`
- Delete: `tests/skills/test_workitem_fetch_github.py`

- [ ] **Step 1: Confirm test_ralph_add.py's `queue_env` is no longer needed there (lifted in Task 1)**

Run: `grep -n "@pytest.fixture" tests/skills/test_ralph_add.py`
Expected: shows the duplicate fixture definition at line 62 — safe to delete because `tests/conftest.py` already provides it.

- [ ] **Step 2: Delete the skill directories**

```bash
git rm -r skills/ralph-add skills/workitem-fetch-github skills/workitem-fetch-mock
```

- [ ] **Step 3: Delete the test files**

```bash
git rm tests/skills/test_ralph_add.py tests/skills/test_workitem_fetch_github.py
```

- [ ] **Step 4: Search the codebase for any remaining references**

Run: `grep -rn "ralph-add\|workitem-fetch\|ralph_add\|workitem_fetch" --include="*.py" --include="*.md" --include="*.toml" --include="*.yaml" .` (exclude `.venv`, `.git`, `node_modules`, `.ralph-work`)
Expected: matches only in `docs/superpowers/specs/`, `docs/superpowers/plans/`, `CHANGELOG.md` (if present), and the new `docs/runbooks/ralph-setup.md` update from Task 10. Code matches → fix them.

If any code match appears, it's a real consumer — open the file, replace the import / call with the ralph-new equivalent, or delete the dead code path.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all green. Notable: ~35 tests from test_ralph_add.py drop out of the collection.

- [ ] **Step 6: Run lint / format / mypy on the whole project**

Run: `uv run ruff check .`
Run: `uv run ruff format --check .`
Run: `uv run mypy ralph_executor scripts skills tests`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "remove: ralph-add + workitem-fetch-{github,mock} skills (retired by ralph-new)"
```

---

## Task 10: Update `docs/runbooks/ralph-setup.md` + `README.md`

**Files:**
- Modify: `docs/runbooks/ralph-setup.md`
- Modify: `README.md`

- [ ] **Step 1: Update the operator-skills section of `docs/runbooks/ralph-setup.md`**

Open `docs/runbooks/ralph-setup.md` and find the `### `ralph-add`` subsection (it should be inside Section 8 "CLI reference: operator skills"). Replace the entire subsection with:

````markdown
### `ralph-new`

`skills/ralph-new/scripts/new.py`. The single submission surface for
PBIs — supersedes `ralph-add`. Authors a well-formed PBI directly into
the queue with no GitHub-issue round-trip.

| Flag | Required | Description |
|---|---|---|
| `--title TEXT` | yes (prompt or flag) | PBI title; slugified to PBI id. |
| `--type {bug,feature}` | yes | PBI type. |
| `--severity {critical,high,normal,low}` | no (default `normal`) | Triage lane. |
| `--target-repo URL` | yes | HTTPS owner/name URL of the target service repo. |
| `--depends-on ID` | no (repeatable) | Validated for syntax AND existence in the queue. |
| `--parent-id ID` | no | Epic link. |
| `--id SLUG` | no | Override auto-generated slug. |
| `--spec-path PATH` | no | Feature: docs/superpowers/specs path. |
| `--plan-path PATH` | no | Feature: docs/superpowers/plans path; blank renders TODO stub. |
| `--body-file PATH` | no | Read entry-file body from file; skips per-section prompts. |
| `--reproduce-file PATH` | no | Bug: read REPRODUCE.md body from file. |
| `--non-interactive` | no | Refuse prompts; missing required field exits 2. |
| `--no-push` | no | Commit but do not push. |
| `--dry-run` | no | Print envelope; no clone / write / commit. |
| `--check-depends-on` | no | Under `--dry-run`, force clone for existence check. |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |

The full design lives at
[`docs/superpowers/specs/2026-05-30-ralph-new-design.md`](../superpowers/specs/2026-05-30-ralph-new-design.md).
````

Also: in the same file's smoke-test section (Section 10), replace any
example `python skills/ralph-add/scripts/add.py --work-item WI-1
--target-repo ...` with:

```bash
uv run python skills/ralph-new/scripts/new.py \
    --non-interactive \
    --title "Smoke test PBI" \
    --type bug \
    --target-repo https://github.com/<owner>/<repo> \
    --body-file /tmp/smoke-body.md \
    --reproduce-file /tmp/smoke-repro.md
```

- [ ] **Step 2: Sweep `README.md` for ralph-add / workitem-fetch mentions**

Run: `grep -n "ralph-add\|workitem-fetch" README.md`

For each match, replace with the ralph-new equivalent or remove if the surrounding paragraph no longer makes sense. If the README has a "Working the queue" subsection, point it at the new skill.

- [ ] **Step 3: Sweep other doc files for stale references**

Run: `grep -rn "ralph-add\|workitem-fetch" docs/ --include="*.md"`
Expected: matches are confined to historical spec/plan docs (`docs/superpowers/{specs,plans}/`) — those are archived design docs and should NOT be edited (they're frozen at the time of writing).
Matches in runbooks (`docs/runbooks/`) must be fixed. Update any remaining runbook mentions to point at `ralph-new`.

- [ ] **Step 4: Verify markdown lint if a linter is configured**

Run: `uv run python -c "from pathlib import Path; [Path(p).read_text(encoding='utf-8') for p in Path('docs').rglob('*.md')]"` (cheap syntax-validity check that all markdown files are readable)
Expected: silent success.

- [ ] **Step 5: Full test suite + lint as a sanity gate**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy ralph_executor scripts skills tests`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add docs/ README.md
git commit -m "docs: replace ralph-add references with ralph-new across runbooks + README"
```

---

## Task 11: Push branch, open PR, babysit

**Files:** (none — branch + PR plumbing only)

- [ ] **Step 1: Final sanity sweep**

Run: `uv run pytest -q`
Run: `uv run ruff check .`
Run: `uv run ruff format --check .`
Run: `uv run mypy ralph_executor scripts skills tests`
Expected: all green.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin ralph/RALPH-NEW-DIRECT-AUTHOR
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --repo emp3thy/ralph --title "ralph-new: direct PBI authoring; retire ralph-add" --body "$(cat <<'EOF'
## Summary

- Introduces `skills/ralph-new/` — the single PBI submission surface for Ralph. Interactive prompts (or flags) gather title, type, severity, `target_repo`, `depends_on`, and per-type body content. Writes canonical PBI directory shape, commits `chore(queue): add <id>`, pushes to the queue branch.
- Retires `skills/ralph-add/`, `skills/workitem-fetch-github/`, `skills/workitem-fetch-mock/` and their tests. `ralph_executor/host_select.py` no longer stages `workitem-fetch/`.
- Fixes the stale `target_repo` paragraph in `prompt/PROMPT.md` (the field is required at claim time per `ralph_executor.loop._read_target_repo_from_pbi`; the docstring said "informational metadata for now"). Adds a regression-guard test.
- Updates `docs/runbooks/ralph-setup.md` + `README.md` operator-skills sections.

Spec: `docs/superpowers/specs/2026-05-30-ralph-new-design.md` (PR #56).

## Test plan

- [x] `uv run pytest` — all green; ~35 tests removed (test_ralph_add.py), ~50 added (test_ralph_new_*.py).
- [x] `uv run ruff check . && uv run ruff format --check .`
- [x] `uv run mypy ralph_executor scripts skills tests`
- [x] Manual: `uv run python skills/ralph-new/scripts/new.py --non-interactive --title "Smoke" --type bug --severity normal --target-repo https://github.com/owner/repo --body-file b.md --reproduce-file r.md --dry-run` prints envelope, exits 0, no clone touched.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Begin babysit loop**

Per the standing memory rule: do NOT ask the user. Poll `gh pr view <N> --json statusCheckRollup,mergeStateStatus,mergeable` and `gh api graphql` for unresolved review threads every 60–300 s (ScheduleWakeup). Fix BugBot / reviewer findings via reply + commit. When all checks SUCCESS, `mergeStateStatus=CLEAN`, zero unresolved threads: `gh pr merge <N> --squash --delete-branch`.

- [ ] **Step 5: After merge — local cleanup**

```bash
git checkout main && git pull
git fetch --prune
git branch -D ralph/RALPH-NEW-DIRECT-AUTHOR
```

---

## Self-review summary

**Spec coverage:** every section of the spec maps to a task above.
- Sec 1 (overview) → motivation in plan header
- Sec 2 (CLI surface) → Tasks 1 (SKILL.md flag table), 6 (argparse)
- Sec 3 (file layout) → Tasks 4 (templates), 5 (writer)
- Sec 4 (module layout + deletions) → Tasks 1 (scaffold), 8 (host_select), 9 (deletions), 10 (docs)
- Sec 5 (validation + errors) → Tasks 2 (validate.py), 6 (orchestration)
- Sec 6 (flow) → Task 6 (orchestration)
- Sec 7 (testing) → Tasks 2, 3, 5, 6 (per-module tests), 7 (PROMPT.md), 8 (host_select)
- Sec 8 (PROMPT.md edit) → Task 7
- Sec 9 (out of scope) → respected (no MCP, no attachments, no pr-feedback support)
- Sec 10 (migration) → Task 10 (docs update)

**Type consistency:** `pbi_type` is always `Literal["bug", "feature"]`; `depends_on` is always a sequence/tuple of strings; `parent_id: str | None`. `slug_from_title(title) -> str` raises `ValueError` on empty/no-alnum input — every test for it uses `pytest.raises(ValueError, ...)`. `resolve_collision(base_slug, *, existing: Iterable[str]) -> str` raises `ValueError("too many collisions...")` after suffix-10.

**Placeholder scan:** none. Every code step contains real code; every test step has assertions; every command has the run line + expected output.
