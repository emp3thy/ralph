"""Tests for ralph_executor.target_clone."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ralph_executor.git_ops import GitCommandError
from ralph_executor.target_clone import (
    TargetClone,
    TargetUnreachable,
    ensure_clone,
)
from ralph_executor.url_utils import parse_target_repo


def test_ensure_clone_runs_git_clone_on_first_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First time a target is seen: git clone into workspace_root/clones/<slug>/."""
    calls: list[tuple[str, str]] = []

    def fake_clone(url: str, dest: Path, **kwargs: Any) -> None:
        calls.append((url, str(dest)))
        dest.mkdir(parents=True)
        (dest / ".git").mkdir()

    monkeypatch.setattr("ralph_executor.target_clone.git_ops.clone", fake_clone)
    info = parse_target_repo("https://github.com/emp3thy/ralph")
    clone = ensure_clone(info, workspace_root=tmp_path)

    expected_dir = tmp_path / "clones" / "emp3thy-ralph"
    assert isinstance(clone, TargetClone)
    assert clone.info == info
    assert clone.clone_root == expected_dir
    assert calls == [("https://github.com/emp3thy/ralph.git", str(expected_dir))]


def test_ensure_clone_runs_git_fetch_when_clone_already_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second call (or subsequent iteration): git fetch origin, no clone."""
    clone_dir = tmp_path / "clones" / "emp3thy-ralph"
    clone_dir.mkdir(parents=True)
    (clone_dir / ".git").mkdir()

    fetch_calls: list[Path] = []
    clone_calls: list[str] = []

    def fake_fetch(repo: Path, remote: str = "origin") -> None:
        fetch_calls.append(repo)

    def fake_clone(url: str, dest: Path, **kwargs: Any) -> None:
        clone_calls.append(url)

    monkeypatch.setattr("ralph_executor.target_clone.git_ops.fetch", fake_fetch)
    monkeypatch.setattr("ralph_executor.target_clone.git_ops.clone", fake_clone)

    info = parse_target_repo("https://github.com/emp3thy/ralph")
    clone = ensure_clone(info, workspace_root=tmp_path)

    assert fetch_calls == [clone_dir]
    assert clone_calls == []
    assert clone.clone_root == clone_dir


def test_ensure_clone_raises_target_unreachable_on_clone_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_clone(url: str, dest: Path, **kwargs: Any) -> None:
        raise GitCommandError(["git", "clone", url, str(dest)], 128, "authentication failed")

    monkeypatch.setattr("ralph_executor.target_clone.git_ops.clone", fake_clone)
    info = parse_target_repo("https://github.com/emp3thy/ralph")

    with pytest.raises(TargetUnreachable, match="authentication failed"):
        ensure_clone(info, workspace_root=tmp_path)


def test_ensure_clone_raises_target_unreachable_on_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone_dir = tmp_path / "clones" / "emp3thy-ralph"
    clone_dir.mkdir(parents=True)
    (clone_dir / ".git").mkdir()

    def fake_fetch(repo: Path, remote: str = "origin") -> None:
        raise GitCommandError(["git", "fetch", remote], 128, "network unreachable")

    monkeypatch.setattr("ralph_executor.target_clone.git_ops.fetch", fake_fetch)
    info = parse_target_repo("https://github.com/emp3thy/ralph")

    with pytest.raises(TargetUnreachable, match="network unreachable"):
        ensure_clone(info, workspace_root=tmp_path)


def test_ensure_clone_creates_clones_subdir_if_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """workspace_root may not have a clones/ subdir yet on first call."""
    calls: list[Path] = []

    def fake_clone(url: str, dest: Path, **kwargs: Any) -> None:
        assert dest.parent.is_dir(), f"parent {dest.parent} does not exist"
        calls.append(dest)
        dest.mkdir(parents=True)
        (dest / ".git").mkdir()

    monkeypatch.setattr("ralph_executor.target_clone.git_ops.clone", fake_clone)
    info = parse_target_repo("https://github.com/emp3thy/ralph")

    ensure_clone(info, workspace_root=tmp_path)

    assert (tmp_path / "clones").is_dir()
    assert len(calls) == 1
