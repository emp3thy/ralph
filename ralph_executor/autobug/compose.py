"""Defensive composer for BUG.md / REPRODUCE.md.

Every section runs through ``_safe``: section failures log a structured
warning and substitute a fallback string but never propagate. This
guarantees the autobug emit never crashes the original-crash handler.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

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
