"""Per-user configuration file for ralph-executor.

Lives at ``~/.ralph/config.toml`` and currently holds a single key:

  ``ralph_home``  --  directory under which every per-repo ralph
                      workspace lives (referenced by ``--workspace NAME``).

Written by ``ralph-executor init`` and read at runtime by the workspace
resolution path. ``$RALPH_HOME`` environment variable always overrides
the file for the daemon / systemd escape-hatch case.

Layout intentionally minimal: this file holds *user* preferences (where
to put my ralph clones), not *project* knobs (which already live in
``<repo>/.ralph/config.toml`` per Plan-7 layering).
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


def read_ralph_home() -> Path | None:
    """Return the ``ralph_home`` value from the user config file, or None
    if the file does not exist OR the key is absent.

    Malformed TOML / wrong-type value / unreadable file raises
    ``ConfigError`` so a broken file surfaces a clear error rather than
    silently looking like "not set".
    """
    cfg_file = user_config_path()
    if not cfg_file.is_file():
        return None
    try:
        with cfg_file.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{cfg_file}: invalid TOML: {exc}") from exc
    except OSError as exc:
        # Catches permission denied, IO errors, and the is_file/open
        # race (file deleted between the existence check and open).
        # Both callers only catch ConfigError; without this wrap, a
        # permission-denied user-config file would surface as a raw
        # traceback rather than the clean operator-facing error the
        # docstring promises.
        raise ConfigError(f"{cfg_file}: cannot read file: {exc}") from exc
    raw = data.get("ralph_home")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(
            f"{cfg_file}: ralph_home must be a non-empty string, got {type(raw).__name__}"
        )
    return Path(raw.strip()).expanduser()


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


def write_ralph_home(value: Path) -> Path:
    """Persist ``ralph_home`` to ``~/.ralph/config.toml``.

    Overwrites the whole file (the file currently holds only this single
    key; when more keys are added later this becomes a merge). Returns
    the path that was written so callers can echo it back to the user.
    """
    cfg_file = user_config_path()
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    escaped = _toml_escape_basic_string(str(value))
    cfg_file.write_text(f'ralph_home = "{escaped}"\n', encoding="utf-8")
    return cfg_file
