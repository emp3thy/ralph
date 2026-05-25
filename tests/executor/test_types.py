"""Tests for ``ralph_executor.types``.

The types are the shared cross-plan contract -- Plans 8, 9, and 10 import
the same module. These tests pin the spelling and the immutability.
"""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.types import (
    PBI,
    PBIStatus,
    PBIType,
    Severity,
)


def _make_pbi(**overrides: object) -> PBI:
    base: dict[str, object] = {
        "id": "WI-1234",
        "type": "feature",
        "status": "inbox",
        "severity": "normal",
        "attempts": 0,
        "created_at": datetime(2026, 5, 24, 9, 15, tzinfo=UTC),
        "updated_at": datetime(2026, 5, 24, 9, 15, tzinfo=UTC),
        "path": Path("/tmp/ralph/inbox/WI-1234"),
    }
    base.update(overrides)
    return PBI(**base)  # type: ignore[arg-type]


def test_pbi_constructs_with_canonical_fields() -> None:
    pbi = _make_pbi()
    assert pbi.id == "WI-1234"
    assert pbi.type == "feature"
    assert pbi.status == "inbox"
    assert pbi.severity == "normal"
    assert pbi.attempts == 0
    assert pbi.path == Path("/tmp/ralph/inbox/WI-1234")


def test_pbi_is_frozen() -> None:
    pbi = _make_pbi()
    with pytest.raises(dataclasses.FrozenInstanceError):
        pbi.id = "WI-9999"  # type: ignore[misc]


def test_pbi_equality_uses_all_fields() -> None:
    a = _make_pbi()
    b = _make_pbi()
    assert a == b
    c = _make_pbi(id="WI-5555")
    assert a != c


def test_literal_aliases_are_exported() -> None:
    # Reaching the names is the assertion -- mypy enforces the literal values.
    assert PBIType is not None
    assert PBIStatus is not None
    assert Severity is not None
