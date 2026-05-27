# Ralph

Ralph is a per-repo autonomous coding loop. You point it at a git repo
that has a `.ralph/` queue of Product Backlog Items (PBIs), and it
spawns Claude in a loop to work them down one by one — opening PRs,
handling review feedback, and reporting back into the queue.

Design and plans:
- Design: `docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md`
- Orchestrator plan: `docs/superpowers/plans/2026-05-24-00-orchestrator.md`

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency / venv management
- Git
- [GitHub CLI (`gh`)](https://cli.github.com/) authenticated via `gh auth login`
- [Claude Code CLI (`claude`)](https://docs.claude.com/claude-code) authenticated via Claude Code's OAuth login

## Install

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

## One-time setup

Tell ralph where to keep its per-repo workspaces:

```bash
uv run ralph-executor init
```

You'll be prompted for `ralph_home` (a directory under which every
ralph-managed repo checkout will live). The default is
`C:\dev\ralph` on Windows / `~/dev/ralph` on POSIX. The choice is
written to `~/.ralph/config.toml`:

```toml
ralph_home = "C:/dev/ralph"
```

`init` also checks that `gh` and `claude` are on PATH and that `gh` is
logged in. Missing tools are warnings, not blockers.

For scripting, skip the prompt:

```bash
uv run ralph-executor init --ralph-home /opt/ralph --yes
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

```bash
uv run ralph-executor --workspace repo
```

This resolves to `$RALPH_HOME/repo`, switches to the `ralph-queue`
branch, and starts iterating. It runs until you Ctrl-C it.

Other forms:

```bash
# Explicit path
uv run ralph-executor --repo /path/to/checkout

# Whatever's in the current directory
cd /path/to/checkout && uv run ralph-executor

# Single iteration (for debugging)
uv run ralph-executor --once --workspace repo
```

## Running multiple ralphs

The `ralph_home` convention is what makes this clean. One subdirectory
per ralph:

```
C:\dev\ralph\
  repo_a\    ← ralph #1
  repo_b\    ← ralph #2
  repo_c\    ← ralph #3
```

Open three terminals:

```powershell
uv run ralph-executor --workspace repo_a   # terminal 1
uv run ralph-executor --workspace repo_b   # terminal 2
uv run ralph-executor --workspace repo_c   # terminal 3
```

Each instance is fully isolated — its own queue, its own feature
branches (`ralph/<PBI-ID>` namespaced inside the workspace), its own
spawned Claude session.

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
