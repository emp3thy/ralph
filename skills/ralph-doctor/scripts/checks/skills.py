"""``skills`` check for ``ralph-doctor``.

Heuristic substring scan over every installed skill's ``SKILL.md`` body
and ``scripts/**/*.py`` files. Any occurrence of ``AskUserQuestion``
fails the check at ``error`` severity.

Markdown regions wrapped with ``<!-- ralph-doctor: ignore -->`` ...
``<!-- /ralph-doctor: ignore -->`` are dropped before the search.
Python lines containing ``# noqa: ralph-doctor`` are dropped before the
search. These escape hatches let a skill author document the needle
(e.g. in a SKILL.md changelog entry) without tripping the doctor.

If the skills directory does not exist, the check passes with a note —
this is the laptop-test case where the configured skills dir does not
match the local install layout.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

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

NEEDLE = "AskUserQuestion"
IGNORE_MARK_OPEN = "<!-- ralph-doctor: ignore -->"
IGNORE_MARK_CLOSE = "<!-- /ralph-doctor: ignore -->"
PY_IGNORE = "# noqa: ralph-doctor"


def _strip_ignored_markdown_regions(text: str) -> str:
    out: list[str] = []
    cursor = 0
    while cursor < len(text):
        open_at = text.find(IGNORE_MARK_OPEN, cursor)
        if open_at == -1:
            out.append(text[cursor:])
            break
        out.append(text[cursor:open_at])
        close_at = text.find(IGNORE_MARK_CLOSE, open_at + len(IGNORE_MARK_OPEN))
        if close_at == -1:
            break
        cursor = close_at + len(IGNORE_MARK_CLOSE)
    return "".join(out)


def _python_lines_without_noqa(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if PY_IGNORE not in line)


def _scan_skill(skill_dir: Path) -> list[str]:
    offending: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_file():
        body = skill_md.read_text(encoding="utf-8", errors="replace")
        stripped = _strip_ignored_markdown_regions(body)
        if NEEDLE in stripped:
            offending.append(str(skill_md))
    scripts_dir = skill_dir / "scripts"
    if scripts_dir.is_dir():
        for py_path in scripts_dir.rglob("*.py"):
            if not py_path.is_file():
                continue
            body = py_path.read_text(encoding="utf-8", errors="replace")
            stripped = _python_lines_without_noqa(body)
            if NEEDLE in stripped:
                offending.append(str(py_path))
    return offending


def _offending_skill_name(path_str: str) -> str:
    # Walk ancestors so files nested below `scripts/` (e.g.
    # `scripts/utils/helper.py`, picked up by `scripts_dir.rglob("*.py")`)
    # still resolve to the owning skill name. Checking only the direct
    # parent would return "utils" instead of the skill dir.
    path = Path(path_str)
    for ancestor in path.parents:
        if ancestor.name == "scripts":
            return ancestor.parent.name
    return path.parent.name


def check(context: CheckContext) -> CheckResult:
    skills_dir = context.skills_dir
    if not skills_dir.is_dir():
        return CheckResult(
            name="skills",
            severity="error",
            status="pass",
            message=f"no skills directory at {skills_dir}; nothing to scan.",
            details={"skills_dir": str(skills_dir)},
        )

    offences: list[str] = []
    skills_seen = 0
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        skills_seen += 1
        offences.extend(_scan_skill(entry))

    if offences:
        offences = sorted(set(offences))
        skill_names = sorted({_offending_skill_name(p) for p in offences})
        return CheckResult(
            name="skills",
            severity="error",
            status="fail",
            message=(
                f"{len(offences)} skill file(s) call AskUserQuestion in their "
                f"main path; skills: {skill_names}."
            ),
            details={"offending_files": offences, "skills": skill_names},
        )

    return CheckResult(
        name="skills",
        severity="error",
        status="pass",
        message=(
            f"No installed skill calls AskUserQuestion in its main path "
            f"(scanned {skills_seen} skill(s))."
        ),
        details={"skills_scanned": skills_seen},
    )
