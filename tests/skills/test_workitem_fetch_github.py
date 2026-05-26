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
