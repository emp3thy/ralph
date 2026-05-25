# `ralph-queue` setup runbook

This runbook covers bootstrapping the `ralph-queue` branch and its
protections on a service repository so that Ralph (the per-repo
autonomous coding loop) can use it as the queue substrate.

Ralph supports two git hosts. The branch model is identical across hosts;
only the protection mechanism differs. Use the section that matches your
pod's `RALPH_GIT_HOST` value.

- **Phase 1 — GitHub** (`RALPH_GIT_HOST=github`): use
  `scripts/setup_ralph_queue_github.py`. Covered fully below.
- **Phase 2 — Azure DevOps** (`RALPH_GIT_HOST=ado`): use
  `scripts/setup_ralph_queue_ado.py`. Phase 2 is deferred; the
  section below stubs the runbook and is expanded when Phase 2
  is implemented.

## What `ralph-queue` is for

Ralph keeps its queue state — the `.ralph/` folder tree containing
`inbox/`, `current/`, `pending-pr/`, `done/`, `blocked/`, and
`archive/` — on a dedicated long-lived branch called `ralph-queue` in
the same project repo as the code Ralph is working on. The branch is
mutated by two parties only: the human supervisor skills (`ralph-add`,
`ralph-status`, `ralph-cancel`, `ralph-promote`, `ralph-triage`) and
the executor itself. PRs are never opened from `ralph-queue` to
`main`; the branch exists purely to keep queue churn out of `main`'s
history while still benefiting from git's durability and
reproducibility.

---

# Phase 1 — GitHub

## Prerequisites (GitHub)

- **A GitHub repository.** Existing repo with a `main` branch and at
  least one commit.
- **A Personal Access Token (PAT) with `repo` scope.** A fine-grained
  PAT works too — it must grant *Contents: Read and write* and
  *Administration: Read and write* on the target repo (administration
  is required to PUT branch protection).
- **Environment variables for the helper script:**
  - `GH_TOKEN` — the PAT value above.
  - `GH_OWNER` — the GitHub org or user that owns the repo (e.g.
    `emp3thy`).

## Option A — the helper script (recommended)

Run from a checkout of the ralph repo:

```bash
export GH_TOKEN="..."
export GH_OWNER="emp3thy"

uv run python scripts/setup_ralph_queue_github.py --repo service-auth
```

The script is idempotent: re-running it against a repo where
`ralph-queue` already exists is a no-op for the branch (it reports
`branch_created: false`, `branch_existed: true`) and a re-PUT of the
protection payload (GitHub's PUT is idempotent for branch protection).

Add `--dry-run` to see what would happen without making any changes:

```bash
uv run python scripts/setup_ralph_queue_github.py \
    --repo service-auth \
    --dry-run
```

Sample successful JSON summary (printed to stdout):

```json
{
  "branch_base_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "branch_created": true,
  "branch_existed": false,
  "branch_name": "ralph-queue",
  "dry_run": false,
  "owner": "emp3thy",
  "protection_applied": true,
  "repo": "service-auth"
}
```

## Option B — the GitHub web UI

Useful when you only need to bootstrap one or two repos and don't want
to fiddle with PATs.

### B1. Create the `ralph-queue` branch off `main`

1. Navigate to `https://github.com/<owner>/<repo>/branches`.
2. Click **New branch**.
3. **New branch name:** `ralph-queue`.
4. **Source:** `main`.
5. Click **Create branch**.

### B2. Add the branch protection rule

1. Navigate to `https://github.com/<owner>/<repo>/settings/branches`.
2. Click **Add branch protection rule**.
3. **Branch name pattern:** `ralph-queue`.
4. Configure:
   - **Require a pull request before merging:** off (the queue is
     mutated directly).
   - **Require status checks to pass before merging:** off (can be
     enabled later if you add CI for queue mutations).
   - **Require conversation resolution before merging:** off.
   - **Require signed commits:** at your discretion.
   - **Require linear history:** off.
   - **Require deployments to succeed before merging:** off.
   - **Lock branch:** off (Ralph and supervisor skills need to push).
   - **Do not allow bypassing the above settings:** ON
     (this is the `enforce_admins=True` toggle).
   - **Restrict who can push to matching branches:** ON if you have
     an org plan; add the Ralph service principal and the supervisor
     group as the only allowed actors.
   - **Allow force pushes:** OFF.
   - **Allow deletions:** OFF.
5. Click **Create**.

### B3. Restrict push access (org repos, optional)

For org-owned repos, the **Restrict who can push to matching
branches** option in B2 is the canonical way to enforce push
restrictions. The helper script leaves this option off because it
requires team/app/user lists that vary per org; add them manually
from the UI for any org-owned repo.

## Option C — the `gh` CLI

For engineers who prefer the command line but don't want to invoke
the Python helper. Requires `gh` ≥ 2.40 with `gh auth login`
completed.

### C1. Authenticate

```bash
gh auth login --hostname github.com
```

Or, for headless / scripted use, export `GH_TOKEN` and `gh` will pick
it up automatically.

### C2. Resolve the main-branch tip and create `ralph-queue`

```bash
OWNER="emp3thy"
REPO="service-auth"

MAIN_SHA=$(gh api "repos/$OWNER/$REPO/git/ref/heads/main" \
    --jq .object.sha)

gh api "repos/$OWNER/$REPO/git/refs" \
    --method POST \
    --field ref="refs/heads/ralph-queue" \
    --field sha="$MAIN_SHA"
```

Expected: a JSON object with `"ref": "refs/heads/ralph-queue"` and an
`object.sha` matching `$MAIN_SHA`.

### C3. Apply branch protection

```bash
gh api "repos/$OWNER/$REPO/branches/ralph-queue/protection" \
    --method PUT \
    --raw-field required_status_checks=null \
    --raw-field required_pull_request_reviews=null \
    --raw-field restrictions=null \
    -F enforce_admins=true \
    -F allow_force_pushes=false \
    -F allow_deletions=false
```

Expected: a JSON object whose `url` ends in
`/branches/ralph-queue/protection`.

## Verification (GitHub, all three options)

### V1. `ralph-queue` exists and points at the right tip

```bash
gh api "repos/$OWNER/$REPO/git/ref/heads/ralph-queue" \
    --jq '{ref: .ref, sha: .object.sha}'
```

Expected: `ref: refs/heads/ralph-queue` and a `sha` matching `main`'s
tip at bootstrap time.

### V2. Branch protection is in place

```bash
gh api "repos/$OWNER/$REPO/branches/ralph-queue/protection" \
    --jq '{
      enforce_admins: .enforce_admins.enabled,
      allow_force_pushes: .allow_force_pushes.enabled,
      allow_deletions: .allow_deletions.enabled
    }'
```

Expected:

```json
{
  "enforce_admins": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
```

### V3. The helper script's idempotency check passes

```bash
uv run python scripts/setup_ralph_queue_github.py --repo service-auth
```

Expected JSON contains `"branch_created": false` and
`"branch_existed": true`, `"protection_applied": true`.

## Troubleshooting (GitHub)

| Symptom | Likely cause | Fix |
|---|---|---|
| `GitHub 404 from .../repos/<owner>/<repo>: Not Found` | `--repo` name does not match a repo accessible to `GH_TOKEN`, OR `GH_OWNER` is wrong. | Confirm with `gh repo list <owner>`. |
| `GitHub 401 from ...: Bad credentials` | `GH_TOKEN` is expired or missing the `repo` scope. | Regenerate the PAT with `repo` scope (or, for fine-grained, *Contents: R/W* + *Administration: R/W* on the target repo). |
| `GitHub 403 from .../protection: ...Resource not accessible by integration` | Fine-grained PAT lacks *Administration: Read and write*. | Edit the PAT, grant administration write, re-run. |
| `GitHub 422 from .../git/refs: Reference already exists` | A concurrent setup run created `ralph-queue` between this script's GET-ref and POST-ref. | Re-run; the second run's GET will see the ref and skip creation. |
| Helper script exits with `error: environment variable GH_TOKEN is required`. | Shell didn't have `GH_TOKEN` exported. | Export `GH_TOKEN` and `GH_OWNER` in the same shell before running. |

---

# Phase 2 — Azure DevOps (deferred)

Phase 2 is the ADO equivalent of Phase 1 and is built later (likely
by Ralph itself, running against Phase 1's GitHub setup). When Phase
2 is delivered, the artifacts will be:

- `scripts/ado_client.py` — minimal PAT-authenticated ADO REST 7.1 client.
- `scripts/setup_ralph_queue_ado.py` — creates `ralph-queue` off `main`
  and creates a Require-Reviewer policy scoped to
  `refs/heads/ralph-queue`.

The Phase 2 prerequisites, helper-script invocation, ADO web UI
steps, and `az` CLI fallback are written into this same runbook
file by Plan 2 Task 11 (deferred). Until then, do NOT bootstrap an
ADO repo for Ralph — the Phase 2 tooling is the only supported
path.
