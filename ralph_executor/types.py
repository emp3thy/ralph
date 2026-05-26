"""Shared typed shapes for the executor and its extensions.

Plans 8 (sweep), 9 (safety controls), and 10 (supervisor skills) import
``PBI`` from this module rather than re-defining it locally. The Literal
aliases mirror the canonical schema in
``docs/superpowers/plans/2026-05-24-00-orchestrator.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

PBIType = Literal["feature", "bug", "pr-feedback"]
PBIStatus = Literal["inbox", "current", "pending-pr", "done", "blocked", "archive"]
Severity = Literal["critical", "high", "normal", "low"]


@dataclass(frozen=True)
class PBI:
    """A Product Backlog Item as it lives on disk under ``.ralph/<state>/``.

    ``path`` is the absolute path to the PBI's directory in the queue
    checkout -- i.e. ``<repo>/.ralph/<status>/<id>/``.

    ``depends_on`` lists IDs of other PBIs that must reach ``done/``
    before this one is eligible for ``pick_next``. Optional — default
    is an empty tuple. Cycles are NOT detected by the queue source;
    operator responsibility (the dependency graph is small and static).
    """

    id: str
    type: PBIType
    status: PBIStatus
    severity: Severity
    attempts: int
    created_at: datetime
    updated_at: datetime
    path: Path
    depends_on: tuple[str, ...] = ()
