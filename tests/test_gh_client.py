"""Unit tests for ``scripts.gh_client.GhClient``.

The client wraps ``requests.Session`` with PAT auth (Bearer token) and
GitHub REST conventions (api.github.com base, ``Accept:
application/vnd.github+json``, ``X-GitHub-Api-Version: 2022-11-28``).
These tests use the ``responses`` library to mock every HTTP exchange.
"""

from __future__ import annotations

import pytest
import responses

from scripts.gh_client import GhClient, GhError

BASE = "https://api.github.com"
OWNER = "example-org"
REPO = "service-auth"
TOKEN = "ghp_fake_token_value"


@responses.activate
def test_get_composes_url_and_sets_bearer_header() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/repos/{OWNER}/{REPO}",
        json={"id": 12345, "name": REPO, "default_branch": "main"},
        status=200,
    )

    client = GhClient(token=TOKEN)
    result = client.get(f"/repos/{OWNER}/{REPO}")

    assert result == {"id": 12345, "name": REPO, "default_branch": "main"}
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert call.request.headers["Accept"] == "application/vnd.github+json"
    assert call.request.headers["X-GitHub-Api-Version"] == "2022-11-28"


@responses.activate
def test_post_sends_json_body() -> None:
    responses.add(
        responses.POST,
        f"{BASE}/repos/{OWNER}/{REPO}/git/refs",
        json={"ref": "refs/heads/ralph-queue", "object": {"sha": "a" * 40}},
        status=201,
    )

    client = GhClient(token=TOKEN)
    result = client.post(
        f"/repos/{OWNER}/{REPO}/git/refs",
        json_body={"ref": "refs/heads/ralph-queue", "sha": "a" * 40},
    )

    assert result["ref"] == "refs/heads/ralph-queue"
    call = responses.calls[0]
    assert call.request.headers["Content-Type"] == "application/json"


@responses.activate
def test_put_sends_json_body() -> None:
    responses.add(
        responses.PUT,
        f"{BASE}/repos/{OWNER}/{REPO}/branches/ralph-queue/protection",
        json={"url": "https://api.github.com/.../protection"},
        status=200,
    )

    client = GhClient(token=TOKEN)
    result = client.put(
        f"/repos/{OWNER}/{REPO}/branches/ralph-queue/protection",
        json_body={
            "required_status_checks": None,
            "enforce_admins": True,
            "required_pull_request_reviews": None,
            "restrictions": None,
        },
    )

    assert "url" in result
    call = responses.calls[0]
    assert call.request.method == "PUT"
    assert call.request.headers["Content-Type"] == "application/json"


@responses.activate
def test_patch_sends_json_body() -> None:
    responses.add(
        responses.PATCH,
        f"{BASE}/repos/{OWNER}/{REPO}",
        json={"name": REPO, "description": "updated"},
        status=200,
    )

    client = GhClient(token=TOKEN)
    result = client.patch(
        f"/repos/{OWNER}/{REPO}",
        json_body={"description": "updated"},
    )

    assert result["description"] == "updated"


@responses.activate
def test_delete_returns_none_on_204() -> None:
    responses.add(
        responses.DELETE,
        f"{BASE}/repos/{OWNER}/{REPO}/branches/ralph-queue/protection",
        status=204,
    )

    client = GhClient(token=TOKEN)
    result = client.delete(f"/repos/{OWNER}/{REPO}/branches/ralph-queue/protection")

    assert result is None


@responses.activate
def test_200_with_empty_body_returns_none() -> None:
    """A 200 response with an empty body returns None (defensive against
    callers that would otherwise hit json.JSONDecodeError on response.json())."""
    responses.add(
        responses.GET,
        f"{BASE}/repos/{OWNER}/{REPO}/empty",
        body=b"",
        status=200,
    )

    client = GhClient(token=TOKEN)
    result = client.get(f"/repos/{OWNER}/{REPO}/empty")
    assert result is None


@responses.activate
def test_non_2xx_raises_gh_error_with_status_and_body() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/repos/{OWNER}/nope",
        json={"message": "Not Found", "documentation_url": "https://docs.github.com/..."},
        status=404,
    )

    client = GhClient(token=TOKEN)
    with pytest.raises(GhError) as excinfo:
        client.get(f"/repos/{OWNER}/nope")

    assert excinfo.value.status_code == 404
    assert "Not Found" in str(excinfo.value)


def test_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="token must be a non-empty string"):
        GhClient(token="")


def test_strips_trailing_slash_from_base_url() -> None:
    client = GhClient(token=TOKEN, base_url=BASE + "/")
    assert client.base_url == BASE


@responses.activate
def test_strips_whitespace_from_token_before_use() -> None:
    """Regression: validation uses token.strip() to check emptiness, so a
    token like '  ghp_abc  ' passes validation. The raw value must NOT then
    leak into the Authorization header (would produce 'Bearer   ghp_abc  '
    and a silent 401). Caught by BugBot on PR #2.
    """
    responses.add(
        responses.GET,
        f"{BASE}/repos/{OWNER}/{REPO}",
        json={"id": 1},
        status=200,
    )

    client = GhClient(token=f"  {TOKEN}  ")
    client.get(f"/repos/{OWNER}/{REPO}")
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == f"Bearer {TOKEN}"


def test_strips_whitespace_from_base_url_before_use() -> None:
    """Same regression scoped to base_url — leading/trailing whitespace must
    be stripped, not just used to test emptiness."""
    client = GhClient(token=TOKEN, base_url=f"  {BASE}/  ")
    assert client.base_url == BASE


@responses.activate
def test_path_without_leading_slash_is_handled() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/repos/{OWNER}/{REPO}",
        json={"id": 1},
        status=200,
    )

    client = GhClient(token=TOKEN)
    result = client.get(f"repos/{OWNER}/{REPO}")
    assert result == {"id": 1}


@responses.activate
def test_extra_headers_are_forwarded() -> None:
    """extra_headers passed via _request are sent on the request."""
    responses.add(
        responses.GET,
        f"{BASE}/repos/{OWNER}/{REPO}",
        json={"id": 1},
        status=200,
    )

    client = GhClient(token=TOKEN)
    result = client._request(
        "GET", f"/repos/{OWNER}/{REPO}", extra_headers={"X-Custom-Header": "ralph-test"}
    )
    assert result == {"id": 1}
    call = responses.calls[0]
    assert call.request.headers["X-Custom-Header"] == "ralph-test"
