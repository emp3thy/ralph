"""OS-level exclusive workspace lockfile.

Each ralph instance holds an exclusive lock on
``<workspace>/queue-<instance-id>/.ralph.lock`` from startup to process
exit. The OS releases the lock on process death (clean or crash); no
stale-lock recovery logic is needed.

Platform dispatch is by :data:`sys.platform`:

* ``win32`` — :func:`msvcrt.locking` with ``LK_NBLCK`` (non-blocking).
* anything else — :func:`fcntl.flock` with ``LOCK_EX | LOCK_NB``.

The JSON payload written to the file is informational only — operators
inspect it to see which instance currently owns the workspace. The
authoritative signal is the OS lock itself, so a corrupted or empty
payload does not break lock semantics.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class LockfileError(RuntimeError):
    """Raised when the workspace lockfile cannot be acquired or released."""


class WorkspaceLockfile:
    """Cross-platform exclusive workspace lockfile.

    Lifecycle is ``acquire() → ... → release()``. The OS releases the
    lock on process exit even if :meth:`release` is never called, so the
    only reason to call :meth:`release` explicitly is to make the JSON
    payload disappear cleanly for the next operator.
    """

    def __init__(self, path: Path, *, instance_id: str, hostname: str) -> None:
        self._path = path
        self._instance_id = instance_id
        self._hostname = hostname
        self._fd: int | None = None

    def acquire(self) -> None:
        """Acquire the OS lock and write the informational JSON payload.

        Raises :class:`LockfileError` when the lock is already held by
        another process (or by another :class:`WorkspaceLockfile` in
        this process) or when the lock file cannot be created at all.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LockfileError(
                f"could not create lockfile parent {self._path.parent}: {exc}"
            ) from exc

        try:
            fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            raise LockfileError(f"could not open lockfile {self._path}: {exc}") from exc

        try:
            self._os_lock(fd)
        except (BlockingIOError, PermissionError, OSError) as exc:
            existing = self._read_payload_safely()
            os.close(fd)
            raise LockfileError(
                f"another ralph already running on this workspace: {existing or '<no payload>'}"
            ) from exc

        self._fd = fd

        payload: dict[str, Any] = {
            "instance_id": self._instance_id,
            "hostname": self._hostname,
            "pid": os.getpid(),
            "started_at": datetime.now(UTC).isoformat(),
        }
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, 0)
            os.write(fd, json.dumps(payload).encode("utf-8"))
        except OSError as exc:
            # Payload write failed — release the lock so a retry can
            # succeed rather than leaving us holding it silently.
            self._release_fd(fd)
            self._fd = None
            raise LockfileError(f"could not write lockfile payload to {self._path}: {exc}") from exc

    def release(self) -> None:
        """Release the OS lock and close the file descriptor.

        No-op when the lock was never acquired (or has already been
        released)."""
        if self._fd is None:
            return
        self._release_fd(self._fd)
        self._fd = None

    @staticmethod
    def _os_lock(fd: int) -> None:
        if sys.platform == "win32":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _os_unlock(fd: int) -> None:
        if sys.platform == "win32":
            # Best-effort: closing the fd also drops the lock on
            # Windows. Suppress so release() stays no-throw.
            with contextlib.suppress(OSError):
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)

    @classmethod
    def _release_fd(cls, fd: int) -> None:
        try:
            cls._os_unlock(fd)
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    def _read_payload_safely(self) -> str | None:
        try:
            return self._path.read_text(encoding="utf-8")
        except OSError:
            return None
