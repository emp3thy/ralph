"""Scan queue state for matching signature; returns kind: new/bump/reopen."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from ralph_executor.autobug.types import DedupResult

__all__ = ["DedupResult", "lookup"]

_SIG_RE = re.compile(r"^signature:\s*([0-9a-f]+)\s*$", re.MULTILINE)
_CLOSED_RE = re.compile(r"^closed_at:\s*(\S+)\s*$", re.MULTILINE)
_ID_RE = re.compile(r"^id:\s*(\S+)\s*$", re.MULTILINE)

_ENTRY_FILES = ("BUG.md", "PBI.md", "FEEDBACK.md")


def _find_by_signature(state_dir: Path, signature: str) -> str | None:
    if not state_dir.is_dir():
        return None
    for child in sorted(state_dir.iterdir()):
        if not child.is_dir():
            continue
        for entry_name in _ENTRY_FILES:
            entry = child / entry_name
            if not entry.is_file():
                continue
            try:
                text = entry.read_text(encoding="utf-8")
            except OSError:
                continue
            m = _SIG_RE.search(text)
            if m and m.group(1) == signature:
                id_m = _ID_RE.search(text)
                return id_m.group(1) if id_m else child.name
    return None


def _find_by_signature_since(
    state_dir: Path, signature: str, cutoff: datetime
) -> str | None:
    if not state_dir.is_dir():
        return None
    for child in sorted(state_dir.iterdir()):
        if not child.is_dir():
            continue
        for entry_name in _ENTRY_FILES:
            entry = child / entry_name
            if not entry.is_file():
                continue
            try:
                text = entry.read_text(encoding="utf-8")
            except OSError:
                continue
            m = _SIG_RE.search(text)
            if not m or m.group(1) != signature:
                continue
            closed_m = _CLOSED_RE.search(text)
            if not closed_m:
                continue
            try:
                closed = datetime.fromisoformat(closed_m.group(1))
            except ValueError:
                continue
            if closed >= cutoff:
                id_m = _ID_RE.search(text)
                return id_m.group(1) if id_m else child.name
    return None


def lookup(signature: str, queue_root: Path, now: datetime) -> DedupResult:
    for state in ("inbox", "current", "pending-pr"):
        match = _find_by_signature(queue_root / ".ralph" / state, signature)
        if match:
            return DedupResult(kind="bump_existing", existing_pbi_id=match)
    cutoff = now - timedelta(days=30)
    match = _find_by_signature_since(
        queue_root / ".ralph" / "done", signature, cutoff
    )
    if match:
        return DedupResult(kind="reopen_regression", existing_pbi_id=match)
    return DedupResult(kind="new")
