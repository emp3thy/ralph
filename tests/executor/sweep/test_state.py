"""Tests for the per-PBI ``.ralph-state.json`` sidecar reader/writer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ralph_executor.sweep.state import (
    SweepSidecar,
    load_sidecar,
    merge_seen_comment_ids,
    write_sidecar,
)


def test_missing_sidecar_returns_empty_default(tmp_path: Path) -> None:
    sidecar = load_sidecar(tmp_path)
    assert sidecar.last_feedback_sweep is None
    assert sidecar.last_feedback_round == 0
    assert sidecar.last_seen_comment_ids == set()


def test_round_trip_sidecar(tmp_path: Path) -> None:
    sidecar = SweepSidecar(
        last_feedback_sweep=datetime(2026, 5, 24, 12, 0, tzinfo=UTC),
        last_feedback_round=2,
        last_seen_comment_ids={"10:1", "10:2", "11:5"},
    )
    write_sidecar(tmp_path, sidecar)
    loaded = load_sidecar(tmp_path)
    assert loaded == sidecar


def test_corrupt_sidecar_returns_default(tmp_path: Path) -> None:
    (tmp_path / ".ralph-state.json").write_text("{ not valid json")
    sidecar = load_sidecar(tmp_path)
    # Corrupt file is treated as "never swept", so we don't lose the PBI.
    assert sidecar.last_feedback_sweep is None
    assert sidecar.last_feedback_round == 0


def test_merge_seen_comment_ids_is_union() -> None:
    existing = {"10:1"}
    new = ["10:2", "11:5", "10:1"]
    merged = merge_seen_comment_ids(existing, new)
    assert merged == {"10:1", "10:2", "11:5"}


def test_write_then_read_preserves_iso_with_offset(tmp_path: Path) -> None:
    sidecar = SweepSidecar(
        last_feedback_sweep=datetime(2026, 5, 24, 8, 0, tzinfo=UTC),
        last_feedback_round=1,
        last_seen_comment_ids=set(),
    )
    write_sidecar(tmp_path, sidecar)
    raw = json.loads((tmp_path / ".ralph-state.json").read_text())
    assert raw["last_feedback_sweep"].endswith("+00:00")
