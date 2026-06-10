"""Manage clone-once + fetch-each-iter lifecycle per target repo.

Given a TargetRepoInfo and operator workspace_root:
- If <workspace_root>/clones/<owner>/<name>/ doesn't exist: git clone
- If exists: git fetch origin (refresh main + branches)
- Any git failure -> TargetUnreachable (caller decides — move to blocked
  typically)

The clone is full (not shallow) — ralph needs ``git diff main..`` and
full history for sweep-side investigation.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ralph_executor import git_ops
from ralph_executor.url_utils import TargetRepoInfo


class TargetUnreachable(RuntimeError):
    """Raised when clone or fetch of a target repo fails.

    Caller (typically ``pbi_claim.claim_pbi``) maps to ``ClaimError`` and
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
      <workspace_root>/clones/<owner>/<name>/

    Two-level structure (not flat ``<owner>-<name>``) so that
    e.g. ``owner=foo-bar, name=baz`` and ``owner=foo, name=bar-baz``
    — both valid GitHub coordinates — resolve to distinct directories.

    First call for a given target: ``git clone <info.clone_url> <root>``.
    Subsequent calls: ``git fetch origin`` inside <root>.

    Creates parent directories as needed. Raises
    ``TargetUnreachable`` on any git failure.
    """
    clone_root = workspace_root / "clones" / info.owner / info.name
    clone_root.parent.mkdir(parents=True, exist_ok=True)

    if (clone_root / ".git").is_dir():
        try:
            git_ops.fetch(clone_root)
        except git_ops.GitCommandError as exc:
            raise TargetUnreachable(f"git fetch failed for {info.clone_url}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TargetUnreachable(f"git fetch timed out for {info.clone_url}: {exc}") from exc
    else:
        try:
            git_ops.clone(info.clone_url, clone_root)
        except git_ops.GitCommandError as exc:
            raise TargetUnreachable(f"git clone failed for {info.clone_url}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TargetUnreachable(f"git clone timed out for {info.clone_url}: {exc}") from exc

    return TargetClone(info=info, clone_root=clone_root)
