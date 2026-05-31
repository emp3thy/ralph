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
)
PHRASES_BUG_ONLY: tuple[str, ...] = (
    "BUG.md",
    "REPRODUCE.md",
    "docs/INVESTIGATE.md",
)
# Elevated-attention categories were inlined into the pr-feedback workflow
# override in Task 10 — they describe reviewer-comment handling and do not
# apply to feature/bug iterations.
PHRASES_PR_FEEDBACK_ONLY: tuple[str, ...] = (
    "ORIGINAL.md",
    "FEEDBACK.md",
    "respectfully",
    "challenge",
    "test deletion",
    "permission widening",
    "large diff",
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
    assert 4_000 <= n <= 25_000, f"assembled {pbi_type} prompt is {n} chars (must be 4k-25k)"
