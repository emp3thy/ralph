"""``ralph-doctor`` skill entry point."""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

_HERE = Path(__file__).resolve().parent
_CHECKS_DIR = _HERE / "checks"

_checks_init = importlib.util.spec_from_file_location(
    "ralph_doctor_checks", _CHECKS_DIR / "__init__.py"
)
if _checks_init is None or _checks_init.loader is None:
    raise RuntimeError(f"could not load checks contract from {_CHECKS_DIR / '__init__.py'}")
_checks_pkg = importlib.util.module_from_spec(_checks_init)
# Register before exec so @dataclass can resolve string annotations
# (from __future__ import annotations) via sys.modules[cls.__module__].
sys.modules[_checks_init.name] = _checks_pkg
_checks_init.loader.exec_module(_checks_pkg)

if TYPE_CHECKING:
    # Mirror the schema in ``checks/__init__.py`` for mypy. The directory
    # name ``ralph-doctor`` contains a hyphen so the real contract module
    # is loaded via importlib (below) — mypy cannot follow that, so the
    # static type information lives here.
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

REGISTRY: tuple[str, ...] = _checks_pkg.REGISTRY
HOST_AUTH_CHECKS: dict[str, str] = _checks_pkg.HOST_AUTH_CHECKS

_LOG = logging.getLogger("ralph_doctor")


def _configure_logging() -> None:
    level_name = os.environ.get("RALPH_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOG.handlers.clear()
    _LOG.addHandler(handler)
    _LOG.setLevel(level)


class _CliError(RuntimeError):
    """Raised internally on invalid CLI usage; runner converts to exit 2."""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-doctor",
        description="Preflight checks for a ralph-safe environment.",
    )
    parser.add_argument(
        "--settings",
        default=str(Path.home() / ".claude" / "settings.json"),
        help="Path to settings.json (default: ~/.claude/settings.json).",
    )
    parser.add_argument(
        "--skills-dir",
        default=str(Path.home() / ".claude" / "skills"),
        help="Path to installed skills (default: ~/.claude/skills).",
    )
    parser.add_argument(
        "--skip",
        default="",
        help="Comma-separated check names to skip.",
    )
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated check names to run; everything else skipped.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Suppress the human summary on stderr; emit JSON only.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warn-severity failures as errors (exit 1).",
    )
    return parser.parse_args(argv)


def _resolve_host_dispatch() -> tuple[str, str, frozenset[str]]:
    """Read RALPH_GIT_HOST and return (host, active_auth_check, skipped_auth_checks).

    Raises ``_CliError`` (-> exit 2) if RALPH_GIT_HOST is unset or unknown.
    """
    raw = os.environ.get("RALPH_GIT_HOST", "").strip().lower()
    if not raw:
        raise _CliError(
            "RALPH_GIT_HOST is not set. Set it to 'github' or 'ado'. "
            "See docs/superpowers/plans/2026-05-24-00-orchestrator.md "
            "(Environment variables table)."
        )
    if raw not in HOST_AUTH_CHECKS:
        raise _CliError(
            f"RALPH_GIT_HOST={raw!r} is not a known value (expected "
            f"one of {sorted(HOST_AUTH_CHECKS)}). See the orchestrator "
            "env-var table."
        )
    active = HOST_AUTH_CHECKS[raw]
    skipped = frozenset(name for host, name in HOST_AUTH_CHECKS.items() if host != raw)
    return raw, active, skipped


def _determine_active_checks(
    args: argparse.Namespace, host_skipped: frozenset[str]
) -> tuple[set[str], set[str]]:
    skip_set = {s.strip() for s in args.skip.split(",") if s.strip()}
    only_set = {s.strip() for s in args.only.split(",") if s.strip()}
    if skip_set and only_set:
        raise _CliError("--skip and --only are mutually exclusive")
    unknown = (skip_set | only_set) - set(REGISTRY)
    if unknown:
        raise _CliError(f"unknown check name(s): {sorted(unknown)} (valid: {list(REGISTRY)})")
    if only_set:
        to_run = only_set - host_skipped
        to_skip = (set(REGISTRY) - only_set) | host_skipped
        return to_run, to_skip
    to_skip = skip_set | host_skipped
    return set(REGISTRY) - to_skip, to_skip


def _load_check_module(name: str) -> ModuleType:
    path = _CHECKS_DIR / f"{name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"check module {name} not found at {path}")
    spec = importlib.util.spec_from_file_location(f"ralph_doctor_check_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not create spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_context(args: argparse.Namespace, git_host: str) -> CheckContext:
    return CheckContext(
        settings_path=Path(args.settings).expanduser().resolve(),
        skills_dir=Path(args.skills_dir).expanduser().resolve(),
        strict=bool(args.strict),
        git_host=git_host,
    )


def _skipped_result(name: str, reason: str) -> CheckResult:
    return CheckResult(
        name=name,
        severity="error",
        status="skipped",
        message=reason,
        details={},
    )


def _json_safe(value: object) -> bool:
    try:
        json.dumps(value)
    except TypeError:
        return False
    return True


def _result_to_dict(result: CheckResult) -> dict[str, Any]:
    payload = asdict(result)
    details = payload.get("details") or {}
    payload["details"] = {str(k): (v if _json_safe(v) else str(v)) for k, v in details.items()}
    return payload


def _summarise(results: list[CheckResult]) -> dict[str, int]:
    return {
        "errors": sum(1 for r in results if r.status == "fail" and r.severity == "error"),
        "warns": sum(1 for r in results if r.status == "fail" and r.severity == "warn"),
        "passes": sum(1 for r in results if r.status == "pass"),
        "skips": sum(1 for r in results if r.status == "skipped"),
    }


def _compute_exit_code(results: list[CheckResult], strict: bool) -> int:
    for r in results:
        if r.status != "fail":
            continue
        if r.severity == "error":
            return 1
        if strict and r.severity == "warn":
            return 1
    return 0


def _emit(payload: dict[str, Any], json_only: bool) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=False))
    sys.stdout.write("\n")
    if json_only:
        return
    header = (
        f"ralph-doctor: {payload['summary']['passes']} pass, "
        f"{payload['summary']['errors']} error, "
        f"{payload['summary']['warns']} warn, "
        f"{payload['summary']['skips']} skipped"
    )
    lines = [header]
    for entry in payload["checks"]:
        marker = {"pass": "  ok ", "fail": "FAIL ", "skipped": "skip "}.get(
            entry["status"], "  ?  "
        )
        lines.append(f"  {marker} {entry['name']:<14} [{entry['severity']:<5}] {entry['message']}")
    sys.stderr.write("\n".join(lines))
    sys.stderr.write("\n")


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise _CliError(f"settings file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _CliError(f"could not read settings file {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _CliError(f"settings file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _CliError(f"settings file {path} must be a JSON object at top level")
    return data


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    try:
        args = _parse_args(argv if argv is not None else sys.argv[1:])
        git_host, _active_auth, host_skipped = _resolve_host_dispatch()
        to_run, to_skip = _determine_active_checks(args, host_skipped)
        if any(name in to_run for name in ("permissions", "hooks", "mcp")):
            _load_settings(Path(args.settings).expanduser())
    except _CliError as exc:
        sys.stderr.write(f"ralph-doctor: {exc}\n")
        return 2

    context = _make_context(args, git_host)
    results: list[CheckResult] = []
    for name in REGISTRY:
        if name in host_skipped:
            results.append(
                _skipped_result(
                    name,
                    f"not the active host check for RALPH_GIT_HOST={git_host}",
                )
            )
            continue
        if name in to_skip:
            results.append(_skipped_result(name, "skipped via --skip / --only"))
            continue
        try:
            module = _load_check_module(name)
            result = module.check(context)
        except Exception as exc:  # pylint: disable=broad-except
            _LOG.exception("check %s raised", name)
            result = CheckResult(
                name=name,
                severity="error",
                status="fail",
                message=f"check raised an exception: {exc!r}",
                details={"exception": str(exc)},
            )
        if not isinstance(result, CheckResult):
            result = CheckResult(
                name=name,
                severity="error",
                status="fail",
                message=(
                    f"check module {name} returned non-CheckResult "
                    f"value (type={type(result).__name__})"
                ),
            )
        results.append(result)

    exit_code = _compute_exit_code(results, strict=context.strict)
    summary = _summarise(results)
    payload = {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "summary": summary,
        "checks": [_result_to_dict(r) for r in results],
    }
    _emit(payload, json_only=bool(args.json))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
