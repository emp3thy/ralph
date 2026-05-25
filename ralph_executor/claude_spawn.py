"""Spawn ``claude -p`` against the current PBI and classify the outcome.

The spawner sets ``RALPH_PBI_DIR`` in the subprocess environment so the
spawned Claude session can locate its working PBI without having to
parse argv. ``classify_outcome`` then maps the (stdout, stderr,
exit_code, on-disk effects, pr-lookup) tuple to one of four typed
outcomes:

  * ``pr_created`` — an OPEN PR exists for the PBI's feature branch
                     (``ralph/<PBI-ID>``). Source of truth is the git
                     host, not Claude's stdout — Claude's stdout is
                     unreliable because PROMPT.md doesn't mandate a
                     specific marker line and the pr-skill returns its
                     own JSON, not a plain "PR created" message.
  * ``stuck``      — Ralph wrote STUCK.md into the PBI directory.
  * ``partial``    — Ralph exited zero with no STUCK.md and no PR
                     (multi-step PBI: stay in current/, run again).
  * ``error``      — Non-zero exit code with no STUCK.md (transient
                     failure; loop driver currently treats this the
                     same as ``partial`` but the explicit kind makes
                     Plan 9's safety controls easier to wire later).

STUCK.md takes precedence over a PR-created signal because Ralph may
have written STUCK.md after pushing the feature branch (e.g. PR was
opened but a later step failed).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ralph_executor.config import ExecutorConfig
from ralph_executor.types import PBI

log = logging.getLogger(__name__)

OutcomeKind = Literal["pr_created", "stuck", "partial", "error"]

_STUCK_FILENAME = "STUCK.md"
_GH_BINARY = "gh"


@dataclass(frozen=True)
class ClaudeOutcome:
    """Result of a single ``claude -p`` invocation against the current PBI."""

    kind: OutcomeKind
    pr_url: str | None
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float


def _build_argv(cfg: ExecutorConfig, pbi: PBI) -> list[str]:
    """Compose the argv passed to the ``claude`` binary.

    ``-p`` puts Claude in non-interactive print mode. The PBI directory
    path is forwarded both as an argument (the standing PROMPT.md tells
    Ralph to read it) and via the ``RALPH_PBI_DIR`` environment variable
    (which test stand-ins use to locate the PBI on disk).
    """
    argv = [
        cfg.claude_binary,
        "-p",
        (
            "Read ./prompt/PROMPT.md and work the PBI in "
            f"{pbi.path}. Follow the standing instructions."
        ),
    ]
    return argv


def _query_open_pr_via_gh(repo_path: Path, branch: str) -> str | None:
    """Ask the GitHub CLI whether an OPEN PR exists for ``branch``.

    Returns the PR URL or ``None`` if no open PR is found. Returns
    ``None`` on any subprocess / parse failure too — classifier callers
    treat "couldn't check" the same as "no PR" so a transient gh outage
    doesn't make the executor mis-classify a partial iteration as
    pr_created. The real PR state is reconciled by sweep (Plan 8).
    """
    try:
        result = subprocess.run(
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
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
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


def spawn_claude_p(cfg: ExecutorConfig, pbi: PBI) -> ClaudeOutcome:
    """Run ``claude -p`` against the PBI and return a classified outcome."""
    argv = _build_argv(cfg, pbi)
    env = os.environ.copy()
    env["RALPH_PBI_DIR"] = str(pbi.path)
    # Only propagate ANTHROPIC_API_KEY when cfg actually carries one.
    # Empty string breaks claude CLI's OAuth fallback — leave it absent
    # so the claude CLI picks up its own OAuth session.
    if cfg.anthropic_api_key:
        env.setdefault("ANTHROPIC_API_KEY", cfg.anthropic_api_key)
    log.info("spawning %s for PBI %s", argv[0], pbi.id)
    start = time.monotonic()
    result = subprocess.run(
        argv,
        cwd=str(cfg.repo_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    duration = time.monotonic() - start
    # Resolve PR state via gh AFTER Claude exits — this is the source of
    # truth for the "did Claude create a PR?" decision. stdout parsing
    # would be unreliable (PROMPT.md doesn't mandate a marker line and
    # the pr-skill emits its own JSON envelope).
    feature_branch = f"ralph/{pbi.id}"
    pr_url = _query_open_pr_via_gh(cfg.repo_path, feature_branch)
    return classify_outcome(
        pbi_dir=pbi.path,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
        duration_seconds=duration,
        pr_url=pr_url,
    )


def classify_outcome(
    *,
    pbi_dir: Path,
    stdout: str,
    stderr: str,
    exit_code: int,
    duration_seconds: float,
    pr_url: str | None = None,
    pr_lookup: Callable[[], str | None] | None = None,
) -> ClaudeOutcome:
    """Map the raw (stdout, stderr, exit, on-disk, pr-state) tuple to a
    typed outcome.

    Precedence (highest first):
      1. STUCK.md present on disk → ``stuck``
      2. Exit code non-zero       → ``error``
      3. open PR exists for the feature branch → ``pr_created``
         (``pr_url`` is taken directly; if not provided, ``pr_lookup``
         is called once and its return value is used)
      4. Otherwise                → ``partial``

    ``pr_url`` and ``pr_lookup`` exist so tests can inject the PR-state
    answer without monkeypatching subprocess. Production callers pass
    ``pr_url`` (already resolved by ``_query_open_pr_via_gh``).
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
        return ClaudeOutcome(
            kind="pr_created",
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
