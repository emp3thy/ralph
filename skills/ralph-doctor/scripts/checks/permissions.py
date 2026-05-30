"""``permissions`` check for ``ralph-doctor``.

Asserts that ``settings.json["permissions"]["allow"]`` covers the seven
tools the executor needs (``Bash``, ``Edit``, ``Write``, ``Read``,
``Grep``, ``Glob``, ``Skill``) and the host-pure ``pr`` skill the
executor stages. An entry covers a tool if it is the global wildcard
``*``, exactly the tool name, or ``Tool(...)`` with any inner pattern.
A skill is covered by ``*``, the bare ``Skill`` key, ``Skill(*)``, or
``Skill(<skill_name>)``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
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
    from dataclasses import dataclass, field
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

REQUIRED_TOOLS: tuple[str, ...] = (
    "Bash",
    "Edit",
    "Write",
    "Read",
    "Grep",
    "Glob",
    "Skill",
)
REQUIRED_SKILLS: tuple[str, ...] = ("pr",)


def _load_allow_list(path: Path) -> list[str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        return None
    allow = permissions.get("allow")
    if not isinstance(allow, list):
        return None
    return [str(entry) for entry in allow]


def _tool_is_covered(tool: str, allow: list[str]) -> bool:
    prefix = f"{tool}("
    for entry in allow:
        if entry == "*" or entry == tool:
            return True
        if entry.startswith(prefix) and entry.endswith(")"):
            return True
    return False


def _skill_is_covered(skill_name: str, allow: list[str]) -> bool:
    targets = {"*", "Skill", "Skill(*)", f"Skill({skill_name})"}
    return any(entry in targets for entry in allow)


def check(context: CheckContext) -> CheckResult:
    allow = _load_allow_list(context.settings_path)
    if allow is None:
        return CheckResult(
            name="permissions",
            severity="error",
            status="fail",
            message=(
                "settings.json is missing permissions.allow (or permissions is not a JSON object)."
            ),
            details={"settings_path": str(context.settings_path)},
        )

    missing = [t for t in REQUIRED_TOOLS if not _tool_is_covered(t, allow)]
    missing_skills = [s for s in REQUIRED_SKILLS if not _skill_is_covered(s, allow)]

    if missing or missing_skills:
        details: dict[str, Any] = {
            "missing": missing,
            "missing_skills": missing_skills,
            "allow": allow,
        }
        return CheckResult(
            name="permissions",
            severity="error",
            status="fail",
            message=(
                f"permissions.allow is missing coverage. "
                f"Missing tools: {missing}; missing skills: {missing_skills}."
            ),
            details=details,
        )

    return CheckResult(
        name="permissions",
        severity="error",
        status="pass",
        message=(
            f"permissions.allow covers all {len(REQUIRED_TOOLS)} required "
            f"tools and {len(REQUIRED_SKILLS)} required skills."
        ),
        details={
            "required_tools": list(REQUIRED_TOOLS),
            "required_skills": list(REQUIRED_SKILLS),
            "allow_entries": len(allow),
        },
    )
