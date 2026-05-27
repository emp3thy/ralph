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
    is_path_in_head,
    push,
    read_frontmatter,
    read_path_from_head,
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


def _resolve_blocked_pbi(repo: Path, pbi_id: str, destination: str) -> Path:
    """Return the on-disk PBI path under ``.ralph/blocked/<pbi_id>``.

    Handles the partial-failure retry case: if the working tree shows
    the PBI at ``.ralph/<destination>/<pbi_id>`` but HEAD still has it
    in ``.ralph/blocked/``, return the blocked path anyway so the
    caller can finish the commit. The blocked path will not exist on
    disk in that case — ``main`` detects this and skips the move +
    history-append, going straight to ``commit_paths``.
    """
    blocked = repo / ".ralph" / "blocked" / pbi_id
    if blocked.is_dir():
        return blocked
    # Retry-after-failed-commit: working tree shows the move already
    # happened, HEAD does not. Return blocked even though it's missing.
    if is_path_in_head(repo, f".ralph/blocked/{pbi_id}"):
        destination_dir = repo / ".ralph" / destination / pbi_id
        if destination_dir.is_dir():
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

        old_path = _resolve_blocked_pbi(repo, args.pbi_id, args.destination)
        archive_existed_before = (repo / ".ralph" / "archive").is_dir()
        new_path = repo / ".ralph" / args.destination / args.pbi_id
        # Detect the partial-failure retry: HEAD still has the PBI in
        # blocked/, but the working tree already moved it to
        # ``new_path`` (frontmatter + HISTORY already updated). Skip
        # write_frontmatter / append_history / _move_directory and go
        # straight to commit so the audit isn't duplicated and the
        # commit lands.
        is_retry = (
            not old_path.is_dir()
            and new_path.is_dir()
            and is_path_in_head(repo, f".ralph/blocked/{args.pbi_id}")
        )

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

        action_name = "return-to-inbox" if args.destination == "inbox" else "archive"
        # Gate each side-effect on its own observable state. ``is_retry``
        # already short-circuits the post-move case (PBI on disk at
        # ``new_path`` while HEAD still has it in ``blocked/``). Inside
        # the not-yet-moved branch we still need per-step idempotency:
        # frontmatter write only when the destination status isn't yet
        # there, and HISTORY append always EXCEPT when this is the
        # partial-failure-retry-pre-move shape AND HISTORY already
        # carries the entry. Outside that retry window we always append,
        # even if HISTORY happens to contain identical ``--note`` text
        # from an older blocked→inbox→blocked cycle.
        if not is_retry:
            entry_file = _resolve_entry_file(old_path)
            frontmatter, body = read_frontmatter(entry_file)
            current_status = frontmatter.get("status")
            if current_status != args.destination:
                frontmatter["status"] = args.destination
                frontmatter["updated_at"] = _now_iso()
                if args.destination == "inbox":
                    frontmatter["attempts"] = 0
                write_frontmatter(entry_file, frontmatter, body)

            history_file = old_path / "HISTORY.md"
            # Per-invocation dedup that survives repeated-cycle history.
            # Compare working tree HISTORY against HEAD's HISTORY: if the
            # working tree already has MORE occurrences of ``args.note``
            # than HEAD, the previous (failed) attempt already appended
            # it — skip to avoid a duplicate. Otherwise always append.
            # Handles both partial-failure shapes AND fresh repeat-cycles
            # (blocked→inbox→blocked→inbox) where the same note text
            # appears in an older committed entry.
            rel_history = str(history_file.relative_to(repo)).replace("\\", "/")
            head_history = read_path_from_head(repo, rel_history) or ""
            working_history = (
                history_file.read_text(encoding="utf-8") if history_file.is_file() else ""
            )
            skip_append = working_history.count(args.note) > head_history.count(args.note)
            if not skip_append:
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
