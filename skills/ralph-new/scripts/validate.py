"""Pure-function validators for ralph-new field inputs.

Each ``validate_*`` returns ``None`` on success or a human-readable error
string on failure. Caller is responsible for choosing how to surface the
error (prompt re-ask, stderr + exit 2, etc.).
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Iterable

# Slug + PBI-id syntax: 2–80 chars, starts and ends with alphanumeric,
# uppercase + digits + hyphens only.
_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]*[A-Z0-9]$")
_MIN_ID_LEN = 2
_MAX_ID_LEN = 80

_ALLOWED_SEVERITIES: tuple[str, ...] = ("critical", "high", "normal", "low")
_ALLOWED_TYPES: tuple[str, ...] = ("bug", "feature")


def validate_target_repo(value: str) -> str | None:
    """Return None if ``value`` is a valid HTTPS owner/name URL."""
    if not isinstance(value, str) or not value:
        return "target_repo must be a non-empty string"
    if "\n" in value or "\r" in value:
        return f"target_repo URL must not contain newline or carriage return: {value!r}"
    if not value.startswith("https://"):
        return f"target_repo must be an HTTPS URL, got {value!r}"
    parsed = urllib.parse.urlparse(value)
    if not parsed.netloc:
        return f"target_repo URL has no host: {value!r}"
    segments = [p for p in parsed.path.split("/") if p]
    if len(segments) < 2:
        return f"target_repo URL must include owner + name path segments: {value!r}"
    return None


def validate_pbi_id(value: str) -> str | None:
    """Return None if ``value`` is a syntactically valid PBI id / slug."""
    if not isinstance(value, str) or not value:
        return "PBI id is empty"
    # Regex enforces min length 2 (first + last alnum) so we check it before
    # the max-length guard. This keeps single-character ids reported as a
    # regex error rather than a length error.
    if not _ID_PATTERN.fullmatch(value):
        return (
            f"PBI id {value!r} must match regex {_ID_PATTERN.pattern} "
            f"(uppercase A-Z + digits + hyphens; no leading/trailing hyphen)"
        )
    n = len(value)
    if n > _MAX_ID_LEN:
        return f"PBI id length {n} not in {_MIN_ID_LEN}..{_MAX_ID_LEN}: {value!r}"
    return None


def validate_severity(value: str) -> str | None:
    if value not in _ALLOWED_SEVERITIES:
        return f"severity {value!r} not in {_ALLOWED_SEVERITIES}"
    return None


def validate_type(value: str) -> str | None:
    if value == "pr-feedback":
        return (
            "type 'pr-feedback' is sweep-generated only; ralph-new does not author pr-feedback PBIs"
        )
    if value not in _ALLOWED_TYPES:
        return f"type {value!r} not in {_ALLOWED_TYPES}"
    return None


def validate_depends_on_syntax(ids: Iterable[str]) -> str | None:
    """Return None if every id passes ``validate_pbi_id``."""
    for dep_id in ids:
        err = validate_pbi_id(dep_id)
        if err is not None:
            return f"depends_on entry {dep_id!r}: {err}"
    return None
