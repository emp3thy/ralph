"""Shared helpers for skills that mutate the queue clone.

The supervisor skills (`ralph-cancel`, `ralph-promote`, `ralph-triage`)
all follow the same pattern: acquire the queue clone at
``<workspace_root>/queue/`` (always on ``main``), mutate one or more PBI
directories under ``.ralph/``, commit, optionally push. This module is
the single source of truth for that pattern so the skills stay
consistent and the git-related test surface has a single home.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ralph_executor.queue_clone import QueueCloneError, ensure_queue_clone

QUEUE_STATE_FOLDERS: tuple[str, ...] = (
    "current",
    "inbox",
    "pending-pr",
    "blocked",
    "done",
    "archive",
)

RALPH_DIR_NAME = ".ralph"


class QueueWriterError(RuntimeError):
    """Raised when a queue-writer operation can't proceed.

    Skills catch this exception, print the message to stderr, and exit
    with code 2.
    """


# ----------------------------------------------------------------------
# Git helpers
# ----------------------------------------------------------------------


def _run_git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise QueueWriterError(f"git {' '.join(args)} failed ({exc.returncode}): {stderr}") from exc
    return result.stdout.strip()


def ensure_git_repo(repo: Path) -> None:
    """Raise ``QueueWriterError`` if ``repo`` is not a git repository."""
    if not repo.exists():
        raise QueueWriterError(f"{repo} is not a git repository (path missing)")
    if not (repo / ".git").exists():
        raise QueueWriterError(f"{repo} is not a git repository (no .git/ directory)")


def acquire_queue_clone(
    workspace_root: Path,
    queue_repo: str,
    queue_branch: str,
    *,
    timeout: float = 120.0,
) -> Path:
    """Idempotent queue clone for operator skills.

    Mirrors ``ralph_executor.queue_clone.ensure_queue_clone``. The branch
    is forwarded unchanged; the operator's ``~/.ralph/config.toml`` knob is
    resolved by the caller via ``resolve_queue_branch``.
    """
    try:
        return ensure_queue_clone(workspace_root, queue_repo, queue_branch, timeout=timeout)
    except QueueCloneError as exc:
        raise QueueWriterError(str(exc)) from exc


_DEFAULT_WORKSPACE_ROOT = Path.home() / "ralph-workspaces"


def resolve_workspace_root(cli_value: Path | None = None) -> Path:
    """Resolve ``workspace_root`` for operator skills.

    Order: explicit ``--workspace`` CLI flag → ``workspace_root`` in
    ``~/.ralph/config.toml`` → default ``~/ralph-workspaces``. Returns an
    absolute, expanded path. Skills never read the env-var fallback
    (``RALPH_WORKSPACE``) directly — that knob is intentional executor-
    only territory; skill paths are an operator-facing surface and stay
    on the TOML / CLI rails.

    ``ConfigError`` from a malformed / unreadable / wrong-type
    ``~/.ralph/config.toml`` is re-raised as ``QueueWriterError`` so
    skills only need to catch one exception type from this module.
    """
    if cli_value is not None:
        return cli_value.expanduser().resolve()
    from ralph_executor.config import ConfigError
    from ralph_executor.user_config import read_workspace_root

    try:
        from_toml = read_workspace_root()
    except ConfigError as exc:
        raise QueueWriterError(str(exc)) from exc
    if from_toml is not None:
        return from_toml.expanduser().resolve()
    return _DEFAULT_WORKSPACE_ROOT.resolve()


def resolve_queue_repo(cli_value: str | None = None) -> str:
    """Resolve ``queue_repo`` for operator skills.

    Order: explicit ``--queue-repo`` CLI flag → ``queue_repo`` in
    ``~/.ralph/config.toml``. No silent default — without a queue URL the
    skill cannot do anything meaningful, so raise ``QueueWriterError``
    pointing the operator at the init command.

    ``ConfigError`` from a malformed / unreadable / wrong-type
    ``~/.ralph/config.toml`` is re-raised as ``QueueWriterError`` so
    skills only need to catch one exception type from this module.
    """
    if cli_value is not None:
        value = cli_value.strip()
        if not value:
            raise QueueWriterError("--queue-repo must be a non-empty string")
        return value
    from ralph_executor.config import ConfigError
    from ralph_executor.user_config import read_queue_repo

    try:
        from_toml = read_queue_repo()
    except ConfigError as exc:
        raise QueueWriterError(str(exc)) from exc
    if from_toml is None:
        raise QueueWriterError(
            "queue_repo not configured: pass --queue-repo or set "
            "'queue_repo' in ~/.ralph/config.toml (e.g. via `ralph-executor init`)"
        )
    return from_toml


DEFAULT_QUEUE_BRANCH = "ralph-queue"


def resolve_queue_branch(cli_value: str | None = None) -> str:
    """Resolve ``queue_branch`` for operator skills.

    Order: explicit CLI value → ``queue_branch`` in ``~/.ralph/config.toml``
    → default ``"ralph-queue"``. Unlike ``resolve_queue_repo`` there IS a
    silent default — every queue repo has a branch, and the default matches
    the spec.

    ``ConfigError`` from a malformed user TOML is re-raised as
    ``QueueWriterError`` so the skill surface stays single-typed.
    """
    if cli_value is not None:
        value = cli_value.strip()
        if not value:
            raise QueueWriterError("--queue-branch must be a non-empty string")
        if value == "HEAD" or value.startswith("refs/heads/"):
            raise QueueWriterError(
                f"--queue-branch must be a plain branch name (got {cli_value!r})"
            )
        return value
    from ralph_executor.config import ConfigError
    from ralph_executor.user_config import read_queue_branch

    try:
        from_toml = read_queue_branch()
    except ConfigError as exc:
        raise QueueWriterError(str(exc)) from exc
    if from_toml is None:
        return DEFAULT_QUEUE_BRANCH
    if from_toml == "HEAD" or from_toml.startswith("refs/heads/"):
        raise QueueWriterError(
            f"queue_branch in ~/.ralph/config.toml must be a plain branch name (got {from_toml!r})"
        )
    return from_toml


def is_path_in_head(repo: Path, rel_path: str) -> bool:
    """Return True if ``rel_path`` exists in the current ``HEAD`` tree.

    Used by ``ralph-cancel`` / ``ralph-promote`` to test idempotency
    against the COMMITTED git state rather than the filesystem. A
    previous invocation that staged the change (``git add`` succeeded)
    but failed the commit step (pre-commit hook, misconfigured
    user.email, etc.) leaves the file on disk while HEAD is unchanged
    — a subsequent invocation must re-run the full path, not return
    "already done" based on ``Path.exists()``.
    """
    ensure_git_repo(repo)
    try:
        subprocess.run(
            ["git", "cat-file", "-e", f"HEAD:{rel_path}"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return False
    return True


def read_path_from_head(repo: Path, rel_path: str) -> str | None:
    """Return the contents of ``rel_path`` at ``HEAD`` as text, or None
    if the path is not in HEAD.

    Used by ``ralph-promote`` to read the COMMITTED frontmatter so
    idempotency reflects what is actually on the queue branch, not what
    a previously-failed commit happened to leave on disk.
    """
    ensure_git_repo(repo)
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{rel_path}"],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError:
        return None
    return result.stdout


def commit_paths(
    repo: Path,
    paths: Iterable[Path],
    message: str,
) -> str:
    """Stage every path in ``paths`` and create a commit with ``message``.

    Returns the new HEAD SHA. Paths are taken relative to ``repo`` for
    ``git add``. Paths that no longer exist on disk are still passed to
    ``git add``, which stages the deletion if the path was tracked at
    HEAD — callers performing renames (delete-old + add-new) rely on
    this behaviour and pass the missing ``old_path`` alongside the
    present ``new_path``.
    """
    ensure_git_repo(repo)
    rel_paths: list[str] = []
    for raw in paths:
        path = raw if raw.is_absolute() else (repo / raw)
        try:
            rel = path.resolve().relative_to(repo.resolve())
        except ValueError as exc:
            raise QueueWriterError(f"path {path} is not inside repo {repo}") from exc
        rel_paths.append(str(rel).replace("\\", "/"))
    if not rel_paths:
        raise QueueWriterError("commit_paths called with no paths to stage")
    _run_git(repo, "add", "--", *rel_paths)
    _run_git(repo, "commit", "-m", message)
    return _run_git(repo, "rev-parse", "HEAD")


def push(repo: Path, branch: str) -> None:
    """Push ``branch`` to ``origin``."""
    _run_git(repo, "push", "origin", branch)


# ----------------------------------------------------------------------
# PBI discovery
# ----------------------------------------------------------------------


def find_pbi_directory(repo: Path, pbi_id: str) -> Path | None:
    """Return the absolute path of ``pbi_id`` under ``.ralph/``, or None.

    Search order matches the Ralph lifecycle precedence (an actively-
    worked PBI in ``current/`` should win over a stale duplicate in
    ``inbox/``):

    1. current/
    2. inbox/
    3. pending-pr/
    4. blocked/
    5. done/
    6. archive/
    """
    base = repo / RALPH_DIR_NAME
    for folder in QUEUE_STATE_FOLDERS:
        candidate = base / folder / pbi_id
        if candidate.is_dir():
            return candidate
    return None


# ----------------------------------------------------------------------
# Frontmatter helpers
# ----------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, str]:
    # Strip both ``\r`` and ``\n`` so Windows CRLF-encoded files (and
    # any file checked out under ``core.autocrlf=true``) still match the
    # ``---`` fence — ``rstrip("\n")`` alone leaves ``"---\r"`` and
    # rejects the file as missing a frontmatter fence.
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise QueueWriterError("file does not start with a YAML frontmatter fence ('---')")
    for idx in range(1, len(lines)):
        if lines[idx].rstrip("\r\n") == "---":
            frontmatter = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            return frontmatter, body
    raise QueueWriterError("frontmatter block is not closed by a second '---'")


def flatten_history_field(value: str) -> str:
    """Collapse embedded newlines/CRs so a multi-line ``--note`` or
    ``detail`` does not corrupt the one-entry-per-block format of
    HISTORY.md (each entry is a contiguous run of ``- key: value`` lines
    terminated by a blank line).
    """
    return value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def parse_frontmatter_text(text: str, *, source: str = "<text>") -> tuple[dict[str, Any], str]:
    """Split ``text`` into ``(frontmatter_dict, body_text)``.

    Same parsing semantics as ``read_frontmatter`` but operates on a
    string (e.g. one returned by ``read_path_from_head``). ``source``
    appears in error messages so the caller can attribute parse failures.
    """
    frontmatter_text, body = _split_frontmatter(text)
    try:
        parsed: Any = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise QueueWriterError(f"frontmatter in {source} is not valid YAML: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise QueueWriterError(
            f"frontmatter in {source} must be a YAML mapping, got {type(parsed).__name__}"
        )
    return dict(parsed), body


def read_frontmatter(entry_file: Path) -> tuple[dict[str, Any], str]:
    """Read ``entry_file`` and return ``(frontmatter_dict, body_text)``.

    Raises ``QueueWriterError`` if the file lacks a YAML frontmatter
    block or the YAML does not parse as a mapping.
    """
    if not entry_file.is_file():
        raise QueueWriterError(f"entry file does not exist: {entry_file}")
    text = entry_file.read_text(encoding="utf-8")
    return parse_frontmatter_text(text, source=entry_file.name)


def write_frontmatter(
    entry_file: Path,
    frontmatter: Mapping[str, Any],
    body: str,
) -> None:
    """Write ``frontmatter`` + ``body`` back to ``entry_file``.

    The frontmatter is rendered with ``yaml.safe_dump`` using
    ``sort_keys=False`` so the key order matches the input order as much
    as possible.
    """
    rendered = yaml.safe_dump(
        dict(frontmatter),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    rendered = rendered.rstrip("\n")
    output = f"---\n{rendered}\n---\n{body}"
    entry_file.write_text(output, encoding="utf-8")


# ----------------------------------------------------------------------
# HISTORY.md
# ----------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def append_history(
    pbi_dir: Path,
    *,
    actor: str,
    action: str,
    detail: str,
) -> None:
    """Append a structured entry to ``<pbi_dir>/HISTORY.md``.

    Each entry has a leading ``---`` separator so entries are visually
    distinct and machine-parseable. Form::

        ---
        - timestamp: <iso8601>
        - actor: <actor>
        - action: <action>
        - detail: <detail>
    """
    history = pbi_dir / "HISTORY.md"
    # Flatten embedded newlines in actor/action/detail so a multi-line
    # ``--note`` from a shell with escape expansion (``$'line1\nline2'``)
    # does not corrupt the one-entry-per-block format of HISTORY.md (the
    # downstream dedup check and human readers both rely on each entry
    # being a contiguous run of ``- key: value`` lines).
    entry = (
        "---\n"
        f"- timestamp: {_now_iso()}\n"
        f"- actor: {flatten_history_field(actor)}\n"
        f"- action: {flatten_history_field(action)}\n"
        f"- detail: {flatten_history_field(detail)}\n"
    )
    with history.open("a", encoding="utf-8") as fh:
        fh.write(entry)


# ----------------------------------------------------------------------
# Frontmatter merge convenience
# ----------------------------------------------------------------------


def update_frontmatter_fields(
    entry_file: Path,
    updates: MutableMapping[str, Any],
) -> dict[str, Any]:
    """Read ``entry_file``, apply ``updates`` to its frontmatter, write back.

    Returns the merged frontmatter dict after the write. ``updated_at`` is
    stamped automatically if ``updates`` does not already set it.
    """
    frontmatter, body = read_frontmatter(entry_file)
    frontmatter.update(updates)
    frontmatter["updated_at"] = updates.get("updated_at", _now_iso())
    write_frontmatter(entry_file, frontmatter, body)
    return frontmatter
