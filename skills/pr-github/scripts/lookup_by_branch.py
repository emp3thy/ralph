"""``pr-github lookup_by_branch`` -- resolve a PR by source branch name.

REST endpoints used:
    GET /repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=all
        docs: https://docs.github.com/en/rest/pulls/pulls#list-pull-requests
    GET /repos/{owner}/{repo}/branches/{branch}  (only when
        --include-branch-check is set)
        docs: https://docs.github.com/en/rest/branches/branches#get-a-branch

Used by ralph_executor.sweep.reconcile to resolve orphan pending-pr/
directories (those without PR-LINK.md) back to a PR state so the sweep
loop can move them to done/, blocked/, or inbox/.

State mapping (GitHub -> reconcile vocabulary):
    state=open                          -> "open"
    state=closed AND merged_at != null  -> "merged"
    state=closed AND merged_at == null  -> "closed"

This vocab is DISTINCT from show.py's cross-host active/completed/
abandoned because reconcile must distinguish merged from closed-unmerged
to pick the right destination directory.

Exit codes: 0 success, 2 validation, 3 GitHub error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _common import (  # noqa: E402
    FatalError,
    GitHubClient,
    HttpError,
    fail,
    handle_http_error,
    load_client,
)

# _map_pr_state below raises HttpError (not FatalError) on unexpected GitHub
# states. The string came from the API response, so an unknown value is a
# host-response problem — exit 3 (via main's HttpError handler) makes
# reconcile.py treat it as KEEP_API_ERROR and retry next sweep, instead of
# exiting 1 with an uncaught traceback.


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lookup_by_branch",
        description=(
            "Resolve a PR by source branch name. Used by sweep to "
            "reconcile orphan pending-pr/ entries."
        ),
    )
    parser.add_argument(
        "--branch",
        required=True,
        help="Source branch name (e.g. ralph/PBI-XYZ).",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="GitHub repository name (without owner).",
    )
    parser.add_argument(
        "--include-branch-check",
        action="store_true",
        help=(
            "Also call GET /branches/{branch} to determine if the branch "
            "exists on origin. Adds one API call per invocation."
        ),
    )
    return parser


def _map_pr_state(raw_state: str, merged_at: str | None) -> str:
    """Map GitHub's (state, merged_at) to the reconcile vocab."""
    if raw_state == "open":
        return "open"
    if raw_state == "closed":
        if merged_at:
            return "merged"
        return "closed"
    raise HttpError(f"unexpected GitHub PR state: {raw_state!r}")


def _lookup_pr(
    client: GitHubClient,
    repo: str,
    branch: str,
) -> dict[str, Any] | None:
    """Return the first matching PR or None.

    GitHub's ``head=owner:branch`` filter is exact-match. At most one
    open PR per source branch is allowed by GitHub policy; closed PRs
    can stack. We take the most recent (first item; the API returns
    newest first by default) as the canonical match.
    """
    path = f"/repos/{client.owner}/{repo}/pulls"
    response = client.session.get(
        f"https://api.github.com{path}",
        headers=client._headers(),
        params={"head": f"{client.owner}:{branch}", "state": "all"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise HttpError(
            f"GET {path}?head={client.owner}:{branch}: "
            f"HTTP {response.status_code}: {response.text[:200]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HttpError(f"GET {path} returned non-JSON: {response.text[:200]}") from exc
    if not isinstance(payload, list):
        raise HttpError(f"GET {path} returned non-list: {type(payload).__name__}")
    if not payload:
        return None
    first = payload[0]
    if not isinstance(first, dict):
        raise HttpError(f"GET {path} first item not a dict: {type(first).__name__}")
    return {
        "state": _map_pr_state(str(first.get("state") or ""), first.get("merged_at")),
        "url": str(first.get("html_url") or ""),
        "pr_id": int(first["number"]),
        "merged_at": first.get("merged_at"),
    }


def _lookup_branch(client: GitHubClient, repo: str, branch: str) -> bool:
    """Return True if the branch exists on origin, False if 404."""
    path = f"/repos/{client.owner}/{repo}/branches/{branch}"
    response = client.session.get(
        f"https://api.github.com{path}",
        headers=client._headers(),
        timeout=30,
    )
    if response.status_code == 404:
        return False
    if response.status_code >= 400:
        raise HttpError(f"GET {path}: HTTP {response.status_code}: {response.text[:200]}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    branch = args.branch.strip()
    repo = args.repo.strip()
    if not branch:
        return fail("--branch must be non-empty")
    if not repo:
        return fail("--repo must be non-empty")

    try:
        client = load_client()
    except FatalError as err:
        return fail(str(err))

    try:
        pr = _lookup_pr(client, repo, branch)
        branch_exists: bool | None = None
        if args.include_branch_check:
            branch_exists = _lookup_branch(client, repo, branch)
    except HttpError as err:
        return handle_http_error(err)

    print(json.dumps({"pr": pr, "branch_exists": branch_exists}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
