"""Tiny GitHub REST client used by the Ralph setup and skill scripts.

Wraps ``requests.Session`` with:
  - Bearer-token authentication using a Personal Access Token (PAT)
    taken from the ``token`` constructor argument (typically
    sourced from the ``GH_TOKEN`` env var by the caller).
  - GitHub's recommended request headers
    (``Accept: application/vnd.github+json`` and
    ``X-GitHub-Api-Version: 2022-11-28``).
  - JSON body encoding for POST/PUT/PATCH.
  - A ``GhError`` raised on non-2xx responses.

Reference: https://docs.github.com/en/rest/overview/authenticating-to-the-rest-api
"""

from __future__ import annotations

from typing import Any

import requests

DEFAULT_BASE_URL = "https://api.github.com"
DEFAULT_API_VERSION = "2022-11-28"
DEFAULT_TIMEOUT_SECONDS = 30.0


class GhError(RuntimeError):
    """Raised when the GitHub REST API returns a non-2xx status."""

    def __init__(self, status_code: int, body: str, url: str) -> None:
        super().__init__(f"GitHub {status_code} from {url}: {body}")
        self.status_code = status_code
        self.body = body
        self.url = url


class GhClient:
    """Minimal GitHub REST client. One instance per token."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("token must be a non-empty string")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")

        # Strip surrounding whitespace BEFORE storing. The validation above
        # uses .strip() to test emptiness but does not assign back; the raw
        # value then leaks into the Authorization header / URL, producing
        # silent 401s.
        token = token.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.api_version = api_version
        self.timeout = timeout
        self._session = session or requests.Session()

        # Note: any pre-existing Authorization on an injected session is overwritten.
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": api_version,
                "User-Agent": "ralph-setup-script",
            }
        )

    def _build_url(self, path: str) -> str:
        path = path.lstrip("/")
        return f"{self.base_url}/{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        url = self._build_url(path)

        headers: dict[str, str] = {}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)

        response = self._session.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers or None,
            timeout=self.timeout,
        )
        if not (200 <= response.status_code < 300):
            raise GhError(response.status_code, response.text, response.url)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Any:
        return self._request("GET", path, params=params)

    def post(
        self,
        path: str,
        *,
        json_body: Any,
        params: dict[str, str] | None = None,
    ) -> Any:
        return self._request("POST", path, json_body=json_body, params=params)

    def put(
        self,
        path: str,
        *,
        json_body: Any,
        params: dict[str, str] | None = None,
    ) -> Any:
        return self._request("PUT", path, json_body=json_body, params=params)

    def patch(
        self,
        path: str,
        *,
        json_body: Any,
        params: dict[str, str] | None = None,
    ) -> Any:
        return self._request("PATCH", path, json_body=json_body, params=params)

    def delete(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Any:
        return self._request("DELETE", path, params=params)
