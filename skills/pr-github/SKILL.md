---
name: pr-github
description: Drive GitHub pull requests from Ralph. Six operations — create-pr, read-threads, reply, set-status, show, merge_pr — each one a Python script under scripts/ that calls the GitHub REST and GraphQL APIs. Use from Ralph's autonomous loop to open the initial PR after pushing a feature branch, to read PR review threads, to reply to reviewer comments, to mark threads resolved, to print a summary of PR state, and (when sweep's auto-merge flag is on) to merge a PR that GitHub reports as clean. wontFix and byDesign thread statuses are human-only; set-status rejects them with a clear error. This skill is host-pure (GitHub only); the executor stages it as `pr/` when RALPH_GIT_HOST=github.
---

# pr-github

## What this skill does

The `pr-github` skill is Ralph's only handle on a GitHub pull request.
It contains six Python entry scripts under `skills/pr-github/scripts/`,
one per operation. Each script uses argparse, prints a JSON summary to
stdout on success, sends progress to stderr, and calls the GitHub REST
(`https://api.github.com`) and GraphQL (`https://api.github.com/graphql`)
endpoints directly via `requests`. Tests live at
`tests/skills/test_pr_github.py`.

## When to use it

Use `pr-github` whenever Ralph needs to talk to a GitHub pull request.
The executor's loop driver invokes it after pushing a feature branch
(`create-pr`), the sweep invokes it to read PR state (`show`,
`read-threads`), and Ralph's PR-feedback work item invokes
`reply` + `set-status` for each addressed reviewer comment.

The executor stages this skill as `pr/` (not `pr-github/`) when
`RALPH_GIT_HOST=github`. The operation surface and JSON shapes are
identical to `pr-ado`; PROMPT.md and the executor only ever see the
generic `pr` name.

## Operations

### `create-pr` — open a PR from a feature branch to `main`

```bash
uv run python skills/pr-github/scripts/create_pr.py \
    --repo service-auth \
    --source-branch ralph/WI-1234 \
    --target-branch main \
    --title "WI-1234: add /healthz endpoint" \
    --body "Closes #1234. Implementation follows PLAN.md."
```

| Flag | Required | Description |
|---|---|---|
| `--repo <name>` | yes | GitHub repository name (without the owner; owner comes from `GH_OWNER`). |
| `--source-branch <branch>` | yes | The feature branch that's already been pushed. Bare branch name; the script strips any `refs/heads/` prefix. |
| `--target-branch <branch>` | no (default `main`) | The branch the PR targets. |
| `--title <text>` | yes | PR title. |
| `--body <text>` | no | PR description in markdown. |
| `--draft` | no | Open the PR in draft state. |
| `--dry-run` | no | Compute the payload but do not POST. |

Returns a JSON object with `pr_id`, `repo`, `source_branch`,
`target_branch`, `title`, `is_draft`, `url`, `dry_run`. Exit codes:
`0` success, `2` validation / IO error, `3` GitHub REST returned non-2xx.

REST endpoint: `POST https://api.github.com/repos/{owner}/{repo}/pulls`
(docs: https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request).
Request body: `{"title": ..., "head": <source>, "base": <target>, "body": ..., "draft": ...}`.

### `read-threads` — list PR review threads + comments + resolved state

```bash
uv run python skills/pr-github/scripts/read_threads.py \
    --repo service-auth \
    --pr-id 4242
```

| Flag | Required | Description |
|---|---|---|
| `--repo <name>` | yes | GitHub repository name. |
| `--pr-id <int>` | yes | The pull request *number* (GitHub calls it the issue/PR number). |
| `--include-deleted` | no | Has no effect on GitHub (GitHub doesn't expose deleted threads); accepted for parity with `pr-ado`. |

Returns a JSON object with `pr_id`, `repo`, `threads`. Each thread is
`{id, status, is_deleted, context, comments: [{id, comment_type, content,
published_date, author}]}`. The `status` is projected onto the shared
vocabulary: `closed` if GitHub reports `isResolved=true`, otherwise
`active`. `is_deleted` is always `false` on GitHub. `context` is
`{file_path, right_start_line, right_end_line}` derived from the first
comment in the thread, or `null` for non-line threads. Exit codes:
`0`, `2`, `3` as above.

GraphQL endpoint: `POST https://api.github.com/graphql`
(docs: https://docs.github.com/en/graphql/reference/objects#pullrequestreviewthread).
Query (real GraphQL):
```graphql
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
```

### `reply` — post a reply to a review thread

```bash
uv run python skills/pr-github/scripts/reply.py \
    --repo service-auth \
    --pr-id 4242 \
    --thread-id "PRRT_kwDOABCD12345" \
    --text "Applied; see commit abcdef0 — moved the validation into the dispatcher."
```

| Flag | Required | Description |
|---|---|---|
| `--repo <name>` | yes | GitHub repository name. |
| `--pr-id <int>` | yes | Pull request number. |
| `--thread-id <id>` | yes | Review thread id from `read-threads` (a GitHub GraphQL node id, e.g. `PRRT_kwDOABCD12345`). Treated as an opaque string. |
| `--text <text>` | yes | Reply body (markdown). Must be non-empty. |

Returns a JSON object with `comment_id`, `thread_id`, `pr_id`, `repo`,
`posted_at`. `comment_id` is GitHub's `databaseId` on the new comment.
Exit codes: `0`, `2`, `3` as above.

GraphQL mutation:
```graphql
mutation Reply($threadId: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $threadId, body: $body}) {
    comment {
      id
      databaseId
      body
      createdAt
      author { login }
    }
  }
}
```
(docs: https://docs.github.com/en/graphql/reference/mutations#addpullrequestreviewthreadreply).

### `set-status` — resolve / re-open a review thread

```bash
uv run python skills/pr-github/scripts/set_status.py \
    --repo service-auth \
    --pr-id 4242 \
    --thread-id "PRRT_kwDOABCD12345" \
    --status fixed
```

| Flag | Required | Description |
|---|---|---|
| `--repo <name>` | yes | GitHub repository name. |
| `--pr-id <int>` | yes | Pull request number. (Accepted for parity with the other ops; not used in the GraphQL mutation.) |
| `--thread-id <id>` | yes | Review thread id (GraphQL node id, opaque string). |
| `--status <enum>` | yes | One of `active`, `pending`, `fixed`, `closed`. **`wontFix` and `byDesign` are human-only and rejected with exit 2 plus a `human-only` error message.** |

**Mapping to GitHub's resolved/unresolved model** (single resolved bit;
documented in this SKILL.md and enforced by the script):
- `fixed` → call `resolveReviewThread` (sets `isResolved=true`)
- `closed` → call `resolveReviewThread` (sets `isResolved=true`)
- `active` → call `unresolveReviewThread` (sets `isResolved=false`)
- `pending` → call `unresolveReviewThread` (sets `isResolved=false`)
- `wontFix` → reject (human-only)
- `byDesign` → reject (human-only)

GitHub does not have `wontFix` or `byDesign` statuses. They are rejected
at argparse choice level so PROMPT.md doesn't need to know about host
differences — the rejection error message says: "this status is
ADO-specific and human-only — for GitHub, leave the thread open and
discuss via comments". Same rejection contract as `pr-ado/set_status.py`.

Returns a JSON object with `pr_id`, `repo`, `thread_id`, `status` (the
shared vocabulary value the caller requested), `previous_status`
(`active` if the thread was previously unresolved, `closed` if it was
previously resolved). Exit codes: `0`, `2`, `3` as above.

GraphQL mutations:
```graphql
mutation Resolve($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}

mutation Unresolve($threadId: ID!) {
  unresolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}
```
(docs: https://docs.github.com/en/graphql/reference/mutations#resolvereviewthread).

### `show` — current PR state, CI results, reviewer decisions

```bash
uv run python skills/pr-github/scripts/show.py \
    --repo service-auth \
    --pr-id 4242
```

| Flag | Required | Description |
|---|---|---|
| `--repo <name>` | yes | GitHub repository name. |
| `--pr-id <int>` | yes | Pull request number. |

Returns a JSON object with `pr_id`, `repo`, `title`, `status` (mapped:
`open` → `active`; merged → `completed`; closed-not-merged →
`abandoned`), `merge_status` (`mergeable`/`conflicting`/`unknown`),
`source_branch`, `target_branch`, `is_draft`, `created_by`
(`{display_name, unique_name}`), `creation_date`, `closed_date`, `url`,
plus `reviewers` (each `{display_name, unique_name, vote, vote_label,
is_required}` — `vote_label` is `approved` / `changes-requested` /
`commented` / `no-vote`) and `statuses` (check-runs per the latest
commit, each `{name, genre, state, description, target_url}`).
Exit codes: `0`, `2`, `3` as above.

REST endpoints used:
- `GET /repos/{owner}/{repo}/pulls/{number}`
  (docs: https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request)
- `GET /repos/{owner}/{repo}/pulls/{number}/reviews`
  (docs: https://docs.github.com/en/rest/pulls/reviews#list-reviews-for-a-pull-request)
- `GET /repos/{owner}/{repo}/commits/{sha}/check-runs`
  (docs: https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference)

The output also includes a raw `mergeable_state` field carrying GitHub's
verbatim mergeability string (`clean`, `dirty`, `blocked`, `behind`,
`unstable`, `unknown`, …) or JSON `null` when GitHub's async mergeability
check has not yet resolved. Sweep's auto-merge predicate keys off the
exact value `"clean"`.

### `merge_pr` — merge a PR that GitHub reports as mergeable

```bash
uv run python skills/pr-github/scripts/merge_pr.py \
    --repo service-auth \
    --pr-id 4242 \
    --merge-method squash
```

| Flag | Required | Description |
|---|---|---|
| `--repo <name>` | yes | GitHub repository name. |
| `--pr-id <int>` | yes | Pull request number. |
| `--merge-method <enum>` | no (default `squash`) | One of `merge`, `squash`, `rebase`. argparse rejects anything else with exit 2. |
| `--commit-title <text>` | no | Optional commit title for the merge commit. |
| `--commit-message <text>` | no | Optional commit message body for the merge commit. |

Returns a JSON object with `pr_id`, `repo`, `merged` (bool), `sha` (the
resulting merge commit SHA, or `null`), `message` (GitHub's response
message, e.g. `"Pull Request successfully merged"`).

Exit codes (this operation has an extra exit 4; see the table below):
`0` success, `2` validation / IO error, `3` GitHub returned an unexpected
non-2xx (422, 5xx, …), `4` race / refused-by-host — GitHub returned 405
Method Not Allowed or 409 Conflict. Sweep treats exit 4 as "not ready
right now" and retries on the next iteration; the PBI stays in
`pending-pr/`.

REST endpoint: `PUT https://api.github.com/repos/{owner}/{repo}/pulls/{number}/merge`
(docs: https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request).
Request body: `{"merge_method": ..., "commit_title": ..., "commit_message": ...}`
(the two commit fields are omitted from the body when not supplied).

The skill itself does not check `mergeable_state` before calling — that
predicate lives in the caller (sweep's `decide_action`). The skill is
intentionally narrow: send the merge, classify the response.

## Environment variables

| Variable | Purpose |
|---|---|
| `GH_TOKEN` | GitHub PAT (classic or fine-grained). Required scopes: `repo` (read/write contents + pull requests) and `read:org` if the repo is in an organisation. |
| `GH_OWNER` | The GitHub org or user that owns the repo (e.g. `myorg`). The full repo url is `{owner}/{repo}`. |

Every script reads these from the environment and exits with code 2 and
a message naming the missing variable when any are absent.

## Exit codes (every operation)

| Code | Meaning |
|---|---|
| 0 | Success. JSON summary on stdout. |
| 2 | Validation / IO error before any HTTP call (bad CLI arg, missing env var, malformed id, human-only status). Message on stderr. |
| 3 | GitHub REST or GraphQL returned non-2xx (or a 200 response with a non-empty `errors` array on GraphQL). The error message is forwarded to stderr. |
| 4 | Race / refused-by-host: GitHub returned 405 Method Not Allowed or 409 Conflict on a merge attempt. Only emitted by `merge_pr`. Caller (sweep) treats this as "not ready" and retries on the next iteration. |

## Thread status semantics

GitHub has a single resolved bit per thread; this skill projects the
ADO four-status vocabulary onto it:

| Requested status | Set by `set-status` | What it does on GitHub |
|---|---|---|
| `active` | yes | Calls `unresolveReviewThread` — thread becomes unresolved. |
| `pending` | yes | Calls `unresolveReviewThread` — thread becomes unresolved. (Same effect as `active` on GitHub.) |
| `fixed` | yes | Calls `resolveReviewThread` — thread becomes resolved. |
| `closed` | yes | Calls `resolveReviewThread` — thread becomes resolved. (Same effect as `fixed` on GitHub.) |
| `wontFix` | NO — rejected with exit 2 | ADO-specific human-only judgement. GitHub has no analogue. |
| `byDesign` | NO — rejected with exit 2 | ADO-specific human-only judgement. GitHub has no analogue. |

## How tests invoke the scripts

```python
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "create_pr_script",
    Path("skills/pr-github/scripts/create_pr.py"),
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
exit_code = module.main([...])
```

Same pattern for all six entry scripts.

## What this skill does NOT do

- It does NOT clone, fetch, or push git refs. The caller is responsible
  for pushing the feature branch before invoking `create-pr`.
- It does NOT set `wontFix` or `byDesign` thread statuses — those are
  ADO-specific human-only decisions. The rejection is preserved on
  GitHub for cross-host parity so PROMPT.md doesn't need to know which
  host it's running against.
- It does NOT modify the PR title or body after creation. Updates to a
  PR happen by pushing more commits to the feature branch.
- It does NOT manage branch policies; that is `scripts/setup_ralph_queue_github.py`'s
  job (Plan 2 Phase 1).
- It does NOT open an MCP server; Ralph invokes the skill via the
  Claude Code `Skill` tool, which spawns the Python scripts directly.
