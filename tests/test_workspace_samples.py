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
    assert errors == [], f"validator errors for samples/{sample_name}:\n  - " + "\n  - ".join(
        errors
    )


def _write_feature_pbi(
    dir_path: Path,
    *,
    attempts: str = "0",
) -> None:
    """Helper: write a minimal valid feature PBI directory, with attempts injected as raw YAML."""
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "PBI.md").write_text(
        "---\n"
        "id: WI-X\n"
        "type: feature\n"
        "status: inbox\n"
        "severity: normal\n"
        f"attempts: {attempts}\n"
        "created_at: 2026-05-25T00:00:00+00:00\n"
        "updated_at: 2026-05-25T00:00:00+00:00\n"
        "---\n\n# body\n",
        encoding="utf-8",
    )
    (dir_path / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    (dir_path / "HISTORY.md").write_text("", encoding="utf-8")


def test_attempts_boolean_true_rejected(tmp_path: Path) -> None:
    """Regression: PyYAML parses `attempts: true` as Python True. Without an
    explicit bool guard, isinstance(True, int) is True and the value passes
    silently. Caught by BugBot review on PR #1.
    """
    sample = tmp_path / "WI-bool"
    _write_feature_pbi(sample, attempts="true")
    errors = validate_sample(sample)
    assert any("attempts must be int, got bool" in e for e in errors), (
        f"expected bool rejection, got: {errors}"
    )


def test_attempts_boolean_false_rejected(tmp_path: Path) -> None:
    """Same regression on the False branch."""
    sample = tmp_path / "WI-bool-false"
    _write_feature_pbi(sample, attempts="false")
    errors = validate_sample(sample)
    assert any("attempts must be int, got bool" in e for e in errors), (
        f"expected bool rejection, got: {errors}"
    )


def test_attempts_negative_int_rejected(tmp_path: Path) -> None:
    """Baseline: negative attempts must still be rejected (the bool fix must
    not break the existing negative-int guard)."""
    sample = tmp_path / "WI-neg"
    _write_feature_pbi(sample, attempts="-1")
    errors = validate_sample(sample)
    assert any("attempts must be >= 0, got -1" in e for e in errors), (
        f"expected negative rejection, got: {errors}"
    )


def test_attempts_valid_int_accepted(tmp_path: Path) -> None:
    """Baseline: valid attempts (positive int, zero) must pass."""
    sample = tmp_path / "WI-ok"
    _write_feature_pbi(sample, attempts="2")
    errors = validate_sample(sample)
    assert errors == [], f"expected clean validation, got: {errors}"
