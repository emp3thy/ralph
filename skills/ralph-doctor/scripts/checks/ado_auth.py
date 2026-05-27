"""``ado_auth`` check for ``ralph-doctor`` (Phase 2 relocated).

Probes the Azure DevOps REST API against a non-existent PR ID. HTTP 404
proves both that the PAT authenticates AND that the project + repository
routing resolves. Any other status (including 200) is a failure: 200
would mean the PR id we picked is wrong (or somehow now exists), 401/403
mean the PAT is bad, anything else means the org/project/repository
URL pieces do not resolve.

The runner dispatches this check only when ``RALPH_GIT_HOST=ado``.

This check intentionally goes one level below ``scripts.ado_client.AdoClient``
and calls ``requests`` directly: ``AdoClient.get`` raises on 404, but the
doctor needs 404-as-success.

External-service knobs read by this module:

- ``ADO_PAT`` — required. ADO Personal Access Token.
- ``ADO_ORG_URL`` — required. ADO organization URL (e.g.
  ``https://dev.azure.com/example-org``).
- ``ADO_PROJECT`` — required. ADO project name.
- ``ADO_REPOSITORY`` — required. ADO git repository name.
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

NON_EXISTENT_PR_ID = 999999999
REQUEST_TIMEOUT_SECONDS = 10.0
ADO_API_VERSION = "7.1"
REQUIRED_ENV: tuple[str, ...] = (
    "ADO_PAT",
    "ADO_ORG_URL",
    "ADO_PROJECT",
    "ADO_REPOSITORY",
)


def _missing_env_vars() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]


def check(context: CheckContext) -> CheckResult:
    del context  # ado_auth probe is environment-driven; context is unused.

    missing = _missing_env_vars()
    if missing:
        return CheckResult(
            name="ado_auth",
            severity="error",
            status="fail",
            message=(
                f"ado_auth check cannot run; missing required env vars: {', '.join(missing)}"
            ),
            details={"missing": missing},
        )

    pat = os.environ["ADO_PAT"].strip()
    org = os.environ["ADO_ORG_URL"].strip().rstrip("/")
    project = os.environ["ADO_PROJECT"].strip()
    repo = os.environ["ADO_REPOSITORY"].strip()

    url = (
        f"{org}/{project}/_apis/git/repositories/{repo}"
        f"/pullrequests/{NON_EXISTENT_PR_ID}"
    )

    try:
        response = requests.get(
            url,
            auth=("", pat),
            timeout=REQUEST_TIMEOUT_SECONDS,
            params={"api-version": ADO_API_VERSION},
        )
    except requests.RequestException as exc:
        return CheckResult(
            name="ado_auth",
            severity="error",
            status="fail",
            message=f"ADO probe raised: {exc!r}",
            details={"url": url},
        )

    status = response.status_code
    if status == 404:
        return CheckResult(
            name="ado_auth",
            severity="error",
            status="pass",
            message=(
                f"ADO PAT works (404 on non-existent PR {NON_EXISTENT_PR_ID}, as expected)."
            ),
            details={
                "status_code": 404,
                "org": org,
                "project": project,
                "repo": repo,
            },
        )

    preview = (response.text or "")[:200] or "<empty>"
    return CheckResult(
        name="ado_auth",
        severity="error",
        status="fail",
        message=(
            f"ADO probe returned {status} (expected 404); body preview: {preview!r}"
        ),
        details={"status_code": status, "url": url},
    )
