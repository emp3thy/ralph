"""Repo-wide guard: every ``subprocess.run`` / ``subprocess.Popen`` call
with ``text=True`` must also pin ``encoding=`` (or go through the
``ralph_executor.subprocess_utils`` wrappers).

Without ``encoding=``, Python falls back to ``locale.getpreferredencoding()``
— cp1252 on a stock Windows host — and any non-cp1252 byte in the child's
stdout/stderr crashes the calling thread with ``UnicodeDecodeError``.

This test scans the executor + tests sources for raw ``subprocess.run`` /
``subprocess.Popen`` calls (multi-line tolerant) and fails if any of them
sets ``text=True`` (or ``universal_newlines=True``) without an explicit
``encoding=``. Use ``run_text`` / ``popen_text`` from
``ralph_executor.subprocess_utils`` instead — they bake the policy in.

See ``BUG-SUBPROCESS-WINDOWS-ENCODING-AUDIT`` for the full backstory.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories scanned. Test fixtures live under ``tests/`` and are
# themselves real subprocess calls — they need the same hygiene.
_SCAN_DIRS = (
    _REPO_ROOT / "ralph_executor",
    _REPO_ROOT / "tests",
    _REPO_ROOT / "scripts",
)

# Filenames the guard does not police. ``subprocess_utils.py`` IS the
# wrapper; its single ``subprocess.run`` / ``subprocess.Popen`` call is
# the centralised, audited entry point and intentionally takes
# ``**kwargs`` rather than pinning ``encoding`` directly. This file is
# also excluded so the regex tokens in its own source don't false-trip
# the guard.
_EXCLUDED = frozenset(
    {
        (_REPO_ROOT / "ralph_executor" / "subprocess_utils.py").resolve(),
        Path(__file__).resolve(),
    }
)

# Multi-line ``subprocess.run(...)`` / ``subprocess.Popen(...)`` capture.
# The ``[^()]*?`` body is conservative — it does NOT cross another
# parenthesis, which would let nested calls slip through. In practice
# every audited call site fits the pattern.
_CALL_RE = re.compile(
    r"subprocess\.(?:run|Popen)\s*\(((?:[^()]|\([^()]*\))*)\)",
    re.DOTALL,
)


def _iter_py_files() -> list[Path]:
    out: list[Path] = []
    for root in _SCAN_DIRS:
        if not root.is_dir():
            continue
        out.extend(p for p in root.rglob("*.py") if p.resolve() not in _EXCLUDED)
    return out


def _violations_in(path: Path) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8")
    bad: list[tuple[int, str]] = []
    for match in _CALL_RE.finditer(src):
        body = match.group(1)
        # Only flag calls that turn on text mode. A purely-bytes call
        # (``text=False`` or unset) is encoding-agnostic and not a
        # vulnerability surface.
        if "text=True" not in body and "universal_newlines=True" not in body:
            continue
        if re.search(r"\bencoding\s*=", body):
            continue
        # Report the call-site line for the failure message.
        line = src.count("\n", 0, match.start()) + 1
        bad.append((line, match.group(0).splitlines()[0]))
    return bad


@pytest.mark.parametrize("path", _iter_py_files(), ids=lambda p: str(p.relative_to(_REPO_ROOT)))
def test_no_unencoded_text_mode_subprocess_call(path: Path) -> None:
    bad = _violations_in(path)
    assert not bad, (
        f"{path.relative_to(_REPO_ROOT)} has subprocess.run/Popen calls with "
        f"text=True but no encoding=. Use ralph_executor.subprocess_utils.run_text / "
        f"popen_text instead, or pass encoding='utf-8', errors='replace' inline.\n"
        + "\n".join(f"  line {ln}: {snippet}" for ln, snippet in bad)
    )
