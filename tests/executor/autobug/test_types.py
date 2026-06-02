from datetime import UTC, datetime
from pathlib import Path

from ralph_executor.autobug.types import Context, DedupResult


def test_context_is_frozen_dataclass(tmp_path: Path) -> None:
    ctx = Context(
        queue_root=tmp_path / "queue",
        state_dir=tmp_path / "queue" / ".ralph" / "state",
        env={"FOO": "bar"},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="51cc97a",
        bot_author_email="bot@example.com",
        triggering_pbi_id="WI-247",
        queue_branch="ralph-queue",
    )
    assert ctx.queue_root == tmp_path / "queue"
    assert ctx.env["FOO"] == "bar"
    assert ctx.ralph_sha == "51cc97a"
    assert ctx.queue_branch == "ralph-queue"


def test_context_triggering_pbi_id_optional(tmp_path: Path) -> None:
    ctx = Context(
        queue_root=tmp_path,
        state_dir=tmp_path / "state",
        env={},
        now=datetime(2026, 5, 31, tzinfo=UTC),
        ralph_sha="abc",
        bot_author_email="b@e.com",
        triggering_pbi_id=None,
        queue_branch="ralph-queue",
    )
    assert ctx.triggering_pbi_id is None


def test_dedup_result_kinds() -> None:
    r = DedupResult(kind="new")
    assert r.kind == "new"
    assert r.existing_pbi_id is None
    r2 = DedupResult(kind="bump_existing", existing_pbi_id="autobug-abc-001")
    assert r2.existing_pbi_id == "autobug-abc-001"
