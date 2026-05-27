"""Check protocol and registry for the ``ralph-doctor`` skill.

Every check module under ``scripts.checks`` exports a single
function:

    def check(context: CheckContext) -> CheckResult: ...

The runner calls every module listed in :data:`REGISTRY` in order and
collects the :class:`CheckResult` for each.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Severity = Literal["error", "warn"]
Status = Literal["pass", "fail", "skipped"]


@dataclass(frozen=True)
class CheckContext:
    settings_path: Path
    skills_dir: Path
    strict: bool = False
    git_host: str = ""
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckResult:
    name: str
    severity: Severity
    status: Status
    message: str
    details: dict[str, object] = field(default_factory=dict)


REGISTRY: tuple[str, ...] = (
    "permissions",
    "hooks",
    "skills",
    "mcp",
    "auth",
    "host_staging",
    "github_auth",
    "ado_auth",
)
"""Module names under ``scripts.checks`` to execute, in order.

Ordering rationale:
1. Settings-only checks (permissions / hooks / skills / mcp) first —
   cheap and offline.
2. ``auth`` — Anthropic (or Bedrock) probe; the AI surface must work
   before anything else matters.
3. ``host_staging`` — host-agnostic gate that verifies the staged
   skill bundle matches ``RALPH_GIT_HOST``. Cheap (filesystem read).
4. ``github_auth`` and ``ado_auth`` — host-specific network probes.
   The runner skips whichever does not match ``RALPH_GIT_HOST``.
"""

HOST_AUTH_CHECKS: dict[str, str] = {
    "github": "github_auth",
    "ado": "ado_auth",
}
"""Map of ``RALPH_GIT_HOST`` value to the host-auth check module name."""
