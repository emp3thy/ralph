"""Claim a PBI from inbox/ into current/.

``claim_pbi`` is the public entry. In order:

  1. ``read_target_repo_from_pbi`` — read ``target_repo`` from the PBI
     entry-file frontmatter (probing ``PBI.md``, ``BUG.md``,
     ``FEEDBACK.md`` — same order as pbi_reader's discovery).
  2. ``parse_target_repo`` + host gate — only ``github.com`` is
     supported; failures raise ``ClaimError``.
  3. ``_claim_pbi_worktree`` — ensure the target clone
     (``target_clone.ensure_clone``), warn on legacy project TOML,
     pre-flight ``origin/<main_branch>``, move the PBI inbox/ ->
     current/ in the queue clone (``move_inbox_to_current``), and
     materialise the per-PBI work worktree on ``ralph/<id>``
     (``worktree_manager.materialise_worktree``).

Any step may raise ``ClaimError`` with a reason string. The caller in
the loop module catches it and routes the PBI to ``blocked/`` with the
reason recorded in HISTORY.md. ``feature_branch_name`` and
``read_target_repo_from_pbi`` are also consumed by the loop's resume
path, which re-derives target identity for PBIs already in current/.
"""

from __future__ import annotations

import logging
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ralph_executor import git_ops
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.movements import move_inbox_to_current
from ralph_executor.queue_git import queue_repo_root
from ralph_executor.safety import open_log
from ralph_executor.types import PBI
from ralph_executor.worktree_manager import materialise_worktree

if TYPE_CHECKING:
    from ralph_executor.url_utils import TargetRepoInfo

log = logging.getLogger(__name__)


def warn_project_toml_in_target_clone(clone_root: Path) -> None:
    """Emit a WARNING if ``<clone>/.ralph/config.toml`` exists.

    Called after every successful ``ensure_clone`` so a freshly cloned
    target gets exactly one WARNING per iteration that touches it. The
    file is NOT loaded — project TOML is gone after this refactor. The
    WARNING names the path so the operator can move the settings to
    ``~/.ralph/config.toml`` and delete the file.

    Detection failure (permission denied, transient I/O error reading
    the clone) is suppressed at DEBUG so a flaky filesystem never
    crashes the iteration.
    """
    cfg_file = clone_root / ".ralph" / "config.toml"
    try:
        present = cfg_file.is_file()
    except OSError as exc:
        log.debug("project-TOML check failed for %s: %s", cfg_file, exc)
        return
    if present:
        log.warning(
            "project TOML at %s is not supported -- ignored. "
            "Move settings to ~/.ralph/config.toml.",
            cfg_file,
        )


def feature_branch_name(pbi: PBI) -> str:
    return f"ralph/{pbi.id}"


class ClaimError(RuntimeError):
    """Raised when claim fails for a reason warranting blocked/ + HISTORY entry.

    Caught by ``iterate_once``, which moves the PBI to ``blocked/<id>/`` and
    appends the error message to HISTORY.md. Used by the multi-target claim
    path (target_repo parse, host check, ensure_clone) where failures are
    not retryable inside the loop itself.
    """


_ENTRY_FILENAMES: tuple[str, ...] = ("PBI.md", "BUG.md", "FEEDBACK.md")


def read_target_repo_from_pbi(pbi: PBI) -> str:
    """Read the ``target_repo`` field from the PBI's entry-file frontmatter.

    Probes ``PBI.md``, ``BUG.md``, ``FEEDBACK.md`` in order — matches
    pbi_reader's entry-file discovery. Raises ``ClaimError`` when the
    entry file is missing, the YAML frontmatter cannot be parsed, or the
    ``target_repo`` field is absent / empty.
    """
    entry: Path | None = None
    for name in _ENTRY_FILENAMES:
        candidate = pbi.path / name
        if candidate.is_file():
            entry = candidate
            break
    if entry is None:
        raise ClaimError(
            f"PBI {pbi.id} has no entry file (looked for {', '.join(_ENTRY_FILENAMES)})"
        )

    text = entry.read_text(encoding="utf-8")
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        raise ClaimError(f"PBI {pbi.id} entry file {entry.name} has no frontmatter fence")
    lines = text.splitlines()
    close_idx = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close_idx is None:
        raise ClaimError(f"PBI {pbi.id} entry file {entry.name} has no closing fence")
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:close_idx]))
    except yaml.YAMLError as exc:
        raise ClaimError(f"PBI {pbi.id} YAML parse error: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise ClaimError(f"PBI {pbi.id} frontmatter is not a mapping")
    target_repo = frontmatter.get("target_repo")
    if not target_repo:
        raise ClaimError(f"PBI {pbi.id} missing target_repo field")
    return str(target_repo)


def claim_pbi(cfg: ExecutorConfig, pbi: PBI) -> PBI:
    """Move PBI into current/ and create the per-PBI feature branch.

    Multi-target prelude:
      0. ``read_target_repo_from_pbi`` — read ``target_repo`` from the
         PBI entry-file frontmatter.
      0b. ``parse_target_repo`` — split into host/owner/name; ValueError
          surfaces as ``ClaimError('invalid target_repo URL: …')``.
      0c. Host gate — only ``github.com`` is supported on this PBI;
          other hosts raise ``ClaimError('unsupported host …')``.

    Claim body (always runs ``_claim_pbi_worktree``):
      1. ``target_clone.ensure_clone`` — clone-once / fetch-each-iter the
         target repo into ``<workspace_root>/clones/<owner>-<name>/``;
         ``TargetUnreachable`` maps to ``ClaimError('target unreachable: …')``.
      2. ``move_inbox_to_current`` operates against the queue clone at
         ``<workspace_root>/queue/`` (movements._move resolves the path
         via ``queue_repo_root``).
      3. Materialise the per-PBI work worktree INSIDE the target clone at
         ``<clone_root>/.ralph-work/<PBI-ID>/`` on ``ralph/<PBI-ID>``,
         forked from the clone's ``origin/<main_branch>`` when the branch
         is new.

    The queue is its own clone now, not a branch on the primary checkout.
    """
    from ralph_executor.url_utils import parse_target_repo

    target_url = read_target_repo_from_pbi(pbi)
    try:
        info = parse_target_repo(target_url)
    except ValueError as exc:
        raise ClaimError(f"invalid target_repo URL: {exc}") from exc
    if info.host != "github.com":
        raise ClaimError(f"unsupported host {info.host!r} (only github.com is supported)")

    return _claim_pbi_worktree(cfg, pbi, target_url=target_url, info=info)


def _claim_pbi_worktree(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    target_url: str,
    info: TargetRepoInfo,
) -> PBI:
    """Worktree-mode implementation of ``claim_pbi``.

    The primary checkout's branch is never touched. ``.ralph/`` reads
    and writes go through the queue clone at ``<workspace_root>/queue/``
    (materialised earlier by ``queue_git.pull_queue`` → ``ensure_queue_clone``);
    code edits go through the per-PBI work worktree, which lives INSIDE
    the target's clone (``<workspace_root>/clones/<owner>/<name>/.ralph-work/<PBI-ID>/``).
    """
    from dataclasses import replace

    from ralph_executor import target_clone as tc_mod

    try:
        clone = tc_mod.ensure_clone(info, workspace_root=cfg.workspace_root)
    except tc_mod.TargetUnreachable as exc:
        raise ClaimError(f"target unreachable: {exc}") from exc
    warn_project_toml_in_target_clone(clone.clone_root)
    # Pre-flight the base ref BEFORE the inbox -> current move. An empty
    # target repo (no commits, no main branch) would otherwise crash
    # ``ensure_worktree`` below with a raw ``GitCommandError`` after the
    # PBI has already been promoted to ``current/`` — taking the loop
    # down AND stranding the PBI with no worktree. Routing this through
    # ``ClaimError`` here keeps the claim atomic and lets ``iterate_once``
    # demote the PBI inbox -> blocked with the reason in HISTORY.md.
    if not git_ops.is_branch_remote(clone.clone_root, cfg.main_branch):
        raise ClaimError(
            f"target repo {target_url} has no origin/{cfg.main_branch} "
            f"(target may be empty or the main branch is misnamed)"
        )
    event_log = open_log(queue_repo_root(cfg))
    try:
        moved = move_inbox_to_current(
            cfg,
            pbi,
            event_log=event_log,
            now=datetime.now(tz=UTC),
            instance_id=cfg.instance_id,
            hostname=socket.gethostname(),
        )
    finally:
        event_log.close()
    branch = feature_branch_name(moved)
    work_wt = materialise_worktree(
        cfg,
        clone_root=clone.clone_root,
        pbi_id=moved.id,
        branch=branch,
    )
    return replace(
        moved,
        target_repo=target_url,
        target_info=info,
        work_worktree=work_wt,
    )
