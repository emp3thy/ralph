# Fix bootstrap-operator-runbook.md staleness (tech-debt: bootstrap-runbook-stale)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Stop the Stage-B-era runbook misdirecting operators: mark it historical, remove all dead `RALPH_REPO_PATH` references, and fix the `PROMPT.md` naming drift.

**Architecture:** Documentation-only change to `docs/bootstrap-operator-runbook.md`. The doc stays (its §8 names itself the "operate without the tools" fallback reference) but gains a prominent historical banner and loses every reference to deleted configuration.

**Guardrails:** Stage specific paths only [[0547374e]]. Docs/README sweep is the change itself [[98056ebc]]. Cross-cutting test grep: `git grep -l RALPH_REPO_PATH tests/` confirmed empty at plan time — no test references the flag.

**Confidence:** Task 1: 95% — pure markdown edits, all 9 stale lines enumerated below from a fresh grep. Task 2: 95% — verification commands fixed, PR flow proven. Floor 92% met.

---

### Task 1: Banner + scrub dead references

**Files:**
- Modify: `docs/bootstrap-operator-runbook.md`

- [ ] **Step 1: Insert historical banner** directly under the `# Ralph Bootstrap Operator Runbook` H1:

```markdown
> **HISTORICAL REFERENCE (Stage B era).** Stage B is complete: the operator skills this runbook works around have shipped (`ralph-new` — successor to the planned `ralph-add` — plus `ralph-status`, `ralph-doctor`, `ralph-cancel`, `ralph-promote`, `ralph-triage`, `ralph-recover`), queue provisioning is `scripts/setup_ralph_queue_github.py`, and the executor is queue-driven (each PBI's `target_repo` frontmatter names the repo; there is no `RALPH_REPO_PATH`). Use this document only as the "operate without the tools" fallback described in §8. For current architecture see `docs/runbooks/ralph-architecture.md`.
```

- [ ] **Step 2: Fix the 9 stale references** (line numbers from 2026-06-10 grep):
  - Line 68 `export RALPH_REPO_PATH=...`: delete the line and the `# Required for the executor` comment's claim; replace with `# (historical) the executor now reads target_repo from each PBI's frontmatter`
  - Lines 88, 169, 266, 361, 379, 409 `cd $RALPH_REPO_PATH`: replace with `cd <path-to-target-repo-clone>` (placeholder; the env var no longer exists)
  - Line 224 `Claude reads PROMPT.md, ...`: replace `PROMPT.md` with `standing-prompt-<pbi.id>.md`
  - Line 394 preflight grep: remove `|RALPH_REPO_PATH` from the grep pattern

- [ ] **Step 3: Verify zero stale refs remain**

Run: `grep -n "RALPH_REPO_PATH" docs/bootstrap-operator-runbook.md`
Expected: exactly 1 match — the historical banner line (it names the variable in its "no longer exists" clause). Run: `grep -n "reads PROMPT.md" docs/bootstrap-operator-runbook.md` — no matches.

### Task 2: Suite, commit, PR

- [ ] **Step 1: Full suite** — `python -m pytest` green (doc-only change; confirms no doc-asserting test breaks, e.g. prompt-structure tests).

- [ ] **Step 2: Commit and PR**

```bash
git add docs/bootstrap-operator-runbook.md
git commit -m "docs: mark bootstrap runbook historical, scrub dead RALPH_REPO_PATH and PROMPT.md refs"
git push -u origin tech-debt/bootstrap-runbook-stale
gh pr create --title "docs: fix stale bootstrap operator runbook" --body "..."
```

Bot-watch to merge per campaign plan Task 10.
