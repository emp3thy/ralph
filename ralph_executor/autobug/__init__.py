"""Autobug — capture executor + Claude subprocess crashes as bug PBIs.

Public API:
  - detect_python_crash(exc, ctx, *, target_repo, severity)
  - detect_subprocess_crash(exit_code, stderr, command, ctx, *, target_repo, severity)
  - Context  (frozen dataclass passed by callers)
"""

from ralph_executor.autobug.detect import detect_python_crash, detect_subprocess_crash
from ralph_executor.autobug.types import Context, DedupResult

__all__ = [
    "Context",
    "DedupResult",
    "detect_python_crash",
    "detect_subprocess_crash",
]
