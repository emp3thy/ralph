"""Typed payloads exchanged inside the sweep module.

Nothing in this file performs I/O. Every value is immutable so a Decision
produced for one PBI can never be confused with another after the fact.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

PrStatus = Literal["active", "completed", "abandoned", "unknown"]
CiStatus = Literal["succeeded", "failed", "running", "none", "unknown"]
ThreadStatus = Literal["active", "pending", "fixed", "closed", "wontFix", "byDesign", "unknown"]


@dataclass(frozen=True)
class CommentSnapshot:
    """One PR comment, as returned by the PR skill's read-threads operation.

    ``thread_id`` is the PR thread id; ``comment_id`` is the per-comment id
    inside the thread. ``author_email`` is the comment author's primary email
    (lower-cased so comparisons against ``RALPH_ADO_AUTHOR_EMAIL`` are stable).
    ``posted_at`` is the comment's published timestamp parsed into a
    timezone-aware datetime; missing dates parse to ``datetime.min`` (UTC).
    """

    thread_id: int
    comment_id: int
    author_email: str
    posted_at: datetime
    text: str
    file_path: str | None
    line: int | None


@dataclass(frozen=True)
class ThreadSnapshot:
    """One PR thread."""

    thread_id: int
    status: ThreadStatus
    file_path: str | None
    line: int | None
    comments: tuple[CommentSnapshot, ...]


@dataclass(frozen=True)
class PrSnapshot:
    """Aggregate of the PR skill's ``show`` + ``read-threads`` for one PR.

    ``last_activity_at`` is the most recent timestamp across the PR's own
    last push and every comment's ``posted_at``; used to decide staleness.
    """

    pr_id: int
    title: str
    pr_status: PrStatus
    ci_status: CiStatus
    source_branch: str
    target_branch: str
    reviewers: tuple[str, ...]
    threads: tuple[ThreadSnapshot, ...]
    last_activity_at: datetime
    url: str
    merge_state: str = ""


class Action(StrEnum):
    MOVE_TO_DONE = "move-to-done"
    MOVE_TO_BLOCKED_ABANDONED = "move-to-blocked-abandoned"
    MOVE_TO_INBOX_RETRY = "move-to-inbox-retry"
    MOVE_TO_BLOCKED_MAX_ATTEMPTS = "move-to-blocked-max-attempts"
    CREATE_FEEDBACK_PBI = "create-feedback-pbi"
    PING_REVIEWER = "ping-reviewer"
    MERGE_PR = "merge-pr"
    NOOP = "noop"


@dataclass(frozen=True)
class Decision:
    """The single result ``decide_action`` returns per pending PBI.

    ``new_comments`` is populated only for ``CREATE_FEEDBACK_PBI`` (the
    filtered set Ralph should respond to). ``reason`` is a short human-
    readable string used in HISTORY.md appends and in the sweep summary.
    """

    action: Action
    reason: str
    new_comments: tuple[CommentSnapshot, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FeedbackPbiBundle:
    """The four files + the directory name for a freshly-generated feedback PBI."""

    directory_name: str  # e.g. "PR-feedback-WI-1234-r2"
    feedback_md: str
    pr_link_md: str
    original_md: str
    history_md: str


class ReconcileAction(StrEnum):
    MOVED_TO_DONE = "moved_to_done"
    MOVED_TO_BLOCKED = "moved_to_blocked"
    MOVED_TO_INBOX = "moved_to_inbox"
    KEEP_PENDING = "keep_pending"  # PR open; PR-LINK.md written
    KEEP_API_ERROR = "keep_api_error"  # subprocess exit 3; retry next iter


@dataclass(frozen=True)
class ReconcileReport:
    """Aggregate of reconcile_all over .ralph/pending-pr/.

    ``actions`` maps each processed PBI id to its chosen action.
    ``errors`` carries human-readable error strings for PBIs that
    raised ReconcileError (matches the per-PBI isolation semantics of
    the existing sweep loop).
    """

    actions: Mapping[str, ReconcileAction]
    errors: Mapping[str, str]
