"""``ralph-status`` skill entry point.

Render a read-only view of one or more service repos' ralph-queue
state. Uses a temporary ``git worktree`` per repo so the user's
working tree is untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
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

DEFAULT_QUEUE_BRANCH = "ralph-queue"


@dataclass
class RepoConfig:
    path: Path
    name: str
    branch: str


@dataclass
class RepoSnapshot:
    config: RepoConfig
    worktree_path: Path
    rows: list[PBIRow | PBIRowError] = field(default_factory=list)


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


def _load_repos_config(args: argparse.Namespace) -> list[RepoConfig]:
    configs: list[RepoConfig] = []
    branch = args.branch

    if args.repo:
        path = Path(args.repo).resolve()
        configs.append(RepoConfig(path=path, name=path.name, branch=branch))
    else:
        cfg_path = Path(args.repos_file).resolve()
        if not cfg_path.is_file():
            raise _FatalError(f"--repos-file path does not exist: {cfg_path}")
        for raw_line in cfg_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            path = Path(line).expanduser().resolve()
            configs.append(RepoConfig(path=path, name=path.name, branch=branch))
        if not configs:
            raise _FatalError(f"--repos-file contained no usable entries: {cfg_path}")
    return configs


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists() or (path / "HEAD").is_file()


def _ensure_branch_exists(repo: Path, branch: str) -> None:
    """Verify the named branch can be resolved locally or via origin/."""
    local = _run_git(repo, "branch", "--list", branch)
    if local.strip():
        return
    remote = _run_git(repo, "branch", "-r", "--list", f"origin/{branch}")
    if remote.strip():
        return
    raise _FatalError(
        f"branch {branch!r} not found locally or as origin/{branch} "
        f"in {repo} (did Plan 2's setup runbook run against this repo?)"
    )


def _resolve_branch_ref(repo: Path, branch: str) -> str:
    """Return a ref name we can hand to ``git worktree add``."""
    local = _run_git(repo, "branch", "--list", branch)
    if local.strip():
        return branch
    return f"origin/{branch}"


def _create_worktree(repo: Path, branch: str, dest: Path) -> Path:
    """Create a detached worktree at ``dest`` pointing at ``branch``."""
    ref = _resolve_branch_ref(repo, branch)
    _run_git(repo, "worktree", "add", "--detach", str(dest), ref)
    return dest


def _remove_worktree(repo: Path, worktree_dir: Path) -> None:
    try:
        _run_git(repo, "worktree", "remove", "--force", str(worktree_dir))
    except subprocess.CalledProcessError:
        shutil.rmtree(worktree_dir, ignore_errors=True)
        with suppress(subprocess.CalledProcessError):
            _run_git(repo, "worktree", "prune")


def _extract_queue_snapshot(
    config: RepoConfig,
    *,
    states: Iterable[str],
    worktree_root: Path,
) -> RepoSnapshot:
    if not config.path.exists():
        raise _FatalError(f"--repo path does not exist: {config.path}")
    if not _is_git_repo(config.path):
        raise _FatalError(f"{config.path} is not a git repository (no .git/ directory)")
    _ensure_branch_exists(config.path, config.branch)

    # Disambiguate repos that share a basename. Two unrelated checkouts
    # like /team-a/service and /team-b/service both have name=="service";
    # without the path-hash suffix they would collide on the same
    # worktree_dir, and the second iteration's `git worktree remove`
    # would fail (the dir is registered against the FIRST repo's git
    # database, not the second), leaving a stale entry that subsequent
    # `git worktree prune` runs would never see.
    path_hash = f"{abs(hash(str(config.path.resolve()))) & 0xFFFFFFFF:08x}"
    worktree_dir = worktree_root / f"{config.name}__{config.branch}__{path_hash}"
    if worktree_dir.exists():
        _remove_worktree(config.path, worktree_dir)

    _create_worktree(config.path, config.branch, worktree_dir)
    snapshot = RepoSnapshot(config=config, worktree_path=worktree_dir)
    for state in states:
        snapshot.rows.extend(enumerate_state(worktree_dir, state, repo_name=config.name))
    return snapshot


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


def _row_to_json(row: PBIRow | PBIRowError) -> dict[str, object]:
    if isinstance(row, PBIRow):
        return {
            "repo": str(row.repo_path),
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
        "repo": str(row.repo_path),
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
            rows.append(_row_to_json(row))
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
    try:
        for config in configs:
            snap = _extract_queue_snapshot(config, states=states, worktree_root=worktree_root)
            snapshots.append(snap)

        all_rows: list[PBIRow | PBIRowError] = []
        for snap in snapshots:
            all_rows.extend(snap.rows)

        if args.emit_json:
            sys.stdout.write(_render_json(snapshots, top_level_errors=[]))
        else:
            sys.stdout.write(_render_table(all_rows))
            repo_count = len(snapshots)
            pbi_count = len(all_rows)
            print(
                f"# {pbi_count} PBI(s) across {repo_count} repo(s) (states: {', '.join(states)})",
                file=sys.stderr,
            )
        return 0
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
    finally:
        if not args.no_cleanup:
            for snap in snapshots:
                _remove_worktree(snap.config.path, snap.worktree_path)
            shutil.rmtree(worktree_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
