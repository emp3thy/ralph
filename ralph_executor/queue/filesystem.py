"""Filesystem-backed queue source.

Reads PBI directories from ``.ralph/<state>/`` inside the queue clone at
``<workspace_root>/queue-<instance_id>/``. Parses the YAML frontmatter of the
type-appropriate entry file and returns ``PBI`` dataclasses. Sorts the
inbox by priority lane, then by ``created_at`` within the lane.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast, get_args

import yaml

from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.claim import CLAIM_FILENAME, ClaimError, read_claim
from ralph_executor.types import PBI, PBIStatus, PBIType, Severity

ENTRY_FILE_BY_TYPE: Mapping[str, str] = {
    "feature": "PBI.md",
    "bug": "BUG.md",
    "pr-feedback": "FEEDBACK.md",
}

# Severity lane ordering for non-pr-feedback PBIs (lower = higher priority).
_SEVERITY_RANK: Mapping[str, int] = {
    "critical": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
}

# PR-feedback PBIs always take priority over plain severity ordering, per
# the spec's "Priority lanes" section.
_PR_FEEDBACK_LANE_RANK = -1


class QueueError(RuntimeError):
    """Raised when the queue layout is malformed."""


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[1:idx]), "\n".join(lines[idx + 1 :])
    return None


def _detect_entry_file(pbi_dir: Path) -> tuple[str, str] | None:
    """Return ``(entry_filename, pbi_type)`` if exactly one entry file exists."""
    for pbi_type, entry_name in ENTRY_FILE_BY_TYPE.items():
        if (pbi_dir / entry_name).is_file():
            return entry_name, pbi_type
    return None


def _coerce_datetime(value: Any, field: str, pbi_dir: Path) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise QueueError(f"{pbi_dir}/{field}={value!r} is not ISO-8601: {exc}") from exc
    raise QueueError(
        f"{pbi_dir}/{field} must be a datetime or ISO-8601 string, got {type(value).__name__}"
    )


def parse_pbi_directory(pbi_dir: Path, *, status: str) -> PBI:
    """Parse the entry file of ``pbi_dir`` into a ``PBI`` dataclass.

    The ``status`` argument is the canonical state name (one of
    ``inbox``/``current``/``pending-pr``/``done``/``blocked``/``archive``);
    the caller knows it because it just read ``.ralph/<status>/`` from
    disk. The frontmatter's ``status`` field is also validated against
    this value when both are present.
    """
    if not pbi_dir.is_dir():
        raise QueueError(f"not a directory: {pbi_dir}")

    detected = _detect_entry_file(pbi_dir)
    if detected is None:
        raise QueueError(
            f"{pbi_dir}: no entry file (expected one of {sorted(ENTRY_FILE_BY_TYPE.values())})"
        )
    entry_name, detected_type = detected
    entry = pbi_dir / entry_name

    text = entry.read_text(encoding="utf-8")
    split = _split_frontmatter(text)
    if split is None:
        raise QueueError(f"{entry}: missing YAML frontmatter block")
    try:
        fm_any: Any = yaml.safe_load(split[0])
    except yaml.YAMLError as exc:
        raise QueueError(f"{entry}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(fm_any, Mapping):
        raise QueueError(
            f"{entry}: frontmatter must be a YAML mapping, got {type(fm_any).__name__}"
        )

    try:
        pbi_id = str(fm_any["id"]).strip()
        declared_type = str(fm_any["type"]).strip()
        severity_raw = str(fm_any["severity"]).strip()
        attempts = int(fm_any["attempts"])
        created_at = _coerce_datetime(fm_any["created_at"], "created_at", pbi_dir)
        updated_at = _coerce_datetime(fm_any["updated_at"], "updated_at", pbi_dir)
    except KeyError as exc:
        raise QueueError(f"{entry}: missing required field {exc}") from exc

    # depends_on is OPTIONAL. Default empty. Accept absent, null, or
    # an empty list. Reject anything else with a clear error so an
    # operator typo doesn't silently disable the dependency gate.
    raw_deps = fm_any.get("depends_on")
    depends_on: tuple[str, ...]
    if raw_deps is None:
        depends_on = ()
    elif isinstance(raw_deps, list) and all(isinstance(d, str) for d in raw_deps):
        depends_on = tuple(d.strip() for d in raw_deps if d.strip())
    else:
        raise QueueError(
            f"{entry}: depends_on must be a list of PBI-id strings, got {type(raw_deps).__name__}"
        )

    if declared_type != detected_type:
        raise QueueError(
            f"{entry}: frontmatter type={declared_type!r} disagrees with "
            f"on-disk entry file {entry_name!r} (type={detected_type!r})"
        )

    if declared_type not in get_args(PBIType):
        raise QueueError(f"{entry}: type={declared_type!r} not in {get_args(PBIType)}")
    if severity_raw not in get_args(Severity):
        raise QueueError(f"{entry}: severity={severity_raw!r} not in {get_args(Severity)}")
    if status not in get_args(PBIStatus):
        raise QueueError(f"caller passed status={status!r} not in {get_args(PBIStatus)}")

    return PBI(
        id=pbi_id,
        type=cast(PBIType, declared_type),
        status=cast(PBIStatus, status),
        severity=cast(Severity, severity_raw),
        attempts=attempts,
        created_at=created_at,
        updated_at=updated_at,
        path=pbi_dir,
        depends_on=depends_on,
    )


def _lane_rank(pbi: PBI) -> tuple[int, int, datetime]:
    """Sort key for inbox PBIs: lane, severity, then created_at."""
    lane: int = (
        _PR_FEEDBACK_LANE_RANK if pbi.type == "pr-feedback" else _SEVERITY_RANK[pbi.severity]
    )
    return (lane, _SEVERITY_RANK[pbi.severity], pbi.created_at)


class FilesystemQueueSource:
    """Reads PBI directories from ``.ralph/<state>/`` on disk."""

    def __init__(self, config: ExecutorConfig) -> None:
        self._config = config

    @property
    def _root(self) -> Path:
        return self._config.queue_clone_path / ".ralph"

    def _list_pbis(self, state: str) -> list[PBI]:
        state_dir = self._root / state
        if not state_dir.is_dir():
            return []
        pbis: list[PBI] = []
        for child in sorted(state_dir.iterdir()):
            if not child.is_dir():
                continue
            # Skip `.gitkeep` etc. by checking for at least one entry file.
            if _detect_entry_file(child) is None:
                continue
            pbis.append(parse_pbi_directory(child, status=state))
        return pbis

    def current_pbi(self) -> PBI | None:
        """Return THIS instance's claimed PBI in ``current/``, or None.

        Scans every ``current/<id>/`` directory and reads its
        ``CLAIM.json``. Returns the PBI whose claim ``instance_id``
        matches ``cfg.instance_id``. PBIs claimed by other instances are
        silently skipped — multiple ``current/<id>/`` directories are
        legal under multi-ralph (one per live instance fleet-wide).

        Raises ``QueueError`` if:

        * any ``current/<id>/`` is missing a ``CLAIM.json`` or the file
          is malformed (post-Scope-1 invariant: every claimed PBI carries
          a CLAIM.json); or
        * this instance owns more than one claim (one PBI per instance
          in flight at any time).
        """
        own: list[PBI] = []
        for pbi in self._list_pbis("current"):
            claim_path = pbi.path / CLAIM_FILENAME
            if not claim_path.exists():
                raise QueueError(f"malformed claim: {pbi.path} is missing CLAIM.json")
            try:
                claim = read_claim(claim_path)
            except ClaimError as exc:
                raise QueueError(f"malformed claim: {exc}") from exc
            if claim.instance_id == self._config.instance_id:
                own.append(pbi)
        if not own:
            return None
        if len(own) > 1:
            ids = sorted(p.id for p in own)
            raise QueueError(
                f"multiple own claims in current/ for instance_id="
                f"{self._config.instance_id!r}: {ids}"
            )
        return own[0]

    def inbox_pbis(self) -> list[PBI]:
        """Return all inbox PBIs sorted by priority lane + created_at."""
        return sorted(self._list_pbis("inbox"), key=_lane_rank)

    def pick_next(self) -> PBI | None:
        """Return the highest-priority inbox PBI whose ``depends_on`` deps
        are all in ``done/``, or None if no eligible PBI exists.

        Dependency semantics: a PBI is eligible IFF every id in its
        ``depends_on`` list is the id of a PBI currently in ``done/``.
        Deps that point at non-existent PBIs OR that point at PBIs not
        yet in done/ block the PBI. The operator is responsible for
        avoiding cycles (the dependency graph is hand-authored and small).
        """
        done_ids = {p.id for p in self.done_pbis()}
        for candidate in self.inbox_pbis():
            if all(dep in done_ids for dep in candidate.depends_on):
                return candidate
        return None

    def pending_pr_pbis(self) -> list[PBI]:
        """Return all PBIs in pending-pr/ (used by Plan 8's sweep)."""
        return self._list_pbis("pending-pr")

    def blocked_pbis(self) -> list[PBI]:
        """Return all PBIs in blocked/ (used by Plans 9 / 10)."""
        return self._list_pbis("blocked")

    def done_pbis(self) -> list[PBI]:
        """Return all PBIs in done/ (used by Plan 9's cycle detector)."""
        return self._list_pbis("done")
