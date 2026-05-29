"""``ralph-status`` skill entry point.

Read-only view of the single queue clone at
``<workspace_root>/queue/``. Walks ``.ralph/<state>/`` for every state
folder, parses each PBI's frontmatter via ``scripts.pbi_reader``, and
renders the rows as a fixed-width table or JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Make ``scripts.pbi_reader`` importable when this script is invoked
# directly via ``uv run python skills/ralph-status/scripts/status.py``.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.pbi_reader import (  # noqa: E402
    STATE_FOLDERS,
    PBIRow,
    PBIRowError,
    enumerate_state,
)
from scripts.queue_writer import (  # noqa: E402
    QueueWriterError,
    acquire_queue_clone,
    resolve_queue_branch,
    resolve_queue_repo,
    resolve_workspace_root,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-status",
        description=(
            "Read-only view of the ralph queue. Reads "
            "$RALPH_WORKSPACE/queue/.ralph/ and groups output by target_repo."
        ),
    )
    parser.add_argument(
        "--state",
        choices=STATE_FOLDERS,
        help="Filter rows to a single state.",
    )
    parser.add_argument(
        "--target-repo",
        dest="target_repo",
        help="Filter rows to PBIs whose target_repo matches this URL.",
    )
    parser.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Emit JSON to stdout instead of a fixed-width table.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Override workspace_root from ~/.ralph/config.toml.",
    )
    parser.add_argument(
        "--queue-repo",
        dest="queue_repo",
        help="Override queue_repo from ~/.ralph/config.toml.",
    )
    parser.add_argument(
        "--queue-branch",
        dest="queue_branch",
        metavar="BRANCH",
        help="Override the queue_branch from ~/.ralph/config.toml for this run (default: ralph-queue).",
    )
    return parser.parse_args(argv)


_COLUMN_ORDER: tuple[str, ...] = (
    "TARGET",
    "STATE",
    "ID",
    "TYPE",
    "SEVERITY",
    "AGE",
    "TITLE",
)

_TARGET_DISPLAY_MAX = 50


def _age_string(created_at: datetime | None) -> str:
    if created_at is None:
        return "?"
    now = datetime.now(tz=UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    delta = now - created_at
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{max(total_seconds, 0)}s"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"


def _truncate_target(target: str) -> str:
    if len(target) <= _TARGET_DISPLAY_MAX:
        return target
    return target[: _TARGET_DISPLAY_MAX - 3] + "..."


def _row_to_cells(row: PBIRow | PBIRowError) -> list[str]:
    if isinstance(row, PBIRow):
        return [
            _truncate_target(row.target_repo) if row.target_repo else "?",
            row.state,
            row.pbi_id,
            row.pbi_type,
            row.severity,
            _age_string(row.created_at),
            row.title,
        ]
    return [
        "?",
        row.state,
        row.pbi_dir.name,
        "?",
        "?",
        "?",
        f"(parse error) {row.message}",
    ]


def _group_and_sort(rows: list[PBIRow | PBIRowError]) -> list[PBIRow | PBIRowError]:
    """Stable sort by (target_repo, state, created_at) so output is grouped."""

    def key(row: PBIRow | PBIRowError) -> tuple[str, str, str]:
        if isinstance(row, PBIRow):
            created = row.created_at.isoformat() if row.created_at else ""
            return (row.target_repo or "", row.state, created)
        return ("", row.state, "")

    return sorted(rows, key=key)


def _render_table(rows: list[PBIRow | PBIRowError]) -> str:
    cells = [list(_COLUMN_ORDER)]
    for row in rows:
        cells.append(_row_to_cells(row))

    widths = [0] * len(_COLUMN_ORDER)
    for row_cells in cells:
        for idx, cell in enumerate(row_cells):
            widths[idx] = max(widths[idx], len(cell))

    lines: list[str] = []
    for row_cells in cells:
        parts = [cell.ljust(widths[idx]) for idx, cell in enumerate(row_cells[:-1])]
        parts.append(row_cells[-1])
        lines.append("  ".join(parts).rstrip())
    return "\n".join(lines) + "\n"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _row_to_json(row: PBIRow | PBIRowError) -> dict[str, object]:
    if isinstance(row, PBIRow):
        return {
            "target_repo": row.target_repo or None,
            "state": row.state,
            "id": row.pbi_id,
            "type": row.pbi_type,
            "severity": row.severity,
            "attempts": row.attempts,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "title": row.title,
            "pbi_dir": row.relative_pbi_dir(),
            "error": None,
        }
    return {
        "target_repo": None,
        "state": row.state,
        "id": row.pbi_dir.name,
        "type": None,
        "severity": None,
        "attempts": None,
        "created_at": None,
        "updated_at": None,
        "title": None,
        "pbi_dir": row.relative_pbi_dir(),
        "error": row.message,
    }


def _render_json(rows: list[PBIRow | PBIRowError], errors: list[str]) -> str:
    payload: dict[str, object] = {
        "rows": [_row_to_json(row) for row in rows],
        "errors": errors,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _collect_rows(
    queue_clone: Path,
    *,
    states: tuple[str, ...],
) -> list[PBIRow | PBIRowError]:
    rows: list[PBIRow | PBIRowError] = []
    for state in states:
        rows.extend(enumerate_state(queue_clone, state))
    return rows


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        workspace_root = resolve_workspace_root(args.workspace)
        queue_repo = resolve_queue_repo(args.queue_repo)
        queue_branch = resolve_queue_branch(args.queue_branch)
        queue_clone = acquire_queue_clone(workspace_root, queue_repo, queue_branch)
    except QueueWriterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        states: tuple[str, ...] = (args.state,) if args.state else STATE_FOLDERS
        rows = _collect_rows(queue_clone, states=states)

        if args.target_repo:
            # PBIRowError rows have no parseable target_repo to filter on
            # but the SKILL.md contract requires them to appear in `rows`
            # so the caller can see parse failures. Pass them through the
            # filter unconditionally; only PBIRow gets target_repo-matched.
            filtered: list[PBIRow | PBIRowError] = [
                row
                for row in rows
                if isinstance(row, PBIRowError)
                or (isinstance(row, PBIRow) and row.target_repo == args.target_repo)
            ]
            rows = filtered

        rows = _group_and_sort(rows)

        if args.emit_json:
            sys.stdout.write(_render_json(rows, errors=[]))
        else:
            sys.stdout.write(_render_table(rows))
            pbi_count = len(rows)
            print(
                f"# {pbi_count} PBI(s) in {queue_clone} (states: {', '.join(states)})",
                file=sys.stderr,
            )
    except Exception as exc:
        # Per SKILL.md: top-level failures print to stderr and exit 2.
        # Catch-all preserves the documented contract even if a future
        # change to _render_* or _collect_rows raises something new.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
