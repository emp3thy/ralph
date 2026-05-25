"""Stub — implemented in Task 5."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ralph_executor.safety.cycle_detector import CycleSignal


class HaltStatus(StrEnum):
    RUNNING = "running"
    HALTED = "halted"
    ACKNOWLEDGED = "acknowledged"


class HaltedError(Exception):
    pass


@dataclass(frozen=True)
class StateSnapshot:
    repo: Path
    current_pbi_ids: list[str]
    pending_count: int
    blocked_count: int


@dataclass(frozen=True)
class MetaBug:
    id: str
    path: Path
    signals: list[CycleSignal]


def snapshot_state(repo: Path) -> StateSnapshot:
    raise NotImplementedError


def write_meta_bug(repo: Path, snapshot: StateSnapshot, signals: list[CycleSignal]) -> Path:
    raise NotImplementedError


def write_halt_sentinel(repo: Path, meta_bug_id: str) -> Path:
    raise NotImplementedError


def check_halt_sentinel(repo: Path) -> HaltStatus:
    raise NotImplementedError


def notify_halt(meta_bug: MetaBug, webhook_url: str | None = None) -> None:
    raise NotImplementedError


def halt_and_acknowledge(
    repo: Path,
    signals: list[CycleSignal],
    *,
    webhook_env: str = "RALPH_HALT_WEBHOOK",
) -> MetaBug:
    raise NotImplementedError
