"""Single source of truth for the per-instance queue-clone path."""

from __future__ import annotations

from pathlib import Path


def queue_clone_path(workspace_root: Path, instance_id: str) -> Path:
    """Return the directory that holds this instance's queue clone."""
    if not instance_id:
        raise ValueError("queue_clone_path requires a non-empty instance_id")
    return workspace_root / f"queue-{instance_id}"
