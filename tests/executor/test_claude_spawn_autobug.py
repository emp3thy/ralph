"""T17: ``_pbi_frontmatter_has_signature`` autobug helper on ``claude_spawn``."""

from __future__ import annotations

from pathlib import Path

from ralph_executor.claude_spawn import _pbi_frontmatter_has_signature
from ralph_executor.queue.filesystem import parse_pbi_directory


def test_pbi_frontmatter_has_signature_true_for_autobug(fake_repo: Path) -> None:
    pbi_dir = fake_repo / ".ralph" / "current" / "autobug-abc-001"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "BUG.md").write_text(
        "---\nid: autobug-abc-001\ntype: bug\nseverity: critical\n"
        "status: current\nattempts: 0\n"
        "created_at: 2026-05-31T00:00:00+00:00\n"
        "updated_at: 2026-05-31T00:00:00+00:00\n"
        "target_repo: https://github.com/x/y\n"
        "signature: abc123def456\n---\n# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "REPRODUCE.md").write_text("# r\n", encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    pbi = parse_pbi_directory(pbi_dir, status="current")
    assert _pbi_frontmatter_has_signature(pbi) is True


def test_pbi_frontmatter_has_signature_false_for_normal(fake_repo: Path) -> None:
    from tests.executor.conftest import write_sample_pbi

    pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-1234", where="current")
    pbi = parse_pbi_directory(pbi_dir, status="current")
    assert _pbi_frontmatter_has_signature(pbi) is False
