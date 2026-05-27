"""Shared helpers for skills that mutate the ``ralph-queue`` branch.

The four supervisor skills (`ralph-add`, `ralph-cancel`, `ralph-promote`,
`ralph-triage`) all follow the same pattern: validate the target repo,
switch onto `ralph-queue`, mutate one or more PBI directories, commit,
optionally push. This module is the single source of truth for that
pattern so the skills stay consistent and the git-related test surface
has a single home.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

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


def checkout_queue_branch(repo: Path, branch: str) -> None:
    """Switch the working tree onto ``branch``.

    If the branch exists locally, ``git checkout <branch>``. Otherwise, if
    ``origin/<branch>`` exists, create a tracking branch. Otherwise raise
    ``QueueWriterError`` -- the caller must run the ``ralph-queue`` setup
    runbook first (Plan 2).
    """
    ensure_git_repo(repo)
    local = _run_git(repo, "branch", "--list", branch)
    if local.strip():
        _run_git(repo, "checkout", branch)
        return
    remote = _run_git(repo, "branch", "-r", "--list", f"origin/{branch}")
    if remote.strip():
        _run_git(repo, "checkout", "-b", branch, f"origin/{branch}")
        return
    raise QueueWriterError(
        f"branch {branch!r} not found locally or as origin/{branch}; "
        "run docs/runbooks/ralph-queue-setup.md first"
    )


def commit_paths(
    repo: Path,
    paths: Iterable[Path],
    message: str,
) -> str:
    """Stage every path in ``paths`` and create a commit with ``message``.

    Returns the new HEAD SHA. Paths are taken relative to ``repo`` for
    ``git add``. Missing paths raise ``QueueWriterError`` before any git
    command runs.
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
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        raise QueueWriterError("file does not start with a YAML frontmatter fence ('---')")
    for idx in range(1, len(lines)):
        if lines[idx].rstrip("\n") == "---":
            frontmatter = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            return frontmatter, body
    raise QueueWriterError("frontmatter block is not closed by a second '---'")


def read_frontmatter(entry_file: Path) -> tuple[dict[str, Any], str]:
    """Read ``entry_file`` and return ``(frontmatter_dict, body_text)``.

    Raises ``QueueWriterError`` if the file lacks a YAML frontmatter
    block or the YAML does not parse as a mapping.
    """
    if not entry_file.is_file():
        raise QueueWriterError(f"entry file does not exist: {entry_file}")
    text = entry_file.read_text(encoding="utf-8")
    frontmatter_text, body = _split_frontmatter(text)
    try:
        parsed: Any = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise QueueWriterError(
            f"frontmatter in {entry_file.name} is not valid YAML: {exc}"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise QueueWriterError(
            f"frontmatter in {entry_file.name} must be a YAML mapping, got {type(parsed).__name__}"
        )
    return dict(parsed), body


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
    entry = (
        "---\n"
        f"- timestamp: {_now_iso()}\n"
        f"- actor: {actor}\n"
        f"- action: {action}\n"
        f"- detail: {detail}\n"
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
