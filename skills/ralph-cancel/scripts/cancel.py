"""``ralph-cancel`` skill entry point.

Drops an empty ``CANCEL`` sentinel file into the PBI directory under
``.ralph/current/`` and pushes the result to ``ralph-queue``. Ralph
reads the sentinel on its next iteration and abandons the PBI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.queue_writer import (  # noqa: E402
    QueueWriterError,
    checkout_queue_branch,
    commit_paths,
    ensure_git_repo,
    push,
)

DEFAULT_QUEUE_BRANCH = "ralph-queue"
CURRENT_FOLDER = "current"
CANCEL_FILE_NAME = "CANCEL"


@dataclass
class CancelResult:
    pbi_id: str
    sentinel_path: str
    repo_path: str
    branch: str
    commit_sha: str
    pushed: bool
    dry_run: bool
    already_cancelled: bool


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-cancel",
        description=(
            "Cancel a PBI that Ralph is actively working on by dropping "
            "an empty CANCEL sentinel into .ralph/current/<pbi-id>/."
        ),
    )
    parser.add_argument(
        "--pbi-id",
        required=True,
        help="PBI identifier matching the directory name under .ralph/current/.",
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
        help="Commit the sentinel locally but do not push.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and log without writing, committing, or pushing.",
    )
    return parser.parse_args(argv)


def _resolve_current_pbi(repo: Path, pbi_id: str) -> Path:
    current_dir = repo / ".ralph" / CURRENT_FOLDER / pbi_id
    if not current_dir.is_dir():
        base = repo / ".ralph"
        for folder in ("inbox", "pending-pr", "blocked", "done", "archive"):
            if (base / folder / pbi_id).is_dir():
                raise QueueWriterError(
                    f"PBI {pbi_id!r} is in .ralph/{folder}/, not in "
                    f".ralph/{CURRENT_FOLDER}/. ralph-cancel only operates "
                    f"on the active PBI."
                )
        raise QueueWriterError(f"PBI {pbi_id!r} not found under any .ralph/ state folder")
    return current_dir


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        repo = Path(args.repo).resolve()
        ensure_git_repo(repo)

        print(f"switching to {args.branch}...", file=sys.stderr)
        checkout_queue_branch(repo, args.branch)

        pbi_dir = _resolve_current_pbi(repo, args.pbi_id)
        sentinel = pbi_dir / CANCEL_FILE_NAME
        rel_sentinel = (
            sentinel.relative_to(repo).as_posix() if sentinel.is_absolute() else str(sentinel)
        )

        if sentinel.exists():
            print(
                f"CANCEL sentinel already present at {rel_sentinel}; nothing to do.",
                file=sys.stderr,
            )
            result = CancelResult(
                pbi_id=args.pbi_id,
                sentinel_path=rel_sentinel,
                repo_path=str(repo),
                branch=args.branch,
                commit_sha="",
                pushed=False,
                dry_run=args.dry_run,
                already_cancelled=True,
            )
            print(json.dumps(asdict(result), indent=2, sort_keys=True))
            return 0

        if args.dry_run:
            print(
                f"dry-run: would write {rel_sentinel} and commit "
                f"'chore(queue): cancel {args.pbi_id}'.",
                file=sys.stderr,
            )
            result = CancelResult(
                pbi_id=args.pbi_id,
                sentinel_path=rel_sentinel,
                repo_path=str(repo),
                branch=args.branch,
                commit_sha="",
                pushed=False,
                dry_run=True,
                already_cancelled=False,
            )
            print(json.dumps(asdict(result), indent=2, sort_keys=True))
            return 0

        print(f"writing sentinel at {rel_sentinel}...", file=sys.stderr)
        sentinel.write_bytes(b"")

        commit_sha = commit_paths(
            repo,
            [sentinel],
            f"chore(queue): cancel {args.pbi_id}",
        )

        pushed = False
        if not args.no_push:
            print(f"pushing {args.branch}...", file=sys.stderr)
            push(repo, args.branch)
            pushed = True

        result = CancelResult(
            pbi_id=args.pbi_id,
            sentinel_path=rel_sentinel,
            repo_path=str(repo),
            branch=args.branch,
            commit_sha=commit_sha,
            pushed=pushed,
            dry_run=False,
            already_cancelled=False,
        )
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    except QueueWriterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
