# Ralph vs. Anthropic long-running-Claude — pre-design analysis

**Status:** pre-design (exploratory).
**Date:** 2026-05-30.
**Source post:** https://www.anthropic.com/research/long-running-Claude
**Surveyed code:** `ralph_executor/{loop,claude_spawn,safety,sweep}/*.py`, `prompt/PROMPT.md`, `docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md`.

This file captures a comparison between the techniques the Anthropic post sketches for long-running Claude agents and what ralph already does, then lists candidate follow-on designs. It is not itself a design — it is the input a later spec would draw from.

---

## One-line take

The blog gives you one Claude babysitting one task with hand-rolled "really done?" prompts. Ralph gives you a queue-driven multi-PBI loop with three-layer safety, source-of-truth gates, and a structured halt protocol. The blog is the napkin sketch; ralph is the production rig.

---

## Blog summary (so we don't have to re-fetch)

Architecture:

- **`CLAUDE.md`** — primary context anchor. Mutable. The agent updates it as it learns; future sessions reference the evolved understanding rather than a static spec.
- **`CHANGELOG.md`** — persistent lab notes across sessions. Completed work, failed approaches, accuracy benchmarks, known limitations. The post stresses: documenting dead ends matters — *"without them, successive sessions will re-attempt the same dead ends."*
- **Test oracles** — clear success criteria (0.1% accuracy targets) plus reference implementations enable autonomous evaluation.
- **Git as coordination** — commits after meaningful units of work create recoverable history and visible progress.
- **SLURM + tmux** — detached operation with occasional check-ins.

Techniques:

- **Subagent spawning** — bounded subtasks, useful for parallel debugging paths.
- **Ralph loop (the Anthropic version)** — iteratively challenge the agent's "I'm done" claim. Counters "agentic laziness" — the tendency to find excuses to stop before completion.
- **HPC integration** — run inside existing infra (SLURM, tmux) rather than build custom orchestration.

Anti-patterns called out:

- **Agentic laziness** — addressed by the explicit loop.
- **Narrow test coverage** — early testing at single parameter points severely limited bug detection.

Key insight: compression of researcher-months into days changes the opportunity cost: *"every night you don't have agents working for you is potential progress left on the table."*

---

## Concept overlap — where ralph already does it

| Blog concept | Ralph implementation | Note |
|---|---|---|
| Mutable lab notes (`CHANGELOG.md`) | `HISTORY.md` per PBI — iteration log; bug PBIs append `Root cause:` + `Fix:` lines | Scoped tighter than the blog: per-work-item, not per-project. Better fit for queue model. |
| Standing instructions (`CLAUDE.md`, mutable) | `prompt/PROMPT.md` — immutable per iteration, read every time | Inversion. Ralph treats the prompt as a contract, state as files. Cleaner separation. |
| Counter "agentic laziness" | `STUCK.md` protocol with five explicit triggers (repeating, attempts ≥ 3, contradictory evidence, ambiguous PBI, safety-line crossing) | Honest stop is a first-class outcome, not a failure mode. PROMPT.md §"Honesty before progress" carries the norm. |
| Detached operation (tmux + SLURM) | `claude -p` non-interactive + Python executor loop | Same idea, Python-driven. No tmux required. |
| Test oracle | `REPRODUCE.md` for bug PBIs + required-CI gate (`_wait_for_pr_checks`) on `pr_created` classification | A PR does not promote to `pending-pr/` unless CI is green. Strong objective signal. |

---

## Where ralph goes beyond the blog

1. **Cycle detector** (`ralph_executor/safety/cycle_detector.py`) — six rules over an append-only event log:
   - `signature_recurrence` — closed PBI's signature reappears inside 24 h.
   - `whack_a_mole` — high PBI-open rate without matching closes.
   - `same_file_thrashing` — N PRs touching the same file in 24 h.
   - `regression_cascade` — green-then-red signals chained across PBIs.
   - `attempt_divergence` — recent attempts-per-PBI mean diverges from baseline.
   - `blocked_growth` — rate of blocking exceeds historical baseline.
   The blog has no equivalent — laziness is one of many failure modes ralph instruments for.
2. **Source-of-truth discipline** — `_query_open_pr_via_gh` resolves PR state via the `gh` CLI, not by parsing Claude's stdout. PROMPT.md does not mandate a specific marker line, and the pr-skill emits JSON; trusting stdout is unreliable.
3. **Queue topology** — `inbox/current/pending-pr/done/blocked` with explicit movements (`queue/movements.py`). The blog's "one big task" model does not address multi-work-item scheduling.
4. **Per-PBI feature branch** (`ralph/<PBI-id>`) + queue branch separation. Reviewable units, not one monster branch.
5. **Elevated-attention categories** (PROMPT.md §"Elevated-attention categories") — test deletion, scope creep, permission widening, large diff. Encodes "be charitable to reviewers" as bias rules.
6. **Streaming stream-json with live tee** (`_tee_stream`) — operator visibility the blog hand-waves.

---

## Candidate follow-on designs (the "what we could borrow" list)

Each item below is a candidate for its own design spec, not a commitment.

### A. Project-wide dead-ends log

Per-PBI `HISTORY.md` covers in-flight work. Post-merge knowledge that lands in `done/` or `blocked/` is buried — the next PBI can't grep for "we tried X last month and it didn't work because Y." The blog's `CHANGELOG.md` model addresses this directly.

Sketch:

- On `move_to_done` / `move_to_blocked`, run a small extractor over the PBI's `HISTORY.md` for lines tagged `Root cause:` / `Fix:` / explicit dead-end notes.
- Append to a repo-level `docs/RALPH-LESSONS.md` (or sidecar JSONL) keyed by file paths and topic tags.
- Wire into PROMPT.md so iteration N greps it during the read phase for the PBI's declared scope.

Open questions:

- Index by which key — file path, function name, error signature?
- Who curates entries that drift / become obsolete?
- Risk of leaking customer-specific details from PBI bodies into a repo-shared file.

### B. In-iteration subagent fan-out

Each iteration is one Claude session, sequential. `superpowers:dispatching-parallel-agents` exists in `skills/` but is not wired into PROMPT.md. Multi-hypothesis bug PBIs (where `docs/INVESTIGATE.md` lists three plausible causes) are the obvious candidate.

Sketch:

- Opt-in flag on the PBI: `parallel_hypotheses: true`.
- Iteration N spawns one subagent per hypothesis, each working in an isolated git worktree.
- Top-level aggregator picks the winning hypothesis (the one whose patch makes `REPRODUCE.md` go green) and discards the rest.

Open questions:

- How to charge compute fairly — N hypotheses × M iterations is not free.
- What happens when two hypotheses both seem to fix the symptom — pick by minimum diff? By INVESTIGATE.md ranking?
- Blast radius if a hypothesis touches shared state outside the worktree.

### C. "Really done?" verification-before-completion pass

Blog's signature move. Ralph trusts `pr_created` + CI green and does not re-read `PLAN.md` to confirm every checkbox earned its tick.

Sketch:

- Before `move_current_to_pending_pr`, run a verification step that:
  - Re-reads `PLAN.md`, lists every checkbox marked `[x]` this PBI.
  - For each checked box, requires either (a) a referenced commit hash, or (b) a referenced HISTORY.md iteration.
  - Refuses to promote if any checked box has no evidence.
- Returns a synthetic `partial` outcome ("verification failed: step K marked done but no evidence found") that the next iteration must address.

Open questions:

- Does this duplicate the CI gate? CI checks code; this would check process. Probably orthogonal.
- Risk of false negatives (a step that's genuinely done but didn't earn a commit because it was, e.g., a doc tweak).

### D. Pre-iteration goal restatement

Blog's mutable `CLAUDE.md` lets the agent restate the task. Ralph's `PROMPT.md` is immutable and the per-PBI files (`PBI.md`, `PLAN.md`) are author-written. Restatement has nowhere to live.

Sketch:

- PROMPT.md requires iteration N to write a one-line "goal of this iteration" header to `HISTORY.md` *before* doing anything else.
- The header is structured: `Step K: <one-line> — confidence <NN>%`.
- The cycle detector can read these headers later to spot "same goal restated three iterations in a row" (a softer signal than signature-recurrence).

Open questions:

- Adds one Claude turn per iteration. Small cost but real.
- Risk of becoming theatre (Claude writes plausible-sounding goals it does not actually pursue).

---

## Concerns in the current code (independent of the blog comparison)

These came out of reading the code today and are worth their own bugs / cleanup PBIs.

1. **Branch handover by convention** — `claude_spawn.py` known issue (the multi-paragraph docstring on `_run_ralph` is explicit about it). Working tree stays on `ralph-queue` so Claude can read `.ralph/current/<PBI-id>/`, then Claude is *trusted* to `git checkout ralph/<PBI-id>` itself. A forgetful Claude commits code to `ralph-queue` and bypasses the feature-branch contract. Worktree-per-PBI fixes this; Plan 7 acknowledges; it is worth a dedicated PBI rather than indefinite deferral.
2. **No timeout on `spawn_claude_p`.** A wedged Claude blocks the entire loop. Add `proc.wait(timeout=…)` + kill-on-timeout → `error` outcome. There is already a CONFIG-PROMOTE-BASH-TIMEOUT PBI in the queue; that addresses Claude-side bash timeouts, not executor-side child-process timeouts. Different concern; both needed.
3. **`_run_sweep` silently skips when `RALPH_ADO_AUTHOR_EMAIL` unset.** Logs a WARNING every iteration and returns. A production-deployable repo can ship with sweep disabled and never notice. Surface this once at executor start, not every iteration.
4. **Event log opened and closed every iteration.** `_check_cycle_detector` + `_persist_iteration_writes` each call `open_log` + `close`. Cheap now, will not stay cheap as event volume grows. Cache the handle on the loop driver.
5. **`PROMPT.md` is 414 lines.** Read every iteration. The "skim if you have read it before" hint in §1 is a hope Claude cannot act on (no session memory across iterations). Split into a tight core + per-type addenda loaded only when relevant.

---

## Recommendation

Of the four follow-on candidates, **A (project-wide dead-ends log)** and **C ("really done?" verification pass)** are the highest-value lowest-risk picks for a v1.1 spec. B (parallel hypotheses) is a bigger architectural shift with real blast-radius concerns. D (goal restatement) is cheap but its value is more speculative.

Of the five concerns, **concern 1 (branch handover)** is the most load-bearing — the rest are latent. The next ralph hardening PBI should probably be a worktree-per-PBI spec.

---

## Notes / loose ends

- The blog's "every night you don't have agents working for you is potential progress left on the table" framing presumes a single high-value task. Ralph's queue model already amortises that — idle iterations sleep on `time.sleep(cfg.iteration_sleep_seconds)` rather than spin.
- The blog does not address review / merge — its agents work in isolation. Ralph's reviewer-as-colleague protocol (PROMPT.md §"Reviewer feedback") + the sweep's feedback-PBI emission is a real differentiator for human-in-the-loop work.
- This file is not a brainstorming output — there is a rendered visualiser snapshot at `.superpowers/brainstorm/135155-1780130960/content/ralph-vs-anthropic-feedback.html` that mirrors this analysis with a mermaid diagram. The two should stay in sync until a follow-on spec lands.
