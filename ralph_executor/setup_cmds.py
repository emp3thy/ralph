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
from ralph_executor.url_utils import parse_target_repo
from ralph_executor.user_config import (
    read_queue_repo,
    read_ralph_home,
    user_config_path,
    write_queue_repo,
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
# Queue repo HTTPS URL lives at the user level (~/.ralph/config.toml),
# not here. `ralph-executor init` writes it. Shown here only as a
# reference for what the executor reads at startup:
# queue_repo = "https://github.com/<owner>/<name>-queue"
# main_branch = "main"
# max_attempts = 20
# iteration_sleep_seconds = 30
# claude_binary = "claude"
# log_level = "INFO"
# git_host = "github"   # or "ado"
#
# Project identifiers + alerting (promoted from env-only). Set these
# instead of exporting $GH_OWNER / $ADO_ORG_URL / $ADO_PROJECT /
# $RALPH_HALT_WEBHOOK every shell. Secrets (GH_TOKEN, ADO_PAT) stay
# env-only by policy.
# gh_owner = "your-github-org-or-user"
# ado_org_url = "https://dev.azure.com/your-org"
# ado_project = "your-ado-project"
# halt_webhook = "https://outlook.office.com/webhook/..."
#
# Sweep tuning (promoted from env-only). Required for sweep to run; if
# bot_author_email is unset, sweep is skipped each iteration with a
# WARNING. Env overrides keep the historical RALPH_ADO_AUTHOR_EMAIL /
# RALPH_STALE_DAYS spelling for backwards compatibility.
# bot_author_email = "ralph-bot@example.com"
# stale_days = 3
#
# Claude Code per-bash-tool ceiling in milliseconds. Default 900000 (15 min);
# Claude Code's own default is 600000 (10 min). Propagated to the spawned
# claude subprocess via the BASH_MAX_TIMEOUT_MS env var (subprocess-scoped —
# ralph's parent env is not touched). Must be positive.
# bash_max_timeout_ms = 900000
#
# Per-iteration wall-clock deadline (seconds) for the spawned claude -p
# subprocess. On timeout the executor kills the child and surfaces an
# error outcome to the loop (attempts increments, max-attempts -> blocked).
# Default 1200 (20 min). Env override: RALPH_CLAUDE_SESSION_TIMEOUT_SECONDS.
# Must be positive.
# claude_session_timeout_seconds = 1200
#
# Sweep auto-merge: when true, sweep auto-merges PRs that GitHub reports as
# mergeable_state == "clean" (CI green + required approvals + no conflicts +
# branch up-to-date). Default false. Opt in carefully — merging a PR a human
# still wanted to review is unrecoverable. Env override:
# RALPH_AUTO_MERGE_CLEAN_PRS=1.
# auto_merge_clean_prs = false
#
# Where target-repo clones live. Default: ~/ralph-workspaces.
# Each target gets a subdir: <workspace_root>/clones/<owner>/<name>/.
# Env override: RALPH_WORKSPACE.
# workspace_root = "~/ralph-workspaces"
#
# Cycle-detector same_file_thrashing thresholds (Layer 3 safety). The rule
# trips when one file is touched by `same_file_min_prs` distinct PBIs inside
# a rolling `same_file_window_hours` window. Defaults 10 / 24h. Operators
# raise them for high-velocity sprints where a central module legitimately
# receives many PRs and the rule otherwise false-trips. Both must be
# positive. Env overrides: RALPH_SAME_FILE_MIN_PRS, RALPH_SAME_FILE_WINDOW_HOURS.
# same_file_min_prs = 10
# same_file_window_hours = 24
#
# Drain-on-idle. Default behaviour (watch_mode = false) exits cleanly after
# `idle_exit_threshold` consecutive idle iterations — the right shape for
# unattended pod / container runs where the queue is baked in at launch and
# no operator is around to push more work. Flip to `watch_mode = true` for
# workstation daemon use (run forever, sleep on idle) — same effect as
# passing --watch on the CLI. Env overrides: RALPH_WATCH_MODE,
# RALPH_IDLE_EXIT_THRESHOLD. idle_exit_threshold must be positive.
# watch_mode = false
# idle_exit_threshold = 2
"""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def _prompt_ralph_home(default: Path) -> Path:
    """Interactive prompt with a sensible default. ``input()`` is used so
    tests can monkeypatch it (and so the loop runs against stdin/tty).

    Catches ``EOFError`` so a piped / closed stdin (CI, non-tty) behaves
    as "accept default" rather than crashing with an unhandled traceback
    — the dispatch in cli.py only catches ConfigError.
    """
    print(f"Where should ralph workspaces live? [{default}]: ", end="", flush=True)
    try:
        raw = input().strip()
    except EOFError:
        print("")  # newline so the next message doesn't overlap the prompt
        raw = ""
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


def _prompt_queue_repo() -> str | None:
    """Prompt for the queue-repo HTTPS URL until valid; None on EOF.

    No default — the operator must set this deliberately, since there is
    no sensible per-machine fallback (a queue points at one specific
    GitHub repo). Empty input → reprompt. Invalid URL → reprompt.
    EOFError (closed stdin) → return None so the caller can warn and
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
            print("queue_repo cannot be empty — try again (Ctrl+D to skip).")
            continue
        try:
            parse_target_repo(raw)
        except ValueError as exc:
            print(f"invalid queue_repo URL: {exc}. Try again.")
            continue
        return raw


def _smoke_clone_queue_repo(url: str, *, timeout: float = 10.0) -> bool:
    """Best-effort reachability check for ``url``: ``git ls-remote --heads``.

    Returns True on a zero-exit ``git ls-remote``; False on any failure
    (non-zero exit, ``git`` missing, network timeout). Caller treats
    False as a warning, never a blocker — the operator may be on a flaky
    network or behind credentials that are valid for clone but reject
    ``ls-remote``.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", url],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


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
    else:
        chosen: Path
        if ralph_home is not None:
            chosen = ralph_home.expanduser()
        elif assume_yes:
            chosen = _default_ralph_home()
        else:
            chosen = _prompt_ralph_home(_default_ralph_home())
        chosen = chosen.resolve()

        # Disk-full / permission-denied / etc. on either the ralph_home
        # directory creation or the user-config write would otherwise
        # surface as a raw traceback. cli.py only catches ConfigError for
        # the init dispatch arm, so convert here.
        try:
            chosen.mkdir(parents=True, exist_ok=True)
            written = write_ralph_home(chosen)
        except OSError as exc:
            print(f"error: cannot write user config: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {written}")
        print(f"ralph_home = {chosen}")

    # queue_repo lives at the user-level (one queue per operator). Prompt
    # for it after ralph_home so a fresh `init` lands both knobs in one
    # pass; a re-run with queue_repo already set is a no-op print.
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
                    f"WARNING: smoke `git ls-remote {chosen_queue}` failed — "
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

    # Capture the original branch BEFORE any state-mutating step so the
    # error handler can restore it on commit failure. If this call fails
    # (corrupt repo), report and bail before touching anything else.
    try:
        original_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    except ScaffoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Detached HEAD: `rev-parse --abbrev-ref HEAD` returns the literal
    # 'HEAD'. The cleanup path would then run `git switch HEAD`, which
    # is invalid syntax. Refuse upfront so the operator gets a clear
    # message instead of a confusing 'could not restore branch HEAD'
    # warning after the scaffold succeeds-then-fails.
    if original_branch == "HEAD":
        print(
            "error: cannot scaffold from detached HEAD — check out a branch first "
            "(e.g. `git switch main`)",
            file=sys.stderr,
        )
        return 2

    # Track whether we actually moved off the original branch so the
    # cleanup path knows whether a restore is needed.
    switched_to_queue = False
    try:
        if not _working_tree_clean(repo):
            raise ScaffoldError(
                "working tree is not clean — commit, stash, or discard local "
                "changes before scaffolding so the queue commit lands cleanly"
            )

        if original_branch != "main" and original_branch != "master":
            print(
                f"warning: current branch is {original_branch!r}, not main/master. "
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
        switched_to_queue = True

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
        # Detect the no-op-scaffold case (every dir + stub already
        # existed) via `git diff --cached --quiet`: exit 0 means the
        # index matches HEAD (nothing to commit), exit 1 means staged
        # changes are present, exit >=2 is a real error. This is locale-
        # independent — the previous string-match on "nothing to commit"
        # broke on non-English git installs (e.g. "rien à valider").
        # ralph_executor.git_ops.commit_index uses the same idiom.
        diff_check = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        if diff_check.returncode >= 2:
            raise ScaffoldError(
                f"git diff --cached --quiet failed (exit {diff_check.returncode}): "
                f"{diff_check.stderr.strip() or diff_check.stdout.strip()}"
            )
        if diff_check.returncode == 1:
            commit_status = subprocess.run(
                ["git", "commit", "-m", "chore(queue): scaffold .ralph/ skeleton"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                check=False,
            )
            if commit_status.returncode != 0:
                raise ScaffoldError(
                    f"git commit failed (exit {commit_status.returncode}): "
                    f"{commit_status.stderr.strip() or commit_status.stdout.strip()}"
                )
    except (ScaffoldError, OSError) as exc:
        # OSError covers disk-full / permission-denied from mkdir /
        # write_text inside the scaffold body — without it, those raise
        # raw tracebacks while the operator is stranded on ralph-queue
        # with a partial .ralph/.
        print(f"error: {exc}", file=sys.stderr)
        # Best-effort cleanup: if we switched into the queue branch
        # before the error fired, restore the operator to where they
        # were AND clean up the partial scaffold.
        #
        # Two-step is required because `git switch` carries staged-but-
        # new files (untracked on both source and target branches) to
        # the target silently. Without the hard reset, the operator
        # would end up on the original branch with `.ralph/` files
        # staged and on disk, needing manual `git reset && rm -rf`.
        #
        #   1. `git reset --hard HEAD` on the queue branch:
        #      - new-branch path: HEAD == base branch's tip, no .ralph/
        #        committed, so this reverts our writes (index AND
        #        working tree).
        #      - --force path: HEAD == queue's prior tip which has
        #        .ralph/ committed, so this restores .ralph/ to its
        #        last-committed state. Either way we're left with a
        #        clean index + working tree relative to the queue
        #        branch's HEAD.
        #   2. `git switch <original>`: now safe (no staged content
        #      to carry across).
        #
        # The pre-flight working-tree-clean check guarantees the hard
        # reset only throws away OUR changes, never operator work.
        # Each step is independently guarded so one failure doesn't
        # block the next.
        if switched_to_queue:
            reset_ok = True
            try:
                _git(repo, "reset", "--hard", "HEAD")
            except ScaffoldError as reset_exc:
                reset_ok = False
                print(
                    f"warning: could not reset queue branch before restore: {reset_exc}. "
                    f"Leaving you on {QUEUE_BRANCH!r}; clean up with "
                    f"`git -C {repo} reset --hard HEAD` then "
                    f"`git -C {repo} switch {original_branch}`.",
                    file=sys.stderr,
                )
            # `git reset --hard` does NOT touch untracked files. If the
            # error fired during the mkdir / write_text loop BEFORE the
            # `git add .ralph` call, the partial scaffold is untracked
            # and would silently cross the upcoming branch switch. Use
            # `git clean -fd .ralph` to remove the untracked entries
            # (best-effort, scoped to .ralph/).
            try:
                _git(repo, "clean", "-fd", ".ralph")
            except ScaffoldError as clean_exc:
                print(
                    f"warning: could not clean untracked .ralph/ entries: {clean_exc}",
                    file=sys.stderr,
                )
            # Skip the switch when reset failed: switching with staged
            # additions would silently carry them to the original branch,
            # which is exactly the cross-branch leak we just fixed.
            if reset_ok:
                try:
                    _git(repo, "switch", original_branch)
                except ScaffoldError as restore_exc:
                    print(
                        f"warning: could not restore original branch "
                        f"{original_branch!r}: {restore_exc}",
                        file=sys.stderr,
                    )
        return 2

    print(f"scaffolded .ralph/ skeleton on {QUEUE_BRANCH} in {repo}")
    print(f"next: git -C {repo} push -u origin {QUEUE_BRANCH}")
    return 0
