"""Render the ralph-report HTML page from snapshot + timeline data.

Pure function (no I/O). Returns a full HTML document string starting
with ``<!DOCTYPE html>``. Bootstrap 5 is loaded from CDN; everything
else is inlined in the ``<style>`` block.

Types from sibling kebab-dir scripts (``snapshot.Snapshot`` /
``snapshot.MetaCycleSentinel`` / ``git_walker.TimelineEvent``) are
addressed structurally — they are passed in by the caller, never
imported here. This sidesteps the kebab-case dir / sibling-import
problem entirely (the same one the test loader works around via
``importlib.util.spec_from_file_location``).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

# Make ``scripts.pbi_reader`` importable; same shim ``snapshot.py`` uses.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.pbi_reader import PBIRow, PBIRowError  # noqa: E402

_BOOTSTRAP_CSS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"

_SEVERITY_BADGE_CLASS: dict[str, str] = {
    "critical": "bg-danger",
    "high": "text-bg-warning",
    "normal": "bg-primary",
    "low": "bg-secondary",
}

# Bootstrap 5 has no ``text-purple`` utility; use ``text-info`` for the
# PR-opened event so the colour-pill actually renders.
_EVENT_LABEL: dict[str, tuple[str, str]] = {
    "added": ("added", "text-primary"),
    "claimed": ("claimed", "text-success"),
    "pr_opened": ("PR opened", "text-info"),
    "shipped": ("shipped", "text-success fw-bold"),
    "blocked": ("blocked", "text-danger"),
    "cycle_trip": ("cycle-trip", "text-danger fw-bold"),
}


def render_page(
    *,
    repo_path: Path,
    snapshot: Any,
    events: list[Any],
    now: datetime,
) -> str:
    """Render the full dashboard page.

    ``snapshot`` is a ``snapshot.Snapshot`` (or any object exposing the
    same attribute surface). ``events`` is a list of
    ``git_walker.TimelineEvent``. ``now`` must be timezone-aware.
    """
    cutoff = now - timedelta(hours=24)
    shipped_events = sorted(
        (e for e in events if e.kind == "shipped" and e.when >= cutoff),
        key=lambda e: e.when,
        reverse=True,
    )
    timeline_events = sorted(
        (e for e in events if e.when >= cutoff),
        key=lambda e: e.when,
        reverse=True,
    )
    refresh_iso = now.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return _PAGE_TEMPLATE.format(
        bootstrap_css=_BOOTSTRAP_CSS,
        repo_path=escape(str(repo_path)),
        refresh_iso=escape(refresh_iso),
        current_panel=_render_current(snapshot.current),
        blocked_panel=_render_blocked(snapshot.blocked, snapshot.meta_cycle_sentinels),
        inbox_panel=_render_inbox(snapshot.inbox),
        pending_pr_panel=_render_pending_pr(snapshot.pending_pr),
        done_panel=_render_done(shipped_events, now),
        timeline_panel=_render_timeline(timeline_events),
    )


def _render_pbi_row(row: PBIRow | PBIRowError) -> str:
    if isinstance(row, PBIRowError):
        return (
            '<div class="row-pbi">'
            f'<span class="id">{escape(row.pbi_dir.name)}</span>'
            f'<span class="text-danger small">{escape(row.message)}</span>'
            "</div>"
        )
    sev_class = _SEVERITY_BADGE_CLASS.get(row.severity, "bg-secondary")
    return (
        '<div class="row-pbi">'
        f'<span class="id">{escape(row.pbi_id)}</span>'
        f'<span class="badge badge-pill {sev_class}">{escape(row.severity)}</span>'
        f'<span class="badge badge-pill bg-info text-dark">{escape(row.pbi_type)}</span>'
        f'<span class="text-muted small">{escape(row.title)}</span>'
        "</div>"
    )


def _render_current(rows: list[PBIRow | PBIRowError]) -> str:
    body = (
        '<div class="empty-state">No current PBI.</div>'
        if not rows
        else "".join(_render_pbi_row(r) for r in rows)
    )
    return _panel("Current", len(rows), "panel-current text-success", body)


def _render_blocked(
    rows: list[PBIRow | PBIRowError],
    sentinels: list[Any],
) -> str:
    total = len(rows) + len(sentinels)
    pieces: list[str] = [_render_pbi_row(r) for r in rows]
    for s in sentinels:
        pieces.append(
            '<div class="row-pbi">'
            f'<span class="id">{escape(s.filename)}</span>'
            '<span class="badge badge-pill bg-danger">sentinel</span>'
            '<span class="text-muted small">cycle-detector trip</span>'
            "</div>"
        )
    body = "".join(pieces) if pieces else '<div class="empty-state">Nothing blocked.</div>'
    return _panel("Blocked", total, "panel-blocked text-danger", body)


def _render_inbox(rows: list[PBIRow | PBIRowError]) -> str:
    body = (
        '<div class="empty-state">Inbox is empty.</div>'
        if not rows
        else "".join(_render_pbi_row(r) for r in rows)
    )
    return _panel("Inbox", len(rows), "panel-snapshot", body)


def _render_pending_pr(rows: list[PBIRow | PBIRowError]) -> str:
    body = (
        '<div class="empty-state">No PRs in review.</div>'
        if not rows
        else "".join(_render_pbi_row(r) for r in rows)
    )
    return _panel("Pending PR", len(rows), "panel-snapshot", body)


def _render_done(shipped: list[Any], now: datetime) -> str:
    if not shipped:
        body = '<div class="empty-state">Nothing shipped in the last 24h.</div>'
    else:
        lines: list[str] = []
        for event in shipped:
            age = _format_age(now - event.when)
            lines.append(
                '<div class="row-pbi">'
                f'<span class="id">{escape(event.pbi_id)}</span>'
                '<span class="badge badge-pill bg-success">shipped</span>'
                f'<span class="text-muted small">{age} ago</span>'
                "</div>"
            )
        body = "".join(lines)
    return _panel("Done (last 24h)", len(shipped), "panel-window text-secondary", body)


def _render_timeline(events: list[Any]) -> str:
    if not events:
        body = '<div class="empty-state">No state-change events in the last 24h.</div>'
    else:
        lines: list[str] = []
        for e in events:
            label, css = _EVENT_LABEL.get(e.kind, (e.kind, ""))
            hhmm = e.when.astimezone(UTC).strftime("%H:%M")
            lines.append(
                '<div class="timeline-row">'
                f'<span class="t-time">{escape(hhmm)}</span>'
                f'<span class="t-kind {css}">{escape(label)}</span>'
                f"<span>{escape(e.pbi_id)}</span>"
                "</div>"
            )
        body = "".join(lines)
    return _panel(
        "Activity timeline (last 24h, state-changes only)",
        len(events),
        "panel-window text-secondary",
        body,
    )


def _panel(title: str, count: int, classes: str, body: str) -> str:
    return (
        f'<div class="mock {classes} mb-3">'
        f'<h6>{escape(title)} <span class="badge bg-secondary">{count}</span></h6>'
        f"{body}"
        "</div>"
    )


def _format_age(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        # Clamp future-dated events (clock skew on the source repo) so we
        # don't render "-3600s" in the dashboard.
        return "0s"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ralph-report</title>
<link rel="stylesheet" href="{bootstrap_css}">
<style>
  body {{ background:#eef2f6; padding-bottom:3rem; }}
  .container-narrow {{ max-width: 1200px; }}
  .mock {{
    background:#fff; border-radius:0.5rem;
    box-shadow:0 1px 3px rgba(0,0,0,0.08); padding:1rem;
  }}
  .mock h6 {{ font-weight:600; margin-bottom:0.5rem; }}
  .panel-snapshot {{ border-left:4px solid #0d6efd; }}
  .panel-window   {{ border-left:4px solid #6c757d; }}
  .panel-current  {{ border-left:4px solid #198754; }}
  .panel-blocked  {{ border-left:4px solid #dc3545; }}
  .row-pbi {{
    font-size:0.85rem; padding:0.3rem 0.5rem;
    border-bottom:1px solid #f0f2f5;
    display:flex; gap:0.5rem; align-items:center;
  }}
  .row-pbi:last-child {{ border-bottom:none; }}
  .row-pbi .id {{
    font-family:monospace; flex:1;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }}
  .badge-pill {{ font-size:0.7rem; padding:0.15rem 0.45rem; }}
  .timeline-row {{
    font-size:0.85rem; padding:0.3rem 0.6rem;
    border-bottom:1px dashed #e9ecef;
    font-family:monospace; display:flex; gap:0.75rem;
  }}
  .timeline-row:last-child {{ border-bottom:none; }}
  .timeline-row .t-time {{ color:#6c757d; min-width:48px; }}
  .timeline-row .t-kind {{ min-width:90px; }}
  .empty-state {{
    color:#adb5bd; font-style:italic;
    font-size:0.85rem; padding:0.3rem 0.5rem;
  }}
  .header-bar {{
    display:flex; align-items:center;
    gap:1rem; margin-bottom:1rem;
  }}
  .repo-pill {{
    background:#212529; color:#fff;
    padding:0.3rem 0.6rem; border-radius:0.3rem;
    font-family:monospace; font-size:0.8rem;
  }}
</style>
</head>
<body>
<div class="container container-narrow py-4">
  <header class="header-bar">
    <h1 class="mb-0 h3">ralph-report</h1>
    <span class="repo-pill">{repo_path}</span>
    <span class="ms-auto text-muted small">refreshed {refresh_iso} &middot; F5 to update</span>
  </header>
  {current_panel}
  {blocked_panel}
  <div class="row g-3 mb-3">
    <div class="col-md-6">{inbox_panel}</div>
    <div class="col-md-6">{pending_pr_panel}</div>
  </div>
  {done_panel}
  {timeline_panel}
</div>
</body>
</html>
"""
