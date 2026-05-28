"""Tests for ralph_executor.migrate_queue (Task 7a — helper only).

Task 7b adds ``main`` + ``_target_is_empty`` and their integration tests.
"""

from __future__ import annotations

from pathlib import Path

from ralph_executor.migrate_queue import copy_queue_tree_filtered


def _seed_source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / ".ralph" / "inbox" / "WI-1").mkdir(parents=True)
    (src / ".ralph" / "inbox" / "WI-1" / "PBI.md").write_text(
        "---\nid: WI-1\n---\n", encoding="utf-8"
    )
    (src / ".ralph" / "current").mkdir(parents=True)
    (src / ".ralph" / "pending-pr").mkdir(parents=True)
    (src / ".ralph" / "blocked" / "WI-9").mkdir(parents=True)
    (src / ".ralph" / "blocked" / "WI-9" / "PBI.md").write_text(
        "---\nid: WI-9\n---\n", encoding="utf-8"
    )
    (src / ".ralph" / "blocked" / "META-cycle-20260101T000000Z.md").write_text(
        "stale sentinel residue\n", encoding="utf-8"
    )
    (src / ".ralph" / "done" / "WI-old").mkdir(parents=True)
    (src / ".ralph" / "done" / "WI-old" / "PBI.md").write_text(
        "---\nid: WI-old\n---\n", encoding="utf-8"
    )
    (src / ".ralph" / "state").mkdir()
    (src / ".ralph" / "state" / "events.db").write_bytes(b"local-only")
    (src / ".ralph" / "config.toml").write_text("# example\n", encoding="utf-8")
    return src


def test_copy_filtered_excludes_done(tmp_path: Path) -> None:
    src = _seed_source(tmp_path)
    dst = tmp_path / "dst"

    counts = copy_queue_tree_filtered(src, dst)

    assert (dst / ".ralph" / "inbox" / "WI-1" / "PBI.md").exists()
    assert (dst / ".ralph" / "blocked" / "WI-9" / "PBI.md").exists()
    assert not (dst / ".ralph" / "done").exists()
    assert not (dst / ".ralph" / "blocked" / "META-cycle-20260101T000000Z.md").exists()
    assert not (dst / ".ralph" / "state" / "events.db").exists()
    assert (dst / ".ralph" / "config.toml").exists()
    assert (dst / ".gitignore").read_text(encoding="utf-8") == ".ralph/state/\n"
    assert counts == {
        "inbox": 1,
        "current": 0,
        "pending-pr": 0,
        "blocked": 1,
        "archive": 0,
    }
