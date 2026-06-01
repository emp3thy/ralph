"""``CLAIM.json`` ownership marker for claimed PBIs.

Every claimed PBI carries a ``CLAIM.json`` file inside its
``current/<id>/`` directory. The marker records which ralph instance
owns the claim, when it was claimed, and which host the instance was
running on. Together with the workspace lockfile, this lets multiple
ralphs share a single queue clone without trampling each other's work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLAIM_FILENAME = "CLAIM.json"


class ClaimError(RuntimeError):
    """Raised when ``CLAIM.json`` is malformed or cannot be read/written."""


@dataclass(frozen=True, slots=True)
class Claim:
    """Ownership marker for a PBI claimed into ``current/``.

    ``claimed_at`` is an ISO-8601 UTC string (caller produces it via
    ``datetime.now(UTC).isoformat()``); kept as a string so the
    dataclass round-trips losslessly through JSON without timezone
    re-derivation surprises.
    """

    instance_id: str
    claimed_at: str
    hostname: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "instance_id": self.instance_id,
                "claimed_at": self.claimed_at,
                "hostname": self.hostname,
            },
            indent=2,
            ensure_ascii=False,
        )


_REQUIRED_KEYS: tuple[str, ...] = ("instance_id", "claimed_at", "hostname")


def _coerce_str(data: dict[str, Any], key: str, source: Path) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise ClaimError(
            f"{source}: CLAIM.json field {key!r} must be a string, got {type(value).__name__}"
        )
    return value


def read_claim(path: Path) -> Claim:
    """Load a ``Claim`` from ``path``.

    Raises ``ClaimError`` when the file is unreadable, not valid JSON,
    not a JSON object, or missing any of the required string fields.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ClaimError(f"{path}: cannot read CLAIM.json: {exc}") from exc

    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClaimError(f"{path}: invalid JSON in CLAIM.json: {exc}") from exc

    if not isinstance(raw, dict):
        raise ClaimError(f"{path}: CLAIM.json must be a JSON object, got {type(raw).__name__}")

    missing = [key for key in _REQUIRED_KEYS if key not in raw]
    if missing:
        raise ClaimError(f"{path}: CLAIM.json missing required key(s): {sorted(missing)}")

    return Claim(
        instance_id=_coerce_str(raw, "instance_id", path),
        claimed_at=_coerce_str(raw, "claimed_at", path),
        hostname=_coerce_str(raw, "hostname", path),
    )


def write_claim(path: Path, claim: Claim) -> None:
    """Serialise ``claim`` to ``path``.

    Writes UTF-8 with a trailing newline. Parent directory must already
    exist — the caller (typically the claim step in ``_claim_pbi``)
    creates ``current/<id>/`` via ``git mv``, so the parent is always
    present when this helper runs.
    """
    try:
        path.write_text(claim.to_json() + "\n", encoding="utf-8")
    except OSError as exc:
        raise ClaimError(f"{path}: cannot write CLAIM.json: {exc}") from exc
