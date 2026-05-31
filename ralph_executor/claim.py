"""``CLAIM.json`` schema and IO helpers.

Each PBI in ``.ralph/current/<id>/`` carries a ``CLAIM.json`` naming the
ralph instance that owns it. Written atomically with the
``git mv inbox/<id>/ current/<id>/`` via the ``_move`` ``post_mv`` hook.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CLAIM_FILENAME = "CLAIM.json"


class ClaimParseError(ValueError):
    """Raised when ``CLAIM.json`` is malformed or missing required fields."""


@dataclass(frozen=True)
class ClaimInfo:
    instance_id: str
    claimed_at: datetime
    hostname: str


def write_claim(pbi_dir: Path, info: ClaimInfo) -> Path:
    payload = {
        "instance_id": info.instance_id,
        "claimed_at": info.claimed_at.isoformat(),
        "hostname": info.hostname,
    }
    path = pbi_dir / CLAIM_FILENAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_claim(pbi_dir: Path) -> ClaimInfo | None:
    path = pbi_dir / CLAIM_FILENAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClaimParseError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ClaimParseError(f"{path}: top level must be an object")
    try:
        instance_id = raw["instance_id"]
        claimed_at_raw = raw["claimed_at"]
        hostname = raw["hostname"]
    except KeyError as exc:
        raise ClaimParseError(f"{path}: missing required field {exc}") from exc
    if not isinstance(instance_id, str) or not instance_id:
        raise ClaimParseError(f"{path}: instance_id must be a non-empty string")
    if not isinstance(claimed_at_raw, str):
        raise ClaimParseError(f"{path}: claimed_at must be an ISO-8601 string")
    try:
        claimed_at = datetime.fromisoformat(claimed_at_raw)
    except ValueError as exc:
        raise ClaimParseError(f"{path}: claimed_at not ISO-8601: {exc}") from exc
    if not isinstance(hostname, str):
        raise ClaimParseError(f"{path}: hostname must be a string")
    return ClaimInfo(instance_id=instance_id, claimed_at=claimed_at, hostname=hostname)
