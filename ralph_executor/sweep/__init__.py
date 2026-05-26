"""Sweep module: reconcile pending-pr PBIs against their PR state.

Public surface (populated in Task 3 once runner.py exists):
    run(ctx) -> SweepResult  --  the loop driver calls this when current/ is empty.
"""
