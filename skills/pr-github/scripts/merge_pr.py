"""``pr-github merge_pr`` -- merge a PR that GitHub reports as mergeable.

REST endpoint:
    PUT https://api.github.com/repos/{owner}/{repo}/pulls/{number}/merge
    docs: https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request

Request body (subset used by Ralph):
    {
      "merge_method": "merge"|"squash"|"rebase",
      "commit_title": "<optional>",
      "commit_message": "<optional>"
    }

Stdout: JSON {pr_id, repo, merged: bool, sha: str|null, message: str}.
Stderr: progress lines and errors.

Exit codes:
    0  success — PR merged.
    2  validation / IO error before any HTTP call (incl. argparse).
    3  GitHub returned an unexpected error (422, 5xx, etc.).
    4  Race / refused-by-host: 405 Method Not Allowed or 409 Conflict.
       Caller (sweep) treats this as "not ready" and retries next iter.
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
    RaceError,
    fail,
    handle_http_error,
    handle_race_error,
    load_client,
    parse_pr_id,
)

MERGE_METHODS = ("merge", "squash", "rebase")
DEFAULT_MERGE_METHOD = "squash"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pr-github merge_pr",
        description=(
            "Merge a pull request via GitHub's PUT /pulls/{n}/merge endpoint. "
            "Reads GH_TOKEN and GH_OWNER from the environment. Prints a JSON "
            "summary to stdout on success. Exit 4 means the merge was refused "
            "(405/409) and the caller should retry."
        ),
    )
    parser.add_argument("--repo", required=True, help="GitHub repository name (without owner).")
    parser.add_argument("--pr-id", required=True, help="PR number (bare positive integer).")
    parser.add_argument(
        "--merge-method",
        choices=MERGE_METHODS,
        default=DEFAULT_MERGE_METHOD,
        help=f"Merge method (default: {DEFAULT_MERGE_METHOD}).",
    )
    parser.add_argument(
        "--commit-title",
        default=None,
        help="Optional commit title for the merge commit.",
    )
    parser.add_argument(
        "--commit-message",
        default=None,
        help="Optional commit message body for the merge commit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        if not args.repo.strip():
            raise FatalError("--repo must be non-empty")
        repo = args.repo.strip()
        pr_id = parse_pr_id(args.pr_id)

        body: dict[str, Any] = {"merge_method": args.merge_method}
        if args.commit_title is not None:
            body["commit_title"] = args.commit_title
        if args.commit_message is not None:
            body["commit_message"] = args.commit_message

        client = load_client()
        print(
            f"merging PR {pr_id} in {client.owner}/{repo} via {args.merge_method}...",
            file=sys.stderr,
        )
        payload = client.put_rest(
            f"/repos/{client.owner}/{repo}/pulls/{pr_id}/merge",
            body,
        )

        if isinstance(payload, dict):
            merged = bool(payload.get("merged", True))
            raw_sha = payload.get("sha")
            sha: str | None = str(raw_sha) if isinstance(raw_sha, str) else None
            message = str(payload.get("message", ""))
        else:
            merged = True
            sha = None
            message = ""

        summary = {
            "pr_id": pr_id,
            "repo": repo,
            "merged": merged,
            "sha": sha,
            "message": message,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    except FatalError as exc:
        return fail(str(exc))
    except RaceError as exc:
        return handle_race_error(exc)
    except HttpError as exc:
        return handle_http_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
