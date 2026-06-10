"""Spawn ``claude -p`` against the current PBI and classify the outcome.

The spawner sets ``RALPH_PBI_DIR`` in the subprocess environment so the
spawned Claude session can locate its working PBI without having to
parse argv. ``classify_outcome`` then maps the (stdout, stderr,
exit_code, on-disk effects, pr-lookup) tuple to one of four typed
outcomes:

  * ``pr_created`` — an OPEN PR exists for the PBI's feature branch
                     (``ralph/<PBI-ID>``). Source of truth is the git
                     host, not Claude's stdout — Claude's stdout is
                     unreliable because PROMPT.md doesn't mandate a
                     specific marker line and the pr-skill returns its
                     own JSON, not a plain "PR created" message.
  * ``stuck``      — Ralph wrote STUCK.md into the PBI directory.
  * ``partial``    — Ralph exited zero with no STUCK.md and no PR
                     (multi-step PBI: stay in current/, run again).
  * ``error``      — Non-zero exit code with no STUCK.md (transient
                     failure; loop driver currently treats this the
                     same as ``partial`` but the explicit kind makes
                     Plan 9's safety controls easier to wire later).

STUCK.md takes precedence over a PR-created signal because Ralph may
have written STUCK.md after pushing the feature branch (e.g. PR was
opened but a later step failed).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any

from ralph_executor.config import ConfigError, ExecutorConfig

# Imported under the old private names so spawn_claude_p keeps resolving
# them as claude_spawn module globals — tests patch
# ``ralph_executor.claude_spawn._query_open_pr_via_gh`` /
# ``._wait_for_pr_checks`` and those targets must keep intercepting.
from ralph_executor.gh_queries import (
    query_open_pr_via_gh as _query_open_pr_via_gh,
)
from ralph_executor.gh_queries import (
    wait_for_pr_checks as _wait_for_pr_checks,
)
from ralph_executor.outcome import ClaudeOutcome, OutcomeKind, PrCheckState, classify_outcome
from ralph_executor.prompt_composer import compose_prompt
from ralph_executor.subprocess_utils import popen_text
from ralph_executor.types import PBI

__all__ = [
    "ClaudeOutcome",
    "OutcomeKind",
    "PrCheckState",
    "classify_outcome",
    "spawn_claude_p",
]

log = logging.getLogger(__name__)

_SIG_FM_RE = re.compile(r"^signature:\s*[0-9a-f]+\s*$", re.MULTILINE)


def _pbi_frontmatter_has_signature(pbi: PBI) -> bool:
    """True iff any of the PBI's entry-file frontmatters contains ``signature: <hex>``.

    Used by ``spawn_claude_p`` to set ``RALPH_AUTOBUG_DEPTH=1`` for autobug
    PBIs so a downstream crash inside the spawned Claude does NOT spawn a
    second autobug for the same signature (recursion guard via env-marker).

    Every candidate file is tried — a missing signature in the first
    matching file (or an OSError reading it) must NOT short-circuit the
    search, or PBIs whose signature lives in a later entry file would
    silently bypass the recursion guard.
    """
    for entry in ("BUG.md", "PBI.md", "FEEDBACK.md"):
        f = pbi.path / entry
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if _SIG_FM_RE.search(text):
            return True
    return False


def _queue_repo_root_for_spawn(cfg: ExecutorConfig) -> Path:
    """Filesystem path of the queue clone (replica of ``loop._queue_repo_root``).

    Replicated here rather than imported to avoid a ``claude_spawn`` ->
    ``loop`` circular import (``loop`` already imports ``spawn_claude_p``
    from this module). The body must stay in sync with
    ``ralph_executor.loop._queue_repo_root``; that function is the single
    source of truth for the queue-clone path in non-spawn code paths.
    The PLAN template proposed a ``cfg.use_worktrees`` branch, but
    ``load_config`` rejects ``use_worktrees=False`` at config-load time,
    so the branch is unreachable; this replica mirrors the loop helper
    exactly.
    """
    return cfg.queue_clone_path


def _build_argv(cfg: ExecutorConfig, *, pbi_dir: Path, pbi: PBI) -> list[str]:
    """Compose the argv passed to the ``claude`` binary.

    Standing prompt (the file formerly known as ``PROMPT.md``) is
    assembled per-PBI-type by ``compose_prompt`` and written to a
    per-iteration file under
    ``<queue>/.ralph/state/standing-prompt-<pbi.id>.md``. The ``-p``
    directive tells Claude to read that file before working on the PBI.
    Writing the prompt to a file (rather than passing it inline via
    ``--append-system-prompt``) is required: the composed feature
    prompt is ~12 KB and pr-feedback is ~16 KB, both exceeding the
    Windows ``cmd.exe`` 8191-char command-line ceiling that any single
    argv element must stay under (verified by Iteration 20 of the
    SPLIT-PROMPT-MD-INTO-PER-TOPIC-FOLDERS-WITH-COMPOSER PBI: 16/17
    spawn tests red with ``"The command line is too long."``). See
    ``docs/superpowers/specs/2026-05-31-prompt-md-split-design.md``.

    ``pbi_dir`` is the absolute on-disk path Claude should read/write.
    In legacy single-checkout mode it equals ``pbi.path``; in worktree
    mode it points into the queue worktree (``.ralph-work/queue/.ralph/
    current/<id>/``) rather than the work worktree where Claude's cwd
    is rooted. ``pbi`` is forwarded so the composer can pick the
    per-type assembly (feature/bug/pr-feedback).

    ``--output-format stream-json --verbose`` makes Claude emit one
    line-delimited JSON event per step (system init, assistant message,
    tool use, result, etc.) and FLUSH after each line. Without this,
    Node's default block-buffering on a piped stdout means the parent
    sees no output until Claude exits — fatal for operator visibility on
    Windows, where ``bufsize=1`` does not propagate line-buffering to
    the child's CRT (see BUG-claude-stdout-streaming-windows).
    """
    queue_root = _queue_repo_root_for_spawn(cfg)
    prompt_root = queue_root / "prompt"
    standing = compose_prompt(prompt_root, pbi.type)

    state_dir = queue_root / ".ralph" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    standing_path = state_dir / f"standing-prompt-{pbi.id}.md"
    standing_path.write_text(standing, encoding="utf-8")

    # Order matters: the prompt string MUST immediately follow `-p`,
    # otherwise Claude's arg parser (commander/yargs style) consumes the
    # next token as the prompt value. Putting `--output-format` between
    # `-p` and the prompt would make Claude treat "--output-format" as
    # the prompt and the actual prompt as an unrecognised positional.
    # Stream-json flags go BEFORE `-p`.
    argv = [
        cfg.claude_binary,
        "--output-format",
        "stream-json",
        "--verbose",
        # Scope the permission mode to ralph's subprocess rather than
        # letting it inherit the host's ``~/.claude/settings.json``
        # ``defaultMode``. Without this, an interactive operator running
        # ``claude`` on the same host would have to globally relax their
        # safety classifier just so ralph can write files the executor
        # needs (e.g. ``.claude/settings.json`` under ROSA). Default
        # value is the executor config's ``claude_permission_mode``
        # (``bypassPermissions`` unless overridden) — ralph is
        # non-interactive and cannot answer permission prompts. MUST
        # appear BEFORE ``-p``; tokens after ``-p`` are consumed as the
        # prompt value by claude's arg parser.
        "--permission-mode",
        cfg.claude_permission_mode,
        "-p",
        (f"Read {standing_path} for your standing instructions, then work the PBI in {pbi_dir}."),
    ]
    return argv


def _pr_number_from_url(pr_url: str) -> int | None:
    """Extract the integer PR number from a GitHub PR URL.

    Returns ``None`` when the URL does not match the expected
    ``.../pull/<int>`` shape so callers can fall back to a non-``pr_created``
    classification rather than crash.
    """
    parts = pr_url.rstrip("/").split("/")
    try:
        idx = parts.index("pull")
    except ValueError:
        return None
    if idx + 1 >= len(parts):
        return None
    try:
        return int(parts[idx + 1])
    except ValueError:
        return None


def _summarise_stream_json_line(raw_line: str) -> str:
    """Return a compact one-line summary of a Claude stream-json event.

    Claude's ``--output-format stream-json`` emits one JSON envelope per
    line. Logging the raw envelope is verbose and hard to skim; this
    helper extracts the most useful fields per event type and falls back
    to the raw (stripped) line on any parse failure. Truncates long
    text payloads at 200 chars so log lines stay scannable.
    """
    stripped = raw_line.rstrip()
    try:
        event = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return stripped
    if not isinstance(event, dict):
        return stripped
    event_type = str(event.get("type", "?"))
    subtype = event.get("subtype")
    if event_type == "assistant":
        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = str(block.get("text", "")).strip().replace("\n", " ")
                        if text:
                            return f"assistant: {text[:200]}"
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool = block.get("name", "?")
                        return f"assistant: tool_use {tool}"
        return "assistant: (no text)"
    if event_type == "system" and subtype:
        hook_name = event.get("hook_name")
        if hook_name:
            return f"system/{subtype}: {hook_name}"
        return f"system/{subtype}"
    if event_type == "result":
        is_error = event.get("is_error")
        duration = event.get("duration_ms")
        marker = "error" if is_error else (subtype or "ok")
        if isinstance(duration, int):
            return f"result: {marker} ({duration} ms)"
        return f"result: {marker}"
    if subtype:
        return f"{event_type}/{subtype}"
    return event_type


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """Kill ``proc`` AND any descendants.

    The Windows ``claude`` binary is a ``.cmd`` shim that ``exec``s the
    real Node entrypoint; ``proc.kill()`` only terminates the shim, so
    the Node child keeps the stdout/stderr pipes open and the tee
    threads block forever on read. We use ``taskkill /T /F`` on Windows
    to walk the parent->child tree. On POSIX the spawn uses a fresh
    process group (``start_new_session=True``) so a single ``killpg``
    fells the whole tree, with ``proc.kill()`` as a fallback if the
    process group could not be set (e.g. the spawn raced ahead of
    ``setsid``).
    """
    if sys.platform == "win32":
        # /T = also terminate child processes; /F = forceful. We ignore
        # the return code: a non-zero exit just means the process was
        # already gone, which is the desired end state. Suppress
        # TimeoutExpired so ``_kill_process_tree`` never propagates an
        # exception from inside an ``except`` handler in the caller.
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                check=False,
                capture_output=True,
                timeout=10,
            )
        # ``taskkill`` does the work; ``proc.kill()`` is a no-op fallback
        # in case ``taskkill`` is unavailable on the operator's PATH (rare
        # on a real Windows install but cheap to guard against).
        with contextlib.suppress(OSError):
            proc.kill()
        return
    # POSIX: kill the whole process group. ``os.killpg`` raises
    # ``ProcessLookupError`` when the leader has already exited; in that
    # case ``proc.kill()`` is enough to clean up anything left.
    try:
        os.killpg(os.getpgid(proc.pid), 9)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(OSError):
            proc.kill()


def _tee_stream(
    stream: IO[str],
    prefix: str,
    buf: list[str],
    err_slot: list[BaseException],
) -> None:
    """Drain a subprocess stream line-by-line: log a compact per-line
    summary live with a prefix AND accumulate the raw content into
    ``buf`` for the caller.

    Lines that parse as stream-json envelopes are summarised via
    :func:`_summarise_stream_json_line`; any other line (e.g. a stderr
    warning) is logged verbatim. The raw line is always preserved in
    ``buf`` so downstream consumers (the classifier, diagnostics) see
    the unaltered output.

    Any exception is captured into ``err_slot`` so the parent can re-raise
    it after join — otherwise it would be silently swallowed by Python's
    default ``threading.excepthook``, leaving ``buf`` truncated and
    causing the classifier to act on partial output.
    """
    try:
        for raw_line in stream:
            buf.append(raw_line)
            summary = _summarise_stream_json_line(raw_line)
            log.info("%s %s", prefix, summary)
    except BaseException as exc:  # noqa: BLE001 - re-raised in parent
        err_slot.append(exc)


def spawn_claude_p(
    cfg: ExecutorConfig,
    pbi: PBI,
    *,
    cwd: Path | None = None,
    pbi_dir: Path | None = None,
) -> ClaudeOutcome:
    """Run ``claude -p`` against the PBI and return a classified outcome.

    Tees Claude's stdout and stderr to the executor's logger live (so the
    operator sees Claude's progress between iteration markers) while ALSO
    accumulating the streams into ``outcome.stdout`` / ``outcome.stderr``
    for the classifier and downstream diagnostics.

    ``cwd`` overrides the subprocess working directory. When omitted,
    falls back to ``pbi.work_worktree`` (set by ``_claim_pbi`` in
    multi-target mode so Claude runs inside the target's per-PBI
    worktree). If neither override is supplied the call raises
    ``ConfigError`` — the per-PBI worktree must be materialised by
    ``_claim_pbi`` or the resume path in ``iterate_once`` before Claude
    can be spawned.

    ``pbi_dir`` overrides the on-disk PBI directory the spawned Claude
    reads/writes; defaults to ``pbi.path`` for legacy mode. In worktree
    mode the loop passes the absolute path under the queue worktree so
    Claude finds ``.ralph/current/<id>/`` even though its cwd is the
    work worktree. The value is forwarded both as the ``RALPH_PBI_DIR``
    environment variable and as the path substituted into the prompt.

    Both overrides are validated as existing directories before exec so
    a misconfigured worktree fails fast with a clear error rather than
    crashing the spawned Claude with a confusing read failure.

    On KeyboardInterrupt or any other exception from ``proc.wait()``, the
    child process is killed and the tee threads are joined before the
    exception propagates — otherwise the non-daemon I/O threads would
    block interpreter shutdown indefinitely on a pipe read that nobody
    can fulfil. ``daemon=True`` is set as a belt-and-braces guard.
    """
    effective_pbi_dir = Path(pbi_dir) if pbi_dir is not None else pbi.path
    if cwd is not None:
        effective_cwd = Path(cwd)
    elif pbi.work_worktree is not None:
        effective_cwd = pbi.work_worktree
    else:
        raise ConfigError(
            f"spawn_claude_p: PBI {pbi.id} has no work_worktree set and no "
            "cwd override was supplied. The per-PBI worktree must be "
            "materialised by _claim_pbi or recovered by the resume path "
            "in iterate_once before Claude can be spawned."
        )
    if not effective_pbi_dir.is_dir():
        raise FileNotFoundError(
            f"RALPH_PBI_DIR target {effective_pbi_dir} is not an existing directory"
        )
    if not effective_cwd.is_dir():
        raise FileNotFoundError(f"claude cwd {effective_cwd} is not an existing directory")
    argv = _build_argv(cfg, pbi_dir=effective_pbi_dir, pbi=pbi)
    env = os.environ.copy()
    env["RALPH_PBI_DIR"] = str(effective_pbi_dir)
    # Only propagate ANTHROPIC_API_KEY when cfg actually carries one.
    # Empty string breaks claude CLI's OAuth fallback — leave it absent
    # so the claude CLI picks up its own OAuth session.
    if cfg.anthropic_api_key:
        env.setdefault("ANTHROPIC_API_KEY", cfg.anthropic_api_key)
    # Subprocess-scoped bridge for the Claude Code per-bash-tool ceiling.
    # Claude Code's own default is 600_000 (10 min); ralph's default is
    # 900_000 (15 min), overridable per repo via TOML and per shell via
    # env. Setting on ``env`` (not os.environ) keeps the override scoped
    # to this child — ralph's parent env stays untouched.
    env["BASH_MAX_TIMEOUT_MS"] = str(cfg.bash_max_timeout_ms)
    # Per-PBI owner so the spawned Claude's ``pr-github`` skill (and any
    # ``gh`` invocation that reads GH_OWNER) writes to the target repo
    # owner from ``pbi.target_repo``, not whatever owner the operator
    # configured for ralph itself. None in legacy single-target mode.
    if pbi.target_info is not None:
        env["GH_OWNER"] = pbi.target_info.owner
        # Per-PBI better-memory project scope so the subagent's observations
        # land in the target repo's project rather than the cwd-derived
        # worktree path. Requires BETTER_MEMORY_PROJECT support in
        # better-memory's project resolver (see better-memory PR
        # memory-project-env-override).
        env["BETTER_MEMORY_PROJECT"] = pbi.target_info.name
    else:
        # Legacy single-target mode — strip any BETTER_MEMORY_PROJECT
        # inherited from ralph's parent env so the subagent's
        # observations don't leak into whatever project name the
        # operator happens to have set for ralph itself.
        env.pop("BETTER_MEMORY_PROJECT", None)
    # Autobug recursion guard: when the spawned Claude is itself iterating
    # on an autobug PBI, mark the child env so any further crash inside
    # that subprocess is suppressed by ``fuses.recursion_check`` (which
    # reads ``RALPH_AUTOBUG_DEPTH``). Prevents an autobug loop emitting
    # a second autobug for the same signature.
    if _pbi_frontmatter_has_signature(pbi):
        env["RALPH_AUTOBUG_DEPTH"] = "1"
    log.info("spawning %s for PBI %s", argv[0], pbi.id)
    start = time.monotonic()
    # Put the child in its own process group / session so the timeout
    # path can fell the whole tree with one ``killpg`` (POSIX). On
    # Windows we use ``taskkill /T`` instead, but starting in a new
    # process group is still useful so the parent's Ctrl-C does not
    # cascade into ralph's spawn while it is being killed deliberately.
    popen_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        # subprocess.CREATE_NEW_PROCESS_GROUP isn't strictly needed for
        # taskkill /T to walk the tree (it traces parent->child via the
        # Win32 toolhelp snapshot), but it isolates the child's signal
        # disposition from ralph's so a Ctrl-C in the parent shell does
        # not race with the timeout-driven taskkill.
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    proc = popen_text(
        argv,
        cwd=str(effective_cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        **popen_kwargs,
    )
    stdout_buf: list[str] = []
    stderr_buf: list[str] = []
    stdout_err: list[BaseException] = []
    stderr_err: list[BaseException] = []
    # Popen with PIPE returns IO streams; assert for the type-checker.
    assert proc.stdout is not None and proc.stderr is not None
    t_out = threading.Thread(
        target=_tee_stream,
        args=(proc.stdout, "[claude]", stdout_buf, stdout_err),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_tee_stream,
        args=(proc.stderr, "[claude!]", stderr_buf, stderr_err),
        daemon=True,
    )
    t_out.start()
    t_err.start()
    timed_out = False
    try:
        returncode = proc.wait(timeout=cfg.claude_session_timeout_seconds)
    except subprocess.TimeoutExpired:
        # Per-iteration deadline exceeded — assume the session is wedged
        # (network hang outside a bash call, infinite tool loop, dead
        # OAuth refresh, classifier deadlock) and fell the whole process
        # tree. The tee threads see EOF once descendants die and exit
        # cleanly. The synthetic outcome below surfaces a clear
        # ``error`` to the loop, which treats it the same as any other
        # error iteration: attempts increments, PBI stays in current/,
        # max-attempts → blocked.
        log.warning(
            "claude session for PBI %s exceeded %ss -- killing",
            pbi.id,
            cfg.claude_session_timeout_seconds,
        )
        _kill_process_tree(proc)
        # Reap the child so it doesn't linger as a zombie (or trip
        # ResourceWarning under ``pytest -W error``); ignore OSError if
        # taskkill / killpg already let Popen finalise.
        with contextlib.suppress(OSError):
            proc.wait()
        timed_out = True
        returncode = -1
    except BaseException:
        # KeyboardInterrupt (Ctrl-C from the operator) or any other
        # exception: fell the child tree so the pipe-read threads exit
        # cleanly, join them, then re-raise. Without this, the non-daemon
        # threads would block interpreter shutdown forever.
        _kill_process_tree(proc)
        with contextlib.suppress(OSError):
            proc.wait()
        t_out.join()
        t_err.join()
        raise
    t_out.join()
    t_err.join()
    if timed_out:
        stderr_buf.append(
            f"\n[ralph] claude session exceeded "
            f"{cfg.claude_session_timeout_seconds}s and was killed\n"
        )
    # Re-raise any exception the tee threads captured so the classifier
    # never operates on truncated output.
    if stdout_err:
        raise stdout_err[0]
    if stderr_err:
        raise stderr_err[0]
    stdout_text = "".join(stdout_buf)
    stderr_text = "".join(stderr_buf)
    duration = time.monotonic() - start

    # Resolve PR state via gh AFTER Claude exits — this is the source of
    # truth for the "did Claude create a PR?" decision. stdout parsing
    # would be unreliable (PROMPT.md doesn't mandate a marker line and
    # the pr-skill emits its own JSON envelope).
    feature_branch = f"ralph/{pbi.id}"
    # ``gh pr list`` runs against the target clone's working tree (the
    # per-PBI worktree on the feature branch). ``cfg`` no longer carries
    # a process-wide repo path — every gh invocation is per-PBI.
    pr_url = _query_open_pr_via_gh(effective_cwd, feature_branch)
    # Gate pr_created on required CI checks being green. _wait_for_pr_checks
    # polls up to 3 min per iteration; non-pass states fall through to
    # ``partial`` in classify_outcome so the next iteration re-polls.
    pr_check_state: PrCheckState = "pass"
    pr_check_failed_names: list[str] = []
    if pr_url is not None:
        pr_number = _pr_number_from_url(pr_url)
        if pr_number is None:
            log.warning("could not parse PR number from %s", pr_url)
            pr_check_state = "error"
            pr_check_failed_names = [f"could not parse PR number from {pr_url}"]
        else:
            pr_check_state, pr_check_failed_names = _wait_for_pr_checks(
                effective_cwd,
                pr_number,
                max_polls=cfg.pr_check_poll_max_attempts,
                interval_seconds=cfg.pr_check_poll_interval_seconds,
            )
    outcome = classify_outcome(
        pbi_dir=effective_pbi_dir,
        stdout=stdout_text,
        stderr=stderr_text,
        exit_code=returncode,
        duration_seconds=duration,
        pr_url=pr_url,
        pr_check_state=pr_check_state,
        pr_check_failed_names=pr_check_failed_names,
    )
    # Autobug subprocess wire: surface a non-zero / timeout exit as a bug
    # PBI. No-op-safe — every failure path in the autobug code is
    # swallowed and logged; the original outcome is always returned.
    if outcome.kind == "error" and getattr(cfg, "autobug_enabled", True):
        try:
            from datetime import UTC, datetime

            from ralph_executor import autobug

            ctx = autobug.Context(
                queue_root=cfg.queue_clone_path,
                state_dir=cfg.queue_clone_path / ".ralph" / "state",
                env=dict(env),
                now=datetime.now(tz=UTC),
                ralph_sha=os.environ.get("RALPH_SHA", "<unknown>"),
                bot_author_email=cfg.bot_author_email,
                triggering_pbi_id=pbi.id,
                queue_branch=cfg.queue_branch,
            )
            from datetime import timedelta as _td

            from ralph_executor.autobug.fuses import RateLimitConfig

            autobug.detect_subprocess_crash(
                exit_code=returncode,
                stderr=stderr_text,
                command=argv,
                ctx=ctx,
                target_repo=cfg.queue_repo,
                severity=cfg.autobug_severity_subprocess_crash,
                rate_cfg=RateLimitConfig(
                    max_writes=cfg.autobug_rate_max,
                    window=_td(minutes=cfg.autobug_rate_window_minutes),
                ),
                dedup_window_days=cfg.autobug_dedup_done_window_days,
            )
        except BaseException as inner:  # noqa: BLE001 — no-op-safe by design
            log.warning("autobug subprocess wire failed: %s", inner)
    return outcome
