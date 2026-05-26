"""Normalised work-item JSON schema.

Both the GitHub fetcher (Phase 1) and the ADO fetcher (Phase 2) emit this
exact shape. ``ralph-add``'s orchestrator parses it without caring which
host produced it.
"""

from __future__ import annotations

from typing import Literal, TypedDict

PBIType = Literal["feature", "bug"]
Severity = Literal["critical", "high", "normal", "low"]
SourceHost = Literal["github", "ado"]


class AttachmentJson(TypedDict):
    """One attached file. ``content_base64`` is the raw bytes encoded."""

    name: str
    url: str
    content_base64: str


class WorkItemJson(TypedDict):
    """The normalised work-item document emitted by every fetcher."""

    id: str
    type: PBIType
    severity: Severity
    title: str
    body_markdown: str
    acceptance_markdown: str
    repro_steps_markdown: str
    attachments: list[AttachmentJson]
    child_ids: list[str]
    parent_id: str | None
    source_url: str
    source_host: SourceHost
    raw: dict[str, object]


REQUIRED_KEYS: tuple[str, ...] = (
    "id",
    "type",
    "severity",
    "title",
    "body_markdown",
    "acceptance_markdown",
    "repro_steps_markdown",
    "attachments",
    "child_ids",
    "parent_id",
    "source_url",
    "source_host",
    "raw",
)
ALLOWED_TYPES: tuple[str, ...] = ("feature", "bug")
ALLOWED_SEVERITIES: tuple[str, ...] = ("critical", "high", "normal", "low")
ALLOWED_HOSTS: tuple[str, ...] = ("github", "ado")


def validate(document: object) -> list[str]:
    """Return a list of validation errors. Empty list means valid."""
    errors: list[str] = []
    if not isinstance(document, dict):
        return [f"document must be a JSON object, got {type(document).__name__}"]

    for key in REQUIRED_KEYS:
        if key not in document:
            errors.append(f"missing required key: {key}")
    if errors:
        return errors

    if document["type"] not in ALLOWED_TYPES:
        errors.append(f"type must be one of {ALLOWED_TYPES}, got {document['type']!r}")
    if document["severity"] not in ALLOWED_SEVERITIES:
        errors.append(f"severity must be one of {ALLOWED_SEVERITIES}, got {document['severity']!r}")
    if document["source_host"] not in ALLOWED_HOSTS:
        errors.append(
            f"source_host must be one of {ALLOWED_HOSTS}, got {document['source_host']!r}"
        )
    if not isinstance(document["attachments"], list):
        errors.append("attachments must be a list")
    if not isinstance(document["child_ids"], list):
        errors.append("child_ids must be a list")
    parent_id = document["parent_id"]
    if parent_id is not None and not isinstance(parent_id, str):
        errors.append("parent_id must be a string or null")
    return errors
