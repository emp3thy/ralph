"""``pr-github show`` -- PR state, CI/status checks, reviewer decisions.

REST endpoints used:
    GET /repos/{owner}/{repo}/pulls/{number}
        docs: https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request
    GET /repos/{owner}/{repo}/pulls/{number}/reviews
        docs: https://docs.github.com/en/rest/pulls/reviews#list-reviews-for-a-pull-request
    GET /repos/{owner}/{repo}/commits/{sha}/check-runs
        docs: https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference

Status mapping (GitHub -> shared cross-host vocabulary):
    open                 -> active
    closed AND merged    -> completed
    closed AND !merged   -> abandoned

Review state -> vote label:
    APPROVED          -> approved
    CHANGES_REQUESTED -> changes-requested
    COMMENTED         -> commented
    DISMISSED         -> no-vote
    PENDING           -> no-vote

Check-run conclusion/status -> state:
    conclusion=success   -> succeeded
    conclusion=failure   -> failed
    conclusion=cancelled -> cancelled
    conclusion=neutral / skipped -> neutral
    status=in_progress / queued (no conclusion) -> pending

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

REVIEW_LABELS: dict[str, str] = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes-requested",
    "COMMENTED": "commented",
    "DISMISSED": "no-vote",
    "PENDING": "no-vote",
}

CHECK_CONCLUSION_TO_STATE: dict[str, str] = {
    "success": "succeeded",
    "failure": "failed",
    "timed_out": "failed",
    "action_required": "failed",
    "cancelled": "cancelled",
    "neutral": "neutral",
    "skipped": "neutral",
    "stale": "neutral",
}


def _strip_refs_prefix(ref: str) -> str:
    prefix = "refs/heads/"
    return ref[len(prefix) :] if ref.startswith(prefix) else ref


def _project_user(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    login = str(raw.get("login") or "")
    return {"display_name": login, "unique_name": login}


def _project_reviewer(review: dict[str, Any]) -> dict[str, Any]:
    user = review.get("user") or {}
    login = str(user.get("login")) if isinstance(user, dict) and user.get("login") else ""
    state = str(review.get("state") or "").upper()
    label = REVIEW_LABELS.get(state, "no-vote")
    vote = 10 if label == "approved" else -10 if label == "changes-requested" else 0
    return {
        "display_name": login,
        "unique_name": login,
        "vote": vote,
        "vote_label": label,
        "is_required": False,  # GitHub reviews are not natively required.
    }


def _project_check_run(raw: dict[str, Any]) -> dict[str, Any]:
    conclusion = raw.get("conclusion")
    status = raw.get("status")
    if conclusion is None:
        state = "pending" if status in ("queued", "in_progress") else "neutral"
    else:
        state = CHECK_CONCLUSION_TO_STATE.get(str(conclusion), "neutral")
    output = raw.get("output") or {}
    summary = (
        str(output.get("summary"))
        if isinstance(output, dict) and output.get("summary") is not None
        else None
    )
    return {
        "name": str(raw.get("name") or ""),
        "genre": "check-run",
        "state": state,
        "description": summary,
        "target_url": (str(raw.get("details_url")) if raw.get("details_url") is not None else None),
    }


def _map_pr_status(state: str, merged: bool) -> str:
    if state == "open":
        return "active"
    if state == "closed" and merged:
        return "completed"
    return "abandoned"


def _map_merge_status(pr: dict[str, Any]) -> str:
    mergeable = pr.get("mergeable")
    mergeable_state = pr.get("mergeable_state")
    if mergeable is True:
        return "mergeable"
    if mergeable is False:
        return "conflicting"
    if isinstance(mergeable_state, str) and mergeable_state:
        return mergeable_state
    return "unknown"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pr-github show",
        description=(
            "Print a JSON summary of a pull request: state, CI/status "
            "checks, reviewer decisions. Reads GH_TOKEN and GH_OWNER "
            "from the environment."
        ),
    )
    parser.add_argument("--repo", required=True, help="GitHub repository name.")
    parser.add_argument("--pr-id", required=True, help="Pull request number.")
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
            f"reading PR {pr_id} state in {client.owner}/{repo}...",
            file=sys.stderr,
        )
        pr: dict[str, Any] = client.get_rest(f"/repos/{client.owner}/{repo}/pulls/{pr_id}")
        if not isinstance(pr, dict):
            raise FatalError("PR response was not a JSON object: " + repr(pr)[:200])

        print(
            f"reading PR {pr_id} reviews in {client.owner}/{repo}...",
            file=sys.stderr,
        )
        reviews_raw = client.get_rest(f"/repos/{client.owner}/{repo}/pulls/{pr_id}/reviews")
        reviews = reviews_raw if isinstance(reviews_raw, list) else []

        head = pr.get("head") or {}
        head_sha = str(head.get("sha")) if isinstance(head, dict) and head.get("sha") else ""

        if head_sha:
            print(
                f"reading check-runs for {head_sha} in {client.owner}/{repo}...",
                file=sys.stderr,
            )
            checks_raw: dict[str, Any] = client.get_rest(
                f"/repos/{client.owner}/{repo}/commits/{head_sha}/check-runs"
            )
            check_runs = checks_raw.get("check_runs", []) if isinstance(checks_raw, dict) else []
        else:
            check_runs = []

        state = str(pr.get("state") or "")
        merged = bool(pr.get("merged", False))

        reviewers = [_project_reviewer(r) for r in reviews if isinstance(r, dict)]
        statuses = [_project_check_run(c) for c in check_runs if isinstance(c, dict)]

        base = pr.get("base") or {}
        source_branch = _strip_refs_prefix(
            str(head.get("ref") or "") if isinstance(head, dict) else ""
        )
        target_branch = _strip_refs_prefix(
            str(base.get("ref") or "") if isinstance(base, dict) else ""
        )

        output: dict[str, Any] = {
            "pr_id": pr_id,
            "repo": repo,
            "title": str(pr.get("title") or ""),
            "status": _map_pr_status(state, merged),
            "merge_status": _map_merge_status(pr),
            "source_branch": source_branch,
            "target_branch": target_branch,
            "is_draft": bool(pr.get("draft", False)),
            "created_by": _project_user(pr.get("user")),
            "creation_date": (
                str(pr.get("created_at")) if pr.get("created_at") is not None else None
            ),
            "closed_date": (str(pr.get("closed_at")) if pr.get("closed_at") is not None else None),
            "url": str(pr.get("html_url") or pr.get("url") or ""),
            "reviewers": reviewers,
            "statuses": statuses,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0

    except FatalError as exc:
        return fail(str(exc))
    except HttpError as exc:
        return handle_http_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
