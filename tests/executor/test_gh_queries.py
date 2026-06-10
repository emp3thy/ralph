"""Tests for ``ralph_executor.gh_queries``."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from ralph_executor.gh_queries import query_pr_checks, wait_for_pr_checks


def _fake_run_factory(
    returncode: int, stdout: str, stderr: str = ""
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a ``subprocess.run`` stand-in returning a fixed CompletedProcess.

    Patched into ``ralph_executor.gh_queries.run_text`` via string-form
    ``monkeypatch.setattr`` so ``query_pr_checks`` exercises its parsing
    without hitting the real gh CLI.
    """

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["gh", "pr", "checks"],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    return _fake_run


def test_query_pr_checks_returns_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = '[{"bucket":"pass","name":"build"},{"bucket":"pass","name":"lint"}]'
    monkeypatch.setattr("ralph_executor.gh_queries.run_text", _fake_run_factory(0, payload))
    state, names = query_pr_checks(tmp_path, 42)
    assert state == "pass"
    assert names == []


def test_query_pr_checks_returns_fail_with_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gh exits 1 when a required check has failed, but the JSON array on
    stdout is still authoritative — query_pr_checks must parse it."""
    payload = (
        '[{"bucket":"pass","name":"build"},'
        '{"bucket":"fail","name":"unit-tests"},'
        '{"bucket":"fail","name":"lint"}]'
    )
    monkeypatch.setattr("ralph_executor.gh_queries.run_text", _fake_run_factory(1, payload))
    state, names = query_pr_checks(tmp_path, 42)
    assert state == "fail"
    assert names == ["unit-tests", "lint"]


def test_query_pr_checks_returns_pending_on_exit_8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gh exits 8 with empty stdout when a required check is still
    pending — query_pr_checks must classify as ``pending`` so the
    polling loop keeps waiting rather than erroring out."""
    monkeypatch.setattr("ralph_executor.gh_queries.run_text", _fake_run_factory(8, ""))
    state, names = query_pr_checks(tmp_path, 42)
    assert state == "pending"
    assert names == []


def test_query_pr_checks_returns_error_on_gh_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``gh`` not installed (FileNotFoundError) is the canonical
    ``error`` path — query_pr_checks must not raise."""

    def _explode(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh not on PATH")

    monkeypatch.setattr("ralph_executor.gh_queries.run_text", _explode)
    state, names = query_pr_checks(tmp_path, 42)
    assert state == "error"
    assert names and "gh not on PATH" in names[0]


def test_wait_for_pr_checks_polls_until_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two pending polls then pass — loop must keep going past pending and
    return as soon as a terminal state arrives."""
    sequence: list[tuple[str, list[str]]] = [
        ("pending", []),
        ("pending", []),
        ("pass", []),
    ]
    calls = {"n": 0}

    def _fake_query(_repo: Path, _num: int, **_kwargs: object) -> tuple[str, list[str]]:
        result = sequence[calls["n"]]
        calls["n"] += 1
        return result

    sleeps: list[float] = []
    monkeypatch.setattr("ralph_executor.gh_queries.query_pr_checks", _fake_query)
    monkeypatch.setattr("ralph_executor.gh_queries.time.sleep", lambda s: sleeps.append(s))
    state, names = wait_for_pr_checks(tmp_path, 42, max_polls=6, interval_seconds=0.5)
    assert state == "pass"
    assert names == []
    assert calls["n"] == 3
    # Two sleeps between three polls; final pass does not trigger a sleep.
    assert sleeps == [0.5, 0.5]


def test_wait_for_pr_checks_budget_exhausted_returns_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All polls report pending — caller treats the returned ``pending``
    as ``partial`` so the NEXT iteration re-polls. The loop must not
    sleep after the final attempt."""
    calls = {"n": 0}

    def _always_pending(*_args: object, **_kwargs: object) -> tuple[str, list[str]]:
        calls["n"] += 1
        return ("pending", [])

    sleeps: list[float] = []
    monkeypatch.setattr("ralph_executor.gh_queries.query_pr_checks", _always_pending)
    monkeypatch.setattr("ralph_executor.gh_queries.time.sleep", lambda s: sleeps.append(s))
    state, names = wait_for_pr_checks(tmp_path, 42, max_polls=3, interval_seconds=0.25)
    assert state == "pending"
    assert names == []
    assert calls["n"] == 3
    # Two sleeps between three polls; no sleep after the last one.
    assert sleeps == [0.25, 0.25]
