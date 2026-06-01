# Ralph architecture

Ralph is a per-repo autonomous coding loop. The executor reads a queue
of Product Backlog Items (PBIs), spawns Claude inside a target-repo
worktree to work each PBI, pushes a feature branch, opens a PR, and
records progress back into the queue.

After the queue-repo split (PR #52, May 2026), the runtime has three
distinct pieces.

## The three pieces

```
+------------------------------+   +------------------------------+
|  emp3thy/ralph               |   |  emp3thy/ralph-queue         |
|  (executor source repo)      |   |  (queue state repo)          |
|                              |   |                              |
|  - ralph_executor/  (Python) |   |  branch: ralph-queue         |
|  - skills/ralph-*/  (skills) |   |  .ralph/                     |
|  - scripts/setup_*.py        |   |    inbox/   current/         |
|  - Dockerfile                |   |    pending-pr/  blocked/     |
|                              |   |    archive/    done/         |
+------------------------------+   +------------------------------+
              |                                  ^
              |  builds                          |  clone + push
              v                                  |
+----------------------------------------------------+
|  ~/ralph-workspaces/<env>/   (operator workspace)  |
|                                                    |
|    queue/                  <- clone of ralph-queue |
|       .ralph/...           on the ralph-queue br.  |
|                                                    |
|    clones/<owner>-<name>/  <- one per target_repo  |
|       .ralph-work/<PBI>/   <- per-PBI worktree     |
+----------------------------------------------------+
```

| Repo / path | Role | Contents |
|---|---|---|
| `emp3thy/ralph` | Executor source. Built into the `ralph-executor` Python package and the container image. | `ralph_executor/` (loop, config, sweep, host_select), `skills/` (operator skills + staged `pr-<host>`), `scripts/setup_ralph_queue_github.py`, `Dockerfile`, `pyproject.toml`. |
| `emp3thy/ralph-queue` | Queue state. Holds the PBI lifecycle on the `ralph-queue` branch. `main` is protected and used only for the README. | `.ralph/inbox/`, `.ralph/current/`, `.ralph/pending-pr/`, `.ralph/blocked/`, `.ralph/archive/`, `.ralph/done/`, plus an optional `.ralph/config.toml` for per-queue knobs. |
| `~/ralph-workspaces/<env>/` | Per-machine workspace. Created by the executor at runtime; never checked into git. | `queue-<instance_id>/` — clone of `ralph-queue` (always on `cfg.queue_branch`; one directory per instance under multi-ralph — see `ralph-setup.md` §12). `clones/<owner>-<name>/` — one clone per `target_repo` ralph has seen; each holds per-PBI worktrees under `.ralph-work/<PBI-id>/`. |

## What runs where

| Component | Where it runs | What it touches |
|---|---|---|
| `ralph-executor` (loop) | Workstation, container, or pod. Single process per workspace, enforced by an OS-level lockfile at `<workspace>/queue-<instance_id>/.ralph.lock`. | Reads/writes `<workspace>/queue-<instance_id>/.ralph/`. Clones each PBI's `target_repo` into `<workspace>/clones/...`. Spawns `claude -p` inside a per-PBI worktree. Pushes back to the queue repo and opens PRs on each target repo. |
| Operator skills (`ralph-new`, `ralph-cancel`, `ralph-promote`, `ralph-triage`, `ralph-status`, `ralph-recover`) | Operator's shell, any time. | Direct read/write on `<workspace>/queue-<instance_id>/.ralph/`. **Never** touch any target repo. Commit + push to the queue repo (except `ralph-status`, which is read-only). `ralph-cancel` and `ralph-promote` refuse to mutate PBIs whose `CLAIM.json` names a different instance — see `ralph-setup.md` §12. |
| `scripts/setup_ralph_queue_github.py` | Operator's shell, once per queue. | One-shot provisioning. Creates the GitHub repo (if absent), seeds `.ralph/` on `ralph-queue`, applies branch protection on `main` and `ralph-queue`. See `docs/runbooks/ralph-queue-setup.md`. |

## Data flow per iteration

1. Executor calls `ensure_queue_clone` — clones `cfg.queue_repo` to
   `<workspace_root>/queue-<instance_id>/` if absent (one-shot
   renames a legacy `queue/` directory into the namespaced path),
   then `git fetch` + `git reset` to `origin/<cfg.queue_branch>`.
   Acquires the workspace lockfile at
   `<workspace>/queue-<instance_id>/.ralph.lock`.
2. Inspects `<workspace>/queue-<instance_id>/.ralph/inbox/` for the
   next PBI; reads its `target_repo` frontmatter field.
3. Moves the PBI directory from `inbox/` to `current/`, writes
   `current/<id>/CLAIM.json` (instance_id, hostname, claimed_at), and
   commits the rename + the new CLAIM.json + the status-frontmatter
   flip in one commit pinned to `chore(queue): claim <id> for
   <instance_id>`; pushes to `origin/<queue_branch>`.
4. Clones `target_repo` to `<workspace>/clones/<owner>-<name>/` if
   absent (one clone per target, reused across PBIs).
5. Creates a per-PBI worktree at
   `<clones>/<owner>-<name>/.ralph-work/<PBI-id>/` on branch
   `ralph/<PBI-id>` (worktree mode is mandatory — Stage A is gone).
6. Spawns `claude -p` inside the worktree. Claude implements the PBI,
   pushes the feature branch, opens a PR on the target repo via the
   staged `pr/` skill, and writes a `PR-LINK.md` into the PBI directory.
7. Executor moves the PBI directory from `current/` to `pending-pr/`
   and pushes the queue update.
8. The sweep step polls open PRs each iteration. On clean merge the PBI
   is moved to `done/`; on stale or persistent failure it goes to
   `blocked/`; if auto-merge is enabled (`auto_merge_clean_prs = true`)
   GitHub-clean PRs are merged in-band.
9. On `max_attempts` failed iterations the PBI moves to `blocked/`. On
   cancellation (CANCEL sentinel in `current/<id>/`) it moves to
   `archive/`.

## Branch model on the queue repo

| Branch | Protection | Who pushes |
|---|---|---|
| `main` | Required PR with 1 approving review; no force-push; no deletion; `enforce_admins=true`. | Humans only (typically just the README). |
| `ralph-queue` (default) | No force-push; no deletion. No PR requirement. | The executor (direct push every iteration) and operator skills. |

The exact GitHub API payloads and PAT scopes are documented in
[`docs/runbooks/ralph-queue-setup.md`](ralph-queue-setup.md).

## Why a separate queue repo

- **Source-history pollution.** Each iteration's queue mutation is a
  separate commit. Keeping those on the executor source repo would
  swamp `git log` with bot churn.
- **Clean reset.** Dropping a stale queue is `git push --delete` on the
  queue repo's `ralph-queue` branch (or deleting the repo entirely) —
  no impact on executor source history.
- **Pod deployment.** A `ralph-executor` container ships with the
  executor baked into the image. At pod start it only needs to clone
  the queue repo, not the executor source. The two artefacts version
  independently.
- **Stable operator surface.** `ralph-new`, `ralph-cancel`, etc. talk
  to the queue clone regardless of which executor build is running, so
  operator workflows do not break when the executor is rebuilt.

## Where to go next

- [`docs/runbooks/ralph-setup.md`](ralph-setup.md) — install, init,
  full config reference, full CLI reference.
- [`docs/runbooks/ralph-queue-setup.md`](ralph-queue-setup.md) —
  GitHub queue-repo provisioning, override precedence, branch
  protection details.
- `docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md`
  — the v1 design doc.
- `docs/superpowers/specs/2026-05-28-queue-repo-split-design.md` —
  the queue-repo split design.
- `docs/superpowers/specs/2026-05-29-ralph-report-design.md` — the
  HTML dashboard design (planned).
