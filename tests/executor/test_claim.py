"""``CLAIM.json`` schema and IO helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.claim import (
    CLAIM_FILENAME,
    ClaimInfo,
    ClaimParseError,
    read_claim,
    write_claim,
)


def test_write_then_read_roundtrips(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    info = ClaimInfo(
        instance_id="ralph-a",
        claimed_at=datetime(2026, 5, 31, 12, 34, 56, tzinfo=UTC),
        hostname="box-a",
    )
    write_claim(pbi_dir, info)
    assert (pbi_dir / CLAIM_FILENAME).is_file()
    assert read_claim(pbi_dir) == info


def test_read_missing_returns_none(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    assert read_claim(pbi_dir) is None


def test_read_malformed_raises(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    (pbi_dir / CLAIM_FILENAME).write_text("not json", encoding="utf-8")
    with pytest.raises(ClaimParseError):
        read_claim(pbi_dir)


def test_read_missing_field_raises(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    (pbi_dir / CLAIM_FILENAME).write_text(
        '{"instance_id": "ralph-a"}', encoding="utf-8"
    )
    with pytest.raises(ClaimParseError, match="claimed_at"):
        read_claim(pbi_dir)


def test_read_top_level_not_object_raises(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    (pbi_dir / CLAIM_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ClaimParseError, match="object"):
        read_claim(pbi_dir)


def test_read_empty_instance_id_raises(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    (pbi_dir / CLAIM_FILENAME).write_text(
        '{"instance_id": "", "claimed_at": "2026-05-31T00:00:00+00:00", "hostname": "h"}',
        encoding="utf-8",
    )
    with pytest.raises(ClaimParseError, match="instance_id"):
        read_claim(pbi_dir)


def test_read_non_string_instance_id_raises(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    (pbi_dir / CLAIM_FILENAME).write_text(
        '{"instance_id": 42, "claimed_at": "2026-05-31T00:00:00+00:00", "hostname": "h"}',
        encoding="utf-8",
    )
    with pytest.raises(ClaimParseError, match="instance_id"):
        read_claim(pbi_dir)


def test_read_claimed_at_not_iso_raises(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    (pbi_dir / CLAIM_FILENAME).write_text(
        '{"instance_id": "ralph-a", "claimed_at": "not-a-date", "hostname": "h"}',
        encoding="utf-8",
    )
    with pytest.raises(ClaimParseError, match="claimed_at"):
        read_claim(pbi_dir)


def test_read_claimed_at_not_string_raises(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    (pbi_dir / CLAIM_FILENAME).write_text(
        '{"instance_id": "ralph-a", "claimed_at": 42, "hostname": "h"}',
        encoding="utf-8",
    )
    with pytest.raises(ClaimParseError, match="claimed_at"):
        read_claim(pbi_dir)


def test_read_hostname_not_string_raises(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    (pbi_dir / CLAIM_FILENAME).write_text(
        '{"instance_id": "ralph-a", "claimed_at": "2026-05-31T00:00:00+00:00", "hostname": 7}',
        encoding="utf-8",
    )
    with pytest.raises(ClaimParseError, match="hostname"):
        read_claim(pbi_dir)


def test_write_returns_path(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-1234"
    pbi_dir.mkdir()
    info = ClaimInfo(
        instance_id="ralph-a",
        claimed_at=datetime(2026, 5, 31, tzinfo=UTC),
        hostname="h",
    )
    returned = write_claim(pbi_dir, info)
    assert returned == pbi_dir / CLAIM_FILENAME
