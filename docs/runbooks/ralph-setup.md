# Ralph setup guide

End-to-end setup for `ralph-executor`: install, queue-repo provision,
`init`, plus a full reference for every config key, CLI flag, and
operator-skill flag.

For the high-level picture of how the executor, the queue repo, and the
operator workspace fit together, read
[`ralph-architecture.md`](ralph-architecture.md) first.

## 1. Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency / venv management
- Git
- [GitHub CLI (`gh`)](https://cli.github.com/) authenticated via
  `gh auth login`
- [Claude Code CLI (`claude`)](https://docs.claude.com/claude-code)
  authenticated via Claude Code's OAuth login

Optional:

- `ANTHROPIC_API_KEY` — set this if you want the executor to use the
  Anthropic API directly instead of Claude Code's OAuth session.

## 2. Install

### Workstation

```bash
git clone https://github.com/emp3thy/ralph.git
cd ralph
uv sync
```

`uv sync` creates the venv and installs `ralph-executor` into it. Run
it through `uv run`:

```bash
uv run ralph-executor --help
```

### Container

Pull the image:

```bash
docker pull <registry>/ralph-executor:<tag>
```

See `docs/superpowers/ops/2026-05-28-pod-deployment.md` for the full
pod runbook.

## 3. Provision the queue repo

One command:

```bash
GH_TOKEN=<token> GH_OWNER=<owner> \
    uv run python -m scripts.setup_ralph_queue_github --repo ralph-queue
```

This creates the GitHub repository (private, under the user named in
`$GH_OWNER`; pass `--org <name>` to create under an organisation
instead), seeds `.ralph/` on the `ralph-queue` branch, and applies
branch protection on both `main` and `ralph-queue`. Idempotent —
re-running is a no-op.

> **GitHub Free + private repos:** branch protection on a private repo
> requires GitHub Pro / Team / Enterprise. On Free, the script logs a
> warning, leaves the repo unprotected, and exits 0; pass
> `--no-protection` explicitly to suppress the warning.

The full deep dive (PAT scopes, override precedence, troubleshooting
table, dry-run mode, Azure DevOps Phase 2) lives in
[`ralph-queue-setup.md`](ralph-queue-setup.md).

## 4. Initialize the executor

```bash
uv run ralph-executor init
```

Interactively prompts for `ralph_home` and `queue_repo`, then for
`queue_branch` (default `ralph-queue`). Writes them to
`~/.ralph/config.toml`. The queue URL is smoke-tested with
`git ls-remote`; failure prints a warning but still writes the config.

Non-interactive form:

```bash
uv run ralph-executor init --ralph-home /opt/ralph --yes
```

`--yes` accepts the OS default for `ralph_home` and skips both the
queue prompt and the branch prompt — `queue_branch` falls back to
`"ralph-queue"`, and `queue_repo` must be added manually to
`~/.ralph/config.toml` afterwards.

## 4a. Configure git host

The executor needs `git_host` plus host-specific auth before it will
start. Two mandatory pieces:

1. Set `git_host` (and on GitHub, `gh_owner`) in
   `<repo>/.ralph/config.toml`. The `<repo>` is the directory the
   executor is invoked from (or pointed at via `--repo` / `--workspace`).
   The file is git-ignored by default, so this is a per-clone
   per-machine knob:

   ```toml
   # <repo>/.ralph/config.toml
   git_host = "github"
   gh_owner = "<your-github-username-or-org>"
   ```

   Alternatively export `RALPH_GIT_HOST=github` and `GH_OWNER=<owner>`
   in the executor's environment.

2. Export the host auth token in the same shell that runs the executor:

   ```bash
   export GH_TOKEN="$(gh auth token)"   # GitHub
   # or
   export ADO_PAT=<personal-access-token>   # Azure DevOps
   ```

   `GH_TOKEN` / `ADO_PAT` are env-only by policy — never write them to
   TOML.

Without these the executor exits with
`error: git host is required but unset...` or
`error: git_host=github but required auth value(s) missing or blank: GH_TOKEN`.

## 5. Config reference: `~/.ralph/config.toml`

Per-machine knobs. Source: `ralph_executor/user_config.py`. Written by
`ralph-executor init`; can also be edited by hand.

| Key | Type | Required | Default | Env override | Description |
|---|---|---|---|---|---|
| `ralph_home` | string (path) | no | — | `$RALPH_HOME` | Root directory used to resolve `--workspace NAME` to `$RALPH_HOME/NAME`. Stored as a string; `~` is expanded. |
| `workspace_root` | string (path) | no | `$HOME/ralph-workspaces` | `$RALPH_WORKSPACE` | Where the queue clone and target-repo clones live. Each target gets `<workspace_root>/clones/<owner>/<name>/`; the queue clone is `<workspace_root>/queue/`. Also readable from the per-queue TOML — the executor reads either. |
| `skills_root` | string (path) | no | source-checkout default | `$RALPH_SKILLS_ROOT` | Source `skills/` tree used by `host_select.prepare_host_environment` to find `pr-<host>/` and `workitem-fetch-<host>/`. |
| `claude_skills_dir` | string (path) | no | `~/.claude/skills` | `$RALPH_CLAUDE_SKILLS_DIR` | Destination directory where staged `pr/` and `workitem-fetch/` end up for the spawned Claude subprocess. |
| `queue_repo` | string (URL) | yes (or set per-repo TOML / `--queue-repo`) | — | none | HTTPS URL of the queue repo. The executor clones it into `<workspace_root>/queue/`. |
| `queue_branch` | string | no | `ralph-queue` | `$RALPH_QUEUE_BRANCH` (executor only — skills do not read this env) | Branch on `queue_repo` that holds `.ralph/` state. |

## 6. Config reference: `<queue_repo>/.ralph/config.toml`

Per-queue knobs. Source: the `_TOML_KNOWN_KEYS` set and the
`DEFAULT_*` constants in `ralph_executor/config.py`. Unknown top-level
keys are logged at WARNING and ignored — forward-compat is cheap.

| Key | Type | Default | Env override | Description |
|---|---|---|---|---|
| `queue_repo` | string (URL) | — (required) | none | HTTPS URL of the queue repo. Required; loop crashes without it. Operators normally set this in `~/.ralph/config.toml` once; this per-repo override is for one-off CI runs. |
| `queue_branch` | string | `ralph-queue` | `$RALPH_QUEUE_BRANCH` | Branch on the queue repo that holds `.ralph/` state. Must be a plain branch name — empty, `HEAD`, or `refs/heads/...` are rejected at load time. |
| `main_branch` | string | `main` | `$RALPH_MAIN_BRANCH` | Default base branch on target repos. |
| `max_attempts` | int | `20` | `$RALPH_MAX_ATTEMPTS` | Failed-iteration budget per PBI (only `stuck`/`error` outcomes decrement). On exhaustion the PBI moves to `blocked/`. |
| `log_level` | string | `INFO` | `$RALPH_LOG_LEVEL` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `iteration_sleep_seconds` | float | `30.0` | `$RALPH_ITERATION_SLEEP_SECONDS` | Sleep between iterations when in `--watch` mode and the queue is idle. |
| `claude_binary` | string | `claude` | `$RALPH_CLAUDE_BINARY` | Name or absolute path of the `claude` CLI. |
| `claude_permission_mode` | string | `bypassPermissions` | `$RALPH_CLAUDE_PERMISSION_MODE` | Forwarded to `claude -p` via `--permission-mode`. Stops the spawned subprocess from inheriting the host's `~/.claude/settings.json` `defaultMode`. Allowed: `acceptEdits`, `auto`, `bypassPermissions`, `default`, `dontAsk`, `plan`. Default is `bypassPermissions` because the executor runs Claude non-interactively. |
| `git_host` | string | `""` | `$RALPH_GIT_HOST` | `github` or `ado`. Empty string means `host_select` reads `RALPH_GIT_HOST` directly and errors if also unset. Validation of allowed values happens in `host_select`. |
| `gh_owner` | string | `""` | `$GH_OWNER` | GitHub org/user that owns target repos. Bridged into the process env at startup so the `pr-github` skill picks it up. |
| `ado_org_url` | string | `""` | `$ADO_ORG_URL` | Azure DevOps organisation URL. |
| `ado_project` | string | `""` | `$ADO_PROJECT` | Azure DevOps project name. |
| `halt_webhook` | string | `""` | `$RALPH_HALT_WEBHOOK` | URL the safety halt POSTs to when the loop trips a safety rule. |
| `bot_author_email` | string | `""` | `$RALPH_ADO_AUTHOR_EMAIL` (historical name; sweep is host-agnostic) | Commit/PR author email the executor uses. Sweep ignores comments authored by this address so the loop doesn't feed back into itself. |
| `stale_days` | int (> 0) | `3` | `$RALPH_STALE_DAYS` | Staleness threshold for `pending-pr/`. PRs older than this move to `blocked/`. |
| `bash_max_timeout_ms` | int (> 0) | `900_000` (15 min) | `$BASH_MAX_TIMEOUT_MS` | Per-bash-tool ceiling propagated to the spawned `claude` subprocess via `BASH_MAX_TIMEOUT_MS`. Claude Code's own default is 600_000 (10 min). Subprocess-scoped — not exported to ralph's parent env. |
| `claude_session_timeout_seconds` | int (> 0) | `1200` (20 min) | `$RALPH_CLAUDE_SESSION_TIMEOUT_SECONDS` | Per-iteration wall-clock deadline for `claude -p`. On expiry the child is killed and a synthetic `error` outcome surfaces to the loop. |
| `pr_check_poll_max_attempts` | int | `6` | `$RALPH_PR_CHECK_POLL_MAX_ATTEMPTS` | CI-green verifier budget. `max_attempts × interval_seconds` is the wall budget per iteration; timeout rolls over to `partial`. |
| `pr_check_poll_interval_seconds` | float | `30.0` | `$RALPH_PR_CHECK_POLL_INTERVAL_SECONDS` | CI-green poll interval. |
| `use_worktrees` | bool | `true` | `$RALPH_USE_WORKTREES` | Must be `true`. The legacy single-checkout branch-dance path is gone; `load_config` rejects `false` with a migration error. |
| `auto_merge_clean_prs` | bool | `false` | `$RALPH_AUTO_MERGE_CLEAN_PRS` | When `true`, the sweep auto-merges PRs that GitHub reports as `mergeable_state == "clean"` (CI green + required approvals + no conflicts + branch up to date). Operators opt in. |
| `workspace_root` | string (path) | `$HOME/ralph-workspaces` | `$RALPH_WORKSPACE` | Where queue + target clones live. Same knob as in the user TOML; per-repo override wins. |
| `same_file_min_prs` | int (> 0) | `10` | `$RALPH_SAME_FILE_MIN_PRS` | Cycle-detector `same_file_thrashing` floor: distinct PBIs that must have touched the same file inside the rolling window before the rule trips. |
| `same_file_window_hours` | float (> 0) | `24.0` | `$RALPH_SAME_FILE_WINDOW_HOURS` | Rolling window for the same-file thrashing rule. |
| `watch_mode` | bool | `false` | `$RALPH_WATCH_MODE` | `false` → `run_loop` exits cleanly after `idle_exit_threshold` consecutive idle iterations (intended for pods / containers). `true` → legacy daemon mode (run forever, sleep on idle). |
| `idle_exit_threshold` | int (> 0) | `2` | `$RALPH_IDLE_EXIT_THRESHOLD` | Consecutive idle iterations before drain-on-idle exit. Raise to tolerate more transient false-idles. |

Precedence (lowest → highest): defaults < per-repo TOML < env < CLI
flag.

Two values are intentionally not readable from TOML:

- `repo_path` — chicken-and-egg: the repo path is needed to locate the
  TOML file itself.
- `anthropic_api_key` — secret; env-only by policy (`ANTHROPIC_API_KEY`,
  optional — empty string falls back to Claude Code's OAuth session).

## 7. CLI reference: `ralph-executor`

Source: `ralph_executor/cli.py`. The default command (no subcommand)
runs the iteration loop.

### Top-level flags

| Flag | Description |
|---|---|
| `--watch` | Daemon mode. Run forever, sleep `iteration_sleep_seconds` on idle. Without this flag the loop exits 0 after `idle_exit_threshold` consecutive idle iterations. Mutually exclusive with `--once` and `--iterations`. |
| `--once` | Run a single iteration and exit. Alias for `--iterations 1`. |
| `--iterations N` | Run exactly `N` iterations and exit. |
| `--repo PATH` | Explicit path to the repo Ralph operates on. Overrides `$RALPH_REPO_PATH` and cwd. Mutually exclusive with `--workspace`. |
| `--workspace NAME` | Resolve repo path against `$RALPH_HOME/NAME` (or `ralph_home` from `~/.ralph/config.toml`). `NAME` must be a plain directory name (no separators, no `.` or `..`, not absolute). |
| `--log-level LEVEL` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Overrides `$RALPH_LOG_LEVEL` for this run. |
| `--queue-repo URL` | Override `queue_repo` (per-run; URL validated). |
| `--queue-branch BRANCH` | Override `queue_branch` (per-run). Plain branch name only; empty, `HEAD`, or `refs/heads/...` raise `ConfigError`. |

`$RALPH_RUN_ONCE` (truthy: `1`, `true`, `yes`, `on`) is equivalent to
`--once` when `--iterations` is not supplied.

### Subcommands

#### `init`

Per-machine setup. Writes `~/.ralph/config.toml`.

| Flag | Description |
|---|---|
| `--ralph-home PATH` | Skip the prompt and set `ralph_home` to `PATH`. |
| `--yes` | Non-interactive. Accept the OS default for `ralph_home`; skip the `queue_repo` and `queue_branch` prompts (`queue_branch` falls back to `"ralph-queue"`; `queue_repo` must be added manually). |

#### `scaffold`

Per-repo setup. Creates a `ralph-queue` branch with the `.ralph/`
skeleton and a commented `config.toml` stub on a local checkout.
Commits locally; does not push.

| Flag | Description |
|---|---|
| `--repo PATH` | Explicit path to the repo to scaffold. Mutually exclusive with `--workspace`. |
| `--workspace NAME` | Resolve target against `$RALPH_HOME/NAME` (or `ralph_home` from `~/.ralph/config.toml`). |
| `--force` | Scaffold even if the `ralph-queue` branch already exists. |
| `--no-config-toml` | Skip writing the `.ralph/config.toml` stub. |

#### `migrate-queue`

One-shot bootstrap of a new queue repo from an existing `.ralph/`
tree.

| Flag | Description |
|---|---|
| `--source PATH` | Path to the existing queue worktree (parent of `.ralph/`). Required. |
| `--target URL` | HTTPS (or `file://`) URL of the empty new queue repo. Required. |

#### `health`

Liveness / readiness probe for orchestrators. Currently a stub —
both probes return 0.

| Flag | Description |
|---|---|
| `--ready` | Readiness probe (mutually exclusive with `--live`). |
| `--live` | Liveness probe (mutually exclusive with `--ready`). |

Exactly one of `--ready` / `--live` is required.

#### `doctor`

Environment diagnostics. Shells out to the `ralph-doctor` skill if
installed; emits a stub message otherwise.

| Flag | Description |
|---|---|
| `--json` | Emit diagnostics as JSON. |

#### `reconcile`

Reconcile orphan `pending-pr/` directories (those without `PR-LINK.md`)
by looking up the PR via the host API.

| Flag | Description |
|---|---|
| `--repo PATH` | Same as the top-level flag. |
| `--workspace NAME` | Same as the top-level flag. |
| `--dry-run` | Print the actions that would be taken without moving any files. |

## 8. CLI reference: operator skills

Skills live under `skills/ralph-*/scripts/`. All five accept a common
set of queue-targeting flags (`--workspace`, `--queue-repo`,
`--queue-branch`); the four mutating skills also accept `--no-push`
and `--dry-run`. Operator skills read `queue_branch` from
`~/.ralph/config.toml` only — they intentionally bypass
`$RALPH_QUEUE_BRANCH` and the per-repo TOML so the operator surface
stays on stable rails.

### `ralph-add`

`skills/ralph-add/scripts/add.py`. Host-agnostic orchestrator: fetches
a work item via the staged `workitem-fetch/` skill and submits it as a
PBI to the queue clone.

| Flag | Required | Description |
|---|---|---|
| `--work-item ID` | yes | Work item id (host-specific). |
| `--target-repo URL` | yes | HTTPS URL of the repo this PBI applies to. Written to `target_repo` frontmatter. Must be `https://<host>/<owner>/<name>`. |
| `--severity {critical,high,normal,low}` | no | Override severity returned by the fetcher. |
| `--expand-children` | no | Write one PBI per child instead of one for the parent. |
| `--via-mcp` | no | Reserved for v2. Raises `NotImplementedError` in v1. |
| `--no-push` | no | Commit but do not push. |
| `--dry-run` | no | Compute without mutating. |
| `--workspace PATH` | no | Override `workspace_root` from `~/.ralph/config.toml`. |
| `--queue-repo URL` | no | Override `queue_repo` from `~/.ralph/config.toml`. |
| `--queue-branch BRANCH` | no | Override `queue_branch` from `~/.ralph/config.toml`. |

### `ralph-cancel`

`skills/ralph-cancel/scripts/cancel.py`. Drops an empty `CANCEL`
sentinel into `.ralph/current/<pbi-id>/` so the executor moves the
PBI out on the next iteration.

| Flag | Required | Description |
|---|---|---|
| `--pbi-id ID` | yes | PBI directory name under `.ralph/current/`. |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |
| `--no-push` | no | Commit but do not push. |
| `--dry-run` | no | Compute without mutating. |

### `ralph-promote`

`skills/ralph-promote/scripts/promote.py`. Moves a PBI between state
folders; updates the `status` frontmatter; commits + pushes.

| Flag | Required | Description |
|---|---|---|
| `--pbi-id ID` | yes | PBI directory name. |
| `--from STATE` | yes | Source state folder: one of `inbox`, `current`, `pending-pr`, `blocked`, `archive`, `done`. |
| `--to STATE` | yes | Destination state folder (same choices). |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |
| `--no-push` | no | Commit but do not push. |
| `--dry-run` | no | Compute without mutating. |

### `ralph-triage`

`skills/ralph-triage/scripts/triage.py`. Routes a PBI in
`.ralph/blocked/` back to `inbox/` (attempts reset to 0) or to
`archive/`. A note is appended to `HISTORY.md`.

| Flag | Required | Description |
|---|---|---|
| `--pbi-id ID` | yes | PBI directory name under `.ralph/blocked/`. |
| `--to {inbox,archive}` | yes | Destination state folder. |
| `--note TEXT` | yes | Operator's reasoning; appended to `HISTORY.md`. |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |
| `--no-push` | no | Commit but do not push. |
| `--dry-run` | no | Compute without mutating. |

### `ralph-status`

`skills/ralph-status/scripts/status.py`. Read-only view of the queue,
grouped by `target_repo`.

| Flag | Required | Description |
|---|---|---|
| `--state STATE` | no | Filter rows to a single state: `inbox`, `current`, `pending-pr`, `blocked`, `archive`, `done`. |
| `--target-repo URL` | no | Filter rows to PBIs whose `target_repo` matches this URL. |
| `--json` | no | Emit JSON instead of a fixed-width table. |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |

## 9. CLI reference: `setup_ralph_queue_github.py`

`scripts/setup_ralph_queue_github.py`. One-shot provisioning of the
queue repo on GitHub. Reads `GH_TOKEN` and `GH_OWNER` from the
environment.

| Flag | Required | Description |
|---|---|---|
| `--repo NAME` | yes | Repo name without owner prefix. |
| `--branch NAME` | no (default `ralph-queue`) | Queue branch name. |
| `--base-branch NAME` | no (default `main`) | Base branch the queue branch is created off. |
| `--org NAME` | no | Create the repo under organisation `NAME` instead of the authenticated user. |
| `--dry-run` | no | Read state, log what would change, no mutations. |
| `--no-protection` | no | Skip the branch-protection PUTs (sandboxes / test repos). |

Full PAT-scope and troubleshooting details live in
[`ralph-queue-setup.md`](ralph-queue-setup.md).

## 10. Smoke test

After install + queue provision + `init`, walk through a single PBI
end-to-end.

1. Add an inbox PBI for some target repo you control:

   ```bash
   uv run python skills/ralph-add/scripts/add.py \
       --work-item WI-1 \
       --target-repo https://github.com/<owner>/<repo>
   ```

2. Confirm it lands in the queue:

   ```bash
   uv run python skills/ralph-status/scripts/status.py
   ```

   You should see `STATE = inbox` for `WI-1`.

3. Run the executor for one iteration:

   ```bash
   uv run ralph-executor --once
   ```

4. Re-run `ralph-status`. The PBI should be in `current/` (the run
   started) or `pending-pr/` (Claude opened a PR in one iteration).

5. If anything fails, run `ralph-executor doctor` and read the section
   below.

## 11. Troubleshooting

The deep-dive table for the queue-provisioning script is in
[`ralph-queue-setup.md`](ralph-queue-setup.md#troubleshooting).
Executor-specific symptoms:

| Symptom | Likely cause | Fix |
|---|---|---|
| `error: <toml-path>: queue_repo not configured.` | `~/.ralph/config.toml` is missing the `queue_repo` key and no `--queue-repo` was passed. | Run `ralph-executor init` (interactive) or add `queue_repo = "<url>"` to `~/.ralph/config.toml` by hand. |
| `error: <toml-path>: queue_branch must be a non-empty branch name` | TOML or `$RALPH_QUEUE_BRANCH` set to empty/whitespace. | Remove the override (falls back to `ralph-queue`) or set a real branch name. |
| `error: --queue-branch must be a plain branch name (got 'HEAD')` | CLI override is `HEAD` or starts with `refs/heads/`. | Use the plain name only (e.g. `--queue-branch ralph-queue`). |
| `error: --queue-repo: ...is not a valid HTTPS URL` | CLI override is not parseable as `https://<host>/<owner>/<name>`. | Fix the URL (no trailing `.git`, no path beyond owner/name). |
| `error: <toml-path>: use_worktrees=False is no longer supported.` | Legacy `use_worktrees = false` from before the queue-repo split. | Remove the line from TOML, unset `$RALPH_USE_WORKTREES`. |
| `error: <toml-path>: claude_permission_mode='X' not in [...]` | Typo on `claude_permission_mode`. | Use one of `acceptEdits`, `auto`, `bypassPermissions`, `default`, `dontAsk`, `plan`. |
| `error: --workspace name must be a plain directory name (no separators, no '.' or '..', not absolute)` | `--workspace foo/bar` or `--workspace ..`. | Use a single-segment name. |
| `error: --workspace needs a ralph_home root.` | Neither `$RALPH_HOME` nor `ralph_home` in `~/.ralph/config.toml`. | Run `ralph-executor init` or set `$RALPH_HOME`. |
| Executor exits with `host environment ready: host=...` then a `HostSelectionError`. | `git_host` set but auth env vars missing (e.g. `$GH_TOKEN`). | Export the required env vars; or set `git_host = "github"` and rerun `gh auth login`. |
| `FileNotFoundError: claude` from inside the loop. | Claude CLI not on PATH for the executor process. | Install Claude Code, run `claude --version` in the same shell, or set `claude_binary` to an absolute path. |
| Loop exits immediately with `queue drained -- exiting after N consecutive idle iterations` and the queue has work. | The queue clone is stale (operator-side push race) or the executor is reading a different `queue_branch` than the operator skills are writing to. | Compare `cfg.queue_branch` (`ralph-executor doctor`) with the skill's resolved branch (`grep RALPH_QUEUE_BRANCH ~/.ralph/config.toml`). |
