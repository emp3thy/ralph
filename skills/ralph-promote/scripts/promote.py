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
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ralph_executor.queue.claim import (  # noqa: E402
    CLAIM_FILENAME,
    ClaimError,
    read_claim,
)
from scripts.queue_writer import (  # noqa: E402
    ENTRY_FILE_BY_TYPE,
    QUEUE_STATE_FOLDERS,
    QueueWriterError,
    acquire_queue_clone,
    append_history,
    commit_paths,
    is_path_in_head,
    now_iso,
    push,
    read_frontmatter,
    resolve_entry_file,
    resolve_instance_id,
    resolve_queue_branch,
    resolve_queue_repo,
    resolve_workspace_root,
    run_git,
    write_frontmatter,
)

CURRENT_FOLDER = "current"

# Exit code reserved for the multi-ralph CLAIM-ownership guard. Distinct
# from QueueWriterError's exit 2 so wrappers can branch on "this is a
# foreign-claim refusal, route to ralph-recover" without parsing stderr.
EXIT_CLAIM_GUARD = 3


class _ClaimGuardError(RuntimeError):
    """Raised by the CLAIM.json guard to short-circuit main() with exit 3."""


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
        "--queue-branch",
        metavar="BRANCH",
        help="Override queue_branch from ~/.ralph/config.toml (default: ralph-queue).",
    )
    parser.add_argument(
        "--instance-id",
        dest="instance_id",
        metavar="NAME",
        help=(
            "Operator instance_id used to compare against CLAIM.json when "
            "moving a PBI out of current/. Resolution order: --instance-id "
            "flag, RALPH_INSTANCE_ID env, instance_id in ~/.ralph/config.toml, "
            "sanitised hostname."
        ),
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


def _enforce_claim_ownership(pbi_dir: Path, operator_instance_id: str) -> None:
    """Refuse to promote out of ``current/`` when CLAIM.json is missing or foreign.

    Mirrors ralph-cancel's Task 13 guard. ``ralph-promote`` only enforces
    ownership when the source state is ``current/`` because every other
    queue folder is CLAIM-less by design. A move out of current/ steals
    the claim from whichever instance owns it, so a non-own claim is
    rejected with ``EXIT_CLAIM_GUARD`` and the operator is steered to
    ``ralph-recover``.
    """
    claim_path = pbi_dir / CLAIM_FILENAME
    if not claim_path.is_file():
        raise _ClaimGuardError("ralph-promote: PBI in current/ but no CLAIM.json")
    try:
        claim = read_claim(claim_path)
    except ClaimError as exc:
        raise _ClaimGuardError(f"ralph-promote: malformed CLAIM.json: {exc}") from exc
    if claim.instance_id != operator_instance_id:
        raise _ClaimGuardError(
            f"ralph-promote: cannot promote PBI claimed by {claim.instance_id!r}; use ralph-recover"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        if args.from_state == args.to_state:
            raise QueueWriterError(f"--from and --to must differ; both are {args.from_state!r}")

        workspace_root = resolve_workspace_root(args.workspace)
        queue_repo = resolve_queue_repo(args.queue_repo)
        queue_branch = resolve_queue_branch(args.queue_branch)

        # Resolve instance_id BEFORE the clone so we land on the same
        # namespaced path the executor uses (queue-<instance_id>/). The
        # halt-sentinel file is gitignored, so it is only visible to skills
        # that clone the executor's path, not the legacy queue/ path.
        # Resolution also happens before the dry-run branch so dry-run
        # output reports the real namespaced path; resolve_instance_id is
        # read-only (flag/env/TOML/hostname), so dry-run stays
        # side-effect free.
        operator_instance_id = resolve_instance_id(args.instance_id)

        if args.dry_run:
            # Dry-run must NOT touch network or filesystem. Report the
            # would-be move against the would-be clone location.
            clone = workspace_root / f"queue-{operator_instance_id}"
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

        clone = acquire_queue_clone(
            workspace_root,
            queue_repo,
            queue_branch,
            instance_id=operator_instance_id,
        )

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

        # Multi-ralph CLAIM-ownership guard: moves out of current/ may only
        # be done by the instance that owns the claim. Other source folders
        # carry no CLAIM.json by design, so the guard is current-only.
        if args.from_state == CURRENT_FOLDER:
            _enforce_claim_ownership(from_dir, operator_instance_id)

        # Resolve entry file BEFORE the move so we know which one to
        # rewrite post-rename.
        entry_before = resolve_entry_file(from_dir)
        entry_name = entry_before.name

        # git mv stages renames for every file inside the PBI dir.
        to_dir.parent.mkdir(parents=True, exist_ok=True)
        rel_from = f".ralph/{args.from_state}/{args.pbi_id}"
        rel_to = f".ralph/{args.to_state}/{args.pbi_id}"
        run_git(clone, "mv", rel_from, rel_to)

        entry_after = to_dir / entry_name
        frontmatter, body = read_frontmatter(entry_after)
        frontmatter["status"] = args.to_state
        frontmatter["updated_at"] = now_iso()
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
            print(f"pushing {queue_branch} to origin...", file=sys.stderr)
            push(clone, queue_branch)
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

    except _ClaimGuardError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CLAIM_GUARD
    except QueueWriterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
