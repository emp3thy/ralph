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
