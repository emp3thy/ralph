"""Shared helpers for the ``pr-github`` skill's entry scripts.

- ``load_client()`` reads ``GH_TOKEN`` and ``GH_OWNER`` from the
  environment and returns a ``GitHubClient`` bound to that owner.
- ``parse_pr_id`` accepts a bare positive integer.
- ``parse_thread_id`` accepts any non-empty opaque string (GitHub uses
  GraphQL node ids like ``PRRT_kwDOABCD12345``).
- ``FatalError`` is the in-process signal for validation / IO error;
  the wrapping ``main`` of each entry script catches it and returns 2.
- ``HttpError`` is raised by ``GitHubClient`` on non-2xx HTTP responses
  (excluding 405/409) or on GraphQL responses with a non-empty ``errors``
  array. Mapped to exit code 3 by ``handle_http_error``.
- ``RaceError`` is raised by ``GitHubClient`` on 405 Method Not Allowed
  or 409 Conflict responses (used by ``merge_pr.py`` to distinguish
  "PR not ready / someone pushed during the call" from generic GitHub
  errors). Mapped to exit code 4 by ``handle_race_error``.
- ``ALLOWED_STATUSES`` / ``HUMAN_ONLY_STATUSES`` are referenced by
  ``set_status.py`` so the enum lives in exactly one place. The set is
  the shared cross-host vocabulary, not GitHub-native.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import requests

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = f"{GITHUB_API}/graphql"
USER_AGENT = "ralph-pr-github/1.0"


# Shared cross-host status vocabulary. GitHub maps these onto a single
# resolved bit (see set_status.py); the rejection set is preserved so
# PROMPT.md doesn't need to know which host it's running against.
ALLOWED_STATUSES: tuple[str, ...] = ("active", "pending", "fixed", "closed")
HUMAN_ONLY_STATUSES: tuple[str, ...] = ("wontFix", "byDesign")


class FatalError(RuntimeError):
    """Raised on validation / IO failure. Mapped to exit code 2."""


class HttpError(RuntimeError):
    """Raised on GitHub HTTP / GraphQL failure. Mapped to exit code 3."""


class RaceError(RuntimeError):
    """Raised on 405 Method Not Allowed or 409 Conflict from GitHub.

    Mapped to exit code 4 by ``handle_race_error``. Currently only
    surfaced by ``merge_pr.py`` (GitHub's documented response for a PR
    that became un-mergeable between the check and the merge call, or
    for a head-SHA conflict from a racing push).
    """


def fail(message: str) -> int:
    """Print ``message`` to stderr and return exit code 2."""
    print(f"error: {message}", file=sys.stderr)
    return 2


def handle_http_error(error: HttpError) -> int:
    """Print an :class:`HttpError` to stderr and return exit code 3."""
    print(f"github error: {error}", file=sys.stderr)
    return 3


def handle_race_error(error: RaceError) -> int:
    """Print a :class:`RaceError` to stderr and return exit code 4."""
    print(f"github race: {error}", file=sys.stderr)
    return 4


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise FatalError(f"environment variable {name} is required")
    return value


@dataclass
class GitHubClient:
    """Minimal GitHub REST + GraphQL client.

    Owner is fixed at construction time (from ``GH_OWNER``); per-call
    methods take ``repo`` and the rest of the path.
    """

    owner: str
    token: str
    session: requests.Session

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Accept": accept,
            "Authorization": f"Bearer {self.token}",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_rest(self, path: str) -> Any:
        url = f"{GITHUB_API}{path}"
        response = self.session.get(url, headers=self._headers(), timeout=30)
        return self._read_rest_response(response)

    def post_rest(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{GITHUB_API}{path}"
        response = self.session.post(url, headers=self._headers(), json=body, timeout=30)
        return self._read_rest_response(response)

    def put_rest(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{GITHUB_API}{path}"
        response = self.session.put(url, headers=self._headers(), json=body, timeout=30)
        if response.status_code in (405, 409):
            raise RaceError(
                f"GitHub REST PUT {response.request.url} returned HTTP "
                f"{response.status_code}: " + GitHubClient._summarise_body(response)
            )
        return self._read_rest_response(response)

    def graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        if operation_name is not None:
            payload["operationName"] = operation_name
        response = self.session.post(
            GITHUB_GRAPHQL,
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        if response.status_code >= 400:
            raise HttpError(
                f"GraphQL request failed with HTTP {response.status_code}: "
                + self._summarise_body(response)
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise HttpError("GraphQL response was not JSON: " + response.text[:200]) from exc
        if isinstance(body, dict) and body.get("errors"):
            messages = "; ".join(
                str(err.get("message", err)) if isinstance(err, dict) else str(err)
                for err in body["errors"]
            )
            raise HttpError(f"GraphQL errors: {messages}")
        return body.get("data", {}) if isinstance(body, dict) else body

    @staticmethod
    def _read_rest_response(response: requests.Response) -> Any:
        if response.status_code >= 400:
            raise HttpError(
                f"GitHub REST {response.request.method} "
                f"{response.request.url} failed with HTTP "
                f"{response.status_code}: " + GitHubClient._summarise_body(response)
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise HttpError("REST response was not JSON: " + response.text[:200]) from exc

    @staticmethod
    def _summarise_body(response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:200]
        if isinstance(body, dict):
            message = body.get("message")
            if isinstance(message, str) and message:
                errors = body.get("errors")
                if isinstance(errors, list) and errors:
                    return f"{message} ({json.dumps(errors)[:120]})"
                return message
        return json.dumps(body)[:200]


def load_client() -> GitHubClient:
    """Build the GitHub client. Raises ``FatalError`` on missing env."""
    token = _require_env("GH_TOKEN")
    owner = _require_env("GH_OWNER")
    session = requests.Session()
    return GitHubClient(owner=owner, token=token, session=session)


def parse_pr_id(raw: str) -> int:
    """Parse ``--pr-id`` argv. Bare positive integer only."""
    stripped = (raw or "").strip()
    if not stripped.lstrip("-").isdigit():
        raise FatalError(f"pr id {stripped!r} must be a positive integer")
    try:
        value = int(stripped)
    except ValueError as exc:
        raise FatalError(f"pr id {stripped!r} must be a positive integer") from exc
    if value <= 0:
        raise FatalError(f"pr id {stripped!r} must be a positive integer")
    return value


def parse_thread_id(raw: str) -> str:
    """Parse ``--thread-id`` argv. Any non-empty opaque string.

    GitHub uses GraphQL node ids (e.g. ``PRRT_kwDOABCD12345``); we don't
    enforce a format beyond non-empty.
    """
    stripped = (raw or "").strip()
    if not stripped:
        raise FatalError("thread id must be non-empty")
    return stripped


def parse_branch(raw: str) -> str:
    """Normalise a branch argument. Strip a leading ``refs/heads/``."""
    stripped = (raw or "").strip()
    if not stripped:
        raise FatalError("branch name must be non-empty")
    prefix = "refs/heads/"
    if stripped.startswith(prefix):
        stripped = stripped[len(prefix) :]
    return stripped


__all__ = [
    "ALLOWED_STATUSES",
    "FatalError",
    "GITHUB_API",
    "GITHUB_GRAPHQL",
    "GitHubClient",
    "HUMAN_ONLY_STATUSES",
    "HttpError",
    "RaceError",
    "fail",
    "handle_http_error",
    "handle_race_error",
    "load_client",
    "parse_branch",
    "parse_pr_id",
    "parse_thread_id",
]
