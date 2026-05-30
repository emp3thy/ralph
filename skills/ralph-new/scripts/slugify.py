"""Derive a PBI slug from a free-form title; resolve queue-side collisions.

Slug rules (must round-trip through ``validate.validate_pbi_id``):

- Uppercase A-Z, digits 0-9, hyphens only
- 2-80 chars
- No leading or trailing hyphen
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_MAX_SLUG_LEN = 80
_MAX_COLLISION_SUFFIX = 10

_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")


def slug_from_title(title: str) -> str:
    """Convert a human title into a canonical PBI slug.

    Raises ``ValueError`` for empty input or input that collapses to no
    alphanumeric characters (e.g. ``"???"``).
    """
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is empty")
    parts = [p for p in _SEPARATOR.split(title) if p]
    if not parts:
        raise ValueError(f"title has no valid characters for a slug: {title!r}")
    slug = "-".join(p.upper() for p in parts)
    if len(slug) > _MAX_SLUG_LEN:
        slug = slug[:_MAX_SLUG_LEN].rstrip("-")
    return slug


def resolve_collision(base_slug: str, *, existing: Iterable[str]) -> str:
    """Return ``base_slug`` if free, else the first free ``base_slug-N`` (N=2..10).

    Raises ``ValueError`` after exhausting suffixes 2-10 inclusive.
    """
    existing_set = set(existing)
    if base_slug not in existing_set:
        return base_slug
    for suffix in range(2, _MAX_COLLISION_SUFFIX + 1):
        candidate = f"{base_slug}-{suffix}"
        if candidate not in existing_set:
            return candidate
    raise ValueError(f"too many collisions on {base_slug!r}; rerun with --id <unique-slug>")
