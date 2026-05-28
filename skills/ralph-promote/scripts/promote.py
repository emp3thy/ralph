"""``ralph-promote`` skill entry point.

Moves a PBI directory between state folders (e.g. ``inbox/`` →
``current/``) in the queue clone, updates the entry file's ``status``
and ``updated_at`` frontmatter, commits, and pushes ``main`` to
``origin``. This is the operator's manual override for the executor's
automatic state transitions.
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
    QUEUE_STATE_FOLDERS,
    QueueWriterError,
    acquire_queue_clone,
    append_history,
    commit_paths,
    is_path_in_head,
    push,
    read_frontmatter,
    resolve_queue_repo,
    resolve_workspace_root,
    write_frontmatter,
)

ENTRY_FILE_BY_TYPE = {
    "feature": "PBI.md",
    "bug": "BUG.md",
    "pr-feedback": "FEEDBACK.md",
}


@dataclass
class PromoteResult:
    pbi_id: str
    from_state: str
    to_state: str
    entry_file: str
    queue_clone: str
    commit_sha: str
    pushed: bool
    dry_run: bool
    already_promoted: bool


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-promote",
        description=(
            "Move a PBI between state folders in the queue clone "
            "(<workspace_root>/queue on main). Updates the PBI's "
            "status frontmatter, commits, and pushes."
        ),
    )
    parser.add_argument(
        "--pbi-id",
        required=True,
        help="PBI identifier matching the directory name under .ralph/<state>/.",
    )
    parser.add_argument(
        "--from",
        dest="from_state",
        required=True,
        choices=QUEUE_STATE_FOLDERS,
        help="Source state folder.",
    )
    parser.add_argument(
        "--to",
        dest="to_state",
        required=True,
        choices=QUEUE_STATE_FOLDERS,
        help="Destination state folder.",
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
        "--no-push",
        action="store_true",
        help="Commit the move locally but do not push.",
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
        if args.from_state == args.to_state:
            raise QueueWriterError(f"--from and --to must differ; both are {args.from_state!r}")

        workspace_root = resolve_workspace_root(args.workspace)
        queue_repo = resolve_queue_repo(args.queue_repo)

        if args.dry_run:
            # Dry-run must NOT touch network or filesystem. Report the
            # would-be move against the would-be clone location.
            clone = workspace_root / "queue"
            rel_from = f".ralph/{args.from_state}/{args.pbi_id}"
            rel_to = f".ralph/{args.to_state}/{args.pbi_id}"
            print(
                f"dry-run: would move {rel_from} -> {rel_to} and commit "
                f"'chore(queue): promote {args.pbi_id} "
                f"({args.from_state} -> {args.to_state})'.",
                file=sys.stderr,
            )
            result = PromoteResult(
                pbi_id=args.pbi_id,
                from_state=args.from_state,
                to_state=args.to_state,
                entry_file="",
                queue_clone=str(clone),
                commit_sha="",
                pushed=False,
                dry_run=True,
                already_promoted=False,
            )
            print(json.dumps(asdict(result), indent=2, sort_keys=True))
            return 0

        clone = acquire_queue_clone(workspace_root, queue_repo)

        from_dir = clone / ".ralph" / args.from_state / args.pbi_id
        to_dir = clone / ".ralph" / args.to_state / args.pbi_id

        # Idempotency check reads the COMMITTED HEAD tree, not disk. If
        # any of the standard entry files is already at the destination
        # in HEAD, the move already landed — re-running must not stack
        # a duplicate commit.
        for entry_name in ENTRY_FILE_BY_TYPE.values():
            rel_to_entry = f".ralph/{args.to_state}/{args.pbi_id}/{entry_name}"
            if is_path_in_head(clone, rel_to_entry):
                print(
                    f"PBI {args.pbi_id} already at .ralph/{args.to_state}/; nothing to do.",
                    file=sys.stderr,
                )
                result = PromoteResult(
                    pbi_id=args.pbi_id,
                    from_state=args.from_state,
                    to_state=args.to_state,
                    entry_file=rel_to_entry,
                    queue_clone=str(clone),
                    commit_sha="",
                    pushed=False,
                    dry_run=False,
                    already_promoted=True,
                )
                print(json.dumps(asdict(result), indent=2, sort_keys=True))
                return 0

        if not from_dir.is_dir():
            raise QueueWriterError(f"PBI {args.pbi_id!r} not found at .ralph/{args.from_state}/")

        if to_dir.exists():
            raise QueueWriterError(
                f".ralph/{args.to_state}/{args.pbi_id}/ already exists in the "
                f"queue clone working tree; refusing to overwrite"
            )

        # Resolve entry file BEFORE the move so we know which one to
        # rewrite post-rename.
        entry_before = _resolve_entry_file(from_dir)
        entry_name = entry_before.name

        # git mv stages renames for every file inside the PBI dir.
        to_dir.parent.mkdir(parents=True, exist_ok=True)
        rel_from = f".ralph/{args.from_state}/{args.pbi_id}"
        rel_to = f".ralph/{args.to_state}/{args.pbi_id}"
        _git(clone, "mv", rel_from, rel_to)

        entry_after = to_dir / entry_name
        frontmatter, body = read_frontmatter(entry_after)
        frontmatter["status"] = args.to_state
        frontmatter["updated_at"] = _now_iso()
        write_frontmatter(entry_after, frontmatter, body)

        append_history(
            to_dir,
            actor="ralph-promote",
            action="promote",
            detail=f"{args.from_state} -> {args.to_state}",
        )

        history_file = to_dir / "HISTORY.md"
        commit_sha = commit_paths(
            clone,
            [entry_after, history_file],
            f"chore(queue): promote {args.pbi_id} ({args.from_state} -> {args.to_state})",
        )

        pushed = False
        if not args.no_push:
            print("pushing main to origin...", file=sys.stderr)
            push(clone, "main")
            pushed = True

        result = PromoteResult(
            pbi_id=args.pbi_id,
            from_state=args.from_state,
            to_state=args.to_state,
            entry_file=entry_after.relative_to(clone).as_posix(),
            queue_clone=str(clone),
            commit_sha=commit_sha,
            pushed=pushed,
            dry_run=False,
            already_promoted=False,
        )
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    except QueueWriterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
