"""Spawn ``claude -p`` against the current PBI and classify the outcome.

The spawner sets ``RALPH_PBI_DIR`` in the subprocess environment so the
spawned Claude session can locate its working PBI without having to
parse argv. ``classify_outcome`` then maps the (stdout, stderr,
exit_code, on-disk effects) tuple to one of four typed outcomes:

  * ``pr_created`` — stdout contains the ``PR created: <url>`` marker.
  * ``stuck``      — Ralph wrote STUCK.md into the PBI directory.
  * ``partial``    — Ralph exited zero with neither marker (multi-step
                     PBI: stay in current/, run again next iteration).
  * ``error``      — Non-zero exit code with no STUCK.md (transient
                     failure; loop driver currently treats this the
                     same as ``partial`` but the explicit kind makes
                     Plan 9's safety controls easier to wire later).

STUCK.md takes precedence over a PR-created marker because Ralph may
have produced a stale stdout line from an earlier step before writing
STUCK.md just before exit.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ralph_executor.config import ExecutorConfig
from ralph_executor.types import PBI

log = logging.getLogger(__name__)

OutcomeKind = Literal["pr_created", "stuck", "partial", "error"]

_PR_URL_RE = re.compile(r"PR created:\s*(\S+)", re.IGNORECASE)
_STUCK_FILENAME = "STUCK.md"


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


def spawn_claude_p(cfg: ExecutorConfig, pbi: PBI) -> ClaudeOutcome:
    """Run ``claude -p`` against the PBI and return a classified outcome."""
    argv = _build_argv(cfg, pbi)
    env = os.environ.copy()
    env["RALPH_PBI_DIR"] = str(pbi.path)
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
    return classify_outcome(
        pbi_dir=pbi.path,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
        duration_seconds=duration,
    )


def classify_outcome(
    *,
    pbi_dir: Path,
    stdout: str,
    stderr: str,
    exit_code: int,
    duration_seconds: float,
) -> ClaudeOutcome:
    """Map the raw (stdout, stderr, exit, on-disk) tuple to a typed outcome.

    Precedence (highest first):
      1. STUCK.md present on disk → ``stuck``
      2. Exit code non-zero       → ``error``
      3. stdout matches PR-created marker → ``pr_created``
      4. Otherwise                → ``partial``
    """
    stuck_present = (pbi_dir / _STUCK_FILENAME).is_file()
    pr_match = _PR_URL_RE.search(stdout)

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
    if pr_match:
        return ClaudeOutcome(
            kind="pr_created",
            pr_url=pr_match.group(1).strip(),
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
