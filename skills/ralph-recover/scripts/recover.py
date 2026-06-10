"""``ralph-recover`` skill entry point.

Manually recover a stuck claim by moving a PBI directory out of
``.ralph/current/`` back to either ``.ralph/inbox/`` (re-dispatchable;
attempts counter reset to 0) or ``.ralph/blocked/`` (human triage;
attempts preserved). Deletes the orphan ``CLAIM.json``, appends a
``HISTORY.md`` audit entry naming the previous owner, and pushes
``ralph-queue`` to ``origin`` as ONE commit pinned to the subject
``chore(queue): recover <pbi-id> from <previous-instance-id>``.

The skill refuses to run when the halt sentinel at
``.ralph/state/halted`` is present and unacknowledged — a queue
mutation during a halt could mask the unresolved root cause. There is
no ``--force`` flag; operator invocation IS the deliberate action.
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
from ralph_executor.safety.halt import HaltStatus, check_halt_sentinel  # noqa: E402
from scripts.queue_writer import (  # noqa: E402
    QueueWriterError,
    acquire_queue_clone,
    append_history,
    commit_paths,
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
RECOVER_DESTINATIONS = ("inbox", "blocked")

# Exit code reserved for the halt-sentinel refusal. Distinct from
# QueueWriterError's exit 2 so wrappers can branch on "halt active,
# resolve the halt before retrying" without parsing stderr.
EXIT_HALT_GUARD = 4

_RECOVERED_FROM_NO_CLAIM = "<no-claim>"


class _HaltGuardError(RuntimeError):
    """Raised when the halt sentinel forbids recovery operations."""


@dataclass
class RecoverResult:
    pbi_id: str
    from_state: str
    to_state: str
    entry_file: str
    queue_clone: str
    commit_sha: str
    pushed: bool
    recovered_from_instance: str
    attempts_reset: bool
    # Reserved for forward-compat per multi-ralph plan: a future
    # ``--dry-run`` flag flips this to True on early-exit paths so
    # consumers can distinguish "stopped early" from "happy-path no-op".
    # Always False in Scope 1 because the skill has no ``--dry-run``.
    dry_run_skipped: bool


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-recover",
        description=(
            "Recover a stuck claim by moving a PBI out of "
            ".ralph/current/ back to .ralph/inbox/ (attempts reset) or "
            ".ralph/blocked/ (attempts preserved). Deletes CLAIM.json, "
            "appends a HISTORY entry, commits, and pushes."
        ),
    )
    parser.add_argument(
        "--pbi-id",
        required=True,
        help="PBI identifier matching the directory name under .ralph/current/.",
    )
    parser.add_argument(
        "--to",
        dest="to_state",
        required=True,
        choices=RECOVER_DESTINATIONS,
        help=(
            "Destination state folder. 'inbox' resets the attempts "
            "counter so the queue can re-dispatch the PBI cleanly; "
            "'blocked' preserves attempts for human triage."
        ),
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
        default=None,
        help=(
            "Operator instance_id used to land on the same namespaced queue "
            "clone path (queue-<instance-id>/) the executor uses. The "
            "halt-sentinel file is gitignored and only visible to skills "
            "that clone the executor's path. Resolution order: "
            "--instance-id flag, RALPH_INSTANCE_ID env, instance_id in "
            "~/.ralph/config.toml, sanitised hostname."
        ),
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Commit the recover locally but do not push.",
    )
    return parser.parse_args(argv)


def _read_claim_audit(claim_path: Path) -> tuple[str, str]:
    """Return ``(instance_id, raw_text)`` for a CLAIM.json on disk.

    Validates via ``read_claim`` so a malformed file surfaces as a
    typed ``QueueWriterError`` (exit 2) rather than leaking a raw
    ``ClaimError`` from the queue layer.
    """
    try:
        claim = read_claim(claim_path)
    except ClaimError as exc:
        raise QueueWriterError(f"malformed CLAIM.json: {exc}") from exc
    try:
        raw = claim_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise QueueWriterError(f"could not read CLAIM.json at {claim_path}: {exc}") from exc
    return claim.instance_id, raw


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        workspace_root = resolve_workspace_root(args.workspace)
        queue_repo = resolve_queue_repo(args.queue_repo)
        queue_branch = resolve_queue_branch(args.queue_branch)

        # Resolve instance_id BEFORE the clone so we land on the same
        # namespaced path the executor uses (queue-<instance-id>/). The
        # halt-sentinel file is gitignored, so it is only visible to
        # skills that clone the executor's path, not the legacy queue/
        # path. BugBot finding 2026-06-01 (PR #69 recover.py:187).
        operator_instance_id = resolve_instance_id(args.instance_id)

        clone = acquire_queue_clone(
            workspace_root,
            queue_repo,
            queue_branch,
            instance_id=operator_instance_id,
        )

        # Halt sentinel guard. ralph-recover is queue-state-mutating;
        # running it during an active halt could mask the unresolved
        # root cause. Operator must acknowledge (or delete) the
        # sentinel before recovering claims.
        if check_halt_sentinel(clone) == HaltStatus.HALTED:
            raise _HaltGuardError("ralph-recover: halt sentinel active")

        from_dir = clone / ".ralph" / CURRENT_FOLDER / args.pbi_id
        to_dir = clone / ".ralph" / args.to_state / args.pbi_id

        if not from_dir.is_dir():
            raise QueueWriterError(f"PBI {args.pbi_id!r} not found at .ralph/{CURRENT_FOLDER}/")
        if to_dir.exists():
            raise QueueWriterError(
                f".ralph/{args.to_state}/{args.pbi_id}/ already exists in the "
                f"queue clone working tree; refusing to overwrite"
            )

        # Read + audit CLAIM.json BEFORE the move so the previous
        # owner's identity lands in the commit subject + HISTORY entry,
        # and the raw payload is preserved on stderr for forensic use.
        # Capture had_claim eagerly: ``git mv`` below renames the dir,
        # so ``claim_path_before.is_file()`` flips to False afterwards
        # and any later check would mis-classify the path.
        claim_path_before = from_dir / CLAIM_FILENAME
        had_claim = claim_path_before.is_file()
        recovered_from = _RECOVERED_FROM_NO_CLAIM
        if had_claim:
            recovered_from, claim_text = _read_claim_audit(claim_path_before)
            print(
                f"ralph-recover: claim contents before move:\n{claim_text}",
                file=sys.stderr,
            )

        entry_before = resolve_entry_file(from_dir)
        entry_name = entry_before.name

        # git mv stages renames for every tracked file in the PBI dir.
        to_dir.parent.mkdir(parents=True, exist_ok=True)
        rel_from = f".ralph/{CURRENT_FOLDER}/{args.pbi_id}"
        rel_to = f".ralph/{args.to_state}/{args.pbi_id}"
        run_git(clone, "mv", rel_from, rel_to)

        # Delete the renamed CLAIM.json so the destination carries no
        # claim — only PBIs in current/ may carry CLAIM.json under the
        # multi-ralph invariant. Subsequent ``git add`` stages the
        # deletion against HEAD so the recover lands as one commit.
        claim_after = to_dir / CLAIM_FILENAME
        if claim_after.is_file():
            claim_after.unlink()

        entry_after = to_dir / entry_name

        # Update the entry-file frontmatter: status follows the move;
        # for --to inbox, reset attempts to 0 so the queue can
        # re-dispatch cleanly (an orphaned claim is not a failed
        # attempt). Updated_at is stamped by write_frontmatter callers
        # via the existing helpers — we set status + attempts only.
        frontmatter, body = read_frontmatter(entry_after)
        frontmatter["status"] = args.to_state
        attempts_reset = False
        if args.to_state == "inbox":
            frontmatter["attempts"] = 0
            attempts_reset = True
        write_frontmatter(entry_after, frontmatter, body)

        append_history(
            to_dir,
            actor="ralph-recover",
            action="recover",
            detail=f"{CURRENT_FOLDER} -> {args.to_state}, from {recovered_from}",
        )

        history_file = to_dir / "HISTORY.md"
        # Only include claim_after in commit_paths when a CLAIM.json
        # actually existed in the source — otherwise nothing was renamed
        # to claim_after and nothing was unlinked, so ``git add --
        # claim_after`` would fail with "pathspec did not match any
        # files". BugBot finding 2026-06-01 (PR #69 recover.py:255).
        paths_to_commit = [entry_after, history_file]
        if had_claim:
            paths_to_commit.append(claim_after)
        commit_sha = commit_paths(
            clone,
            paths_to_commit,
            f"chore(queue): recover {args.pbi_id} from {recovered_from}",
        )

        pushed = False
        if not args.no_push:
            print(f"pushing {queue_branch} to origin...", file=sys.stderr)
            push(clone, queue_branch)
            pushed = True

        result = RecoverResult(
            pbi_id=args.pbi_id,
            from_state=CURRENT_FOLDER,
            to_state=args.to_state,
            entry_file=entry_after.relative_to(clone).as_posix(),
            queue_clone=str(clone),
            commit_sha=commit_sha,
            pushed=pushed,
            recovered_from_instance=recovered_from,
            attempts_reset=attempts_reset,
            dry_run_skipped=False,
        )
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    except _HaltGuardError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_HALT_GUARD
    except QueueWriterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
