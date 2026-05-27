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


def test_pbi_carries_target_repo_and_target_info_fields() -> None:
    """PBI dataclass has target_repo (raw string), target_info (parsed),
    and work_worktree (per-PBI worktree path)."""
    from ralph_executor.url_utils import TargetRepoInfo

    pbi = PBI(
        id="WI-1",
        type="feature",
        status="inbox",
        severity="normal",
        attempts=0,
        created_at=datetime(2026, 5, 24, 9, 15, tzinfo=UTC),
        updated_at=datetime(2026, 5, 24, 9, 15, tzinfo=UTC),
        path=Path("/tmp/x"),
        target_repo="https://github.com/emp3thy/ralph",
        target_info=TargetRepoInfo(host="github.com", owner="emp3thy", name="ralph"),
        work_worktree=Path("/tmp/clone/.ralph-work/WI-1"),
    )
    assert pbi.target_repo == "https://github.com/emp3thy/ralph"
    assert pbi.target_info is not None
    assert pbi.target_info.owner == "emp3thy"
    assert pbi.work_worktree == Path("/tmp/clone/.ralph-work/WI-1")
