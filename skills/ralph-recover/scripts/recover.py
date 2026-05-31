"""``ralph-recover`` skill entry point.

Force a PBI in ``.ralph/current/`` back to ``inbox/`` or ``blocked/``,
stripping its ``CLAIM.json`` and resetting frontmatter. The sole
manual override for the multi-ralph claim protocol: every other
operator skill refuses to act on a foreign claim and directs the
operator here.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ralph_executor.claim import CLAIM_FILENAME, ClaimParseError, read_claim  # noqa: E402
from scripts.queue_writer import (  # noqa: E402
    QueueWriterError,
    acquire_queue_clone,
    append_history,
    commit_paths,
    push,
    resolve_instance_id,
    resolve_queue_branch,
    resolve_queue_repo,
    resolve_workspace_root,
    update_frontmatter_fields,
)

DESTINATIONS = ("inbox", "blocked")
_ENTRY_FILENAMES = ("PBI.md", "BUG.md", "FEEDBACK.md")
_ACTOR = "ralph-recover"


@dataclass
class RecoverResult:
    pbi_id: str
    from_state: str
    to_state: str
    previous_owner: str | None
    queue_clone: str
    commit_sha: str
    pushed: bool


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-recover",
        description=(
            "Force a PBI out of .ralph/current/ to inbox/ or blocked/, "
            "stripping its CLAIM.json. Used when the claiming instance "
            "is unreachable and a human needs to redistribute the PBI."
        ),
    )
    parser.add_argument(
        "--pbi-id",
        required=True,
        help="PBI identifier matching the directory name under .ralph/current/.",
    )
    parser.add_argument(
        "--to",
        choices=DESTINATIONS,
        required=True,
        dest="destination",
        help="Destination state folder. 'inbox' resets attempts to 0.",
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
        help="Override queue_branch from ~/.ralph/config.toml (default: ralph-queue).",
    )
    parser.add_argument(
        "--instance-id",
        dest="instance_id",
        help="Override instance_id from ~/.ralph/config.toml.",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Commit the recovery locally but do not push.",
    )
    return parser.parse_args(argv)


def _find_entry_file(pbi_dir: Path) -> Path:
    for name in _ENTRY_FILENAMES:
        candidate = pbi_dir / name
        if candidate.is_file():
            return candidate
    raise QueueWriterError(
        f"PBI {pbi_dir.name}: no entry file (PBI.md, BUG.md, or FEEDBACK.md) found"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        workspace_root = resolve_workspace_root(args.workspace)
        queue_repo = resolve_queue_repo(args.queue_repo)
        queue_branch = resolve_queue_branch(args.queue_branch)
        instance_id = resolve_instance_id(args.instance_id)

        clone = acquire_queue_clone(
            workspace_root,
            queue_repo,
            queue_branch,
            instance_id=instance_id,
        )

        # Halt sentinel is a global stop. Recovery refuses just like every
        # other queue mutation. Operator must clear the halt first.
        halt = clone / ".ralph" / "state" / "halted"
        if halt.is_file():
            raise QueueWriterError(
                "halt sentinel is active at .ralph/state/halted; refusing to operate"
            )

        src_dir = clone / ".ralph" / "current" / args.pbi_id
        dst_dir = clone / ".ralph" / args.destination / args.pbi_id
        if not src_dir.is_dir():
            raise QueueWriterError(f"PBI {args.pbi_id!r} not found at .ralph/current/")
        if dst_dir.exists():
            raise QueueWriterError(
                f".ralph/{args.destination}/{args.pbi_id}/ already exists in the "
                f"queue clone working tree; refusing to overwrite"
            )

        previous_owner: str | None = None
        try:
            claim = read_claim(src_dir)
        except ClaimParseError as exc:
            raise QueueWriterError(f"malformed CLAIM.json at {src_dir.name}: {exc}") from exc
        if claim is not None:
            previous_owner = claim.instance_id

        claim_file = src_dir / CLAIM_FILENAME
        if claim_file.is_file():
            claim_file.unlink()

        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        # shutil.move handles cross-volume moves on Windows (Path.rename
        # raises OSError if workspace_root and the temp dir live on
        # different drives). Mirrors T8's legacy-rename rationale.
        shutil.move(str(src_dir), str(dst_dir))

        entry = _find_entry_file(dst_dir)
        if args.destination == "inbox":
            update_frontmatter_fields(entry, {"status": "inbox", "attempts": 0})
        else:
            update_frontmatter_fields(entry, {"status": "blocked"})

        append_history(
            dst_dir,
            actor=_ACTOR,
            action="recover",
            detail=(
                f"recovered {args.pbi_id} from instance "
                f"{previous_owner or '<no claim>'} to {args.destination}"
            ),
        )

        commit_sha = commit_paths(
            clone,
            [src_dir, dst_dir],
            f"chore(queue): recover {args.pbi_id} from {previous_owner or '<no claim>'}",
        )

        pushed = False
        if not args.no_push:
            print(f"pushing {queue_branch} to origin...", file=sys.stderr)
            push(clone, queue_branch)
            pushed = True

        result = RecoverResult(
            pbi_id=args.pbi_id,
            from_state="current",
            to_state=args.destination,
            previous_owner=previous_owner,
            queue_clone=str(clone),
            commit_sha=commit_sha,
            pushed=pushed,
        )
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    except QueueWriterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
