"""Render the FEEDBACK / PR-LINK / ORIGINAL / HISTORY files for a feedback PBI.

The FEEDBACK.md template is the boilerplate from the spec; only the
per-comment list and the header substitutions vary between rounds.
"""

from __future__ import annotations

from datetime import datetime
from textwrap import dedent

from ralph_executor.sweep.types import (
    CommentSnapshot,
    FeedbackPbiBundle,
    PrSnapshot,
)

_FEEDBACK_INSTRUCTIONS = dedent(
    """\
    ## Instructions for Ralph

    For each active comment above:

    1. **Read the comment carefully** and examine the referenced code
       (file:line for line comments; the diff for general comments).

    2. **Decide: is the reviewer correct?**
       - If the concern is valid → implement the fix.
       - If the concern is incorrect or based on a misunderstanding →
         don't change the code. Prepare a clear, respectful reply explaining why.
       - If you can't tell from available context → write STUCK.md
         with what's unclear.

    3. **Always reply.** Use the PR skill to post in the thread.
       Tell the reviewer:
       - What you changed (filename, line, brief description), OR
       - Why you didn't change anything (your reasoning).

    4. **Set thread status after replying:**
       - Applied a fix → set thread to **fixed**.
       - Challenged AND your reasoning is strong → leave **active**;
         the reviewer will close it after reading.
       - Never set **wontFix** or **byDesign** — those are human-only.

    5. **Commit and push** to the existing branch after all comments addressed.
       The PR will auto-update and CI re-runs.

    ## Constraints
    - Don't open a new PR. Push commits to the existing branch.
    - Don't address comments outside this FEEDBACK.md (no scope creep).
    - If a comment references work that should be a separate PBI
      ("can we also rename X?"), note it in your reply and don't do it here.
    - If you challenge and the reviewer later responds with a counter, that
      becomes a new FEEDBACK round — don't try to predict their response.
    """
)


def render(
    *,
    pr: PrSnapshot,
    originating_pbi_id: str,
    round_number: int,
    new_comments: tuple[CommentSnapshot, ...],
    original_pbi_summary: str,
    generated_at: datetime,
) -> FeedbackPbiBundle:
    """Build the four files for a fresh feedback PBI."""
    pbi_id = f"PR-feedback-{originating_pbi_id}-r{round_number}"
    directory_name = pbi_id

    feedback_md = _render_feedback_md(
        pr=pr,
        pbi_id=pbi_id,
        originating_pbi_id=originating_pbi_id,
        round_number=round_number,
        new_comments=new_comments,
        generated_at=generated_at,
    )
    pr_link_md = _render_pr_link_md(pr=pr, generated_at=generated_at)
    original_md = _render_original_md(
        originating_pbi_id=originating_pbi_id,
        summary=original_pbi_summary,
    )
    history_md = _render_history_md(pbi_id=pbi_id, round_number=round_number)

    return FeedbackPbiBundle(
        directory_name=directory_name,
        feedback_md=feedback_md,
        pr_link_md=pr_link_md,
        original_md=original_md,
        history_md=history_md,
    )


# ----------------------------------------------------------------------
# File renderers
# ----------------------------------------------------------------------


def _render_feedback_md(
    *,
    pr: PrSnapshot,
    pbi_id: str,
    originating_pbi_id: str,
    round_number: int,
    new_comments: tuple[CommentSnapshot, ...],
    generated_at: datetime,
) -> str:
    iso = generated_at.isoformat()
    frontmatter = "\n".join(
        [
            "---",
            f"id: {pbi_id}",
            "type: pr-feedback",
            "status: inbox",
            "severity: high",
            "attempts: 0",
            f"created_at: {iso}",
            f"updated_at: {iso}",
            f"parent_id: {originating_pbi_id}",
            "---",
            "",
        ]
    )

    header = "\n".join(
        [
            f"# PR feedback — PBI {originating_pbi_id} — round {round_number}",
            "",
            f'**PR:** !{pr.pr_id} — "{pr.title}"',
            f"**Branch:** {pr.source_branch}",
            "**Commit at time of feedback:** (computed at apply time)",
            f"**Reviewers:** {', '.join(pr.reviewers) if pr.reviewers else '(none)'}",
            f"**Feedback collected at:** {iso}",
            "",
            "## Active comments",
            "",
        ]
    )

    comment_blocks: list[str] = []
    for index, comment in enumerate(new_comments, start=1):
        location = (
            f"file: {comment.file_path}, line {comment.line}"
            if comment.file_path and comment.line is not None
            else "general"
        )
        block = "\n".join(
            [
                f"### Comment {index} — {location}",
                f"- **Thread ID:** {comment.thread_id}",
                f"- **Reviewer:** {comment.author_email}",
                f"- **Posted:** {comment.posted_at.isoformat()}",
                "",
                "> " + comment.text.replace("\n", "\n> "),
                "",
                "---",
                "",
            ]
        )
        comment_blocks.append(block)

    return frontmatter + header + "".join(comment_blocks) + "\n" + _FEEDBACK_INSTRUCTIONS


def _render_pr_link_md(*, pr: PrSnapshot, generated_at: datetime) -> str:
    return dedent(
        f"""\
        # PR link

        - **URL:** {pr.url}
        - **PR ID:** !{pr.pr_id}
        - **Source branch:** {pr.source_branch}
        - **Target branch:** {pr.target_branch}
        - **Captured at:** {generated_at.isoformat()}
        """
    )


def _render_original_md(*, originating_pbi_id: str, summary: str) -> str:
    return dedent(
        f"""\
        # Original PBI — {originating_pbi_id}

        The PR Ralph is responding to was opened for this original work item.
        A compact summary is preserved below so Ralph has context without
        having to re-read the full ``pending-pr/`` directory.

        ---

        {summary}
        """
    )


def _render_history_md(*, pbi_id: str, round_number: int) -> str:
    return dedent(
        f"""\
        # History — {pbi_id}

        - Generated by the executor sweep as round {round_number} for the PR.
        """
    )
