"""Implementation of the ``ralph-executor init`` subcommand.

``ralph-executor init`` -- per-machine setup: prompt for
``workspace_root`` / ``queue_repo`` / ``queue_branch``, write
``~/.ralph/config.toml``, sanity-check ``gh`` and ``claude``.

KILL-RALPH-HOME T10 removed the ``scaffold`` subcommand and all of its
helpers (``cmd_scaffold``, ``ScaffoldError``, ``_git``, ``_has_branch``,
``_working_tree_clean``, ``CONFIG_TOML_STUB``, ``QUEUE_BRANCH``,
``QUEUE_SUBDIRS``, ``_validate_scaffold_target``). The queue is now
bootstrapped via ``scripts/setup_ralph_queue_github.py`` (host-side
ops), not by the executor itself.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from ralph_executor.subprocess_utils import run_text
from ralph_executor.url_utils import parse_target_repo
from ralph_executor.user_config import (
    read_queue_branch,
    read_queue_repo,
    read_workspace_root,
    user_config_path,
    write_queue_branch,
    write_queue_repo,
    write_workspace_root,
)

# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def _prompt_workspace_root(default: Path) -> Path:
    """Interactive prompt for ``workspace_root`` with a sensible default.

    ``input()`` is used so tests can monkeypatch it (and so the loop
    runs against stdin/tty). Catches ``EOFError`` so a piped / closed
    stdin (CI, non-tty) behaves as "accept default" rather than crashing
    with an unhandled traceback -- the dispatch in cli.py only catches
    ConfigError.
    """
    print(
        f"Where should ralph workspaces live (queue clone + target clones)? [{default}]: ",
        end="",
        flush=True,
    )
    try:
        raw = input().strip()
    except EOFError:
        print("")  # newline so the next message doesn't overlap the prompt
        raw = ""
    return Path(raw).expanduser() if raw else default


def _default_workspace_root() -> Path:
    """OS-flavoured suggestion for ``workspace_root``.

    Not prescriptive -- just the prompt default; operator can type
    anything. ``~/ralph-workspaces`` matches the convention used in the
    setup runbook (queue clone at ``<root>/queue/``, target clones at
    ``<root>/clones/<owner>/<name>/``).
    """
    return Path.home() / "ralph-workspaces"


def _check_tool(name: str) -> str | None:
    """Return the resolved path to ``name`` on PATH, or None."""
    return shutil.which(name)


def _prompt_queue_repo() -> str | None:
    """Prompt for the queue-repo HTTPS URL until valid; None on EOF.

    No default -- the operator must set this deliberately, since there is
    no sensible per-machine fallback (a queue points at one specific
    GitHub repo). Empty input -> reprompt. Invalid URL -> reprompt.
    EOFError (closed stdin) -> return None so the caller can warn and
    move on instead of crashing.
    """
    while True:
        print(
            "Queue repo URL (e.g. https://github.com/<owner>/ralph-queue): ",
            end="",
            flush=True,
        )
        try:
            raw = input().strip()
        except EOFError:
            print("")
            return None
        if not raw:
            print("queue_repo cannot be empty -- try again (Ctrl+D to skip).")
            continue
        try:
            parse_target_repo(raw)
        except ValueError as exc:
            print(f"invalid queue_repo URL: {exc}. Try again.")
            continue
        return raw


def _prompt_queue_branch(default: str) -> str | None:
    """Prompt for the queue branch name with a default; None on EOF.

    Blank input -> accept the default. EOFError (closed stdin) -> return
    None so the caller can warn and write the default itself rather
    than blocking on a non-tty / piped session.
    """
    print(f"Queue branch [{default}]: ", end="", flush=True)
    try:
        raw = input().strip()
    except EOFError:
        print("")
        return None
    return raw or default


def _smoke_clone_queue_repo(url: str, *, timeout: float = 10.0) -> bool:
    """Best-effort reachability check for ``url``: ``git ls-remote --heads``.

    Returns True on a zero-exit ``git ls-remote``; False on any failure
    (non-zero exit, ``git`` missing, network timeout). Caller treats
    False as a warning, never a blocker -- the operator may be on a flaky
    network or behind credentials that are valid for clone but reject
    ``ls-remote``.
    """
    try:
        result = run_text(
            ["git", "ls-remote", url],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def cmd_init(*, assume_yes: bool) -> int:
    """Run ``ralph-executor init``.

    Prompts for ``workspace_root`` / ``queue_repo`` / ``queue_branch``
    (in that order) and writes them to ``~/.ralph/config.toml``. Each
    prompt is idempotent -- re-running ``init`` against an already-
    configured file is a no-op print for keys already present.

    Args:
        assume_yes: skip every prompt; use defaults non-interactively
            (suitable for scripting).

    Returns: process exit code (0 on success, 2 on operator-recoverable
    error).
    """
    # workspace_root prompt
    existing_ws = read_workspace_root()
    if existing_ws is not None:
        print(f"workspace_root already set to {existing_ws} in {user_config_path()}")
    else:
        if assume_yes:
            chosen_ws = _default_workspace_root()
        else:
            chosen_ws = _prompt_workspace_root(_default_workspace_root())
        chosen_ws = chosen_ws.expanduser().resolve()

        # Disk-full / permission-denied / etc. on either the
        # workspace_root directory creation or the user-config write
        # would otherwise surface as a raw traceback. cli.py only
        # catches ConfigError for the init dispatch arm, so convert
        # here.
        try:
            chosen_ws.mkdir(parents=True, exist_ok=True)
            written = write_workspace_root(chosen_ws)
        except OSError as exc:
            print(f"error: cannot write user config: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {written}")
        print(f"workspace_root = {chosen_ws}")

    # queue_repo lives at the user-level (one queue per operator). Prompt
    # for it after workspace_root so a fresh `init` lands all knobs in
    # one pass; a re-run with queue_repo already set is a no-op print.
    existing_queue = read_queue_repo()
    if existing_queue is not None:
        print(f"queue_repo already set to {existing_queue} in {user_config_path()}")
    elif assume_yes:
        print(
            "WARNING: queue_repo not set. Add it manually to "
            f'{user_config_path()}: queue_repo = "<https-url>"'
        )
    else:
        chosen_queue = _prompt_queue_repo()
        if chosen_queue is None:
            print(
                "WARNING: queue_repo not set (closed stdin or skipped). "
                f"Add it manually to {user_config_path()}."
            )
        else:
            if not _smoke_clone_queue_repo(chosen_queue):
                print(
                    f"WARNING: smoke `git ls-remote {chosen_queue}` failed -- "
                    "proceeding anyway (could be a flaky network or "
                    "credentials valid only for clone, not ls-remote)."
                )
            try:
                queue_cfg = write_queue_repo(chosen_queue)
            except OSError as exc:
                print(
                    f"error: cannot write queue_repo to user config: {exc}",
                    file=sys.stderr,
                )
                return 2
            print(f"queue_repo = {chosen_queue}")
            print(f"wrote {queue_cfg}")

    # queue_branch: per-operator branch override, default "ralph-queue".
    # Mirrors queue_repo handling -- prompt after queue_repo, idempotent
    # on re-run, --yes writes the default non-interactively, EOF on
    # stdin falls back to the default.
    existing_branch = read_queue_branch()
    if existing_branch is not None:
        print(f"queue_branch already set to {existing_branch} in {user_config_path()}")
    else:
        queue_branch_default = "ralph-queue"
        chosen_branch: str = queue_branch_default
        if not assume_yes:
            prompted = _prompt_queue_branch(queue_branch_default)
            if prompted is None:
                # Closed/piped stdin during the prompt -- fall back to
                # the default rather than leaving the knob unset. Unlike
                # queue_repo (no sensible per-machine fallback), the
                # branch name has an obvious universal default.
                print(
                    f"WARNING: queue_branch prompt closed; defaulting to {queue_branch_default!r}."
                )
            else:
                chosen_branch = prompted
        try:
            branch_cfg = write_queue_branch(chosen_branch)
        except OSError as exc:
            print(
                f"error: cannot write queue_branch to user config: {exc}",
                file=sys.stderr,
            )
            return 2
        print(f"queue_branch = {chosen_branch}")
        print(f"wrote {branch_cfg}")

    # Best-effort tool checks. Warn, never abort: an operator might be
    # setting up on a host where claude is installed under a non-default
    # name or shimmed via PATH munging.
    gh = _check_tool("gh")
    if gh is None:
        print("WARNING: gh CLI not found on PATH -- install from https://cli.github.com/")
    else:
        try:
            result = run_text(
                [gh, "auth", "status"],
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                print("WARNING: gh CLI present but not logged in -- run `gh auth login`")
        except OSError as exc:
            print(f"WARNING: could not run `gh auth status`: {exc}")

    claude = _check_tool("claude")
    if claude is None:
        print("WARNING: claude CLI not found on PATH -- install Claude Code per docs")

    print("")
    print("Done. Run `uv run ralph-executor` to drain the queue.")
    return 0
