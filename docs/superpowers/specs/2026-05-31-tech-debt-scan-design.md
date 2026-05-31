# Tech-debt scan — design

- **Status:** Draft — pending user review
- **Date:** 2026-05-31
- **Phase:** Phase 1 (human-in-loop). Phase 2 ("mow the lawn" autonomy) is explicitly out of scope.
- **Distribution target:** New standalone GitHub skills repo (name TBD by user). Not in ralph. Not in superpowers.

## Problem

After a burst of feature work, a repo accumulates tech debt — god modules, duplication, dead code, test gaps, doc drift, half-finished features. Today the only way to surface it is to ask Claude to audit the repo ad-hoc, which produces a one-shot report that disappears at session end. There is no repeatable, ranked, actionable path from "audit this repo" to "ship a fix".

## Goals

A language-independent skill that:

1. Scans any repo (Python, C#, Java, React/TSX, mixed) via LLM scouts.
2. Synthesises a ranked **top 5** debt findings.
3. Emits a single design doc the user can review and approve per-finding.
4. Converts approved findings to ralph-friendly PBI bundles the user can hand to ralph-new (or paste into any tracker).

Phase 1 keeps a human in the loop between scan and PBI emission.

## Non-goals

- Phase 2 ("mow the lawn" autonomous debt cleanup → auto-PBI → ralph builds without review).
- Persistent state across runs (no diff-vs-last-scan in phase 1).
- Severity floor / noise suppression beyond the hard top-5 cut.
- AST-based or per-language static analysis (LLM scouts read source text directly).
- Direct PBI emission into ralph-queue. Skill writes ralph-friendly bundles to a local folder; user feeds them to ralph manually.

## Locked decisions

| Decision | Choice |
| --- | --- |
| Scan engine | LLM-driven scouts (parallel Agent dispatch) |
| Distribution | New standalone GitHub skills repo |
| Scout slicing | Fixed categories, one scout per category |
| Top-5 selection | Synthesis LLM pass after scouts return |
| Approval flow | Two commands: `/tech-debt-scan` then `/tech-debt-promote` |
| PBI fan-out | One design.md → up to 5 PBI bundles (one per approved finding) |
| Promote target | Local folder of ralph-friendly bundles. **Skill does not call ralph-new.** |
| Language scope | Language-independent. Scouts read source text. No language-specific parsers. |

## Architecture

### 1. Repo + skill layout

```
skills-repo/
├── README.md
├── LICENSE
├── .github/workflows/test.yml
├── pyproject.toml                     # pytest, ruff, mypy strict
├── skills/
│   └── tech-debt-scan/
│       ├── SKILL.md                   # workflow for /tech-debt-scan and /tech-debt-promote
│       ├── scripts/
│       │   ├── inventory.py
│       │   ├── categories.py          # data: the 6 scout category prompts
│       │   ├── build_synthesis_prompt.py
│       │   ├── design_writer.py
│       │   ├── design_parser.py
│       │   └── bundle_writer.py
│       └── tests/
│           ├── fixtures/              # python-repo, csharp-repo, react-repo, multi-lang-repo
│           ├── golden/                # canonical design.md + bundle outputs
│           ├── test_inventory.py
│           ├── test_design_parser.py
│           ├── test_design_writer.py
│           ├── test_bundle_writer.py
│           └── test_e2e.py
└── docs/
    └── architecture.md
```

One `SKILL.md` declares both triggers (`/tech-debt-scan` and `/tech-debt-promote`).

**Installation.** Clone the skills repo, then either:
- Symlink: `ln -s <repo>/skills/tech-debt-scan ~/.claude/skills/tech-debt-scan` (Linux/macOS) or PowerShell `New-Item -ItemType SymbolicLink ...` (Windows), **or**
- Plugin install: register the repo as a Claude Code plugin source. Plugin manifest shape is TBD when the repo is scaffolded.

The skill scripts run under the user's existing Python interpreter (3.11+). No package install required beyond stdlib + pyyaml.

`scripts/` are pure Python orchestration + rendering helpers. None of them parse target-repo source — they only do filesystem walks, JSON I/O, and markdown rendering. All language-aware work happens inside the LLM scouts.

The skill repo has zero `ralph_executor` / `ralph-new` imports. It is host-pure.

### 2. /tech-debt-scan flow

Driven by SKILL.md. Claude follows ordered steps.

**Step 1 — Inventory.** Claude runs `python scripts/inventory.py <path>` → writes `.tech-debt/inventory.json`:
- File list: `{path, ext, loc, mtime}` per file
- Per-dir aggregates: file count, total LOC
- Detected language mix (by extension)
- Ignore-list applied: `node_modules/`, `bin/`, `obj/`, `target/`, `.venv/`, `__pycache__/`, `dist/`, `build/`, plus `.tech-debt-ignore` if present

Pure Python, no LLM.

**Step 2 — Dispatch 6 scouts in parallel.** Claude reads category prompts from `scripts/categories.py` and dispatches 6 Agent tool calls in one message. Categories:

1. **god-modules** — files > 400 LOC, top-N functions/classes by size, single-file responsibility violations
2. **duplication** — near-identical helpers across modules, duplicated argparse, duplicated HTTP clients, fixture duplication
3. **dead-code** — unused exports, never-imported functions, files barely referenced, abandoned migration scripts
4. **test-gaps** — production files with no test, skipped tests with no reason, real-subprocess tests masquerading as unit tests
5. **doc-drift** — README / SKILL.md describing features that no longer exist or omitting features that do, stale spec status fields, superseded plans
6. **half-finished** — `NotImplementedError`, pass-only stubs, TODO/FIXME/XXX/HACK, "for now", "temporary", "legacy", "deprecated" markers, commented-out blocks

Each scout receives:
- `inventory.json` contents
- repo root path
- its category prompt
- required output schema: JSON list of findings — `{title (str ≤80 chars), severity (1-5), evidence (list of {file, line, note}), suggested_fix (str ≤500 chars), category (str, must match scout's category)}`

The category list is **fixed**. Adding or removing a category is a repo-level change (edit `scripts/categories.py` + bump skill version). No `.tech-debt.toml` per-repo override in phase 1.

Scouts use `subagent_type: Explore` (read-only). They grep and read but never edit.

**Step 3 — Persist raw findings.** Claude writes raw scout outputs to `.tech-debt/raw-findings.json` for audit. Expected: 20–40 findings total.

**Step 4 — Synthesis.** Claude runs `python scripts/build_synthesis_prompt.py --raw .tech-debt/raw-findings.json` which prints the synthesis prompt. Claude evaluates that prompt inline (single LLM call, not an agent), parses the JSON response into `.tech-debt/top5.json`.

Synthesis instruction: pick top 5 by **impact × tractability**. Impact = blast radius if left (how many future changes touch this code, how many users see it). Tractability = how cleanly a focused PR could fix it. One-sentence reasoning per pick.

**Step 5 — Render design.md.** Claude runs `python scripts/design_writer.py --findings .tech-debt/top5.json --inventory .tech-debt/inventory.json --out docs/superpowers/specs/YYYY-MM-DD-tech-debt-scan.md` (output location configurable via `--out`; defaults to `docs/superpowers/specs/` if it exists, else `./tech-debt-scan-<date>.md`).

**Step 6 — Report.** Claude prints the design.md path, the 5 finding titles + severities, and instructions to edit `status:` markers then run `/tech-debt-promote <path>`.

**Token budget.** Roughly 60–80k output tokens per scan (6 scouts × ~10k each + ~5k synthesis). Documented in SKILL.md.

### 3. design.md format

Single markdown file. Top-level YAML frontmatter holds run metadata. Each finding is an `H2` section with an embedded `yaml` fenced block (the bit the user edits) followed by free-form markdown.

```markdown
---
scan_date: 2026-05-31
scope: /abs/path/to/repo
inventory:
  files: 247
  loc: 42830
  languages: [python, markdown]
skill_version: 0.1.0
raw_findings: .tech-debt/raw-findings.json
---

# Tech-debt scan — 2026-05-31

5 findings, ranked by impact × tractability. Edit `status:` on each finding
to `approved` or `rejected`, then run:

    /tech-debt-promote docs/superpowers/specs/2026-05-31-tech-debt-scan.md

---

## 1. Split god module `ralph_executor/loop.py`

` ` `yaml
status: pending          # edit to approved | rejected
slug: split-loop-py      # used as folder name; safe to rename
severity: 5
category: god-modules
` ` `

### Evidence
- `ralph_executor/loop.py` — 1005 LOC, mixes queue sync + Claude spawn + sweep + cycle detection + persistence
- ...

### Suggested fix
Extract three sub-modules: ...

### Out of scope
Refactor of `claude_spawn.py` itself.

### Why this ranked top-5
Every new feature lands here ...
```

(The triple-backtick blocks above are escaped with spaces for display in this design doc; the real file uses unescaped ``` fences.)

**Parser anchor.** Per-finding YAML block must be the first ```yaml fenced block under its `##` heading. Parser uses that as the anchor. A finding section with no yaml anchor block → exit 2 with line ref.

**Adding / removing findings.** Parser counts `##` sections and matches their slugs against synthesis-time slugs recorded in `.tech-debt/top5.json` (referenced by frontmatter). Mismatches:

- User removes a finding section → parser treats removed slug as `rejected`. No error.
- User adds a new `##` section with a slug not in `top5.json` → exit 2 with line ref. Skill does not support user-authored findings; those go through a fresh scan.

**Editable vs locked.**

- User-editable: `status:`, `slug:` (slug rename allowed if user wants a different PBI folder name).
- Locked: `severity:`, `category:`. Parser ignores edits to these, logs warning.

**Status values.** `pending` (default) | `approved` | `rejected` | `promoted` (set by promote, not by user).

**Severity range.** Integer 1–5. 5 = highest priority. Synthesis pass assigns.

**Audit file.** `.tech-debt/raw-findings.json` lists all ~30 scout findings + synthesis reasoning for which 5 made the cut. Kept for inspection. `.tech-debt/` added to suggested `.gitignore`.

### 4. /tech-debt-promote flow

Invocation: `/tech-debt-promote <path-to-design.md> [--out ./tech-debt-pbis/] [--force]`

Driven by SKILL.md.

**Step 1 — Parse.** Claude runs `python scripts/design_parser.py <path>` → JSON `{findings: [{slug, status, severity, category, title, body_md, line}]}`. Validates:
- Every `status` ∈ {`pending`, `approved`, `rejected`, `promoted`}. Unknown → exit 2 with line ref.
- No duplicate slugs → exit 2.
- Slug pattern `^[a-z0-9][a-z0-9-]{1,63}$` → else exit 2.
- Frontmatter parseable → else exit 2.

**Step 2 — Plan + report.** Print: list of approved findings (slug + title), list of skipped findings with reason (pending / rejected / already promoted), absolute output paths the bundles will land at. No interactive prompt — user already opted in by running the command.

**Step 3 — Emit bundles.** Per approved finding, Claude runs `python scripts/bundle_writer.py --finding <json> --out <out-dir>`. Writes:

```
./tech-debt-pbis/chore-<slug>-<YYYY-MM-DD>/
├── PBI.md
├── PLAN.md           # stub: "TODO: plan via superpowers:writing-plans"
└── HISTORY.md        # empty placeholder for ralph-queue compatibility
```

PBI.md content:

```yaml
---
type: chore
status: pending
severity: <int>
category: <string>
target_repo:                            # blank — user fills if/when feeding to ralph
source: tech-debt-scan
source_design: <relative path to design.md>
slug: <slug>
created: 2026-05-31
---

# <Finding title>

<body_md copied verbatim from the design.md finding section: Evidence,
 Suggested fix, Out of scope, Why this ranked top-5>
```

**Step 4 — Collision policy.** If `chore-<slug>-<date>/` already exists:
- Default: skip with warning `already-promoted` (or `collision` if frontmatter doesn't match).
- `--force`: overwrite (logged loudly).
- Never silently merge.

**Step 5 — Mark promoted.** Claude runs `python scripts/design_writer.py --mark-promoted <slug> ...` which edits design.md in-place: each emitted finding's `status: approved` → `status: promoted`. Idempotent — re-running promote is a no-op for already-promoted findings.

**Step 6 — Report.** Print summary: `N emitted, M already-promoted, K rejected, L pending`. Print absolute paths. Print one-line reminder of how to feed to ralph: `cp -r ./tech-debt-pbis/<bundle> /path/to/ralph-queue/pending/` or `ralph-new --from-dir <bundle>`.

**Exit codes.**

| Code | Meaning |
| --- | --- |
| 0 | At least one bundle emitted, or all approved findings were already-promoted (idempotent success) |
| 2 | Parse error / invalid status / invalid slug |
| 3 | Collision with existing bundle and `--force` not set |
| 4 | Output dir not writable |
| 5 | (scan only) ≥3 of 6 scouts failed |
| 6 | Concurrent promote — lock not acquired within 10s |

### 5. Testing + error handling

**Test layers.**

1. **Unit (deterministic helpers).** Fixture repos under `tests/fixtures/{python-repo, csharp-repo, react-repo, multi-lang-repo}/`. Inventory walker tested against each: asserts language detection, ignore-list behaviour, LOC counts.
2. **Golden-file.** `tests/golden/` holds canonical design.md inputs and expected bundle outputs. Catches format drift. Refresh via `pytest --update-goldens`.
3. **End-to-end smoke (LLM-mocked).** Mocks Agent dispatch + synthesis pass with recorded JSON payloads. Asserts design.md + bundles materialise. Real-LLM smoke gated behind `@pytest.mark.live`, off by default.

**CI.** GitHub Actions, matrix on Python 3.11 + 3.12. No live LLM calls. Lint: ruff + mypy strict.

**Error cases.**

| Case | Behaviour |
| --- | --- |
| Scan path missing | Exit 2, `path not found: <p>` |
| Inventory empty | Exit 0, design.md notes "no files in scope" |
| Scout returns invalid JSON | Print `scout <name> failed`; continue with remaining. If ≥3 of 6 fail → exit 5 |
| Synthesis returns < 5 findings | Use what came back; design.md notes actual count |
| Synthesis returns > 5 | Trim to 5; log truncation |
| Promote: design.md missing | Exit 2 |
| Promote: zero approved findings | Exit 0, message "nothing to promote" |
| Concurrent promote | File-lock on design.md during status mutation; second run waits up to 10s, then exit 6 |
| User edits `severity` or `category` | Parser ignores; warning logged |
| User edits slug to invalid chars | Parser rejects; exit 2 with line ref |
| Out-dir read-only | Exit 4 |

**Cancellation.** SIGINT during scan: write partial `raw-findings.json` (completed scouts only), skip synthesis + design.md, exit 130. No resume in phase 1 — re-run from scratch.

**Logging.** Scan + promote write structured JSON log to `.tech-debt/run-<timestamp>.log`. `--verbose` echoes to stderr.

**No telemetry.** Local files only.

## Open questions

- **Skill repo name.** User to decide (e.g. `gh:emp3thy/skills`, `claude-skills`, project-specific).
- **Severity calibration.** First scan against ralph will calibrate the synthesis prompt. If severity 5 is awarded too liberally, tighten the rubric and re-run.
- **`docs/superpowers/specs/` is ralph-specific.** Default output location assumes superpowers convention; in non-superpowers repos, the design.md falls back to `./tech-debt-scan-<date>.md`. Acceptable for phase 1.

## Phase 2 (out of scope, listed for reference)

- "Mow the lawn" autonomous mode — scan emits design + implementation plan, auto-promotes without user review. Guarded by allow-list (auditor's own files excluded to prevent self-modification feedback loop).
- Diff-vs-last-scan: persistent state under `.tech-debt/history/` so re-runs surface delta rather than re-flagging the same debt every week.
- Per-host promote adapters (ado-promote, github-issues-promote) alongside the ralph-friendly bundle output.
