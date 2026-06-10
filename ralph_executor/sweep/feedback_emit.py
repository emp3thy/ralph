"""Two-phase feedback-PBI emission: write the bundle directory, then persist
sidecar state; either failure rolls the directory back so the next sweep can
retry.

``emit_feedback_pbi`` takes exactly what it consumes (``queue_root`` and
the sweep's ``now`` timestamp) instead of the runner's ``SweepContext``
so this module stays leaf-level within ``sweep/`` and never imports the
runner.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from ralph_executor.sweep import feedback_pbi as feedback_module
from ralph_executor.sweep import state as sidecar_state
from ralph_executor.sweep.history import append_history
from ralph_executor.sweep.types import Decision, PrSnapshot, SweepPbiError


def emit_feedback_pbi(
    *,
    pbi_dir: Path,
    decision: Decision,
    snapshot: PrSnapshot,
    sidecar: sidecar_state.SweepSidecar,
    queue_root: Path,
    now: datetime,
) -> None:
    next_round = sidecar.last_feedback_round + 1
    bundle = feedback_module.render(
        pr=snapshot,
        originating_pbi_id=pbi_dir.name,
        round_number=next_round,
        new_comments=decision.new_comments,
        original_pbi_summary=read_original_summary(pbi_dir),
        generated_at=now,
    )
    target_dir = queue_root / "inbox" / bundle.directory_name
    if target_dir.exists():
        raise SweepPbiError(f"feedback PBI {target_dir} already exists; refusing to overwrite")
    # Wrap the file IO so EXDEV / EACCES / ENOSPC become SweepPbiError
    # rather than escaping run()'s per-PBI isolation. On failure, tear
    # down any partial target_dir — otherwise the guard above
    # (`if target_dir.exists()`) blocks every subsequent sweep with
    # "already exists; refusing to overwrite", AND the sidecar never
    # advances so next_round computes the same path on every retry,
    # permanently stranding the PBI in pending-pr/.
    try:
        target_dir.mkdir(parents=True)
        (target_dir / "FEEDBACK.md").write_text(bundle.feedback_md, encoding="utf-8")
        (target_dir / "PR-LINK.md").write_text(bundle.pr_link_md, encoding="utf-8")
        (target_dir / "ORIGINAL.md").write_text(bundle.original_md, encoding="utf-8")
        (target_dir / "HISTORY.md").write_text(bundle.history_md, encoding="utf-8")
    except OSError as exc:
        _cleanup_partial_feedback_dir(target_dir)
        raise SweepPbiError(f"failed to write feedback PBI {target_dir}: {exc}") from exc

    new_ids = {f"{c.thread_id}:{c.comment_id}" for c in decision.new_comments}
    # write_sidecar calls tmp.write_text + tmp.replace; both can raise
    # OSError (ENOSPC etc.). Wrap so it's caught per-PBI rather than
    # escaping run(). If the sidecar fails AFTER the feedback dir has
    # been written, tear down the feedback dir too — otherwise the
    # next sweep computes the same next_round (sidecar wasn't bumped),
    # hits target_dir.exists() and raises "already exists" forever,
    # permanently stranding the PBI. Mirrors the cleanup in the
    # feedback-dir except block above.
    try:
        sidecar_state.write_sidecar(
            pbi_dir,
            sidecar_state.SweepSidecar(
                last_feedback_sweep=now,
                last_feedback_round=next_round,
                last_seen_comment_ids=sidecar_state.merge_seen_comment_ids(
                    sidecar.last_seen_comment_ids, new_ids
                ),
                last_ci_status=sidecar.last_ci_status,
            ),
        )
    except OSError as exc:
        _cleanup_partial_feedback_dir(target_dir)
        raise SweepPbiError(f"failed to write sidecar for {pbi_dir}: {exc}") from exc
    append_history(pbi_dir, decision.reason, now)


def read_original_summary(pbi_dir: Path) -> str:
    for candidate in ("PBI.md", "BUG.md", "FEEDBACK.md"):
        path = pbi_dir / candidate
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise SweepPbiError(f"failed to read {candidate} in {pbi_dir}: {exc}") from exc
            # First 40 lines; enough for context, bounded for file size.
            return "\n".join(text.splitlines()[:40])
    return "(no original PBI body found)"


def _cleanup_partial_feedback_dir(target_dir: Path) -> None:
    """Tear down a partially written feedback-PBI directory.

    Single-sourced cleanup for both failure sites in ``emit_feedback_pbi``
    (bundle-write failure and sidecar-write failure); each site keeps its
    own distinct ``SweepPbiError`` message.
    """
    shutil.rmtree(target_dir, ignore_errors=True)
