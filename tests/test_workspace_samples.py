"""Workspace + sample-PBI tests.

Drives ``scripts.validate_samples`` against every directory under ``samples/``
to verify that the canonical PBI frontmatter and sibling-file layout hold.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_samples import validate_sample

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "samples"

REQUIRED_SAMPLE_DIRS = (
    "feature-WI-1234",
    "bug-deploy-rosa-irsa-2026-05-23",
    "pr-feedback-WI-1234-r2",
)


def test_samples_dir_exists() -> None:
    assert SAMPLES_DIR.is_dir(), f"samples/ directory missing at {SAMPLES_DIR}"


def test_investigate_template_present() -> None:
    template = SAMPLES_DIR / "INVESTIGATE-template.md"
    assert template.is_file(), "samples/INVESTIGATE-template.md is required"
    body = template.read_text(encoding="utf-8")
    for heading in (
        "Service overview",
        "Key classes",
        "How to run locally",
        "How to read",
        "Configuration",
        "External dependencies",
        "gotchas",
        "Tests",
    ):
        assert heading.lower() in body.lower(), (
            f"INVESTIGATE-template.md missing section referencing '{heading}'"
        )


@pytest.mark.parametrize("sample_name", REQUIRED_SAMPLE_DIRS)
def test_required_sample_dirs_exist(sample_name: str) -> None:
    assert (SAMPLES_DIR / sample_name).is_dir(), (
        f"required sample directory missing: samples/{sample_name}"
    )


@pytest.mark.parametrize("sample_name", REQUIRED_SAMPLE_DIRS)
def test_sample_validates(sample_name: str) -> None:
    errors = validate_sample(SAMPLES_DIR / sample_name)
    assert errors == [], (
        f"validator errors for samples/{sample_name}:\n  - "
        + "\n  - ".join(errors)
    )
