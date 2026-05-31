"""OS-level exclusive lockfile for the per-instance workspace.

Acquired at executor startup; released by the OS on process exit even if
``release()`` is never called. Lockfile lives at
``<workspace_root>/queue-<instance-id>/.ralph.lock`` — outside the .git
tree, never committed.

POSIX uses ``fcntl.flock(LOCK_EX | LOCK_NB)`` (advisory, whole-file).
Windows uses ``msvcrt.locking(LK_NBLCK, 1)`` which locks N bytes from the
current file position — seek to byte 0 first then lock 1 byte. The seek
is critical; without it the lock targets an undefined offset.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO


class LockHeldError(RuntimeError):
    """Raised when the lockfile is already held by another process."""


@dataclass
class WorkspaceLock:
    path: Path
    instance_id: str
    _fh: IO[bytes] | None = field(default=None, init=False, repr=False)

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")  # noqa: SIM115 — closed in release()
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise LockHeldError(f"another process already holds {self.path}") from exc
            else:
                import fcntl

                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise LockHeldError(f"another process already holds {self.path}") from exc
        except BaseException:
            fh.close()
            raise

        self._fh = fh
        payload = {
            "instance_id": self.instance_id,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": datetime.now(tz=UTC).isoformat(),
        }
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write((json.dumps(payload, indent=2) + "\n").encode("utf-8"))
        self._fh.flush()

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> WorkspaceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()
