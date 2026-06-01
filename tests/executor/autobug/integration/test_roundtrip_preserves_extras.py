"""Load-bearing test: autobug extras survive every queue move.

If movements.py ever switches from line-based to dataclass-roundtrip
serialisation, this test breaks immediately. That's by design — autobug
dedup depends on signature surviving moves.
"""

from datetime import UTC, datetime
from pathlib import Path

from ralph_executor.autobug import emit
from ralph_executor.autobug.types import Context
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import parse_pbi_directory
from ralph_executor.queue.movements import (
    move_current_to_pending_pr,
    move_inbox_to_current,
    move_pending_pr_to_done,
)


def _ctx_for(fake_repo: Path) -> Context:
    return Context(
        queue_root=fake_repo,
        state_dir=fake_repo / ".ralph" / "state",
        env={},
        now=datetime(2026, 5, 31, 14, 23, 1, tzinfo=UTC),
        ralph_sha="51cc97a",
        bot_author_email="bot@e.com",
        triggering_pbi_id="WI-247",
        queue_branch="main",
    )


def test_roundtrip_preserves_autobug_extras(
    fake_repo: Path, cfg_for_repo: ExecutorConfig
) -> None:
    ctx = _ctx_for(fake_repo)
    try:
        raise RuntimeError("roundtrip test")
    except RuntimeError as exc:
        pbi_id = emit.new(
            signature="f" * 64,
            exc=exc,
            ctx=ctx,
            trigger_kind="python_crash",
            severity="critical",
            target_repo="https://github.com/emp3thy/ralph",
        )
    pbi_dir = fake_repo / ".ralph" / "inbox" / pbi_id
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    pbi = move_inbox_to_current(cfg_for_repo, pbi)
    pbi = move_current_to_pending_pr(cfg_for_repo, pbi)
    pbi = move_pending_pr_to_done(cfg_for_repo, pbi)
    final = (pbi.path / "BUG.md").read_text(encoding="utf-8")
    for key in (
        "signature:",
        "occurrences:",
        "trigger_kind:",
        "first_seen:",
        "last_seen:",
        "regression_of:",
        "triggering_pbi:",
        "ralph_sha:",
    ):
        assert key in final, f"autobug extra {key!r} lost during roundtrip"
    assert "closed_at:" in final  # T6.5 stamp on done/


def test_autobug_writes_never_touch_claim_json(
    fake_repo: Path, cfg_for_repo: ExecutorConfig
) -> None:
    """v4 changelog #7: autobug emit must not overwrite the multi-ralph CLAIM marker.

    Path exercised:
      1. emit.new → inbox PBI (no CLAIM.json yet).
      2. move_inbox_to_current with instance_id → CLAIM.json written.
      3. emit.bump on the same PBI now in current/ → CLAIM.json must be
         byte-identical before and after.
      4. move_current_to_pending_pr → CLAIM.json still byte-identical.
      5. move_pending_pr_to_done → CLAIM.json still byte-identical.
    """
    ctx = _ctx_for(fake_repo)
    try:
        raise RuntimeError("claim test")
    except RuntimeError as exc:
        pbi_id = emit.new(
            signature="a" * 64,
            exc=exc,
            ctx=ctx,
            trigger_kind="python_crash",
            severity="critical",
            target_repo="https://github.com/emp3thy/ralph",
        )
    pbi_dir = fake_repo / ".ralph" / "inbox" / pbi_id
    pbi = parse_pbi_directory(pbi_dir, status="inbox")
    pbi = move_inbox_to_current(
        cfg_for_repo,
        pbi,
        instance_id="test-ralph",
        hostname="testhost",
    )
    claim_path = pbi.path / "CLAIM.json"
    assert claim_path.is_file(), "move_inbox_to_current with instance_id should write CLAIM.json"
    claim_before = claim_path.read_bytes()

    try:
        raise RuntimeError("second occurrence")
    except RuntimeError as exc2:
        emit.bump(pbi_id, exc2, ctx)
    assert claim_path.read_bytes() == claim_before, "emit.bump must not touch CLAIM.json"

    pbi = move_current_to_pending_pr(cfg_for_repo, pbi)
    claim_path = pbi.path / "CLAIM.json"
    assert claim_path.read_bytes() == claim_before, (
        "move_current_to_pending_pr must not touch CLAIM.json"
    )

    pbi = move_pending_pr_to_done(cfg_for_repo, pbi)
    claim_path = pbi.path / "CLAIM.json"
    assert claim_path.read_bytes() == claim_before, (
        "move_pending_pr_to_done must not touch CLAIM.json"
    )
