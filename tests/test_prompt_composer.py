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
