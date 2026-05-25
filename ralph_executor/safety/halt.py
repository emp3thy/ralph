"""Halt-and-acknowledge: META-BUG writer, sentinel file, optional webhook.

When :func:`halt_and_acknowledge` is called, the executor:

1. Snapshots the queue (inbox / current / pending-pr / done / blocked)
   into a :class:`StateSnapshot`.
2. Writes a META-BUG markdown file under ``.ralph/blocked/`` carrying
   the snapshot plus a per-signal trace.
3. Writes the sentinel file at ``.ralph/state/halted``. The sentinel
   records the META-BUG id and contains two placeholder lines a human
   must fill in (``acknowledged_by:`` and ``acknowledged_at:``).
   :func:`check_halt_sentinel` reports the executor's state based on
   whether those placeholders are still empty.
4. Optionally POSTs a small JSON payload to the URL named by the
   ``RALPH_HALT_WEBHOOK`` env var (override via the ``webhook_url=``
   argument in tests). When unset, the function logs the META-BUG id
   and summary to stderr.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import requests

from ralph_executor.safety.cycle_detector import CycleSignal

_META_BUG_PREFIX = "META-cycle-"
_META_BUG_SUFFIX = ".md"
_BLOCKED_RELATIVE = ".ralph/blocked"
_SENTINEL_RELATIVE = ".ralph/state/halted"
_SENTINEL_PLACEHOLDER_BY = "acknowledged_by:"
_SENTINEL_PLACEHOLDER_AT = "acknowledged_at:"


# ----------------------------------------------------------------------
# Dataclasses + enums
# ----------------------------------------------------------------------


class HaltStatus(StrEnum):
    RUNNING = "running"
    HALTED = "halted"
    ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True)
class StateSnapshot:
    repo_path: Path
    inbox: tuple[str, ...]
    current: tuple[str, ...]
    pending_pr: tuple[str, ...]
    done: tuple[str, ...]
    blocked: tuple[str, ...]
    taken_at: datetime


@dataclass(frozen=True)
class MetaBug:
    id: str
    path: Path
    severity: str
    summary: str
    signals: tuple[CycleSignal, ...]
    created_at: datetime
    snapshot: StateSnapshot | None = None


class HaltedError(RuntimeError):
    """Raised by the loop driver when the halt sentinel forbids restart."""

    def __init__(
        self,
        *,
        meta_bug_id: str,
        meta_bug_path: Path,
        sentinel_path: Path,
    ) -> None:
        super().__init__(
            f"executor is halted by {meta_bug_id} "
            f"(see {meta_bug_path}); acknowledge by editing "
            f"{sentinel_path}"
        )
        self.meta_bug_id = meta_bug_id
        self.meta_bug_path = meta_bug_path
        self.sentinel_path = sentinel_path


# ----------------------------------------------------------------------
# Snapshot
# ----------------------------------------------------------------------


def snapshot_state(repo: Path) -> StateSnapshot:
    def _ls(bucket: str) -> tuple[str, ...]:
        directory = repo / ".ralph" / bucket
        if not directory.is_dir():
            return ()
        entries = sorted(p.name for p in directory.iterdir() if p.is_dir())
        return tuple(entries)

    return StateSnapshot(
        repo_path=repo,
        inbox=_ls("inbox"),
        current=_ls("current"),
        pending_pr=_ls("pending-pr"),
        done=_ls("done"),
        blocked=_ls("blocked"),
        taken_at=datetime.now(tz=UTC),
    )


# ----------------------------------------------------------------------
# META-BUG
# ----------------------------------------------------------------------


def _meta_bug_id(now: datetime) -> str:
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{_META_BUG_PREFIX}{stamp}"


def write_meta_bug(
    *,
    repo: Path,
    snapshot: StateSnapshot,
    signals: list[CycleSignal],
    now: datetime | None = None,
) -> MetaBug:
    """Render the META-BUG markdown file and return its dataclass."""
    created_at = now or datetime.now(tz=UTC)
    meta_id = _meta_bug_id(created_at)
    blocked_root = repo / _BLOCKED_RELATIVE
    blocked_root.mkdir(parents=True, exist_ok=True)
    path = blocked_root / f"{meta_id}{_META_BUG_SUFFIX}"

    summary = ", ".join(signal.kind.value for signal in signals)
    lines: list[str] = [
        "---",
        f"id: {meta_id}",
        "type: meta",
        "severity: critical",
        "status: blocked",
        f"created_at: {created_at.isoformat()}",
        f"summary: {summary}",
        "---",
        "",
        f"# {meta_id}",
        "",
        "Cycle detector tripped. Halt is in effect; the executor's main",
        "loop refuses to start until the sentinel at",
        f"`{_SENTINEL_RELATIVE}` is acknowledged (fill in",
        "`acknowledged_by` and `acknowledged_at`).",
        "",
        "## Signals",
        "",
    ]
    for signal in signals:
        lines.extend(
            [
                f"### {signal.kind.value} ({signal.severity})",
                "",
                signal.description,
                "",
            ]
        )
        if signal.supporting_events:
            lines.append("Supporting events:")
            for event in signal.supporting_events:
                lines.append(
                    f"- `{event.kind.value}` "
                    f"at {event.recorded_at.isoformat()} "
                    f"(pbi={event.pbi_id})"
                )
            lines.append("")
    inbox_str = ", ".join(snapshot.inbox) if snapshot.inbox else "(empty)"
    current_str = ", ".join(snapshot.current) if snapshot.current else "(empty)"
    pending_str = ", ".join(snapshot.pending_pr) if snapshot.pending_pr else "(empty)"
    done_str = ", ".join(snapshot.done) if snapshot.done else "(empty)"
    blocked_str = ", ".join(snapshot.blocked) if snapshot.blocked else "(empty)"
    lines.extend(
        [
            "## Queue snapshot",
            "",
            f"- inbox ({len(snapshot.inbox)}): {inbox_str}",
            f"- current ({len(snapshot.current)}): {current_str}",
            f"- pending-pr ({len(snapshot.pending_pr)}): {pending_str}",
            f"- done ({len(snapshot.done)}): {done_str}",
            f"- blocked ({len(snapshot.blocked)}): {blocked_str}",
            "",
            "## How to acknowledge",
            "",
            "1. Read the signals above and the supporting events.",
            "2. Decide whether the underlying cause is fixed. If not,",
            "   resolve the cause BEFORE acknowledging -- the same cycle",
            "   will re-trigger on restart.",
            "3. Edit `.ralph/state/halted` and fill in the",
            "   `acknowledged_by` and `acknowledged_at` lines with your",
            "   identity and the current timestamp.",
            "4. The executor will resume on its next iteration.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return MetaBug(
        id=meta_id,
        path=path,
        severity="critical",
        summary=summary,
        signals=tuple(signals),
        created_at=created_at,
        snapshot=snapshot,
    )


# ----------------------------------------------------------------------
# Sentinel
# ----------------------------------------------------------------------


def write_halt_sentinel(*, repo: Path, meta_bug_id: str) -> Path:
    path = repo / _SENTINEL_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"# Ralph halt sentinel\n"
        f"#\n"
        f"# The executor refuses to start its main loop while this file\n"
        f"# is unacknowledged. To acknowledge: fill in BOTH the\n"
        f"# acknowledged_by and acknowledged_at lines, then save.\n"
        f"# To resume from a clean state instead, delete this file AND\n"
        f"# the META-BUG it references after fixing the underlying cause.\n"
        f"#\n"
        f"meta_bug_id: {meta_bug_id}\n"
        f"halted_at: {datetime.now(tz=UTC).isoformat()}\n"
        f"acknowledged_by:\n"
        f"acknowledged_at:\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def check_halt_sentinel(repo: Path) -> HaltStatus:
    path = repo / _SENTINEL_RELATIVE
    if not path.is_file():
        return HaltStatus.RUNNING
    text = path.read_text(encoding="utf-8")
    by_filled = _is_filled(text, _SENTINEL_PLACEHOLDER_BY)
    at_filled = _is_filled(text, _SENTINEL_PLACEHOLDER_AT)
    if by_filled and at_filled:
        return HaltStatus.ACKNOWLEDGED
    return HaltStatus.HALTED


def _is_filled(text: str, key: str) -> bool:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(key):
            value = line[len(key) :].strip()
            return value != ""
    return False


# ----------------------------------------------------------------------
# Webhook notification
# ----------------------------------------------------------------------


def notify_halt(meta_bug: MetaBug, *, webhook_url: str | None) -> None:
    payload = {
        "meta_bug_id": meta_bug.id,
        "severity": meta_bug.severity,
        "summary": meta_bug.summary,
        "created_at": meta_bug.created_at.isoformat(),
        "path": str(meta_bug.path),
        "signals": [
            {
                "kind": s.kind.value,
                "description": s.description,
                "severity": s.severity,
            }
            for s in meta_bug.signals
        ],
    }
    if not webhook_url:
        print(
            f"RALPH HALT: {meta_bug.id} -- {meta_bug.summary} (see {meta_bug.path})",
            file=sys.stderr,
        )
        return
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        # Notification failure must NOT mask the halt itself; log and move on.
        print(
            f"RALPH HALT NOTIFY FAILED: {exc!r}; META-BUG {meta_bug.id} "
            f"is still written at {meta_bug.path}",
            file=sys.stderr,
        )


# ----------------------------------------------------------------------
# Top-level orchestrator
# ----------------------------------------------------------------------


def halt_and_acknowledge(
    *,
    repo: Path,
    signals: list[CycleSignal],
    now: datetime | None = None,
    webhook_env: str = "RALPH_HALT_WEBHOOK",
) -> MetaBug:
    """Snapshot state, write META-BUG + sentinel, fire webhook.

    Returns the :class:`MetaBug`. The caller (the executor loop driver)
    is expected to raise :class:`HaltedError` immediately after -- there
    is no return-to-loop path once a META-BUG is written. The current
    PBI is intentionally NOT moved.
    """
    snapshot = snapshot_state(repo)
    meta = write_meta_bug(
        repo=repo,
        snapshot=snapshot,
        signals=list(signals),
        now=now,
    )
    write_halt_sentinel(repo=repo, meta_bug_id=meta.id)
    webhook_url = os.environ.get(webhook_env, "").strip() or None
    notify_halt(meta, webhook_url=webhook_url)
    return meta
