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
