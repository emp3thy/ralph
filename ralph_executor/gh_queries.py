"""GitHub CLI queries for PR state and required checks.

Thin subprocess wrappers around the ``gh`` binary used by the executor
to resolve "did Claude create a PR?" and "are its required checks
green?" after a ``claude -p`` run. ``query_open_pr_via_gh`` looks up an
OPEN PR for a feature branch, ``query_pr_checks`` reads the required
check buckets for a PR once, and ``wait_for_pr_checks`` polls the latter
until a terminal state or the poll budget is exhausted.

Failure mapping is deliberately pessimistic throughout: any subprocess /
parse failure degrades to "no PR" or a non-``pass`` check state so a
transient gh outage can never escalate a partial iteration to
``pr_created``. Extracted from ``claude_spawn`` so the gh plumbing is
testable without spawn machinery. This module stays leaf-level — it must
not import loop, sweep, or claude_spawn.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from ralph_executor.outcome import PrCheckState
from ralph_executor.subprocess_utils import run_text

log = logging.getLogger(__name__)

_GH_BINARY = "gh"


def query_open_pr_via_gh(repo_path: Path, branch: str) -> str | None:
    """Ask the GitHub CLI whether an OPEN PR exists for ``branch``.

    Returns the PR URL or ``None`` if no open PR is found. Returns
    ``None`` on any subprocess / parse failure too — classifier callers
    treat "couldn't check" the same as "no PR" so a transient gh outage
    doesn't make the executor mis-classify a partial iteration as
    pr_created. The real PR state is reconciled by sweep (Plan 8).
    """
    try:
        result = run_text(
            [
                _GH_BINARY,
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "open",
                "--json",
                "url",
                "--limit",
                "1",
            ],
            cwd=str(repo_path),
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        # OSError catches FileNotFoundError (gh not installed),
        # PermissionError (gh present but not executable in this
        # container/CI), and any other transient OS-level failure.
        # All map to "no PR detected" — sweep (Plan 8) will reconcile.
        log.warning("gh pr list failed for %s: %s", branch, exc)
        return None
    if result.returncode != 0:
        log.warning(
            "gh pr list returned %d for %s: %s",
            result.returncode,
            branch,
            result.stderr.strip()[:200],
        )
        return None
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        log.warning("gh pr list returned non-JSON for %s: %s", branch, exc)
        return None
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if not isinstance(first, dict):
        return None
    url = first.get("url")
    return str(url) if isinstance(url, str) and url else None


def query_pr_checks(
    repo_path: Path,
    pr_number: int,
    *,
    timeout_seconds: float = 30.0,
) -> tuple[PrCheckState, list[str]]:
    """Call ``gh pr checks <num> --required --json bucket,name`` once.

    The verifier in Plan 18 uses this to decide whether to classify a
    just-pushed PR as ``pr_created`` (real CI green) or to fall back to
    ``partial`` (so the next iteration tries again).

    Returns a (state, names) tuple:

      * ``("pass", [])`` — every required check has ``bucket == "pass"``.
        Note: when ``gh`` returns an empty array (no required checks are
        configured on the branch) we ALSO return ``"pass"`` and log a
        WARNING. This PBI assumes the operator has required checks
        configured; if they don't, the verifier has nothing to gate on.
      * ``("fail", [<failed names>])`` — at least one required check has
        ``bucket == "fail"``. ``names`` is the list of failing check
        names so the classifier can surface them on stderr for the next
        iteration to read.
      * ``("pending", [])`` — gh exited 8 (at least one bucket is
        ``"pending"``). Caller's polling loop re-tries.
      * ``("error", [<one-line summary>])`` — gh missing, timed out,
        returned non-JSON, or some other unexpected failure. Caller
        treats as ``partial`` (re-poll next iteration).

    Failure mapping is intentionally pessimistic: anything we cannot
    confidently parse as "all required checks green" returns non-``pass``
    so the classifier never escalates a partial iteration to
    ``pr_created`` on a transient gh outage.
    """
    try:
        result = run_text(
            [
                _GH_BINARY,
                "pr",
                "checks",
                str(pr_number),
                "--required",
                "--json",
                "bucket,name",
            ],
            cwd=str(repo_path),
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        log.warning("gh pr checks timed out after %ss for PR #%d", timeout_seconds, pr_number)
        return ("error", [f"gh pr checks timed out after {timeout_seconds}s"])
    except OSError as exc:
        # FileNotFoundError (gh not installed), PermissionError, etc.
        log.warning("gh pr checks failed for PR #%d: %s", pr_number, exc)
        return ("error", [str(exc)])

    # ``gh pr checks --json bucket,name`` always emits a JSON array on
    # stdout when it ran at all — even when at least one check failed
    # (exit 1) or is pending (exit 8). We therefore parse the JSON FIRST
    # and use the buckets as the source of truth; the exit code is only a
    # fallback when stdout is empty/unparseable.
    if result.returncode == 8 and not (result.stdout or "").strip():
        return ("pending", [])

    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        if result.returncode == 8:
            return ("pending", [])
        summary = (result.stderr or "").strip()[:200]
        return ("error", [summary or f"gh pr checks exit {result.returncode}"])
    if not isinstance(payload, list):
        return ("error", ["gh pr checks JSON payload was not a list"])
    if not payload:
        # No required checks configured. The verifier has nothing to gate
        # on; we return "pass" but warn loudly so the operator notices.
        log.warning(
            "PR #%d has no required checks configured; treating as pass",
            pr_number,
        )
        return ("pass", [])

    failed: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            return ("error", ["gh pr checks JSON contained a non-object entry"])
        if item.get("bucket") == "fail":
            name = item.get("name")
            failed.append(str(name) if isinstance(name, str) else "<unnamed>")
    if failed:
        return ("fail", failed)

    buckets = {str(item.get("bucket")) for item in payload}
    if buckets == {"pass"}:
        return ("pass", [])
    if "pending" in buckets:
        return ("pending", [])
    # Any other state (e.g. all "skipping" / "cancel") is conservatively
    # treated as error so we never mis-classify it as pass.
    return ("error", ["unexpected bucket states: " + repr(sorted(buckets))])


def wait_for_pr_checks(
    repo_path: Path,
    pr_number: int,
    *,
    max_polls: int = 6,
    interval_seconds: float = 30.0,
) -> tuple[PrCheckState, list[str]]:
    """Poll ``query_pr_checks`` until terminal or the budget is exhausted.

    Polls up to ``max_polls`` times, sleeping ``interval_seconds`` between
    attempts. Total wall budget = ``max_polls * interval_seconds`` (default
    6 × 30 s = 3 minutes per iteration).

    A state is **terminal** if it lets the classifier make a confident
    decision now:

      * ``pass``    — CI green; classifier returns ``pr_created``.
      * ``fail``    — at least one required check failed; classifier
                      returns ``partial`` with the failed names visible
                      on stderr.
      * ``error``   — gh-side problem (binary missing, timeout, malformed
                      JSON). Retrying within the same iteration is
                      unlikely to help and would just burn the budget;
                      we surface the error to the caller, which treats
                      it as ``partial`` so the NEXT iteration re-polls.

    Non-terminal:

      * ``pending`` — at least one check is still running. Sleep and
                      re-poll.

    Returns ``("pending", [])`` if the budget is exhausted without a
    decision; the caller treats this as ``partial`` so the PBI stays in
    ``current/`` and the next iteration polls again.
    """
    if max_polls < 1:
        return ("pending", [])
    # Scale the per-call subprocess timeout to the configured poll
    # interval so the documented total wall budget actually holds. With
    # a hard-coded timeout, an operator setting interval_seconds=5 to
    # get faster feedback would still see each gh-call block up to 30 s
    # and overrun the 30-s total budget. Floor at 5 s to leave gh
    # enough time to spin up + hit the API even on slow links.
    per_call_timeout = max(interval_seconds, 5.0)
    for attempt in range(max_polls):
        state, names = query_pr_checks(repo_path, pr_number, timeout_seconds=per_call_timeout)
        if state != "pending":
            return (state, names)
        if attempt < max_polls - 1:
            time.sleep(interval_seconds)
    return ("pending", [])
