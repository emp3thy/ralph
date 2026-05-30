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
