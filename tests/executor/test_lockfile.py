"""Cross-platform workspace lockfile tests.

POSIX uses ``fcntl.flock(LOCK_EX | LOCK_NB)``; Windows uses
``msvcrt.locking(LK_NBLCK, 1)``. The same-process second-acquire
behaviour is platform-specific so the negative test splits into two
platform-gated variants.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from ralph_executor.lockfile import LockHeldError, WorkspaceLock


def test_acquire_creates_lockfile(tmp_path: Path) -> None:
    path = tmp_path / "queue-a" / ".ralph.lock"
    with WorkspaceLock(path, instance_id="ralph-a"):
        assert path.is_file()


def test_lock_payload_includes_pid_and_instance(tmp_path: Path) -> None:
    """Payload is written to disk and survives release for forensics."""
    path = tmp_path / "queue-a" / ".ralph.lock"
    lock = WorkspaceLock(path, instance_id="ralph-a")
    lock.acquire()
    lock.release()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["instance_id"] == "ralph-a"
    assert payload["pid"] == os.getpid()
    assert "hostname" in payload and isinstance(payload["hostname"], str)
    assert "started_at" in payload and isinstance(payload["started_at"], str)


def test_release_is_idempotent(tmp_path: Path) -> None:
    """Calling release() twice is a no-op (defensive)."""
    path = tmp_path / "queue-a" / ".ralph.lock"
    lock = WorkspaceLock(path, instance_id="ralph-a")
    lock.acquire()
    lock.release()
    lock.release()


def test_reacquire_after_release_succeeds(tmp_path: Path) -> None:
    path = tmp_path / "queue-a" / ".ralph.lock"
    first = WorkspaceLock(path, instance_id="ralph-a")
    first.acquire()
    first.release()
    second = WorkspaceLock(path, instance_id="ralph-a")
    second.acquire()
    second.release()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fcntl.flock semantics only")
def test_second_acquire_raises_posix(tmp_path: Path) -> None:
    """POSIX: fcntl.flock(LOCK_EX | LOCK_NB) blocks same-process second
    acquire on a different file handle (spike-confirmed [Errno 13])."""
    path = tmp_path / "queue-a" / ".ralph.lock"
    first = WorkspaceLock(path, instance_id="ralph-a")
    second = WorkspaceLock(path, instance_id="ralph-a")
    first.acquire()
    try:
        with pytest.raises(LockHeldError):
            second.acquire()
    finally:
        first.release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows msvcrt.locking semantics only")
def test_second_acquire_raises_windows(tmp_path: Path) -> None:
    """Windows: msvcrt.locking(LK_NBLCK, 1) is expected to block a
    same-process second acquire on a different file handle."""
    path = tmp_path / "queue-a" / ".ralph.lock"
    first = WorkspaceLock(path, instance_id="ralph-a")
    second = WorkspaceLock(path, instance_id="ralph-a")
    first.acquire()
    try:
        with pytest.raises(LockHeldError):
            second.acquire()
    finally:
        first.release()
