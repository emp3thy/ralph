"""Encoding-safe wrappers around ``subprocess.run`` / ``subprocess.Popen``.

Plain ``text=True`` falls back to ``locale.getpreferredencoding()``,
which is cp1252 on a default Windows host. Any byte that is not valid
cp1252 (e.g. ``0x90`` in the middle of a UTF-8 sequence) raises
``UnicodeDecodeError`` and crashes the calling thread — observed in
production when Claude's stream-json got cut mid-codepoint by a
session-timeout kill (see ``BUG-SUBPROCESS-WINDOWS-ENCODING-AUDIT``).

``run_text`` and ``popen_text`` set ``text=True``, ``encoding="utf-8"``,
and ``errors="replace"`` by default so every child stream is decoded
with the same UTF-8 + U+FFFD-substitution policy. Callers can still
override any of the three explicitly (e.g. ``errors="strict"`` for a
call that genuinely wants to crash on bad bytes), but the common case
is one call.

Use these wrappers instead of ``subprocess.run`` / ``subprocess.Popen``
whenever the child's stdout/stderr is captured as text. A regression
test (``tests/executor/test_subprocess_encoding_audit.py``) fails the
build if a new ``text=True`` call site forgets ``encoding=``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any, cast


def run_text(
    argv: Sequence[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` with UTF-8 + ``errors="replace"`` defaults.

    Any of ``text`` / ``encoding`` / ``errors`` may be overridden by the
    caller; everything else passes through unchanged.
    """
    kwargs.setdefault("text", True)
    # Only inject UTF-8 defaults when text mode is actually on. A caller
    # that explicitly passes ``text=False`` wants binary streams;
    # injecting ``encoding=`` / ``errors=`` on top would either silently
    # flip the child back to text mode (Python ≤ 3.11) or raise
    # ``ValueError`` (Python ≥ 3.12). Same guard applies to
    # ``universal_newlines=False``.
    if kwargs.get("text", True) and kwargs.get("universal_newlines", True):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return cast(
        "subprocess.CompletedProcess[str]",
        subprocess.run(argv, **kwargs),  # noqa: S603
    )


def popen_text(
    argv: Sequence[str],
    **kwargs: Any,
) -> subprocess.Popen[str]:
    """``subprocess.Popen`` with UTF-8 + ``errors="replace"`` defaults.

    Same default-injection contract as :func:`run_text`.
    """
    kwargs.setdefault("text", True)
    # Only inject UTF-8 defaults when text mode is actually on. A caller
    # that explicitly passes ``text=False`` wants binary streams;
    # injecting ``encoding=`` / ``errors=`` on top would either silently
    # flip the child back to text mode (Python ≤ 3.11) or raise
    # ``ValueError`` (Python ≥ 3.12). Same guard applies to
    # ``universal_newlines=False``.
    if kwargs.get("text", True) and kwargs.get("universal_newlines", True):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return cast(
        "subprocess.Popen[str]",
        subprocess.Popen(argv, **kwargs),  # noqa: S603
    )
