# Queue-repo split: separate ralph-executor from the work queue

**Status:** Draft (2026-05-28)
**Author:** gethin (brainstormed with Claude)
**Supersedes / extends:** `docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md`, `docs/superpowers/specs/2026-05-27-multi-repo-checkout-design.md`

## Problem

Today, `emp3thy/ralph` is one repository carrying two distinct concerns:

1. **Executor source code** lives on `main`: the `ralph_executor/` Python package, the Dockerfile (ROSA Phase 1), the skills, the tests, the canonical plans and specs.
2. **Work queue state** lives on the `ralph-queue` branch: PBIs under `.ralph/{inbox,current,pending-pr,blocked,archive,done}/`, plus the gitignored local state under `.ralph/state/` (events.db, halt sentinel).

The two branches share history. When the executor runs, it checks out `ralph-queue` in a worktree and mutates `.ralph/`. When developers ship features, they branch off `main` and PR back into it. Feature branches `ralph/<PBI-ID>` are based on `main` and merge to `main`. The `target_repo` PBI field (landed via `RALPH-PBI-TARGET-REPO-FIELD`) and per-target clones (`RALPH-MULTI-REPO-CHECKOUT`) already separate the executor from the *target* repo it works on — but they don't separate the executor from its own queue. The queue's home is still the executor's own repo, on a sibling branch.

This is unsatisfying for several reasons:

- **Pod deployment.** A ralph-executor pod that needs to read the queue today must clone `emp3thy/ralph` and check out `ralph-queue`. It pulls down all the executor source history just to get at `.ralph/`. The conceptual coupling ("the tool ships with its inbox") leaks into operator setup.
- **Queue authoring tools.** `ralph-add`, `ralph-cancel`, `ralph-promote`, `ralph-triage`, and `ralph-status` all assume the queue is a branch on some target repo. Operators reason about which repo they're "in", and which branch, before adding a PBI. The conceptual model is more complex than it needs to be.
- **History pollution.** The `ralph-queue` branch accumulates one `chore(queue): persist iteration writes` commit per Claude iteration on top of the queue-move commits. The executor's own source repo is dragged through that noise even though the data has no relationship to its source.
- **No clean reset.** The queue can only be reset by force-pushing `ralph-queue`. Branch protection and merge-base assumptions on the executor's `main` make this awkward.

## Goal

Separate the work queue from the executor source. Specifically:

- A new repository `emp3thy/ralph-queue` holds the queue state on its `main` branch.
- `emp3thy/ralph` keeps its source code (the executor and its skills, Dockerfile, plans/specs, tests) and stays the place where pull requests land. The `ralph-queue` branch is deleted.
- The executor reads/writes the queue by cloning `emp3thy/ralph-queue` into `$RALPH_WORKSPACE/queue/`, just as it already clones each `target_repo` into `$RALPH_WORKSPACE/clones/<owner>-<name>/`.
- All queue-authoring skills work against the queue clone, not against a branch in a target repo.

The change is a strict cleanup of the architecture's seams, not a feature addition. After the split:

- Operators set one new TOML knob (`queue_repo`) and run a one-shot migration.
- A ralph-executor pod points at the queue URL via config and is otherwise unchanged.
- The executor's iteration loop, cycle detector, sweep, and per-PBI feature-branch worktrees all operate exactly as today — only the queue's *location* has changed.

## Architecture

The repo layout after the split:

```
emp3thy/ralph                          (executor source — unchanged name)
├─ main                                source code, Dockerfile, skills, docs, tests
└─ feature branches ralph/<PBI-ID>     as today; PRs land back on main

emp3thy/ralph-queue                    (NEW repo; queue state only)
└─ main
   └─ .ralph/
      ├─ inbox/  current/  pending-pr/  blocked/  archive/
      ├─ config.toml                   per-queue knobs (sweep cadence, etc.)
      └─ state/                        gitignored — events.db, halted

target repos                           (any GitHub repo a PBI declares)
└─ as today                            cloned by executor into $RALPH_WORKSPACE/clones/<owner>-<name>/
```

The executor's working layout on disk (workstation or pod) is unchanged conceptually but the queue moves into the workspace tree:

```
$RALPH_WORKSPACE/
├─ queue/                              clone of emp3thy/ralph-queue
└─ clones/
   └─ <owner>-<name>/                  one per target_repo seen
      └─ .ralph-work/
         └─ <PBI-ID>/                  per-PBI feature-branch worktree
```

`.ralph-work/queue/` (the worktree-under-executor-checkout that existed today) goes away. Operator setup ends with a `queue_repo` TOML key and a single `git clone` baked into the executor.

## Components and changes

### Executor (PBI 1)

**`ralph_executor/config.py`**

- Add `queue_repo: str` field on `ExecutorConfig`. Required. Sources, in precedence order:
  1. CLI flag `--queue-repo <url>` (one-shot, doesn't persist).
  2. TOML key `queue_repo` in `~/.ralph/config.toml` (the operator's config).
- No environment-variable fallback. Configuration discipline: TOML or CLI only.
- Remove `queue_branch` from `ExecutorConfig` (no longer used).
- Validation: `queue_repo` must parse via the existing `parse_target_repo` (HTTPS, ≥2 path segments). Missing or invalid value at startup is a clear error: `"queue_repo not configured. Add 'queue_repo = \"<url>\"' to ~/.ralph/config.toml or pass --queue-repo."`

**`ralph_executor/queue_clone.py`** (new file, mirrors `target_clone.py`)

- `ensure_queue_clone(workspace_root: Path, queue_repo: str) -> Path`
  - Computes `workspace_root / "queue"`.
  - If the path doesn't exist or isn't a valid git checkout: `git clone <url> <path>`.
  - If it exists: `git fetch origin && git pull --ff-only main`.
  - Returns the path.
  - Network/auth failures raise a clear error that points at `gh auth login`.

**`ralph_executor/loop.py`**

- `_queue_repo_root(cfg)` returns `cfg.workspace_root / "queue"`.
- `_pull_queue(cfg)` calls `ensure_queue_clone(cfg.workspace_root, cfg.queue_repo)` instead of today's `ensure_worktree` + pull. No branch swapping anywhere.
- `_ensure_on_queue_branch(cfg)` is deleted. The queue clone is permanently on `main`; nothing to swap.
- Every site that calls `git_ops.push(queue_repo, cfg.queue_branch)` now passes `"main"`.

**`ralph_executor/cli.py init`**

- Adds a prompt for `queue_repo` after the existing `ralph_home` / `workspace_root` prompts.
- After write, performs a smoke clone (clone, `git rev-parse HEAD`, remove) to confirm credentials. Failure is a warning, not a blocker (the operator may be on a flaky network).

**`ralph_executor/cli.py migrate-queue`** (new subcommand)

- `ralph-executor migrate-queue --source <path> --target <url>`.
- `--source` defaults to `<ralph_home>/.ralph-work/queue` if absent.
- `--target` is the URL of the *empty* new queue repo.
- Behaviour:
  1. Validate source contains `.ralph/inbox/` (sanity).
  2. Validate target repo exists and `main` has zero commits — refuse otherwise.
  3. Stage a temp dir. Copy `.ralph/` tree from source, **excluding**:
     - `.ralph/done/` (entire directory)
     - `.ralph/blocked/META-cycle-*.md` (stale halt artefacts)
     - `.ralph/state/` (gitignored local data; events.db deliberately resets — see "Open decisions")
  4. Ensure skeleton dirs exist: `inbox/`, `current/`, `pending-pr/`, `blocked/`, `archive/`.
  5. Write a `.gitignore` with `.ralph/state/`.
  6. Copy `.ralph/config.toml` if present in source.
  7. `git init`, set remote to target, commit (`chore: bootstrap ralph-queue from existing state`), push to `main`.
  8. Print a summary report (count per state folder) and the exact follow-up actions:
     - The TOML snippet to add to `~/.ralph/config.toml`.
     - The `gh` command to delete the old `ralph-queue` branch.
     - The command to remove the old `.ralph-work/queue/` worktree.

The subcommand does **not** mutate the operator's config file or delete the old branch automatically. The operator runs those steps manually after confirming the new repo looks right.

### Shared helper (PBI 2)

**`scripts/queue_writer.py`**

- Remove `checkout_queue_branch(repo, branch)`.
- Add (re-exported from `ralph_executor.queue_clone` if convenient, or duplicated for skill-isolation) the queue-clone-resolver: skills call it the same way the executor does.

### Skills (PBI 2)

All five skills move from "operate on a target repo's ralph-queue branch" to "operate on the queue clone".

| Skill | Today | After split |
|---|---|---|
| `ralph-add` | `--repo <target>` `--branch <queue>` + workitem args | `--target-repo <url>` + workitem args |
| `ralph-cancel` | `--repo <target>` `--branch <queue>` `--pbi-id` | `--pbi-id` |
| `ralph-promote` | `--repo <target>` `--branch <queue>` `--pbi-id` + state args | `--pbi-id` + state args |
| `ralph-triage` | `--repo <target>` `--branch <queue>` `--pbi-id` + routing | `--pbi-id` + routing |
| `ralph-status` | `--repo <target>` `--branch <queue>` (variadic) | (none — reads the single queue clone) |

Implementation:

- Each `scripts/<skill>.py` drops `repo_path` / `branch` plumbing.
- Each acquires the queue clone via `ensure_queue_clone(workspace_root, queue_repo)` (config-resolved at skill entry).
- `ralph-add`'s `--target-repo` argument writes the PBI's `target_repo` frontmatter field (already required by the schema).
- `ralph-status` is reframed away from the obsolete per-target-queue-branch model. It now reads the single queue clone and groups output by each PBI's `target_repo` field. The legacy `--repo` / `--repos-file` / `--branch` arguments are removed entirely (they were promising a multi-queue model that the data layer never delivered). Old `SKILL.md` lore about "service repos with their own ralph-queue branch" is rewritten.
- Each `SKILL.md` is updated to document the new argument shape.

### Docs (PBI 2)

- **README**: rewrite "Install" and "One-time setup". Remove instructions to clone the queue as a branch; replace with the `queue_repo` TOML key. Cover both workstation install and pulling the ROSA Docker image. Add a new "Working the queue" section showing how to use each operator-facing skill: `ralph-add` to create a PBI, `ralph-status` to see the current board (with sample output), `ralph-cancel` / `ralph-promote` / `ralph-triage` for state operations. `ralph-status` in particular has been under-surfaced; the README is where new operators learn it exists.
- **New ops doc**: `docs/superpowers/ops/2026-05-28-pod-deployment.md`. Pod-shaped runbook: required config, expected layout under `$RALPH_WORKSPACE`, what minimal `~/.ralph/config.toml` looks like, how to seed `queue_repo` into a fresh pod.
- **`prompt/PROMPT.md`**: review for any references to `ralph-queue` as a branch; update if found.
- **Older specs / plans under `docs/superpowers/`**: add a banner pointing to this spec where the older docs describe the single-repo model.

## Testing

### PBI 1

- Unit: `ensure_queue_clone` clones on first call, fetch/pull on second. Use a local bare git repo as the fake remote.
- Unit: `_pull_queue` calls `ensure_queue_clone` exactly once per iteration.
- Unit: `ExecutorConfig` rejects missing `queue_repo` with the expected error message.
- Integration: full `iterate_once` against a fake queue repo containing one inbox PBI. Assert: claim → feature-branch worktree creation → mocked PR creation → state move on the queue clone → push.
- Smoke: `migrate-queue` subcommand against a fixture `.ralph/` tree. Assert: filtered contents land on the target bare repo's `main`; the report mentions the correct per-state counts; old `done/` and `META-cycle-*.md` are absent on the target.

### PBI 2

- Unit per skill: writes/edits land in the queue clone, commit message matches expected, push is invoked. Fake remote = local bare repo. Assert the old `--repo`/`--branch` arguments are no longer accepted (clear deprecation error).
- Integration: `ralph-add` + `ralph-cancel` chained against the same queue clone — confirm queue state is consistent.

Existing `tests/executor/test_*` updated by PBI 1's diff: any reference to `cfg.queue_branch` becomes a queue-clone path.

## Error handling

- `queue_repo` missing from TOML and CLI: clear startup error pointing at the TOML key and the CLI flag.
- Queue clone HTTPS auth failure: clear error pointing at `gh auth login`.
- Migration target not empty: refuse with the existing commit SHA in the error; no force.
- Migration source missing or not a queue tree: refuse with the expected layout printed.
- Cycle detector starts blind on the new queue clone (no events.db). Noted in the migration report so the operator isn't surprised.

## Risks

- **Brief gap between PBI 1 and PBI 2 landing.** Between merging PBI 1 to `main` and merging PBI 2, the executor uses the new queue repo but the skills still expect the old branch model. Mitigation: keep both PBIs in flight in one session; merge PBI 2 immediately after PBI 1. Manual PBI authoring (editing a file in the new queue clone and pushing) is the documented fallback during the window.
- **Operator confusion about where to clone what.** Mitigated by the README rewrite and the pod-deployment ops doc. The new operator command list is shorter, not longer.
- **events.db reset.** Deliberate, accepted: the cycle detector's windows are 4–24h, so behaviour recovers within a day. The migration is a significant architectural change; resetting the rule state is appropriate.

## Out of scope

- Multi-queue support (one operator, one queue). The TOML key is a single value.
- Multi-host support (queue on ADO / GitLab). GitHub only for now — same as today.
- Queue-state versioning / migrations. The `.ralph/` schema is unchanged.
- Replacing the ralph-queue commit history with anything richer (audit log, replay log, etc.). Out of scope.
- Auditing PBIs that might land between brainstorming and implementation. Migration script handles the "current state at run time" — operator-authored PBIs after migration kickoff are operator's responsibility to either re-add or accept on the old branch.

## Open decisions

None remaining at design time. The four sub-decisions surfaced during brainstorming have been resolved:

1. Where the executor source lives: stays in `emp3thy/ralph` (renamed conceptually).
2. How the executor locates the queue: clone-on-startup from `queue_repo` URL.
3. Migration strategy: migrate with pruning (exclude `done/` and META-cycle artefacts).
4. Scope split: PBI 1 (executor + migration), PBI 2 (queue_writer helper + 5 skills + docs).

## Implementation order

1. PBI 1 (`EXECUTOR-QUEUE-REPO-SPLIT`): executor config, `queue_clone.py`, `_pull_queue` rewrite, `migrate-queue` subcommand, init prompt for `queue_repo`. Lands on `emp3thy/ralph` `main`.
2. Operator runs `ralph-executor migrate-queue` after PBI 1 lands. Updates TOML config. Verifies the new queue repo.
3. PBI 2 (`SKILLS-QUEUE-CLONE-MIGRATION`): `queue_writer.py` helper swap, five skill rewrites, doc rewrites. Lands on `emp3thy/ralph` `main`.
4. Operator deletes the old `ralph-queue` branch on `emp3thy/ralph` (`gh api -X DELETE ...`) and removes the stale `.ralph-work/queue/` worktree.

Both PBIs target `emp3thy/ralph` (executor source repo). Their feature branches `ralph/EXECUTOR-QUEUE-REPO-SPLIT` and `ralph/SKILLS-QUEUE-CLONE-MIGRATION` follow the existing convention.

---

## Addendum (2026-05-29): `queue_branch` re-introduced

PBI #48 deleted the `queue_branch` field on `ExecutorConfig` and hardcoded
`"main"` everywhere the queue clone is touched. The follow-up spec
`docs/superpowers/specs/2026-05-28-queue-branch-configurable-design.md`
restores it as a configurable knob, default `"ralph-queue"`.

Why:
- `main` of a queue repo accumulates a persist commit per iteration plus
  every PBI move. With state on `main`, branch-protection rules either
  block the executor or are loosened to the point of being decorative.
- Keeping `main` protected and clean (with PR-required updates) while
  the executor pushes to `ralph-queue` matches the operator-protection
  intent without changing the executor's data flow.

Migration: pre-split deployments running on `main` set
`queue_branch = "main"` in their TOML. Fresh deployments using
`scripts/setup_ralph_queue_github.py` get the new shape end-to-end.
