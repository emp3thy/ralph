# Configurable queue branch: protect main, work on `ralph-queue`

**Status:** Draft (2026-05-28)
**Author:** gethin (brainstormed with Claude)
**Extends:** `docs/superpowers/specs/2026-05-28-queue-repo-split-design.md`

## Problem

The queue-repo split (PR #48, shipped 2026-05-28) moved queue state into its own GitHub repo (`emp3thy/ralph-queue`) and pinned the executor to that repo's `main` branch. `ExecutorConfig.queue_branch` was deleted; every clone, pull, and push hardcodes `"main"`. Sites: `ralph_executor/queue_clone.py:64,80`, `ralph_executor/loop.py:303`, `ralph_executor/queue/movements.py:145`.

The hardcode forecloses on a layout the operator wants:

- **`main` protected and clean.** Branch-protection rules (require PR, no force-push, no deletion) on `main` are the GitHub-conventional shape. With the queue on `main`, every iteration's persist commit + every PBI move pushes directly to the protected branch — either the rule blocks the executor, or the rule is loosened to the point of being decorative.
- **`ralph-queue` as the working branch.** One persist commit per iteration plus the queue-move commits accumulate noise. Keeping that noise off `main` matches operator intuition from the pre-split model, where `ralph-queue` was the queue branch by convention.

The split made the queue repo independent; this spec restores the branch-naming flexibility the split removed.

## Goal

Re-introduce a configurable `queue_branch` on `ExecutorConfig`, defaulting to `"ralph-queue"` (not `"main"`). The executor reads, pulls, and pushes the configured branch. The queue repo's `main` holds only a README and serves as the GitHub default branch with PR-required protection; `ralph-queue` is the working branch the executor mutates.

Specifically, after this lands:

- `ExecutorConfig` gains `queue_branch: str` with default `"ralph-queue"`. Resolution precedence: CLI flag `--queue-branch`, env `RALPH_QUEUE_BRANCH`, TOML key `queue_branch` in the operator config (`~/.ralph/config.toml`), default.
- `ensure_queue_clone` accepts a `queue_branch` argument, uses it in `git clone -b <branch>` and `git pull --ff-only origin <branch>`.
- Every `push_with_rebase(..., branch="main")` site in the executor now passes `cfg.queue_branch`.
- `scripts/setup_ralph_queue_github.py` provisions a queue repo end-to-end: creates the GitHub repo (if absent), seeds `main` with a README, branches `ralph-queue` off `main`, seeds the `.ralph/` skeleton on `ralph-queue`, and applies branch-protection rules to both branches.
- Operator skills (`ralph-add`, `ralph-cancel`, `ralph-promote`, `ralph-triage`, `ralph-status`) read `queue_branch` from config and thread it through their git operations.

The change is a strict cleanup. Existing deployments running on `main` opt in to the old behaviour with `queue_branch = "main"` in their TOML; the executor's iteration loop, sweep, and per-PBI feature-branch worktrees are unchanged.

## Non-goals

- **Per-environment queue branches** (e.g. `queue_branch_prod`, `queue_branch_staging`). YAGNI; no current deployment needs it.
- **Non-GitHub queue hosts.** `scripts/setup_ralph_queue_github.py` stays GitHub-only.
- **Branch protection on target repos.** Already handled elsewhere.
- **Renaming the GitHub default branch.** `main` stays the default; `ralph-queue` is the working branch but not the default.

## Architecture

Layout of the queue repo after this lands:

```
emp3thy/ralph-queue                    (queue repo created by the split)
├─ main                                README only; GitHub default branch
│                                      protection: require PR (1 approval), no force-push, no deletion
└─ ralph-queue                         working branch (branched off main)
   ├─ .ralph/
   │  ├─ inbox/  current/  pending-pr/  blocked/  archive/  done/
   │  ├─ config.toml                   per-queue knobs
   │  └─ state/                        gitignored — events.db, halted
   └─ protection: no force-push, no deletion (no PR requirement — executor pushes directly)
```

Workspace layout on disk is unchanged:

```
$RALPH_WORKSPACE/
├─ queue/                              clone of emp3thy/ralph-queue, checked out to <queue_branch>
└─ clones/
   └─ <owner>-<name>/                  one per target_repo seen
      └─ .ralph-work/
         └─ <PBI-ID>/                  per-PBI feature-branch worktree
```

The executor never checks out `main` of the queue repo. The clone lives on `<queue_branch>` (default `ralph-queue`) for its entire lifetime.

## Components and changes

### Executor (PBI 1)

**`ralph_executor/config.py`**

- Add `queue_branch: str` to `ExecutorConfig` (alongside `queue_repo`).
- Add `"queue_branch"` to the frozen field tuple (TOML key whitelist).
- Resolve via existing `_resolve_str` helper: env `RALPH_QUEUE_BRANCH`, then operator-config TOML key `queue_branch`, then default `"ralph-queue"`.
- Define module constant `DEFAULT_QUEUE_BRANCH = "ralph-queue"`.
- Validation: strip whitespace; reject empty; reject `"HEAD"`; reject leading `"refs/heads/"`. On failure: `ConfigError("queue_branch must be a non-empty branch name (got <value!r>)")`.

**`ralph_executor/queue_clone.py`**

- Change signature to `ensure_queue_clone(workspace_root: Path, queue_repo: str, queue_branch: str, *, timeout: float = 120.0) -> Path`.
- First-run clone (currently line 64): `git clone -b <queue_branch> <queue_repo> <dest>`.
- Refresh pull (currently line 80): `git pull --ff-only origin <queue_branch>`.
- Update the force-push error message to name the configured branch, not `main`.

**`ralph_executor/loop.py`**

- Line 303 (`_persist_iteration_writes`): `git_ops.push_with_rebase(queue_repo, remote="origin", branch=cfg.queue_branch)`.
- Line 320 (`_pull_queue`): `ensure_queue_clone(cfg.workspace_root, cfg.queue_repo, cfg.queue_branch)`.

**`ralph_executor/queue/movements.py`**

- Line 145: `git_ops.push_with_rebase(queue_repo, remote="origin", branch=cfg.queue_branch)`.

**`ralph_executor/cli.py`**

- New argparse flag: `--queue-branch <name>` (optional; one-shot override).
- Wire through `_apply_overrides` so it lands on `cfg.queue_branch`.
- Startup log line (currently line 602): `"ralph-executor starting (repo=%s queue_repo=%s queue_branch=%s main=%s)"`.

**`ralph_executor/user_config.py`**

- Init prompt: after `queue_repo`, ask `"Queue branch [ralph-queue]: "`. Blank → default. Write to operator TOML.

**`ralph_executor/git_ops.py`**

- No change. `push_with_rebase(branch=...)` already accepts arbitrary branch names. Spot-verify during implementation (open question, line ~193-260).

### Setup script (PBI 2)

**`scripts/setup_ralph_queue_github.py`**

Repurpose for end-to-end queue-repo provisioning. New shape:

1. **Repo existence**: `GET /repos/{owner}/{repo}`. If 404, `POST /user/repos` (or `/orgs/{org}/repos` if `--org`) with `private=true`, `auto_init=false`. If exists, confirm token can read it.
2. **Seed `main`**: if HEAD of `main` is absent or repo was just created, create an initial commit with a README via `PUT /repos/{owner}/{repo}/contents/README.md`. README content: `# <repo>\n\nQueue repo for ralph-executor. Queue state lives on the \`ralph-queue\` branch.`.
3. **Create `<queue_branch>`** (default `"ralph-queue"`): existing logic (`GET refs/heads/<queue_branch>`, then `POST refs` off `main`'s tip). Skip if exists.
4. **Seed `.ralph/` skeleton** on `<queue_branch>`: create `inbox/.gitkeep`, `current/.gitkeep`, `pending-pr/.gitkeep`, `blocked/.gitkeep`, `archive/.gitkeep`, `done/.gitkeep`, and `.ralph/config.toml` stub. One commit, pushed to `<queue_branch>`. Skip if `.ralph/` already exists.
5. **Branch protection**:
   - **`main`**: `PUT branches/main/protection` with `required_pull_request_reviews.required_approving_review_count = 1`, `enforce_admins = true`, `allow_force_pushes = false`, `allow_deletions = false`.
   - **`<queue_branch>`**: `PUT branches/<queue_branch>/protection` with `allow_force_pushes = false`, `allow_deletions = false`. No PR requirement (executor pushes directly).
6. **JSON summary** to stdout: repo URL, branches created, protection applied, dry-run flag.

CLI flags: `--owner <name>`, `--repo <name>`, `--queue-branch <name>` (default `ralph-queue`), `--org <name>` (optional), `--dry-run`, `--no-protection` (escape hatch).

Idempotent: every step checks state first. Re-run on a fully-provisioned repo is a no-op.

### Operator skills (PBI 3)

`skills/ralph-add`, `skills/ralph-cancel`, `skills/ralph-promote`, `skills/ralph-triage`, `skills/ralph-status` all read the queue clone post-split (PR #50). They need to:

- Read `queue_branch` from the operator config (same resolution as the executor).
- Pass it to any `git pull` / `git push` operations they perform on the queue clone.

Audit each skill in the implementation plan. Expect ~1-3 line changes per skill.

### Docs (PBI 4)

- **Rewrite** `docs/runbooks/ralph-queue-setup.md`: new repo creation flow, branch layout, protection rules, `queue_branch` knob and override precedence.
- **Append addendum** to `docs/superpowers/specs/2026-05-28-queue-repo-split-design.md`: `queue_branch` re-introduced; default flipped from `"main"` to `"ralph-queue"`; reason (protect `main`, work on `ralph-queue`).
- **`README.md`**: one-line entry in the executor config table for `queue_branch`.

## Data flow

Unchanged from the split spec. The only delta is that every `git` call against the queue clone now targets `<queue_branch>` instead of `"main"`:

1. Executor startup → `load_config()` resolves `queue_branch` → `_pull_queue(cfg)` → `ensure_queue_clone(workspace_root, queue_repo, queue_branch)` clones (`-b <branch>`) on first run, pulls (`origin <branch>`) thereafter.
2. PBI movement → `movements._move(...)` → `push_with_rebase(..., branch=cfg.queue_branch)`.
3. Iteration persist → `_persist_iteration_writes(...)` → `push_with_rebase(..., branch=cfg.queue_branch)`.

## Error handling

- **Config**: missing `queue_branch` is impossible (default applies). Invalid `queue_branch` raises `ConfigError` at startup with the message above.
- **Clone with wrong branch**: `git clone -b <nonexistent>` fails with `fatal: Remote branch <name> not found`. `queue_clone._run_git` already wraps subprocess errors into `QueueCloneError`; the error message includes the configured branch and points at `setup_ralph_queue_github.py` to provision it.
- **Pull when branch was force-pushed**: existing behaviour. `git pull --ff-only` fails; `QueueCloneError` raised; loop logs and bails the iteration. Operator resolves manually.
- **Setup-script protection PUT**: already wrapped in `GhError` handling; no change.

## Testing

- **`tests/executor/test_config.py`**: TOML override, env override, CLI flag override, default applies, validation rejects (empty, whitespace, `"HEAD"`, `"refs/heads/foo"`).
- **`tests/executor/test_queue_clone.py`**: clone with `-b <branch>` (verify the `git clone` command line includes the flag); pull against a non-`main` branch.
- **`tests/executor/test_loop_integration.py`**: `_pull_queue` and `_persist_iteration_writes` push to the configured branch (use a temp queue repo with `ralph-queue` as the working branch).
- **`tests/executor/queue/test_movements.py`**: `_move` push targets `cfg.queue_branch`.
- **`tests/executor/test_cli.py`**: `--queue-branch` flag override resolves into `cfg.queue_branch`.
- **`tests/test_setup_ralph_queue_github.py`**: extend to cover repo creation (404 → POST), README seeding on `main`, `.ralph/` skeleton on `ralph-queue`, dual-branch protection PUTs, idempotency on re-run.

Existing executor tests that hardcode `"main"` as the queue branch get updated to use the default `"ralph-queue"` (PBI 1's diff).

## Edge cases

- **`queue_branch = "main"` (opting back into the shipped behaviour).** Fully supported. Operator with an existing queue on `main` sets the TOML key; the executor reads from and pushes to `main` exactly as today.
- **Setup script re-run on a fully-provisioned repo.** Each step short-circuits; exit 0, JSON summary reports all steps as `skipped: true`.
- **Setup script run with `--no-protection`.** Steps 1-4 run; step 5 skipped. Useful for sandbox/test repos where protection rules block test cleanup.
- **`queue_branch` containing a `/`** (e.g. `team/queue`). Allowed — git branch names permit slashes. No special handling beyond the existing rejection of `refs/heads/` prefix.
- **TOML key drift.** If an old config still has `queue_branch = "ralph-queue"` from the pre-split era, post-split it was ignored; post-this-PBI it becomes load-bearing again. No breakage; the value matches the new default. Document the re-activation in the changelog.

## Rollout

1. PBI 1 (`EXECUTOR-QUEUE-BRANCH-CONFIGURABLE`): executor config, `queue_clone.py`, `loop.py`, `movements.py`, `cli.py`, `user_config.py` init prompt. Lands on `emp3thy/ralph` `main`.
2. PBI 2 (`SETUP-QUEUE-REPO-FULL-PROVISION`): rewrite `scripts/setup_ralph_queue_github.py` to do end-to-end provisioning + dual-branch protection.
3. PBI 3 (`SKILLS-QUEUE-BRANCH-THREADING`): audit and update the five operator skills to read `queue_branch` and pass it through git operations.
4. PBI 4 (`DOCS-QUEUE-BRANCH-CONFIGURABLE`): runbook rewrite, split-spec addendum, README entry.

PBI 1 and PBI 2 are independent and can ship in parallel; PBI 3 depends on PBI 1; PBI 4 depends on all three.

Operator migration (one-time, per deployment):
- Existing deployment on `main`: add `queue_branch = "main"` to `~/.ralph/config.toml` before installing the new executor build. Or, migrate: create `ralph-queue` off `main` in the queue repo, push, then let the default take over.
- New deployment: run the rewritten setup script. No further action.

## Open questions

- **`git_ops.push_with_rebase(branch=...)` arbitrary-branch correctness.** The function takes `branch` as a parameter, but the rebase-on-conflict path may assume `origin/<branch>` resolves to the same name. Spot-verify by reading `ralph_executor/git_ops.py:193-260` during PBI 1. Risk: low — if assumption holds, no change; if it doesn't, fix is local to that function.
- **`gh repo create` vs REST `POST /user/repos`.** The setup script today uses `requests` against the REST API directly. New repo creation should follow the same pattern (no shelling out to `gh`). Confirm REST endpoint accepts `private=true` + `auto_init=false` (it does, per GitHub docs).
