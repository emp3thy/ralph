# Tech-debt removal campaign — design

- **Status:** Approved in brainstorming 2026-06-10; pending user review of written spec
- **Date:** 2026-06-10
- **Approach:** Sequential, dependency-ordered (option 1 of 3 considered)
- **Executor:** Claude in-session via subagent-driven-development (not ralph PBIs)
- **Precondition:** Ralph executor is not running against this repo

## Problem

The 2026-06-01 tech-debt scan produced a top-5 findings list (`.tech-debt/design.md`), all still `status: pending`. Since then, the autobug feature (PR #70) landed: `loop.py` grew from 1078 to 1314 lines and `claude_spawn.py` from 843 to 917, so the scan and the committed loop.py split plan (`docs/superpowers/plans/2026-06-01-loop-py-split-implementation.md`) are stale. The debt remains unaddressed and is growing.

## Decision summary

| Decision | Choice |
| --- | --- |
| Scope | Fresh full re-scan first, then plan from new findings |
| Fix executor | Claude in this session, subagent-driven-development per finding |
| Concurrency | Sequential — one finding, one PR, merge before next starts |
| Scan pipeline | Manual run of the 2026-05-31 tech-debt-scan design (6 LLM scouts + synthesis) |
| Prior-scan handling | Archive to `.tech-debt/archive/2026-06-01/`; blind re-scan; synthesis marks carry-overs |
| Approval | Per-finding `status:` markers in new `.tech-debt/design.md`; user approves before any fix work |

## Phase 1 — Scan

1. **Archive prior scan.** Move current `.tech-debt/*` files to `.tech-debt/archive/2026-06-01/` so the new scan is comparable against the old one.
2. **Inventory.** Count files, LOC, languages; record in design.md frontmatter.
3. **Dispatch 6 scouts in parallel** (Agent tool, read-only), one per category: god-modules, duplication, dead-code, test-gaps, doc-drift, half-finished. Each returns JSON findings: `{title (≤80 chars), severity (1-5), evidence [{file, line, note}], suggested_fix (≤500 chars), category}`.
4. **Persist raw findings** to `.tech-debt/raw-findings.json` (expect 20–40 findings).
5. **Synthesis pass.** Rank impact × tractability, merge near-duplicates, cut to top 5. Compare against the archived 2026-06-01 top5 and annotate carry-overs vs new findings. Write `.tech-debt/top5.json`.
6. **Render `.tech-debt/design.md`** with `status: pending` per finding.
7. **Report** the 5 titles, severities, and one-line reasoning to the user.

Scan scope: whole repo — `ralph_executor/`, `scripts/`, `skills/`, `tests/`, `docs/`, `prompt/`, `manifests/`, `samples/`. Scouts are blind to prior findings (cleaner signal); comparison happens only in synthesis. Token budget ~60–80k.

## Phase 2 — Approval gate

- User flips `status: pending` → `approved` or `rejected` per finding, either by editing `.tech-debt/design.md` directly or by saying so in chat (Claude flips the markers).
- Only `approved` findings proceed. No fix work starts before approval.

## Phase 3 — Execution

For each approved finding, in dependency order (legacy-removal-type findings before god-module splits; doc fixes slot anywhere):

1. **Spec + plan per finding.** Large refactors (loop.py-split scale) get a design doc plus an implementation plan via the writing-plans skill. Small fixes (doc scrub, dead-code deletion) get a lightweight inline plan. The existing 2026-06-01 loop.py split plan is refreshed against the current file, not rewritten from scratch — each of its 8 tasks re-validated.
2. **subagent-driven-development per plan** — full discipline: implementer, spec reviewer, and code-quality reviewer per task. No skipped reviews. Task complete only when both reviews pass.
3. **One PR per finding.** Feature branch off fresh main. After `gh pr create`: bot-watch loop — poll checks and review threads, fix findings, reply and resolve threads, squash-merge when checks are green and zero threads remain unresolved.
4. **Strictly sequential.** The next finding starts only after the previous PR merges, so every refactor rebases on clean main.
5. **Refactor constraints.** Behavior-identical extractions only; no API changes smuggled in. Tests migrate module-by-module; the suite is green at every task boundary.
6. **Memory.** Reviewer-flagged bugs and non-obvious findings recorded to better-memory per the mandatory triggers in CLAUDE.md.

### Error handling

- PR check fails → fix in the branch; never force-merge.
- Mid-refactor dead end → revert the branch, replan, report to user.
- Scout returns malformed JSON → re-dispatch that scout once; if still malformed, proceed with remaining scouts and note the gap in design.md.

## Success criteria

- All approved findings merged to main, each via a reviewed PR.
- Full test suite green on main after each merge.
- God-module line/branch counts reduced to the targets in each finding's plan.
- Zero references to deleted flags (e.g. `RALPH_REPO_PATH`) in docs, if the doc-drift finding is approved.
- `.tech-debt/design.md` statuses updated to reflect completion; prior scan preserved under `.tech-debt/archive/`.

## Out of scope

- "Mow the lawn" autonomy — phase 2 of the original tech-debt-scan design (auto-scan → auto-PBI → ralph builds without review). Unrelated to this document's Phase 2 approval gate.
- Installing or distributing tech-debt-scan as a standalone skill or repo.
- New features or behavior changes beyond the approved findings.
- Findings outside the approved top 5 — `raw-findings.json` is kept for future rounds but not acted on now.
