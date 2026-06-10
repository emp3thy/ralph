"""Event emission for sweep outcomes — PR merged/closed and CI green-to-red transitions.

Both emitters take exactly what they consume (the optional ``EventLog``
sink and the sweep's ``now`` timestamp) so this module stays leaf-level
within ``sweep/`` and never imports the runner.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ralph_executor.safety.events import (
    Event,
    EventLog,
    EventType,
    signature_from_text,
)
from ralph_executor.sweep.types import PrSnapshot

log = logging.getLogger(__name__)


def emit_pr_merged_and_pbi_closed(
    *,
    pbi_id: str,
    snapshot: PrSnapshot,
    event_log: EventLog | None,
    now: datetime,
) -> None:
    """Emit PR_MERGED + PBI_CLOSED on a pending-pr → done transition.

    Both events share the same ``signature_from_text(pr_url)`` so the
    cycle detector's ``signature_recurrence`` and ``regression_cascade``
    rules can match across pairs. ``files`` is included for payload-shape
    parity with ``PR_CREATED`` but is left empty: the PR skill's ``show``
    op does not expose the file list and adding a second REST call per
    sweep tick was deemed out of scope for v1.
    """
    if event_log is None:
        return
    pr_url = snapshot.url
    signature = signature_from_text(pr_url) if pr_url else ""
    # The PBI has already been moved to done/ by the caller; if the
    # event-log write raises (e.g. sqlite3.Error on flush), there is
    # NO retry path — the PBI is gone from pending-pr/ and won't be
    # rescanned. Swallow the exception with a WARNING rather than
    # letting it propagate uncaught past the per-PBI handler (which
    # only catches SweepPbiError) and abort the whole sweep. The
    # cycle detector's regression_cascade rule will be missing this
    # pair, but that's a softer failure than a sweep-wide abort that
    # also masks every other PBI's progress.
    try:
        event_log.append(
            Event(
                kind=EventType.PR_MERGED,
                recorded_at=now,
                pbi_id=pbi_id,
                payload={
                    "pr_url": pr_url,
                    "signature": signature,
                    "files": [],
                },
            )
        )
        event_log.append(
            Event(
                kind=EventType.PBI_CLOSED,
                recorded_at=now,
                pbi_id=pbi_id,
                payload={"signature": signature},
            )
        )
    except Exception as exc:
        log.warning(
            "sweep: failed to emit PR_MERGED/PBI_CLOSED for %s (PBI already moved "
            "to done/, no retry path): %s",
            pbi_id,
            exc,
        )


def emit_pr_green_then_red(
    *,
    pbi_id: str,
    snapshot: PrSnapshot,
    event_log: EventLog | None,
    now: datetime,
) -> None:
    """Emit PR_GREEN_THEN_RED on a succeeded → failed CI transition.

    Payload shape matches PR_CREATED / PR_MERGED so the cycle detector's
    ``regression_cascade`` rule can pair a recent merge with a later
    regression by signature. ``files`` is empty: the PR skill's ``show``
    op does not expose the file list and adding a second REST call per
    sweep tick was deemed out of scope for v1 (same reasoning as
    ``emit_pr_merged_and_pbi_closed``).
    """
    if event_log is None:
        return
    pr_url = snapshot.url
    signature = signature_from_text(pr_url) if pr_url else ""
    # Log-and-continue on event-log failure — same resilience policy as
    # the rest of the sweep. The PBI has not yet been moved here, so a
    # missing event only means the cycle detector won't fire its
    # regression_cascade for this transition; the sweep continues
    # processing other PBIs and the next tick will re-evaluate state.
    try:
        event_log.append(
            Event(
                kind=EventType.PR_GREEN_THEN_RED,
                recorded_at=now,
                pbi_id=pbi_id,
                payload={
                    "pr_url": pr_url,
                    "signature": signature,
                    "files": [],
                },
            )
        )
    except Exception as exc:
        log.warning(
            "sweep: failed to emit PR_GREEN_THEN_RED for %s: %s",
            pbi_id,
            exc,
        )
