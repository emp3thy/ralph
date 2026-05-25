"""``pr-github set-status`` -- resolve / re-open a review thread.

Maps the shared cross-host status vocabulary onto GitHub's single
resolved bit:
    fixed   -> resolveReviewThread (isResolved=true)
    closed  -> resolveReviewThread (isResolved=true)
    active  -> unresolveReviewThread (isResolved=false)
    pending -> unresolveReviewThread (isResolved=false)
    wontFix, byDesign -> REJECTED with exit code 2 (human-only)

GraphQL mutations:
    mutation Resolve($threadId: ID!)   { resolveReviewThread(...)   { thread { id isResolved } } }
    mutation Unresolve($threadId: ID!) { unresolveReviewThread(...) { thread { id isResolved } } }
    docs: https://docs.github.com/en/graphql/reference/mutations#resolvereviewthread

Stdout JSON: { "pr_id", "repo", "thread_id", "status",
               "previous_status" }.

``previous_status`` is inferred from the requested direction:
    requesting a resolve -> previously "active" (unresolved)
    requesting an unresolve -> previously "closed" (resolved)
This is a deliberate approximation: GitHub's API does not return the
prior state, so we report what the request *implies* the prior state
was. The contract matches pr-ado where ``previous_status`` reflects
what was overwritten.

Exit codes: 0 success, 2 validation (including the human-only
rejection), 3 GitHub error.
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
    ALLOWED_STATUSES,
    HUMAN_ONLY_STATUSES,
    FatalError,
    HttpError,
    fail,
    handle_http_error,
    load_client,
    parse_pr_id,
    parse_thread_id,
)

ARGPARSE_CHOICES = tuple(list(ALLOWED_STATUSES) + list(HUMAN_ONLY_STATUSES))

RESOLVE_MUTATION = """
mutation Resolve($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}
"""

UNRESOLVE_MUTATION = """
mutation Unresolve($threadId: ID!) {
  unresolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}
"""

# Status -> (mutation, operation_name, is_resolve)
STATUS_ROUTING: dict[str, tuple[str, str, bool]] = {
    "fixed": (RESOLVE_MUTATION, "Resolve", True),
    "closed": (RESOLVE_MUTATION, "Resolve", True),
    "active": (UNRESOLVE_MUTATION, "Unresolve", False),
    "pending": (UNRESOLVE_MUTATION, "Unresolve", False),
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pr-github set-status",
        description=(
            "Set a PR review thread's status. Accepts active, pending, "
            "fixed, closed (mapped onto GitHub's resolve / unresolve). "
            "Rejects wontFix and byDesign (ADO-specific, human-only). "
            "Reads GH_TOKEN and GH_OWNER from the environment."
        ),
    )
    parser.add_argument("--repo", required=True, help="GitHub repository name.")
    parser.add_argument("--pr-id", required=True, help="Pull request number.")
    parser.add_argument(
        "--thread-id",
        required=True,
        help="Review thread GraphQL node id (opaque string).",
    )
    parser.add_argument(
        "--status",
        required=True,
        choices=ARGPARSE_CHOICES,
        help=(
            "Thread status. Ralph-safe: "
            + ", ".join(ALLOWED_STATUSES)
            + ". Rejected (human-only): "
            + ", ".join(HUMAN_ONLY_STATUSES)
            + "."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        if args.status in HUMAN_ONLY_STATUSES:
            raise FatalError(
                f"thread status {args.status!r} is ADO-specific and "
                "human-only; pr-github rejects it for parity. For GitHub, "
                "leave the thread open and discuss via comments instead "
                "of marking wontFix / byDesign."
            )
        if args.status not in STATUS_ROUTING:
            # Defence in depth: argparse choices should make this unreachable.
            raise FatalError(
                f"thread status {args.status!r} is not recognised; "
                f"expected one of {', '.join(ALLOWED_STATUSES)}"
            )

        pr_id = parse_pr_id(args.pr_id)
        thread_id = parse_thread_id(args.thread_id)
        if not args.repo.strip():
            raise FatalError("--repo must be non-empty")
        repo = args.repo.strip()
        client = load_client()

        mutation, operation_name, is_resolve = STATUS_ROUTING[args.status]

        print(
            f"setting thread {thread_id} on PR {pr_id} to {args.status} ({operation_name})...",
            file=sys.stderr,
        )
        data = client.graphql(
            mutation,
            variables={"threadId": thread_id},
            operation_name=operation_name,
        )

        if not isinstance(data, dict):
            raise FatalError("set-status response was not a JSON object: " + repr(data)[:200])

        previous_status = "active" if is_resolve else "closed"

        summary: dict[str, Any] = {
            "pr_id": pr_id,
            "repo": repo,
            "thread_id": thread_id,
            "status": args.status,
            "previous_status": previous_status,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    except FatalError as exc:
        return fail(str(exc))
    except HttpError as exc:
        return handle_http_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
