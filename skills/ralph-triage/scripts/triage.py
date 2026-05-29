"""``ralph-triage`` skill entry point.

Walks ``.ralph/blocked/`` in the queue clone. Routes a blocked PBI
either back to ``.ralph/inbox/`` (with ``attempts`` reset to 0) or out
to ``.ralph/archive/`` (creating the archive folder on demand). The
operator's reasoning is captured in HISTORY.md so every triage decision
leaves an audit trail.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.queue_writer import (  # noqa: E402
    QueueWriterError,
    acquire_queue_clone,
    append_history,
    commit_paths,
    is_path_in_head,
    push,
    read_frontmatter,
    resolve_queue_branch,
    resolve_queue_repo,
    resolve_workspace_root,
    write_frontmatter,
)

ALLOWED_DESTINATIONS = ("inbox", "archive")
SOURCE_FOLDER = "blocked"
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
    queue_clone: str
    commit_sha: str
    pushed: bool
    dry_run: bool
    already_triaged: bool


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-triage",
        description=(
            "Triage a PBI in .ralph/blocked/ inside the queue clone "
            "(<workspace_root>/queue on main). Routes the PBI either "
            "back to inbox/ (for retry, attempts reset to 0) or to "
            "archive/ (closed out). A note explaining the decision is "
            "required and is appended to HISTORY.md."
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
        "--workspace",
        type=Path,
        help="Override workspace_root from ~/.ralph/config.toml.",
    )
    parser.add_argument(
        "--queue-repo",
        help="Override queue_repo from ~/.ralph/config.toml.",
    )
    parser.add_argument(
        "--queue-branch",
        metavar="BRANCH",
        help="Override the queue_branch from ~/.ralph/config.toml for this run (default: ralph-queue).",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Commit the move locally but do not push.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and log without moving, committing, or pushing.",
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


def _git(repo: Path, *args: str) -> None:
    try:
        subprocess.run(
            ["git", *args],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise QueueWriterError(f"git {' '.join(args)} failed ({exc.returncode}): {stderr}") from exc


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        workspace_root = resolve_workspace_root(args.workspace)
        queue_repo = resolve_queue_repo(args.queue_repo)
        queue_branch = resolve_queue_branch(args.queue_branch)

        action_name = "return-to-inbox" if args.destination == "inbox" else "archive"
        rel_from = f".ralph/{SOURCE_FOLDER}/{args.pbi_id}"
        rel_to = f".ralph/{args.destination}/{args.pbi_id}"

        if args.dry_run:
            # Dry-run must NOT touch network or filesystem. Report the
            # would-be move against the would-be clone location.
            clone = workspace_root / "queue"
            print(
                f"dry-run: would move {rel_from} -> {rel_to} and commit "
                f"'chore(queue): triage {args.pbi_id} "
                f"({SOURCE_FOLDER} -> {args.destination})'.",
                file=sys.stderr,
            )
            # archive_created cannot be known in dry-run: the non-dry-run path
            # computes it from `is_path_in_head(clone, ".ralph/archive")`,
            # which requires a clone. Report the conservative value (False)
            # rather than the unconditional `destination == "archive"` that
            # would lie when the archive folder already exists in HEAD.
            result = TriageResult(
                pbi_id=args.pbi_id,
                destination=args.destination,
                previous_state_folder=SOURCE_FOLDER,
                old_path=rel_from,
                new_path=rel_to,
                attempts_reset_to_zero=(args.destination == "inbox"),
                archive_created=False,
                queue_clone=str(clone),
                commit_sha="",
                pushed=False,
                dry_run=True,
                already_triaged=False,
            )
            print(json.dumps(asdict(result), indent=2, sort_keys=True))
            return 0

        clone = acquire_queue_clone(workspace_root, queue_repo, queue_branch)

        from_dir = clone / ".ralph" / SOURCE_FOLDER / args.pbi_id
        to_dir = clone / ".ralph" / args.destination / args.pbi_id

        # Idempotency check reads the COMMITTED HEAD tree, not disk. If
        # any of the standard entry files is already at the destination
        # in HEAD, the move already landed — re-running must not stack
        # a duplicate commit.
        for entry_name in ENTRY_FILE_BY_TYPE.values():
            rel_to_entry = f"{rel_to}/{entry_name}"
            if is_path_in_head(clone, rel_to_entry):
                print(
                    f"PBI {args.pbi_id} already at .ralph/{args.destination}/; nothing to do.",
                    file=sys.stderr,
                )
                result = TriageResult(
                    pbi_id=args.pbi_id,
                    destination=args.destination,
                    previous_state_folder=SOURCE_FOLDER,
                    old_path=rel_from,
                    new_path=rel_to,
                    attempts_reset_to_zero=False,
                    archive_created=False,
                    queue_clone=str(clone),
                    commit_sha="",
                    pushed=False,
                    dry_run=False,
                    already_triaged=True,
                )
                print(json.dumps(asdict(result), indent=2, sort_keys=True))
                return 0

        if not from_dir.is_dir():
            # Not in blocked/ — give the operator a precise reason: PBI
            # is in another state folder vs. PBI does not exist at all.
            base = clone / ".ralph"
            for folder in ("current", "inbox", "pending-pr", "done", "archive"):
                if (base / folder / args.pbi_id).is_dir():
                    raise QueueWriterError(
                        f"PBI {args.pbi_id!r} is in .ralph/{folder}/, not in "
                        f".ralph/{SOURCE_FOLDER}/. ralph-triage only operates on the "
                        f"blocked queue."
                    )
            raise QueueWriterError(f"PBI {args.pbi_id!r} not found under .ralph/{SOURCE_FOLDER}/")

        if to_dir.exists():
            raise QueueWriterError(
                f".ralph/{args.destination}/{args.pbi_id}/ already exists in the "
                f"queue clone working tree; refusing to overwrite"
            )

        # Whether THIS commit produces the ``.ralph/archive/`` directory.
        # Use HEAD (not disk) so the partial-failure retry case — a prior
        # incomplete run created the dir on disk but never committed it —
        # still reports correctly when the retry lands.
        archive_existed_before = is_path_in_head(clone, ".ralph/archive")

        # Resolve entry file BEFORE the move so we know which one to
        # rewrite post-rename.
        entry_before = _resolve_entry_file(from_dir)
        entry_name = entry_before.name

        to_dir.parent.mkdir(parents=True, exist_ok=True)
        _git(clone, "mv", rel_from, rel_to)

        entry_after = to_dir / entry_name
        frontmatter, body = read_frontmatter(entry_after)
        frontmatter["status"] = args.destination
        frontmatter["updated_at"] = _now_iso()
        if args.destination == "inbox":
            frontmatter["attempts"] = 0
        write_frontmatter(entry_after, frontmatter, body)

        append_history(
            to_dir,
            actor="ralph-triage",
            action=action_name,
            detail=args.note,
        )

        history_file = to_dir / "HISTORY.md"
        commit_sha = commit_paths(
            clone,
            [entry_after, history_file],
            f"chore(queue): triage {args.pbi_id} ({SOURCE_FOLDER} -> {args.destination})",
        )

        pushed = False
        if not args.no_push:
            print(f"pushing {queue_branch} to origin...", file=sys.stderr)
            push(clone, queue_branch)
            pushed = True

        archive_created = args.destination == "archive" and not archive_existed_before
        result = TriageResult(
            pbi_id=args.pbi_id,
            destination=args.destination,
            previous_state_folder=SOURCE_FOLDER,
            old_path=rel_from,
            new_path=rel_to,
            attempts_reset_to_zero=(args.destination == "inbox"),
            archive_created=archive_created,
            queue_clone=str(clone),
            commit_sha=commit_sha,
            pushed=pushed,
            dry_run=False,
            already_triaged=False,
        )
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    except QueueWriterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
