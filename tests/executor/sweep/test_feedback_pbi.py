"""Tests for ``ralph_executor.sweep.feedback_pbi.render``.

The FEEDBACK.md template is the boilerplate from the v1 spec. The tests
pin the boilerplate verbatim (so a future refactor that accidentally
rewrites it fails loudly) and verify the per-comment substitutions.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ralph_executor.sweep.feedback_pbi import render
from ralph_executor.sweep.types import (
    CommentSnapshot,
    PrSnapshot,
    ThreadSnapshot,
)

NOW = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)


def _comment(**overrides: object) -> CommentSnapshot:
    defaults: dict[str, object] = dict(
        thread_id=11,
        comment_id=101,
        author_email="reviewer@example.com",
        posted_at=NOW,
        text="Please rename this variable.",
        file_path="src/app.py",
        line=42,
    )
    defaults.update(overrides)
    return CommentSnapshot(**defaults)  # type: ignore[arg-type]


def _snapshot(threads: tuple[ThreadSnapshot, ...]) -> PrSnapshot:
    return PrSnapshot(
        pr_id=100,
        title="Add /healthz endpoint",
        pr_status="active",
        ci_status="succeeded",
        source_branch="ralph/WI-1234",
        target_branch="main",
        reviewers=("reviewer@example.com",),
        threads=threads,
        last_activity_at=NOW,
        url="https://dev.azure.com/example-org/_git/svc/pullrequest/100",
    )


def _render(
    *,
    round_number: int = 1,
    new_comments: tuple[CommentSnapshot, ...] | None = None,
    summary: str = "# Original feature ask",
):
    return render(
        pr=_snapshot(threads=()),
        originating_pbi_id="WI-1234",
        round_number=round_number,
        new_comments=new_comments or (_comment(),),
        original_pbi_summary=summary,
        generated_at=NOW,
    )


def test_directory_name_uses_pbi_id_and_round() -> None:
    assert _render(round_number=2).directory_name == "PR-feedback-WI-1234-r2"


def test_feedback_md_carries_boilerplate_and_frontmatter() -> None:
    bundle = _render()
    text = bundle.feedback_md
    # Boilerplate (verbatim from the spec) must appear.
    for marker in (
        "## Instructions for Ralph",
        "For each active comment above",
        "Read the comment carefully",
        "Always reply",
        "Set thread status after replying",
        "Never set **wontFix** or **byDesign**",
        "## Constraints",
        "Don't open a new PR",
    ):
        assert marker in text, f"missing boilerplate marker: {marker}"
    # Frontmatter is well-formed.
    assert text.startswith("---\n")
    assert "id: PR-feedback-WI-1234-r1" in text
    assert "type: pr-feedback" in text
    assert "status: inbox" in text
    assert "severity: high" in text


def test_feedback_md_lists_comments_in_order_with_location_labels() -> None:
    c1 = _comment(thread_id=11, comment_id=101, text="First")
    c2 = _comment(thread_id=12, comment_id=201, text="Second", file_path=None, line=None)
    text = _render(new_comments=(c1, c2)).feedback_md
    assert text.index("First") < text.index("Second")
    assert "general" in text  # c2 has no file_path → "general"
    assert "src/app.py, line 42" in text  # c1 has file/line


def test_other_files_carry_expected_substitutions() -> None:
    bundle = _render(summary="# The original ask was about /healthz")
    assert "https://dev.azure.com/example-org/_git/svc/pullrequest/100" in bundle.pr_link_md
    assert "ralph/WI-1234" in bundle.pr_link_md
    assert "/healthz" in bundle.original_md
    # HISTORY.md is seeded with lineage so Ralph sees the round.
    assert "round 1" in bundle.history_md
    assert "PR-feedback-WI-1234-r1" in bundle.history_md
