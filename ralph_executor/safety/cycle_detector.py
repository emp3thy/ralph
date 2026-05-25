"""Stub — implemented in Task 4."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ralph_executor.safety.events import Event


class SignalKind(StrEnum):
    SIGNATURE_RECURRENCE = "signature_recurrence"
    WHACK_A_MOLE = "whack_a_mole"
    SAME_FILE_THRASHING = "same_file_thrashing"
    REGRESSION_CASCADE = "regression_cascade"
    ATTEMPT_DIVERGENCE = "attempt_divergence"
    BLOCKED_GROWTH = "blocked_growth"


@dataclass(frozen=True)
class CycleSignal:
    kind: SignalKind
    severity: str
    description: str
    supporting_event_ids: list[int] = field(default_factory=list)


def evaluate_signature_recurrence(events: list[Event], now: datetime) -> CycleSignal | None:
    raise NotImplementedError


def evaluate_whack_a_mole(events: list[Event], now: datetime) -> CycleSignal | None:
    raise NotImplementedError


def evaluate_same_file_thrashing(events: list[Event], now: datetime) -> CycleSignal | None:
    raise NotImplementedError


def evaluate_regression_cascade(events: list[Event], now: datetime) -> CycleSignal | None:
    raise NotImplementedError


def evaluate_attempt_divergence(events: list[Event], now: datetime) -> CycleSignal | None:
    raise NotImplementedError


def evaluate_blocked_growth(events: list[Event], now: datetime) -> CycleSignal | None:
    raise NotImplementedError


def evaluate_all(events: list[Event], now: datetime) -> list[CycleSignal]:
    raise NotImplementedError
