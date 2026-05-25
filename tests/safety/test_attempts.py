"""Tests for the attempts-counter helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.safety.attempts import (
    AttemptCounter,
    AttemptsExceeded,
    max_attempts,
    read_attempts,
    write_attempts,
)
from tests.safety.conftest import write_pbi_dir


def test_default_max_attempts_is_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RALPH_MAX_ATTEMPTS", raising=False)
    assert max_attempts() == 3


def test_env_override_for_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "7")
    assert max_attempts() == 7


def test_invalid_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "not-a-number")
    assert max_attempts() == 3


def test_zero_or_negative_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "0")
    assert max_attempts() == 3
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "-2")
    assert max_attempts() == 3


def test_read_attempts_returns_zero_when_field_missing(
    repo_dir: Path,
) -> None:
    pbi_dir = write_pbi_dir(
        repo_dir,
        bucket="current",
        pbi_id="WI-1",
        frontmatter={"attempts": 0},
    )
    assert read_attempts(pbi_dir) == 0


def test_write_attempts_updates_frontmatter_in_place(
    repo_dir: Path,
) -> None:
    pbi_dir = write_pbi_dir(
        repo_dir,
        bucket="current",
        pbi_id="WI-1",
        frontmatter={"attempts": 1, "severity": "high"},
    )
    write_attempts(pbi_dir, value=4)
    assert read_attempts(pbi_dir) == 4
    # Other frontmatter must be preserved.
    text = (pbi_dir / "PBI.md").read_text(encoding="utf-8")
    assert "severity: high" in text


def test_increment_returns_new_value(repo_dir: Path) -> None:
    pbi_dir = write_pbi_dir(repo_dir, bucket="current", pbi_id="WI-1", frontmatter={"attempts": 2})
    counter = AttemptCounter(pbi_dir=pbi_dir)
    assert counter.increment() == 3
    assert read_attempts(pbi_dir) == 3


def test_increment_raises_when_max_exceeded(
    repo_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RALPH_MAX_ATTEMPTS", "2")
    pbi_dir = write_pbi_dir(repo_dir, bucket="current", pbi_id="WI-1", frontmatter={"attempts": 2})
    counter = AttemptCounter(pbi_dir=pbi_dir)
    with pytest.raises(AttemptsExceeded):
        counter.increment()


def test_attempts_exceeded_records_pbi_id(repo_dir: Path) -> None:
    pbi_dir = write_pbi_dir(
        repo_dir, bucket="current", pbi_id="WI-attempts", frontmatter={"attempts": 99}
    )
    counter = AttemptCounter(pbi_dir=pbi_dir)
    with pytest.raises(AttemptsExceeded) as exc_info:
        counter.increment()
    assert exc_info.value.pbi_id == "WI-attempts"
    assert exc_info.value.attempts >= 99
