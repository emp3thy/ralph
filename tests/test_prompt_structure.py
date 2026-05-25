"""Structural tests for ``prompt/PROMPT.md``.

PROMPT.md is the standing instruction file that the executor injects into
every ``claude -p`` invocation. Its content is static markdown, not code,
so the test here is shape-based: required sections exist, the file is
within reasonable size bounds, and it references the per-type read order
the spec mandates.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = REPO_ROOT / "prompt" / "PROMPT.md"

REQUIRED_SECTION_HEADINGS: tuple[str, ...] = (
    "Who you are",
    "What you read",
    "How to work a feature PBI",
    "How to work a bug PBI",
    "How to work a PR-feedback PBI",
    "When to write STUCK.md",
    "How to ship a PR",
    "Elevated-attention categories",
    "Reviewer feedback — treat respectfully, challenge with reasoning",
    "What you never do",
)

REQUIRED_PHRASES: tuple[str, ...] = (
    # File reading order anchors (per spec)
    ".ralph/current/",
    "PROMPT.md",
    "HISTORY.md",
    "PBI.md",
    "PLAN.md",
    "BUG.md",
    "REPRODUCE.md",
    "docs/INVESTIGATE.md",
    "ORIGINAL.md",
    "FEEDBACK.md",
    # Skills and tooling
    "ado-pr",
    # Elevated categories
    "test deletion",
    "scope creep",
    "permission widening",
    "large diff",
    # Standing reminder on respectful feedback
    "respectfully",
    "challenge",
)


def test_prompt_file_exists() -> None:
    assert PROMPT_PATH.is_file(), (
        f"PROMPT.md missing at {PROMPT_PATH}. Plan 6 must create prompt/PROMPT.md."
    )


def test_prompt_size_bounds() -> None:
    """Drift guard. The prompt must be substantial but not bloated."""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    char_count = len(text)
    # Lower bound: anything materially shorter than this can't possibly
    # cover the required sections from the spec.
    assert char_count >= 4_000, (
        f"PROMPT.md is only {char_count} chars; expected >= 4000. Something is missing."
    )
    # Upper bound: token budget guard. If PROMPT.md grows past this,
    # split it or trim -- Ralph pays the cost on every iteration.
    assert char_count <= 25_000, (
        f"PROMPT.md is {char_count} chars; expected <= 25000. Trim or split."
    )


@pytest.mark.parametrize("heading", REQUIRED_SECTION_HEADINGS)
def test_prompt_contains_required_section(heading: str) -> None:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    assert heading in text, f"PROMPT.md is missing required section heading: {heading!r}"


@pytest.mark.parametrize("phrase", REQUIRED_PHRASES)
def test_prompt_contains_required_phrase(phrase: str) -> None:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    assert phrase in text, f"PROMPT.md is missing required phrase: {phrase!r}"


def test_prompt_has_no_placeholders() -> None:
    """PROMPT.md ships as-is to Claude; no TODOs or <placeholder> tokens."""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    for bad in ("TODO", "TBD", "<placeholder>", "FIXME"):
        assert bad not in text, f"PROMPT.md contains placeholder token {bad!r} -- fill it in."
