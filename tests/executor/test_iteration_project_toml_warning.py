"""Tests for the per-iteration project-TOML warning."""

from pathlib import Path

import pytest

from ralph_executor.iteration import _warn_project_toml_in_target_clone


def test_warns_when_project_toml_present(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clone_root = tmp_path / "clones" / "acme" / "widget"
    (clone_root / ".ralph").mkdir(parents=True)
    cfg_file = clone_root / ".ralph" / "config.toml"
    cfg_file.write_text("# legacy project TOML\n", encoding="utf-8")

    with caplog.at_level("WARNING", logger="ralph_executor.pbi_claim"):
        _warn_project_toml_in_target_clone(clone_root)

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "project TOML" in m and "is not supported" in m and str(cfg_file) in m for m in messages
    )


def test_no_warning_when_project_toml_absent(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clone_root = tmp_path / "clones" / "acme" / "widget"
    clone_root.mkdir(parents=True)

    with caplog.at_level("WARNING", logger="ralph_executor.pbi_claim"):
        _warn_project_toml_in_target_clone(clone_root)

    assert caplog.records == []


def test_no_warning_when_clone_root_missing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Defensive: never crash on a clone_root that doesn't exist."""
    clone_root = tmp_path / "does" / "not" / "exist"

    with caplog.at_level("WARNING", logger="ralph_executor.pbi_claim"):
        _warn_project_toml_in_target_clone(clone_root)

    assert caplog.records == []


def test_permission_denied_logged_at_debug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Detection failure (e.g. permission denied) suppressed to DEBUG per spec."""
    clone_root = tmp_path / "clones" / "acme" / "widget"
    clone_root.mkdir(parents=True)

    def _boom(self: Path) -> bool:
        raise PermissionError("simulated")

    monkeypatch.setattr(Path, "is_file", _boom)

    with caplog.at_level("DEBUG", logger="ralph_executor.pbi_claim"):
        _warn_project_toml_in_target_clone(clone_root)

    # No WARNING records emitted.
    assert not any(r.levelname == "WARNING" for r in caplog.records)
