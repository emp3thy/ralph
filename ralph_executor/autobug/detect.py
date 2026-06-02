"""Top-level orchestration: signature → recursion → dedup → rate → emit."""

from __future__ import annotations

import logging

from ralph_executor.autobug import dedup, emit, fuses
from ralph_executor.autobug import signature as sig_mod
from ralph_executor.autobug.fuses import RateLimitConfig
from ralph_executor.autobug.types import Context, DedupResult

log = logging.getLogger(__name__)

_DEFAULT_RATE = RateLimitConfig()


def detect_python_crash(
    exc: BaseException,
    ctx: Context,
    *,
    target_repo: str,
    severity: str = "critical",
    rate_cfg: RateLimitConfig = _DEFAULT_RATE,
    dedup_window_days: int = 30,
) -> None:
    try:
        _do_detect_python(
            exc,
            ctx,
            target_repo=target_repo,
            severity=severity,
            rate_cfg=rate_cfg,
            dedup_window_days=dedup_window_days,
        )
    except BaseException as inner:
        log.warning("autobug.detect_python_crash itself failed: %s", inner)


def detect_subprocess_crash(
    exit_code: int,
    stderr: str,
    command: list[str],
    ctx: Context,
    *,
    target_repo: str,
    severity: str = "high",
    rate_cfg: RateLimitConfig = _DEFAULT_RATE,
    dedup_window_days: int = 30,
) -> None:
    try:
        _do_detect_subprocess(
            exit_code,
            stderr,
            command,
            ctx,
            target_repo=target_repo,
            severity=severity,
            rate_cfg=rate_cfg,
            dedup_window_days=dedup_window_days,
        )
    except BaseException as inner:
        log.warning("autobug.detect_subprocess_crash itself failed: %s", inner)


def _do_detect_python(
    exc: BaseException,
    ctx: Context,
    *,
    target_repo: str,
    severity: str,
    rate_cfg: RateLimitConfig,
    dedup_window_days: int,
) -> None:
    if not fuses.recursion_check(ctx.env):
        log.warning("autobug: recursion guard tripped (python)")
        return
    try:
        sig = sig_mod.python_crash_signature(exc)
    except BaseException as e:
        log.warning("autobug: signature computation failed: %s", e)
        return
    if not ctx.bot_author_email:
        log.warning("autobug: bot_author_email missing; aborting emit signature=%s", sig[:8])
        return
    result = dedup.lookup(sig, ctx.queue_root, ctx.now, window_days=dedup_window_days)
    _dispatch(
        result,
        sig,
        exc,
        ctx,
        target_repo=target_repo,
        severity=severity,
        trigger_kind="python_crash",
        rate_cfg=rate_cfg,
    )


def _do_detect_subprocess(
    exit_code: int,
    stderr: str,
    command: list[str],
    ctx: Context,
    *,
    target_repo: str,
    severity: str,
    rate_cfg: RateLimitConfig,
    dedup_window_days: int,
) -> None:
    del command
    if not fuses.recursion_check(ctx.env):
        log.warning("autobug: recursion guard tripped (subprocess)")
        return
    try:
        sig = sig_mod.subprocess_crash_signature(exit_code, stderr)
    except BaseException as e:
        log.warning("autobug: signature computation failed: %s", e)
        return
    if not ctx.bot_author_email:
        log.warning(
            "autobug: bot_author_email missing; aborting subprocess emit signature=%s",
            sig[:8],
        )
        return
    result = dedup.lookup(sig, ctx.queue_root, ctx.now, window_days=dedup_window_days)
    tail = stderr.splitlines()[-1] if stderr else ""
    synth: BaseException = RuntimeError(f"subprocess exit {exit_code}: {tail}")
    _dispatch(
        result,
        sig,
        synth,
        ctx,
        target_repo=target_repo,
        severity=severity,
        trigger_kind="subprocess_crash",
        rate_cfg=rate_cfg,
        stderr=stderr,
        exit_code=exit_code,
    )


def _dispatch(
    result: DedupResult,
    sig: str,
    exc: BaseException,
    ctx: Context,
    *,
    target_repo: str,
    severity: str,
    trigger_kind: str,
    rate_cfg: RateLimitConfig,
    stderr: str | None = None,
    exit_code: int | None = None,
) -> None:
    if result.kind == "bump_existing":
        if result.existing_pbi_id is None:
            log.warning("autobug dispatch: bump_existing missing existing_pbi_id")
            return
        emit.bump(result.existing_pbi_id, exc, ctx)
        return
    if result.kind == "reopen_regression":
        if result.existing_pbi_id is None:
            log.warning("autobug dispatch: reopen_regression missing existing_pbi_id")
            return
        if not fuses.rate_check(ctx.state_dir, rate_cfg, ctx.now):
            fuses.rollup(ctx.state_dir, sig, "rate-limited reopen", now=ctx.now)
            return
        emit.reopen(
            existing_pbi_id=result.existing_pbi_id,
            exc=exc,
            signature=sig,
            ctx=ctx,
            trigger_kind=trigger_kind,
            severity=severity,
            target_repo=target_repo,
            stderr=stderr,
            exit_code=exit_code,
        )
        fuses.append_emission(ctx.state_dir, ctx.now)
        return
    if not fuses.rate_check(ctx.state_dir, rate_cfg, ctx.now):
        fuses.rollup(ctx.state_dir, sig, "rate-limited new", now=ctx.now)
        return
    emit.new(
        signature=sig,
        exc=exc,
        ctx=ctx,
        trigger_kind=trigger_kind,
        severity=severity,
        target_repo=target_repo,
        stderr=stderr,
        exit_code=exit_code,
    )
    fuses.append_emission(ctx.state_dir, ctx.now)
