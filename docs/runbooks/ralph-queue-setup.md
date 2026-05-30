# Setting up a queue repo

The queue repo holds your ralph queue state: PBIs in
`.ralph/{inbox,current,pending-pr,blocked,archive,done}/`. The executor and
operator skills (`ralph-add`, `ralph-cancel`, `ralph-promote`, `ralph-triage`,
`ralph-status`) read and write this repo on a working branch (`ralph-queue`
by default), while `main` stays protected and clean.

Ralph supports two git hosts. The branch model is identical across hosts;
only the provisioning helper and protection mechanism differ. Phase 1
(GitHub) is shipped; Phase 2 (Azure DevOps) is deferred and stubbed at the
bottom of this file.

## One command, end to end (GitHub)

```bash
GH_TOKEN=<token> GH_OWNER=<owner> \
    uv run python -m scripts.setup_ralph_queue_github --repo <repo-name>
```

Creates (idempotent on re-run):

1. The GitHub repository (private; `--org <name>` to create under an org).
2. `main` branch with a stub README.
3. `ralph-queue` branch off `main` with the `.ralph/` state-folder skeleton
   AND a `.ralph/config.toml` stub.
4. Branch protection: `main` requires 1-approval PRs + no force-push + no
   deletion; `ralph-queue` no force-push + no deletion.

Flags:

- `--repo NAME` — repo name without owner prefix (required).
- `--branch NAME` — queue branch name (default: `ralph-queue`).
- `--base-branch NAME` — base branch the queue branch is created off
  (default: `main`).
- `--org NAME` — create under organisation `NAME` instead of the
  authenticated user.
- `--dry-run` — read state, log what would change, no mutations.
- `--no-protection` — skip the branch-protection PUTs (sandboxes / test
  repos).

The script exits 0 on success and prints a JSON summary to stdout — see
`SetupResult` in `scripts/setup_ralph_queue_github.py` for the schema.

### Prerequisites (GitHub)

- **A GitHub Personal Access Token (PAT).** Classic PAT with `repo` scope,
  or a fine-grained PAT with *Contents: Read and write*, *Administration:
  Read and write* (required for branch-protection PUTs), and — when
  `--org` is used — *Repository creation: Write* at the org level.
- **Environment variables:**
  - `GH_TOKEN` — the PAT value above.
  - `GH_OWNER` — the GitHub user that owns the repo. When `--org NAME` is
    passed, `GH_OWNER` should match (the script POSTs to `/orgs/<org>/repos`
    and reads/writes via `/repos/<owner>/<repo>`).

## Wiring the executor

Once the repo exists, point the executor at it:

```bash
ralph-executor init
# Prompts for ralph_home, queue_repo URL, and queue_branch (default ralph-queue)
```

Or write `~/.ralph/config.toml` directly:

```toml
queue_repo = "https://github.com/<owner>/<repo>"
queue_branch = "ralph-queue"   # optional — this is the default
```

The executor clones `queue_repo` into `<workspace_root>/queue/` (always
checked out on `queue_branch`); every loop iteration pulls before claim
and pushes after each queue mutation.

## Override precedence

The `queue_branch` value resolves in this order (highest precedence first):

1. `--queue-branch NAME` on the executor CLI (per-run override).
2. `RALPH_QUEUE_BRANCH` environment variable (executor only).
3. `queue_branch` in `<repo>/.ralph/config.toml` (executor only — the
   per-project knob lives on the queue branch itself).
4. `queue_branch` in `~/.ralph/config.toml` (operator-level; read by the
   executor AND every operator skill).
5. The hard-coded default `"ralph-queue"`
   (`DEFAULT_QUEUE_BRANCH` in `ralph_executor/config.py`).

Operator skills (`ralph-add`, `ralph-cancel`, `ralph-promote`,
`ralph-triage`, `ralph-status`) only read steps 1, 4, and 5 — they
intentionally bypass `RALPH_QUEUE_BRANCH` and the per-repo TOML so the
operator surface stays on stable rails (skills run outside an executor
context, often from a fresh shell).

Validation (applied by both `load_config` and
`queue_writer.resolve_queue_branch`):

- Empty string → `ConfigError`.
- `HEAD` → `ConfigError` (must be a real branch name, not a symbolic ref).
- Anything starting with `refs/heads/` → `ConfigError` (the prefix is
  appended by the git layer; passing it here doubles up).

## Operating on `main` instead

Pre-split deployments stored queue state on `main`. To preserve that:

```toml
# ~/.ralph/config.toml
queue_repo = "https://github.com/<owner>/<repo>"
queue_branch = "main"
```

Then loosen `main`'s branch protection accordingly (the executor needs to
push there). Rerun `setup_ralph_queue_github.py` with `--no-protection`,
or strip the PR-review requirement via the GitHub UI under
*Settings → Branches → main*.

## Idempotency

Re-running the setup script on a fully-provisioned repo is a no-op:

- Repo creation skips when the repo already exists (GET returns 200).
- README seed skips when `README.md` is present on `main`.
- `.ralph/` skeleton skips per-file when each `.gitkeep` path exists on
  `ralph-queue`.
- `.ralph/config.toml` stub skips when the file exists on `ralph-queue`.
- Branch-protection PUTs are idempotent on GitHub's side.

A concurrent run that creates `ralph-queue` between the GET-ref check and
the POST-ref create is detected (`422 Reference already exists`) and
treated as already-existed so the script still falls through to the
protection PUT.

Use `--dry-run` to preview what would change without mutating anything:

```bash
GH_TOKEN=<token> GH_OWNER=<owner> \
    uv run python -m scripts.setup_ralph_queue_github --repo <repo-name> --dry-run
```

## Verification

After the script returns, confirm the wire-up:

```bash
gh api "repos/$GH_OWNER/<repo>/git/ref/heads/ralph-queue" \
    --jq '{ref: .ref, sha: .object.sha}'
```

Expected: `ref: refs/heads/ralph-queue` and a `sha` matching `main`'s tip
at bootstrap time.

```bash
gh api "repos/$GH_OWNER/<repo>/branches/ralph-queue/protection" \
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

Re-running the helper script should report `branch_existed: true`,
`branch_created: false`, `protection_applied: true`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `GitHub 404 from .../repos/<owner>/<repo>: Not Found` | `--repo` name does not match a repo accessible to `GH_TOKEN`, OR `GH_OWNER` is wrong. | Confirm with `gh repo list <owner>`. |
| `GitHub 401 from ...: Bad credentials` | `GH_TOKEN` is expired or missing the `repo` scope. | Regenerate the PAT with `repo` scope (or, for fine-grained, *Contents: R/W* + *Administration: R/W* on the target repo). |
| `GitHub 403 from .../protection: ...Resource not accessible by integration` | Fine-grained PAT lacks *Administration: Read and write*. | Edit the PAT, grant administration write, re-run. |
| `GitHub 403 from .../protection: ...Upgrade to GitHub Pro or make this repository public to enable this feature.` | Branch protection on a private repo requires GitHub Pro / Team / Enterprise. | Re-run with `--no-protection`, or make the repo public (`gh repo edit <owner>/<repo> --visibility public`). The script now downgrades to a warning and exits 0 automatically. |
| `GitHub 422 from .../git/refs: Reference already exists` after a clean exit | A concurrent setup run created `ralph-queue` between this script's GET-ref and POST-ref. | Re-run; the second run's GET will see the ref and skip creation. |
| Helper script exits with `error: environment variable GH_TOKEN is required` | Shell didn't have `GH_TOKEN` exported. | Export `GH_TOKEN` and `GH_OWNER` in the same shell before running. |
| Executor crashes with `queue_repo not configured` | `~/.ralph/config.toml` missing or lacks `queue_repo`. | Run `ralph-executor init`, or add `queue_repo = "<url>"` to that file by hand. |
| Executor crashes with `queue_branch must be a non-empty branch name` | TOML or env set `queue_branch` to an empty / whitespace string. | Set a valid name or remove the override to fall back to the default. |

## Phase 2 — Azure DevOps (deferred)

Phase 2 is the ADO equivalent of Phase 1 and is built later (likely by
Ralph itself, running against Phase 1's GitHub setup). When Phase 2 is
delivered, the artefacts will be:

- `scripts/ado_client.py` — minimal PAT-authenticated ADO REST 7.1 client.
- `scripts/setup_ralph_queue_ado.py` — creates the queue branch off
  `main` and creates a Require-Reviewer policy scoped to
  `refs/heads/<queue_branch>`.

The Phase 2 prerequisites, helper-script invocation, ADO web UI steps,
and `az` CLI fallback will be written into this same runbook file when
Phase 2 lands. Until then, do NOT bootstrap an ADO repo for Ralph — the
Phase 2 tooling is the only supported path.
