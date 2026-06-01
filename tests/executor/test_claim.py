"""Tests for ``ralph_executor.queue.claim`` — CLAIM.json IO helpers."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from ralph_executor.queue.claim import (
    CLAIM_FILENAME,
    Claim,
    ClaimError,
    read_claim,
    write_claim,
)


def test_claim_filename_is_claim_json() -> None:
    assert CLAIM_FILENAME == "CLAIM.json"


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    claim = Claim(
        instance_id="ralph-a",
        claimed_at="2026-06-01T15:20:00+00:00",
        hostname="box-a",
    )
    path = tmp_path / "CLAIM.json"
    write_claim(path, claim)
    assert read_claim(path) == claim


def test_to_json_is_indented_object_with_expected_keys() -> None:
    claim = Claim(
        instance_id="ralph-a",
        claimed_at="2026-06-01T15:20:00+00:00",
        hostname="box-a",
    )
    payload = json.loads(claim.to_json())
    assert payload == {
        "instance_id": "ralph-a",
        "claimed_at": "2026-06-01T15:20:00+00:00",
        "hostname": "box-a",
    }


def test_unicode_hostname_roundtrip(tmp_path: Path) -> None:
    """Hosts with non-ASCII names must survive write/read unchanged.

    ``json.dumps(ensure_ascii=False)`` + ``encoding="utf-8"`` keeps the
    raw glyphs in the file; this test guards against a future regression
    where someone flips ``ensure_ascii=True`` and double-encodes the
    string.
    """
    claim = Claim(
        instance_id="ralph-a",
        claimed_at="2026-06-01T15:20:00+00:00",
        hostname="büro-α-01",
    )
    path = tmp_path / "CLAIM.json"
    write_claim(path, claim)
    assert path.read_text(encoding="utf-8").find("büro-α-01") != -1
    assert read_claim(path).hostname == "büro-α-01"


def test_read_claim_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(ClaimError, match="cannot read CLAIM.json"):
        read_claim(missing)


def test_read_claim_rejects_non_json(tmp_path: Path) -> None:
    path = tmp_path / "CLAIM.json"
    path.write_text("not-json{", encoding="utf-8")
    with pytest.raises(ClaimError, match="invalid JSON"):
        read_claim(path)


def test_read_claim_rejects_non_object(tmp_path: Path) -> None:
    """A JSON array is valid JSON but not a CLAIM.json — must raise."""
    path = tmp_path / "CLAIM.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ClaimError, match="must be a JSON object"):
        read_claim(path)


@pytest.mark.parametrize(
    "drop_key",
    ["instance_id", "claimed_at", "hostname"],
)
def test_read_claim_rejects_missing_required_key(tmp_path: Path, drop_key: str) -> None:
    payload = {
        "instance_id": "ralph-a",
        "claimed_at": "2026-06-01T15:20:00+00:00",
        "hostname": "box-a",
    }
    del payload[drop_key]
    path = tmp_path / "CLAIM.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ClaimError, match="missing required key"):
        read_claim(path)


@pytest.mark.parametrize(
    "bad_key",
    ["instance_id", "claimed_at", "hostname"],
)
def test_read_claim_rejects_non_string_field(tmp_path: Path, bad_key: str) -> None:
    payload: dict[str, object] = {
        "instance_id": "ralph-a",
        "claimed_at": "2026-06-01T15:20:00+00:00",
        "hostname": "box-a",
    }
    payload[bad_key] = 42
    path = tmp_path / "CLAIM.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ClaimError, match="must be a string"):
        read_claim(path)


def test_write_claim_raises_when_parent_missing(tmp_path: Path) -> None:
    """Caller invariant: parent must exist (``git mv`` creates it).

    The helper itself does NOT ``mkdir(parents=True)`` — that would let
    a stray write land somewhere unexpected on the queue clone. Asserts
    the helper surfaces the OSError as ``ClaimError`` instead of leaking
    the raw exception.
    """
    claim = Claim(
        instance_id="ralph-a",
        claimed_at="2026-06-01T15:20:00+00:00",
        hostname="box-a",
    )
    missing_parent = tmp_path / "no-such-dir" / "CLAIM.json"
    with pytest.raises(ClaimError, match="cannot write CLAIM.json"):
        write_claim(missing_parent, claim)


def test_claim_is_frozen() -> None:
    """``Claim`` is ``frozen=True`` so it can be safely cached / hashed."""
    claim = Claim(
        instance_id="ralph-a",
        claimed_at="2026-06-01T15:20:00+00:00",
        hostname="box-a",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.instance_id = "ralph-b"  # type: ignore[misc]
