"""Instance identity helpers — sanitisation, validation, hostname default."""

from __future__ import annotations

import re
import socket

INSTANCE_ID_REGEX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class InstanceIdError(ValueError):
    """Raised when an instance_id cannot be validated."""


def sanitize_instance_id(value: str) -> str:
    """Lowercase; replace any char outside [a-z0-9_-] with `-`; collapse
    runs; strip leading/trailing dashes. Pure — never raises."""
    lowered = value.strip().lower()
    swapped = re.sub(r"[^a-z0-9_-]", "-", lowered)
    collapsed = re.sub(r"-+", "-", swapped)
    return collapsed.strip("-")


def validate_instance_id(value: str) -> str:
    """Return value unchanged when it matches INSTANCE_ID_REGEX; else raise."""
    if not value:
        raise InstanceIdError(
            "instance_id must be set (CLI / env / TOML / hostname default)"
        )
    if not INSTANCE_ID_REGEX.match(value):
        raise InstanceIdError(
            f"instance_id {value!r} does not match {INSTANCE_ID_REGEX.pattern}"
        )
    return value


def default_instance_id() -> str:
    """Return the sanitised hostname for use as the default instance_id."""
    return sanitize_instance_id(socket.gethostname())
