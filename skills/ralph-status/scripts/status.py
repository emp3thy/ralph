"""``ralph-status`` skill entry point.

Render a read-only view of one or more service repos' ralph-queue
state. Uses a temporary ``git worktree`` per repo so the user's
working tree is untouched.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Make ``scripts.pbi_reader`` importable when this script is invoked
# directly via ``uv run python skills/ralph-status/scripts/show.py``.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.pbi_reader import (  # noqa: E402
    STATE_FOLDERS,
    PBIRow,
    PBIRowError,
    enumerate_state,
)


class _FatalError(RuntimeError):
    """Raised internally to signal a clean exit with code 2."""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-status",
        description=(
            "Read-only view of Ralph's queue across one or more service "
            "repos. Creates a short-lived git worktree per repo to read "
            "the ralph-queue branch without disturbing the user's "
            "working tree."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--repo",
        help="Path to a service repo to inspect.",
    )
    group.add_argument(
        "--repos-file",
        help=(
            "Path to a config file listing service repos to inspect "
            "(one path per non-blank, non-comment line)."
        ),
    )
    parser.add_argument(
        "--state",
        choices=STATE_FOLDERS,
        help="Filter rows to a single state.",
    )
    parser.add_argument(
        "--branch",
        default=os.environ.get("RALPH_QUEUE_BRANCH", DEFAULT_QUEUE_BRANCH),
        help=f"Queue branch name (default: {DEFAULT_QUEUE_BRANCH}).",
    )
    parser.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Emit JSON to stdout instead of a fixed-width table.",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Keep the temporary worktree(s) on disk after the command exits.",
    )
    return parser.parse_args(argv)


_COLUMN_ORDER: tuple[str, ...] = (
    "REPO",
    "STATE",
    "ID",
    "TYPE",
    "SEVERITY",
    "AGE",
    "TITLE",
)


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


def _row_to_cells(row: PBIRow | PBIRowError) -> list[str]:
    if isinstance(row, PBIRow):
        return [
            row.repo_name,
            row.state,
            row.pbi_id,
            row.pbi_type,
            row.severity,
            _age_string(row.created_at),
            row.title,
        ]
    return [
        row.repo_name,
        row.state,
        row.pbi_dir.name,
        "?",
        "?",
        "?",
        f"(parse error) {row.message}",
    ]


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


def _row_to_json(row: PBIRow | PBIRowError, *, canonical_repo_path: Path) -> dict[str, object]:
    """Render a row as JSON. ``canonical_repo_path`` is the service repo
    path the operator passed via ``--repo``, NOT the temp worktree under
    which the rows were collected.

    The reader currently stores ``row.repo_path == worktree_dir`` because
    ``enumerate_state`` walks the worktree. That's the right value for
    ``row.relative_pbi_dir()`` (pbi_dir IS under the worktree), but it's
    the wrong value to expose to downstream JSON consumers — they expect
    the service repo path (the one in ``repos[*].path``). Pass the
    canonical path in from the snapshot to emit the right value."""
    if isinstance(row, PBIRow):
        return {
            "repo": str(canonical_repo_path),
            "repo_name": row.repo_name,
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
        "repo": str(canonical_repo_path),
        "repo_name": row.repo_name,
        "state": row.state,
        "id": row.pbi_dir.name,
        "type": "?",
        "severity": "?",
        "attempts": 0,
        "created_at": None,
        "updated_at": None,
        "title": f"(parse error) {row.message}",
        "pbi_dir": row.relative_pbi_dir(),
        "error": row.message,
    }


def _render_json(
    snapshots: list[RepoSnapshot],
    top_level_errors: list[str],
) -> str:
    rows: list[dict[str, object]] = []
    for snap in snapshots:
        for row in snap.rows:
            rows.append(_row_to_json(row, canonical_repo_path=snap.config.path))
    payload: dict[str, object] = {
        "rows": rows,
        "errors": top_level_errors,
        "repos": [
            {
                "path": str(snap.config.path),
                "name": snap.config.name,
                "branch": snap.config.branch,
                "worktree_path": str(snap.worktree_path),
            }
            for snap in snapshots
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    states: tuple[str, ...] = (args.state,) if args.state else STATE_FOLDERS

    try:
        configs = _load_repos_config(args)
    except _FatalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    worktree_root = Path(tempfile.mkdtemp(prefix="ralph-status-"))
    snapshots: list[RepoSnapshot] = []
    # Per-repo failures land here and propagate to the JSON `errors`
    # field, per the SKILL.md contract: "Repos that could not be
    # inspected at all appear in the top-level errors array and cause
    # exit code 2." Without this, a single bad repo aborts the whole
    # run and downstream JSON consumers never see per-repo detail.
    top_level_errors: list[str] = []
    try:
        for config in configs:
            # _extract_queue_snapshot may call _create_worktree (which
            # registers an entry in the service repo's .git/worktrees/)
            # and THEN fail in enumerate_state (e.g. PermissionError on
            # a state dir). Guard the call so a partial worktree gets
            # cleaned up AND a failing repo doesn't kill the whole run.
            try:
                snap = _extract_queue_snapshot(config, states=states, worktree_root=worktree_root)
            except Exception as exc:
                if not args.no_cleanup:
                    path_hash = f"{abs(hash(str(config.path.resolve()))) & 0xFFFFFFFF:08x}"
                    candidate = worktree_root / f"{config.name}__{config.branch}__{path_hash}"
                    if candidate.exists():
                        with contextlib.suppress(Exception):
                            _remove_worktree(config.path, candidate)
                top_level_errors.append(f"{config.path}: {type(exc).__name__}: {exc}")
                continue
            snapshots.append(snap)

        all_rows: list[PBIRow | PBIRowError] = []
        for snap in snapshots:
            all_rows.extend(snap.rows)

        if args.emit_json:
            sys.stdout.write(_render_json(snapshots, top_level_errors=top_level_errors))
        else:
            sys.stdout.write(_render_table(all_rows))
            repo_count = len(snapshots)
            pbi_count = len(all_rows)
            print(
                f"# {pbi_count} PBI(s) across {repo_count} repo(s) (states: {', '.join(states)})",
                file=sys.stderr,
            )
            for err in top_level_errors:
                print(f"# repo-level error: {err}", file=sys.stderr)
        # Exit 2 if any per-repo failure happened, 0 only if every
        # repo inspected cleanly. Matches the SKILL.md contract.
        return 2 if top_level_errors else 0
    except _FatalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        print(
            f"error: git command failed ({exc.returncode}): {' '.join(exc.cmd)}\n{stderr}",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        # Catch-all for failures OUTSIDE the per-repo loop (e.g. an
        # unexpected error during _render_json / _render_table). The
        # per-repo failures are already captured in top_level_errors
        # above; this is the safety net for everything else.
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if not args.no_cleanup:
            for snap in snapshots:
                _remove_worktree(snap.config.path, snap.worktree_path)
            shutil.rmtree(worktree_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
