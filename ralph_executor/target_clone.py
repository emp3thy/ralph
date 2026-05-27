"""Manage clone-once + fetch-each-iter lifecycle per target repo.

Given a TargetRepoInfo and operator workspace_root:
- If <workspace_root>/clones/<info.slug>/ doesn't exist: git clone
- If exists: git fetch origin (refresh main + branches)
- Any git failure -> TargetUnreachable (caller decides — move to blocked
  typically)

The clone is full (not shallow) — ralph needs ``git diff main..`` and
full history for sweep-side investigation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ralph_executor import git_ops
from ralph_executor.url_utils import TargetRepoInfo


class TargetUnreachable(RuntimeError):
    """Raised when clone or fetch of a target repo fails.

    Caller (typically ``loop._claim_pbi``) maps to ``_ClaimError`` and
    moves the PBI to ``blocked/`` with the error message in HISTORY.md.
    """


@dataclass(frozen=True)
class TargetClone:
    """A target-repo clone on the operator's machine."""

    info: TargetRepoInfo
    clone_root: Path


def ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
    """Return a TargetClone, cloning or fetching as needed.

    Disk layout:
      <workspace_root>/clones/<info.slug>/

    First call for a given target: ``git clone <info.clone_url> <root>``.
    Subsequent calls: ``git fetch origin`` inside <root>.

    Creates ``workspace_root/clones/`` if missing. Raises
    ``TargetUnreachable`` on any git failure.
    """
    clones_dir = workspace_root / "clones"
    clones_dir.mkdir(parents=True, exist_ok=True)
    clone_root = clones_dir / info.slug

    if (clone_root / ".git").is_dir():
        try:
            git_ops.fetch(clone_root)
        except git_ops.GitCommandError as exc:
            raise TargetUnreachable(f"git fetch failed for {info.clone_url}: {exc}") from exc
    else:
        try:
            git_ops.clone(info.clone_url, clone_root)
        except git_ops.GitCommandError as exc:
            raise TargetUnreachable(f"git clone failed for {info.clone_url}: {exc}") from exc

    return TargetClone(info=info, clone_root=clone_root)
