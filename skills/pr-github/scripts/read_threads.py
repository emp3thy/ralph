"""``pr-github read-threads`` -- list PR review threads + comments.

GraphQL endpoint:
    POST https://api.github.com/graphql
    query: query ReadThreads($owner, $repo, $number) { ... }
    docs: https://docs.github.com/en/graphql/reference/objects#pullrequestreviewthread

Stdout JSON: { "pr_id", "repo", "threads": [ { "id", "status",
                "is_deleted", "context", "comments": [...] } ] }.
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
    HttpError,
    fail,
    handle_http_error,
    load_client,
    parse_pr_id,
)

READ_THREADS_QUERY = """
query ReadThreads($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          startLine
          comments(first: 100) {
            nodes {
              id
              databaseId
              body
              createdAt
              author { login }
            }
          }
        }
      }
    }
  }
}
"""


def _project_comment(raw: dict[str, Any]) -> dict[str, Any]:
    author_obj = raw.get("author") or {}
    author = (
        str(author_obj.get("login"))
        if isinstance(author_obj, dict) and author_obj.get("login")
        else ""
    )
    database_id = raw.get("databaseId")
    return {
        "id": int(database_id) if isinstance(database_id, int) else None,
        "comment_type": "text",
        "content": str(raw.get("body") or ""),
        "published_date": (str(raw.get("createdAt")) if raw.get("createdAt") is not None else None),
        "author": author,
    }


def _project_context(raw: dict[str, Any]) -> dict[str, Any] | None:
    path = raw.get("path")
    line = raw.get("line")
    start_line = raw.get("startLine")
    if not path or (line is None and start_line is None):
        return None
    right_start = start_line if isinstance(start_line, int) else line
    right_end = line if isinstance(line, int) else start_line
    return {
        "file_path": str(path),
        "right_start_line": (int(right_start) if isinstance(right_start, int) else None),
        "right_end_line": (int(right_end) if isinstance(right_end, int) else None),
    }


def _project_thread(raw: dict[str, Any]) -> dict[str, Any]:
    is_resolved = bool(raw.get("isResolved", False))
    comments_obj = raw.get("comments") or {}
    comments_nodes = comments_obj.get("nodes") if isinstance(comments_obj, dict) else None
    return {
        "id": str(raw.get("id") or ""),
        "status": "closed" if is_resolved else "active",
        "is_deleted": False,
        "context": _project_context(raw),
        "comments": [_project_comment(c) for c in (comments_nodes or []) if isinstance(c, dict)],
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pr-github read-threads",
        description=(
            "Read every review thread + comments for a PR. Output is "
            "JSON on stdout. Reads GH_TOKEN and GH_OWNER from the "
            "environment."
        ),
    )
    parser.add_argument("--repo", required=True, help="GitHub repository name.")
    parser.add_argument("--pr-id", required=True, help="Pull request number.")
    parser.add_argument(
        "--include-deleted",
        action="store_true",
        help=(
            "Accepted for parity with pr-ado; has no effect on GitHub "
            "(GitHub doesn't expose deleted threads)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        pr_id = parse_pr_id(args.pr_id)
        if not args.repo.strip():
            raise FatalError("--repo must be non-empty")
        repo = args.repo.strip()
        client = load_client()

        print(
            f"reading threads for PR {pr_id} in {client.owner}/{repo}...",
            file=sys.stderr,
        )
        data = client.graphql(
            READ_THREADS_QUERY,
            variables={
                "owner": client.owner,
                "repo": repo,
                "number": pr_id,
            },
            operation_name="ReadThreads",
        )

        repository = data.get("repository") if isinstance(data, dict) else None
        pull_request = repository.get("pullRequest") if isinstance(repository, dict) else None
        if pull_request is None:
            raise FatalError(f"pull request #{pr_id} not found in {client.owner}/{repo}")
        review_threads = (
            pull_request.get("reviewThreads") if isinstance(pull_request, dict) else None
        )
        nodes = (review_threads.get("nodes") if isinstance(review_threads, dict) else None) or []

        threads = [_project_thread(t) for t in nodes if isinstance(t, dict)]

        summary: dict[str, Any] = {
            "pr_id": pr_id,
            "repo": repo,
            "threads": threads,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    except FatalError as exc:
        return fail(str(exc))
    except HttpError as exc:
        return handle_http_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
