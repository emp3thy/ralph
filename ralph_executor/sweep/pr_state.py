"""Wrap the PR skill scripts so the sweep can fetch PR state.

The PR skill exposes ``show.py`` and ``read_threads.py`` (Plan 5). This
module invokes each via ``subprocess.run`` with ``check=False`` (so we can
attach ``stderr`` to the exception on failure), then parses the JSON they
print on stdout into the typed dataclasses in ``ralph_executor.sweep.types``.

We deliberately do NOT import ``requests`` here — every PR-host HTTP call
goes through the skill so the sweep can be tested without an HTTP mock layer
and so the skill remains the single source of truth for the host's REST
quirks. The skill binding (``ado-pr`` vs ``pr-github``) is decided by the
loop driver (Task 6); ``fetch`` is host-agnostic.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ralph_executor.sweep.types import (
    CiStatus,
    CommentSnapshot,
    PrSnapshot,
    PrStatus,
    ThreadSnapshot,
    ThreadStatus,
)


class AdoSkillError(RuntimeError):
    """The PR skill returned a non-zero exit or unparseable output."""


@dataclass(frozen=True)
class _RunOutput:
    stdout: str
    stderr: str
    returncode: int


def fetch(*, pr_id: int, skill_scripts_path: Path) -> PrSnapshot:
    """Invoke ``show.py`` and ``read_threads.py`` for ``pr_id``; build PrSnapshot."""
    show_payload = _invoke_skill(
        skill_scripts_path / "show.py",
        [str(pr_id)],
    )
    threads_payload = _invoke_skill(
        skill_scripts_path / "read_threads.py",
        [str(pr_id)],
    )
    threads = _parse_threads(threads_payload)
    last_activity_at = _compute_last_activity_at(show_payload, threads)
    show_dict: dict[str, Any] = show_payload if isinstance(show_payload, dict) else {}
    return PrSnapshot(
        # dict.get(key, default) returns default only when KEY ABSENT.
        # If the skill returns {"pr_id": null}, .get returns None and
        # int(None) raises TypeError, escaping AdoSkillError handling.
        # Use `or pr_id` to coerce None/0/"" back to the fallback.
        pr_id=int(show_dict.get("pr_id") or pr_id),
        title=str(show_dict.get("title", "")),
        pr_status=_coerce_pr_status(show_dict.get("status")),
        ci_status=_coerce_ci_status(show_dict.get("ci_status")),
        source_branch=str(show_dict.get("source_branch", "")),
        target_branch=str(show_dict.get("target_branch", "main")),
        reviewers=tuple(str(r) for r in (show_dict.get("reviewers") or ()) if isinstance(r, str)),
        threads=threads,
        last_activity_at=last_activity_at,
        url=str(show_dict.get("url", "")),
    )


# ----------------------------------------------------------------------
# Subprocess + JSON
# ----------------------------------------------------------------------


def _invoke_skill(script_path: Path, args: list[str]) -> dict[str, Any] | list[Any]:
    if not script_path.is_file():
        raise AdoSkillError(f"skill script not found: {script_path}")
    cmd = [sys.executable, str(script_path), *args]
    result = subprocess.run(  # noqa: S603
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    output = _RunOutput(
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )
    if output.returncode != 0:
        raise AdoSkillError(
            f"{script_path.name} exited with exit code {output.returncode}: "
            f"{output.stderr.strip() or '<no stderr>'}"
        )
    try:
        parsed = json.loads(output.stdout)
    except json.JSONDecodeError as err:
        raise AdoSkillError(
            f"{script_path.name} returned non-JSON output: {err}\nstdout was: {output.stdout!r}"
        ) from err
    if not isinstance(parsed, dict | list):
        raise AdoSkillError(f"{script_path.name} returned non-object JSON: {type(parsed).__name__}")
    return parsed


# ----------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------


_VALID_PR_STATUSES: set[str] = {"active", "completed", "abandoned"}
_VALID_CI_STATUSES: set[str] = {"succeeded", "failed", "running", "none"}
_VALID_THREAD_STATUSES: set[str] = {
    "active",
    "pending",
    "fixed",
    "closed",
    "wontFix",
    "byDesign",
}


def _coerce_pr_status(raw: object) -> PrStatus:
    if isinstance(raw, str) and raw in _VALID_PR_STATUSES:
        return raw  # type: ignore[return-value]
    return "unknown"


def _coerce_ci_status(raw: object) -> CiStatus:
    if isinstance(raw, str) and raw in _VALID_CI_STATUSES:
        return raw  # type: ignore[return-value]
    return "unknown"


def _coerce_thread_status(raw: object) -> ThreadStatus:
    if isinstance(raw, str) and raw in _VALID_THREAD_STATUSES:
        return raw  # type: ignore[return-value]
    return "unknown"


def _parse_iso(raw: object) -> datetime:
    if not isinstance(raw, str) or not raw:
        return datetime.min.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_threads(payload: object) -> tuple[ThreadSnapshot, ...]:
    if not isinstance(payload, list):
        return ()
    threads: list[ThreadSnapshot] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        comments: list[CommentSnapshot] = []
        for c in entry.get("comments") or ():
            if not isinstance(c, dict):
                continue
            comments.append(
                CommentSnapshot(
                    thread_id=int(entry.get("thread_id") or 0),
                    comment_id=int(c.get("comment_id") or 0),
                    author_email=str(c.get("author_email", "")).lower(),
                    posted_at=_parse_iso(c.get("posted_at")),
                    text=str(c.get("text", "")),
                    file_path=(
                        str(c["file_path"]) if isinstance(c.get("file_path"), str) else None
                    ),
                    line=(int(c["line"]) if isinstance(c.get("line"), int) else None),
                )
            )
        threads.append(
            ThreadSnapshot(
                thread_id=int(entry.get("thread_id") or 0),
                status=_coerce_thread_status(entry.get("status")),
                file_path=(
                    str(entry["file_path"]) if isinstance(entry.get("file_path"), str) else None
                ),
                line=(int(entry["line"]) if isinstance(entry.get("line"), int) else None),
                comments=tuple(comments),
            )
        )
    return tuple(threads)


def _compute_last_activity_at(
    show_payload: object,
    threads: tuple[ThreadSnapshot, ...],
) -> datetime:
    if isinstance(show_payload, dict):
        last = _parse_iso(show_payload.get("last_activity_at"))
    else:
        last = datetime.min.replace(tzinfo=UTC)
    for thread in threads:
        for comment in thread.comments:
            if comment.posted_at > last:
                last = comment.posted_at
    return last
