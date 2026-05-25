"""Shared fixtures for the safety test package."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ralph_executor.safety.events import Event, EventLog, EventType, open_log

RALPH_DIRS = (
    ".ralph/inbox",
    ".ralph/current",
    ".ralph/pending-pr",
    ".ralph/done",
    ".ralph/blocked",
    ".ralph/state",
)


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    """Create an empty ``.ralph/`` skeleton under ``tmp_path``."""
    for relative in RALPH_DIRS:
        (tmp_path / relative).mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def event_log(repo_dir: Path) -> Iterator[EventLog]:
    log = open_log(repo_dir)
    try:
        yield log
    finally:
        log.close()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)


def make_event(
    *,
    kind: EventType,
    recorded_at: datetime,
    pbi_id: str = "WI-1",
    payload: dict[str, Any] | None = None,
) -> Event:
    return Event(
        kind=kind,
        recorded_at=recorded_at,
        pbi_id=pbi_id,
        payload=payload or {},
    )


def offset(now: datetime, **kwargs: int) -> datetime:
    """Return ``now`` offset by the given keyword duration (negative for past)."""
    return now + timedelta(**kwargs)


def write_pbi_dir(
    base: Path,
    *,
    bucket: str,
    pbi_id: str,
    frontmatter: dict[str, Any] | None = None,
    body: str = "",
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Write a minimal PBI directory under ``base/.ralph/<bucket>/<pbi_id>/``."""
    pbi_dir = base / ".ralph" / bucket / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=True)
    fm = dict(frontmatter or {})
    fm.setdefault("id", pbi_id)
    fm.setdefault("type", "feature")
    fm.setdefault("status", bucket)
    fm.setdefault("severity", "normal")
    fm.setdefault("attempts", 0)
    front = "---\n"
    for key, value in fm.items():
        if isinstance(value, str):
            front += f"{key}: {value}\n"
        else:
            front += f"{key}: {json.dumps(value)}\n"
    front += "---\n\n"
    (pbi_dir / "PBI.md").write_text(front + body, encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    for name, content in (extra_files or {}).items():
        (pbi_dir / name).write_text(content, encoding="utf-8")
    return pbi_dir
