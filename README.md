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
| `workspace_root` | Where ralph clones the queue and target repos | `~/ralph-workspaces` |
| `queue_repo` | HTTPS URL of the queue repo holding `.ralph/` state | `https://github.com/emp3thy/ralph-queue` |
| `queue_branch` | Branch on `queue_repo` that holds `.ralph/` state. Default `ralph-queue`. Override with TOML, `RALPH_QUEUE_BRANCH`, or `--queue-branch`. | `ralph-queue` |
| `instance_id` | Per-instance identity for multi-ralph. Defaults to the sanitised hostname. Override with TOML, `RALPH_INSTANCE_ID`, or `--instance-id`. Must match `^[a-z0-9][a-z0-9_-]{0,62}$`. See [Running multiple ralphs](#running-multiple-ralphs). | `ralph-a` |

`init` only accepts one flag: `--yes` (non-interactive: OS default for
`workspace_root`, default `ralph-queue` for `queue_branch`, default
`instance_id` from the sanitised hostname, `queue_repo` must be added
manually post-init).

For fully scripted setup (CI, pods), write `~/.ralph/config.toml`
directly instead:

```bash
mkdir -p ~/.ralph
cat > ~/.ralph/config.toml <<'EOF'
workspace_root = "/opt/ralph/workspaces"
queue_repo = "https://github.com/emp3thy/ralph-queue"
queue_branch = "ralph-queue"
instance_id = "ralph-a"   # optional — defaults to sanitised hostname
EOF
```

The interactive `init` smoke-tests the queue URL by attempting a clone.
If your network or auth is flaky it will print a warning but still write
the config — the executor will retry on its next iteration.

## Working the queue

`ralph-new` is the single sanctioned adder. Add a PBI with `ralph-new`
(interactive) or by writing a Markdown file under
`.ralph/inbox/<PBI-ID>/PBI.md` on the queue repo's `ralph-queue` branch.
The retired `ralph-add` skill is auto-removed by `ralph-executor init`
from both `claude_skills_dir` (`~/.claude/skills/` by default) and the
configured `skills_root` — operators upgrading from a pre-93d3158
install no longer need to scrub it by hand. The PBI's `target_repo`
frontmatter field points at the GitHub repo to be modified. Ralph
clones every target it encounters under
`<workspace_root>/clones/<owner>/<name>/`.

Six skills cover the operator workflow. All read or write
`<workspace_root>/queue-<instance_id>/.ralph/` (the queue clone — see
[Running multiple ralphs](#running-multiple-ralphs) for the namespacing)
and never touch the target repos themselves.

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

### Recover an orphan claim: `ralph-recover`

Move a PBI out of `current/<id>/` when the owning ralph instance is gone
(host crashed, workspace lost, fleet rebalance). Deletes the orphan
`CLAIM.json`, appends a `HISTORY.md` audit entry naming the previous
owner, and (for `--to inbox`) resets the attempt counter.

```bash
uv run python skills/ralph-recover/scripts/recover.py --pbi-id WI-1234 --to inbox
```

`ralph-cancel` and `ralph-promote` refuse with exit `3` when the
PBI's `CLAIM.json` names a different instance and route the operator
here. See [Running multiple ralphs](#running-multiple-ralphs).

## Running ralph

```bash
uv run ralph-executor             # drain queue
uv run ralph-executor --watch     # daemon
uv run ralph-executor --once      # single iteration
```

One executor drains the whole queue across every distinct `target_repo`
URL. No second-terminal / second-workspace setup is needed.

## Running multiple ralphs

Ralph supports multiple instances draining the same queue concurrently
across separate hosts. Each instance carries a unique `instance_id`,
clones the queue into its own namespaced workspace directory, and
writes a `CLAIM.json` ownership marker beside each PBI it claims so the
other instances skip foreign work.

### Per-instance identity

Each instance has an `instance_id` resolved from (highest priority
wins):

1. `--instance-id NAME` CLI flag
2. `RALPH_INSTANCE_ID` environment variable
3. `instance_id` in `~/.ralph/config.toml`
4. The sanitised hostname (default)

The value must match `^[a-z0-9][a-z0-9_-]{0,62}$` — filesystem-safe
lowercase, 1–63 chars, must start with an alphanumeric. Empty strings,
uppercase, dots, and spaces are rejected at resolution time.

### Single ralph (N=1)

You do not have to set anything. `init` writes the sanitised hostname
to `~/.ralph/config.toml` as the default `instance_id`, and that name
flows through to the workspace path
(`<workspace_root>/queue-<instance_id>/`) and every `CLAIM.json`. The
operator surface is unchanged.

### Two or more ralphs across hosts (N≥2)

Set `instance_id` explicitly on each host. Either edit
`~/.ralph/config.toml`:

```toml
instance_id = "ralph-a"   # on host A
```

…or export it in the executor's environment:

```bash
export RALPH_INSTANCE_ID=ralph-a   # on host A
export RALPH_INSTANCE_ID=ralph-b   # on host B
```

Each host's queue clone lives at `<workspace_root>/queue-<instance_id>/`
(e.g. `queue-ralph-a/`, `queue-ralph-b/`). All instances share the same
upstream queue repo and `queue_branch`; they coordinate through
atomic `CLAIM.json` writes pushed back to the queue. `current_pbi()`
filters by the local `instance_id` so each instance only iterates on
PBIs it claimed.

`ralph-status` shows an `OWNER` column with the claim's instance id
for every PBI in `current/` (em-dash for inbox / pending-pr / blocked
rows and for any `current/<id>/` directory whose `CLAIM.json` is
missing or malformed).

`ralph-cancel` and `ralph-promote` refuse to act on a PBI whose
`CLAIM.json` names a different instance (exit `3`) and direct the
operator at `ralph-recover`.

### Two ralphs on the same host

Refused by the workspace lockfile. Each instance acquires an
exclusive OS lock on
`<workspace_root>/queue-<instance_id>/.ralph.lock` at startup (POSIX
`fcntl.flock`, Windows `msvcrt.locking`); a second startup against
the same path raises `LockfileError` and aborts. To run more than one
ralph on the same machine, give each its own `workspace_root` AND its
own `instance_id`. The OS releases the lock on process exit (clean or
crash), so a stale lockfile from a previous run is not a manual
cleanup problem.

### Recovering a stuck claim

If a ralph instance crashes mid-iteration (or its workspace is
deleted) leaving a `CLAIM.json` no other instance can take, use
[`ralph-recover`](#recover-an-orphan-claim-ralph-recover):

```bash
uv run python skills/ralph-recover/scripts/recover.py --pbi-id WI-1234 --to inbox
```

`--to inbox` re-dispatches the PBI and resets its attempt counter;
`--to blocked` parks it for human triage and preserves the counter.
The skill refuses to run when the halt sentinel is active (exit `4`).

### Upgrading from a pre-multi-ralph install

The first startup of the new executor binary renames the legacy
single-host clone at `<workspace_root>/queue/` to
`<workspace_root>/queue-<instance_id>/` atomically; if both paths
already exist the executor refuses to start and asks the operator to
remove one. Recommended upgrade procedure:

1. Drain `current/` on the running ralph (let it finish in-flight PBIs
   or `ralph-cancel` them).
2. Stop the executor.
3. Upgrade the binary (`uv sync` or pull the new container image).
4. Start the new executor. Legacy `queue/` is renamed to
   `queue-<instance_id>/` on the first iteration; existing PBIs in
   `current/` continue to be owned by this instance because the new
   loop writes a `CLAIM.json` only when claiming inbox PBIs — current
   PBIs without a `CLAIM.json` surface as `QueueError: malformed
   claim` if they ever reach `current_pbi()`. The cleanest path is to
   drain `current/` before upgrading.

## Configuration precedence

Knobs read from (highest priority wins):

1. CLI flags (`--log-level`, `--queue-repo`, `--queue-branch`, `--instance-id`, `--watch`)
2. `RALPH_*` environment variables
3. `~/.ralph/config.toml` (user TOML)
4. Hard-coded defaults

Secrets stay env-only: `GH_TOKEN` (auto-resolved by `gh` CLI),
optional `ANTHROPIC_API_KEY` (omit to use Claude Code OAuth).

`git_host` is also TOML-readable — set `git_host = "github"` (or
`"ado"`) in `~/.ralph/config.toml` instead of exporting
`$RALPH_GIT_HOST` every shell.

Same goes for project identifiers and alerting URLs that aren't
secrets — set these once in `~/.ralph/config.toml`:

```toml
gh_owner = "your-github-org-or-user"     # was $GH_OWNER
ado_org_url = "https://dev.azure.com/..." # was $ADO_ORG_URL
ado_project = "your-project"             # was $ADO_PROJECT
halt_webhook = "https://..."             # was $RALPH_HALT_WEBHOOK
```

Per-machine paths also live in `~/.ralph/config.toml`:

```toml
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

Override via user TOML:

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
