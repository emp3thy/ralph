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

Branch-protection payload notes (apply to both ``main`` and ``ralph-queue``):

- ``enforce_admins=True``: organisation admins cannot bypass the protection
  rules. The GitHub API accepts this flag on every plan, but actual enforcement
  on personal repositories and lower-tier organisation plans depends on the
  plan. The runbook documents the plan-level caveat; do not assume the flag
  alone guarantees admin enforcement on every repo.
- ``restrictions=None``: no push-actor restrictions are applied at bootstrap.
  GitHub only honours ``restrictions`` on organisation-owned repos and the
  payload must enumerate teams/apps/users, so any actor-level lockdown is
  layered on top of this bootstrap by a separate step (org-only).
- ``required_status_checks=None``: no CI checks are required at bootstrap.
  Per-repo overrides can layer status-check requirements in later once CI
  has stabilised; the bootstrap deliberately leaves the door open.
"""

from __future__ import annotations

import argparse
import base64 as _b64
import json
import os
import sys
from dataclasses import asdict, dataclass

import requests

from scripts.gh_client import GhClient, GhError

QUEUE_BRANCH = "ralph-queue"
MAIN_BRANCH = "main"
QUEUE_STATE_FOLDERS = ("inbox", "current", "pending-pr", "blocked", "archive", "done")


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


def _ensure_repo_exists(
    client: GhClient,
    owner: str,
    repo: str,
    *,
    org: str | None,
    dry_run: bool,
) -> bool:
    """Return True if the repo already existed; False if it was just created
    (or would be created under --dry-run).

    ``--dry-run`` is read-only: when the repo is absent we log what a real run
    would do and return ``False`` so ``main`` can emit the dry-run summary
    without firing the create POST.
    """
    try:
        client.get(f"/repos/{owner}/{repo}")
        return True
    except GhError as exc:
        if exc.status_code != 404:
            raise
    if dry_run:
        print(
            f"DRY-RUN would create {owner}/{repo} (private)",
            file=sys.stderr,
        )
        return False  # do NOT POST in dry-run mode
    print(f"creating {owner}/{repo}...", file=sys.stderr)
    # auto_init=False: GitHub's auto-init would commit a default README on main,
    # which then short-circuits _seed_main_readme via _content_exists and prevents
    # the custom README from landing. With auto_init=False, the first _put_content
    # PUT on main creates both the branch and the README in one commit.
    payload = {"name": repo, "private": True, "auto_init": False}
    if org is not None:
        client.post(f"/orgs/{org}/repos", json_body=payload)
    else:
        client.post("/user/repos", json_body=payload)
    return False


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


def _base_protection_payload() -> dict:
    """Common protection payload shared by main + queue-branch protection.

    Anything overlaid by the two callers (PR-review requirements, queue-branch-specific
    flags) goes on top of this base. Centralising the shared flags here prevents
    drift between the two payloads. See the module docstring for the rationale
    on ``enforce_admins``, ``restrictions``, and ``required_status_checks``.
    """
    return {
        "required_status_checks": None,
        "enforce_admins": True,
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }


def _apply_main_protection(client: GhClient, owner: str, repo: str, branch: str) -> None:
    """Base-branch protection: require PR (1 approval), no force-push, no deletion.

    See the module docstring for the rationale on ``enforce_admins``
    (plan-level enforcement on personal repos) and ``restrictions``
    (org-only, layered separately).
    """
    payload = _base_protection_payload()
    payload["required_pull_request_reviews"] = {
        "dismiss_stale_reviews": False,
        "require_code_owner_reviews": False,
        "required_approving_review_count": 1,
    }
    client.put(
        f"/repos/{owner}/{repo}/branches/{branch}/protection",
        json_body=payload,
    )


def _apply_queue_branch_protection(client: GhClient, owner: str, repo: str, branch: str) -> None:
    """ralph-queue branch protection: no force-push, no deletion.

    No PR requirement -- the executor pushes directly. The extra
    ``required_conversation_resolution`` / ``lock_branch`` / ``allow_fork_syncing``
    flags explicitly set the recommended defaults so a future GitHub-side API
    change can't silently flip them. See the module docstring for the shared
    flag rationale.
    """
    payload = _base_protection_payload()
    payload["required_pull_request_reviews"] = None
    payload["required_conversation_resolution"] = False
    payload["lock_branch"] = False
    payload["allow_fork_syncing"] = False
    client.put(
        f"/repos/{owner}/{repo}/branches/{branch}/protection",
        json_body=payload,
    )


def _content_exists(client: GhClient, owner: str, repo: str, path: str, ref: str) -> bool:
    """Return True if ``path`` exists on ``ref`` in the repo, False on 404."""
    try:
        client.get(f"/repos/{owner}/{repo}/contents/{path}?ref={ref}")
        return True
    except GhError as exc:
        if exc.status_code == 404:
            return False
        raise


def _put_content(
    client: GhClient,
    owner: str,
    repo: str,
    path: str,
    *,
    branch: str,
    content_bytes: bytes,
    message: str,
    dry_run: bool,
) -> bool:
    """Idempotent file PUT. Returns True if a new commit was created."""
    if _content_exists(client, owner, repo, path, branch):
        return False
    if dry_run:
        print(f"DRY-RUN would PUT {path} on {branch}", file=sys.stderr)
        return False
    payload = {
        "message": message,
        "content": _b64.b64encode(content_bytes).decode("ascii"),
        "branch": branch,
    }
    client.put(f"/repos/{owner}/{repo}/contents/{path}", json_body=payload)
    return True


def _seed_main_readme(
    client: GhClient,
    owner: str,
    repo: str,
    *,
    base_branch: str,
    queue_branch: str,
    dry_run: bool,
) -> bool:
    readme = (
        f"# {repo}\n\n"
        f"Queue repo for ralph-executor. Queue state lives on the `{queue_branch}` branch.\n"
    )
    return _put_content(
        client,
        owner,
        repo,
        "README.md",
        branch=base_branch,
        content_bytes=readme.encode("utf-8"),
        message="docs: seed README",
        dry_run=dry_run,
    )


def _seed_ralph_skeleton(
    client: GhClient, owner: str, repo: str, queue_branch: str, *, dry_run: bool
) -> None:
    """Seed `.ralph/<state>/.gitkeep` placeholders on the queue branch."""
    for folder in QUEUE_STATE_FOLDERS:
        _put_content(
            client,
            owner,
            repo,
            f".ralph/{folder}/.gitkeep",
            branch=queue_branch,
            content_bytes=b"",
            message=f"chore(queue): seed {folder}/",
            dry_run=dry_run,
        )


_RALPH_CONFIG_STUB = (
    "# Per-queue ralph-executor config.\n"
    "# Settings here are loaded by the executor + operator skills.\n"
    "# See docs/superpowers/specs/2026-05-28-queue-repo-split-design.md.\n"
)


def _seed_ralph_config_stub(
    client: GhClient, owner: str, repo: str, queue_branch: str, *, dry_run: bool
) -> None:
    """Seed `.ralph/config.toml` stub on the queue branch (idempotent)."""
    _put_content(
        client,
        owner,
        repo,
        ".ralph/config.toml",
        branch=queue_branch,
        content_bytes=_RALPH_CONFIG_STUB.encode("utf-8"),
        message="chore(queue): seed .ralph/config.toml stub",
        dry_run=dry_run,
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
    parser.add_argument(
        "--org",
        default=None,
        help="Create the repo under this organisation (default: under the authenticated user).",
    )
    parser.add_argument(
        "--no-protection",
        action="store_true",
        help="Skip applying branch-protection rules (for sandboxes / test repos).",
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
        repo_existed = _ensure_repo_exists(
            client, owner, args.repo, org=args.org, dry_run=args.dry_run
        )
        if not repo_existed and args.dry_run:
            # The repo doesn't exist yet; downstream steps (read main tip,
            # create queue branch, apply protection) would all 404. Emit a
            # summary describing what a real run would do and exit clean.
            result = SetupResult(
                owner=owner,
                repo=args.repo,
                branch_name=args.branch,
                branch_base_sha="",
                branch_created=False,
                branch_existed=False,
                protection_applied=False,
                dry_run=True,
            )
            print(json.dumps(asdict(result), indent=2, sort_keys=True))
            return 0

        # Seed main README FIRST. For a freshly-created repo (auto_init=False),
        # main does not exist yet — _put_content on a non-existent branch creates
        # both the file and the branch in one commit. Seeding here also ensures
        # _read_branch_tip below finds a tip on the next line. Idempotent for
        # already-provisioned repos: _content_exists short-circuits.
        _seed_main_readme(
            client,
            owner,
            args.repo,
            base_branch=args.base_branch,
            queue_branch=args.branch,
            dry_run=args.dry_run,
        )

        print(f"reading tip of {args.base_branch}...", file=sys.stderr)
        base_sha = _read_branch_tip(client, owner, args.repo, args.base_branch)
        if base_sha is None:
            # Under --dry-run on a fresh repo, _seed_main_readme is a no-op
            # ("would PUT") so main has no tip — accept this and skip the
            # downstream branch + protection steps with a dry-run summary.
            if args.dry_run:
                print(
                    f"DRY-RUN: {args.base_branch!r} has no tip; would have been "
                    f"created by README seed. Skipping branch creation + protection.",
                    file=sys.stderr,
                )
                result = SetupResult(
                    owner=owner,
                    repo=args.repo,
                    branch_name=args.branch,
                    branch_base_sha="",
                    branch_created=False,
                    branch_existed=False,
                    protection_applied=False,
                    dry_run=True,
                )
                print(json.dumps(asdict(result), indent=2, sort_keys=True))
                return 0
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
                try:
                    _create_branch(client, owner, args.repo, args.branch, base_sha)
                    branch_created = True
                except GhError as exc:
                    # 422 "Reference already exists" — a concurrent run created
                    # ralph-queue between our GET-ref (step 3) and our POST-ref
                    # (step 4). Treat as already-existed and continue to
                    # branch-protection so the repo doesn't end up unprotected.
                    if exc.status_code == 422:
                        print(
                            f"{args.branch} was created concurrently; continuing",
                            file=sys.stderr,
                        )
                        branch_existed = True
                    else:
                        raise

        # Seed .ralph/ skeleton on queue_branch (idempotent per file)
        _seed_ralph_skeleton(client, owner, args.repo, args.branch, dry_run=args.dry_run)
        # Seed .ralph/config.toml stub on queue_branch (idempotent)
        _seed_ralph_config_stub(client, owner, args.repo, args.branch, dry_run=args.dry_run)

        # Protection-handling precedence: --no-protection always wins (even
        # under --dry-run), then --dry-run, then the real PUT. Keeping the
        # branches independent makes the output transparent about which
        # flag suppressed the PUT.
        protection_applied = False
        if args.no_protection:
            print("skipping branch protection (--no-protection)", file=sys.stderr)
        elif args.dry_run:
            print(
                f"DRY-RUN would PUT protection on {args.base_branch} and {args.branch}",
                file=sys.stderr,
            )
        else:
            print(f"applying branch protection on {args.base_branch}...", file=sys.stderr)
            _apply_main_protection(client, owner, args.repo, args.base_branch)
            print(
                f"applying branch protection on {args.branch}...",
                file=sys.stderr,
            )
            _apply_queue_branch_protection(client, owner, args.repo, args.branch)
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
