"""Tests for the ``workitem-fetch-github`` skill's fetcher.

The fetcher lives at ``skills/workitem-fetch-github/scripts/fetch.py``.
All GitHub REST endpoints are mocked with ``responses``; no network.
"""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
import responses

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "skills" / "workitem-fetch-github" / "scripts" / "fetch.py"

GH_API = "https://api.github.com"
OWNER = "example-org"
REPO_NAME = "service-auth"
TOKEN = "ghp_fake_token"
ISSUE_NUMBER = 42
CHILD_A = 101
CHILD_B = 102


@pytest.fixture(scope="module")
def fetch_module() -> ModuleType:
    """Load ``skills/workitem-fetch-github/scripts/fetch.py`` as a module."""
    assert SCRIPT_PATH.is_file(), f"missing fetcher script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("workitem_fetch_github", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.setenv("GH_OWNER", OWNER)


def _issue_url(number: int) -> str:
    return f"{GH_API}/repos/{OWNER}/{REPO_NAME}/issues/{number}"


def _register_feature_issue() -> None:
    responses.add(
        responses.GET,
        _issue_url(ISSUE_NUMBER),
        json={
            "number": ISSUE_NUMBER,
            "title": "Add /healthz endpoint to service-auth",
            "body": (
                "Platform requires every service to expose a liveness probe "
                "at `GET /healthz`.\n\n"
                "## Acceptance criteria\n\n"
                "- GET /healthz returns 200.\n"
                '- Body is JSON `{"status":"ok"}`.\n\n'
                "![design](https://user-images.githubusercontent.com/1/design.png)\n"
            ),
            "labels": [
                {"name": "enhancement"},
                {"name": "priority/normal"},
            ],
            "html_url": (f"https://github.com/{OWNER}/{REPO_NAME}/issues/{ISSUE_NUMBER}"),
            "user": {"login": "someone"},
        },
        status=200,
    )
    responses.add(
        responses.GET,
        "https://user-images.githubusercontent.com/1/design.png",
        body=b"\x89PNG\r\n\x1a\nfake-png-bytes",
        status=200,
        content_type="image/png",
    )


def _register_bug_issue() -> None:
    responses.add(
        responses.GET,
        _issue_url(ISSUE_NUMBER),
        json={
            "number": ISSUE_NUMBER,
            "title": "service-auth pod crashloops on deploy",
            "body": (
                "AccessDenied calling STS via IRSA.\n\n"
                "## Reproduce\n\n"
                "1. kubectl get pods\n"
                "2. Observe CrashLoopBackOff\n"
            ),
            "labels": [
                {"name": "bug"},
                {"name": "priority/critical"},
            ],
            "html_url": (f"https://github.com/{OWNER}/{REPO_NAME}/issues/{ISSUE_NUMBER}"),
            "user": {"login": "someone"},
        },
        status=200,
    )


def _register_issue_with_children() -> None:
    responses.add(
        responses.GET,
        _issue_url(ISSUE_NUMBER),
        json={
            "number": ISSUE_NUMBER,
            "title": "Onboarding flow rewrite",
            "body": (f"Parent feature.\n\n## Tasks\n\n- [ ] #{CHILD_A}\n- [ ] #{CHILD_B}\n"),
            "labels": [{"name": "enhancement"}],
            "html_url": (f"https://github.com/{OWNER}/{REPO_NAME}/issues/{ISSUE_NUMBER}"),
            "user": {"login": "someone"},
        },
        status=200,
    )


@responses.activate
def test_feature_issue_produces_normalised_feature_json(
    env: None,
    fetch_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_feature_issue()
    exit_code = fetch_module.main(
        [
            "--work-item",
            str(ISSUE_NUMBER),
            "--repo",
            f"{OWNER}/{REPO_NAME}",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == f"WI-{ISSUE_NUMBER}"
    assert payload["type"] == "feature"
    assert payload["severity"] == "normal"
    assert payload["title"] == "Add /healthz endpoint to service-auth"
    assert "/healthz" in payload["body_markdown"]
    assert payload["source_host"] == "github"
    assert payload["source_url"].endswith(f"/issues/{ISSUE_NUMBER}")
    assert payload["parent_id"] is None
    assert payload["child_ids"] == []
    assert len(payload["attachments"]) == 1
    att = payload["attachments"][0]
    assert att["name"] == "design.png"
    decoded = base64.b64decode(att["content_base64"])
    assert decoded.startswith(b"\x89PNG")


@responses.activate
def test_bug_issue_produces_normalised_bug_json(
    env: None,
    fetch_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_bug_issue()
    exit_code = fetch_module.main(
        [
            "--work-item",
            f"#{ISSUE_NUMBER}",
            "--repo",
            f"{OWNER}/{REPO_NAME}",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["type"] == "bug"
    assert payload["severity"] == "critical"
    assert "AccessDenied" in payload["body_markdown"]
    assert "kubectl get pods" in payload["repro_steps_markdown"]


@responses.activate
def test_include_children_populates_child_ids(
    env: None,
    fetch_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_issue_with_children()
    exit_code = fetch_module.main(
        [
            "--work-item",
            str(ISSUE_NUMBER),
            "--repo",
            f"{OWNER}/{REPO_NAME}",
            "--include-children",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["child_ids"] == [f"WI-{CHILD_A}", f"WI-{CHILD_B}"]


def test_missing_token_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    fetch_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GH_OWNER", OWNER)
    exit_code = fetch_module.main(
        [
            "--work-item",
            str(ISSUE_NUMBER),
            "--repo",
            f"{OWNER}/{REPO_NAME}",
        ]
    )
    assert exit_code == 2
    assert "GH_TOKEN" in capsys.readouterr().err


@responses.activate
def test_schema_validation_runs_before_emit(
    env: None,
    fetch_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The fetcher MUST emit a document that ``schema.validate`` accepts."""
    _register_feature_issue()
    exit_code = fetch_module.main(
        [
            "--work-item",
            str(ISSUE_NUMBER),
            "--repo",
            f"{OWNER}/{REPO_NAME}",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    schema_path = REPO_ROOT / "skills" / "workitem-fetch-github" / "scripts" / "schema.py"
    spec = importlib.util.spec_from_file_location("wi_schema", schema_path)
    assert spec is not None and spec.loader is not None
    schema_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(schema_mod)
    errors = schema_mod.validate(payload)
    assert errors == [], f"fetcher emitted invalid document: {errors}"


# ---------------------------------------------------------------------------
# _download host-allow-list (regression for PR #25 BugBot finding)
# ---------------------------------------------------------------------------


def test_download_uses_auth_session_for_github_hosts(
    fetch_module: ModuleType,
    env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub-hosted attachments need the bearer token (private user-content
    URLs return 404 without it). The fetcher's GhClient session carries
    the Authorization header — assert that session is used for GH hosts."""
    from scripts.gh_client import GhClient

    client = GhClient(token=TOKEN)

    captured: dict[str, object] = {}

    class _StubResponse:
        status_code = 200
        content = b"github-bytes"
        text = ""

    def _stub_session_get(url: str, timeout: float) -> _StubResponse:
        captured["used"] = "session"
        captured["url"] = url
        return _StubResponse()

    monkeypatch.setattr(client._session, "get", _stub_session_get)
    # Ensure the authless path is NOT called for github hosts.
    monkeypatch.setattr(
        fetch_module.requests,
        "get",
        lambda *a, **kw: pytest.fail("authless requests.get called for github URL"),
    )

    result = fetch_module._download(client, "https://github.com/x/y/files/123")
    assert result == b"github-bytes"
    assert captured["used"] == "session"


def test_download_uses_authless_for_third_party_hosts(
    fetch_module: ModuleType,
    env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for PR #25 BugBot finding: a URL from issue body that
    points to a non-GitHub host MUST be fetched WITHOUT the auth-bearing
    session. The bearer token would otherwise leak to whoever owns
    that third-party host."""
    from scripts.gh_client import GhClient

    client = GhClient(token=TOKEN)

    captured: dict[str, object] = {}

    class _StubResponse:
        status_code = 200
        content = b"third-party-bytes"
        text = ""

    # Sentinel: if the auth-bearing session is used, fail loudly.
    monkeypatch.setattr(
        client._session,
        "get",
        lambda *a, **kw: pytest.fail("auth session used for third-party URL"),
    )

    def _stub_requests_get(url: str, timeout: float) -> _StubResponse:
        captured["used"] = "authless"
        captured["url"] = url
        return _StubResponse()

    monkeypatch.setattr(fetch_module.requests, "get", _stub_requests_get)

    result = fetch_module._download(client, "https://attacker.example/leak.png")
    assert result == b"third-party-bytes"
    assert captured["used"] == "authless"


def test_download_recognises_user_content_subdomains(
    fetch_module: ModuleType,
    env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """user-images.githubusercontent.com and friends are GitHub-hosted —
    must use the auth session, not the authless path."""
    from scripts.gh_client import GhClient

    client = GhClient(token=TOKEN)

    class _StubResponse:
        status_code = 200
        content = b"user-content-bytes"
        text = ""

    session_calls: list[str] = []

    def _stub_session_get(url: str, timeout: float) -> _StubResponse:
        session_calls.append(url)
        return _StubResponse()

    monkeypatch.setattr(client._session, "get", _stub_session_get)
    monkeypatch.setattr(
        fetch_module.requests,
        "get",
        lambda *a, **kw: pytest.fail("authless requests.get called for user-content URL"),
    )

    fetch_module._download(client, "https://user-images.githubusercontent.com/123/abc.png")
    fetch_module._download(
        client,
        "https://private-user-images.githubusercontent.com/456/def.png",
    )
    assert len(session_calls) == 2
