"""Tests for scripts.validate_samples — focused on the target_repo field
introduced by RALPH-PBI-TARGET-REPO-FIELD."""

from __future__ import annotations

from scripts.validate_samples import _validate_frontmatter


def _valid_pbi_dict(**overrides: object) -> dict[str, object]:
    """Build a frontmatter dict that would otherwise validate cleanly.
    Caller can override any field via kwargs."""
    base: dict[str, object] = {
        "id": "WI-1",
        "type": "feature",
        "status": "inbox",
        "severity": "normal",
        "attempts": 0,
        "created_at": "2026-05-27T00:00:00+00:00",
        "updated_at": "2026-05-27T00:00:00+00:00",
        "target_repo": "https://github.com/emp3thy/ralph",
    }
    base.update(overrides)
    return base


def test_validate_rejects_missing_target_repo() -> None:
    pbi = _valid_pbi_dict()
    del pbi["target_repo"]
    errors = _validate_frontmatter(pbi)
    assert any("target_repo" in e for e in errors), errors


def test_validate_accepts_valid_github_url() -> None:
    pbi = _valid_pbi_dict(target_repo="https://github.com/emp3thy/ralph")
    errors = _validate_frontmatter(pbi)
    assert not any("target_repo" in e for e in errors), errors


def test_validate_accepts_github_url_with_git_suffix() -> None:
    pbi = _valid_pbi_dict(target_repo="https://github.com/emp3thy/ralph.git")
    errors = _validate_frontmatter(pbi)
    assert not any("target_repo" in e for e in errors), errors


def test_validate_accepts_ado_url() -> None:
    pbi = _valid_pbi_dict(
        target_repo="https://dev.azure.com/myorg/myproj/_git/myrepo"
    )
    errors = _validate_frontmatter(pbi)
    assert not any("target_repo" in e for e in errors), errors


def test_validate_rejects_http_target_repo() -> None:
    pbi = _valid_pbi_dict(target_repo="http://github.com/x/y")
    errors = _validate_frontmatter(pbi)
    assert any("HTTPS" in e and "target_repo" in e for e in errors), errors


def test_validate_rejects_ssh_target_repo() -> None:
    pbi = _valid_pbi_dict(target_repo="git@github.com:emp3thy/ralph.git")
    errors = _validate_frontmatter(pbi)
    assert any("HTTPS" in e and "target_repo" in e for e in errors), errors


def test_validate_rejects_target_repo_without_path() -> None:
    pbi = _valid_pbi_dict(target_repo="https://github.com")
    errors = _validate_frontmatter(pbi)
    assert any("path" in e and "target_repo" in e for e in errors), errors


def test_validate_rejects_non_string_target_repo() -> None:
    pbi = _valid_pbi_dict(target_repo=42)
    errors = _validate_frontmatter(pbi)
    assert any("string" in e and "target_repo" in e for e in errors), errors
