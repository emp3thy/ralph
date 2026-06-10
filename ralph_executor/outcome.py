"""Outcome domain for Claude subprocess runs: result dataclass and pure classification."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

OutcomeKind = Literal["pr_created", "stuck", "partial", "error"]

_STUCK_FILENAME = "STUCK.md"

PrCheckState = Literal["pass", "fail", "pending", "error"]


@dataclass(frozen=True)
class ClaudeOutcome:
    """Result of a single ``claude -p`` invocation against the current PBI."""

    kind: OutcomeKind
    pr_url: str | None
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float


def classify_outcome(
    *,
    pbi_dir: Path,
    stdout: str,
    stderr: str,
    exit_code: int,
    duration_seconds: float,
    pr_url: str | None = None,
    pr_lookup: Callable[[], str | None] | None = None,
    pr_check_state: PrCheckState = "pass",
    pr_check_failed_names: list[str] | None = None,
) -> ClaudeOutcome:
    """Map the raw (stdout, stderr, exit, on-disk, pr-state) tuple to a
    typed outcome.

    Precedence (highest first):
      1. STUCK.md present on disk → ``stuck``
      2. Exit code non-zero       → ``error``
      3. open PR exists for the feature branch AND
         ``pr_check_state == "pass"`` → ``pr_created``
         (``pr_url`` is taken directly; if not provided, ``pr_lookup``
         is called once and its return value is used)
      4. open PR exists but ``pr_check_state == "fail"`` → ``partial``
         with a synthetic stderr line naming the failed required checks
         so the next iteration's Claude session reads them.
      5. open PR exists but ``pr_check_state in ("pending", "error")``
         → ``partial`` (no synthetic stderr; the next iteration's
         polling will re-decide).
      6. Otherwise                → ``partial``

    ``pr_url`` and ``pr_lookup`` exist so tests can inject the PR-state
    answer without monkeypatching subprocess. Production callers pass
    ``pr_url`` (already resolved by ``_query_open_pr_via_gh``) AND
    ``pr_check_state`` (resolved by ``_wait_for_pr_checks``).

    ``pr_check_state`` defaults to ``"pass"`` so legacy tests that
    pre-date the CI-green verifier (and don't care about it) keep
    classifying as ``pr_created`` when a ``pr_url`` is set. Production
    code paths in ``spawn_claude_p`` always pass the real state.
    """
    stuck_present = (pbi_dir / _STUCK_FILENAME).is_file()

    if stuck_present:
        return ClaudeOutcome(
            kind="stuck",
            pr_url=None,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
        )
    if exit_code != 0:
        return ClaudeOutcome(
            kind="error",
            pr_url=None,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
        )
    if pr_url is None and pr_lookup is not None:
        pr_url = pr_lookup()
    if pr_url:
        if pr_check_state == "pass":
            return ClaudeOutcome(
                kind="pr_created",
                pr_url=pr_url,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                duration_seconds=duration_seconds,
            )
        if pr_check_state == "fail":
            names = pr_check_failed_names or []
            synthetic = (
                f"PR {pr_url} required checks failed: "
                f"{', '.join(names) if names else '<unnamed>'}. "
                f"Fix the failures next iteration."
            )
            joined_stderr = (stderr + "\n" + synthetic) if stderr else synthetic
            return ClaudeOutcome(
                kind="partial",
                pr_url=pr_url,
                stdout=stdout,
                stderr=joined_stderr,
                exit_code=exit_code,
                duration_seconds=duration_seconds,
            )
        # pending or error: keep PBI in current/; next iteration re-polls.
        return ClaudeOutcome(
            kind="partial",
            pr_url=pr_url,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
        )
    return ClaudeOutcome(
        kind="partial",
        pr_url=None,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_seconds=duration_seconds,
    )
