# Fix SKILL.md queue documentation drift (tech-debt: skill-md-queue-docs-drift)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Every operator-skill SKILL.md documents the real queue contract: clone at `<workspace_root>/queue-<instance_id>/`, branch `cfg.queue_branch` (default `ralph-queue`), JSON examples showing namespaced paths, and the `--queue-branch` / `--instance-id` flags documented where the scripts support them.

**Architecture:** Doc-only editing pass over `skills/ralph-status|cancel|promote|triage|recover|report/SKILL.md` plus `skills/ralph-new/SKILL.md` (gains the `--instance-id` flag row added in the legacy-queue-clone-path fix, commit 2e776e1). No code changes. One task for the edits, one for gates + PR.

**Evidence map (verified by RC1-B 2026-06-10 evening; line numbers pre-edit):**
- ralph-status/SKILL.md:13 (`queue/` path), :25 (`Always on main`), :50 (flag table missing `--queue-branch` though status.py:75 supports it), :3 (YAML description path)
- ralph-cancel/SKILL.md:33 (path), :52 (JSON example `/queue`)
- ralph-promote/SKILL.md:39 (path), :11 (`on main`), :61 (JSON example)
- ralph-triage/SKILL.md:50 (path), :11 (`on main`), :74 (JSON example)
- ralph-recover/SKILL.md:47 (path), :81 (JSON example)
- ralph-report/SKILL.md:3 (YAML description path), :64 (data-sources prose)
- ralph-new/SKILL.md: `--instance-id` flag undocumented (added to new.py in 2e776e1; check whether other new flags rows exist to mirror)

**Canonical wording (use consistently):** path → `` `<workspace_root>/queue-<instance_id>/` ``; branch → `` `cfg.queue_branch` (default: `ralph-queue`) ``; JSON examples → `/home/dev/ralph-workspaces/queue-<instance-id>` style placeholder or the concrete `queue-ralph-a` if the example uses concrete values elsewhere — match each file's example style. `--queue-branch` row help: "Override queue_branch from `~/.ralph/config.toml` (default: `ralph-queue`)". `--instance-id` row help: mirror the argparse help text in the script.

**Guardrails:** Stage specific paths [[0547374e]]. Sweep verification grep at the end — the drift pattern must die everywhere, not just the enumerated lines [[4a9d8030: when a convention changes, grep the OLD literal repo-wide]]. Do not touch `docs/superpowers/` historical files or `.tech-debt/`.

**Confidence:** T1 95% — enumerated, verified line edits + a closing sweep grep; doc-only. T2 95% — gates + PR proven. Floor 92% met.

---

### Task 1: Edit pass

**Files:**
- Modify: `skills/ralph-status/SKILL.md`, `skills/ralph-cancel/SKILL.md`, `skills/ralph-promote/SKILL.md`, `skills/ralph-triage/SKILL.md`, `skills/ralph-recover/SKILL.md`, `skills/ralph-report/SKILL.md`, `skills/ralph-new/SKILL.md`

- [ ] **Step 1:** Apply the evidence-map edits per file using the canonical wording. Read each file fully first — fix EVERY occurrence of the un-namespaced path / `main`-branch claim in that file, not only the enumerated lines (the evidence is a sample, not an exhaustive list).
- [ ] **Step 2:** Add the missing flag rows: `--queue-branch` to ralph-status's Inputs table (verify which OTHER skills' scripts also accept --queue-branch but lack the row — `git grep -n "queue-branch" skills/*/scripts/*.py skills/*/SKILL.md` — add rows wherever script-supports-but-doc-omits); `--instance-id` to ralph-new's flag table (mirror the script's argparse help, incl. resolution order).
- [ ] **Step 3:** Sweep verification:
  - `git grep -n "workspace_root>/queue/\|workspace-root>/queue/\|workspaces/queue\"" skills/` → zero hits
  - `git grep -niE "always on .main.|on \`main\`" skills/*/SKILL.md` → zero hits referring to the queue clone (a hit about the TARGET repo's main branch is fine — judge each)
- [ ] **Step 4:** Commit — `git add skills/*/SKILL.md && git commit -m "docs(skills): queue clone path, branch, and flag docs match multi-ralph reality"`

### Task 2: Gates, PR

- [ ] **Step 1:** `uv run pytest tests/skills tests/test_prompt_structure.py -q` green (some tests read SKILL.md frontmatter); `uv run ruff check skills` clean (markdown ignored but cheap).
- [ ] **Step 2:** Push `tech-debt/skill-md-queue-docs-drift`, PR per campaign Task 10, bot-watch to merge.
