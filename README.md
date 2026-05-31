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

`init` only accepts one flag: `--yes` (non-interactive: OS default for
`workspace_root`, default `ralph-queue` for `queue_branch`, `queue_repo`
must be added manually post-init).

For fully scripted setup (CI, pods), write `~/.ralph/config.toml`
directly instead:

```bash
mkdir -p ~/.ralph
cat > ~/.ralph/config.toml <<'EOF'
workspace_root = "/opt/ralph/workspaces"
queue_repo = "https://github.com/emp3thy/ralph-queue"
queue_branch = "ralph-queue"
EOF
```

The interactive `init` smoke-tests the queue URL by attempting a clone.
If your network or auth is flaky it will print a warning but still write
the config — the executor will retry on its next iteration.

## Working the queue

Add a PBI with `ralph-new` (interactive) or by writing a Markdown file
under `.ralph/inbox/<PBI-ID>/PBI.md` on the queue repo's `ralph-queue`
branch. The PBI's `target_repo` frontmatter field points at the GitHub
repo to be modified. Ralph clones every target it encounters under
`<workspace_root>/clones/<owner>/<name>/`.

Five skills cover the operator workflow. All read or write
`<workspace_root>/queue/.ralph/` (the queue clone) and never touch the
target repos themselves.

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

## Running ralph

```bash
uv run ralph-executor             # drain queue
uv run ralph-executor --watch     # daemon
uv run ralph-executor --once      # single iteration
```

One executor drains the whole queue across every distinct `target_repo`
URL. No second-terminal / second-workspace setup is needed.

## Configuration precedence

Knobs read from (highest priority wins):

1. CLI flags (`--log-level`, `--queue-repo`, `--queue-branch`, `--watch`)
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
