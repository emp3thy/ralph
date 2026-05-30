"""``host_staging`` check for ``ralph-doctor``.

Verifies the executor's ``host_select.py`` staging step produced the
``pr`` skill directory matching the active ``RALPH_GIT_HOST``. Reads
the YAML frontmatter of ``<skills_dir>/pr/SKILL.md`` and asserts the
frontmatter ``name:`` value equals ``pr-<host>``. Missing or mismatched
→ fail. This check is host-agnostic and always runs.

Inlines a minimal YAML-frontmatter parser. Claude Code's frontmatter
format is a strict scalar-only subset of YAML and ``host_staging`` only
needs the ``name:`` field, so a tiny parser avoids a PyYAML dependency.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

_checks_pkg = sys.modules.get("ralph_doctor_checks")
if _checks_pkg is None:
    _spec = importlib.util.spec_from_file_location(
        "ralph_doctor_checks", Path(__file__).resolve().parent / "__init__.py"
    )
    if _spec is None or _spec.loader is None:
        raise RuntimeError("could not load ralph_doctor_checks contract module")
    _checks_pkg = importlib.util.module_from_spec(_spec)
    sys.modules["ralph_doctor_checks"] = _checks_pkg
    _spec.loader.exec_module(_checks_pkg)

if TYPE_CHECKING:
    from typing import Literal

    Severity = Literal["error", "warn"]
    Status = Literal["pass", "fail", "skipped"]

    @dataclass(frozen=True)
    class CheckContext:
        settings_path: Path
        skills_dir: Path
        strict: bool = False
        git_host: str = ""
        extra: dict[str, str] = field(default_factory=dict)

    @dataclass(frozen=True)
    class CheckResult:
        name: str
        severity: Severity
        status: Status
        message: str
        details: dict[str, object] = field(default_factory=dict)
else:
    CheckContext = _checks_pkg.CheckContext
    CheckResult = _checks_pkg.CheckResult


REQUIRED_STAGED_SKILLS: tuple[str, ...] = ("pr",)
KNOWN_HOSTS: tuple[str, ...] = ("github", "ado")

_NAME_LINE_RE = re.compile(r"^name\s*:\s*(.+?)\s*$")


def _parse_frontmatter_name(path: Path) -> tuple[str | None, str]:
    """Return ``(name, error_reason)``. On success, ``error_reason`` is empty."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, f"could not read file: {exc}"

    if text.startswith("﻿"):
        text = text[1:]

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "missing opening frontmatter fence"

    fm_lines: list[str] = []
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        fm_lines.append(line)
    if not closed:
        return None, "missing closing frontmatter fence"

    for line in fm_lines:
        match = _NAME_LINE_RE.match(line)
        if match is None:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value, ""

    return None, "no 'name:' field in frontmatter"


def check(context: CheckContext) -> CheckResult:
    git_host = context.git_host
    if git_host not in KNOWN_HOSTS:
        return CheckResult(
            name="host_staging",
            severity="error",
            status="fail",
            message=(
                f"host_staging cannot run: RALPH_GIT_HOST={git_host!r} is "
                f"not a known host (expected one of {list(KNOWN_HOSTS)})."
            ),
            details={"git_host": git_host},
        )

    offences: list[dict[str, Any]] = []
    for skill_name in REQUIRED_STAGED_SKILLS:
        skill_md = context.skills_dir / skill_name / "SKILL.md"
        if not skill_md.is_file():
            offences.append(
                {
                    "skill": skill_name,
                    "path": str(skill_md),
                    "reason": f"{skill_name}/SKILL.md not found at staged path",
                }
            )
            continue

        parsed, reason = _parse_frontmatter_name(skill_md)
        if parsed is None:
            offences.append(
                {
                    "skill": skill_name,
                    "path": str(skill_md),
                    "reason": f"could not parse frontmatter name: {reason}",
                }
            )
            continue

        expected = f"{skill_name}-{git_host}"
        if parsed != expected:
            offences.append(
                {
                    "skill": skill_name,
                    "path": str(skill_md),
                    "frontmatter_name": parsed,
                    "expected_name": expected,
                    "reason": "frontmatter name does not match RALPH_GIT_HOST",
                }
            )

    if offences:
        return CheckResult(
            name="host_staging",
            severity="error",
            status="fail",
            message=(
                f"Staged skills do not match RALPH_GIT_HOST={git_host}. "
                f"Likely cause: the pod was built for a different host and "
                f"RALPH_GIT_HOST was changed at runtime, OR host_select.py "
                f"did not run. Offending entries: {offences}"
            ),
            details={
                "offences": offences,
                "git_host": git_host,
                "skills_dir": str(context.skills_dir),
            },
        )

    return CheckResult(
        name="host_staging",
        severity="error",
        status="pass",
        message=(f"Staged 'pr' skill matches RALPH_GIT_HOST={git_host} (pr-{git_host})."),
        details={
            "checked": list(REQUIRED_STAGED_SKILLS),
            "git_host": git_host,
        },
    )
