"""``pr-github reply`` -- post a reply to a PR review thread.

GraphQL mutation:
    POST https://api.github.com/graphql
    mutation Reply($threadId: ID!, $body: String!) {
      addPullRequestReviewThreadReply(
        input: {pullRequestReviewThreadId: $threadId, body: $body}
      ) { comment { id databaseId body createdAt author { login } } }
    }
    docs: https://docs.github.com/en/graphql/reference/mutations#addpullrequestreviewthreadreply

Stdout JSON: { "comment_id", "thread_id", "pr_id", "repo", "posted_at" }.
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
    parse_thread_id,
)

REPLY_MUTATION = """
mutation Reply($threadId: ID!, $body: String!) {
  addPullRequestReviewThreadReply(
    input: {pullRequestReviewThreadId: $threadId, body: $body}
  ) {
    comment {
      id
      databaseId
      body
      createdAt
      author { login }
    }
  }
}
"""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pr-github reply",
        description=(
            "Post a reply to an existing PR review thread. Reads "
            "GH_TOKEN and GH_OWNER from the environment."
        ),
    )
    parser.add_argument("--repo", required=True, help="GitHub repository name.")
    parser.add_argument("--pr-id", required=True, help="Pull request number.")
    parser.add_argument(
        "--thread-id",
        required=True,
        help=("Review thread id from read-threads (GraphQL node id; treated as an opaque string)."),
    )
    parser.add_argument(
        "--text",
        required=True,
        help="Reply body (markdown). Must be non-empty.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        pr_id = parse_pr_id(args.pr_id)
        thread_id = parse_thread_id(args.thread_id)
        if not args.repo.strip():
            raise FatalError("--repo must be non-empty")
        if not isinstance(args.text, str) or not args.text.strip():
            raise FatalError("--text must be non-empty")
        repo = args.repo.strip()
        client = load_client()

        print(
            f"posting reply to thread {thread_id} on PR {pr_id} in {client.owner}/{repo}...",
            file=sys.stderr,
        )
        data = client.graphql(
            REPLY_MUTATION,
            variables={"threadId": thread_id, "body": args.text},
            operation_name="Reply",
        )

        mutation_result = (
            data.get("addPullRequestReviewThreadReply") if isinstance(data, dict) else None
        )
        comment = mutation_result.get("comment") if isinstance(mutation_result, dict) else None
        if not isinstance(comment, dict) or "databaseId" not in comment:
            raise FatalError("reply response missing comment.databaseId: " + json.dumps(data)[:200])

        summary: dict[str, Any] = {
            "comment_id": int(comment["databaseId"]),
            "thread_id": thread_id,
            "pr_id": pr_id,
            "repo": repo,
            "posted_at": (
                str(comment.get("createdAt")) if comment.get("createdAt") is not None else None
            ),
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    except FatalError as exc:
        return fail(str(exc))
    except HttpError as exc:
        return handle_http_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
