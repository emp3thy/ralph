# ralph

Design and (eventually) implementation of an autonomous coding loop ("Ralph") that drains a curated queue of work items (features and bugs) and produces pull requests for each.

This repo is currently **design only**. Implementation will follow a per-subsystem cycle (spec → plan → implement) once the umbrella design and the v1 sub-project spec are approved.

## Current state

- `docs/superpowers/specs/2026-05-23-ralph-umbrella-design.md` — high-level overview of the full system (supervisor, watchers, per-repo executor, memory, skills family). Reference only; not implemented in v1.
- `docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md` — the first sub-project: per-repo executor + Ralph + folder conventions + PR feedback workflow. This is what gets built first.

## What "Ralph" is

A Python supervisor process (the **executor**) inside a project repo. Each iteration:

1. Sweeps open PRs from prior iterations for state changes (merged, closed, new review comments)
2. Picks the highest-priority work item from `.ralph/inbox/`
3. Spawns `claude -p` in a fresh context with the work item's PBI directory
4. Lets Ralph (Claude) edit code, open a PR, and exit
5. Continues to the next iteration

Ralph never waits for CI or human review — PRs land asynchronously. Reviewer comments become new work items in the next sweep.

See the v1 spec for full details.

## Conventions

- Designs are markdown in `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`.
- Implementation plans are in `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`.
- The format follows the superpowers convention used in adjacent projects.
