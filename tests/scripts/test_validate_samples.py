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
    pbi = _valid_pbi_dict(target_repo="https://dev.azure.com/myorg/myproj/_git/myrepo")
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


def test_validate_rejects_target_repo_with_embedded_newline() -> None:
    """Regression: BugBot PR #40 flagged that ``urlparse`` accepts
    URLs with embedded ``\\n`` and ``_render_frontmatter`` interpolates
    the raw value into YAML, allowing an attacker-controlled
    ``--target-repo`` to inject extra YAML keys."""
    for bad in (
        "https://github.com/owner/repo\ninjected: value",
        "https://github.com/owner/repo\r\ninjected: value",
        "https://github.com/owner/repo\rinjected: value",
    ):
        pbi = _valid_pbi_dict(target_repo=bad)
        errors = _validate_frontmatter(pbi)
        assert any(
            "target_repo" in e and ("newline" in e or "carriage" in e) for e in errors
        ), f"missing newline/carriage rejection for {bad!r}: {errors}"


def test_all_live_pbis_have_target_repo() -> None:
    """Regression: every PBI / BUG / FEEDBACK file in .ralph/ AND samples/
    must validate (including the new target_repo field) post-migration.

    .ralph/ is gitignored on this feature branch — only directories that
    actually exist with entry files are scanned.
    """
    import pathlib

    import yaml

    from scripts.validate_samples import _validate_frontmatter

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    state_dirs = [
        repo_root / ".ralph" / state
        for state in ("inbox", "current", "pending-pr", "done", "blocked")
    ]
    sample_root = repo_root / "samples"

    failures: list[str] = []
    for root in (*state_dirs, sample_root):
        if not root.is_dir():
            continue
        for entry in root.rglob("*.md"):
            if entry.name not in ("PBI.md", "BUG.md", "FEEDBACK.md"):
                continue
            text = entry.read_text(encoding="utf-8")
            if not (text.startswith("---\n") or text.startswith("---\r\n")):
                continue
            lines = text.splitlines()
            close = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
            if close is None:
                failures.append(f"{entry}: no closing fence")
                continue
            try:
                fm = yaml.safe_load("\n".join(lines[1:close]))
            except yaml.YAMLError as exc:
                failures.append(f"{entry}: YAML error: {exc}")
                continue
            if not isinstance(fm, dict):
                failures.append(f"{entry}: frontmatter is not a mapping")
                continue
            errors = _validate_frontmatter(fm)
            target_errors = [e for e in errors if "target_repo" in e]
            if target_errors:
                failures.append(f"{entry}: {target_errors}")
    assert not failures, failures
