"""``ralph-promote`` skill entry point.

Bumps the ``severity`` frontmatter field of an existing PBI and pushes
the result to ``ralph-queue``. The PBI may live in any ``.ralph/``
state folder; this skill is purely a metadata update.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.queue_writer import (  # noqa: E402
    QueueWriterError,
    append_history,
    checkout_queue_branch,
    commit_paths,
    ensure_git_repo,
    find_pbi_directory,
    push,
    read_frontmatter,
    write_frontmatter,
)

DEFAULT_QUEUE_BRANCH = "ralph-queue"
ALLOWED_SEVERITIES = ("critical", "high", "normal", "low")
ENTRY_FILE_BY_TYPE = {
    "feature": "PBI.md",
    "bug": "BUG.md",
    "pr-feedback": "FEEDBACK.md",
}


@dataclass
class PromoteResult:
    pbi_id: str
    previous_severity: str
    new_severity: str
    state_folder: str
    entry_file: str
    repo_path: str
    branch: str
    commit_sha: str
    pushed: bool
    dry_run: bool


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-promote",
        description=(
            "Bump a PBI's severity. Locates the PBI under any .ralph/ "
            "state folder, updates the severity frontmatter field, and "
            "pushes the change to ralph-queue."
        ),
    )
    parser.add_argument(
        "--pbi-id",
        required=True,
        help="PBI identifier matching the directory name under .ralph/.",
    )
    parser.add_argument(
        "--severity",
        required=True,
        choices=ALLOWED_SEVERITIES,
        help="New severity. Must be one of critical, high, normal, low.",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Absolute path to the target service repo checkout.",
    )
    parser.add_argument(
        "--branch",
        default=os.environ.get("RALPH_QUEUE_BRANCH", DEFAULT_QUEUE_BRANCH),
        help=f"Queue branch name (default: {DEFAULT_QUEUE_BRANCH}).",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Commit locally but do not push.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and log without writing, committing, or pushing.",
    )
    return parser.parse_args(argv)


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _resolve_entry_file(pbi_dir: Path) -> Path:
    for candidate in ENTRY_FILE_BY_TYPE.values():
        path = pbi_dir / candidate
        if path.is_file():
            return path
    raise QueueWriterError(f"no entry file (PBI.md, BUG.md, or FEEDBACK.md) found in {pbi_dir}")


def _state_folder_for(pbi_dir: Path, repo: Path) -> str:
    try:
        rel = pbi_dir.relative_to(repo / ".ralph")
    except ValueError as exc:
        raise QueueWriterError(f"PBI directory {pbi_dir} is not inside {repo / '.ralph'}") from exc
    return rel.parts[0]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        repo = Path(args.repo).resolve()
        ensure_git_repo(repo)

        print(f"switching to {args.branch}...", file=sys.stderr)
        checkout_queue_branch(repo, args.branch)

        pbi_dir = find_pbi_directory(repo, args.pbi_id)
        if pbi_dir is None:
            raise QueueWriterError(f"PBI {args.pbi_id!r} not found in any .ralph/ state folder")
        state_folder = _state_folder_for(pbi_dir, repo)
        entry_file = _resolve_entry_file(pbi_dir)

        frontmatter, body = read_frontmatter(entry_file)
        previous_severity = str(frontmatter.get("severity", ""))

        if previous_severity == args.severity:
            print(
                f"PBI {args.pbi_id} already has severity={args.severity!r}; nothing to do.",
                file=sys.stderr,
            )
            result = PromoteResult(
                pbi_id=args.pbi_id,
                previous_severity=previous_severity,
                new_severity=args.severity,
                state_folder=state_folder,
                entry_file=str(entry_file.relative_to(repo)).replace("\\", "/"),
                repo_path=str(repo),
                branch=args.branch,
                commit_sha="",
                pushed=False,
                dry_run=args.dry_run,
            )
            print(json.dumps(asdict(result), indent=2, sort_keys=True))
            return 0

        if args.dry_run:
            print(
                f"dry-run: would update severity from "
                f"{previous_severity!r} to {args.severity!r} on "
                f"{entry_file.relative_to(repo).as_posix()}.",
                file=sys.stderr,
            )
            result = PromoteResult(
                pbi_id=args.pbi_id,
                previous_severity=previous_severity,
                new_severity=args.severity,
                state_folder=state_folder,
                entry_file=str(entry_file.relative_to(repo)).replace("\\", "/"),
                repo_path=str(repo),
                branch=args.branch,
                commit_sha="",
                pushed=False,
                dry_run=True,
            )
            print(json.dumps(asdict(result), indent=2, sort_keys=True))
            return 0

        frontmatter["severity"] = args.severity
        frontmatter["updated_at"] = _now_iso()
        write_frontmatter(entry_file, frontmatter, body)

        append_history(
            pbi_dir,
            actor="ralph-promote",
            action="promote",
            detail=f"severity: {previous_severity} -> {args.severity}",
        )

        history_file = pbi_dir / "HISTORY.md"
        commit_sha = commit_paths(
            repo,
            [entry_file, history_file],
            f"chore(queue): promote {args.pbi_id} ({previous_severity} -> {args.severity})",
        )

        pushed = False
        if not args.no_push:
            print(f"pushing {args.branch}...", file=sys.stderr)
            push(repo, args.branch)
            pushed = True

        result = PromoteResult(
            pbi_id=args.pbi_id,
            previous_severity=previous_severity,
            new_severity=args.severity,
            state_folder=state_folder,
            entry_file=str(entry_file.relative_to(repo)).replace("\\", "/"),
            repo_path=str(repo),
            branch=args.branch,
            commit_sha=commit_sha,
            pushed=pushed,
            dry_run=False,
        )
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    except QueueWriterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
