"""Reconcile orphan ``pending-pr/<PBI-ID>/`` directories.

An "orphan" is a pending-pr directory missing ``PR-LINK.md`` -- typically
created by manual gh squash-merges during operator-driven babysit cycles,
or by older PBIs predating PR-LINK.md introduction.

This module is called from two places:

- ``sweep/runner.py``'s ``run()`` loop, automatically, when an orphan
  is found during a sweep pass.
- ``ralph-executor reconcile`` CLI subcommand, on operator demand.

Both paths delegate to ``reconcile_orphan`` per PBI; ``reconcile_all``
is the iteration wrapper used by the CLI.

Subprocess shape: the staged ``pr/scripts/lookup_by_branch.py`` is
invoked via ``subprocess.run``. JSON on stdout, exit 0/2/3 per skill
convention. API errors (exit 3) do NOT raise -- they return
``KEEP_API_ERROR`` so a transient host outage doesn't crash the sweep
pass.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ralph_executor.sweep.runner import SweepContext
from ralph_executor.sweep.types import ReconcileAction, ReconcileReport

log = logging.getLogger(__name__)


class ReconcileError(RuntimeError):
    """Raised on a programming-error response from lookup_by_branch
    (exit 2) or on malformed JSON. API errors (exit 3) do NOT raise --
    they return ``KEEP_API_ERROR`` so a transient host outage doesn't
    crash the sweep pass.
    """


@dataclass(frozen=True)
class _LookupResult:
    """Parsed JSON payload from lookup_by_branch.py."""

    state: str | None  # "open" | "merged" | "closed" | "api_error" | None (no PR)
    url: str | None
    pr_id: int | None
    branch_exists: bool | None


def _invoke_lookup(
    ctx: SweepContext,
    branch: str,
    repo: str,
) -> _LookupResult:
    """Run lookup_by_branch.py and parse stdout.

    Raises ReconcileError on exit 2 / malformed JSON.
    Returns ``_LookupResult(state="api_error", ...)`` on exit 3 so the
    caller can map to KEEP_API_ERROR without raising.
    """
    script = ctx.ado_pr_scripts_path / "lookup_by_branch.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--branch",
            branch,
            "--repo",
            repo,
            "--include-branch-check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 2:
        raise ReconcileError(f"lookup_by_branch validation error (exit 2): {proc.stderr.strip()}")
    if proc.returncode == 3:
        log.warning(
            "reconcile: lookup_by_branch API error (exit 3) for branch %s: %s",
            branch,
            proc.stderr.strip(),
        )
        return _LookupResult(state="api_error", url=None, pr_id=None, branch_exists=None)
    if proc.returncode != 0:
        raise ReconcileError(
            f"lookup_by_branch unexpected exit {proc.returncode}: {proc.stderr.strip()}"
        )

    try:
        payload: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ReconcileError(
            f"lookup_by_branch returned malformed JSON: {exc} (stdout={proc.stdout!r})"
        ) from exc

    pr = payload.get("pr")
    if pr is None:
        return _LookupResult(
            state=None,
            url=None,
            pr_id=None,
            branch_exists=payload.get("branch_exists"),
        )
    return _LookupResult(
        state=pr["state"],
        url=pr["url"],
        pr_id=int(pr["pr_id"]),
        branch_exists=payload.get("branch_exists"),
    )


def _resolve_repo_name(ctx: SweepContext) -> str:
    """Return the GitHub/ADO repo name for ``lookup_by_branch --repo``.

    Prefer the explicit ``ctx.repo_name`` (always correct under both
    single-checkout and two-worktree mode). Fall back to deriving from
    ``ctx.queue_root.parent.name`` only when it is empty — this fallback
    exists for old callers and tests that construct ``SweepContext``
    without setting ``repo_name`` and are still on the single-checkout
    layout where ``queue_root`` is ``<repo>/.ralph``.
    """
    if ctx.repo_name:
        return ctx.repo_name
    return ctx.queue_root.parent.name


def reconcile_orphan(
    pbi_dir: Path,
    ctx: SweepContext,
    *,
    dry_run: bool = False,
) -> ReconcileAction:
    """Reconcile a single orphan pending-pr/ directory.

    Returns the action that WAS applied (or would be applied, when
    ``dry_run=True``). Raises ReconcileError on programming errors.
    """
    pbi_id = pbi_dir.name
    branch = f"ralph/{pbi_id}"
    repo = _resolve_repo_name(ctx)
    queue_root = ctx.queue_root

    result = _invoke_lookup(ctx, branch=branch, repo=repo)

    if result.state == "api_error":
        return ReconcileAction.KEEP_API_ERROR

    if result.state == "open":
        if not dry_run:
            _write_pr_link(pbi_dir, url=result.url, pr_id=result.pr_id)
        return ReconcileAction.KEEP_PENDING

    if result.state == "merged":
        dest = queue_root / "done" / pbi_id
        if not dry_run:
            _move_dir(pbi_dir, dest)
            _write_pr_link(dest, url=result.url, pr_id=result.pr_id)
        return ReconcileAction.MOVED_TO_DONE

    if result.state == "closed":
        dest = queue_root / "blocked" / pbi_id
        if not dry_run:
            _move_dir(pbi_dir, dest)
            _write_pr_link(dest, url=result.url, pr_id=result.pr_id)
        return ReconcileAction.MOVED_TO_BLOCKED

    if result.branch_exists:
        dest = queue_root / "blocked" / pbi_id
        action = ReconcileAction.MOVED_TO_BLOCKED
    else:
        dest = queue_root / "inbox" / pbi_id
        action = ReconcileAction.MOVED_TO_INBOX
    if not dry_run:
        _move_dir(pbi_dir, dest)
    return action


def _move_dir(src: Path, dest: Path) -> None:
    """Atomic-ish move via shutil.move. Wraps OSError to ReconcileError."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            raise ReconcileError(f"destination {dest} already exists; refusing to overwrite")
        shutil.move(str(src), str(dest))
    except OSError as exc:
        raise ReconcileError(f"failed to move {src} -> {dest}: {exc}") from exc


def _write_pr_link(pbi_dir: Path, *, url: str | None, pr_id: int | None) -> None:
    """Write PR-LINK.md with the discovered PR id + URL.

    Matches the shape ralph's normal flow writes: the file has a
    parseable ``PR ID <n>`` line so ``_read_pr_id`` in runner.py can
    consume it on the next sweep iteration.
    """
    if url is None or pr_id is None:
        return
    content = (
        f"# PR Link\n\n"
        f"PR ID {pr_id}\n"
        f"URL: {url}\n\n"
        f"_Reconciled by ralph-executor reconcile on {_now_iso()}._\n"
    )
    try:
        (pbi_dir / "PR-LINK.md").write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ReconcileError(f"failed to write PR-LINK.md in {pbi_dir}: {exc}") from exc


def _now_iso() -> str:
    """Wall-clock timestamp for audit. Module-level for monkeypatching."""
    return datetime.now(tz=UTC).isoformat()


def reconcile_all(
    ctx: SweepContext,
    *,
    dry_run: bool = False,
) -> ReconcileReport:
    """Iterate ``.ralph/pending-pr/`` and reconcile every orphan.

    Per-PBI failures are captured in ``report.errors`` without aborting.
    Dirs that already have PR-LINK.md are skipped (sweep handles them
    via the normal path).
    """
    pending_dir = ctx.queue_root / "pending-pr"
    actions: dict[str, ReconcileAction] = {}
    errors: dict[str, str] = {}

    if not pending_dir.is_dir():
        return ReconcileReport(actions={}, errors={})

    for entry in sorted(pending_dir.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / "PR-LINK.md").is_file():
            continue
        pbi_id = entry.name
        try:
            action = reconcile_orphan(entry, ctx, dry_run=dry_run)
            actions[pbi_id] = action
            log.info(
                "reconcile: %s -> %s%s",
                pbi_id,
                action.value,
                " (dry-run)" if dry_run else "",
            )
        except ReconcileError as err:
            errors[pbi_id] = str(err)
            log.warning("reconcile: failed for %s: %s", pbi_id, err)

    return ReconcileReport(actions=actions, errors=errors)
