"""``hooks`` check for ``ralph-doctor``.

Scans every ``command`` string in ``settings.json["hooks"]`` for
interactive indicators (the interactive-prompt needle, ``input(``,
``read -p``, ``Read-Host``). Matches in synchronous hooks fail at
``error`` severity;
matches in hooks marked ``async: true`` cannot block ``claude`` directly,
so they downgrade to ``warn``.
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

INTERACTIVE_INDICATORS: tuple[str, ...] = (
    "AskUserQuestion",  # noqa: ralph-doctor
    "input(",
    "read -p",
    "Read-Host",
)


def _iter_hook_commands(
    hooks_section: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Flatten ``{event: [{matcher, hooks: [{command, async, ...}]}]}`` to pairs."""
    pairs: list[tuple[str, dict[str, Any]]] = []
    for event, entries in hooks_section.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            inner = entry.get("hooks")
            if not isinstance(inner, list):
                continue
            for hook in inner:
                if isinstance(hook, dict):
                    pairs.append((str(event), hook))
    return pairs


def _matches_indicator(command: str) -> tuple[bool, str]:
    for needle in INTERACTIVE_INDICATORS:
        if needle in command:
            return True, needle
    return False, ""


def check(context: CheckContext) -> CheckResult:
    try:
        text = context.settings_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(
            name="hooks",
            severity="error",
            status="fail",
            message=f"could not read settings.json: {exc!r}",
            details={"settings_path": str(context.settings_path)},
        )

    if not isinstance(data, dict):
        return CheckResult(
            name="hooks",
            severity="error",
            status="fail",
            message="settings.json must be a JSON object at top level.",
            details={"settings_path": str(context.settings_path)},
        )

    hooks_section = data.get("hooks", {})
    if not isinstance(hooks_section, dict):
        return CheckResult(
            name="hooks",
            severity="error",
            status="fail",
            message='settings.json["hooks"] is not a JSON object.',
            details={"settings_path": str(context.settings_path)},
        )

    flattened = _iter_hook_commands(hooks_section)

    blocking_offences: list[dict[str, Any]] = []
    async_offences: list[dict[str, Any]] = []

    for event, hook in flattened:
        command = hook.get("command")
        if not isinstance(command, str):
            continue
        matched, needle = _matches_indicator(command)
        if not matched:
            continue
        record: dict[str, Any] = {
            "event": event,
            "needle": needle,
            "command": command,
        }
        if hook.get("async") is True:
            async_offences.append(record)
        else:
            blocking_offences.append(record)

    if blocking_offences:
        needles = sorted({o["needle"] for o in blocking_offences})
        return CheckResult(
            name="hooks",
            severity="error",
            status="fail",
            message=(
                f"{len(blocking_offences)} blocking hook(s) call interactive primitives: {needles}."
            ),
            details={"blocking": blocking_offences, "async": async_offences},
        )

    if async_offences:
        needles = sorted({o["needle"] for o in async_offences})
        return CheckResult(
            name="hooks",
            severity="warn",
            status="fail",
            message=(
                f"{len(async_offences)} async hook(s) call interactive "
                f"primitives: {needles}. Async hooks cannot block claude but "
                "remain a smell."
            ),
            details={"async": async_offences},
        )

    return CheckResult(
        name="hooks",
        severity="error",
        status="pass",
        message="No hook calls AskUserQuestion or blocks on stdin.",  # noqa: ralph-doctor
        details={"hooks_checked": len(flattened)},
    )
