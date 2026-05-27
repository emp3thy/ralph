"""Sweep module: reconcile pending-pr PBIs against their PR state.

Public surface:
    run(ctx) -> SweepResult  --  the loop driver calls this when current/ is empty.
"""

from ralph_executor.sweep.runner import SweepResult, run

__all__ = ["SweepResult", "run"]
