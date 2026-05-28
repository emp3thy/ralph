"""migrate-queue subcommand: bootstrap emp3thy/ralph-queue from an existing
.ralph/ tree. One-shot. Refuses a non-empty target.

Exclusions per spec section 3:
  - .ralph/done/      (whole dir)
  - .ralph/blocked/META-cycle-*.md
  - .ralph/state/     (gitignored, local-only)

This module currently exposes only the file-filter helper. The CLI
``main`` entry point and the ``_target_is_empty`` git-probe land with
Task 7b.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

_STATE_DIRS = ("inbox", "current", "pending-pr", "blocked", "archive")


class MigrateQueueError(RuntimeError):
    """Raised on any migration failure."""


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
