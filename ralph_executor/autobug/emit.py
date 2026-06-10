"""Queue write + push_with_rebase for new / bump / reopen autobug PBIs."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from ralph_executor import git_ops
from ralph_executor.autobug.compose import (
    _format_traceback,
    build_bug_md,
    build_frontmatter,
    build_reproduce_md,
)
from ralph_executor.autobug.types import Context

log = logging.getLogger(__name__)


def _next_seq(queue_root: Path, short_sig: str) -> str:
    """Find the highest existing <seq> for this short_sig across all states."""
    max_seq = 0
    for state in ("inbox", "current", "pending-pr", "done", "blocked"):
        state_dir = queue_root / ".ralph" / state
        if not state_dir.is_dir():
            continue
        for child in state_dir.iterdir():
            if not child.is_dir():
                continue
            m = re.fullmatch(rf"autobug-{short_sig}-(\d+)", child.name)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
    return f"{max_seq + 1:03d}"


def new(
    *,
    signature: str,
    exc: BaseException,
    ctx: Context,
    trigger_kind: str,
    severity: str,
    target_repo: str,
    stderr: str | None = None,
    exit_code: int | None = None,
) -> str:
    short_sig = signature[:6]
    seq = _next_seq(ctx.queue_root, short_sig)
    pbi_id = f"autobug-{short_sig}-{seq}"
    pbi_dir = ctx.queue_root / ".ralph" / "inbox" / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=False)
    try:
        fm = build_frontmatter(
            pbi_id=pbi_id,
            signature=signature,
            trigger_kind=trigger_kind,
            severity=severity,
            ctx=ctx,
            target_repo=target_repo,
        )
        bug_body = build_bug_md(exc, ctx)
        (pbi_dir / "BUG.md").write_text(fm + "\n" + bug_body + "\n", encoding="utf-8")
        (pbi_dir / "REPRODUCE.md").write_text(
            build_reproduce_md(
                pbi_id,
                trigger_kind=trigger_kind,
                exc=exc,
                stderr=stderr,
                exit_code=exit_code,
                ctx=ctx,
            )
            + "\n",
            encoding="utf-8",
        )
        (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
        _commit_and_push(
            ctx,
            f"chore(queue): add {pbi_id} (autobug signature {short_sig})",
            paths=[pbi_dir],
        )
    except BaseException:
        # Atomic emit: a stranded uncommitted inbox dir would deadlock
        # iterate_once on UncommittedSource forever (see emit module docstring).
        shutil.rmtree(pbi_dir, ignore_errors=True)
        raise
    return pbi_id


def bump(pbi_id: str, exc: BaseException, ctx: Context) -> None:
    """Increment occurrences + last_seen, append trace, push.

    The original BUG.md's ``## Stacktrace`` section counts as trace 1
    (matching ``occurrences: 1`` at emit time); each bump appends
    ``## Trace N`` where N == post-bump occurrences value.
    """
    bug_path = _locate_bug_md(ctx, pbi_id)
    if bug_path is None:
        log.warning("autobug bump: PBI %s not found in any open state", pbi_id)
        return
    original_bytes = bug_path.read_bytes()
    text = original_bytes.decode("utf-8")
    text = _bump_occurrences(text)
    text = _set_last_seen(text, ctx)
    trace_n = _read_occurrences(text)
    text += f"\n\n## Trace {trace_n}\n\n```\n{_format_traceback(exc)}\n```\n"
    try:
        # write_text lives INSIDE the try so a mid-write failure (disk
        # full, permission revoked) is also covered by the restore path.
        bug_path.write_text(text, encoding="utf-8")
        _commit_and_push(
            ctx,
            f"chore(ralph-queue): bump {pbi_id} (occurrence {trace_n})",
            paths=[bug_path],
        )
    except BaseException:
        # Restore the tracked BUG.md so the next iteration does not
        # carry an uncommitted occurrence edit forever.
        bug_path.write_bytes(original_bytes)
        raise


def reopen(
    *,
    existing_pbi_id: str,
    exc: BaseException,
    signature: str,
    ctx: Context,
    trigger_kind: str,
    severity: str,
    target_repo: str,
    stderr: str | None = None,
    exit_code: int | None = None,
) -> str:
    short_sig = signature[:6]
    seq = _next_seq(ctx.queue_root, short_sig)
    # Ensure the regression PBI id is distinct from the existing one even
    # if the prior dir has been moved/removed and ``_next_seq`` would
    # otherwise reuse its number.
    existing_m = re.fullmatch(rf"autobug-{re.escape(short_sig)}-(\d+)", existing_pbi_id)
    if existing_m:
        existing_seq = int(existing_m.group(1))
        if int(seq) <= existing_seq:
            seq = f"{existing_seq + 1:03d}"
    pbi_id = f"autobug-{short_sig}-{seq}"
    pbi_dir = ctx.queue_root / ".ralph" / "inbox" / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=False)
    try:
        fm = build_frontmatter(
            pbi_id=pbi_id,
            signature=signature,
            trigger_kind=trigger_kind,
            severity=severity,
            ctx=ctx,
            target_repo=target_repo,
            regression_of=existing_pbi_id,
        )
        bug_body = build_bug_md(exc, ctx)
        (pbi_dir / "BUG.md").write_text(fm + "\n" + bug_body + "\n", encoding="utf-8")
        (pbi_dir / "REPRODUCE.md").write_text(
            build_reproduce_md(
                pbi_id,
                trigger_kind=trigger_kind,
                exc=exc,
                stderr=stderr,
                exit_code=exit_code,
                ctx=ctx,
            )
            + "\n",
            encoding="utf-8",
        )
        (pbi_dir / "HISTORY.md").write_text(
            f"## Regression of {existing_pbi_id}\n\n"
            f"Re-emerged on {ctx.now.replace(microsecond=0).isoformat()}.\n",
            encoding="utf-8",
        )
        _commit_and_push(
            ctx,
            f"chore(queue): reopen {existing_pbi_id} as {pbi_id}",
            paths=[pbi_dir],
        )
    except BaseException:
        # Atomic emit — see new() for rationale.
        shutil.rmtree(pbi_dir, ignore_errors=True)
        raise
    return pbi_id


def _locate_bug_md(ctx: Context, pbi_id: str) -> Path | None:
    for state in ("inbox", "current", "pending-pr"):
        p = ctx.queue_root / ".ralph" / state / pbi_id / "BUG.md"
        if p.is_file():
            return p
    return None


_OCC_RE = re.compile(r"^occurrences:\s*(\d+)\s*$", re.MULTILINE)
_LAST_SEEN_RE = re.compile(r"^last_seen:\s*\S+\s*$", re.MULTILINE)


def _bump_occurrences(text: str) -> str:
    m = _OCC_RE.search(text)
    if not m:
        return text
    new_n = int(m.group(1)) + 1
    return text[: m.start()] + f"occurrences: {new_n}" + text[m.end() :]


def _read_occurrences(text: str) -> int:
    m = _OCC_RE.search(text)
    return int(m.group(1)) if m else 1


def _set_last_seen(text: str, ctx: Context) -> str:
    iso = ctx.now.replace(microsecond=0).isoformat()
    return _LAST_SEEN_RE.sub(f"last_seen: {iso}", text)


def _commit_and_push(ctx: Context, msg: str, *, paths: list[Path]) -> None:
    """Stage the named paths and commit + push on the queue branch.

    ``paths`` is REQUIRED — ``git add -A`` over the whole worktree is
    unsafe while ralph holds the exclusive ``.ralph.lock`` byte-range
    lock on Windows (BUG-COMMIT-ALL-RALPH-LOCK-WINDOWS). Callers pass
    the PBI dir (for ``new`` / ``reopen``) or the modified ``BUG.md``
    path (for ``bump``).
    """
    git_ops.commit_paths(ctx.queue_root, msg, paths)
    git_ops.push_with_rebase(ctx.queue_root, remote="origin", branch=ctx.queue_branch)
