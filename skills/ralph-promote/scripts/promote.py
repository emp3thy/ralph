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
    parse_frontmatter_text,
    push,
    read_frontmatter,
    read_path_from_head,
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
        working_severity = str(frontmatter.get("severity", ""))

        # Idempotency check reads HEAD's frontmatter, not the working
        # tree's. A previous invocation that wrote the new severity to
        # disk but failed the commit (pre-commit hook, missing user
        # config, etc.) leaves the new value on disk while HEAD still
        # has the old one — re-running must commit + push the change,
        # not silently exit 0 leaving the queue branch unchanged.
        rel_entry = str(entry_file.relative_to(repo)).replace("\\", "/")
        head_text = read_path_from_head(repo, rel_entry)
        head_severity = ""
        if head_text is not None:
            head_front, _ = parse_frontmatter_text(head_text, source=f"HEAD:{rel_entry}")
            head_severity = str(head_front.get("severity", ""))
        # ``previous_severity`` is what HISTORY.md + the commit message
        # report. In the partial-failure retry case the working tree
        # already shows the new value, so we'd write a misleading
        # "high -> high" entry. Prefer HEAD's committed severity when
        # it exists; fall back to the working tree only when HEAD does
        # not yet contain the file (fresh PBI just added).
        previous_severity = head_severity if head_text is not None else working_severity
        # "nothing to do" cases:
        #   (a) HEAD has the file AND both HEAD and working tree show
        #       the target severity (the normal already-promoted shape).
        #   (b) HEAD does NOT have the file (fresh PBI from a failed
        #       ralph-add commit) AND working tree shows the target
        #       severity. Without this branch we'd compute a misleading
        #       "severity: high -> high" history entry from
        #       previous_severity == working_severity == args.severity.
        nothing_to_do = working_severity == args.severity and (
            head_severity == args.severity or head_text is None
        )
        if nothing_to_do:
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

        # Gate write + append independently.
        # ``working_severity != args.severity`` → frontmatter still needs
        # writing.
        # Suppress the append ONLY when working_severity == args.severity
        # AND head_severity != args.severity — the precise partial-
        # failure-retry signature where the prior attempt wrote the
        # working tree but never committed. Inside that window the
        # prior attempt may also have appended the HISTORY entry; skip
        # only if it's already there.
        # Outside the retry window — i.e. the working tree disagrees
        # with the target, so this is a fresh promote — always append,
        # even if HISTORY happens to contain an identical detail string
        # from an older now-reversed cycle (normal→high→normal→high).
        if working_severity != args.severity:
            frontmatter["severity"] = args.severity
            frontmatter["updated_at"] = _now_iso()
            write_frontmatter(entry_file, frontmatter, body)

        history_file = pbi_dir / "HISTORY.md"
        history_detail = f"severity: {previous_severity} -> {args.severity}"
        # Per-invocation dedup that survives repeated-cycle history.
        # Compare working tree HISTORY against HEAD's HISTORY: if the
        # working tree already has MORE occurrences of ``history_detail``
        # than HEAD, the previous (failed) attempt already appended it
        # — skip to avoid a duplicate. Otherwise (no uncommitted append
        # yet, including the case where HEAD's count == working count
        # because earlier successful cycles match) always append. This
        # works for both partial-failure shapes (crash between write+
        # append vs between append+commit) AND for fresh repeat-cycles
        # where the detail string happens to match an older entry.
        rel_history = str(history_file.relative_to(repo)).replace("\\", "/")
        head_history = read_path_from_head(repo, rel_history) or ""
        working_history = history_file.read_text(encoding="utf-8") if history_file.is_file() else ""
        skip_append = working_history.count(history_detail) > head_history.count(history_detail)
        if not skip_append:
            append_history(
                pbi_dir,
                actor="ralph-promote",
                action="promote",
                detail=history_detail,
            )
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
