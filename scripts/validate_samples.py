"""Validate sample PBI directories against the canonical frontmatter schema.

The schema is defined in
``docs/superpowers/plans/2026-05-24-00-orchestrator.md`` (Cross-plan
integration points) and mirrored here for runtime enforcement.

Cross-plan reconciliation note #3: type is derived from the frontmatter
``type`` field, NOT from the directory name prefix. Type-prefixed sample
directory names (e.g. ``feature-WI-1234``) are accepted as-is; the validator
does not require or validate any naming prefix.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# NOTE: these schema constants mirror the canonical definitions in
# docs/superpowers/plans/2026-05-24-00-orchestrator.md (Cross-plan integration
# points). When Plan 7 lands ralph_executor/types.py with the PBIType /
# PBIStatus / Severity Literals, replace the frozensets here with
# `set(get_args(<Literal>))` imports so the validator and the rest of the
# executor share one source of truth.
REQUIRED_FRONTMATTER_FIELDS: tuple[str, ...] = (
    "id",
    "type",
    "status",
    "severity",
    "attempts",
    "created_at",
    "updated_at",
    "target_repo",
)

ALLOWED_TYPES: frozenset[str] = frozenset({"feature", "bug", "pr-feedback"})
ALLOWED_STATUSES: frozenset[str] = frozenset(
    {"inbox", "current", "pending-pr", "done", "blocked", "archive"}
)
ALLOWED_SEVERITIES: frozenset[str] = frozenset({"critical", "high", "normal", "low"})

ENTRY_FILE_BY_TYPE: dict[str, str] = {
    "feature": "PBI.md",
    "bug": "BUG.md",
    "pr-feedback": "FEEDBACK.md",
}

REQUIRED_SIBLINGS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "feature": ("PBI.md", "PLAN.md", "HISTORY.md"),
    "bug": ("BUG.md", "REPRODUCE.md", "HISTORY.md"),
    "pr-feedback": ("FEEDBACK.md", "PR-LINK.md", "ORIGINAL.md", "HISTORY.md"),
}

# All candidate entry filenames for exhaustive search.
_ALL_ENTRY_FILES: tuple[str, ...] = ("PBI.md", "BUG.md", "FEEDBACK.md")

# Maps entry filename back to the expected frontmatter type value.
_TYPE_BY_ENTRY_FILE: dict[str, str] = {v: k for k, v in ENTRY_FILE_BY_TYPE.items()}


def split_frontmatter(text: str) -> str | None:
    """Return the frontmatter YAML string if the file starts with a YAML block.

    Returns ``None`` if no leading ``---`` fence is present.  The body after
    the closing fence is not returned; it is never consumed by callers.

    Fence lines must be EXACTLY ``---`` (no leading or trailing whitespace).
    An indented ``  ---`` line is treated as YAML content (e.g. inside a block
    scalar), not as a fence — earlier ``.strip()``-based matching would
    prematurely terminate the frontmatter on such lines.

    Public API: Plan 4 (``ralph-status`` skill) imports this for reading PBI
    frontmatter from the live queue. See orchestrator reconciliation #6.
    """
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    for idx in range(1, len(lines)):
        if lines[idx] == "---":
            return "\n".join(lines[1:idx])
    return None


def _validate_target_repo(value: object) -> str | None:
    """Return error message or None if value is a valid target_repo URL.

    Accepts HTTPS URLs with at least 2 non-empty path segments (owner + name).
    Tolerates trailing ``.git`` via the generic path check. No host whitelist —
    works for GitHub (``https://github.com/owner/name``) and Azure DevOps
    (``https://dev.azure.com/org/proj/_git/repo``) alike.
    """
    if not isinstance(value, str):
        return f"target_repo must be a string, got {type(value).__name__}"
    if "\n" in value or "\r" in value:
        return f"target_repo URL must not contain newline or carriage return: {value!r}"
    if not value.startswith("https://"):
        return f"target_repo must be an HTTPS URL, got {value!r}"
    parsed = urllib.parse.urlparse(value)
    if not parsed.netloc:
        return f"target_repo URL has no host: {value!r}"
    path_segments = [p for p in parsed.path.split("/") if p]
    if len(path_segments) < 2:
        return f"target_repo URL must include owner + name path: {value!r}"
    return None


def _validate_frontmatter(frontmatter: Mapping[str, Any]) -> list[str]:
    """Validate frontmatter fields; return a list of error strings."""
    errors: list[str] = []

    # Treat explicit null (YAML `field: null` / `field: ~`) the same as an
    # absent key. Without this, `id: null` would pass the presence check;
    # all per-field validators below short-circuit on None, so a fully-null
    # frontmatter would validate clean.
    missing = [
        f for f in REQUIRED_FRONTMATTER_FIELDS if f not in frontmatter or frontmatter[f] is None
    ]
    if missing:
        errors.append(f"missing frontmatter fields: {sorted(missing)}")

    pbi_type = frontmatter.get("type")
    if pbi_type is not None and pbi_type not in ALLOWED_TYPES:
        errors.append(f"type={pbi_type!r} not in allowed set {sorted(ALLOWED_TYPES)}")

    status = frontmatter.get("status")
    if status is not None and status not in ALLOWED_STATUSES:
        errors.append(f"status={status!r} not in allowed set {sorted(ALLOWED_STATUSES)}")

    severity = frontmatter.get("severity")
    if severity is not None and severity not in ALLOWED_SEVERITIES:
        errors.append(f"severity={severity!r} not in allowed set {sorted(ALLOWED_SEVERITIES)}")

    attempts = frontmatter.get("attempts")
    # bool is a subclass of int in Python; YAML `attempts: true` would parse to
    # True and pass an isinstance(..., int) check. Exclude bools explicitly so
    # `attempts: true` is rejected as the wrong type.
    if attempts is not None and (isinstance(attempts, bool) or not isinstance(attempts, int)):
        errors.append(f"attempts must be int, got {type(attempts).__name__}")
    elif isinstance(attempts, int) and not isinstance(attempts, bool) and attempts < 0:
        errors.append(f"attempts must be >= 0, got {attempts}")

    for field in ("created_at", "updated_at"):
        value = frontmatter.get(field)
        if value is None:
            continue
        if isinstance(value, datetime):
            continue
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value)
            except ValueError:
                errors.append(f"{field}={value!r} is not a valid ISO-8601 datetime")
        else:
            errors.append(
                f"{field} must be a datetime or ISO-8601 string, got {type(value).__name__}"
            )

    # depends_on is OPTIONAL — list of PBI-id strings.
    raw_deps = frontmatter.get("depends_on")
    if raw_deps is not None:
        if not isinstance(raw_deps, list):
            errors.append(
                f"depends_on must be a list of PBI-id strings, got {type(raw_deps).__name__}"
            )
        else:
            for i, dep in enumerate(raw_deps):
                if not isinstance(dep, str) or not dep.strip():
                    errors.append(
                        f"depends_on[{i}] must be a non-empty string, "
                        f"got {type(dep).__name__}={dep!r}"
                    )

    pbi_id = frontmatter.get("id")
    if pbi_id is not None and not isinstance(pbi_id, str):
        errors.append(f"id must be a string, got {type(pbi_id).__name__}")
    elif isinstance(pbi_id, str) and not pbi_id.strip():
        errors.append("id must be a non-empty string")

    target_repo = frontmatter.get("target_repo")
    if target_repo is not None:
        err = _validate_target_repo(target_repo)
        if err is not None:
            errors.append(err)

    return errors


def validate_sample(sample_dir: Path) -> list[str]:
    """Validate a single sample PBI directory.

    Returns a list of error strings. An empty list means the sample is valid.

    Type is derived from the frontmatter ``type`` field (reconciliation #3),
    not the directory name. The directory name may carry any prefix or none.
    """
    if not sample_dir.is_dir():
        return [f"not a directory: {sample_dir}"]

    # Step 1: find candidate entry files.
    present = [f for f in _ALL_ENTRY_FILES if (sample_dir / f).is_file()]
    if len(present) == 0:
        return ["no entry file found; expected one of: " + ", ".join(_ALL_ENTRY_FILES)]
    if len(present) > 1:
        return [f"multiple entry files found: {present}"]

    entry_file_name = present[0]
    entry_file = sample_dir / entry_file_name

    # Step 2: read the entry file.
    try:
        text = entry_file.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"failed to read {entry_file_name}: {exc}"]

    # Step 3: require a leading YAML frontmatter fence.
    split = split_frontmatter(text)
    if split is None:
        return [
            f"{entry_file_name} does not start with a YAML frontmatter block "
            f"(expected leading '---' fence)"
        ]

    frontmatter_yaml = split

    # Step 4: parse YAML.
    try:
        parsed: Any = yaml.safe_load(frontmatter_yaml)
    except yaml.YAMLError as exc:
        return [f"{entry_file_name} frontmatter is not valid YAML: {exc}"]

    if not isinstance(parsed, Mapping):
        return [
            f"{entry_file_name} frontmatter must be a YAML mapping, got {type(parsed).__name__}"
        ]

    errors: list[str] = []

    # Step 5: validate individual frontmatter fields.
    errors.extend(_validate_frontmatter(parsed))

    # Step 6: cross-check frontmatter type against entry filename.
    # E.g. BUG.md requires type=bug; PBI.md requires type=feature, etc.
    # Guard: only run when type is present — _validate_frontmatter already
    # emits a missing-field error for absent type (step 5); emitting a
    # second "type=None does not match …" error would be redundant.
    fm_type = parsed.get("type")
    expected_type_for_entry = _TYPE_BY_ENTRY_FILE[entry_file_name]
    if fm_type is not None and fm_type != expected_type_for_entry:
        errors.append(
            f"type={fm_type!r} does not match entry file {entry_file_name!r} "
            f"(expected type={expected_type_for_entry!r})"
        )

    # Step 7: check required siblings.
    # Use the entry-file-derived type (already validated above) for sibling
    # lookup; fall back gracefully if type is unknown/invalid.
    sibling_key = expected_type_for_entry
    for sibling in REQUIRED_SIBLINGS_BY_TYPE[sibling_key]:
        if not (sample_dir / sibling).is_file():
            errors.append(f"missing required sibling file: {sibling}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate sample PBI directories.")
    parser.add_argument(
        "samples_dir",
        type=Path,
        help="Path to the samples/ directory.",
    )
    args = parser.parse_args(argv)

    samples_dir: Path = args.samples_dir
    if not samples_dir.is_dir():
        print(f"error: not a directory: {samples_dir}", file=sys.stderr)
        return 2

    any_errors = False
    for child in sorted(samples_dir.iterdir()):
        if not child.is_dir():
            continue
        errs = validate_sample(child)
        if errs:
            any_errors = True
            print(f"FAIL {child.name}", file=sys.stderr)
            for err in errs:
                print(f"  - {err}", file=sys.stderr)
        else:
            print(f"OK   {child.name}")

    return 1 if any_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
