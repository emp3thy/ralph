"""migrate-queue subcommand: bootstrap ``emp3thy/ralph-queue`` from an
existing ``.ralph/`` tree. One-shot. Refuses a non-empty target.

Exclusions per spec section 3:
  - ``.ralph/done/``      (whole dir)
  - ``.ralph/blocked/META-cycle-*.md``
  - ``.ralph/state/``     (gitignored, local-only)
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ralph_executor.subprocess_utils import run_text

log = logging.getLogger(__name__)

_STATE_DIRS = ("inbox", "current", "pending-pr", "blocked", "archive")


class MigrateQueueError(RuntimeError):
    """Raised on any migration failure."""


def _run_git(
    repo: Path | None, *args: str, timeout: float = 120.0
) -> subprocess.CompletedProcess[str]:
    """``run_text`` wrapper that converts subprocess failures to
    :class:`MigrateQueueError`.

    Without this, ``TimeoutExpired`` (slow ``ls-remote`` / ``push``)
    or ``OSError`` (``git`` absent from ``PATH``) would escape past
    ``cli.py``'s migrate-queue dispatch handler (which only catches
    ``MigrateQueueError``) and crash with a raw traceback.
    """
    argv = ["git", *(["-C", str(repo)] if repo is not None else []), *args]
    try:
        return run_text(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MigrateQueueError(
            f"git {' '.join(args)} exceeded {timeout}s in {repo or '<no repo>'}"
        ) from exc
    except OSError as exc:
        raise MigrateQueueError(
            f"git {' '.join(args)} failed in {repo or '<no repo>'}: {exc}"
        ) from exc


def copy_queue_tree_filtered(source: Path, dest: Path) -> dict[str, int]:
    """Copy ``source/.ralph/`` into ``dest/.ralph/`` with spec exclusions.

    Excludes ``.ralph/done/`` (omitted from the copy set), every
    ``.ralph/blocked/META-cycle-*.md`` sentinel, and the local-only
    ``.ralph/state/`` directory. Writes a skeleton ``.gitignore`` at
    ``dest/.gitignore`` so ``state/`` stays untracked on the new repo.

    Returns per-state-folder PBI counts (number of direct sub-directories
    under each kept state folder).
    """
    src_root = source / ".ralph"
    if not (src_root / "inbox").is_dir():
        raise MigrateQueueError(
            f"source {source} is not a valid queue tree (missing .ralph/inbox/)"
        )
    dst_root = dest / ".ralph"
    dst_root.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {state: 0 for state in _STATE_DIRS}

    for state in _STATE_DIRS:
        src_state = src_root / state
        dst_state = dst_root / state
        dst_state.mkdir(parents=True, exist_ok=True)

        if not src_state.is_dir():
            continue

        for child in sorted(src_state.iterdir()):
            if (
                state == "blocked"
                and child.is_file()
                and child.name.startswith("META-cycle-")
                and child.suffix == ".md"
            ):
                continue
            if child.is_dir():
                shutil.copytree(child, dst_state / child.name)
                counts[state] += 1
            elif child.is_file():
                shutil.copy2(child, dst_state / child.name)

    cfg_src = src_root / "config.toml"
    if cfg_src.is_file():
        shutil.copy2(cfg_src, dst_root / "config.toml")

    (dest / ".gitignore").write_text(".ralph/state/\n", encoding="utf-8")

    return counts


def _target_is_empty(target_url: str) -> bool:
    """Return True if the target repo has zero refs (empty repo).

    Lists ALL refs (no ``--heads`` filter) so a target that contains
    only tags (or only PR refs) is correctly classified as non-empty
    and rejected by the migrate-queue safety check.
    """
    result = _run_git(None, "ls-remote", target_url)
    if result.returncode != 0:
        raise MigrateQueueError(
            f"could not list refs on target {target_url!r} "
            f"(exit {result.returncode}): {result.stderr.strip()}\n"
            f"If this is an auth problem, run `gh auth login`."
        )
    return not result.stdout.strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ralph-executor migrate-queue")
    parser.add_argument(
        "--source",
        required=True,
        type=Path,
        help="Path to the existing queue worktree (parent of .ralph/).",
    )
    parser.add_argument(
        "--target",
        required=True,
        help="HTTPS (or file://) URL of the empty new queue repo.",
    )
    return parser


def main(argv: list[str]) -> int:
    """Entry point for the ``migrate-queue`` subcommand.

    Validates the source has ``.ralph/inbox/``, probes the target with
    ``git ls-remote`` (must report zero refs of any kind), stages a temp dir via
    :func:`copy_queue_tree_filtered`, then ``git init / add / commit /
    push origin main``. Raises :class:`MigrateQueueError` on any failure
    so the caller can surface a clean error message.
    """
    args = _build_parser().parse_args(argv)
    src: Path = args.source.resolve()
    target: str = args.target

    if not (src / ".ralph" / "inbox").is_dir():
        raise MigrateQueueError(f"source {src} does not contain .ralph/inbox/ -- wrong path?")
    if not _target_is_empty(target):
        raise MigrateQueueError(
            f"target {target!r} is not empty (has existing refs); refusing to overwrite"
        )

    with tempfile.TemporaryDirectory(prefix="ralph-migrate-queue-") as stage_str:
        stage = Path(stage_str)
        counts = copy_queue_tree_filtered(src, stage)

        author = ("-c", "user.email=ralph@local", "-c", "user.name=ralph")
        steps: tuple[tuple[str, ...], ...] = (
            ("init", "--initial-branch=main"),
            (*author, "add", "."),
            (
                *author,
                "commit",
                "-m",
                "chore: bootstrap ralph-queue from existing state",
            ),
            ("remote", "add", "origin", target),
            ("push", "origin", "main"),
        )
        for cmd in steps:
            result = _run_git(stage, *cmd)
            if result.returncode != 0:
                raise MigrateQueueError(
                    f"git {' '.join(cmd)} failed (exit {result.returncode}): "
                    f"{result.stderr.strip()}"
                )

    print("migrate-queue: success.")
    print()
    print("PBI counts pushed:")
    for state in _STATE_DIRS:
        print(f"  {state:11s} {counts[state]}")
    print()
    print("Next steps (operator runs manually):")
    print("  1. Add to ~/.ralph/config.toml:")
    print(f'         queue_repo = "{target}"')
    print("  2. Delete the old ralph-queue branch (GitHub):")
    print("         gh api -X DELETE repos/<owner>/<repo>/git/refs/heads/ralph-queue")
    print("  3. Remove the stale local queue worktree if any:")
    print("         git worktree remove <repo>/.ralph-work/queue")
    print()
    print("Cycle-detector state is local-only; the new queue clone starts blind.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
