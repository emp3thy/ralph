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

import ast
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


def _iter_py_files() -> list[Path]:
    out: list[Path] = []
    for root in _SCAN_DIRS:
        if not root.is_dir():
            continue
        out.extend(p for p in root.rglob("*.py") if p.resolve() not in _EXCLUDED)
    return out


def _is_subprocess_run_or_popen(call: ast.Call) -> bool:
    """Return True for ``subprocess.run(...)`` / ``subprocess.Popen(...)`` calls.

    Other call shapes (``Popen(...)`` without the ``subprocess.`` prefix,
    aliased imports like ``from subprocess import run``) are intentionally
    NOT matched — the audit guards the explicit module-qualified pattern
    and ``subprocess_utils`` provides the wrapped helpers for everything
    else.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in {"run", "Popen"}:
        return False
    return isinstance(func.value, ast.Name) and func.value.id == "subprocess"


def _kwarg_is_true(kw: ast.keyword) -> bool:
    """Return True if ``kw.value`` evaluates to literal ``True``.

    Only matches ``ast.Constant(True)``; an expression like ``text=flag``
    is treated as text-mode-off for the audit, matching the regex-era
    behaviour.
    """
    return isinstance(kw.value, ast.Constant) and kw.value.value is True


def _violations_in(path: Path) -> list[tuple[int, str]]:
    """Walk ``path``'s AST and flag text-mode subprocess calls with no encoding.

    AST-based (replaces the previous depth-1 regex) so the audit is
    robust against arbitrarily-nested parenthesised expressions in the
    argument list (e.g. ``cwd=str(Path(repo))``).
    """
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []
    bad: list[tuple[int, str]] = []
    src_lines = src.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_run_or_popen(node):
            continue
        kwargs = {kw.arg: kw for kw in node.keywords if kw.arg is not None}
        text_on = (
            "text" in kwargs
            and _kwarg_is_true(kwargs["text"])
            or "universal_newlines" in kwargs
            and _kwarg_is_true(kwargs["universal_newlines"])
        )
        if not text_on:
            continue
        if "encoding" in kwargs:
            continue
        snippet = src_lines[node.lineno - 1].strip() if 0 < node.lineno <= len(src_lines) else ""
        bad.append((node.lineno, snippet))
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
