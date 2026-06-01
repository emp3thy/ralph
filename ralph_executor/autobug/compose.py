"""Defensive composer for BUG.md / REPRODUCE.md.

Every section runs through ``_safe``: section failures log a structured
warning and substitute a fallback string but never propagate. This
guarantees the autobug emit never crashes the original-crash handler.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ralph_executor.autobug.types import Context

log = logging.getLogger(__name__)


def _safe[T](fn: Callable[[], T], fallback: T) -> T:
    try:
        return fn()
    except Exception as exc:
        log.warning(
            "autobug.compose section failed: %s",
            exc,
            extra={"section_failed": True},
        )
        return fallback


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body}"


def build_frontmatter(
    *,
    pbi_id: str,
    signature: str,
    trigger_kind: str,
    severity: str,
    ctx: Context,
    target_repo: str,
    regression_of: str | None = None,
) -> str:
    """Compose the YAML frontmatter block for an autobug BUG.md.

    Load-bearing fields (id, type, signature, target_repo) cannot fall
    back — caller (detect.py) aborts emission earlier if they're missing.
    """
    now_iso = ctx.now.replace(microsecond=0).isoformat()
    lines = [
        "---",
        f"id: {pbi_id}",
        "type: bug",
        f"severity: {severity}",
        "status: inbox",
        "attempts: 0",
        f"created_at: {now_iso}",
        f"updated_at: {now_iso}",
        f"target_repo: {target_repo}",
        f"signature: {signature}",
        f"trigger_kind: {trigger_kind}",
        "occurrences: 1",
        f"first_seen: {now_iso}",
        f"last_seen: {now_iso}",
        f"regression_of: {regression_of if regression_of else 'null'}",
        f"triggering_pbi: {ctx.triggering_pbi_id if ctx.triggering_pbi_id else 'null'}",
        f"ralph_sha: {ctx.ralph_sha}",
        "---",
        "",
    ]
    return "\n".join(lines)
