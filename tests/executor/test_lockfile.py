"""Tests for :mod:`ralph_executor.lockfile`."""

from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

import pytest

from ralph_executor.lockfile import LockfileError, WorkspaceLockfile


def test_acquire_creates_lockfile(tmp_path: Path) -> None:
    lock = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-a", hostname="box-a")
    lock.acquire()
    try:
        assert (tmp_path / ".ralph.lock").exists()
    finally:
        lock.release()
    # Read payload AFTER release: on Windows ``msvcrt.locking`` byte-range
    # locks block any other open() of the locked file, including from the
    # same process. The payload survives release (file isn't deleted).
    info = json.loads((tmp_path / ".ralph.lock").read_text(encoding="utf-8"))
    assert info["instance_id"] == "ralph-a"
    assert info["hostname"] == "box-a"
    assert info["pid"] > 0
    assert "started_at" in info


def test_second_acquire_same_process_raises(tmp_path: Path) -> None:
    a = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-a", hostname="box-a")
    b = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-b", hostname="box-a")
    a.acquire()
    try:
        with pytest.raises(LockfileError, match="another ralph already running"):
            b.acquire()
    finally:
        a.release()


def test_release_allows_reacquire(tmp_path: Path) -> None:
    a = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-a", hostname="box-a")
    a.acquire()
    a.release()
    b = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-a", hostname="box-a")
    b.acquire()
    b.release()


def test_release_is_idempotent_when_not_acquired(tmp_path: Path) -> None:
    """release() before acquire() (or after a prior release) must not raise.

    Lifecycle is documented as ``acquire → run → release``, but the
    ``finally``-clause style used by callers may invoke release on a
    never-acquired instance if acquire itself raised."""
    lock = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-a", hostname="box-a")
    lock.release()  # no-op
    lock.acquire()
    lock.release()
    lock.release()  # second release is a no-op


def test_corrupted_payload_does_not_break_lock(tmp_path: Path) -> None:
    """If the JSON payload is malformed (e.g. partial prior crash), the lock
    semantics still work — the OS lock is the source of truth, not the JSON."""
    (tmp_path / ".ralph.lock").write_text("not json")
    a = WorkspaceLockfile(tmp_path / ".ralph.lock", instance_id="ralph-a", hostname="box-a")
    a.acquire()
    a.release()
    # Acquire succeeded; payload now valid JSON of our own.
    info = json.loads((tmp_path / ".ralph.lock").read_text(encoding="utf-8"))
    assert info["instance_id"] == "ralph-a"


def test_parent_directory_is_created(tmp_path: Path) -> None:
    """Lock path inside a not-yet-existing dir: acquire mkdir -p's the parent."""
    deep = tmp_path / "queue-ralph-a" / ".ralph.lock"
    assert not deep.parent.exists()
    lock = WorkspaceLockfile(deep, instance_id="ralph-a", hostname="box-a")
    lock.acquire()
    try:
        assert deep.exists()
    finally:
        lock.release()


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl-only test")
def test_cross_process_contention_posix(tmp_path: Path) -> None:
    """Spawn a second process; second acquire fails with LockfileError."""
    lock_path = tmp_path / ".ralph.lock"
    a = WorkspaceLockfile(lock_path, instance_id="ralph-a", hostname="box-a")
    a.acquire()
    try:
        ctx = multiprocessing.get_context("spawn")
        proc = ctx.Process(
            target=_attempt_acquire,
            args=(str(lock_path), "ralph-b", "box-a"),
        )
        proc.start()
        proc.join(timeout=10)
        assert proc.exitcode == 1  # second acquire should fail
    finally:
        a.release()


def _attempt_acquire(path: str, instance_id: str, hostname: str) -> None:
    """Helper run in subprocess; exits 1 on LockfileError, 0 on unexpected success."""
    try:
        lock = WorkspaceLockfile(Path(path), instance_id=instance_id, hostname=hostname)
        lock.acquire()
    except LockfileError:
        sys.exit(1)
    sys.exit(0)
