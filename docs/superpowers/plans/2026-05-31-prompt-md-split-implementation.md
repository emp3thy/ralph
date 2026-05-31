# Prompt Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `prompt/PROMPT.md` into per-topic folders under `prompt/`, add a composer that assembles per-type prompts at spawn time, and switch the executor to inject via `--append-system-prompt`.

**Architecture:** Topic folders (`prompt/0N-topic/`) hold one `.md` shared across types OR a default `.md` plus override subfolders matching `PBIType` values (`bug/`, `pr-feedback/`). A pure-function composer walks topics in alpha order, picks override-or-root per topic, concatenates. The spawn flow injects the result into Claude as a system prompt — no on-disk runtime artifact.

**Tech Stack:** Python 3.11+, pytest, dataclasses, pathlib. No new dependencies.

**Reference docs:**
- Spec: `docs/superpowers/specs/2026-05-31-prompt-md-split-design.md`
- Original PROMPT.md being split: `prompt/PROMPT.md` (460 lines as of this plan)
- PBI dataclass: `ralph_executor/types.py` (`PBI.type: PBIType = Literal["feature", "bug", "pr-feedback"]`)
- Spawn site: `ralph_executor/claude_spawn.py::_build_argv` (lines ~67-119)
- Queue root helper: `ralph_executor/loop.py::_queue_repo_root`

**Conventions:**
- Test runner: `uv run pytest`
- Type checker: implied by codebase patterns; follow `from __future__ import annotations` style
- Commit style: conventional commits (`feat(prompt-split): ...`, `test(prompt-split): ...`)

---

## File Structure

**New files:**
- `ralph_executor/prompt_composer.py` — composer module (`compose_prompt`, `VALID_TYPES`, `PromptComposeError`)
- `tests/test_prompt_composer.py` — composer unit tests (error-path + behavioural)
- `tests/test_prompt_topic_shape.py` — folder-convention lint
- `tests/test_prompt_no_duplication.py` — anti-duplication lint
- `prompt/01-identity/identity.md`
- `prompt/02-read-order/read.md` (default = feature)
- `prompt/02-read-order/bug/read.md`
- `prompt/02-read-order/pr-feedback/read.md`
- `prompt/03-workflow/workflow.md` (default = feature)
- `prompt/03-workflow/bug/workflow.md`
- `prompt/03-workflow/pr-feedback/workflow.md`
- `prompt/04-stuck/stuck.md`
- `prompt/05-shipping/shipping.md` (default = feature/bug)
- `prompt/05-shipping/pr-feedback/shipping.md`
- `prompt/06-memory/memory.md`
- `prompt/07-forbidden/forbidden.md`
- `prompt/08-closing/closing.md`

**Modified files:**
- `ralph_executor/claude_spawn.py` (`_build_argv` only)
- `tests/test_prompt_structure.py` (rewritten to assert against assembled prompts)
- `tests/test_prompt_contents.py` (rewritten for assembled prompts)
- `tests/test_prompt_smoke.py` (fixture copies topic tree; spawn unchanged — already uses `--append-system-prompt`)
- `prompt/README.md` (rewritten to document topic-folder convention)

**Deleted files:**
- `prompt/PROMPT.md` (after composer + tests pass)

---

# Phase 1 — Composer infrastructure & lint guards

Build the composer and lint guards first so subsequent content-extraction tasks have a structural safety net. Composer behavioural tests come later (Phase 3) because they need real topic content.

---

### Task 1: Create composer module skeleton

**Files:**
- Create: `ralph_executor/prompt_composer.py`

- [ ] **Step 1: Create the module with constants, error class, and unimplemented function**

Create `ralph_executor/prompt_composer.py`:

```python
"""Standing-prompt composer for the ralph executor.

The executor calls ``compose_prompt`` once per ``claude -p`` spawn to
assemble the per-PBI-type standing prompt from per-topic source files
under ``prompt/0N-topic/``.

See ``docs/superpowers/specs/2026-05-31-prompt-md-split-design.md`` for
the topic-folder convention, the override-vs-root rule, and the
spawn-time injection mechanism.
"""

from __future__ import annotations

from pathlib import Path

VALID_TYPES = frozenset({"feature", "bug", "pr-feedback"})


class PromptComposeError(RuntimeError):
    """Raised when prompt composition cannot produce a valid result.

    Surfaces fast at spawn time before ``claude -p`` is launched so the
    executor can log a clear error and the iteration is classified as
    ``error`` rather than spawning Claude with an incomplete prompt.
    """


def compose_prompt(prompt_root: Path, pbi_type: str) -> str:
    """Assemble standing prompt for a given PBI type.

    Walks topic folders under ``prompt_root`` in alpha order. For each
    topic, uses the override subfolder matching ``pbi_type`` if present,
    otherwise the topic root ``.md`` file. Concatenates the chosen file
    contents into a single string (sections separated by blank lines)
    and returns it.

    Raises ``PromptComposeError`` if ``pbi_type`` is not in
    ``VALID_TYPES``, or if any topic folder has neither a root ``.md``
    nor a matching override.
    """
    raise NotImplementedError
```

- [ ] **Step 2: Run the existing test suite to confirm nothing is broken yet**

Run: `uv run pytest -q`
Expected: no new failures from the new module (it is unused).

- [ ] **Step 3: Commit**

```bash
git add ralph_executor/prompt_composer.py
git commit -m "feat(prompt-split): scaffold composer module skeleton"
```

---

### Task 2: Composer error-path tests (TDD red)

**Files:**
- Create: `tests/test_prompt_composer.py`

- [ ] **Step 1: Write three error-path tests against the unimplemented composer**

Create `tests/test_prompt_composer.py`:

```python
"""Unit tests for ``ralph_executor.prompt_composer.compose_prompt``.

Error-path tests use hand-crafted tmp directories so they are
independent of the real ``prompt/`` content. Behavioural tests
(against the real ``prompt/`` directory) live in Phase 3 once the
topic content has been extracted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.prompt_composer import (
    PromptComposeError,
    VALID_TYPES,
    compose_prompt,
)


def test_unknown_pbi_type_raises(tmp_path: Path) -> None:
    (tmp_path / "01-identity").mkdir()
    (tmp_path / "01-identity" / "identity.md").write_text("hello", encoding="utf-8")
    with pytest.raises(PromptComposeError, match="unknown pbi_type"):
        compose_prompt(tmp_path, "spike")


def test_empty_topic_folder_raises(tmp_path: Path) -> None:
    (tmp_path / "01-broken").mkdir()
    with pytest.raises(PromptComposeError, match="no .md in"):
        compose_prompt(tmp_path, "feature")


def test_topic_with_only_irrelevant_override_raises(tmp_path: Path) -> None:
    """If a topic has no root and no override matching the type, raise."""
    topic = tmp_path / "01-only-bug"
    topic.mkdir()
    bug = topic / "bug"
    bug.mkdir()
    (bug / "x.md").write_text("bug only", encoding="utf-8")
    with pytest.raises(PromptComposeError, match="no .md in"):
        compose_prompt(tmp_path, "feature")


def test_valid_types_constant_matches_pbi_type_literal() -> None:
    """Guard against drift between PBIType literal and VALID_TYPES."""
    assert VALID_TYPES == frozenset({"feature", "bug", "pr-feedback"})
```

- [ ] **Step 2: Run the new tests, expect all to fail with NotImplementedError**

Run: `uv run pytest tests/test_prompt_composer.py -v`
Expected: 3 FAILED (NotImplementedError), 1 PASSED (the constants guard).

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_prompt_composer.py
git commit -m "test(prompt-split): composer error-path tests (TDD red)"
```

---

### Task 3: Composer implementation (TDD green)

**Files:**
- Modify: `ralph_executor/prompt_composer.py` (replace `NotImplementedError` body)

- [ ] **Step 1: Implement compose_prompt**

Replace the `NotImplementedError` body in `compose_prompt`:

```python
    if pbi_type not in VALID_TYPES:
        raise PromptComposeError(f"unknown pbi_type {pbi_type!r}")
    sections: list[str] = []
    for topic in sorted(prompt_root.iterdir()):
        if not topic.is_dir():
            continue
        override = topic / pbi_type
        chosen = override if override.is_dir() else topic
        md_files = sorted(chosen.glob("*.md"))
        if not md_files:
            raise PromptComposeError(f"no .md in {chosen}")
        for md in md_files:
            sections.append(md.read_text(encoding="utf-8"))
    return "\n\n".join(sections)
```

- [ ] **Step 2: Run the composer tests, expect all to pass**

Run: `uv run pytest tests/test_prompt_composer.py -v`
Expected: 4 PASSED.

- [ ] **Step 3: Commit**

```bash
git add ralph_executor/prompt_composer.py
git commit -m "feat(prompt-split): implement compose_prompt"
```

---

### Task 4: Topic-shape lint test

**Files:**
- Create: `tests/test_prompt_topic_shape.py`

- [ ] **Step 1: Write the topic-shape lint**

Create `tests/test_prompt_topic_shape.py`:

```python
"""Lints the ``prompt/`` topic-folder convention.

A topic folder is either fully shared (root ``.md`` only) or per-type
divergent (root ``.md`` plus subfolders named after PBI types). The
composer rule is mechanical: root unless matching type override. This
lint enforces the shape so the rule cannot quietly stop working.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ralph_executor.prompt_composer import VALID_TYPES

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_ROOT = REPO_ROOT / "prompt"

TOPIC_NAME = re.compile(r"^\d{2}-[a-z][a-z0-9-]*$")
# pr-feedback contains a hyphen, so the override subfolder name uses the
# same character class as the topic name.
OVERRIDE_NAMES = VALID_TYPES - {"feature"}


def _topic_dirs() -> list[Path]:
    return sorted(p for p in PROMPT_ROOT.iterdir() if p.is_dir())


@pytest.mark.parametrize("topic", _topic_dirs(), ids=lambda p: p.name)
def test_topic_folder_name_matches_convention(topic: Path) -> None:
    assert TOPIC_NAME.match(topic.name), (
        f"topic folder {topic.name!r} must match ^NN-kebab-name"
    )


@pytest.mark.parametrize("topic", _topic_dirs(), ids=lambda p: p.name)
def test_topic_has_root_md_or_overrides(topic: Path) -> None:
    roots = list(topic.glob("*.md"))
    overrides = [d for d in topic.iterdir() if d.is_dir()]
    assert roots or overrides, f"topic {topic.name} is empty"


@pytest.mark.parametrize("topic", _topic_dirs(), ids=lambda p: p.name)
def test_overrides_use_valid_type_names_and_are_non_empty(topic: Path) -> None:
    for sub in topic.iterdir():
        if not sub.is_dir():
            continue
        assert sub.name in OVERRIDE_NAMES, (
            f"unknown override subfolder {sub.name!r} in {topic.name}; "
            f"expected one of {sorted(OVERRIDE_NAMES)}"
        )
        assert list(sub.glob("*.md")), f"empty override {sub}"
```

- [ ] **Step 2: Run it; expect it to pass trivially (no topic folders exist yet, so parametrize yields zero cases)**

Run: `uv run pytest tests/test_prompt_topic_shape.py -v`
Expected: 0 tests collected (the parametrize list is empty until topics exist). pytest exits 5 when no tests are collected.

If pytest exits 5 (no tests), that is expected for now; the lint becomes active in Phase 2 as topic folders appear.

- [ ] **Step 3: Commit**

```bash
git add tests/test_prompt_topic_shape.py
git commit -m "test(prompt-split): topic-shape lint guard"
```

---

### Task 5: Anti-duplication lint test

**Files:**
- Create: `tests/test_prompt_no_duplication.py`

- [ ] **Step 1: Write the anti-duplication lint**

Create `tests/test_prompt_no_duplication.py`:

```python
"""Lints that the same content does not appear in multiple override files.

If a substantial line appears in two override subfolders of the same
topic, it should have been hoisted to the topic root instead. This
test enforces the single-source-of-truth invariant.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_ROOT = REPO_ROOT / "prompt"

MIN_LINE_LENGTH = 60  # ignore short lines (headings, bullets, blanks)


def _collect_substantial_lines(folder: Path) -> set[str]:
    out: set[str] = set()
    for md in folder.glob("*.md"):
        for raw in md.read_text(encoding="utf-8").splitlines():
            ln = raw.strip()
            if len(ln) >= MIN_LINE_LENGTH:
                out.add(ln)
    return out


def _topics_with_multiple_overrides() -> list[Path]:
    if not PROMPT_ROOT.is_dir():
        return []
    out: list[Path] = []
    for topic in sorted(PROMPT_ROOT.iterdir()):
        if not topic.is_dir():
            continue
        overrides = [d for d in topic.iterdir() if d.is_dir()]
        if len(overrides) >= 2:
            out.append(topic)
    return out


@pytest.mark.parametrize(
    "topic", _topics_with_multiple_overrides(), ids=lambda p: p.name
)
def test_no_substantial_line_appears_in_multiple_overrides(topic: Path) -> None:
    overrides = [d for d in topic.iterdir() if d.is_dir()]
    line_sets = {sub.name: _collect_substantial_lines(sub) for sub in overrides}
    for (name_a, lines_a), (name_b, lines_b) in combinations(line_sets.items(), 2):
        common = lines_a & lines_b
        assert not common, (
            f"topic {topic.name}: lines duplicated between "
            f"{name_a}/ and {name_b}/ — hoist to root.\n"
            + "\n".join(sorted(common))
        )
```

- [ ] **Step 2: Run it; expect no tests collected (parametrize empty until topics with multiple overrides exist)**

Run: `uv run pytest tests/test_prompt_no_duplication.py -v`
Expected: 0 tests collected. Lint activates in Phase 2.

- [ ] **Step 3: Commit**

```bash
git add tests/test_prompt_no_duplication.py
git commit -m "test(prompt-split): anti-duplication lint guard"
```

---

# Phase 2 — Topic content extraction

Each task extracts one topic's content from `prompt/PROMPT.md` into the new structure. The source file remains in place until Phase 3 — the structural test still asserts against it. Tasks are ordered to make commits independently reviewable.

**Source mapping reference (from spec):**

| PROMPT.md lines | Destination |
|---|---|
| 1-50 | `01-identity/identity.md` |
| 52-70 | `02-read-order/read.md` (feature default) |
| 72-90 | `02-read-order/bug/read.md` |
| 92-107 | `02-read-order/pr-feedback/read.md` |
| 109-134 | `03-workflow/workflow.md` (feature default) |
| 136-168 | `03-workflow/bug/workflow.md` (plus bug-only memory triggers from 412-413) |
| 170-202 | `03-workflow/pr-feedback/workflow.md` (plus elevated 319-340, reviewer 342-393, feedback forbiddens 447-453, feedback memory trigger 415) |
| 204-266 | `04-stuck/stuck.md` |
| 268-300 + 307-317 | `05-shipping/shipping.md` (default = feature/bug + body conventions + post-push HISTORY rule from 454-455) |
| 302-305 | `05-shipping/pr-feedback/shipping.md` |
| 395-411 + 416-427 | `06-memory/memory.md` |
| 429-446 | `07-forbidden/forbidden.md` |
| 457-460 | `08-closing/closing.md` |

For each topic task: open `prompt/PROMPT.md` at the listed line range, copy the content verbatim, paste into the new file. Adjust internal cross-references if the section moved relative to others (e.g. "see X below" pointers).

---

### Task 6: Extract 01-identity

**Files:**
- Create: `prompt/01-identity/identity.md`

- [ ] **Step 1: Read PROMPT.md lines 1-50 and write to new file**

Source: `prompt/PROMPT.md`, lines 1-50 (header + Who you are + What you read intro + target_repo paragraph).

Create `prompt/01-identity/identity.md` with the content of those lines verbatim. The file should begin `# Ralph — standing instructions` (the original H1) and end with the paragraph describing `target_repo`.

Adjust: the original "Read in this order" list of "1. prompt/PROMPT.md, 2. HISTORY.md" inside `What you read` references `prompt/PROMPT.md`. Update that reference to read "the standing prompt (already loaded as your system prompt)" so the new injection mechanism is reflected accurately.

- [ ] **Step 2: Verify the file exists and is non-empty**

Run: `wc -l prompt/01-identity/identity.md`
Expected: ~50 lines.

- [ ] **Step 3: Run topic-shape lint to confirm folder is well-formed**

Run: `uv run pytest tests/test_prompt_topic_shape.py -v`
Expected: 3 tests collected (one per parametrize axis × 1 topic), all PASSED.

- [ ] **Step 4: Commit**

```bash
git add prompt/01-identity/identity.md
git commit -m "feat(prompt-split): extract 01-identity"
```

---

### Task 7: Extract 02-read-order (root + bug + pr-feedback overrides)

**Files:**
- Create: `prompt/02-read-order/read.md`
- Create: `prompt/02-read-order/bug/read.md`
- Create: `prompt/02-read-order/pr-feedback/read.md`

- [ ] **Step 1: Create default `read.md` (feature)**

Source: `prompt/PROMPT.md`, lines 52-58 ("All PBI types — always read first" block) + lines 60-70 ("### Feature PBI — read order" section).

Write `prompt/02-read-order/read.md`. Begin with the shared "always read first" preamble (keep `## What you read` heading or demote to `## Read order` — pick whichever keeps the structural test happy; see Task 17 for the rewritten test).

Replace the bullet `1. prompt/PROMPT.md (this file)` with `1. The standing prompt (loaded as your system prompt — already in context)` to match the new injection mechanism. Keep the rest of the read-order list intact.

- [ ] **Step 2: Decision — handle the "always read first" preamble**

Lines 52-58 of the source are an "always read first" preamble shared by all three read-order variants. Strict-replace override semantics mean the override owns the topic; if we put the preamble only in the root, Claude won't see it on bug or pr-feedback iterations.

**Decision (v1):** include the preamble at the top of all three files (`read.md`, `bug/read.md`, `pr-feedback/read.md`). The preamble is 4-5 short lines (each well under 60 characters), so the anti-duplication lint won't flag it.

If a future change makes the preamble substantial (>60-char lines), apply the invariant I4 remedy: split this topic into `02-read-order-preamble/preamble.md` (shared) + `02a-read-order-typed/` (per-type with no preamble). Do NOT add merge logic to the composer.

- [ ] **Step 3: Create `bug/read.md`**

Source: lines 72-90 ("### Bug PBI — read order" section, including the `docs/INVESTIGATE.md` note).

Write `prompt/02-read-order/bug/read.md`. Start with the same preamble as `read.md` (lines 52-58), then add the bug-specific read-order section.

- [ ] **Step 4: Create `pr-feedback/read.md`**

Source: lines 92-107 ("### PR-feedback PBI — read order" section).

Write `prompt/02-read-order/pr-feedback/read.md`. Start with the same preamble (lines 52-58), then add the PR-feedback-specific read-order section.

- [ ] **Step 5: Run topic-shape lint**

Run: `uv run pytest tests/test_prompt_topic_shape.py -v`
Expected: All tests pass for both topics (01-identity, 02-read-order).

- [ ] **Step 6: Run anti-duplication lint**

Run: `uv run pytest tests/test_prompt_no_duplication.py -v`
Expected: 1 test collected (02-read-order has 2 overrides), PASSED. The preamble bullets are short enough to slip under the 60-char threshold.

If the lint FAILS, apply the invariant I4 remedy from Step 2: split the topic. Do NOT lower the lint threshold (that defeats the purpose of the guard).

- [ ] **Step 7: Commit**

```bash
git add prompt/02-read-order/
git commit -m "feat(prompt-split): extract 02-read-order with bug + pr-feedback overrides"
```

---

### Task 8: Extract 03-workflow/workflow.md (feature default)

**Files:**
- Create: `prompt/03-workflow/workflow.md`

- [ ] **Step 1: Copy feature workflow content**

Source: `prompt/PROMPT.md`, lines 109-134 ("## How to work a feature PBI" section, steps 1-7).

Write `prompt/03-workflow/workflow.md`. Keep the `## How to work a feature PBI` heading verbatim so the rewritten structural test (Task 17) can pin it.

- [ ] **Step 2: Run topic-shape lint**

Run: `uv run pytest tests/test_prompt_topic_shape.py -v`
Expected: All collected tests pass.

- [ ] **Step 3: Commit**

```bash
git add prompt/03-workflow/workflow.md
git commit -m "feat(prompt-split): extract 03-workflow feature default"
```

---

### Task 9: Extract 03-workflow/bug/workflow.md

**Files:**
- Create: `prompt/03-workflow/bug/workflow.md`

- [ ] **Step 1: Compose bug workflow with inlined bug-only memory triggers**

Source content (in this order):
- `prompt/PROMPT.md` lines 136-168 (`## How to work a bug PBI` section, steps 1-8 + the failed-hypothesis tail paragraph)
- A new sub-section at the end titled `### Memory triggers specific to bug PBIs`, content from lines 412-413 (the "After REPRODUCE.md confirms" and "After identify the root cause" bullets), prefixed with a short sentence linking back to "the universal memory triggers in `06-memory/memory.md`".

Example tail to append:

```markdown
### Memory triggers specific to bug PBIs

In addition to the universal triggers documented in the memory section,
bug iterations also record:

- **After `REPRODUCE.md` confirms the failure signature.** If the
  reproduction signal is non-obvious (specific input, environment
  variable, race condition, ordering), call
  `mcp__better-memory__memory_observe` with `outcome="neutral"`
  recording how you triggered the failure.
- **After you identify the root cause in `HISTORY.md`.** Call
  `memory_observe` with `outcome="failure"` recording the cause and the
  diagnostic path that surfaced it. Include component / file paths in
  `component`.
```

- [ ] **Step 2: Run topic-shape lint**

Run: `uv run pytest tests/test_prompt_topic_shape.py -v`
Expected: All collected tests pass.

- [ ] **Step 3: Commit**

```bash
git add prompt/03-workflow/bug/workflow.md
git commit -m "feat(prompt-split): extract 03-workflow bug override with inlined memory triggers"
```

---

### Task 10: Extract 03-workflow/pr-feedback/workflow.md

**Files:**
- Create: `prompt/03-workflow/pr-feedback/workflow.md`

- [ ] **Step 1: Compose the feedback workflow with all inlined sections**

This is the largest single file in the split. Content order:

1. `## How to work a PR-feedback PBI` heading + body from `prompt/PROMPT.md` lines 170-202 (steps 1-5 + the "Do not address comments not in this FEEDBACK.md" paragraph at line 200-202).
2. `## Elevated-attention categories` heading + body from lines 319-340 verbatim (intro paragraph + the 4-row category table + the concluding paragraph). Keep the heading exact.
3. `## Reviewer feedback — treat respectfully, challenge with reasoning` heading + body from lines 342-393 verbatim (intro + "When the reviewer is right", "When you believe the reviewer is wrong", "When you cannot tell", "What never goes in a reply" subsections). Keep the heading exact (it includes em-dash and the trailing phrase).
4. `### Memory triggers specific to PR-feedback PBIs` sub-section, content from line 415 (the "After a BugBot or reviewer-fix commit" bullet) prefixed with a short sentence linking back to universal triggers.
5. `### Forbiddens specific to PR-feedback PBIs` sub-section, content from lines 447-453 (the three feedback-only forbiddens: never create a new PR, never address comments outside FEEDBACK.md, never set wontFix/byDesign) prefixed with a short sentence linking back to the universal "What you never do" section.

Example tail sections:

```markdown
### Memory triggers specific to PR-feedback PBIs

In addition to the universal triggers documented in the memory section,
feedback iterations also record:

- **After a BugBot or reviewer-fix commit.** (Global CLAUDE.md
  mandatory trigger.) Call `memory_observe` with `outcome="failure"`
  recording the gap the fix closed — the fix's existence proves it was
  non-obvious. Do this BEFORE moving on to the next reviewer comment.

### Forbiddens specific to PR-feedback PBIs

In addition to the universal prohibitions in the "What you never do"
section, feedback iterations must NEVER:

- **Create a new PR for PR-feedback work.** Push to the existing
  branch.
- **Address comments outside the current `FEEDBACK.md`.** If you
  see other comments on the PR that are not in `FEEDBACK.md`, the
  sweep will pick them up next round.
- **Set thread status to `wontFix` or `byDesign`.** Those are
  human-only.
```

- [ ] **Step 2: Run topic-shape + anti-duplication lints**

Run: `uv run pytest tests/test_prompt_topic_shape.py tests/test_prompt_no_duplication.py -v`
Expected: All collected tests pass. Anti-duplication has two collected cases now (02-read-order and 03-workflow each have ≥2 overrides). Both should pass — bug and pr-feedback workflow content do not overlap substantially.

- [ ] **Step 3: Commit**

```bash
git add prompt/03-workflow/pr-feedback/workflow.md
git commit -m "feat(prompt-split): extract 03-workflow pr-feedback override with inlined elevated, reviewer, forbiddens, memory triggers"
```

---

### Task 11: Extract 04-stuck/stuck.md

**Files:**
- Create: `prompt/04-stuck/stuck.md`

- [ ] **Step 1: Copy stuck content**

Source: `prompt/PROMPT.md`, lines 204-266 (`## When to write STUCK.md` section, including triggers list and `### STUCK.md structure` template).

Write `prompt/04-stuck/stuck.md` verbatim.

- [ ] **Step 2: Run topic-shape lint**

Run: `uv run pytest tests/test_prompt_topic_shape.py -v`
Expected: All collected tests pass.

- [ ] **Step 3: Commit**

```bash
git add prompt/04-stuck/stuck.md
git commit -m "feat(prompt-split): extract 04-stuck"
```

---

### Task 12: Extract 05-shipping (root + pr-feedback override)

**Files:**
- Create: `prompt/05-shipping/shipping.md`
- Create: `prompt/05-shipping/pr-feedback/shipping.md`

- [ ] **Step 1: Create the default (feature/bug) shipping file**

Source content:
- Lines 268-273 (the intro paragraph) + 274-300 (`### For features and bugs (new PR)` section, steps 1-4)
- Lines 307-317 (`### PR body conventions` subsection)
- Lines 454-455 (the "Never write to HISTORY.md AFTER pushing the PR" forbidden, inlined as a final paragraph since it is shipping-tied)

Skip lines 302-305 (the `### For PR-feedback (existing PR)` subsection — that goes in the override).

Replace the `## How to ship a PR` heading with a structure that omits the "For PR-feedback" branch — the override will be the only file feedback PBIs see. Keep `## How to ship a PR` heading verbatim for the structural test.

Example final paragraph for the post-push rule:

```markdown
**Never write to `HISTORY.md` AFTER pushing the PR.** The append
happens BEFORE the push so the commit captures the full history.
```

- [ ] **Step 2: Create the feedback override**

Source: lines 302-305 (`### For PR-feedback (existing PR)` subsection — "No new PR. Push to the existing branch...").

Write `prompt/05-shipping/pr-feedback/shipping.md`. Keep `## How to ship a PR` as the top-level heading for consistency (the structural test pins it in the assembled feedback prompt). Body can be a single paragraph explaining the feedback shipping flow + a backreference to step 4 of the feedback workflow.

- [ ] **Step 3: Run lints**

Run: `uv run pytest tests/test_prompt_topic_shape.py tests/test_prompt_no_duplication.py -v`
Expected: All collected tests pass. 05-shipping has only one override (`pr-feedback`), so anti-duplication lint does not parametrize over it.

- [ ] **Step 4: Commit**

```bash
git add prompt/05-shipping/
git commit -m "feat(prompt-split): extract 05-shipping with pr-feedback override"
```

---

### Task 13: Extract 06-memory/memory.md

**Files:**
- Create: `prompt/06-memory/memory.md`

- [ ] **Step 1: Copy memory section minus type-specific triggers**

Source content:
- Lines 395-411 (intro paragraph + `### At iteration start` subsection)
- Line 414 (the "After a fix commit lands" universal trigger)
- Line 416 (the "Before this iteration exits" universal trigger)
- Lines 418-427 (`### What NOT to record` + `### Observation field defaults`)

Skip lines 412-413 (bug-specific triggers — moved to bug workflow override in Task 9) and line 415 (feedback-specific trigger — moved to feedback workflow override in Task 10).

The `### Inline triggers — observe AS the iteration progresses` subsection should remain but with only the two universal bullets. Add a one-line note that "type-specific triggers are documented in the workflow section for the relevant PBI type."

- [ ] **Step 2: Run topic-shape lint**

Run: `uv run pytest tests/test_prompt_topic_shape.py -v`
Expected: All collected tests pass.

- [ ] **Step 3: Commit**

```bash
git add prompt/06-memory/memory.md
git commit -m "feat(prompt-split): extract 06-memory (universal triggers only)"
```

---

### Task 14: Extract 07-forbidden/forbidden.md

**Files:**
- Create: `prompt/07-forbidden/forbidden.md`

- [ ] **Step 1: Copy absolute prohibitions only**

Source content from `prompt/PROMPT.md`:
- Lines 429-431 (the `## What you never do` heading + intro sentence)
- Lines 433-446 (the first 5 bullets: never touch main, never touch ralph-queue, never delete out-of-scope files, never widen permissions, never call AskUserQuestion)

Skip lines 447-453 (feedback-specific forbiddens — moved to feedback workflow override in Task 10) and lines 454-455 (the HISTORY.md-after-push rule — moved to shipping in Task 12).

Keep `## What you never do` heading verbatim for the structural test.

- [ ] **Step 2: Run topic-shape lint**

Run: `uv run pytest tests/test_prompt_topic_shape.py -v`
Expected: All collected tests pass.

- [ ] **Step 3: Commit**

```bash
git add prompt/07-forbidden/forbidden.md
git commit -m "feat(prompt-split): extract 07-forbidden (absolute prohibitions only)"
```

---

### Task 15: Extract 08-closing/closing.md

**Files:**
- Create: `prompt/08-closing/closing.md`

- [ ] **Step 1: Copy closing marker**

Source: `prompt/PROMPT.md`, lines 457-460 (the `---` separator + `**End of standing instructions.** Now read HISTORY.md in the current ...` paragraph).

Write `prompt/08-closing/closing.md` verbatim. The closing paragraph references "read PROMPT.md" implicitly via context; since the prompt is now a system prompt, adjust the trailing sentence to read "Now read `HISTORY.md` in the current PBI directory and proceed with the per-type read order above" (drop any leftover reference to reading PROMPT.md mid-session).

- [ ] **Step 2: Run topic-shape lint over the whole tree**

Run: `uv run pytest tests/test_prompt_topic_shape.py -v`
Expected: 24 tests collected (3 axes × 8 topics), all PASSED.

- [ ] **Step 3: Commit**

```bash
git add prompt/08-closing/closing.md
git commit -m "feat(prompt-split): extract 08-closing"
```

---

# Phase 3 — Behavioural tests, integration, cleanup

Content is fully extracted; now wire up behavioural tests, the spawn integration, and clean up the legacy `PROMPT.md` and README.

---

### Task 16: Composer behavioural tests against real prompt/

**Files:**
- Modify: `tests/test_prompt_composer.py` (append new tests)

- [ ] **Step 1: Append behavioural tests using the real `prompt/` directory**

Add to `tests/test_prompt_composer.py`:

```python
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_PROMPT = REPO_ROOT / "prompt"


def test_feature_assembly_contains_feature_workflow() -> None:
    out = compose_prompt(REAL_PROMPT, "feature")
    assert "How to work a feature PBI" in out
    assert "How to work a bug PBI" not in out
    assert "How to work a PR-feedback PBI" not in out


def test_bug_assembly_contains_bug_workflow_and_investigate_ref() -> None:
    out = compose_prompt(REAL_PROMPT, "bug")
    assert "How to work a bug PBI" in out
    assert "docs/INVESTIGATE.md" in out
    assert "BUG.md" in out
    assert "REPRODUCE.md" in out
    assert "How to work a feature PBI" not in out


def test_pr_feedback_assembly_inlines_elevated_and_reviewer_blocks() -> None:
    out = compose_prompt(REAL_PROMPT, "pr-feedback")
    assert "How to work a PR-feedback PBI" in out
    assert "FEEDBACK.md" in out
    assert "Elevated-attention" in out
    assert "Reviewer feedback" in out
    assert "How to work a feature PBI" not in out


def test_assembly_order_matches_numeric_prefix() -> None:
    out = compose_prompt(REAL_PROMPT, "feature")
    # Section order anchors — pick one well-known phrase per topic.
    idx_identity = out.index("Who you are")
    idx_workflow = out.index("How to work a feature PBI")
    idx_stuck = out.index("When to write STUCK.md")
    idx_ship = out.index("How to ship a PR")
    idx_forbidden = out.index("What you never do")
    assert idx_identity < idx_workflow < idx_stuck < idx_ship < idx_forbidden


def test_assembly_size_bounds() -> None:
    """Assembled prompt should be substantially smaller than the old single
    file for feature/bug, slightly smaller for feedback."""
    out_feature = compose_prompt(REAL_PROMPT, "feature")
    out_feedback = compose_prompt(REAL_PROMPT, "pr-feedback")
    # Sanity floor: at least the universal content.
    assert len(out_feature) > 4_000
    assert len(out_feedback) > 4_000
    # Feature drops feedback-only content, so it must be smaller than feedback.
    assert len(out_feature) < len(out_feedback)
```

- [ ] **Step 2: Run the composer tests**

Run: `uv run pytest tests/test_prompt_composer.py -v`
Expected: 9 PASSED (4 from Phase 1 + 5 new). If any behavioural test fails, the content extraction in Phase 2 missed a required phrase — fix the relevant topic file before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/test_prompt_composer.py
git commit -m "test(prompt-split): composer behavioural tests against real prompt tree"
```

---

### Task 17: Rewrite tests/test_prompt_structure.py

**Files:**
- Modify: `tests/test_prompt_structure.py` (full rewrite)

- [ ] **Step 1: Replace the file with a per-type structural test**

Replace the contents of `tests/test_prompt_structure.py`:

```python
"""Structural tests for the assembled prompt.

The standing prompt now lives under ``prompt/0N-topic/`` and is
assembled by ``compose_prompt`` at spawn time. These tests load the
assembled string for each ``PBIType`` and assert that required headings
and load-bearing phrases appear in the appropriate per-type assembly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.prompt_composer import compose_prompt

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_ROOT = REPO_ROOT / "prompt"

# Headings expected in each per-type assembly.
HEADINGS_ALL_TYPES: tuple[str, ...] = (
    "Who you are",
    "When to write STUCK.md",
    "How to ship a PR",
    "What you never do",
)
HEADINGS_FEATURE_ONLY: tuple[str, ...] = ("How to work a feature PBI",)
HEADINGS_BUG_ONLY: tuple[str, ...] = ("How to work a bug PBI",)
HEADINGS_PR_FEEDBACK_ONLY: tuple[str, ...] = (
    "How to work a PR-feedback PBI",
    "Elevated-attention categories",
    "Reviewer feedback",
)

# Phrases that must appear in every assembled prompt (load-bearing).
PHRASES_ALL_TYPES: tuple[str, ...] = (
    "HISTORY.md",
    "MUST carry a `target_repo`",
    "test deletion",
    "permission widening",
    "large diff",
)
PHRASES_BUG_ONLY: tuple[str, ...] = (
    "BUG.md",
    "REPRODUCE.md",
    "docs/INVESTIGATE.md",
)
PHRASES_PR_FEEDBACK_ONLY: tuple[str, ...] = (
    "ORIGINAL.md",
    "FEEDBACK.md",
    "respectfully",
    "challenge",
)


def _assembled(pbi_type: str) -> str:
    return compose_prompt(PROMPT_ROOT, pbi_type)


@pytest.mark.parametrize("pbi_type", ["feature", "bug", "pr-feedback"])
@pytest.mark.parametrize("heading", HEADINGS_ALL_TYPES)
def test_assembled_contains_shared_heading(pbi_type: str, heading: str) -> None:
    assert heading in _assembled(pbi_type), (
        f"assembled {pbi_type} prompt missing required heading: {heading!r}"
    )


@pytest.mark.parametrize("heading", HEADINGS_FEATURE_ONLY)
def test_feature_only_headings(heading: str) -> None:
    assembled = _assembled("feature")
    assert heading in assembled, f"feature prompt missing heading {heading!r}"


@pytest.mark.parametrize("heading", HEADINGS_BUG_ONLY)
def test_bug_only_headings(heading: str) -> None:
    assembled = _assembled("bug")
    assert heading in assembled, f"bug prompt missing heading {heading!r}"


@pytest.mark.parametrize("heading", HEADINGS_PR_FEEDBACK_ONLY)
def test_pr_feedback_only_headings(heading: str) -> None:
    assembled = _assembled("pr-feedback")
    assert heading in assembled, f"pr-feedback prompt missing heading {heading!r}"


@pytest.mark.parametrize("pbi_type", ["feature", "bug", "pr-feedback"])
@pytest.mark.parametrize("phrase", PHRASES_ALL_TYPES)
def test_assembled_contains_shared_phrase(pbi_type: str, phrase: str) -> None:
    assert phrase in _assembled(pbi_type), (
        f"assembled {pbi_type} prompt missing required phrase: {phrase!r}"
    )


@pytest.mark.parametrize("phrase", PHRASES_BUG_ONLY)
def test_bug_only_phrases(phrase: str) -> None:
    assert phrase in _assembled("bug")


@pytest.mark.parametrize("phrase", PHRASES_PR_FEEDBACK_ONLY)
def test_pr_feedback_only_phrases(phrase: str) -> None:
    assert phrase in _assembled("pr-feedback")


@pytest.mark.parametrize("pbi_type", ["feature", "bug", "pr-feedback"])
def test_assembled_has_no_placeholder_tokens(pbi_type: str) -> None:
    assembled = _assembled(pbi_type)
    for bad in ("TODO", "TBD", "<placeholder>", "FIXME"):
        assert bad not in assembled, (
            f"assembled {pbi_type} prompt contains placeholder token {bad!r}"
        )


@pytest.mark.parametrize("pbi_type", ["feature", "bug", "pr-feedback"])
def test_assembled_size_bounds(pbi_type: str) -> None:
    """Assembled prompt must be substantial but not bloated. Mirrors the
    legacy single-file bounds (4 KB lower, 25 KB upper)."""
    assembled = _assembled(pbi_type)
    n = len(assembled)
    assert 4_000 <= n <= 25_000, (
        f"assembled {pbi_type} prompt is {n} chars (must be 4k-25k)"
    )
```

- [ ] **Step 2: Run the rewritten structural test**

Run: `uv run pytest tests/test_prompt_structure.py -v`
Expected: All tests PASS. If a phrase or heading test fails, the content extraction in Phase 2 placed it in the wrong topic — locate the missing content in `prompt/0N-topic/` and fix.

- [ ] **Step 3: Commit**

```bash
git add tests/test_prompt_structure.py
git commit -m "test(prompt-split): rewrite structural tests for per-type assembled prompt"
```

---

### Task 18: Rewrite tests/test_prompt_contents.py

**Files:**
- Modify: `tests/test_prompt_contents.py` (full rewrite)

- [ ] **Step 1: Replace the file**

Replace contents of `tests/test_prompt_contents.py`:

```python
"""Guard rails for load-bearing wording in the assembled prompt.

Specific phrases pinned here are load-bearing: ralph-new's design
depends on ``target_repo`` being documented as REQUIRED, and the
loop already enforces it. Assertions run against the assembled
prompt for each PBI type so a phrase that should appear in every
type cannot be accidentally moved into a type-specific override.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.prompt_composer import compose_prompt

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_ROOT = REPO_ROOT / "prompt"


@pytest.mark.parametrize("pbi_type", ["feature", "bug", "pr-feedback"])
def test_target_repo_documented_as_required(pbi_type: str) -> None:
    assembled = compose_prompt(PROMPT_ROOT, pbi_type)
    assert "MUST carry a `target_repo`" in assembled
    assert "canonical" in assembled.lower()


@pytest.mark.parametrize("pbi_type", ["feature", "bug", "pr-feedback"])
def test_no_stale_informational_only_wording(pbi_type: str) -> None:
    assembled = compose_prompt(PROMPT_ROOT, pbi_type)
    assert "informational metadata for now" not in assembled
    assert "the loop does not yet act on it" not in assembled
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_prompt_contents.py -v`
Expected: 6 PASSED (3 types × 2 tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_prompt_contents.py
git commit -m "test(prompt-split): rewrite contents tests for assembled prompt"
```

---

### Task 19: Delete prompt/PROMPT.md

**Files:**
- Delete: `prompt/PROMPT.md`

- [ ] **Step 1: Confirm nothing in the codebase still references the file path**

Run: `grep -rn "prompt/PROMPT.md\|./prompt/PROMPT.md\|PROMPT.md" --include="*.py" ralph_executor tests scripts skills 2>&1 | grep -v "test_prompt_smoke\|claude_spawn.py"`
Expected: only references in `claude_spawn.py` and `test_prompt_smoke.py` (those get updated in Tasks 20 and 21). Any other reference must be removed before deletion.

- [ ] **Step 2: Delete the file**

Run: `git rm prompt/PROMPT.md`

- [ ] **Step 3: Run the full test suite to confirm no test reads the deleted file**

Run: `uv run pytest tests/ -v --ignore=tests/test_prompt_smoke.py -x`
Expected: All tests PASS. The smoke test is excluded because it's still under update (Task 21) and is opt-in anyway.

- [ ] **Step 4: Commit the deletion alone (clean removal)**

```bash
git commit -m "feat(prompt-split): remove legacy prompt/PROMPT.md (split into topic folders)"
```

---

### Task 20: Update _build_argv to use composer + --append-system-prompt

**Files:**
- Modify: `ralph_executor/claude_spawn.py` (lines 67-119, `_build_argv` function)

- [ ] **Step 1: Update the import block at the top of the file**

In `ralph_executor/claude_spawn.py`, add (alongside existing imports):

```python
from ralph_executor.prompt_composer import PromptComposeError, compose_prompt
```

If the `_queue_repo_root` helper is not imported here (it lives in `loop.py`), import it or replicate the path computation. Replicate is fine if it avoids a circular import:

```python
# Local replica to avoid importing from loop.py (would risk circular import).
def _queue_repo_root_for_spawn(cfg: ExecutorConfig) -> Path:
    if cfg.use_worktrees:
        return cfg.workspace_root / "queue"
    return cfg.repo_path
```

Verify the body matches what `loop.py::_queue_repo_root` does (read it first).

- [ ] **Step 2: Update `_build_argv` to take the composed standing prompt**

Modify `_build_argv` signature and body in `ralph_executor/claude_spawn.py`:

```python
def _build_argv(
    cfg: ExecutorConfig, *, pbi_dir: Path, pbi: PBI
) -> list[str]:
    """Compose the argv passed to the ``claude`` binary.

    Standing prompt (the file formerly known as PROMPT.md) is assembled
    per-PBI-type by ``compose_prompt`` and injected via
    ``--append-system-prompt`` so it arrives as system context rather
    than via a mid-session ``Read`` tool call. The user-facing ``-p``
    instruction shrinks to a one-line directive pointing at the PBI dir.

    See ``docs/superpowers/specs/2026-05-31-prompt-md-split-design.md``.
    """
    prompt_root = _queue_repo_root_for_spawn(cfg) / "prompt"
    standing = compose_prompt(prompt_root, pbi.type)

    argv = [
        cfg.claude_binary,
        "--output-format",
        "stream-json",
        "--verbose",
        # See original docstring for why --permission-mode comes before -p.
        "--permission-mode",
        cfg.claude_permission_mode,
        "--append-system-prompt",
        standing,
        "-p",
        f"Work the PBI in {pbi_dir}. Follow your standing instructions.",
    ]
    return argv
```

The `pbi` parameter is new (was implicit before). Update the only caller of `_build_argv` in `claude_spawn.py` to pass `pbi`.

Search for `_build_argv(` in `claude_spawn.py` and update the call site:

```bash
grep -n "_build_argv(" ralph_executor/claude_spawn.py
```

There should be exactly one call. Adjust it to pass `pbi=pbi` (the variable is already in scope at that call site — it's the parameter of `spawn_claude_p`).

- [ ] **Step 3: Update any tests that directly call `_build_argv` to pass `pbi`**

Find tests that call `_build_argv`:

```bash
grep -rn "_build_argv" tests/
```

For each test, update the call signature to pass a `PBI` instance with a valid `type`. Sample PBIs already exist in test fixtures — reuse them.

- [ ] **Step 4: Run unit tests excluding the opt-in smoke**

Run: `uv run pytest tests/ -v --ignore=tests/test_prompt_smoke.py`
Expected: All PASS. If a `claude_spawn` test asserts on the old `-p` text containing `Read ./prompt/PROMPT.md`, update the assertion to match the new shorter directive.

- [ ] **Step 5: Commit**

```bash
git add ralph_executor/claude_spawn.py tests/
git commit -m "feat(prompt-split): inject composed prompt via --append-system-prompt"
```

---

### Task 21: Update tests/test_prompt_smoke.py for the new topic tree

**Files:**
- Modify: `tests/test_prompt_smoke.py`

- [ ] **Step 1: Update the fixture to copy the topic tree**

In `tests/test_prompt_smoke.py`, locate the `tmp_ralph_workspace` fixture (around line 67). Replace the block that copies `PROMPT.md` with one that copies the entire `prompt/` directory:

```python
# Copy the prompt/ topic tree into the workspace at the canonical path.
import shutil
src_prompt = REPO_ROOT / "prompt"
dst_prompt = workspace / "prompt"
shutil.copytree(src_prompt, dst_prompt)
```

Remove the existing `PROMPT_PATH = REPO_ROOT / "prompt" / "PROMPT.md"` constant (no longer applicable) and the `shutil.copy2(PROMPT_PATH, prompt_dir / "PROMPT.md")` block.

- [ ] **Step 2: Update `_run_claude` to compose the prompt and inject it**

Replace the `_run_claude` function body so it composes via the same function the executor uses:

```python
def _run_claude(workspace: Path) -> subprocess.CompletedProcess[str]:
    """Spawn ``claude -p`` against the workspace mirroring production."""
    from ralph_executor.prompt_composer import compose_prompt

    prompt_root = workspace / "prompt"
    # Smoke uses the feature default — sample is a feature PBI.
    standing = compose_prompt(prompt_root, "feature")
    user_message = (
        "Begin iteration on the PBI in .ralph/current/WI-1234. Follow "
        "your standing instructions, advance the PBI by exactly one "
        "step, and exit."
    )

    return subprocess.run(
        [
            "claude",
            "-p", user_message,
            "--append-system-prompt", standing,
            "--output-format", "json",
            "--dangerously-skip-permissions",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CLAUDE_TIMEOUT_SECONDS,
        check=False,
    )
```

- [ ] **Step 3: Run the smoke test opt-in (manual, requires `claude` CLI + ANTHROPIC_API_KEY)**

This step requires network + a real claude CLI. Skip it if running headless:

Run: `RALPH_PROMPT_SMOKE=1 uv run pytest tests/test_prompt_smoke.py -v`
Expected: PASS. The sample feature PBI gets advanced by one step.

If skipping, run the structural smoke surface (everything except the spawn):

Run: `uv run pytest tests/test_prompt_smoke.py --collect-only`
Expected: tests collected without import errors.

- [ ] **Step 4: Commit**

```bash
git add tests/test_prompt_smoke.py
git commit -m "test(prompt-split): smoke fixture copies topic tree; composes via prompt_composer"
```

---

### Task 22: Update prompt/README.md

**Files:**
- Modify: `prompt/README.md` (full rewrite)

- [ ] **Step 1: Replace the file**

Replace `prompt/README.md` with a rewrite that documents the topic-folder convention:

```markdown
# `prompt/` — Ralph's standing instructions

This directory holds the per-topic source files that compose Ralph's
standing instructions. The executor calls `compose_prompt` at spawn
time to assemble a per-PBI-type string and injects it into Claude via
`--append-system-prompt`. There is no single `PROMPT.md` file — the
content lives under topic folders.

## Layout

```
prompt/
  01-identity/identity.md          # shared
  02-read-order/
    read.md                        # default (feature)
    bug/read.md
    pr-feedback/read.md
  03-workflow/
    workflow.md                    # default (feature)
    bug/workflow.md
    pr-feedback/workflow.md
  04-stuck/stuck.md                # shared
  05-shipping/
    shipping.md                    # default (feature/bug)
    pr-feedback/shipping.md
  06-memory/memory.md              # shared
  07-forbidden/forbidden.md        # shared
  08-closing/closing.md            # shared
```

Topic folders carry a numeric prefix; alpha-sort gives the assembly
order. Override subfolder names match the `PBIType` literal values in
`ralph_executor/types.py` (`bug`, `pr-feedback`). `feature` has no
override — it is the default at the root of each topic.

## Composer rule

For each topic folder, the composer picks the override subfolder
matching the current PBI type if present, otherwise the topic root.
Override semantics are **strict replace** — the override owns the
whole topic's content; it does not merge with the root.

## How to iterate

1. **Edit** the relevant topic file. Locate the topic that owns the
   content you want to change — use the layout above as a map.
2. **Run lints + structural tests.**

   ```
   uv run pytest tests/test_prompt_topic_shape.py tests/test_prompt_no_duplication.py tests/test_prompt_structure.py tests/test_prompt_contents.py tests/test_prompt_composer.py -v
   ```

3. **Optional opt-in smoke** against the sample feature PBI:

   ```
   RALPH_PROMPT_SMOKE=1 uv run pytest tests/test_prompt_smoke.py -v
   ```

4. **Commit** with a conventional message:

   ```
   git add prompt/0N-topic/
   git commit -m "feat(prompt): <one-line summary of the behaviour change>"
   ```

## What goes in `prompt/` vs the PBI directory vs `INVESTIGATE.md`

| Information | Lives in | Why |
|---|---|---|
| "How Ralph works in general" — the loop, the read order, the safety lines, the PR shipping protocol | `prompt/0N-topic/` | Stable across PBIs and services. |
| "What this specific PBI is asking for" — context, acceptance criteria, the step-by-step plan | `.ralph/current/<PBI-id>/PBI.md` and `PLAN.md` (or `BUG.md`/`REPRODUCE.md`, or `FEEDBACK.md`) | Per-PBI. Written by humans (`ralph-new`) or by the sweep (feedback). |
| "How THIS service works" — key modules, log format, common gotchas | `docs/INVESTIGATE.md` in the service repo | Per-service. Written by the service's engineers. |

## Single-source-of-truth invariant

If you find the same rule appearing in two override files of the same
topic, the lint will fail — hoist that content to the topic root
instead, where it applies to all types. If a topic has shared content
plus per-type tweaks, draw the topic boundary finer: split into two
topics (one shared, one with overrides). Never duplicate.

## When to bump the structural test

The structural test (`tests/test_prompt_structure.py`) pins specific
headings and phrases per PBI type. If you rename a heading or drop a
phrase intentionally, update the test in the same commit.
```

- [ ] **Step 2: Run the lints again as a sanity check**

Run: `uv run pytest tests/test_prompt_topic_shape.py tests/test_prompt_no_duplication.py tests/test_prompt_structure.py tests/test_prompt_contents.py tests/test_prompt_composer.py -v`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add prompt/README.md
git commit -m "docs(prompt-split): rewrite README for topic-folder layout"
```

---

### Task 23: Full test suite + opt-in smoke verification

**Files:** (none)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -v --ignore=tests/test_prompt_smoke.py`
Expected: All PASS. No new failures, no regressions.

- [ ] **Step 2: Run the opt-in subprocess smoke (requires network + `claude` CLI)**

Run: `RALPH_PROMPT_SMOKE=1 uv run pytest tests/test_prompt_smoke.py -v`
Expected: PASS. If `claude` is not available locally, document the skip and run this on a host that has it (e.g. the ROSA dev pod).

- [ ] **Step 3: Manual 3-PBI smoke against a real workspace**

This is the residual-risk R2 retirement step from the spec.

Pick three sample PBIs (one of each type — feature, bug, pr-feedback) from `samples/` and run a single iteration against each via the executor:

```bash
# Pseudo — exact commands depend on the operator's setup:
RALPH_HOME=/tmp/smoke-prompt-split python -m ralph_executor.cli iterate --once
```

For each iteration, verify:

- Claude reads the relevant PBI files (use stream-json output).
- Claude advances the PBI by one meaningful step.
- For a feature/bug iteration, the PR-feedback-only content (elevated-attention, reviewer-tone) does NOT appear in Claude's working context. Verify by inspecting the `--append-system-prompt` value with a debug print or by reading the executor's stream-json output if available.

Document the result in `docs/superpowers/specs/2026-05-31-prompt-md-split-design.md` under a new "Smoke verification" section, or in the PR description.

- [ ] **Step 4: Optional one-time sanity diff (R1 retirement)**

Compare the assembled feature prompt against the git history's last `prompt/PROMPT.md` to confirm nothing of substance was dropped:

```bash
git show HEAD~22:prompt/PROMPT.md > /tmp/old-prompt.md
uv run python -c "from pathlib import Path; from ralph_executor.prompt_composer import compose_prompt; print(compose_prompt(Path('prompt'), 'feature'))" > /tmp/new-feature-prompt.md
diff -u /tmp/old-prompt.md /tmp/new-feature-prompt.md | less
```

(Adjust `HEAD~22` to point at the commit that deleted `PROMPT.md`.)

Expected: differences are the feedback-only sections (elevated-attention, reviewer-tone, feedback workflow, etc.) absent from the feature assembly, and the iteration-start "Read PROMPT.md" reference replaced with the new system-prompt phrasing. No load-bearing rule should be missing.

- [ ] **Step 5: No commit (verification only)**

Verification step. If everything passes, the implementation is done.

---

## Self-review checklist

After all tasks complete, verify:

- [ ] `prompt/PROMPT.md` is deleted; the topic tree under `prompt/0N-topic/` is the source of truth.
- [ ] `ralph_executor/prompt_composer.py` exists with `VALID_TYPES`, `PromptComposeError`, `compose_prompt`.
- [ ] `ralph_executor/claude_spawn.py::_build_argv` uses `--append-system-prompt` and the shorter `-p` directive.
- [ ] `tests/test_prompt_composer.py`, `tests/test_prompt_topic_shape.py`, `tests/test_prompt_no_duplication.py` are new and passing.
- [ ] `tests/test_prompt_structure.py`, `tests/test_prompt_contents.py`, `tests/test_prompt_smoke.py` are rewritten and passing.
- [ ] `prompt/README.md` is rewritten.
- [ ] Manual 3-PBI smoke ran successfully across feature, bug, and pr-feedback types.
- [ ] No copy-paste between override files (anti-duplication lint green).
- [ ] All topic folders match the numeric-prefix convention (topic-shape lint green).
