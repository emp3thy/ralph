"""Tests for scripts.migrate_pbis_to_target_repo."""

from __future__ import annotations

from pathlib import Path


def _make_pbi(pbi_dir: Path, *, with_target_repo: bool) -> Path:
    pbi_dir.mkdir(parents=True)
    target_line = "target_repo: https://github.com/emp3thy/ralph\n" if with_target_repo else ""
    entry = pbi_dir / "PBI.md"
    entry.write_text(
        "---\n"
        "id: WI-1\n"
        "type: feature\n"
        "status: inbox\n"
        "severity: normal\n"
        "attempts: 0\n"
        f"{target_line}"
        "---\n"
        "# Title\n",
        encoding="utf-8",
    )
    return entry


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_migrate_adds_target_repo_to_missing_pbi(tmp_path: Path) -> None:
    """A PBI without the field gets it injected before the closing fence."""
    from scripts.migrate_pbis_to_target_repo import _insert_field

    entry = _make_pbi(tmp_path / ".ralph" / "inbox" / "WI-1", with_target_repo=False)
    result = _insert_field(entry)
    assert result == "updated"
    text = _read(entry)
    assert "target_repo: https://github.com/emp3thy/ralph" in text


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    """A PBI that already has the field is left untouched (skipped)."""
    from scripts.migrate_pbis_to_target_repo import _insert_field

    entry = _make_pbi(tmp_path / ".ralph" / "current" / "WI-2", with_target_repo=True)
    before = _read(entry)
    result = _insert_field(entry)
    assert result == "skipped"
    after = _read(entry)
    assert before == after


def test_migrate_inserts_before_closing_fence(tmp_path: Path) -> None:
    """The new line lands inside the frontmatter, not at file start or end."""
    from scripts.migrate_pbis_to_target_repo import _insert_field

    entry = _make_pbi(tmp_path / ".ralph" / "done" / "WI-3", with_target_repo=False)
    _insert_field(entry)
    lines = _read(entry).splitlines()
    fence_positions = [i for i, line in enumerate(lines) if line == "---"]
    assert len(fence_positions) == 2, lines
    target_position = next(i for i, line in enumerate(lines) if line.startswith("target_repo:"))
    assert fence_positions[0] < target_position < fence_positions[1]


def test_migrate_preserves_existing_frontmatter_order(tmp_path: Path) -> None:
    """Fields before the closing fence keep their original order;
    target_repo is appended as the last frontmatter line."""
    from scripts.migrate_pbis_to_target_repo import _insert_field

    entry = _make_pbi(tmp_path / ".ralph" / "blocked" / "WI-4", with_target_repo=False)
    _insert_field(entry)
    lines = _read(entry).splitlines()
    field_order = [
        line.split(":")[0] for line in lines if line and not line.startswith("---") and ":" in line
    ]
    expected_prefix = ["id", "type", "status", "severity", "attempts"]
    assert field_order[: len(expected_prefix)] == expected_prefix
    assert "target_repo" in field_order
    assert field_order.index("target_repo") == len(expected_prefix)


def test_migrate_handles_no_fence_gracefully(tmp_path: Path) -> None:
    """A file without a frontmatter fence returns an 'error: ...' status,
    not a crash."""
    from scripts.migrate_pbis_to_target_repo import _insert_field

    entry = tmp_path / "no_fence.md"
    entry.write_text("just a body, no fence\n", encoding="utf-8")
    result = _insert_field(entry)
    assert result.startswith("error:")
