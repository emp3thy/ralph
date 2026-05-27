"""``ralph-triage`` skill entry point.

Walks the ``.ralph/blocked/`` queue. Routes a blocked PBI either back
to ``.ralph/inbox/`` (with attempts reset to 0) or out to
``.ralph/archive/`` (creating the folder on demand). The operator's
reasoning is captured in HISTORY.md so every triage decision leaves an
audit trail.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
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
    push,
    read_frontmatter,
    write_frontmatter,
)

DEFAULT_QUEUE_BRANCH = "ralph-queue"
ALLOWED_DESTINATIONS = ("inbox", "archive")
ENTRY_FILE_BY_TYPE = {
    "feature": "PBI.md",
    "bug": "BUG.md",
    "pr-feedback": "FEEDBACK.md",
}


@dataclass
class TriageResult:
    pbi_id: str
    destination: str
    previous_state_folder: str
    old_path: str
    new_path: str
    attempts_reset_to_zero: bool
    archive_created: bool
    repo_path: str
    branch: str
    commit_sha: str
    pushed: bool
    dry_run: bool


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-triage",
        description=(
            "Triage a PBI in .ralph/blocked/. Routes the PBI either back "
            "to inbox/ (for retry, attempts reset to 0) or to archive/ "
            "(closed out). A note explaining the decision is required."
        ),
    )
    parser.add_argument(
        "--pbi-id",
        required=True,
        help="PBI identifier matching the directory name under .ralph/blocked/.",
    )
    parser.add_argument(
        "--to",
        dest="destination",
        required=True,
        choices=ALLOWED_DESTINATIONS,
        help="Destination state folder. One of inbox, archive.",
    )
    parser.add_argument(
        "--note",
        required=True,
        help="Operator's reasoning, appended to HISTORY.md.",
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
        help="Compute and log without moving, committing, or pushing.",
    )
    return parser.parse_args(argv)


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _resolve_blocked_pbi(repo: Path, pbi_id: str) -> Path:
    blocked = repo / ".ralph" / "blocked" / pbi_id
    if blocked.is_dir():
        return blocked
    base = repo / ".ralph"
    for folder in ("current", "inbox", "pending-pr", "done", "archive"):
        if (base / folder / pbi_id).is_dir():
            raise QueueWriterError(
                f"PBI {pbi_id!r} is in .ralph/{folder}/, not in "
                f".ralph/blocked/. ralph-triage only operates on the "
                f"blocked queue."
            )
    raise QueueWriterError(
        f"PBI {pbi_id!r} not found under .ralph/blocked/ (or any other state folder)"
    )


def _resolve_entry_file(pbi_dir: Path) -> Path:
    for candidate in ENTRY_FILE_BY_TYPE.values():
        path = pbi_dir / candidate
        if path.is_file():
            return path
    raise QueueWriterError(f"no entry file (PBI.md, BUG.md, or FEEDBACK.md) found in {pbi_dir}")


def _move_directory(src: Path, dest: Path) -> None:
    """Move ``src`` to ``dest``. Creates ``dest.parent`` on demand.

    Uses ``shutil.move`` so it works whether ``src`` and ``dest`` are
    on the same filesystem or not.
    """
    if dest.exists():
        raise QueueWriterError(f"destination already exists: {dest}; refusing to overwrite")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        repo = Path(args.repo).resolve()
        ensure_git_repo(repo)

        print(f"switching to {args.branch}...", file=sys.stderr)
        checkout_queue_branch(repo, args.branch)

        old_path = _resolve_blocked_pbi(repo, args.pbi_id)
        archive_existed_before = (repo / ".ralph" / "archive").is_dir()
        new_path = repo / ".ralph" / args.destination / args.pbi_id

        if args.dry_run:
            print(
                f"dry-run: would move "
                f"{old_path.relative_to(repo).as_posix()} -> "
                f"{new_path.relative_to(repo).as_posix()}.",
                file=sys.stderr,
            )
            result = TriageResult(
                pbi_id=args.pbi_id,
                destination=args.destination,
                previous_state_folder="blocked",
                old_path=old_path.relative_to(repo).as_posix(),
                new_path=new_path.relative_to(repo).as_posix(),
                attempts_reset_to_zero=(args.destination == "inbox"),
                archive_created=(args.destination == "archive" and not archive_existed_before),
                repo_path=str(repo),
                branch=args.branch,
                commit_sha="",
                pushed=False,
                dry_run=True,
            )
            print(json.dumps(asdict(result), indent=2, sort_keys=True))
            return 0

        entry_file = _resolve_entry_file(old_path)
        frontmatter, body = read_frontmatter(entry_file)
        frontmatter["status"] = args.destination
        frontmatter["updated_at"] = _now_iso()
        if args.destination == "inbox":
            frontmatter["attempts"] = 0
        write_frontmatter(entry_file, frontmatter, body)

        action_name = "return-to-inbox" if args.destination == "inbox" else "archive"
        append_history(
            old_path,
            actor="ralph-triage",
            action=action_name,
            detail=args.note,
        )

        _move_directory(old_path, new_path)

        commit_message = f"chore(queue): triage {args.pbi_id} (blocked -> {args.destination})"
        commit_sha = commit_paths(
            repo,
            [old_path, new_path],
            commit_message,
        )

        pushed = False
        if not args.no_push:
            print(f"pushing {args.branch}...", file=sys.stderr)
            push(repo, args.branch)
            pushed = True

        archive_created = args.destination == "archive" and not archive_existed_before
        result = TriageResult(
            pbi_id=args.pbi_id,
            destination=args.destination,
            previous_state_folder="blocked",
            old_path=old_path.relative_to(repo).as_posix(),
            new_path=new_path.relative_to(repo).as_posix(),
            attempts_reset_to_zero=(args.destination == "inbox"),
            archive_created=archive_created,
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
