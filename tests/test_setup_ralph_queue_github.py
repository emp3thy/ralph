"""Tests for ``scripts.setup_ralph_queue_github``.

Every GitHub REST endpoint the script touches is mocked with ``responses``.
No network calls happen during the test run.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import responses

from scripts import setup_ralph_queue_github

BASE = "https://api.github.com"
OWNER = "example-org"
REPO = "service-auth"
TOKEN = "ghp_fake_token_value"
MAIN_SHA = "a" * 40


REPO_URL = f"{BASE}/repos/{OWNER}/{REPO}"
MAIN_REF_URL = f"{REPO_URL}/git/ref/heads/main"
QUEUE_REF_URL = f"{REPO_URL}/git/ref/heads/ralph-queue"
REFS_CREATE_URL = f"{REPO_URL}/git/refs"
PROTECTION_URL = f"{REPO_URL}/branches/ralph-queue/protection"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.setenv("GH_OWNER", OWNER)


def _register_repo_lookup() -> None:
    responses.add(
        responses.GET,
        REPO_URL,
        json={
            "id": 12345,
            "name": REPO,
            "full_name": f"{OWNER}/{REPO}",
            "default_branch": "main",
        },
        status=200,
    )


def _register_main_tip(sha: str = MAIN_SHA) -> None:
    responses.add(
        responses.GET,
        MAIN_REF_URL,
        json={
            "ref": "refs/heads/main",
            "object": {"sha": sha, "type": "commit"},
        },
        status=200,
    )


def _register_queue_branch_absent() -> None:
    responses.add(
        responses.GET,
        QUEUE_REF_URL,
        json={"message": "Not Found"},
        status=404,
    )


def _register_queue_branch_present(sha: str = MAIN_SHA) -> None:
    responses.add(
        responses.GET,
        QUEUE_REF_URL,
        json={
            "ref": "refs/heads/ralph-queue",
            "object": {"sha": sha, "type": "commit"},
        },
        status=200,
    )


def _register_queue_branch_create(sha: str = MAIN_SHA) -> None:
    responses.add(
        responses.POST,
        REFS_CREATE_URL,
        json={
            "ref": "refs/heads/ralph-queue",
            "object": {"sha": sha, "type": "commit"},
        },
        status=201,
    )


def _register_protection_put() -> None:
    responses.add(
        responses.PUT,
        PROTECTION_URL,
        json={"url": PROTECTION_URL, "enabled": True},
        status=200,
    )


@responses.activate
def test_happy_path_returns_zero_and_prints_json(
    env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _register_repo_lookup()
    _register_main_tip()
    _register_queue_branch_absent()
    _register_queue_branch_create()
    _register_protection_put()

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 0

    captured = capsys.readouterr()
    payload: dict[str, Any] = json.loads(captured.out)
    assert payload["repo"] == REPO
    assert payload["owner"] == OWNER
    assert payload["branch_name"] == "ralph-queue"
    assert payload["branch_base_sha"] == MAIN_SHA
    assert payload["branch_created"] is True
    assert payload["branch_existed"] is False
    assert payload["protection_applied"] is True
    assert payload["dry_run"] is False


@responses.activate
def test_idempotent_when_branch_already_exists(
    env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _register_repo_lookup()
    _register_main_tip()
    _register_queue_branch_present()
    _register_protection_put()

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["branch_created"] is False
    assert payload["branch_existed"] is True
    assert payload["protection_applied"] is True


@responses.activate
def test_repo_not_found_exits_nonzero(env: None, capsys: pytest.CaptureFixture[str]) -> None:
    responses.add(
        responses.GET,
        REPO_URL,
        json={"message": "Not Found"},
        status=404,
    )

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "Not Found" in stderr


def test_missing_gh_token_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GH_OWNER", OWNER)

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 2
    assert "GH_TOKEN" in capsys.readouterr().err


def test_missing_gh_owner_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.delenv("GH_OWNER", raising=False)

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 2
    assert "GH_OWNER" in capsys.readouterr().err


@responses.activate
def test_dry_run_makes_no_mutations(env: None, capsys: pytest.CaptureFixture[str]) -> None:
    _register_repo_lookup()
    _register_main_tip()
    _register_queue_branch_absent()

    exit_code = setup_ralph_queue_github.main(["--repo", REPO, "--dry-run"])
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["branch_created"] is False
    assert payload["protection_applied"] is False
    assert all(call.request.method == "GET" for call in responses.calls)


@responses.activate
def test_protection_payload_shape(env: None, capsys: pytest.CaptureFixture[str]) -> None:
    """The PUT to /branches/ralph-queue/protection sends the documented shape."""
    _register_repo_lookup()
    _register_main_tip()
    _register_queue_branch_absent()
    _register_queue_branch_create()
    _register_protection_put()

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 0

    # Find the PUT call.
    put_calls = [c for c in responses.calls if c.request.method == "PUT"]
    assert len(put_calls) == 1
    raw_body = put_calls[0].request.body
    assert raw_body is not None
    body = json.loads(raw_body)
    assert body["enforce_admins"] is True
    assert body["allow_force_pushes"] is False
    assert body["allow_deletions"] is False
    # Required-status-checks and restrictions may be null OR an object;
    # both are documented as accepted by the GitHub API. The script
    # MUST include each top-level key (even if value is null) per the docs.
    assert "required_status_checks" in body
    assert "required_pull_request_reviews" in body
    assert "restrictions" in body


@responses.activate
def test_network_error_exits_nonzero(env: None, capsys: pytest.CaptureFixture[str]) -> None:
    """Network-level errors (ConnectionError, Timeout) must produce a clean
    exit code 2, not an unhandled traceback."""
    from requests.exceptions import ConnectionError as ReqConnectionError

    responses.add(
        responses.GET,
        REPO_URL,
        body=ReqConnectionError("simulated network failure"),
    )

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "network error" in stderr.lower() or "simulated network failure" in stderr


@responses.activate
def test_concurrent_create_422_still_applies_protection(
    env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Race regression: when a concurrent run creates ralph-queue between our
    GET-ref check and our POST-ref attempt, GitHub returns 422 'Reference
    already exists'. The script must treat this as branch-existed-already and
    still apply branch protection — otherwise the repo is left unprotected.
    Caught by BugBot on PR #2.
    """
    _register_repo_lookup()
    _register_main_tip()
    _register_queue_branch_absent()
    # POST returns 422 — branch was concurrently created.
    responses.add(
        responses.POST,
        REFS_CREATE_URL,
        json={"message": "Reference already exists"},
        status=422,
    )
    _register_protection_put()

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 0

    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert payload["branch_created"] is False
    assert payload["branch_existed"] is True
    assert payload["protection_applied"] is True
    # Confirm the protection PUT actually fired (would be skipped if the 422
    # had escaped to the outer GhError handler).
    put_calls = [c for c in responses.calls if c.request.method == "PUT"]
    assert len(put_calls) == 1
