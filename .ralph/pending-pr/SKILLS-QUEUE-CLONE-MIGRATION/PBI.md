---
id: SKILLS-QUEUE-CLONE-MIGRATION
type: feature
status: pending-pr
severity: high
attempts: 0
created_at: 2026-05-28T01:01:00+00:00
updated_at: 2026-05-28T14:54:24+00:00
depends_on: ["EXECUTOR-QUEUE-REPO-SPLIT"]
target_repo: https://github.com/emp3thy/ralph
---

# Skills + docs migrate to the queue-clone model

PBI 2 of 2 in the queue-repo-split architecture change. Migrates all operator-facing skills (`ralph-add`, `ralph-cancel`, `ralph-promote`, `ralph-triage`, `ralph-status`) and `scripts/queue_writer.py` from the branch-checkout model to the queue-clone model introduced in `EXECUTOR-QUEUE-REPO-SPLIT`.

Surfaces the under-marketed `ralph-status` skill in a new README "Working the queue" section.

Adds a pod-deployment ops doc under `docs/superpowers/ops/`.

**Strict depends_on `EXECUTOR-QUEUE-REPO-SPLIT`** — uses `ensure_queue_clone` introduced there. Cannot start until PBI 1 lands in `done/`.

Spec: `docs/superpowers/specs/2026-05-28-queue-repo-split-design.md`
Plan: `docs/superpowers/plans/2026-05-28-skills-queue-clone-migration-plan.md`

See `PLAN.md` for the implementation plan pointer.

## Acceptance criteria

- `scripts/queue_writer.py` exposes `acquire_queue_clone` (thin wrapper around `ensure_queue_clone`). `checkout_queue_branch` deleted entirely (no compat shim).
- `ralph-add` argv shape: `--target-repo` replaces `--repo` + `--branch`. PBI's `target_repo` frontmatter field populated from the new flag.
- `ralph-cancel`, `ralph-promote`, `ralph-triage` argv shape: target/branch arguments removed; only PBI-id + per-skill operation args remain.
- `ralph-status` rewrite (split into 4 subtasks in the plan): delete worktree-per-repo machinery; new `--target-repo` / `--state` / `--json` argv; output groups by `target_repo` field; JSON envelope drops `repos` key.
- Each `SKILL.md` updated to match new argv.
- `README.md` Install section rewrites for the queue-repo model; new "Working the queue" section walks through all five skills with sample output.
- New `docs/superpowers/ops/2026-05-28-pod-deployment.md` runbook for pod-shaped deployments.
- Plan-defined gates pass: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy ralph_executor scripts skills tests`.
- PR opened against `main` from `ralph/SKILLS-QUEUE-CLONE-MIGRATION`.
