# Ralph

Ralph is a per-repo autonomous coding loop. You point it at a git repo
that has a `.ralph/` queue of Product Backlog Items (PBIs), and it
spawns Claude in a loop to work them down one by one — opening PRs,
handling review feedback, and reporting back into the queue.

Design and plans:
- Design: `docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md`
- Orchestrator plan: `docs/superpowers/plans/2026-05-24-00-orchestrator.md`

## Architecture & setup

- **Architecture overview:** [`docs/runbooks/ralph-architecture.md`](docs/runbooks/ralph-architecture.md) — the three pieces (executor source, queue repo, workspace), data flow, branch model.
- **Setup guide:** [`docs/runbooks/ralph-setup.md`](docs/runbooks/ralph-setup.md) — install, init, full config + CLI reference.
- **Queue repo provisioning:** [`docs/runbooks/ralph-queue-setup.md`](docs/runbooks/ralph-queue-setup.md) — `setup_ralph_queue_github.py` deep dive.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency / venv management
- Git
- [GitHub CLI (`gh`)](https://cli.github.com/) authenticated via `gh auth login`
- [Claude Code CLI (`claude`)](https://docs.claude.com/claude-code) authenticated via Claude Code's OAuth login

## Install

You have two options.

### Workstation (recommended for development)

```bash
git clone https://github.com/emp3thy/ralph.git
cd ralph
uv sync
```

This creates the venv and installs `ralph-executor` into it. Run it
through `uv run`:

```bash
uv run ralph-executor --help
```

### Container (recommended for unattended pod deployment)

Pull the ROSA image (Phase 1 — GitHub variant):

```bash
docker pull <registry>/ralph-executor:<tag>
```

See `docs/superpowers/ops/2026-05-28-pod-deployment.md` for the full
pod runbook.

## One-time setup

Ralph reads its config from `~/.ralph/config.toml`. The `init`
subcommand creates it interactively:

```bash
uv run ralph-executor init
```

You will be prompted for the following values:

| Key | What it is | Example |
|---|---|---|
| `ralph_home` | Root for legacy single-checkout state (kept for compatibility) | `~/dev/ralph` |
| `workspace_root` | Where ralph clones the queue and target repos | `~/ralph-workspaces` |
| `queue_repo` | HTTPS URL of the queue repo holding `.ralph/` state | `https://github.com/emp3thy/ralph-queue` |
| `queue_branch` | Branch on `queue_repo` that holds `.ralph/` state. Default `ralph-queue`. Override with TOML, `RALPH_QUEUE_BRANCH`, or `--queue-branch`. | `ralph-queue` |

`init` only accepts two flags: `--ralph-home PATH` (skip the ralph_home
prompt) and `--yes` (non-interactive: OS default for `ralph_home`, default
`queue_branch`, and SKIP the `queue_repo` prompt with a warning — there is
no sensible default to write). All other keys are collected via interactive
prompts or written manually to the TOML.

For fully scripted setup (CI, pods), write `~/.ralph/config.toml`
directly instead:

```bash
mkdir -p ~/.ralph
cat > ~/.ralph/config.toml <<'EOF'
ralph_home = "/opt/ralph"
workspace_root = "/opt/ralph/workspaces"
queue_repo = "https://github.com/emp3thy/ralph-queue"
queue_branch = "ralph-queue"
EOF
```

The interactive `init` smoke-tests the queue URL by attempting a clone.
If your network or auth is flaky it will print a warning but still write
the config — the executor will retry on its next iteration.

## Working the queue

Five skills cover the operator workflow. All read or write
`<workspace_root>/queue-<instance_id>/.ralph/` (the queue clone — see
"Running multiple ralphs" below for the multi-instance namespacing) and
never touch the target repos themselves. All four mutating skills plus
`ralph-status` accept `--instance-id ID` to target a specific instance's
clone; without it they resolve the same chain as the executor.

### See what's in flight: `ralph-status`

```bash
uv run python skills/ralph-status/scripts/status.py
```

Output (grouped by target repo):

```
TARGET                                  STATE       ID        TYPE     SEVERITY  AGE   TITLE
https://github.com/emp3thy/svc-auth     inbox       WI-1234   feature  normal    2h    Add /healthz
https://github.com/emp3thy/svc-auth     current     WI-1235   bug      critical  1h    Pod crashloops
https://github.com/emp3thy/svc-billing  pending-pr  WI-980    feature  high      6h    Migrate invoices
```

Filters:
- `--state {inbox,current,pending-pr,done,blocked}` — narrow to one state.
- `--target-repo <url>` — narrow to one target.
- `--json` — machine-readable.

### Add a PBI: `ralph-new`

```bash
uv run python skills/ralph-new/scripts/new.py \
  --target-repo https://github.com/emp3thy/svc-auth \
  --title "Add /healthz endpoint" \
  --type feature \
  --severity normal
```

The skill slugifies the title to a PBI id, writes the canonical PBI
directory shape into the queue clone's `inbox/`, commits
`chore(queue): add <id>`, and pushes. Interactive prompts gather any
missing required fields unless `--non-interactive` is passed. See
`docs/runbooks/ralph-setup.md` for the full flag table.

### Cancel a PBI: `ralph-cancel`

Writes a CANCEL sentinel into `current/<id>/`. The executor picks it up
on the next iteration and moves the PBI out.

```bash
uv run python skills/ralph-cancel/scripts/cancel.py --pbi-id WI-1234
```

### Promote a PBI: `ralph-promote`

Move a PBI between state folders (e.g. from `inbox/` to `current/` to
manually queue it for the next iteration).

```bash
uv run python skills/ralph-promote/scripts/promote.py \
  --pbi-id WI-1234 --from inbox --to current
```

### Triage a blocked PBI: `ralph-triage`

Route a `blocked/` PBI either back to `inbox/` (with attempts reset) or
out to `archive/`.

```bash
uv run python skills/ralph-triage/scripts/triage.py --pbi-id WI-1234 --to inbox
```

## Per-repo setup

For each repo you want ralph to manage:

```bash
# 1. Clone the target repo into $RALPH_HOME/<name>
git clone https://github.com/owner/repo C:\dev\ralph\repo

# 2. Scaffold the ralph-queue branch + .ralph/ skeleton
uv run ralph-executor scaffold --workspace repo

# 3. Push the queue branch
git -C C:\dev\ralph\repo push -u origin ralph-queue
```

`scaffold` creates the `ralph-queue` branch off your current `HEAD`
(usually `main`), populates `.ralph/{inbox,current,pending-pr,done,blocked}/`
with `.gitkeep` files, and writes a commented `.ralph/config.toml`
stub. It commits the scaffold locally but does **not** push — you
inspect, then push when ready.

Optional: harden the queue branch on GitHub (require linear history,
disable force-push):

```bash
GH_OWNER=owner GH_TOKEN=$(gh auth token) \
  uv run python scripts/setup_ralph_queue_github.py repo
```

## Running ralph

**Ralph startup script (Windows / PowerShell):** `scripts/start-ralph.ps1`
wraps the prerequisite checks (`uv`, `git`, `gh`, `claude`, `~/.ralph/config.toml`),
runs `uv sync`, and launches the executor. Defaults to workspace `queue`,
drain-once mode.

```powershell
.\scripts\start-ralph.ps1                    # default: workspace queue, drain-once
.\scripts\start-ralph.ps1 -Workspace repo    # different workspace
.\scripts\start-ralph.ps1 -Watch             # daemon mode
.\scripts\start-ralph.ps1 -Once              # single iteration
```

Or invoke the executor directly:

```bash
uv run ralph-executor --workspace repo
```

This resolves to `$RALPH_HOME/repo`, switches to the `ralph-queue`
branch, and starts iterating. By default ralph **drains the queue and
exits 0** once it sees `idle_exit_threshold` (default `2`) consecutive
idle iterations — i.e. no PBI to claim and nothing in `current/`. The
final log line is `INFO ralph_executor.cli: queue drained -- exiting
after N consecutive idle iterations`, which a pod / container
supervisor can grep for to confirm orderly shutdown.

This default is built for **unattended pod / container deployments**
where the queue contents are baked in at launch and a process that
doesn't terminate when it's done its work is just burning compute.

For an interactive workstation session — operator keeps pushing PBIs
into `inbox/` mid-run — pass `--watch` to get the legacy daemon
behaviour (run forever, sleep `iteration_sleep_seconds` between idles,
exit only on Ctrl-C):

```bash
uv run ralph-executor --watch --workspace repo
```

Other forms:

```bash
# Explicit path
uv run ralph-executor --repo /path/to/checkout

# Whatever's in the current directory
cd /path/to/checkout && uv run ralph-executor

# Single iteration (for debugging) — exits after 1 iter regardless of outcome
uv run ralph-executor --once --workspace repo

# Daemon mode (workstation use)
uv run ralph-executor --watch --workspace repo
```

Pin the daemon default per project in `<repo>/.ralph/config.toml`:

```toml
watch_mode = true            # equivalent to passing --watch every time
idle_exit_threshold = 5      # tolerate more transient idles before drain
```

…or per shell:

```bash
RALPH_WATCH_MODE=1 uv run ralph-executor --workspace repo
RALPH_IDLE_EXIT_THRESHOLD=5 uv run ralph-executor --workspace repo
```

## Running multiple ralphs

Two patterns:

1. **One ralph per target repo** — run `N` executors, each pointed at a
   distinct target repo. Each has its own `ralph_home` subdir and
   queue. Open `N` terminals (`--workspace repo_a`, `--workspace repo_b`,
   …). No coordination needed because the queues are disjoint.
2. **N ralphs draining one queue** (multi-ralph) — run `N` executors
   against the SAME `queue_repo`, each with a distinct `instance_id`.
   They take PBIs from the shared `inbox/` concurrently, push claim
   commits via the queue branch's git rebase machinery, and never
   touch each other's `current/<PBI>` work. This is the right shape
   when one team owns one queue and wants horizontal throughput.

### Multi-ralph: per-instance identity

Each executor carries a stable `instance_id`. It selects:

* the per-instance queue clone path: `<workspace_root>/queue-<instance_id>/`
* the `CLAIM.json` written into every PBI under `current/`
* the workspace lockfile: `<workspace_root>/queue-<instance_id>/.ralph.lock`
* `tripped_by_instance` in any META-BUG written by the cycle detector

**Resolution chain** (highest precedence wins):

1. `--instance-id ID` CLI flag (on `ralph-executor` and every operator skill)
2. `$RALPH_INSTANCE_ID` env var
3. `instance_id` in `<repo>/.ralph/config.toml`
4. `instance_id` in `~/.ralph/config.toml`
5. Sanitised hostname

Sanitisation lowercases, replaces non-`[a-z0-9_-]` chars with `-`,
collapses runs, and strips leading/trailing dashes. Validation rejects
empty values and anything not matching `^[a-z0-9][a-z0-9_-]{0,62}$`. `ralph-executor init`
prompts for `instance_id` with the sanitised hostname as the default;
`--yes` accepts the default non-interactively.

### Multi-ralph: CLAIM.json ownership

When the loop claims a PBI from `inbox/` into `current/`, it writes a
`CLAIM.json` alongside the entry file (`PBI.md`/`BUG.md`/`FEEDBACK.md`)
naming the owning `instance_id`, the claim timestamp, and the hostname.
The claim is atomic — `CLAIM.json` lands in the same git commit as the
`git mv inbox/<id>/ current/<id>/` so concurrent pulls never observe a
PBI in `current/` without its claim.

`current_pbi()` filters by `instance_id`: each executor only sees PBIs
it owns. Foreign claims and PBI dirs lacking a `CLAIM.json` are skipped.

### Multi-ralph: operator skills

All four operator skills accept `--instance-id ID`, propagate it to the
queue clone path (`<workspace_root>/queue-<instance_id>/`), and the
mutating ones refuse foreign claims:

* `ralph-status` — adds an `OWNER` column. Resolves `instance_id` so
  the read targets the correct clone. Foreign claims display the other
  instance's id; malformed CLAIM.json shows `<malformed>`.
* `ralph-cancel` — refuses to cancel a PBI claimed by another instance
  (exit 2, "claimed by …, not …. Use `ralph-recover` if you need to
  force.").
* `ralph-promote` — refuses to move a PBI out of `current/` if the
  claim does not belong to the resolved `instance_id`. Sources other
  than `current/` are not gated.
* `ralph-triage` — adds `--instance-id` for the namespaced clone.
  No ownership check (operates on `blocked/`, which carries no claim).

### Multi-ralph: workspace lockfile

`ralph-executor` acquires an exclusive OS lock at
`<workspace_root>/queue-<instance_id>/.ralph.lock` at startup
(`fcntl.flock` on POSIX, `msvcrt.locking` on Windows). A second process
trying the same workspace + instance combination exits 2 with `error:
another ralph already running on this workspace: …`. The lock is
released on every Python exit path, including Ctrl-C and unhandled
exceptions. The lockfile is OUTSIDE `.git` and never committed.

### Multi-ralph: upgrade procedure

When upgrading from single-ralph to multi-ralph:

1. **Drain `current/` first** — let any in-flight PBI complete (or
   `ralph-cancel` it) so no work needs a claim transferred.
2. Set `instance_id` (TOML or hostname default) on each executor host.
3. First `ralph-executor` startup migrates `<workspace_root>/queue/`
   to `<workspace_root>/queue-<instance_id>/` automatically (atomic
   `shutil.move`; refuses if BOTH paths already exist).

If a previous owner crashed and left a stale `CLAIM.json` blocking
recovery, use `ralph-recover --pbi-id <id> --to inbox|blocked` — the
sole skill authorised to bypass the ownership check.

### Multi-ralph: example two-instance layout

```
C:\dev\ralph\
  ws-a\
    queue-ralph-a\           ← ralph-a's queue clone + .ralph.lock
      .ralph\current\WI-1\   ← CLAIM.json:{instance_id: ralph-a}
  ws-b\
    queue-ralph-b\           ← ralph-b's queue clone + .ralph.lock
      .ralph\current\WI-2\   ← CLAIM.json:{instance_id: ralph-b}
```

Two terminals:

```powershell
$env:RALPH_WORKSPACE = "C:\dev\ralph\ws-a"
uv run ralph-executor --instance-id ralph-a    # terminal 1

$env:RALPH_WORKSPACE = "C:\dev\ralph\ws-b"
uv run ralph-executor --instance-id ralph-b    # terminal 2
```

Both drain the same `queue_repo`; the queue branch's `git pull --rebase`
mechanism arbitrates concurrent claim pushes.

## Configuration precedence

Knobs read from (highest priority wins):

1. CLI flags (`--repo`, `--workspace`, `--log-level`)
2. `RALPH_*` environment variables
3. `<repo>/.ralph/config.toml` (per-project)
4. Hard-coded defaults

Workspace root specifically:

1. `$RALPH_HOME` environment variable
2. `ralph_home` in `~/.ralph/config.toml` (written by `init`)

Secrets stay env-only: `GH_TOKEN` (auto-resolved by `gh` CLI),
optional `ANTHROPIC_API_KEY` (omit to use Claude Code OAuth).

`git_host` is also TOML-readable — set `git_host = "github"` (or
`"ado"`) in `<repo>/.ralph/config.toml` instead of exporting
`$RALPH_GIT_HOST` every shell. The `scaffold` subcommand emits this
key in the stub it writes.

Same goes for project identifiers and alerting URLs that aren't
secrets — set these once in `<repo>/.ralph/config.toml`:

```toml
gh_owner = "your-github-org-or-user"     # was $GH_OWNER
ado_org_url = "https://dev.azure.com/..." # was $ADO_ORG_URL
ado_project = "your-project"             # was $ADO_PROJECT
halt_webhook = "https://..."             # was $RALPH_HALT_WEBHOOK
```

Per-machine paths (where things live on YOUR machine) live in
`~/.ralph/config.toml` instead of project config:

```toml
ralph_home = "C:/dev/ralph"
skills_root = "C:/Users/you/source/ralph/skills"  # was $RALPH_SKILLS_ROOT
claude_skills_dir = "C:/Users/you/.claude/skills" # was $RALPH_CLAUDE_SKILLS_DIR
```

Secrets stay env-only: `GH_TOKEN`, `ADO_PAT`, optional
`ANTHROPIC_API_KEY`.

### Claude subprocess permission mode

The executor spawns each `claude -p` invocation with an explicit
`--permission-mode` argument so the spawned subprocess never inherits
the host's `~/.claude/settings.json` `defaultMode`. Default is
`bypassPermissions` because ralph runs Claude non-interactively and
cannot answer permission prompts. Operators do NOT need to relax the
host's global `defaultMode` for ralph to run — leaving it at `"auto"`
is fine.

Override per project via TOML:

```toml
claude_permission_mode = "acceptEdits"   # was $RALPH_CLAUDE_PERMISSION_MODE
```

…or per shell via env:

```bash
export RALPH_CLAUDE_PERMISSION_MODE=plan
```

Allowed values mirror the claude CLI's `--permission-mode` enum:
`acceptEdits`, `auto`, `bypassPermissions`, `default`, `dontAsk`,
`plan`. An unrecognised value raises `ConfigError` at startup.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy ralph_executor scripts tests
```

<!-- ralph smoke test passed on 2026-05-25 -->
