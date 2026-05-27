"""``workitem-fetch-github`` skill entry point.

Fetches one GitHub Issue and emits the normalised work-item JSON document
(defined in ``schema.py``) on stdout. Host-pure: knows nothing about ADO.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.gh_client import GhClient, GhError  # noqa: E402

_SCHEMA_PATH = Path(__file__).with_name("schema.py")
_spec = importlib.util.spec_from_file_location("wi_schema", _SCHEMA_PATH)
assert _spec is not None and _spec.loader is not None
_schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_schema)
validate = _schema.validate


TYPE_LABELS_BUG: frozenset[str] = frozenset({"bug"})
TYPE_LABELS_FEATURE: frozenset[str] = frozenset({"enhancement", "feature"})

SEVERITY_LABELS: dict[str, str] = {
    "priority/critical": "critical",
    "severity/critical": "critical",
    "critical": "critical",
    "priority/high": "high",
    "severity/high": "high",
    "high": "high",
    "priority/low": "low",
    "severity/low": "low",
    "low": "low",
    "priority/normal": "normal",
    "severity/normal": "normal",
    "normal": "normal",
}

ACCEPTANCE_HEADING_RE = re.compile(
    r"^\s*##+\s*(acceptance criteria|acceptance)\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)
REPRO_HEADING_RE = re.compile(
    r"^\s*##+\s*(reproduce|reproduction|steps to reproduce|repro)\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)
NEXT_HEADING_RE = re.compile(r"^\s*##+\s+\S", flags=re.MULTILINE)
IMAGE_MD_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>https?://[^)]+)\)")
TASK_REF_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*#(?P<num>\d+)", flags=re.MULTILINE)


class _FatalError(RuntimeError):
    """Raised internally to signal a clean exit with code 2."""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="workitem-fetch-github",
        description=(
            "Fetch a GitHub Issue and emit a normalised work-item JSON "
            "document on stdout. Requires GH_TOKEN (and GH_OWNER if --repo "
            "is not given) in the environment."
        ),
    )
    parser.add_argument("--work-item", required=True, help="Issue number (e.g. 42 or #42).")
    parser.add_argument(
        "--repo",
        help="Repo slug 'owner/name'. If omitted, uses GH_OWNER + --repo-name.",
    )
    parser.add_argument("--repo-name", help="Repo name (combined with GH_OWNER).")
    parser.add_argument(
        "--include-children",
        action="store_true",
        help="Populate child_ids from task-list references (- [ ] #N).",
    )
    return parser.parse_args(argv)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise _FatalError(f"environment variable {name} is required")
    return value


def _resolve_repo(args: argparse.Namespace) -> tuple[str, str]:
    if args.repo:
        parts = args.repo.split("/", 1)
        if len(parts) != 2 or not all(parts):
            raise _FatalError(f"--repo must be 'owner/name', got {args.repo!r}")
        return parts[0], parts[1]
    owner = _require_env("GH_OWNER")
    if not args.repo_name:
        raise _FatalError("either --repo OR --repo-name (with GH_OWNER env) must be given")
    return owner, args.repo_name


def _parse_issue_number(raw: str) -> int:
    raw = raw.strip().lstrip("#")
    if raw.lower().startswith("wi-"):
        raw = raw[3:]
    if not raw.isdigit():
        raise _FatalError(f"issue number {raw!r} must be a positive integer")
    value = int(raw)
    if value <= 0:
        raise _FatalError(f"issue number must be > 0, got {value}")
    return value


def _classify_type(labels: list[str]) -> str:
    lowered = [label.lower() for label in labels]
    for label in lowered:
        if label in TYPE_LABELS_BUG:
            return "bug"
    for label in lowered:
        if label in TYPE_LABELS_FEATURE:
            return "feature"
    return "feature"


def _classify_severity(labels: list[str]) -> str:
    for label in labels:
        mapped = SEVERITY_LABELS.get(label.lower())
        if mapped:
            return mapped
    return "normal"


def _extract_section(body: str, heading_re: re.Pattern[str]) -> str:
    match = heading_re.search(body)
    if not match:
        return ""
    start = match.end()
    next_match = NEXT_HEADING_RE.search(body, pos=start)
    end = next_match.start() if next_match else len(body)
    return body[start:end].strip()


def _extract_children(body: str) -> list[int]:
    return [int(m.group("num")) for m in TASK_REF_RE.finditer(body)]


def _extract_attachment_urls(body: str) -> list[tuple[str, str]]:
    """Return [(filename, url)] for markdown image references in the body."""
    results: list[tuple[str, str]] = []
    for match in IMAGE_MD_RE.finditer(body):
        url = match.group("url")
        name = url.rsplit("/", 1)[-1].split("?", 1)[0] or "attachment"
        results.append((name, url))
    return results


# Hosts that legitimately need (or accept) the GitHub bearer token on
# attachment GETs. Any URL outside this allow-list is fetched WITHOUT
# the auth header so the GH_TOKEN doesn't leak to third-party image
# hosts referenced from issue bodies.
_GITHUB_ATTACHMENT_HOSTS = frozenset(
    {
        "github.com",
        "api.github.com",
        "raw.githubusercontent.com",
        "user-images.githubusercontent.com",
        "private-user-images.githubusercontent.com",
        "objects.githubusercontent.com",
    }
)


def _download(client: GhClient, url: str) -> bytes:
    """Fetch ``url`` and return the body bytes.

    Uses the auth-bearing session ONLY for GitHub-controlled hosts.
    Issue bodies can reference any image URL — without this gate the
    GH_TOKEN bearer header travels to every third-party host. An
    attacker who controls or can observe such a host could capture
    the token.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in _GITHUB_ATTACHMENT_HOSTS:
        response = client._session.get(url, timeout=client.timeout)
    else:
        # Authless fetch — same timeout, but no Authorization header.
        # If a third-party host returns 403 because it needs other
        # creds, surface as GhError just like the GitHub path.
        response = requests.get(url, timeout=client.timeout)
    if not (200 <= response.status_code < 300):
        raise GhError(status_code=response.status_code, body=response.text, url=url)
    return bytes(response.content)


def _build_document(
    *,
    issue: dict[str, Any],
    owner: str,
    repo: str,
    include_children: bool,
    client: GhClient,
) -> dict[str, Any]:
    raw_labels = issue.get("labels") or []
    labels: list[str] = []
    for label in raw_labels:
        if isinstance(label, dict):
            name = label.get("name")
            if isinstance(name, str):
                labels.append(name)
        elif isinstance(label, str):
            labels.append(label)

    body = str(issue.get("body") or "")
    title = str(issue.get("title") or f"Issue {issue.get('number')}")
    number = int(issue["number"])

    pbi_type = _classify_type(labels)
    severity = _classify_severity(labels)

    acceptance = _extract_section(body, ACCEPTANCE_HEADING_RE)
    repro = _extract_section(body, REPRO_HEADING_RE)

    attachments: list[dict[str, str]] = []
    for name, url in _extract_attachment_urls(body):
        print(f"downloading attachment {name} from {url}...", file=sys.stderr)
        content = _download(client, url)
        attachments.append(
            {
                "name": name,
                "url": url,
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        )

    child_ids: list[str] = []
    if include_children:
        child_ids = [f"WI-{n}" for n in _extract_children(body)]

    source_url = str(issue.get("html_url") or f"https://github.com/{owner}/{repo}/issues/{number}")

    user_field = issue.get("user")
    user_login = user_field.get("login") if isinstance(user_field, dict) else None

    return {
        "id": f"WI-{number}",
        "type": pbi_type,
        "severity": severity,
        "title": title,
        "body_markdown": body,
        "acceptance_markdown": acceptance,
        "repro_steps_markdown": repro,
        "attachments": attachments,
        "child_ids": child_ids,
        "parent_id": None,
        "source_url": source_url,
        "source_host": "github",
        "raw": {
            "labels": labels,
            "work_item_type": "issue",
            "user": user_login,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        token = _require_env("GH_TOKEN")
        owner, repo = _resolve_repo(args)
        number = _parse_issue_number(args.work_item)

        print(f"fetching GitHub issue {owner}/{repo}#{number}...", file=sys.stderr)
        client = GhClient(token=token)
        issue = client.get(f"repos/{owner}/{repo}/issues/{number}")
        if not isinstance(issue, dict):
            raise _FatalError("GitHub issue response was not a JSON object")

        document = _build_document(
            issue=issue,
            owner=owner,
            repo=repo,
            include_children=args.include_children,
            client=client,
        )
        errors = validate(document)
        if errors:
            raise _FatalError("fetcher produced an invalid document:\n  - " + "\n  - ".join(errors))
        print(json.dumps(document, indent=2, sort_keys=True))
        return 0
    except _FatalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except GhError as exc:
        print(f"error: GitHub request failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
