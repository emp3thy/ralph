"""``auth`` check for ``ralph-doctor``.

POSTs a tiny single-message body to ``/v1/messages/count_tokens`` — the
Anthropic Messages-API token-count endpoint, which requires the API key
to authenticate but does NOT consume token quota. 2xx → pass; non-2xx →
fail.

Under ``RALPH_USE_BEDROCK=1``, the check probes AWS Bedrock instead via
``boto3.client('bedrock').list_foundation_models()`` (the cheapest
control-plane read that proves the IAM role authenticates).

External-service knobs read by this module:

- ``ANTHROPIC_API_KEY`` — required unless ``RALPH_USE_BEDROCK=1``.
- ``ANTHROPIC_BASE_URL`` — optional override; default
  ``https://api.anthropic.com``.
- ``RALPH_USE_BEDROCK`` — set to ``1`` to probe Bedrock instead.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    from dataclasses import dataclass, field
    from typing import Literal

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

ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"
DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"
TOKEN_COUNT_PATH = "/v1/messages/count_tokens"
PROBE_MODEL = "claude-haiku-4-5"
ANTHROPIC_VERSION = "2023-06-01"
REQUEST_TIMEOUT_SECONDS = 10.0
BEDROCK_TIMEOUT_SECONDS = 5.0


def _anthropic_probe() -> CheckResult:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return CheckResult(
            name="auth",
            severity="error",
            status="fail",
            message=("ANTHROPIC_API_KEY is not set (and RALPH_USE_BEDROCK is not enabled)."),
            details={},
        )

    base = os.environ.get(ANTHROPIC_BASE_URL_ENV, DEFAULT_ANTHROPIC_BASE).rstrip("/")
    url = f"{base}{TOKEN_COUNT_PATH}"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    body: dict[str, Any] = {
        "model": PROBE_MODEL,
        "messages": [{"role": "user", "content": "ping"}],
    }

    try:
        response = requests.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return CheckResult(
            name="auth",
            severity="error",
            status="fail",
            message=f"Anthropic auth call raised: {exc!r}",
            details={"url": url},
        )

    status = response.status_code
    if 200 <= status < 300:
        return CheckResult(
            name="auth",
            severity="error",
            status="pass",
            message=f"Anthropic auth resolved on cold start (model={PROBE_MODEL}).",
            details={"status_code": status},
        )

    preview = (response.text or "")[:200]
    return CheckResult(
        name="auth",
        severity="error",
        status="fail",
        message=(f"Anthropic auth probe returned {status}; body preview: {preview!r}"),
        details={"status_code": status, "url": url},
    )


def _bedrock_probe() -> CheckResult:
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError as exc:
        return CheckResult(
            name="auth",
            severity="error",
            status="fail",
            message=(
                f"RALPH_USE_BEDROCK=1 but boto3 is not installed: {exc}. "
                "Install boto3 in the pod image."
            ),
            details={},
        )

    try:
        from botocore.config import Config  # type: ignore[import-not-found]
    except ImportError as exc:
        return CheckResult(
            name="auth",
            severity="error",
            status="fail",
            message=(f"RALPH_USE_BEDROCK=1 but botocore is not installed: {exc}."),
            details={},
        )

    cfg = Config(
        connect_timeout=BEDROCK_TIMEOUT_SECONDS,
        read_timeout=BEDROCK_TIMEOUT_SECONDS,
        retries={"max_attempts": 1},
    )

    try:
        client = boto3.client("bedrock", config=cfg)
        response = client.list_foundation_models()
    except Exception as exc:  # pylint: disable=broad-except
        return CheckResult(
            name="auth",
            severity="error",
            status="fail",
            message=f"Bedrock auth failed: {exc!r}",
            details={},
        )

    if not isinstance(response, dict) or "modelSummaries" not in response:
        return CheckResult(
            name="auth",
            severity="error",
            status="fail",
            message="Bedrock auth call succeeded but response is malformed.",
            details={},
        )

    summaries = response["modelSummaries"]
    count = len(summaries) if isinstance(summaries, list) else 0
    return CheckResult(
        name="auth",
        severity="error",
        status="pass",
        message=(f"Bedrock auth resolved on cold start ({count} foundation models)."),
        details={"model_count": count},
    )


def check(context: CheckContext) -> CheckResult:
    del context  # auth probe is environment-driven; context is unused.
    if os.environ.get("RALPH_USE_BEDROCK", "").strip() == "1":
        return _bedrock_probe()
    return _anthropic_probe()
