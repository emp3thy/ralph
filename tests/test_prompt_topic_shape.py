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
    if not PROMPT_ROOT.is_dir():
        return []
    return sorted(p for p in PROMPT_ROOT.iterdir() if p.is_dir())


@pytest.mark.parametrize("topic", _topic_dirs(), ids=lambda p: p.name)
def test_topic_folder_name_matches_convention(topic: Path) -> None:
    assert TOPIC_NAME.match(topic.name), f"topic folder {topic.name!r} must match ^NN-kebab-name"


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
