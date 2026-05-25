"""``pr-github create-pr`` -- open a PR from a pushed feature branch.

REST endpoint:
    POST https://api.github.com/repos/{owner}/{repo}/pulls
    docs: https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request

Request body shape (subset used by Ralph):
    {
      "title": "<title>",
      "head": "<source-branch>",
      "base": "<target-branch>",
      "body": "<body>",
      "draft": true|false
    }

Stdout: JSON summary with pr_id, repo, source_branch, target_branch,
title, is_draft, url, dry_run.
Stderr: progress lines.
Exit codes: 0 success, 2 validation / IO error, 3 GitHub error.
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
    HttpError,
    fail,
    handle_http_error,
    load_client,
    parse_branch,
)

DEFAULT_TARGET_BRANCH = "main"


def _build_payload(
    source_branch: str,
    target_branch: str,
    title: str,
    body: str | None,
    is_draft: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": title,
        "head": source_branch,
        "base": target_branch,
        "draft": is_draft,
    }
    if body is not None:
        payload["body"] = body
    return payload


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pr-github create-pr",
        description=(
            "Open a pull request from a pushed feature branch. Reads "
            "GH_TOKEN and GH_OWNER from the environment. Prints a JSON "
            "summary to stdout on success."
        ),
    )
    parser.add_argument("--repo", required=True, help="GitHub repository name (without owner).")
    parser.add_argument(
        "--source-branch",
        required=True,
        help="Source branch (bare name; refs/heads/ is stripped).",
    )
    parser.add_argument(
        "--target-branch",
        default=DEFAULT_TARGET_BRANCH,
        help=f"Target branch (default: {DEFAULT_TARGET_BRANCH}).",
    )
    parser.add_argument("--title", required=True, help="PR title.")
    parser.add_argument("--body", default=None, help="PR description (markdown).")
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Open the PR in draft state.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the payload and print the summary but do not POST.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        if not args.repo.strip():
            raise FatalError("--repo must be non-empty")
        source_branch = parse_branch(args.source_branch)
        target_branch = parse_branch(args.target_branch)
        if not args.title.strip():
            raise FatalError("--title must be non-empty")

        payload = _build_payload(
            source_branch=source_branch,
            target_branch=target_branch,
            title=args.title,
            body=args.body,
            is_draft=args.draft,
        )

        if args.dry_run:
            summary: dict[str, Any] = {
                "pr_id": None,
                "repo": args.repo.strip(),
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": args.title,
                "is_draft": args.draft,
                "url": "",
                "dry_run": True,
                "payload": payload,
            }
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0

        client = load_client()
        repo = args.repo.strip()
        print(
            f"creating PR {source_branch} -> {target_branch} in {client.owner}/{repo}...",
            file=sys.stderr,
        )
        response = client.post_rest(f"/repos/{client.owner}/{repo}/pulls", payload)

        if not isinstance(response, dict) or "number" not in response:
            raise FatalError("create-pr response missing 'number': " + json.dumps(response)[:200])

        html_url = response.get("html_url") or response.get("url") or ""
        summary = {
            "pr_id": int(response["number"]),
            "repo": repo,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": str(response.get("title") or args.title),
            "is_draft": bool(response.get("draft", args.draft)),
            "url": str(html_url),
            "dry_run": False,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    except FatalError as exc:
        return fail(str(exc))
    except HttpError as exc:
        return handle_http_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
