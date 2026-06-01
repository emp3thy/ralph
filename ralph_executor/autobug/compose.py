"""Defensive composer for BUG.md / REPRODUCE.md.

Every section runs through ``_safe``: section failures log a structured
warning and substitute a fallback string but never propagate. This
guarantees the autobug emit never crashes the original-crash handler.
"""

from __future__ import annotations

import logging
import platform
import sys
import traceback
from collections.abc import Callable

from ralph_executor.autobug.types import Context

log = logging.getLogger(__name__)


def _safe[T](fn: Callable[[], T], fallback: T) -> T:
    try:
        return fn()
    except Exception as exc:
        log.warning(
            "autobug.compose section failed: %s",
            exc,
            extra={"section_failed": True},
        )
        return fallback


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body}"


def build_frontmatter(
    *,
    pbi_id: str,
    signature: str,
    trigger_kind: str,
    severity: str,
    ctx: Context,
    target_repo: str,
    regression_of: str | None = None,
) -> str:
    """Compose the YAML frontmatter block for an autobug BUG.md.

    Load-bearing fields (id, type, signature, target_repo) cannot fall
    back — caller (detect.py) aborts emission earlier if they're missing.
    """
    now_iso = ctx.now.replace(microsecond=0).isoformat()
    lines = [
        "---",
        f"id: {pbi_id}",
        "type: bug",
        f"severity: {severity}",
        "status: inbox",
        "attempts: 0",
        f"created_at: {now_iso}",
        f"updated_at: {now_iso}",
        f"target_repo: {target_repo}",
        f"signature: {signature}",
        f"trigger_kind: {trigger_kind}",
        "occurrences: 1",
        f"first_seen: {now_iso}",
        f"last_seen: {now_iso}",
        f"regression_of: {regression_of if regression_of else 'null'}",
        f"triggering_pbi: {ctx.triggering_pbi_id if ctx.triggering_pbi_id else 'null'}",
        f"ralph_sha: {ctx.ralph_sha}",
        "---",
        "",
    ]
    return "\n".join(lines)


def _format_traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _env_snapshot(ctx: Context) -> str:
    env = ctx.env or {}
    relevant_keys = sorted(k for k in env if k.startswith("RALPH_") or k in ("GH_OWNER",))
    rows = [
        f"- {k}: <redacted>" if "TOKEN" in k or "KEY" in k else f"- {k}: {env[k]}"
        for k in relevant_keys
    ]
    return (
        f"- OS: {platform.system()}\n"
        f"- Python: {sys.version.split()[0]}\n"
        f"- Ralph SHA: {ctx.ralph_sha}\n"
        + ("\n".join(rows) if rows else "- (no RALPH_/GH_OWNER env keys)")
    )


def _pbi_ref(ctx: Context) -> str:
    if ctx.triggering_pbi_id:
        return f"Triggering PBI: {ctx.triggering_pbi_id}"
    return "Triggering PBI: <none — startup / pre-claim crash>"


def _short_title(exc: BaseException) -> str:
    msg = str(exc).splitlines()[0] if str(exc) else "<no message>"
    return f"{type(exc).__qualname__}: {msg[:80]}"


def build_bug_md(exc: BaseException, ctx: Context) -> str:
    """Compose BUG.md body. Every section is _safe-wrapped; never raises."""
    parts: list[str] = []
    parts.append(f"# autobug — {_safe(lambda: _short_title(exc), 'unknown crash')}")
    parts.append(
        _section(
            "Stacktrace",
            _safe(
                lambda: f"```\n{_format_traceback(exc)}\n```",
                "<traceback unavailable>",
            ),
        )
    )
    parts.append(
        _section("Environment", _safe(lambda: _env_snapshot(ctx), "<env unavailable>"))
    )
    parts.append(
        _section("Triggering PBI", _safe(lambda: _pbi_ref(ctx), "<no PBI context>"))
    )
    assert parts, "build_bug_md produced no sections — refusing to emit empty BUG.md"
    return "\n\n".join(parts)


_REPRODUCE_HEADER = (
    "> AUTOBUG NOTE: This bug was auto-captured at crash time. The content\n"
    "> below is what we observed, NOT a confirmed reproduction recipe. Your\n"
    "> iteration's job is to convert these observations into runnable\n"
    "> reproduction commands and append them to this file. This is a\n"
    "> starting point, not a recipe. If after investigation the crash is\n"
    "> genuinely non-deterministic (race condition, OOM under load, network\n"
    "> blip), write STUCK.md per the bug workflow's contradictory-evidence\n"
    "> trigger — do not stack speculative fixes."
)


def build_reproduce_md(
    pbi_id: str,
    *,
    trigger_kind: str,
    exc: BaseException | None,
    stderr: str | None,
    exit_code: int | None,
    ctx: Context,
) -> str:
    parts: list[str] = []
    parts.append(f"# REPRODUCE — {pbi_id}")
    parts.append(_REPRODUCE_HEADER)
    parts.append(f"## Observed\n\nTrigger: {trigger_kind}")
    if exit_code is not None:
        parts.append(f"Exit code: {exit_code}")
    if exc is not None:
        parts.append(
            _section(
                "Stacktrace",
                _safe(
                    lambda: f"```\n{_format_traceback(exc)}\n```",
                    "<traceback unavailable>",
                ),
            )
        )
    if stderr:
        parts.append(
            _section(
                "stderr (last 40 lines)",
                "```\n" + "\n".join(stderr.splitlines()[-40:]) + "\n```",
            )
        )
    parts.append(
        _section("Environment snapshot", _safe(lambda: _env_snapshot(ctx), "<env unavailable>"))
    )
    parts.append(
        "## Suggested reproduction approach\n\n"
        "- Inspect the deepest user frame in the stacktrace above\n"
        "- Reproduce the env conditions captured above\n"
        "- See triggering PBI in HISTORY.md if available"
    )
    assert parts, "build_reproduce_md produced no sections — refusing empty REPRODUCE.md"
    return "\n\n".join(parts)
