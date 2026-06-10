# Tech-Debt Removal Campaign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-scan the ralph repo for tech debt, get per-finding user approval, then fix each approved finding behind a reviewed PR — sequentially, on clean main.

**Architecture:** Three phases from the spec (`docs/superpowers/specs/2026-06-10-tech-debt-removal-design.md`): (1) a manual run of the tech-debt-scan pipeline — archive the 2026-06-01 scan, inventory the repo, dispatch 6 read-only LLM scouts in parallel, synthesize a top-5; (2) a hard user-approval gate on `.tech-debt/design.md` status markers; (3) a sequential fix loop — one finding, one plan, one PR, merge before the next starts. Phases 1–2 are orchestration (verification steps instead of tests); phase 3 work is TDD via subagent-driven-development with per-finding plans.

**Tech Stack:** Agent tool (parallel scouts), Python (inventory), git + `gh` CLI, superpowers:writing-plans + superpowers:subagent-driven-development for phase 3.

**Preconditions:** Ralph executor is NOT running against this repo. Working directory `C:\Users\gethi\source\ralph`, branch `main`, clean of unrelated staged changes.

---

## Guardrails (from better-memory, cited by id)

1. **Stage specific paths, never `git add -A`** [[0547374e]] — habit guard even though ralph is idle; every commit step in this plan names its paths.
2. **Bot-watch every PR to merge, no user babysitting** [[29f762c9]] — Task 10 encodes the poll → fix → resolve → squash-merge loop.
3. **Confidence floor is 92%, full task bodies inline** [[9e1897ff]] — every task below carries a post-mitigation rating ≥92%; no "same as Task N" hand-waves.
4. **Mitigations must recompute confidence, not decorate it** [[29446bd6]] — the summary table shows pre-lift → post-lift values with the lift named.
5. **Plan amendments require a confidence-delta audit in the commit diff** [[5d76cff3]] — this v2 commit carries the v1(unrated) → v2 audit in the summary table.
6. **Per-finding plans must enumerate cross-cutting test files** [[94f87716]] — Task 8 requires a `git grep -l` sweep over `tests/` for every symbol/flag a fix deletes (e.g. `test_supervisor_skills_smoke.py` class of misses).
7. **Docs/README sweep on every code PR** [[98056ebc]] — Task 8 sizing rule already mandates it; reviewers check it.
8. **Queue state transitions through the one git-aware helper** [[a5af973e]] — any loop.py/sweep refactor must keep state moves routed through the existing helper, not inline new git calls.
9. **Windows: never `git worktree remove` from a shell that entered the worktree; create feature branches at task start** [[standards/ralph-runtime.md]] — Task 9 creates `tech-debt/<slug>` before any change; SDD subagents told not to cd into worktrees they later remove.
10. **`git mv` does not stage content edits; `python -m` for package-relative imports** [[63c4e75a]] [[8cada12c]] — module-split refactors in phase 3 re-stage moved files after edits and invoke package modules with `-m`.

Considered and dismissed: Playwright/text-match reflections (no UI work), ExecutorConfig call-site sweep (no config field changes planned — re-check per finding at Task 8), tempfile fd-leak (no tempfile code in phases 1–2).

## Assumption surface (3 buckets)

### Real concerns — pick a mitigation each (A/B/C)

**RC1 — Scout evidence may be hallucinated** (wrong file:line, misread code). **RESOLVED: user picked B (2026-06-10)** — Task 5 Step 5 runs a read-only verifier agent over every evidence entry of the chosen top5; failed entries are corrected or the finding swapped for rank #6. Cost ~5–10k tokens.

**RC2 — loop.py split plan refresh may understate drift** (+236 lines since plan written). **RESOLVED: user picked B (2026-06-10)** — threshold rule: if >50% of the 8 tasks are invalidated at re-validation, abandon the refresh and re-run the loop.py split design from scratch via writing-plans. Encoded in Task 8 Step 2.

### Verified safe

- `.tech-debt/` is untracked (`git status` shows `??`) → plain file moves in Task 1 touch no git state.
- Ralph executor idle (user-confirmed) → no co-run staging or branch hazard.
- `Explore` agent type is read-only and available → scouts cannot edit the repo.
- `gh` PR flow works in this repo (PR #70 created, checked, merged through it).
- Prior scan artifacts exist and were read → carry-over comparison in Task 5 has its input.

### Minor / accepted

- Scan cost ~60–80k tokens (+5–10k if RC1-B).
- 6 fixed scout categories, same as the 2026-06-01 scan — keeps results comparable.
- `design.md` format inherited from the prior scan rather than redesigned.

## Confidence summary (v1 unrated → v2 audit; floor 92%)

| Task | Pre-lift | Post-lift | Lift applied |
| --- | --- | --- | --- |
| T1 archive | 98% | 98% | — |
| T2 inventory | 88% | 96% | Script written to file and run by path; kills PowerShell `-c` here-string quoting + argv-length risk [[355faeb8]] |
| T3 scouts | 85% | 93% | Read-only Explore type; ≥2 verified evidence entries required; malformed → 1 retry → drop + `## Scan gaps` note |
| T4 persist | 97% | 97% | — |
| T5 synthesis | 87% | 94% | Prompt persisted for audit; strict JSON schema + 1 retry; RC1-B verifier step baked in (v2→v3: 93→94) |
| T6 render | 96% | 96% | — |
| T7 gate | 97% | 97% | — |
| T8 per-finding plan | 80% | 92% | Unknown-finding risk contained by nested writing-plans gates (per-task 92% floor applies to each per-finding plan) + RC2 threshold rule + cross-cutting-test grep sweep |
| T9 execute | 80% | 92% | SDD dual review per task; suite green at every boundary; behavior-identical constraint; revert-and-replan path |
| T10 PR/merge | 90% | 95% | Bot-watch loop proven on this repo (PR #70); never force-merge; fix-in-branch rule |
| T11 close-out | 96% | 96% | — |

No task below 92% post-lift. T8/T9 are meta-tasks: their residual risk lands in the per-finding plans, each of which gets its own confidence gate at write time.

---

## Phase 1 — Scan

### Task 1: Archive the 2026-06-01 scan

**Confidence: 98%** — plain file moves on an untracked directory.

**Files:**
- Move: `.tech-debt/*.json`, `.tech-debt/*.md`, `.tech-debt/*.txt` → `.tech-debt/archive/2026-06-01/`

`.tech-debt/` is untracked (gitignored by intent) — plain filesystem moves, no git involvement.

- [ ] **Step 1: Create archive dir and move prior artifacts**

```powershell
New-Item -ItemType Directory -Force "C:\Users\gethi\source\ralph\.tech-debt\archive\2026-06-01" | Out-Null
Get-ChildItem "C:\Users\gethi\source\ralph\.tech-debt" -File | Move-Item -Destination "C:\Users\gethi\source\ralph\.tech-debt\archive\2026-06-01"
```

- [ ] **Step 2: Verify**

Run: `Get-ChildItem "C:\Users\gethi\source\ralph\.tech-debt"` and `Get-ChildItem "C:\Users\gethi\source\ralph\.tech-debt\archive\2026-06-01"`
Expected: root contains only `archive\`; archive contains `design.md`, `inventory.json`, `raw-findings.json`, `synthesis-prompt.txt`, `top5.json`, `autobug-pbi-body.md`.

### Task 2: Inventory the repo

**Confidence: 96%** (pre-lift 88%) — lifted by writing the script to a file and running it by path, eliminating PowerShell `-c` here-string quoting and argv-length hazards [[355faeb8]].

**Files:**
- Create: `.tech-debt/inventory_scan.py`
- Create: `.tech-debt/inventory.json`

- [ ] **Step 1: Write `.tech-debt/inventory_scan.py`** (Write tool) with exactly:

```python
import json, os
ROOT = r'C:\Users\gethi\source\ralph'
SKIP_DIRS = {'.git', '.tech-debt', '.superpowers', '__pycache__', 'ralph.egg-info', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'node_modules', '.claude'}
EXT_LANG = {'.py': 'python', '.md': 'markdown', '.sh': 'shell', '.ps1': 'powershell', '.toml': 'toml', '.yml': 'yaml', '.yaml': 'yaml', '.json': 'json'}
files, loc, langs = 0, 0, {}
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for fn in filenames:
        if fn == 'uv.lock':
            continue
        ext = os.path.splitext(fn)[1].lower()
        lang = EXT_LANG.get(ext)
        if lang is None:
            continue
        path = os.path.join(dirpath, fn)
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                n = sum(1 for _ in f)
        except OSError:
            continue
        files += 1
        loc += n
        langs[lang] = langs.get(lang, 0) + n
out = {'scan_date': '2026-06-10', 'root': ROOT, 'total_files': files, 'total_loc': loc, 'languages': sorted(langs, key=langs.get, reverse=True), 'loc_by_language': langs}
with open(os.path.join(ROOT, '.tech-debt', 'inventory.json'), 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=2)
print(json.dumps(out, indent=2))
```

- [ ] **Step 2: Run it**

Run: `python "C:\Users\gethi\source\ralph\.tech-debt\inventory_scan.py"`

- [ ] **Step 3: Verify**

Expected: JSON printed with `total_files` in the hundreds, `total_loc` six figures, `python` and `markdown` dominant. `.tech-debt/inventory.json` exists.

### Task 3: Dispatch 6 scouts in parallel

**Confidence: 93%** (pre-lift 85%) — lifted by read-only Explore agent type, the ≥2-verified-evidence rule, and the malformed-output path (1 retry, then drop + `## Scan gaps` note in Task 6).

No files written in this task — scout outputs are collected in-conversation and persisted in Task 4.

- [ ] **Step 1: Dispatch all 6 Agent calls in ONE message** (read-only `Explore` subagent type, so scouts cannot edit). Each scout gets this prompt with `{CATEGORY}`, `{CATEGORY_FOCUS}` substituted:

```text
You are a read-only tech-debt scout for the repo at C:\Users\gethi\source\ralph.
Category: {CATEGORY}

{CATEGORY_FOCUS}

Scan these directories only: ralph_executor/, scripts/, skills/, tests/, docs/, prompt/, manifests/, samples/.
Ignore: .git, .tech-debt, .superpowers, __pycache__, ralph.egg-info, uv.lock.

Do NOT consult any prior scan results. Find debt fresh from the source.

Return ONLY a JSON array (no prose, no markdown fence) of 3-8 findings:
[{"title": "<≤80 chars>",
  "severity": <1-5, 5 worst>,
  "category": "{CATEGORY}",
  "evidence": [{"file": "<repo-relative path>", "line": <int>, "note": "<what is wrong here>"}],
  "suggested_fix": "<≤500 chars, concrete>"}]
Each finding needs ≥2 evidence entries with real file:line references you verified by reading the file.
Severity guide: 5 = blocks safe change on a hot path; 3 = real cost, contained; 1 = cosmetic.
```

Category focus blocks:

| `{CATEGORY}` | `{CATEGORY_FOCUS}` |
| --- | --- |
| `god-modules` | Find files > ~500 lines or functions > ~60 lines that mix multiple responsibilities (orchestration + I/O + policy). For each, count approximate branch density and nesting depth, and name the distinct responsibilities tangled together. |
| `duplication` | Find logic blocks ≥ ~10 lines repeated (near-)identically in 2+ places: copied helpers, parallel git/subprocess invocation patterns, repeated path/env resolution, duplicated validation. Name every copy site. |
| `dead-code` | Find unreferenced functions/classes/modules, unreachable branches, flags that are never set, config options nothing reads, commented-out blocks, and scripts no longer invoked by anything. Verify non-reference by searching call sites. |
| `test-gaps` | Find complex decision logic (multi-branch pure-ish functions) with no direct unit test — only incidental coverage through integration paths. Check tests/ for what each suspect module actually has. |
| `doc-drift` | Find docs (docs/, README.md, prompt/, skills/*/SKILL.md) referencing deleted env vars, removed flags, renamed files, or flows the code no longer implements. Verify each claim against current source. |
| `half-finished` | Find migrations or refactors left incomplete: compatibility branches for removed modes, TODO/FIXME clusters, modules whose docstring promises more than exists, feature flags defaulted permanently one way with both branches still maintained. |

- [ ] **Step 2: Sanity-check each scout's reply**

Expected per scout: parseable JSON array, 3–8 findings, every finding has the 5 required keys and ≥2 evidence entries. Malformed → re-dispatch that one scout once with the same prompt plus: `Your previous output was not valid JSON. Return ONLY the JSON array.` Still malformed → drop it and record the gap (Task 6 notes it in design.md).

### Task 4: Persist raw findings

**Confidence: 97%** — merge + JSON validation only.

**Files:**
- Create: `.tech-debt/raw-findings.json`

- [ ] **Step 1: Merge all scout arrays into one array and write `.tech-debt/raw-findings.json`** (Write tool, pretty-printed, findings ordered by category then severity desc).

- [ ] **Step 2: Verify**

Run: `python -c "import json; d=json.load(open(r'C:\Users\gethi\source\ralph\.tech-debt\raw-findings.json')); print(len(d), sorted({f['category'] for f in d}))"`
Expected: 18–48 findings, categories ⊆ the 6 dispatched, no exception.

### Task 5: Synthesize top 5

**Confidence: 94%** (pre-lift 87%; v2 93%) — lifted by persisting the prompt for audit, strict JSON schema with 1 retry, and the RC1-B evidence-verification step (Step 5) now baked in. v2→v3 delta: +1 for the verifier closing the hallucinated-evidence gap; verifier output shares the same retry-on-malformed path.

**Files:**
- Create: `.tech-debt/synthesis-prompt.txt`
- Create: `.tech-debt/top5.json`

- [ ] **Step 1: Build the synthesis prompt** and save to `.tech-debt/synthesis-prompt.txt`:

```text
You are synthesising the raw tech-debt findings below into the five most valuable items to fix. Each raw finding came from a read-only scout.

Pick exactly 5 by ranking on impact x tractability: prefer findings that are both damaging if left (impact) and feasible to fix soon (tractability). Merge near-duplicate findings into one. Do not invent findings.

Valid category names: god-modules, duplication, dead-code, test-gaps, doc-drift, half-finished.

Additionally: the previous scan's top 5 (2026-06-01) is appended after the raw findings. For each of your 5 picks, set "carry_over" to the prior slug if it is substantially the same issue, else null.

Return ONLY JSON:
{"top5": [{"slug": "<kebab-case>", "title": "<≤80 chars>", "severity": <1-5>, "category": "<valid category>", "carry_over": "<prior slug or null>", "reasoning": "<why this made the cut: impact + tractability>", "evidence": [<copied from the merged raw findings>], "suggested_fix": "<≤500 chars>"}]}

Raw findings:
<contents of .tech-debt/raw-findings.json>

Previous top 5 (2026-06-01):
<contents of .tech-debt/archive/2026-06-01/top5.json>
```

- [ ] **Step 2: Run synthesis as a single Agent call** with that prompt (general-purpose, no repo access needed). Parse the reply; malformed JSON → one retry with the same correction suffix as Task 3.

- [ ] **Step 3: Write `.tech-debt/top5.json`** with the parsed object.

- [ ] **Step 4: Verify**

Run: `python -c "import json; d=json.load(open(r'C:\Users\gethi\source\ralph\.tech-debt\top5.json')); assert len(d['top5'])==5; print([ (f['slug'], f['severity'], f['carry_over']) for f in d['top5'] ])"`
Expected: exactly 5 entries printed, no exception.

- [ ] **Step 5: Verify evidence (RC1-B).** Dispatch one read-only `Explore` agent:

```text
For each evidence entry below, open the named file at the named line in C:\Users\gethi\source\ralph and judge whether the note accurately describes what is there (allow ±10 lines of drift; report the corrected line if drifted).
Return ONLY JSON: [{"file": "...", "line": <claimed>, "verdict": "confirmed" | "corrected" | "refuted", "corrected_line": <int or null>, "reason": "<one line>"}]
Evidence entries:
<all evidence arrays from .tech-debt/top5.json>
```

Apply results: `corrected` → fix the line number in `top5.json`; `refuted` → if a finding loses enough evidence to fall below 2 entries, swap it for the synthesis run's rank #6 (re-verify that one) and note the swap in design.md. Rewrite `top5.json` with the cleaned data.

### Task 6: Render design.md and report

**Confidence: 96%** — deterministic rendering from `top5.json` in the prior scan's proven format.

**Files:**
- Create: `.tech-debt/design.md`

- [ ] **Step 1: Render `.tech-debt/design.md`** in the same shape as the archived one — frontmatter from `inventory.json`, then per finding:

```markdown
## <title>

```yaml
status: pending
slug: <slug>
severity: <severity>
category: <category>
carry_over: <prior slug or none>
```

### Reasoning

<reasoning>

### Evidence

- `<file>:<line>` - <note>
(one bullet per evidence entry)

### Suggested fix

<suggested_fix>
```

If any scout was dropped in Task 3, add a `## Scan gaps` section at the bottom naming the missing category.

- [ ] **Step 2: Report to user**: the 5 titles + severities + one-line reasoning each, carry-over status vs the 2026-06-01 scan, and the design.md path. **STOP — phase 2 gate.**

## Phase 2 — Approval gate

### Task 7: Per-finding approval

**Confidence: 97%** — user action plus a regex gate check; no `pending` may survive.

**Files:**
- Modify: `.tech-debt/design.md` (status markers only)

- [ ] **Step 1: Collect user verdicts.** User either edits `status:` fields directly or says them in chat (e.g. "approve 1,3,5"); in the latter case flip the markers for them: `pending` → `approved` or `rejected`.

- [ ] **Step 2: Verify gate before proceeding**

Run: `python -c "import re; t=open(r'C:\Users\gethi\source\ralph\.tech-debt\design.md').read(); print(re.findall(r'status: (\w+)', t))"`
Expected: no `pending` remains; ≥1 `approved`. Zero `approved` → campaign ends here, report and stop.

## Phase 3 — Sequential fix loop

Run Tasks 8–10 **once per approved finding**, in this order: half-finished/legacy-removal findings first (they shrink god modules), then god-module splits smallest-first, then everything else; doc-drift findings may slot anywhere. Never start a finding before the previous finding's PR is merged.

### Task 8: Per-finding plan

**Confidence: 92%** (pre-lift 80%) — meta-task over findings unknown until the scan lands. Lifted by: each per-finding plan runs the full writing-plans gate set itself (per-task ratings, 92% floor, guardrail retrieval); RC2 threshold rule for the loop.py refresh; mandatory `git grep -l` cross-cutting-test sweep [[94f87716]].

**Files:**
- Create: `docs/superpowers/specs/2026-06-10-<slug>-design.md` (large refactors only)
- Create: `docs/superpowers/plans/2026-06-10-<slug>.md`

- [ ] **Step 1: Sync main**

```bash
git checkout main && git pull
```

- [ ] **Step 2: Write the per-finding plan** using superpowers:writing-plans, seeded with the finding's evidence + suggested_fix from `.tech-debt/design.md`. Sizing rule:
  - Large refactor (god-module split scale): short design doc + full TDD plan. If the finding `carry_over`s `loop-py-god-module`, refresh `docs/superpowers/plans/2026-06-01-loop-py-split-implementation.md` — re-validate each of its 8 tasks against the current file line-by-line, keep valid tasks, rewrite invalidated ones. **RC2-B threshold rule:** if >4 of the 8 tasks are invalidated, abandon the refresh and re-run the loop.py split design + plan from scratch via writing-plans.
  - Small fix (doc scrub, dead-code delete): plan of 1–3 tasks, no separate design doc.
  - Every plan ends with: full test suite green, docs/README swept for impact (per repo convention), commit.

- [ ] **Step 3: Commit the plan**

```bash
git add docs/superpowers/ && git commit -m "docs(plan): <slug> — tech-debt fix plan"
```

### Task 9: Execute the per-finding plan

**Confidence: 92%** (pre-lift 80%) — refactor risk is per-finding and lands in that finding's own rated plan. Lifted at campaign level by: SDD dual review per task, suite green at every task boundary, behavior-identical constraint, and the revert-and-replan path. Feature branch created at task start per standards.

- [ ] **Step 1: Create the feature branch**

```bash
git checkout -b tech-debt/<slug>
```

- [ ] **Step 2: Execute via superpowers:subagent-driven-development** — full discipline: implementer + spec reviewer + code-quality reviewer per task; task complete only when both reviews pass. Constraints from the spec: behavior-identical extractions, no API changes, tests migrate module-by-module, suite green at every task boundary.

- [ ] **Step 3: Verify before PR**

Run: `python -m pytest` (and `ruff check .` / `mypy` if configured in pyproject.toml)
Expected: all green. Not green → fix before any PR.

- [ ] **Step 4: Record reviewer-flagged non-obvious findings to better-memory** (mandatory CLAUDE.md triggers).

### Task 10: PR, bot-watch, merge

**Confidence: 95%** (pre-lift 90%) — lifted by: bot-watch loop already proven on this repo (PR #70), fix-in-branch rule, never force-merge.

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin tech-debt/<slug>
gh pr create --title "refactor: <finding title>" --body "<summary + link to plan + 'Fixes tech-debt finding <slug>'>"
```

- [ ] **Step 2: Bot-watch loop** (no user babysitting): poll `gh pr view <N> --json statusCheckRollup,mergeStateStatus` and the GraphQL `reviewThreads` query every 60–300s (ScheduleWakeup); fix CI/review findings in the branch; reply via `addPullRequestReviewThreadReply`; resolve via `resolveReviewThread`. Check failures are fixed in-branch — never force-merged. Mid-refactor dead end → revert the branch, replan (back to Task 8 Step 2), report.

- [ ] **Step 3: Merge when green and zero unresolved threads**

```bash
gh pr merge <N> --squash --delete-branch
```

- [ ] **Step 4: Mark the finding done**: in `.tech-debt/design.md` flip its `status: approved` → `status: promoted`. Then loop to Task 8 for the next approved finding, or proceed to Task 11 when none remain.

## Wrap-up

### Task 11: Campaign close-out

**Confidence: 96%** — verification commands against criteria fixed in the spec.

- [ ] **Step 1: Verify success criteria** from the spec: all approved findings `promoted`; `git log main` shows one squash-merge per finding; `python -m pytest` green on fresh main; for god-module findings, line counts hit plan targets (`wc -l` the named files); for doc findings, zero hits for the scrubbed terms (e.g. `grep -r RALPH_REPO_PATH docs/` → empty).

- [ ] **Step 2: better-memory sweep** — record campaign-level lessons (what the scan missed, what reviews caught) per the end-of-phase mandatory trigger.

- [ ] **Step 3: Report** final table to user: finding → PR → merged SHA → measured result.
