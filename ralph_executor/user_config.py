"""Per-user configuration file for ralph-executor.

Lives at ``~/.ralph/config.toml`` and holds *per-machine* knobs:

  ``ralph_home``         -- directory under which every per-repo ralph
                            workspace lives (referenced by ``--workspace NAME``)
  ``skills_root``        -- source ``skills/`` tree containing
                            ``pr-<host>/`` and ``workitem-fetch-<host>/``
  ``claude_skills_dir``  -- where staged ``pr/`` + ``workitem-fetch/`` end up
                            (defaults to ``~/.claude/skills``)

Written by ``ralph-executor init`` and read at runtime by the workspace
resolution path and ``host_select.prepare_host_environment``. The
matching environment variables (``$RALPH_HOME``, ``$RALPH_SKILLS_ROOT``,
``$RALPH_CLAUDE_SKILLS_DIR``) always override the file for the daemon /
systemd escape-hatch case.

Layout intentionally minimal: this file holds *user* preferences
(where things live on THIS machine), not *project* knobs (which live
in ``<repo>/.ralph/config.toml`` per Plan-7 layering).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from ralph_executor.config import ConfigError

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


def read_ralph_home() -> Path | None:
    """Return the ``ralph_home`` value from the user config, or None.

    Errors on malformed TOML / wrong-type value / unreadable file.
    """
    return _read_path_key("ralph_home")


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
    rewritten output — same trade-off as the original
    ``write_ralph_home`` rewrite path, but now key-merging instead of
    whole-file-clobber so adding a new key (``queue_repo``) doesn't lose
    the existing one (``ralph_home``).
    """
    cfg_file = user_config_path()
    cfg_file.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_user_config()
    existing.update(updates)

    lines: list[str] = []
    for key, val in existing.items():
        rendered = _render_toml_value(val)
        if rendered is None:
            continue
        lines.append(f"{key} = {rendered}")
    cfg_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg_file


def write_ralph_home(value: Path) -> Path:
    """Persist ``ralph_home`` to ``~/.ralph/config.toml``.

    Merges with existing keys so subsequent writes of other knobs
    (``queue_repo``, ``skills_root``, ...) survive. Returns the path
    that was written so callers can echo it back to the user.
    """
    return _write_user_config({"ralph_home": str(value)})


def write_queue_repo(url: str) -> Path:
    """Persist ``queue_repo`` to ``~/.ralph/config.toml``.

    Merges with existing keys so ``ralph_home`` (and any future user-
    level knobs) are preserved.
    """
    return _write_user_config({"queue_repo": url})
