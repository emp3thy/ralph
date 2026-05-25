"""Implementations of the ``init`` and ``scaffold`` subcommands.

``ralph-executor init``      -- per-machine setup: choose where ralph
                                workspaces live, write ``~/.ralph/config.toml``,
                                sanity-check ``gh`` and ``claude``.
``ralph-executor scaffold``  -- per-repo setup: create the ``ralph-queue``
                                branch with the ``.ralph/{inbox,current,
                                pending-pr,done,blocked}/`` skeleton and a
                                commented ``.ralph/config.toml`` stub, all
                                committed locally. Push left to the operator.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from ralph_executor.config import ConfigError, validate_repo_path
from ralph_executor.user_config import (
    read_ralph_home,
    user_config_path,
    write_ralph_home,
)

QUEUE_BRANCH = "ralph-queue"
QUEUE_SUBDIRS = ("inbox", "current", "pending-pr", "done", "blocked")

CONFIG_TOML_STUB = """\
# ralph-executor per-repo configuration.
#
# All keys are optional. Defaults are baked into ralph_executor.config.
# Environment variables (RALPH_*) override anything set here; CLI flags
# override env. See the load_config docstring for the precedence chain.
#
# queue_branch = "ralph-queue"
# main_branch = "main"
# max_attempts = 20
# iteration_sleep_seconds = 30
# claude_binary = "claude"
# log_level = "INFO"
"""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def _prompt_ralph_home(default: Path) -> Path:
    """Interactive prompt with a sensible default. ``input()`` is used so
    tests can monkeypatch it (and so the loop runs against stdin/tty)."""
    print(f"Where should ralph workspaces live? [{default}]: ", end="", flush=True)
    raw = input().strip()
    return Path(raw).expanduser() if raw else default


def _default_ralph_home() -> Path:
    """OS-flavoured suggestion. Not prescriptive — just the prompt
    default; operator can type anything."""
    if sys.platform == "win32":
        return Path("C:/dev/ralph")
    return Path.home() / "dev" / "ralph"


def _check_tool(name: str) -> str | None:
    """Return the resolved path to ``name`` on PATH, or None."""
    return shutil.which(name)


def cmd_init(*, ralph_home: Path | None, assume_yes: bool) -> int:
    """Run ``ralph-executor init``.

    Args:
        ralph_home: explicit path from ``--ralph-home``; if None, prompt
            unless ``--yes`` is given (in which case the OS default is
            used non-interactively, suitable for scripting).
        assume_yes: skip the prompt; use the OS default when ``ralph_home``
            is None.

    Returns: process exit code (0 on success, 2 on operator-recoverable
    error).
    """
    existing = read_ralph_home()
    if existing is not None and ralph_home is None:
        print(f"ralph_home already set to {existing} in {user_config_path()}")
        print("(pass --ralph-home PATH to overwrite, or edit the file directly)")
        return 0

    chosen: Path
    if ralph_home is not None:
        chosen = ralph_home.expanduser()
    elif assume_yes:
        chosen = _default_ralph_home()
    else:
        chosen = _prompt_ralph_home(_default_ralph_home())
    chosen = chosen.resolve()

    chosen.mkdir(parents=True, exist_ok=True)
    written = write_ralph_home(chosen)
    print(f"wrote {written}")
    print(f"ralph_home = {chosen}")

    # Best-effort tool checks. Warn, never abort: an operator might be
    # setting up on a host where claude is installed under a non-default
    # name or shimmed via PATH munging.
    gh = _check_tool("gh")
    if gh is None:
        print("WARNING: gh CLI not found on PATH — install from https://cli.github.com/")
    else:
        try:
            result = subprocess.run(
                [gh, "auth", "status"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                print("WARNING: gh CLI present but not logged in — run `gh auth login`")
        except OSError as exc:
            print(f"WARNING: could not run `gh auth status`: {exc}")

    claude = _check_tool("claude")
    if claude is None:
        print("WARNING: claude CLI not found on PATH — install Claude Code per docs")

    print("")
    print("Next: `ralph-executor scaffold` inside a repo you want ralph to manage.")
    return 0


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------


class ScaffoldError(RuntimeError):
    """Raised when the local repo scaffold cannot proceed."""


def _git(repo: Path, *args: str) -> str:
    """Run a git subcommand against ``repo`` and return stripped stdout.

    Raises ``ScaffoldError`` on non-zero exit so the caller can convert
    to a clean operator-facing message rather than a traceback.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ScaffoldError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _has_branch(repo: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _working_tree_clean(repo: Path) -> bool:
    status = _git(repo, "status", "--porcelain")
    return status == ""


def cmd_scaffold(*, repo_path: Path, force: bool, with_config_toml: bool) -> int:
    """Run ``ralph-executor scaffold``.

    Args:
        repo_path: the local clone to scaffold. Resolved by the caller
            from ``--repo`` / ``--workspace`` / cwd; this function only
            validates and acts on it.
        force: if ``ralph-queue`` already exists, fail unless True.
            ``--force`` skips the existence check (but the branch is NOT
            deleted — operator's responsibility to clean up).
        with_config_toml: write ``.ralph/config.toml`` skeleton.

    Returns: 0 on success, 2 on operator-recoverable error.
    """
    try:
        repo = validate_repo_path(repo_path, source="scaffold target")
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if not _working_tree_clean(repo):
            raise ScaffoldError(
                "working tree is not clean — commit, stash, or discard local "
                "changes before scaffolding so the queue commit lands cleanly"
            )

        current_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        if current_branch != "main" and current_branch != "master":
            print(
                f"warning: current branch is {current_branch!r}, not main/master. "
                f"ralph-queue will be created off it.",
                file=sys.stderr,
            )

        if _has_branch(repo, QUEUE_BRANCH):
            if not force:
                raise ScaffoldError(
                    f"{QUEUE_BRANCH!r} branch already exists; re-run with --force "
                    f"to scaffold on top of it (existing branch will NOT be deleted)"
                )
            _git(repo, "switch", QUEUE_BRANCH)
        else:
            _git(repo, "switch", "-c", QUEUE_BRANCH)

        ralph_dir = repo / ".ralph"
        for sub in QUEUE_SUBDIRS:
            d = ralph_dir / sub
            d.mkdir(parents=True, exist_ok=True)
            (d / ".gitkeep").write_text("", encoding="utf-8")

        if with_config_toml:
            cfg = ralph_dir / "config.toml"
            if not cfg.exists():
                cfg.write_text(CONFIG_TOML_STUB, encoding="utf-8")

        _git(repo, "add", ".ralph")
        # An empty commit would mean the scaffold was a no-op (every dir
        # and the stub already existed); allow that, the operator sees a
        # clean status afterward.
        commit_status = subprocess.run(
            ["git", "commit", "-m", "chore(queue): scaffold .ralph/ skeleton"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        if commit_status.returncode != 0 and "nothing to commit" not in commit_status.stdout:
            raise ScaffoldError(
                f"git commit failed (exit {commit_status.returncode}): "
                f"{commit_status.stderr.strip() or commit_status.stdout.strip()}"
            )
    except ScaffoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"scaffolded .ralph/ skeleton on {QUEUE_BRANCH} in {repo}")
    print(f"next: git -C {repo} push -u origin {QUEUE_BRANCH}")
    return 0
