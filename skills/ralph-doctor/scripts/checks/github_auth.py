"""``github_auth`` check for ``ralph-doctor`` (Phase 1).

Probes the GitHub REST API to prove two things in sequence:

1. ``GET /user`` — the canonical auth-test endpoint. 2xx proves the
   ``GH_TOKEN`` PAT authenticates; 401 means the token is invalid.
2. ``GET /repos/{owner}/test-permissions`` — a sentinel repo name that
   almost certainly does NOT exist under any real org. 404 means the PAT
   has the ``repo`` scope (it can reach the repos surface, but the named
   repo isn't there); 200 means the repo happens to exist and is
   accessible (also proves auth + scopes); 403 means scopes are missing.

The runner dispatches this check only when ``RALPH_GIT_HOST=github``.

External-service knobs read by this module:

- ``GH_TOKEN`` — required. GitHub PAT.
- ``GH_OWNER`` — required. GitHub org or user.
- ``GITHUB_API_BASE_URL`` — optional override; default
  ``https://api.github.com``.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import requests

_checks_pkg = sys.modules.get("ralph_doctor_checks")
if _checks_pkg is None:
    _spec = importlib.util.spec_from_file_location(
        "ralph_doctor_checks", Path(__file__).resolve().parent / "__init__.py"
    )
    if _spec is None or _spec.loader is None:
        raise RuntimeError("could not load ralph_doctor_checks contract module")
    _checks_pkg = importlib.util.module_from_spec(_spec)
    sys.modules["ralph_doctor_checks"] = _checks_pkg
    _spec.loader.exec_module(_checks_pkg)

if TYPE_CHECKING:
    Severity = Literal["error", "warn"]
    Status = Literal["pass", "fail", "skipped"]

    @dataclass(frozen=True)
    class CheckContext:
        settings_path: Path
        skills_dir: Path
        strict: bool = False
        git_host: str = ""
        extra: dict[str, str] = field(default_factory=dict)

    @dataclass(frozen=True)
    class CheckResult:
        name: str
        severity: Severity
        status: Status
        message: str
        details: dict[str, object] = field(default_factory=dict)
else:
    CheckContext = _checks_pkg.CheckContext
    CheckResult = _checks_pkg.CheckResult

GITHUB_API_BASE_URL_ENV = "GITHUB_API_BASE_URL"
DEFAULT_GITHUB_API_BASE = "https://api.github.com"
USER_PATH = "/user"
TEST_REPO_NAME = "test-permissions"
REQUEST_TIMEOUT_SECONDS = 10.0
REQUIRED_ENV: tuple[str, ...] = ("GH_TOKEN", "GH_OWNER")


def _missing_env_vars() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _user_probe(base_url: str, token: str) -> tuple[bool, str, int]:
    url = f"{base_url}{USER_PATH}"
    try:
        response = requests.get(url, headers=_headers(token), timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return False, f"GH_TOKEN probe GET /user raised: {exc!r}", 0

    status = response.status_code
    if 200 <= status < 300:
        return True, "", status

    preview = (response.text or "")[:200]
    return (
        False,
        (f"GH_TOKEN probe GET /user returned {status} (expected 2xx); body preview: {preview!r}"),
        status,
    )


def _repo_scope_probe(base_url: str, token: str, owner: str) -> tuple[bool, str, int]:
    url = f"{base_url}/repos/{owner}/{TEST_REPO_NAME}"
    try:
        response = requests.get(url, headers=_headers(token), timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return (
            False,
            f"GET /repos/{owner}/{TEST_REPO_NAME} raised: {exc!r}",
            0,
        )

    status = response.status_code
    if status == 404:
        return True, "404 -- auth + scopes both work", 404
    if 200 <= status < 300:
        return True, "repo exists; auth + scopes both work", status
    if status == 403:
        return (
            False,
            (
                "403 -- PAT lacks 'repo' scope. Re-issue the GH_TOKEN with "
                "the 'repo' scope (or the fine-grained equivalent for the "
                "target repository)."
            ),
            403,
        )
    if status == 401:
        return (
            False,
            (
                "401 -- GH_TOKEN is invalid on the repos surface "
                "(but /user passed earlier; rotate the token)."
            ),
            401,
        )

    preview = (response.text or "")[:200]
    return (
        False,
        f"unexpected status {status}; body preview: {preview!r}",
        status,
    )


def check(context: CheckContext) -> CheckResult:
    del context  # github_auth probe is environment-driven; context is unused.

    missing = _missing_env_vars()
    if missing:
        return CheckResult(
            name="github_auth",
            severity="error",
            status="fail",
            message=(f"github_auth cannot run; missing required env vars: {', '.join(missing)}"),
            details={"missing": missing},
        )

    base_url = os.environ.get(GITHUB_API_BASE_URL_ENV, DEFAULT_GITHUB_API_BASE).rstrip("/")
    token = os.environ["GH_TOKEN"].strip()
    owner = os.environ["GH_OWNER"].strip()

    user_ok, user_msg, user_status = _user_probe(base_url, token)
    if not user_ok:
        return CheckResult(
            name="github_auth",
            severity="error",
            status="fail",
            message=user_msg,
            details={
                "step": "user",
                "status_code": user_status,
                "base_url": base_url,
            },
        )

    scope_ok, scope_msg, scope_status = _repo_scope_probe(base_url, token, owner)
    if not scope_ok:
        return CheckResult(
            name="github_auth",
            severity="error",
            status="fail",
            message=(
                f"GH_TOKEN auth ok (/user returned {user_status}) but "
                f"scope probe failed: {scope_msg}"
            ),
            details={
                "step": "repo_scope",
                "status_code": scope_status,
                "owner": owner,
            },
        )

    return CheckResult(
        name="github_auth",
        severity="error",
        status="pass",
        message=(
            f"GitHub auth resolved (/user {user_status}; "
            f"/repos/{owner}/{TEST_REPO_NAME} scope probe: {scope_msg})."
        ),
        details={
            "user_status": user_status,
            "scope_status": scope_status,
            "owner": owner,
        },
    )
