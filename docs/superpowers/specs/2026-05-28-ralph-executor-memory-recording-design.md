# Ralph executor memory recording — design

## Problem

Ralph has produced ~48 merged PRs over the past month, but better-memory's `ralph` project holds only 15 episodes, all empty (zero observations). The autonomous executor subagent — spawned by `ralph_executor.claude_spawn` once per iteration — never records what it learned.

Two compounding causes:

1. **No recording instruction.** The subagent reads `prompt/PROMPT.md` on every iteration. PROMPT.md never mentions better-memory or `memory_observe`. The global `~/.claude/CLAUDE.md` does carry mandatory recording triggers, but they are phrased for interactive multi-task sessions ("after every code-review fix commit", "before marking a subagent task complete") — they read as not-applicable to a one-shot iteration.
2. **Wrong project scope even if it did record.** better-memory infers project from cwd. The subagent runs inside a per-PBI worktree (`.ralph-work/ralph-<PBI-id>/...`). Any observation would land in a `ralph-<PBI-id>` (or worktree-derived) project, not in the PBI's actual target repo.

## Goals

- Executor subagent observations land in the **target repo's** project scope (PBI's `target_repo`).
- Subagent actually records non-obvious facts as the iteration progresses, not at exit only (iterations time out mid-work; CLAUDE.md style inline triggers protect against that).
- Subagent retrieves both the target-repo's memory AND any cross-project workflow rules curated as general-scope knowledge.

## Design

### 1. Scope signal: `BETTER_MEMORY_PROJECT` env var (NEW in better-memory)

Add an environment-variable override to better-memory's project resolver. Resolution priority becomes:

1. `BETTER_MEMORY_PROJECT` env var (if set and non-empty)
2. `.better-memory` file in cwd (existing)
3. cwd-derived fallback (existing)

Ralph's `ralph_executor.claude_spawn` sets `BETTER_MEMORY_PROJECT=<repo-name>` in the subprocess env before exec'ing claude, where `<repo-name>` is the bare repository name parsed from `pbi.target_repo` (e.g. `https://github.com/emp3thy/growatt-monitor` → `growatt-monitor`).

This is the cleanest signal: no filesystem pollution, no `.gitignore` coordination across N target repos, signal scoped exactly to the subprocess that needs it.

**Better-memory side change (separate repo).** File a GitHub issue on the better-memory repo for the env-var resolver branch. Small change: one read in the project resolver, one paragraph in `website/configuration.md`.

### 2. Recording instruction: PROMPT.md addition

Add a "Recording learnings (better-memory)" section to `prompt/PROMPT.md`. Inline triggers tuned for the ralph one-shot iteration lifecycle, citing CLAUDE.md mandatory triggers verbatim where they apply:

- **After REPRODUCE.md confirms failure signature.** Observe the reproduction signal if non-obvious (e.g. specific input, environment variable, race condition).
- **After root cause identified in HISTORY.md.** Observe the root cause and the diagnostic path that surfaced it.
- **After fix commit lands.** Observe the fix if it represents a non-obvious gotcha, dependency, or convention.
- **After BugBot / reviewer-fix commit.** (CLAUDE.md mandatory trigger — cited.) Observe the gap the fix closed; the fix's existence proves it was non-obvious.
- **Before the iteration exits.** Sweep the iteration's commits and HISTORY.md for anything not yet recorded — backstop for missed inline triggers.

Each trigger should call `mcp__better-memory__memory_observe` directly. The env var (component 1) guarantees the observation lands in the right project.

### 3. Retrieval: target-repo memory + general-scope knowledge (C1)

At iteration start, the subagent runs two retrievals:

- `mcp__better-memory__memory_retrieve` — scoped to the target repo via the env var. Returns recent project-specific observations and reflections.
- `mcp__better-memory__knowledge_search` with `scope=general` — returns curated cross-project workflow rules.

The PROMPT.md addition explicitly directs both calls at iteration start.

### 4. Curation pass: ralph-workflow reflections → general-scope knowledge (one-shot)

Audit existing reflections in the `ralph` project. Classify each as:

- **ralph as project under development** — about the ralph codebase itself (e.g. "executor: feature-branch checkout of PBI HISTORY.md from ralph-queue"). Stays in `ralph` project scope.
- **ralph as runtime workflow rule** — generic discipline that applies whenever Claude operates inside a ralph iteration (e.g. "Windows: don't `git worktree remove` from a shell whose CWD is or was inside the worktree", "create the feature branch at task start"). Promote to curated markdown under better-memory `knowledge-base/standards/` with `scope=general`. Drop the reflection from the project once promoted.

Promotion produces hand-curated, durable rules — higher quality than raw reflection signal mixed across scopes.

## Components affected

| Component | Change |
|-----------|--------|
| better-memory project resolver | Add `BETTER_MEMORY_PROJECT` env-var branch (separate repo / issue / PR) |
| better-memory `website/configuration.md` | Document the new override |
| `ralph_executor/claude_spawn.py` `_build_argv` callers | Set `BETTER_MEMORY_PROJECT=<repo-name>` in subprocess env |
| `prompt/PROMPT.md` | New "Recording learnings (better-memory)" section with inline triggers + iteration-start retrieval calls |
| better-memory `knowledge-base/standards/` | New markdown entries for ralph-runtime workflow rules (curation pass) |
| better-memory `ralph` project reflections | Drop the reflections promoted into general-scope knowledge |

## Risks

- **Iteration timeouts skip the exit-sweep trigger.** Mitigated: inline triggers fire as facts are learned. Exit sweep is a backstop, not the primary path.

## Out of scope

- Multi-project `memory_retrieve` (C2 alternative). Not needed under the curated-knowledge model.
- Cross-iteration memory persistence beyond what better-memory already provides.
- Changes to executor sweep / triage / promote subsystems.

## Deliverables

1. **better-memory GitHub issue + PR.** Add `BETTER_MEMORY_PROJECT` env-var override to the project resolver. Document in `website/configuration.md`.
2. **ralph PR.** Wire `BETTER_MEMORY_PROJECT` in `claude_spawn` subprocess env. Add "Recording learnings" section to `prompt/PROMPT.md` (inline triggers + iteration-start retrieval).
3. **Curation pass.** Audit existing `ralph` reflections, promote ralph-runtime workflow rules into general-scope knowledge entries, retire the promoted reflections.
