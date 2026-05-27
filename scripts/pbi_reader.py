"""Shared PBI directory reader used by ``ralph-status`` (and reusable).

The canonical PBI schema lives in ``scripts.validate_samples``; this
module imports those constants rather than redefining them, so any
future schema change made in Plan 1 propagates here without edits.

The reader is deliberately tolerant: malformed PBI directories are
surfaced as ``PBIRowError`` records rather than raising. Callers
(e.g. ``skills/ralph-status/scripts/show.py``) decide whether to
display, log, or ignore the errors.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from scripts.validate_samples import (
    ALLOWED_SEVERITIES,
    ALLOWED_STATUSES,
    ALLOWED_TYPES,
    ENTRY_FILE_BY_TYPE,
    REQUIRED_FRONTMATTER_FIELDS,
)

STATE_FOLDERS: tuple[str, ...] = (
    "inbox",
    "current",
    "pending-pr",
    "done",
    "blocked",
)

# Entry-file detection order when the directory is on the queue (not in
# ``samples/``). On the queue the directory name is the canonical
# ``WI-<n>`` form, so we cannot infer type from the directory name —
# instead we try each entry filename in priority order and pick the
# first one that exists.
_ENTRY_FILE_PROBE_ORDER: tuple[str, ...] = ("PBI.md", "BUG.md", "FEEDBACK.md")


def _split_frontmatter_with_body(text: str) -> tuple[str, str] | None:
    """Return ``(frontmatter_yaml, body)`` or ``None`` if no fence present.

    Fence lines must be EXACTLY ``---`` (no surrounding whitespace) — same
    rule as :func:`scripts.validate_samples.split_frontmatter`. The body is
    everything after the closing fence.
    """
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    for idx in range(1, len(lines)):
        if lines[idx] == "---":
            frontmatter = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1 :])
            return frontmatter, body
    return None


@dataclass(frozen=True)
class PBIRow:
    """A successfully-parsed PBI from a queue folder."""

    repo_path: Path
    repo_name: str
    state: str
    pbi_dir: Path
    pbi_id: str
    pbi_type: str
    severity: str
    attempts: int
    created_at: datetime | None
    updated_at: datetime | None
    title: str

    def relative_pbi_dir(self) -> str:
        try:
            return str(self.pbi_dir.relative_to(self.repo_path)).replace("\\", "/")
        except ValueError:
            return str(self.pbi_dir).replace("\\", "/")


@dataclass(frozen=True)
class PBIRowError:
    """A PBI directory we could not parse."""

    repo_path: Path
    repo_name: str
    state: str
    pbi_dir: Path
    message: str

    def relative_pbi_dir(self) -> str:
        try:
            return str(self.pbi_dir.relative_to(self.repo_path)).replace("\\", "/")
        except ValueError:
            return str(self.pbi_dir).replace("\\", "/")


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _extract_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
    return fallback


def _detect_entry_file(pbi_dir: Path) -> Path | None:
    for name in _ENTRY_FILE_PROBE_ORDER:
        candidate = pbi_dir / name
        if candidate.is_file():
            return candidate
    return None


def read_pbi(
    pbi_dir: Path,
    *,
    repo_path: Path,
    repo_name: str,
    state: str,
) -> PBIRow | PBIRowError:
    """Read a single PBI directory; tolerant of malformed input."""
    if not pbi_dir.is_dir():
        return PBIRowError(
            repo_path=repo_path,
            repo_name=repo_name,
            state=state,
            pbi_dir=pbi_dir,
            message=f"not a directory: {pbi_dir}",
        )

    entry_file = _detect_entry_file(pbi_dir)
    if entry_file is None:
        return PBIRowError(
            repo_path=repo_path,
            repo_name=repo_name,
            state=state,
            pbi_dir=pbi_dir,
            message=(f"no entry file (expected one of {list(_ENTRY_FILE_PROBE_ORDER)})"),
        )

    try:
        text = entry_file.read_text(encoding="utf-8")
    except OSError as exc:
        return PBIRowError(
            repo_path=repo_path,
            repo_name=repo_name,
            state=state,
            pbi_dir=pbi_dir,
            message=f"failed to read {entry_file.name}: {exc}",
        )

    split = _split_frontmatter_with_body(text)
    if split is None:
        return PBIRowError(
            repo_path=repo_path,
            repo_name=repo_name,
            state=state,
            pbi_dir=pbi_dir,
            message=(f"{entry_file.name} has no leading YAML frontmatter block"),
        )

    frontmatter_yaml, body = split
    try:
        parsed: Any = yaml.safe_load(frontmatter_yaml)
    except yaml.YAMLError as exc:
        return PBIRowError(
            repo_path=repo_path,
            repo_name=repo_name,
            state=state,
            pbi_dir=pbi_dir,
            message=f"frontmatter YAML is invalid: {exc}",
        )

    if not isinstance(parsed, Mapping):
        return PBIRowError(
            repo_path=repo_path,
            repo_name=repo_name,
            state=state,
            pbi_dir=pbi_dir,
            message=(f"frontmatter is not a YAML mapping (got {type(parsed).__name__})"),
        )

    missing = [f for f in REQUIRED_FRONTMATTER_FIELDS if f not in parsed]
    if missing:
        return PBIRowError(
            repo_path=repo_path,
            repo_name=repo_name,
            state=state,
            pbi_dir=pbi_dir,
            message=f"frontmatter missing required fields: {sorted(missing)}",
        )

    pbi_type = parsed.get("type")
    if pbi_type not in ALLOWED_TYPES:
        return PBIRowError(
            repo_path=repo_path,
            repo_name=repo_name,
            state=state,
            pbi_dir=pbi_dir,
            message=(f"type={pbi_type!r} not in allowed set {sorted(ALLOWED_TYPES)}"),
        )

    severity = parsed.get("severity")
    if severity not in ALLOWED_SEVERITIES:
        return PBIRowError(
            repo_path=repo_path,
            repo_name=repo_name,
            state=state,
            pbi_dir=pbi_dir,
            message=(f"severity={severity!r} not in allowed set {sorted(ALLOWED_SEVERITIES)}"),
        )

    status_value = parsed.get("status")
    if status_value is not None and status_value not in ALLOWED_STATUSES:
        return PBIRowError(
            repo_path=repo_path,
            repo_name=repo_name,
            state=state,
            pbi_dir=pbi_dir,
            message=(f"status={status_value!r} not in allowed set {sorted(ALLOWED_STATUSES)}"),
        )

    # bool is a subclass of int in Python — `isinstance(True, int)` is
    # True. Without the explicit bool exclusion, a frontmatter line like
    # `attempts: true` would survive validation and propagate as `True`
    # into JSON output (`"attempts": true` instead of an integer),
    # breaking callers that expect a numeric field.
    attempts_raw = parsed.get("attempts")
    attempts = (
        attempts_raw if isinstance(attempts_raw, int) and not isinstance(attempts_raw, bool) else 0
    )

    pbi_id_raw = parsed.get("id")
    pbi_id = pbi_id_raw if isinstance(pbi_id_raw, str) and pbi_id_raw.strip() else pbi_dir.name

    return PBIRow(
        repo_path=repo_path,
        repo_name=repo_name,
        state=state,
        pbi_dir=pbi_dir,
        pbi_id=pbi_id,
        pbi_type=str(pbi_type),
        severity=str(severity),
        attempts=attempts,
        created_at=_coerce_datetime(parsed.get("created_at")),
        updated_at=_coerce_datetime(parsed.get("updated_at")),
        title=_extract_title(body, fallback=pbi_id),
    )


def enumerate_state(
    repo_root: Path,
    state: str,
    *,
    repo_name: str | None = None,
) -> list[PBIRow | PBIRowError]:
    """Walk ``.ralph/<state>/`` under ``repo_root`` and read every PBI.

    Returns an empty list when the state folder does not exist (which
    is a normal condition — a brand-new queue may not have created
    ``done/`` yet).
    """
    if state not in STATE_FOLDERS:
        raise ValueError(f"unknown state {state!r}; expected one of {STATE_FOLDERS}")
    state_dir = repo_root / ".ralph" / state
    if not state_dir.is_dir():
        return []
    effective_repo_name = repo_name or repo_root.name
    rows: list[PBIRow | PBIRowError] = []
    for child in sorted(state_dir.iterdir()):
        if not child.is_dir():
            continue
        rows.append(
            read_pbi(
                child,
                repo_path=repo_root,
                repo_name=effective_repo_name,
                state=state,
            )
        )
    return rows


__all__ = [
    "ENTRY_FILE_BY_TYPE",
    "PBIRow",
    "PBIRowError",
    "STATE_FOLDERS",
    "enumerate_state",
    "read_pbi",
]
