"""Stub — implemented in Task 3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ralph_executor.safety.events import Event


@dataclass(frozen=True)
class StuckOutcome:
    blocked_path: Path
    reason: str
    event: Event


def detect_stuck(pbi_dir: Path) -> bool:
    raise NotImplementedError


def read_stuck_reason(pbi_dir: Path) -> str:
    raise NotImplementedError


def move_to_blocked(*, repo: Path, pbi_dir: Path, reason: str) -> Path:
    raise NotImplementedError


def handle_stuck(*, repo: Path, pbi_dir: Path, now: datetime) -> StuckOutcome | None:
    raise NotImplementedError
