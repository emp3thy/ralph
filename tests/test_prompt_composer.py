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
    VALID_TYPES,
    PromptComposeError,
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
    assert frozenset({"feature", "bug", "pr-feedback"}) == VALID_TYPES


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
    assert len(out_feature) > 4_000
    assert len(out_feedback) > 4_000
    assert len(out_feature) < len(out_feedback)
