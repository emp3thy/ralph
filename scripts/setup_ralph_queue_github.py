"""Set up the ``ralph-queue`` branch and its protection on a GitHub repo.

Reads ``GH_TOKEN`` and ``GH_OWNER`` from the environment.

Operations (in order):

    1. GET /repos/{owner}/{repo}
       — confirm the repository exists and is accessible.
    2. GET /repos/{owner}/{repo}/git/ref/heads/main
       — read the tip SHA of main.
    3. GET /repos/{owner}/{repo}/git/ref/heads/ralph-queue
       — detect whether ralph-queue already exists (404 means absent).
    4. POST /repos/{owner}/{repo}/git/refs
       — create refs/heads/ralph-queue off main's tip (skipped if it exists).
    5. PUT /repos/{owner}/{repo}/branches/ralph-queue/protection
       — apply branch protection (skipped in --dry-run).
    6. Print a JSON summary to stdout, exit 0.

GitHub REST documentation:
    https://docs.github.com/en/rest/repos/repos#get-a-repository
    https://docs.github.com/en/rest/git/refs#get-a-reference
    https://docs.github.com/en/rest/git/refs#create-a-reference
    https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any

import requests

from scripts.gh_client import GhClient, GhError

QUEUE_BRANCH = "ralph-queue"
MAIN_BRANCH = "main"


@dataclass
class SetupResult:
    owner: str
    repo: str
    branch_name: str
    branch_base_sha: str
    branch_created: bool
    branch_existed: bool
    protection_applied: bool
    dry_run: bool


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise _MissingEnv(name)
    return value


class _MissingEnv(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def _lookup_repo(client: GhClient, owner: str, repo: str) -> dict[str, Any]:
    data: dict[str, Any] = client.get(f"/repos/{owner}/{repo}")
    return data


def _read_branch_tip(client: GhClient, owner: str, repo: str, branch: str) -> str | None:
    """Return the tip SHA of ``refs/heads/{branch}`` or ``None`` if absent."""
    try:
        data = client.get(f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
    except GhError as exc:
        if exc.status_code == 404:
            return None
        raise
    if not isinstance(data, dict):
        return None
    obj = data.get("object") or {}
    sha = obj.get("sha")
    return str(sha) if sha else None


def _create_branch(client: GhClient, owner: str, repo: str, branch: str, base_sha: str) -> None:
    payload = {
        "ref": f"refs/heads/{branch}",
        "sha": base_sha,
    }
    client.post(f"/repos/{owner}/{repo}/git/refs", json_body=payload)


def _apply_protection(client: GhClient, owner: str, repo: str, branch: str) -> None:
    """PUT branch protection on ``refs/heads/{branch}``.

    The payload follows the documented shape at
    https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection.

    Intent:
      - ``enforce_admins=True``: admins can't bypass the rules.
      - ``allow_force_pushes=False`` and ``allow_deletions=False``: nothing
        rewrites or removes the queue branch.
      - ``required_status_checks=None``: no CI checks are required at
        bootstrap time. Per-repo overrides can layer them in later.
      - ``required_pull_request_reviews=None``: the queue branch is
        mutated directly by the executor; PR review is not part of the
        queue's lifecycle.
      - ``restrictions=None``: no push-actor restrictions at this layer.
        Push restrictions are enforced via a separate org/repo-level
        policy (documented in the runbook) because the
        ``restrictions`` object is only honoured on organisation-owned
        repos and requires team/app/user lists, which are out of scope
        for the bootstrap script. The runbook covers how to layer
        restrictions in for org repos.
    """
    payload = {
        "required_status_checks": None,
        "enforce_admins": True,
        "required_pull_request_reviews": None,
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_conversation_resolution": False,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }
    client.put(
        f"/repos/{owner}/{repo}/branches/{branch}/protection",
        json_body=payload,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create the ralph-queue branch on a GitHub repo and apply "
            "branch protection. Reads GH_TOKEN and GH_OWNER from the "
            "environment."
        )
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Name of the GitHub repository (without the owner prefix).",
    )
    parser.add_argument(
        "--branch",
        default=QUEUE_BRANCH,
        help=f"Queue branch name (default: {QUEUE_BRANCH}).",
    )
    parser.add_argument(
        "--base-branch",
        default=MAIN_BRANCH,
        help=(f"Base branch the queue branch is created from (default: {MAIN_BRANCH})."),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read state but do not POST/PUT any mutations.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        token = _require_env("GH_TOKEN")
        owner = _require_env("GH_OWNER")
    except _MissingEnv as exc:
        return _fail(f"environment variable {exc.name} is required")

    client = GhClient(token=token)

    try:
        print(f"resolving repo {owner}/{args.repo!r}...", file=sys.stderr)
        _lookup_repo(client, owner, args.repo)

        print(f"reading tip of {args.base_branch}...", file=sys.stderr)
        base_sha = _read_branch_tip(client, owner, args.repo, args.base_branch)
        if base_sha is None:
            return _fail(f"base branch {args.base_branch!r} has no tip in {owner}/{args.repo}")

        print(
            f"checking whether {args.branch} already exists...",
            file=sys.stderr,
        )
        existing_tip = _read_branch_tip(client, owner, args.repo, args.branch)
        branch_existed = existing_tip is not None
        branch_created = False
        if not branch_existed:
            if args.dry_run:
                print(
                    f"DRY-RUN would create {args.branch} off {base_sha}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"creating {args.branch} off {base_sha}...",
                    file=sys.stderr,
                )
                _create_branch(client, owner, args.repo, args.branch, base_sha)
                branch_created = True

        protection_applied = False
        if args.dry_run:
            print(
                f"DRY-RUN would PUT protection on refs/heads/{args.branch}",
                file=sys.stderr,
            )
        else:
            print(
                f"applying branch protection on {args.branch}...",
                file=sys.stderr,
            )
            _apply_protection(client, owner, args.repo, args.branch)
            protection_applied = True

        result = SetupResult(
            owner=owner,
            repo=args.repo,
            branch_name=args.branch,
            branch_base_sha=base_sha,
            branch_created=branch_created,
            branch_existed=branch_existed,
            protection_applied=protection_applied,
            dry_run=args.dry_run,
        )
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    except GhError as exc:
        return _fail(str(exc))
    except requests.exceptions.RequestException as exc:
        return _fail(f"network error: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
