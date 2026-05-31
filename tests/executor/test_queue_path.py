"""Tests for ``ralph_executor.queue_path.queue_clone_path``."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.queue_path import queue_clone_path


def test_returns_namespaced_path(tmp_path: Path) -> None:
    assert queue_clone_path(tmp_path, "ralph-a") == tmp_path / "queue-ralph-a"


def test_n1_default_case(tmp_path: Path) -> None:
    assert queue_clone_path(tmp_path, "boxa") == tmp_path / "queue-boxa"


def test_empty_instance_id_raises() -> None:
    with pytest.raises(ValueError, match="instance_id"):
        queue_clone_path(Path("/tmp"), "")
