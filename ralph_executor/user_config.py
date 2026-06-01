"""Per-user configuration file for ralph-executor.

Lives at ``~/.ralph/config.toml`` and holds *per-machine* knobs:

  ``workspace_root``     -- directory under which queue clone and every
                            target clone are materialised
                            (``<workspace_root>/queue/``,
                            ``<workspace_root>/clones/<owner>/<name>/``)
  ``queue_repo``         -- HTTPS URL of the queue repo holding ``.ralph/``
  ``queue_branch``       -- branch on the queue repo (default ``ralph-queue``)
  ``skills_root``        -- source ``skills/`` tree containing ``pr-<host>/``
  ``claude_skills_dir``  -- where staged ``pr/`` ends up
                            (defaults to ``~/.claude/skills``)

Written by ``ralph-executor init`` and read at runtime by
``ralph_executor.config.load_config`` and
``host_select.prepare_host_environment``. The matching environment
variables (``$RALPH_WORKSPACE``, ``$RALPH_SKILLS_ROOT``,
``$RALPH_CLAUDE_SKILLS_DIR``) always override the file for the daemon /
systemd escape-hatch case.

Layout intentionally minimal: this file holds *user* preferences
(where things live on THIS machine). There is no project-level TOML —
``<repo>/.ralph/config.toml`` in a target clone is ignored with a
WARNING at iteration time.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

from ralph_executor.config import ConfigError

log = logging.getLogger(__name__)

# Path is constructed dynamically (``Path.home()`` is called per access)
# so monkeypatched HOME / USERPROFILE in tests sees the override without
# the module needing reimport.


def user_config_path() -> Path:
    """Return the absolute path to ``~/.ralph/config.toml``."""
    return Path.home() / ".ralph" / "config.toml"


def _load_user_config() -> dict[str, object]:
    """Read the user config TOML, returning {} if absent.

    Malformed TOML / wrong-type value / unreadable file raises
    ``ConfigError`` so a broken file surfaces a clear error rather than
    silently looking like "not set".
    """
    cfg_file = user_config_path()
    if not cfg_file.is_file():
        return {}
    try:
        with cfg_file.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{cfg_file}: invalid TOML: {exc}") from exc
    except OSError as exc:
        # Catches permission denied, IO errors, and the is_file/open
        # race (file deleted between the existence check and open).
        # Callers only catch ConfigError; without this wrap, a permission
        # error would surface as a raw traceback rather than the clean
        # operator-facing error the docstring promises.
        raise ConfigError(f"{cfg_file}: cannot read file: {exc}") from exc
    return data


def _read_path_key(key: str) -> Path | None:
    """Read a Path-valued key from the user config; return None if absent."""
    data = _load_user_config()
    raw = data.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(
            f"{user_config_path()}: {key} must be a non-empty string, got {type(raw).__name__}"
        )
    return Path(raw.strip()).expanduser()


def read_workspace_root() -> Path | None:
    """Return the ``workspace_root`` value from the user config, or None.

    The operator-facing skills resolve their queue clone path relative to
    ``workspace_root`` — same knob the executor consumes via
    ``ralph_executor.config``. Exposing it here lets skills read the
    user-level TOML without depending on the executor's full config
    loader (which has many env-var side effects irrelevant to skill use).
    """
    return _read_path_key("workspace_root")


def read_skills_root() -> Path | None:
    """Return the ``skills_root`` value from the user config, or None.

    ``host_select.prepare_host_environment`` falls back to this between
    its explicit kwarg / env-var layer and the source-checkout default.
    """
    return _read_path_key("skills_root")


def read_claude_skills_dir() -> Path | None:
    """Return the ``claude_skills_dir`` value from the user config, or None.

    ``host_select.prepare_host_environment`` falls back to this between
    its explicit kwarg / env-var layer and the ``~/.claude/skills`` default.
    """
    return _read_path_key("claude_skills_dir")


def read_queue_repo() -> str | None:
    """Return the ``queue_repo`` HTTPS URL from the user config, or None.

    The queue-repo-split architecture stores ``queue_repo`` at the
    user-level (per-machine, not per-repo) because a single operator runs
    against a single queue. ``cmd_init`` writes it; downstream consumers
    read it via this helper.
    """
    data = _load_user_config()
    raw = data.get("queue_repo")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(
            f"{user_config_path()}: queue_repo must be a non-empty string, got {type(raw).__name__}"
        )
    return raw.strip()


def read_queue_branch() -> str | None:
    """Return the ``queue_branch`` value from the user config, or None.

    Mirrors ``read_queue_repo`` for the matching knob. The executor's
    ``load_config`` reads project TOML first; this helper is the
    operator-config fallback consulted by operator skills via
    ``scripts.queue_writer.resolve_queue_branch``.
    """
    data = _load_user_config()
    raw = data.get("queue_branch")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(
            f"{user_config_path()}: queue_branch must be a non-empty string, "
            f"got {type(raw).__name__}"
        )
    return raw.strip()


def read_instance_id() -> str | None:
    """Return the ``instance_id`` value from the user config, or None.

    Multi-ralph (Scope 1) per-instance identity. Mirrors
    ``read_queue_branch`` / ``read_queue_repo`` for the matching knob.
    ``ralph_executor.config.load_config`` consults this layer as part of
    the CLI > env > TOML > sanitised-hostname resolution chain; an empty
    / whitespace-only value is treated as malformed here (defence in
    depth — the resolver's validator would also reject it).
    """
    data = _load_user_config()
    raw = data.get("instance_id")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(
            f"{user_config_path()}: instance_id must be a non-empty string, "
            f"got {type(raw).__name__}"
        )
    return raw.strip()


def _toml_escape_basic_string(value: str) -> str:
    """Escape a string for use as a TOML basic-string value.

    TOML basic strings forbid bare control characters U+0000–U+001F and
    U+007F in addition to the obvious backslash + quote. A path
    containing any of those would produce a syntactically invalid TOML
    file that round-trips as a TOMLDecodeError on the next read.
    """
    parts: list[str] = []
    for ch in value:
        if ch == "\\":
            parts.append("\\\\")
        elif ch == '"':
            parts.append('\\"')
        elif ch == "\n":
            parts.append("\\n")
        elif ch == "\r":
            parts.append("\\r")
        elif ch == "\t":
            parts.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            parts.append(f"\\u{ord(ch):04X}")
        else:
            parts.append(ch)
    return "".join(parts)


def _render_toml_value(value: object) -> str | None:
    """Render a Python value as a TOML literal, or None if unsupported.

    Only the scalar types our user_config actually persists are handled —
    string, bool, int, float. Anything else (lists, tables, nested dicts)
    is dropped on the floor by the merge writer; user_config does not
    persist non-scalar values today.
    """
    if isinstance(value, str):
        return f'"{_toml_escape_basic_string(value)}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _write_user_config(updates: dict[str, object]) -> Path:
    """Merge ``updates`` into ``~/.ralph/config.toml`` and rewrite it.

    Reads the existing file (if any), updates the given keys, and writes
    back. Unknown / non-scalar pre-existing keys are dropped from the
    rewritten output: key-merging (not whole-file clobber) preserves
    co-existing user-level knobs (``workspace_root`` / ``queue_repo`` /
    ``queue_branch`` / ``skills_root`` / ``claude_skills_dir``) when any
    one of them is rewritten.
    """
    cfg_file = user_config_path()
    cfg_file.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_user_config()
    existing.update(updates)

    lines: list[str] = []
    for key, val in existing.items():
        rendered = _render_toml_value(val)
        if rendered is None:
            # ``_render_toml_value`` only handles str / bool / int / float.
            # A TOML datetime / inline table / array (operator-edited)
            # would silently disappear from the rewritten file. Log
            # loudly so the operator can move the value back manually.
            log.warning(
                "user-config rewrite dropped key %r (value type %s not "
                "serialisable by _render_toml_value); re-add it manually "
                "to %s",
                key,
                type(val).__name__,
                cfg_file,
            )
            continue
        lines.append(f"{key} = {rendered}")
    cfg_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg_file


def write_queue_repo(url: str) -> Path:
    """Persist ``queue_repo`` to ``~/.ralph/config.toml``.

    Merges with existing keys so ``workspace_root`` (and any future
    user-level knobs) are preserved.
    """
    return _write_user_config({"queue_repo": url})


def write_queue_branch(branch: str) -> Path:
    """Persist ``queue_branch`` to ``~/.ralph/config.toml``.

    Merges with existing keys so ``queue_repo`` / ``workspace_root`` survive.
    """
    return _write_user_config({"queue_branch": branch})


def write_instance_id(value: str) -> Path:
    """Persist ``instance_id`` to ``~/.ralph/config.toml``.

    Merges with existing keys so ``queue_repo`` / ``queue_branch`` /
    ``workspace_root`` survive. Used by ``ralph-executor init`` (Task 4)
    to write the operator-chosen ``instance_id`` once at setup time.
    """
    return _write_user_config({"instance_id": value})


def write_workspace_root(value: Path) -> Path:
    """Persist ``workspace_root`` to ``~/.ralph/config.toml``.

    Merges with existing keys so ``queue_repo`` / ``queue_branch`` /
    other user-level knobs survive. Mirrors ``write_queue_repo`` /
    ``write_queue_branch`` and is the single write path used by
    ``ralph-executor init`` for the workspace_root prompt.
    """
    return _write_user_config({"workspace_root": str(value)})


def _warn_stale_ralph_home_in_user_config() -> None:
    """Emit a one-time WARNING if ``~/.ralph/config.toml`` still has ``ralph_home``.

    Called once at the top of ``load_config``. The key is no longer read
    anywhere — silent removal would leave operators wondering why their
    ``--workspace NAME`` invocations stopped resolving. The WARNING points
    at the file path so the operator can delete the stale line.
    """
    cfg_file = user_config_path()
    if not cfg_file.is_file():
        return
    try:
        with cfg_file.open("rb") as fh:
            data = tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError):
        # Malformed / unreadable user TOML surfaces its own ConfigError
        # via _load_user_config when read for real keys; suppress here
        # so the warning helper never crashes startup.
        return
    if "ralph_home" in data:
        log.warning(
            "%s: 'ralph_home' key is no longer supported and is ignored. "
            "Delete the line; the executor uses 'workspace_root' instead.",
            cfg_file,
        )
