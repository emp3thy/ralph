# Ralph Bootstrap Operator Runbook

**Audience:** an LLM (or human) setting up and operating Ralph during the partial-bootstrap phase — Stage B per the orchestrator. The MVR is built (Stage A complete) but most surrounding tooling (`ralph-add`, `ralph-status`, `ralph-doctor`, `ralph-cancel`, `ralph-promote`, `ralph-triage`) hasn't shipped yet. Every operation that those tools will eventually automate has a manual equivalent here.

**You are NOT building Ralph in this runbook** — you're operating it. Building happens via the plans in `docs/superpowers/plans/`.

**Target host:** GitHub at home (Phase 1). `RALPH_GIT_HOST=github`. If you're past Stage C and operating against ADO, substitute `ado` everywhere and use the ADO equivalents — but this runbook assumes GitHub.

---

## 1. Prerequisites

### 1.1 Local machine

- **OS:** Windows / macOS / Linux. Windows users: PowerShell or Git Bash both work; this runbook shows POSIX shell commands and notes Windows alternatives where they differ.
- **Python 3.12+:** verify `python --version`. If absent, install via `uv python install 3.12`.
- **uv:** install per https://docs.astral.sh/uv/getting-started/installation/. Verify `uv --version`.
- **Claude Code CLI:** install per Anthropic docs. Verify `claude --version`. The MVR's `claude_spawn.py` invokes `claude -p`, so it must be on PATH.
- **GitHub CLI (`gh`) — optional but recommended** for quick PR inspection. Verify `gh --version`.
- **git:** verify `git --version` ≥ 2.30 (needed for `git rev-parse --git-common-dir`).

### 1.2 GitHub identity

- **A GitHub account.**
- **A target repository** Ralph will operate against — this is the project Ralph builds features in. For Stage B, this is the `ralph` repo itself. (Self-hosting is the whole point of Stage B.)
- **A Personal Access Token (PAT)** with scopes `repo` (full), `read:org` if your repo is in an org. Save the value somewhere; you'll set it as `GH_TOKEN`.

### 1.3 Anthropic credentials

- **Anthropic API key.** Set as `ANTHROPIC_API_KEY` env var. Verify by running a no-op `claude -p` against a trivial prompt.

---

## 2. One-time setup

These steps happen once on your machine. After this, daily operation is fast.

### 2.1 Clone the ralph repo

```bash
cd ~/source
git clone https://github.com/<your-user>/ralph.git
cd ralph
```

(Replace `<your-user>` with your GitHub username or org.)

### 2.2 Install Python deps

```bash
uv sync
```

This creates `.venv/` and installs everything from `pyproject.toml`.

### 2.3 Set environment variables

For convenience, put these in `~/.config/ralph/env.sh` (POSIX) or `~/Documents/ralph-env.ps1` (Windows PowerShell) and `source` / dot-source them each session:

```bash
# Required for any iteration
export RALPH_GIT_HOST=github
export GH_TOKEN=ghp_yourtokenhere
export GH_OWNER=your-github-user-or-org
export ANTHROPIC_API_KEY=sk-ant-...

# Required for the executor
export RALPH_REPO_PATH=/absolute/path/to/your/target/repo
export RALPH_QUEUE_BRANCH=ralph-queue
export RALPH_MAIN_BRANCH=main
export RALPH_MAX_ATTEMPTS=3
export RALPH_LOG_LEVEL=INFO

# Required by Plan 7's host_select.py (defaults are usually fine)
# export RALPH_SKILLS_ROOT=/absolute/path/to/ralph/skills
# export RALPH_CLAUDE_SKILLS_DIR=~/.claude/skills
```

PowerShell equivalent: replace `export FOO=bar` with `$env:FOO = "bar"`.

### 2.4 Set up the `ralph-queue` branch on your target repo

Until Plan 02 Phase 1 ships, you do this by hand. After Plan 02 Phase 1 ships, run the script: `uv run scripts/setup_ralph_queue_github.py --owner $GH_OWNER --repo <repo-name>`.

**Manual procedure:**

```bash
cd $RALPH_REPO_PATH
git fetch
git checkout -B ralph-queue origin/main   # branch off main
mkdir -p .ralph/inbox .ralph/current .ralph/pending-pr .ralph/done .ralph/blocked .ralph/archive .ralph/state
touch .ralph/inbox/.gitkeep .ralph/current/.gitkeep .ralph/pending-pr/.gitkeep .ralph/done/.gitkeep .ralph/blocked/.gitkeep .ralph/archive/.gitkeep .ralph/state/.gitkeep
git add .ralph/
git commit -m "chore: initialise .ralph/ queue structure"
git push -u origin ralph-queue
```

**Configure GitHub branch protection** for `ralph-queue` via the UI or `gh`:
- Settings → Branches → Add rule for `ralph-queue`
- Allow only your account / Ralph's service principal to push (this is what stops accidental pushes)
- Do NOT require PR to merge — `ralph-queue` is not for PRs; supervisor skills and the executor push to it directly

### 2.5 Stage the Phase 1 skills into the Claude skills directory

Until Plan 07's `host_select.py` ships and runs automatically at executor startup, you stage the skills manually:

```bash
# Linux / macOS
mkdir -p ~/.claude/skills
ln -sfn $PWD/skills/pr-github ~/.claude/skills/pr
ln -sfn $PWD/skills/workitem-fetch-github ~/.claude/skills/workitem-fetch
```

```powershell
# Windows PowerShell (requires admin OR Developer Mode enabled for symlinks)
New-Item -ItemType SymbolicLink -Path "$HOME\.claude\skills\pr" -Target "$PWD\skills\pr-github" -Force
New-Item -ItemType SymbolicLink -Path "$HOME\.claude\skills\workitem-fetch" -Target "$PWD\skills\workitem-fetch-github" -Force
```

Fallback (no symlinks): copy the directories. They're small.

**Verify the staging worked:**

```bash
ls ~/.claude/skills/pr/scripts/
# Expect: create-pr.py, read-threads.py, reply.py, set-status.py, show.py

ls ~/.claude/skills/workitem-fetch/scripts/
# Expect: fetch.py (or whatever Plan 03 Phase 1 produced)

cat ~/.claude/skills/pr/SKILL.md | head -5
# Expect: name: pr-github
```

If `SKILL.md` says `name: pr-ado`, you staged the wrong host's skill.

### 2.6 Configure `.claude/settings.json`

The MVR's `.claude/settings.json` at the repo root should already allow the necessary tools. Verify it contains at minimum:

```json
{
  "permissions": {
    "allow": [
      "Bash(*)",
      "Edit(*)",
      "Write(*)",
      "Read(*)",
      "Grep(*)",
      "Glob(*)",
      "Skill(pr)",
      "Skill(workitem-fetch)"
    ]
  }
}
```

If you're running unattended (cron / background loop), add `--dangerously-skip-permissions` to the `claude` invocation, but for Stage B development we recommend keeping permission prompts ON and watching them.

---

## 3. Verify the MVR is functional (pre-iteration sanity)

Before running Ralph for real, confirm the MVR works end-to-end with a hand-crafted minimal PBI.

### 3.1 Hand-craft a trivial test PBI

```bash
cd $RALPH_REPO_PATH
git checkout ralph-queue
mkdir -p .ralph/inbox/TEST-001
```

Create `.ralph/inbox/TEST-001/PBI.md`:

```markdown
---
id: TEST-001
type: feature
status: inbox
severity: normal
attempts: 0
created_at: 2026-05-25T10:00:00Z
updated_at: 2026-05-25T10:00:00Z
---

# Add a comment to README.md

This is a trivial smoke test PBI.
```

Create `.ralph/inbox/TEST-001/PLAN.md`:

```markdown
# Plan: Add a smoke-test comment

- [ ] Step 1: Open README.md
- [ ] Step 2: Add a single line at the bottom: `<!-- ralph smoke test passed at $(date) -->`
- [ ] Step 3: Stage and commit
- [ ] Step 4: Push the branch (the auto PR job will create the PR; via ado-pr.create-pr if no auto PR job)
```

Create `.ralph/inbox/TEST-001/HISTORY.md` (empty):

```bash
touch .ralph/inbox/TEST-001/HISTORY.md
git add .ralph/inbox/TEST-001
git commit -m "test: add smoke-test PBI"
git push origin ralph-queue
```

### 3.2 Run a single Ralph iteration

```bash
cd ~/source/ralph
uv run ralph-executor --once
```

**Expected behaviour:**

1. `host_select.py` runs (if Plan 07's amendment has shipped — if not, you staged manually in step 2.5)
2. Iteration claims `TEST-001` from `inbox/` into `current/`
3. Spawns `claude -p`
4. Claude reads PROMPT.md, HISTORY.md, PBI.md, PLAN.md
5. Claude does the trivial edit on a fresh feature branch `ralph/TEST-001`
6. Claude pushes the branch
7. Claude calls the staged `pr` skill's `create-pr` operation
8. PR is created on GitHub
9. Executor moves `current/TEST-001` to `pending-pr/TEST-001`
10. Executor exits (because `--once`)

### 3.3 Verify the PR

```bash
gh pr list --repo $GH_OWNER/<repo>
# Expect: a PR titled "Add a comment to README.md" or similar
```

If this works, the MVR is functional and you can proceed to Stage B work.

If it fails, see the **Failure modes** section below.

---

## 4. Operating Ralph during Stage B

### 4.1 The four manual operations you'll do most often

| Want to do | After tool ships | Until then, manually |
|---|---|---|
| Add a PBI to the queue | `ralph-add <id>` | Hand-craft directory in `.ralph/inbox/<id>/` and push to `ralph-queue` (see 4.2) |
| Inspect queue state | `ralph-status` | `git fetch && git checkout ralph-queue && git pull && ls .ralph/*/` |
| Cancel an in-flight PBI | `ralph-cancel <id>` | `touch .ralph/current/<id>/CANCEL && git commit -m "cancel <id>" && git push` |
| Bump severity | `ralph-promote <id> critical` | Edit `<dir>/PBI.md` frontmatter, commit, push |
| Triage a blocked PBI | `ralph-triage <id> inbox --note "..."` | mv from `blocked/` to `inbox/`, edit HISTORY.md with note, push |
| Preflight check | `ralph-doctor` | Manual checklist in 4.6 |
| Run smoke | `uv run pytest tests/e2e/test_smoke.py` | (Plan 13 hasn't shipped yet) — manually run a trivial PBI through |

### 4.2 Hand-crafting a Stage B PBI for Ralph to build

Stage B's recommended order is: Plan 04 → Plan 03 → Plan 10 → Plan 11 P1 → Plan 08 → Plan 12 P1 → Plan 13.

For each one, you hand-craft a PBI that points Ralph at the plan file. Template:

```bash
cd $RALPH_REPO_PATH
git checkout ralph-queue
git pull

# Replace WI-1234 with the actual PBI id
PBI_ID="STAGE-B-PLAN-04"
mkdir -p .ralph/inbox/$PBI_ID
```

`.ralph/inbox/$PBI_ID/PBI.md`:

```markdown
---
id: STAGE-B-PLAN-04
type: feature
status: inbox
severity: normal
attempts: 0
created_at: 2026-05-25T10:00:00Z
updated_at: 2026-05-25T10:00:00Z
---

# Implement Plan 04: ralph-status skill

Implement the `ralph-status` skill per the canonical plan file at:

`docs/superpowers/plans/2026-05-24-04-ralph-status-skill.md`

Read the plan in full. Follow its TDD-shape tasks in order. Each task ends with a commit. When all tasks complete, push the branch and create a PR via the staged `pr` skill.

## Acceptance criteria

- All tests in `tests/skills/test_ralph_status.py` pass (`uv run pytest tests/skills/test_ralph_status.py -v`)
- `uv run ruff check .` clean
- `uv run mypy ralph_executor` clean
- The PR matches the verification gate at the bottom of the plan file
```

`.ralph/inbox/$PBI_ID/PLAN.md`:

Either:
- (a) Copy the plan file contents verbatim, OR
- (b) Add a one-line PLAN.md that says "See `docs/superpowers/plans/2026-05-24-04-ralph-status-skill.md` — Ralph reads it directly."

Option (b) is preferred; the plan file is the authoritative PLAN.md.

`.ralph/inbox/$PBI_ID/HISTORY.md`: empty.

```bash
touch .ralph/inbox/$PBI_ID/HISTORY.md
git add .ralph/inbox/$PBI_ID
git commit -m "feat(queue): add Stage B PBI for $PBI_ID"
git push origin ralph-queue
```

Now Ralph picks it up next iteration.

### 4.3 Run Ralph continuously

For a normal Stage B work session, run Ralph in a terminal you watch:

```bash
cd ~/source/ralph
uv run ralph-executor   # no --once = loop forever
```

It loops, polling `inbox/` for new PBIs every iteration. When inbox is empty and current is empty, it sleeps briefly between checks.

**To pause:** Ctrl+C. Anything in `current/` stays put; Ralph resumes on next start.

**To kill cleanly:** SIGTERM. Same behaviour.

**To restart on a fresh terminal:** just rerun the command. The filesystem state (folders) drives everything; the executor has no in-memory state to preserve.

### 4.4 Watching what Ralph does

Three useful views in three terminals:

**Terminal 1 — the executor itself** (running `uv run ralph-executor`).

**Terminal 2 — the queue state** (refreshes manually with each command until Plan 04 ships):

```bash
# Quick state snapshot
cd $RALPH_REPO_PATH
git fetch && git checkout ralph-queue && git pull
echo "INBOX:"; ls .ralph/inbox/ 2>/dev/null | grep -v gitkeep
echo "CURRENT:"; ls .ralph/current/ 2>/dev/null | grep -v gitkeep
echo "PENDING-PR:"; ls .ralph/pending-pr/ 2>/dev/null | grep -v gitkeep
echo "DONE (last 5):"; ls -t .ralph/done/ 2>/dev/null | grep -v gitkeep | head -5
echo "BLOCKED:"; ls .ralph/blocked/ 2>/dev/null | grep -v gitkeep
```

**Terminal 3 — open PRs:**

```bash
gh pr list --repo $GH_OWNER/<repo>
```

### 4.5 Inspecting a single PBI in flight

```bash
cd $RALPH_REPO_PATH
git checkout ralph-queue && git pull
cat .ralph/current/<PBI-id>/PBI.md     # the ask
cat .ralph/current/<PBI-id>/HISTORY.md # what Ralph has tried
ls .ralph/current/<PBI-id>/             # see if STUCK.md or CANCEL appeared
```

If `STUCK.md` exists, Ralph self-halted on this PBI. Read it — Ralph documents WHY in there.

### 4.6 Manual preflight (until ralph-doctor ships)

Before running Ralph for a longer session, sanity-check:

```bash
# Required env vars set?
env | grep -E "RALPH_GIT_HOST|GH_TOKEN|GH_OWNER|ANTHROPIC_API_KEY|RALPH_REPO_PATH"

# Skills staged correctly?
cat ~/.claude/skills/pr/SKILL.md | grep "^name:"
# Expect: name: pr-github  (for github host)
cat ~/.claude/skills/workitem-fetch/SKILL.md | grep "^name:"
# Expect: name: workitem-fetch-github

# GitHub auth works?
curl -sf -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/user | grep '"login":'

# Anthropic auth works?
echo "ping" | claude -p --max-turns 1 --append-system-prompt "Respond with one word." 2>&1 | head -1

# Repo writable?
cd $RALPH_REPO_PATH && git push --dry-run origin ralph-queue
```

If all of those succeed, you're good to start.

### 4.7 Recovering from failure modes

| Symptom | Likely cause | Recovery |
|---|---|---|
| Executor immediately exits with "RALPH_GIT_HOST unset" | Forgot to source the env file | `source ~/.config/ralph/env.sh` and retry |
| Claude session opens but immediately exits with permission prompt error | `.claude/settings.json` allow list missing a tool | Add the missing tool to the allow list |
| Claude makes a change but `git push` fails | Branch protection blocks Ralph's identity | Adjust GitHub branch protection or use a Ralph-specific account |
| PR is created but `create-pr` script returns error | GitHub auth scope is missing `repo`; or `GH_OWNER` is wrong | Regenerate PAT with `repo` scope; verify `GH_OWNER` matches the repo owner |
| PBI stuck in `current/` with no progress | Executor was killed mid-iteration. Filesystem state is consistent. | Just restart `ralph-executor`. It'll continue. |
| Multiple PBIs in `current/` (only one allowed) | A previous run was killed in an inconsistent state | mv all but one back to `inbox/`, commit, push, restart |
| Ralph keeps trying the same wrong thing | No cycle detector yet (Plan 09 may have shipped but not properly integrated) | Cancel the PBI by hand (4.1), append your notes to HISTORY.md, re-triage to inbox or archive |
| New PBI not picked up | Ralph already has something in `current/`; or queue branch wasn't pushed | Check `current/`; check `git log origin/ralph-queue` for your commit |

---

## 5. Handing the next Stage B PBI to Ralph — concrete checklist

For each plan in Stage B's recommended order:

1. **Confirm Ralph is idle** — `current/` empty, no STUCK.md, no blocked items needing your attention.
2. **Confirm working tree is clean** on the target repo.
3. **Hand-craft the PBI** per 4.2, pointing at the plan file.
4. **Verify Ralph picks it up** — start (or restart) the executor; watch the first iteration claim from `inbox/`.
5. **Watch for the first few iterations** — read what Ralph writes to HISTORY.md and to the feature branch. If Ralph is going off the rails, cancel (4.1) and amend the PBI's body with clarification.
6. **When Ralph creates the PR** — review it. If it looks right, merge. If it's wrong, leave a PR comment; Ralph's sweep (once Plan 08 ships) will pick it up. Until Plan 08 ships, manually hand-craft a PR-feedback PBI in `inbox/`.
7. **After PR merges** — the next iteration's sweep moves the PBI from `pending-pr/` to `done/` (once Plan 08 ships). Until then, you do this manually:
   ```bash
   git checkout ralph-queue && git pull
   git mv .ralph/pending-pr/<id> .ralph/done/<id>
   echo "Merged at $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> .ralph/done/<id>/HISTORY.md
   git commit -m "chore(queue): mv <id> to done" && git push
   ```
8. **Update Stage B progress** — track which plans have shipped (a simple text file in `~/.config/ralph/stage-b-progress.txt` is fine until something better exists).

---

## 6. When Stage B is complete — the smoke gate

Per the orchestrator's "Bootstrap stages" section, Stage B exits only when the smoke harness (Plan 13) runs green. The exact gate command:

```bash
cd ~/source/ralph
export RALPH_GIT_HOST=github GH_TOKEN=... GH_OWNER=...
uv run pytest tests/e2e/test_smoke.py -v
```

All 5 smoke scenarios pass → Stage B complete, Stage C can begin.

If any fail, fix forward inside Stage B. Don't ship Stage C work against a red smoke.

---

## 7. Common gotchas

- **`current/` should hold AT MOST ONE PBI.** If you see two, the executor will fail loudly. Resolve by hand.
- **Don't commit to `main` directly during Stage B.** Ralph's feature branches go to `main` via PR; queue mutations go to `ralph-queue`. Anything else confuses the executor.
- **Don't edit `PBI.md` after the PBI is in `current/`.** The body is immutable once Ralph starts working. If you need to change scope, cancel + re-add with new content.
- **Symlinks on Windows** require Developer Mode or admin. If they fail silently, fall back to copying.
- **Claude Code permission prompts** can interrupt Ralph if `--dangerously-skip-permissions` isn't set OR the allow list is incomplete. During Stage B watch the executor terminal — if it hangs, it's probably waiting on a prompt.
- **GitHub rate limits.** A PAT gets 5000 REST requests/hour. Ralph's per-iteration cost is small (~5-10 requests) so this is usually fine, but be aware during heavy iterations.
- **Branch confusion.** Ralph's iteration uses `ralph-queue` for queue state and `main` + `ralph/<PBI-ID>` for code. You should NEVER need to be on `ralph/<PBI-ID>` yourself — that's Ralph's working space.

---

## 8. Eventual end-state

Once Stage B completes (smoke green), most of this runbook becomes redundant — `ralph-status` replaces the queue-inspection commands, `ralph-add` replaces the hand-crafted PBI directories, `ralph-doctor` replaces the manual preflight, `ralph-cancel` / `ralph-promote` / `ralph-triage` replace the manual git operations. This runbook stays as a reference for "how to operate without the tools" — useful when debugging tool failures.

Stage C work is then handed to Ralph via the same PBI mechanism (now via `ralph-add`).
