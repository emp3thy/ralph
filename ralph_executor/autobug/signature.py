"""Signature hashing for Python and subprocess crashes."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from types import TracebackType

_USER_ROOT = "ralph_executor"


@dataclass(frozen=True)
class _Frame:
    filename: str
    name: str
    lineno: int


_SENTINEL = _Frame(filename="<none>", name="<none>", lineno=0)


def _two_deepest_user_frames(tb: TracebackType | None) -> tuple[_Frame, _Frame]:
    """Walk traceback; return the two deepest frames under ralph_executor/."""
    frames: list[_Frame] = []
    cur = tb
    while cur is not None:
        f = cur.tb_frame
        filename = f.f_code.co_filename.replace("\\", "/")
        if _USER_ROOT in filename:
            frames.append(_Frame(filename=filename, name=f.f_code.co_name, lineno=cur.tb_lineno))
        cur = cur.tb_next
    if not frames:
        cur = tb
        while cur is not None:
            f = cur.tb_frame
            frames.append(
                _Frame(
                    filename=f.f_code.co_filename.replace("\\", "/"),
                    name=f.f_code.co_name,
                    lineno=cur.tb_lineno,
                )
            )
            cur = cur.tb_next
        if not frames:
            return _SENTINEL, _SENTINEL
        return frames[-1], _SENTINEL
    if len(frames) == 1:
        return frames[-1], _SENTINEL
    return frames[-1], frames[-2]


def python_crash_signature(exc: BaseException) -> str:
    throw, caller = _two_deepest_user_frames(exc.__traceback__)
    payload = (
        f"{type(exc).__qualname__}:"
        f"{throw.filename}:{throw.name}:{throw.lineno}|"
        f"{caller.filename}:{caller.name}:{caller.lineno}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?")
_EPOCH_RE = re.compile(r"\b\d{10,}\b")
_PATH_RE = re.compile(r"(?:[A-Z]:\\|/)[^\s:]+")
_BRANCH_RE = re.compile(r"ralph/[A-Z0-9][A-Z0-9_-]*")
_PBI_RE = re.compile(r"\b(WI-\d+|autobug-[a-f0-9]+-\d+)\b")
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_ADDR_RE = re.compile(r"0x[0-9a-f]+")
_LINENO_RE = re.compile(r":line \d+|at line \d+")


def _normalise(line: str) -> str:
    """Apply regex substitutions in spec-order (longest/most-specific first)."""
    out = _UUID_RE.sub("<UUID>", line)
    out = _TS_RE.sub("<TS>", out)
    out = _EPOCH_RE.sub("<EPOCH>", out)
    out = _PATH_RE.sub("<PATH>", out)
    out = _BRANCH_RE.sub("<BRANCH>", out)
    out = _PBI_RE.sub("<PBI>", out)
    out = _SHA_RE.sub("<SHA>", out)
    out = _ADDR_RE.sub("<ADDR>", out)
    out = _LINENO_RE.sub(":line <N>", out)
    return out


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def subprocess_crash_signature(exit_code: int, stderr: str) -> str:
    last_line = _last_nonempty_line(stderr)
    normalised = _normalise(last_line)
    payload = f"{exit_code}:{normalised}"
    return hashlib.sha256(payload.encode()).hexdigest()
