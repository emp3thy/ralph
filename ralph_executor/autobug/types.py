"""Frozen dataclasses shared across the autobug package."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Context:
    """Carries per-emission state to every autobug step.

    All callers (cli outer wrapper, loop wires, claude_spawn subprocess
    wire) build this same shape so the signature/dedup/emit pipeline
    never reaches back into ExecutorConfig directly.
    """

    queue_root: Path
    state_dir: Path
    env: Mapping[str, str]
    now: datetime
    ralph_sha: str
    bot_author_email: str
    triggering_pbi_id: str | None
    queue_branch: str


@dataclass(frozen=True)
class DedupResult:
    """Outcome of a dedup lookup against queue state."""

    kind: Literal["new", "bump_existing", "reopen_regression"]
    existing_pbi_id: str | None = None
