"""Guard rails for the canonical prompt/PROMPT.md wording.

Specific phrases here are load-bearing: ralph-new's design depends on
target_repo being documented as REQUIRED (the loop already enforces it).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT = (REPO_ROOT / "prompt" / "PROMPT.md").read_text(encoding="utf-8")


def test_target_repo_documented_as_required() -> None:
    assert "MUST carry a `target_repo`" in PROMPT
    assert "canonical" in PROMPT.lower()


def test_no_stale_informational_only_wording() -> None:
    assert "informational metadata for now" not in PROMPT
    assert "the loop does not yet act on it" not in PROMPT
