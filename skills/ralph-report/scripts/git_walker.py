"""Walk ``git log`` on the ralph-queue branch and extract typed timeline events.

Two public surfaces:

* :func:`parse_commit_subject` — pure regex matcher; the unit-test surface.
* :func:`walk_log` — runs ``git log --since=<window> <ref>`` and converts
  each matching commit subject into a :class:`TimelineEvent`. Commits whose
  ``--name-status`` output adds a ``.ralph/blocked/META-cycle-*.md`` file
  also emit a ``cycle_trip`` event keyed off the sentinel filename stem.

The grammar is deterministic — the executor commits state moves with
fixed prefixes (``chore(queue):``, ``chore(ralph-queue):``,
``feat(ralph-queue):``). Persist-iteration commits, doc updates, and
out-of-band housekeeping are filtered out.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

EventKind = Literal[
    "added",
    "claimed",
    "pr_opened",
    "shipped",
    "blocked",
    "cycle_trip",
]


@dataclass(frozen=True)
class TimelineEvent:
    kind: EventKind
    pbi_id: str
    commit_sha: str
    when: datetime


# PBI IDs are uppercase letters / digits / hyphens. Tightening the id charset
# (vs the broader ``[A-Za-z0-9_-]+`` in the original plan) rejects real-world
# false positives such as ``chore(queue): add target_repo + set status=inbox``,
# where ``target_repo`` would otherwise be captured as a PBI id.
_ID = r"[A-Z][A-Z0-9-]*"

_PATTERNS: tuple[tuple[re.Pattern[str], EventKind], ...] = (
    (re.compile(rf"^chore\(queue\): add (?P<id>{_ID})\b"), "added"),
    (
        re.compile(rf"^chore\(ralph-queue\): move (?P<id>{_ID}) from inbox to current\b"),
        "claimed",
    ),
    (
        re.compile(rf"^feat\(ralph-queue\): move (?P<id>{_ID}) from current to pending-pr\b"),
        "pr_opened",
    ),
    (
        re.compile(rf"^chore\(ralph-queue\): move (?P<id>{_ID}) from pending-pr to done\b"),
        "shipped",
    ),
    (
        re.compile(rf"^chore\(ralph-queue\): move (?P<id>{_ID}) from current to blocked\b"),
        "blocked",
    ),
)

_META_CYCLE_RE = re.compile(r"^\.ralph/blocked/META-cycle-[^/]+\.md$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_EPOCH = datetime.fromtimestamp(0)


def parse_commit_subject(subject: str) -> TimelineEvent | None:
    """Return a typed event for a known commit subject, else ``None``.

    The returned event carries empty ``commit_sha`` and a placeholder
    ``when`` — :func:`walk_log` populates both when it constructs events
    from real log output. This keeps the matcher pure and unit-testable
    without a git repo.
    """
    for pattern, kind in _PATTERNS:
        m = pattern.match(subject)
        if m is not None:
            return TimelineEvent(
                kind=kind,
                pbi_id=m.group("id"),
                commit_sha="",
                when=_EPOCH,
            )
    return None


def walk_log(
    *,
    repo_path: Path,
    ref: str = "origin/ralph-queue",
    since: str = "24.hours.ago",
) -> list[TimelineEvent]:
    """Return state-change events on ``ref`` from ``since`` to HEAD.

    Order matches ``git log`` (newest-first). Returns ``[]`` when the ref
    is absent or the repo is not a git repo at all.
    """
    fmt = "%H%x09%aI%x09%s"
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "log",
            ref,
            f"--since={since}",
            f"--pretty=format:{fmt}",
            "--name-status",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return []
    events: list[TimelineEvent] = []
    current_header: tuple[str, datetime] | None = None
    for raw in result.stdout.splitlines():
        if not raw.strip():
            current_header = None
            continue
        parts = raw.split("\t")
        if _is_commit_header(parts):
            sha = parts[0]
            iso = parts[1]
            subject = "\t".join(parts[2:])
            try:
                when = datetime.fromisoformat(iso)
            except ValueError:
                current_header = None
                continue
            current_header = (sha, when)
            event = parse_commit_subject(subject)
            if event is not None:
                events.append(
                    TimelineEvent(
                        kind=event.kind,
                        pbi_id=event.pbi_id,
                        commit_sha=sha,
                        when=when,
                    )
                )
            continue
        if current_header is None:
            continue
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1]
        if status.startswith("A") and _META_CYCLE_RE.match(path):
            sha, when = current_header
            events.append(
                TimelineEvent(
                    kind="cycle_trip",
                    pbi_id=Path(path).stem,
                    commit_sha=sha,
                    when=when,
                )
            )
    return events


def _is_commit_header(parts: list[str]) -> bool:
    """A header line has form ``SHA40\\tISO\\tSUBJECT``.

    Distinguishing by hex-SHA prefix avoids the fragility of negative
    ``startswith(("A\\t", "M\\t", ...))`` checks against the open set of
    name-status status letters (A/M/D/T/C/R/U, some with similarity
    indices).
    """
    return len(parts) >= 3 and bool(_SHA40_RE.match(parts[0]))
