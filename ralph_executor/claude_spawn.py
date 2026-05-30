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
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Literal

from ralph_executor.config import ExecutorConfig
from ralph_executor.subprocess_utils import popen_text, run_text
from ralph_executor.types import PBI

log = logging.getLogger(__name__)

OutcomeKind = Literal["pr_created", "stuck", "partial", "error"]

_STUCK_FILENAME = "STUCK.md"
_GH_BINARY = "gh"


@dataclass(frozen=True)
class ClaudeOutcome:
    """Result of a single ``claude -p`` invocation against the current PBI."""

    kind: OutcomeKind
    pr_url: str | None
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float


def _build_argv(cfg: ExecutorConfig, *, pbi_dir: Path) -> list[str]:
    """Compose the argv passed to the ``claude`` binary.

    ``-p`` puts Claude in non-interactive print mode. The PBI directory
    path is forwarded both as an argument (the standing PROMPT.md tells
    Ralph to read it) and via the ``RALPH_PBI_DIR`` environment variable
    (which test stand-ins use to locate the PBI on disk).

    ``pbi_dir`` is the absolute on-disk path Claude should read/write.
    In legacy single-checkout mode it equals ``pbi.path``; in worktree
    mode it points into the queue worktree (``.ralph-work/queue/.ralph/
    current/<id>/``) rather than the work worktree where Claude's cwd
    is rooted.

    ``--output-format stream-json --verbose`` makes Claude emit one
    line-delimited JSON event per step (system init, assistant message,
    tool use, result, etc.) and FLUSH after each line. Without this,
    Node's default block-buffering on a piped stdout means the parent
    sees no output until Claude exits — fatal for operator visibility on
    Windows, where ``bufsize=1`` does not propagate line-buffering to
    the child's CRT (see BUG-claude-stdout-streaming-windows).
    """
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
        (
            "Read ./prompt/PROMPT.md and work the PBI in "
            f"{pbi_dir}. Follow the standing instructions."
        ),
    ]
    return argv


def _query_open_pr_via_gh(repo_path: Path, branch: str) -> str | None:
    """Ask the GitHub CLI whether an OPEN PR exists for ``branch``.

    Returns the PR URL or ``None`` if no open PR is found. Returns
    ``None`` on any subprocess / parse failure too — classifier callers
    treat "couldn't check" the same as "no PR" so a transient gh outage
    doesn't make the executor mis-classify a partial iteration as
    pr_created. The real PR state is reconciled by sweep (Plan 8).
    """
    try:
        result = run_text(
            [
                _GH_BINARY,
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "open",
                "--json",
                "url",
                "--limit",
                "1",
            ],
            cwd=str(repo_path),
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        # OSError catches FileNotFoundError (gh not installed),
        # PermissionError (gh present but not executable in this
        # container/CI), and any other transient OS-level failure.
        # All map to "no PR detected" — sweep (Plan 8) will reconcile.
        log.warning("gh pr list failed for %s: %s", branch, exc)
        return None
    if result.returncode != 0:
        log.warning(
            "gh pr list returned %d for %s: %s",
            result.returncode,
            branch,
            result.stderr.strip()[:200],
        )
        return None
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        log.warning("gh pr list returned non-JSON for %s: %s", branch, exc)
        return None
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if not isinstance(first, dict):
        return None
    url = first.get("url")
    return str(url) if isinstance(url, str) and url else None


PrCheckState = Literal["pass", "fail", "pending", "error"]


def _query_pr_checks(
    repo_path: Path,
    pr_number: int,
    *,
    timeout_seconds: float = 30.0,
) -> tuple[PrCheckState, list[str]]:
    """Call ``gh pr checks <num> --required --json bucket,name`` once.

    The verifier in Plan 18 uses this to decide whether to classify a
    just-pushed PR as ``pr_created`` (real CI green) or to fall back to
    ``partial`` (so the next iteration tries again).

    Returns a (state, names) tuple:

      * ``("pass", [])`` — every required check has ``bucket == "pass"``.
        Note: when ``gh`` returns an empty array (no required checks are
        configured on the branch) we ALSO return ``"pass"`` and log a
        WARNING. This PBI assumes the operator has required checks
        configured; if they don't, the verifier has nothing to gate on.
      * ``("fail", [<failed names>])`` — at least one required check has
        ``bucket == "fail"``. ``names`` is the list of failing check
        names so the classifier can surface them on stderr for the next
        iteration to read.
      * ``("pending", [])`` — gh exited 8 (at least one bucket is
        ``"pending"``). Caller's polling loop re-tries.
      * ``("error", [<one-line summary>])`` — gh missing, timed out,
        returned non-JSON, or some other unexpected failure. Caller
        treats as ``partial`` (re-poll next iteration).

    Failure mapping is intentionally pessimistic: anything we cannot
    confidently parse as "all required checks green" returns non-``pass``
    so the classifier never escalates a partial iteration to
    ``pr_created`` on a transient gh outage.
    """
    try:
        result = run_text(
            [
                _GH_BINARY,
                "pr",
                "checks",
                str(pr_number),
                "--required",
                "--json",
                "bucket,name",
            ],
            cwd=str(repo_path),
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        log.warning("gh pr checks timed out after %ss for PR #%d", timeout_seconds, pr_number)
        return ("error", [f"gh pr checks timed out after {timeout_seconds}s"])
    except OSError as exc:
        # FileNotFoundError (gh not installed), PermissionError, etc.
        log.warning("gh pr checks failed for PR #%d: %s", pr_number, exc)
        return ("error", [str(exc)])

    # ``gh pr checks --json bucket,name`` always emits a JSON array on
    # stdout when it ran at all — even when at least one check failed
    # (exit 1) or is pending (exit 8). We therefore parse the JSON FIRST
    # and use the buckets as the source of truth; the exit code is only a
    # fallback when stdout is empty/unparseable.
    if result.returncode == 8 and not (result.stdout or "").strip():
        return ("pending", [])

    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        if result.returncode == 8:
            return ("pending", [])
        summary = (result.stderr or "").strip()[:200]
        return ("error", [summary or f"gh pr checks exit {result.returncode}"])
    if not isinstance(payload, list):
        return ("error", ["gh pr checks JSON payload was not a list"])
    if not payload:
        # No required checks configured. The verifier has nothing to gate
        # on; we return "pass" but warn loudly so the operator notices.
        log.warning(
            "PR #%d has no required checks configured; treating as pass",
            pr_number,
        )
        return ("pass", [])

    failed: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            return ("error", ["gh pr checks JSON contained a non-object entry"])
        if item.get("bucket") == "fail":
            name = item.get("name")
            failed.append(str(name) if isinstance(name, str) else "<unnamed>")
    if failed:
        return ("fail", failed)

    buckets = {str(item.get("bucket")) for item in payload}
    if buckets == {"pass"}:
        return ("pass", [])
    if "pending" in buckets:
        return ("pending", [])
    # Any other state (e.g. all "skipping" / "cancel") is conservatively
    # treated as error so we never mis-classify it as pass.
    return ("error", ["unexpected bucket states: " + repr(sorted(buckets))])


def _wait_for_pr_checks(
    repo_path: Path,
    pr_number: int,
    *,
    max_polls: int = 6,
    interval_seconds: float = 30.0,
) -> tuple[PrCheckState, list[str]]:
    """Poll ``_query_pr_checks`` until terminal or the budget is exhausted.

    Polls up to ``max_polls`` times, sleeping ``interval_seconds`` between
    attempts. Total wall budget = ``max_polls * interval_seconds`` (default
    6 × 30 s = 3 minutes per iteration).

    A state is **terminal** if it lets the classifier make a confident
    decision now:

      * ``pass``    — CI green; classifier returns ``pr_created``.
      * ``fail``    — at least one required check failed; classifier
                      returns ``partial`` with the failed names visible
                      on stderr.
      * ``error``   — gh-side problem (binary missing, timeout, malformed
                      JSON). Retrying within the same iteration is
                      unlikely to help and would just burn the budget;
                      we surface the error to the caller, which treats
                      it as ``partial`` so the NEXT iteration re-polls.

    Non-terminal:

      * ``pending`` — at least one check is still running. Sleep and
                      re-poll.

    Returns ``("pending", [])`` if the budget is exhausted without a
    decision; the caller treats this as ``partial`` so the PBI stays in
    ``current/`` and the next iteration polls again.
    """
    if max_polls < 1:
        return ("pending", [])
    # Scale the per-call subprocess timeout to the configured poll
    # interval so the documented total wall budget actually holds. With
    # a hard-coded timeout, an operator setting interval_seconds=5 to
    # get faster feedback would still see each gh-call block up to 30 s
    # and overrun the 30-s total budget. Floor at 5 s to leave gh
    # enough time to spin up + hit the API even on slow links.
    per_call_timeout = max(interval_seconds, 5.0)
    for attempt in range(max_polls):
        state, names = _query_pr_checks(repo_path, pr_number, timeout_seconds=per_call_timeout)
        if state != "pending":
            return (state, names)
        if attempt < max_polls - 1:
            time.sleep(interval_seconds)
    return ("pending", [])


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
    worktree), then to ``cfg.repo_path`` for legacy single-checkout
    setups.

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
        effective_cwd = cfg.repo_path
    if not effective_pbi_dir.is_dir():
        raise FileNotFoundError(
            f"RALPH_PBI_DIR target {effective_pbi_dir} is not an existing directory"
        )
    if not effective_cwd.is_dir():
        raise FileNotFoundError(f"claude cwd {effective_cwd} is not an existing directory")
    argv = _build_argv(cfg, pbi_dir=effective_pbi_dir)
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
    pr_url = _query_open_pr_via_gh(cfg.repo_path, feature_branch)
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
                cfg.repo_path,
                pr_number,
                max_polls=cfg.pr_check_poll_max_attempts,
                interval_seconds=cfg.pr_check_poll_interval_seconds,
            )
    return classify_outcome(
        pbi_dir=effective_pbi_dir,
        stdout=stdout_text,
        stderr=stderr_text,
        exit_code=returncode,
        duration_seconds=duration,
        pr_url=pr_url,
        pr_check_state=pr_check_state,
        pr_check_failed_names=pr_check_failed_names,
    )


def classify_outcome(
    *,
    pbi_dir: Path,
    stdout: str,
    stderr: str,
    exit_code: int,
    duration_seconds: float,
    pr_url: str | None = None,
    pr_lookup: Callable[[], str | None] | None = None,
    pr_check_state: PrCheckState = "pass",
    pr_check_failed_names: list[str] | None = None,
) -> ClaudeOutcome:
    """Map the raw (stdout, stderr, exit, on-disk, pr-state) tuple to a
    typed outcome.

    Precedence (highest first):
      1. STUCK.md present on disk → ``stuck``
      2. Exit code non-zero       → ``error``
      3. open PR exists for the feature branch AND
         ``pr_check_state == "pass"`` → ``pr_created``
         (``pr_url`` is taken directly; if not provided, ``pr_lookup``
         is called once and its return value is used)
      4. open PR exists but ``pr_check_state == "fail"`` → ``partial``
         with a synthetic stderr line naming the failed required checks
         so the next iteration's Claude session reads them.
      5. open PR exists but ``pr_check_state in ("pending", "error")``
         → ``partial`` (no synthetic stderr; the next iteration's
         polling will re-decide).
      6. Otherwise                → ``partial``

    ``pr_url`` and ``pr_lookup`` exist so tests can inject the PR-state
    answer without monkeypatching subprocess. Production callers pass
    ``pr_url`` (already resolved by ``_query_open_pr_via_gh``) AND
    ``pr_check_state`` (resolved by ``_wait_for_pr_checks``).

    ``pr_check_state`` defaults to ``"pass"`` so legacy tests that
    pre-date the CI-green verifier (and don't care about it) keep
    classifying as ``pr_created`` when a ``pr_url`` is set. Production
    code paths in ``spawn_claude_p`` always pass the real state.
    """
    stuck_present = (pbi_dir / _STUCK_FILENAME).is_file()

    if stuck_present:
        return ClaudeOutcome(
            kind="stuck",
            pr_url=None,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
        )
    if exit_code != 0:
        return ClaudeOutcome(
            kind="error",
            pr_url=None,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
        )
    if pr_url is None and pr_lookup is not None:
        pr_url = pr_lookup()
    if pr_url:
        if pr_check_state == "pass":
            return ClaudeOutcome(
                kind="pr_created",
                pr_url=pr_url,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                duration_seconds=duration_seconds,
            )
        if pr_check_state == "fail":
            names = pr_check_failed_names or []
            synthetic = (
                f"PR {pr_url} required checks failed: "
                f"{', '.join(names) if names else '<unnamed>'}. "
                f"Fix the failures next iteration."
            )
            joined_stderr = (stderr + "\n" + synthetic) if stderr else synthetic
            return ClaudeOutcome(
                kind="partial",
                pr_url=pr_url,
                stdout=stdout,
                stderr=joined_stderr,
                exit_code=exit_code,
                duration_seconds=duration_seconds,
            )
        # pending or error: keep PBI in current/; next iteration re-polls.
        return ClaudeOutcome(
            kind="partial",
            pr_url=pr_url,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
        )
    return ClaudeOutcome(
        kind="partial",
        pr_url=None,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_seconds=duration_seconds,
    )
