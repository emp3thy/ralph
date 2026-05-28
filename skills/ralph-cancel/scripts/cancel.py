"""``ralph-cancel`` skill entry point.

Drops an empty ``CANCEL`` sentinel file into the PBI directory under
``.ralph/current/`` in the queue clone, commits, and pushes ``main`` to
``origin``. Ralph reads the sentinel on its next iteration and abandons
the PBI.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.queue_writer import (  # noqa: E402
    QueueWriterError,
    acquire_queue_clone,
    commit_paths,
    is_path_in_head,
    push,
    resolve_queue_repo,
    resolve_workspace_root,
)

CURRENT_FOLDER = "current"
CANCEL_FILE_NAME = "CANCEL"


@dataclass
class CancelResult:
    pbi_id: str
    sentinel_path: str
    queue_clone: str
    commit_sha: str
    pushed: bool
    dry_run: bool
    already_cancelled: bool


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-cancel",
        description=(
            "Cancel a PBI that Ralph is actively working on by dropping "
            "an empty CANCEL sentinel into .ralph/current/<pbi-id>/ in "
            "the queue clone (<workspace_root>/queue on main)."
        ),
    )
    parser.add_argument(
        "--pbi-id",
        required=True,
        help="PBI identifier matching the directory name under .ralph/current/.",
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
        help="Commit the sentinel locally but do not push.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and log without writing, committing, or pushing.",
    )
    return parser.parse_args(argv)


def _resolve_current_pbi(clone: Path, pbi_id: str) -> Path:
    current_dir = clone / ".ralph" / CURRENT_FOLDER / pbi_id
    if not current_dir.is_dir():
        base = clone / ".ralph"
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
        workspace_root = resolve_workspace_root(args.workspace)
        queue_repo = resolve_queue_repo(args.queue_repo)

        if args.dry_run:
            # Dry-run must NOT touch network or filesystem. Report the
            # would-be sentinel path against the would-be clone location
            # without cloning or checking PBI existence.
            clone = workspace_root / "queue"
            rel_sentinel = (
                Path(".ralph") / CURRENT_FOLDER / args.pbi_id / CANCEL_FILE_NAME
            ).as_posix()
            print(
                f"dry-run: would write {rel_sentinel} and commit "
                f"'chore(queue): cancel {args.pbi_id}'.",
                file=sys.stderr,
            )
            result = CancelResult(
                pbi_id=args.pbi_id,
                sentinel_path=rel_sentinel,
                queue_clone=str(clone),
                commit_sha="",
                pushed=False,
                dry_run=True,
                already_cancelled=False,
            )
            print(json.dumps(asdict(result), indent=2, sort_keys=True))
            return 0

        clone = acquire_queue_clone(workspace_root, queue_repo)

        pbi_dir = _resolve_current_pbi(clone, args.pbi_id)
        sentinel = pbi_dir / CANCEL_FILE_NAME
        rel_sentinel = sentinel.relative_to(clone).as_posix()

        # Idempotency check reads the COMMITTED HEAD tree, not the
        # filesystem. A previous invocation that staged the sentinel but
        # failed the commit step (pre-commit hook reject, missing user
        # config, etc.) leaves the file on disk while HEAD is unchanged
        # — that scenario must re-run, not silently return "already
        # cancelled" and leave ralph thinking the PBI is still active.
        if is_path_in_head(clone, rel_sentinel):
            print(
                f"CANCEL sentinel already present at {rel_sentinel}; nothing to do.",
                file=sys.stderr,
            )
            result = CancelResult(
                pbi_id=args.pbi_id,
                sentinel_path=rel_sentinel,
                queue_clone=str(clone),
                commit_sha="",
                pushed=False,
                dry_run=False,
                already_cancelled=True,
            )
            print(json.dumps(asdict(result), indent=2, sort_keys=True))
            return 0

        print(f"writing sentinel at {rel_sentinel}...", file=sys.stderr)
        sentinel.write_bytes(b"")

        commit_sha = commit_paths(
            clone,
            [sentinel],
            f"chore(queue): cancel {args.pbi_id}",
        )

        pushed = False
        if not args.no_push:
            print("pushing main to origin...", file=sys.stderr)
            push(clone, "main")
            pushed = True

        result = CancelResult(
            pbi_id=args.pbi_id,
            sentinel_path=rel_sentinel,
            queue_clone=str(clone),
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
