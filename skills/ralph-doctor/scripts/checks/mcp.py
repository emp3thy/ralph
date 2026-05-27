"""``mcp`` check for ``ralph-doctor``.

Walks ``mcpServers`` from settings.json AND any sibling ``.mcp.json``.
Flags any server whose ``command``, ``args``, or ``env`` contains an
OAuth-style indicator (``oauth``, ``--auth``, ``--login``, ``BROWSER``).
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

OAUTH_INDICATORS: tuple[str, ...] = (
    "oauth",
    "OAuth",
    "OAUTH",
    "--auth",
    "--login",
    "BROWSER",
)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _extract_mcp_servers(
    settings: dict[str, Any] | None,
    mcp_file: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for source in (mcp_file, settings):
        if source is None:
            continue
        servers = source.get("mcpServers")
        if not isinstance(servers, dict):
            servers = source.get("mcp_servers")
        if not isinstance(servers, dict):
            continue
        for name, value in servers.items():
            if isinstance(value, dict):
                merged[str(name)] = value
    return merged


def _server_uses_oauth(server: dict[str, Any]) -> tuple[bool, str]:
    parts: list[str] = []
    command = server.get("command")
    if isinstance(command, str):
        parts.append(command)
    args = server.get("args")
    if isinstance(args, list):
        for item in args:
            parts.append(str(item))
    env = server.get("env")
    if isinstance(env, dict):
        for k, v in env.items():
            parts.append(f"{k}={v}")
    haystack = " ".join(parts)
    for needle in OAUTH_INDICATORS:
        if needle in haystack:
            return True, needle
    return False, ""


def check(context: CheckContext) -> CheckResult:
    settings_path = context.settings_path
    mcp_path = settings_path.parent / ".mcp.json"

    settings_data = _read_json(settings_path)
    mcp_data = _read_json(mcp_path)
    servers = _extract_mcp_servers(settings_data, mcp_data)

    if not servers:
        return CheckResult(
            name="mcp",
            severity="error",
            status="pass",
            message=(
                f"no MCP servers configured (checked {settings_path} and {mcp_path})."
            ),
            details={
                "settings_path": str(settings_path),
                "mcp_path": str(mcp_path),
            },
        )

    offenders: dict[str, str] = {}
    for name, server in servers.items():
        matched, needle = _server_uses_oauth(server)
        if matched:
            offenders[name] = needle

    if offenders:
        listing = ", ".join(f"{n} ({needle!r})" for n, needle in offenders.items())
        return CheckResult(
            name="mcp",
            severity="error",
            status="fail",
            message=(
                f"{len(offenders)} MCP server(s) require interactive auth: {listing}."
            ),
            details={"offenders": offenders, "total_servers": len(servers)},
        )

    return CheckResult(
        name="mcp",
        severity="error",
        status="pass",
        message=f"All {len(servers)} MCP server(s) use non-interactive auth.",
        details={"total_servers": len(servers)},
    )
