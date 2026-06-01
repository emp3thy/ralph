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

Interactively prompts for `workspace_root`, `queue_repo`,
`queue_branch` (default `ralph-queue`), and `instance_id` (default:
sanitised hostname). Writes them to `~/.ralph/config.toml`. The queue
URL is smoke-tested with `git ls-remote`; failure prints a warning but
still writes the config. `init` is idempotent — a re-run keeps existing
TOML keys and only prompts for the missing ones.

Non-interactive form:

```bash
uv run ralph-executor init --yes
```

`--yes` accepts the OS default for `workspace_root`
(`~/ralph-workspaces`), skips the `queue_repo` prompt with a warning,
writes the default `queue_branch` (`"ralph-queue"`), and writes the
default `instance_id` (sanitised hostname). `queue_repo` must be added
manually to `~/.ralph/config.toml` afterwards.

## 4a. Configure git host

The executor needs `git_host` plus host-specific auth before it will
start. Two mandatory pieces:

1. Set `git_host` (and on GitHub, `gh_owner`) in
   `~/.ralph/config.toml` — the same file `ralph-executor init` writes.
   Per-machine:

   ```toml
   # ~/.ralph/config.toml
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
| `workspace_root` | string (path) | no | `$HOME/ralph-workspaces` | `$RALPH_WORKSPACE` | Where the queue clone and target-repo clones live. Each target gets `<workspace_root>/clones/<owner>/<name>/`; the queue clone is `<workspace_root>/queue-<instance_id>/` (one per instance — see section 12). |
| `skills_root` | string (path) | no | source-checkout default | `$RALPH_SKILLS_ROOT` | Source `skills/` tree used by `host_select.prepare_host_environment` to find `pr-<host>/`. |
| `claude_skills_dir` | string (path) | no | `~/.claude/skills` | `$RALPH_CLAUDE_SKILLS_DIR` | Destination directory where staged `pr/` ends up for the spawned Claude subprocess. |
| `queue_repo` | string (URL) | yes (or `--queue-repo`) | — | none | HTTPS URL of the queue repo. The executor clones it into `<workspace_root>/queue-<instance_id>/`. |
| `queue_branch` | string | no | `ralph-queue` | `$RALPH_QUEUE_BRANCH` (executor only — skills do not read this env) | Branch on `queue_repo` that holds `.ralph/` state. |
| `instance_id` | string | no | sanitised hostname | `$RALPH_INSTANCE_ID` | Per-instance identity for multi-ralph (Scope 1). Drives the namespaced queue clone path (`<workspace_root>/queue-<instance_id>/`) and the `CLAIM.json` ownership marker on each claimed PBI. Must match `^[a-z0-9][a-z0-9_-]{0,62}$` (filesystem-safe lowercase, 1–63 chars, starts with alnum). Resolution: `--instance-id` CLI flag > `$RALPH_INSTANCE_ID` env > TOML key > sanitised hostname. See section 12. |

## 6. Config reference: operational knobs in `~/.ralph/config.toml`

All values live in the same `~/.ralph/config.toml` as section 5; the
split between "per-machine essentials" and these "operational knobs"
is editorial. Source: the `_TOML_KNOWN_KEYS` set and the `DEFAULT_*`
constants in `ralph_executor/config.py`. Unknown top-level keys are
logged at WARNING and ignored — forward-compat is cheap.

| Key | Type | Default | Env override | Description |
|---|---|---|---|---|
| `queue_repo` | string (URL) | — (required) | none | HTTPS URL of the queue repo. Required; loop crashes without it. Listed here for completeness — section 5 covers the same key. |
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
| `same_file_min_prs` | int (> 0) | `10` | `$RALPH_SAME_FILE_MIN_PRS` | Cycle-detector `same_file_thrashing` floor: distinct PBIs that must have touched the same file inside the rolling window before the rule trips. |
| `same_file_window_hours` | float (> 0) | `24.0` | `$RALPH_SAME_FILE_WINDOW_HOURS` | Rolling window for the same-file thrashing rule. |
| `watch_mode` | bool | `false` | `$RALPH_WATCH_MODE` | `false` → `run_loop` exits cleanly after `idle_exit_threshold` consecutive idle iterations (intended for pods / containers). `true` → legacy daemon mode (run forever, sleep on idle). |
| `idle_exit_threshold` | int (> 0) | `2` | `$RALPH_IDLE_EXIT_THRESHOLD` | Consecutive idle iterations before drain-on-idle exit. Raise to tolerate more transient false-idles. |

Precedence (lowest → highest): defaults < `~/.ralph/config.toml` <
env < CLI flag.

One value is intentionally not readable from TOML:

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
| `--log-level LEVEL` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Overrides `$RALPH_LOG_LEVEL` for this run. |
| `--queue-repo URL` | Override `queue_repo` (per-run; URL validated). |
| `--queue-branch BRANCH` | Override `queue_branch` (per-run). Plain branch name only; empty, `HEAD`, or `refs/heads/...` raise `ConfigError`. |
| `--instance-id NAME` | Override `instance_id` (per-run). Top-level flag only — not declared on subparsers; all subcommands inherit. Validated against `^[a-z0-9][a-z0-9_-]{0,62}$`; empty string reaches the validator and raises `ConfigError` (it does NOT silently fall through to env / TOML / hostname). See section 12. |

`$RALPH_RUN_ONCE` (truthy: `1`, `true`, `yes`, `on`) is equivalent to
`--once` when `--iterations` is not supplied.

### Subcommands

#### `init`

Per-machine setup. Writes `~/.ralph/config.toml`.

| Flag | Description |
|---|---|
| `--yes` | Non-interactive. Accept the OS default for `workspace_root`; skip the `queue_repo` and `queue_branch` prompts (`queue_branch` falls back to `"ralph-queue"`; `queue_repo` must be added manually). |

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
| `--dry-run` | Print the actions that would be taken without moving any files. |

## 8. CLI reference: operator skills

Skills live under `skills/ralph-*/scripts/`. All five accept a common
set of queue-targeting flags (`--queue-repo`, `--queue-branch`); the
four mutating skills also accept `--no-push` and `--dry-run`. Operator
skills read `queue_branch` from `~/.ralph/config.toml` only — they
intentionally bypass `$RALPH_QUEUE_BRANCH` so the operator surface
stays on stable rails.

### `ralph-new`

`skills/ralph-new/scripts/new.py`. The single submission surface for
PBIs — supersedes `ralph-add`. Authors a well-formed PBI directly into
the queue with no GitHub-issue round-trip.

| Flag | Required | Description |
|---|---|---|
| `--title TEXT` | yes (prompt or flag) | PBI title; slugified to PBI id. |
| `--type {bug,feature}` | yes | PBI type. |
| `--severity {critical,high,normal,low}` | no (default `normal`) | Triage lane. |
| `--target-repo URL` | yes | HTTPS owner/name URL of the target service repo. |
| `--depends-on ID` | no (repeatable) | Validated for syntax AND existence in the queue. |
| `--parent-id ID` | no | Epic link. |
| `--id SLUG` | no | Override auto-generated slug. |
| `--spec-path PATH` | no | Feature: docs/superpowers/specs path. |
| `--plan-path PATH` | no | Feature: docs/superpowers/plans path; blank renders TODO stub. |
| `--body-file PATH` | no | Read entry-file body from file; skips per-section prompts. |
| `--reproduce-file PATH` | no | Bug: read REPRODUCE.md body from file. |
| `--non-interactive` | no | Refuse prompts; missing required field exits 2. |
| `--no-push` | no | Commit but do not push. |
| `--dry-run` | no | Print envelope; no clone / write / commit. |
| `--check-depends-on` | no | Under `--dry-run`, force clone for existence check. |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |

The full design lives at
[`docs/superpowers/specs/2026-05-30-ralph-new-design.md`](../superpowers/specs/2026-05-30-ralph-new-design.md).

### `ralph-cancel`

`skills/ralph-cancel/scripts/cancel.py`. Drops an empty `CANCEL`
sentinel into `.ralph/current/<pbi-id>/` so the executor moves the
PBI out on the next iteration. Refuses with exit `3` when the PBI's
`CLAIM.json` names a different `instance_id` than the operator (the
operator is then directed at `ralph-recover`) and when `CLAIM.json` is
missing or malformed.

| Flag | Required | Description |
|---|---|---|
| `--pbi-id ID` | yes | PBI directory name under `.ralph/current/`. |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |
| `--instance-id NAME` | no | Operator identity for the CLAIM.json ownership guard. Resolution: `--instance-id` flag, `RALPH_INSTANCE_ID` env, `instance_id` in `~/.ralph/config.toml`, sanitised hostname. |
| `--no-push` | no | Commit but do not push. |
| `--dry-run` | no | Compute without mutating; the ownership guard is skipped (no clone happens). |

### `ralph-promote`

`skills/ralph-promote/scripts/promote.py`. Moves a PBI between state
folders; updates the `status` frontmatter; commits + pushes. When the
source state is `current/`, refuses with exit `3` if the PBI's
`CLAIM.json` names a different `instance_id` than the operator, or if
`CLAIM.json` is missing or malformed. Moves out of any other state
folder bypass the guard (those folders have no `CLAIM.json` by
design).

| Flag | Required | Description |
|---|---|---|
| `--pbi-id ID` | yes | PBI directory name. |
| `--from STATE` | yes | Source state folder: one of `inbox`, `current`, `pending-pr`, `blocked`, `archive`, `done`. |
| `--to STATE` | yes | Destination state folder (same choices). |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |
| `--instance-id NAME` | no | Operator identity for the CLAIM.json ownership guard. Only consulted when `--from current` is the source state. Resolution mirrors `ralph-cancel`. |
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
grouped by `target_repo`. Renders an `OWNER` column derived from each
PBI's `CLAIM.json::instance_id`; cells show `—` (em-dash) for any row
that is not in `current/`, has no `CLAIM.json`, or whose `CLAIM.json`
is malformed. The JSON envelope carries the same value as `owner`
(snake-case; `null` for the empty cases).

| Flag | Required | Description |
|---|---|---|
| `--state STATE` | no | Filter rows to a single state: `inbox`, `current`, `pending-pr`, `blocked`, `archive`, `done`. |
| `--target-repo URL` | no | Filter rows to PBIs whose `target_repo` matches this URL. |
| `--json` | no | Emit JSON instead of a fixed-width table. |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |

### `ralph-recover`

`skills/ralph-recover/scripts/recover.py`. Manually recovers an orphan
claim by moving a PBI out of `current/<id>/` back to `inbox/<id>/` (re-
dispatch — attempt counter reset) or to `blocked/<id>/` (human triage
— attempt counter preserved). Deletes the orphan `CLAIM.json`, appends
a `HISTORY.md` audit entry naming the previous owner, and pushes one
commit pinned to `chore(queue): recover <id> from <previous-instance>`.
Refuses with exit `4` when the halt sentinel is active. No
`--instance-id` flag — the prior owner read from `CLAIM.json` is what
drives the commit subject, the operator's identity is not needed. No
`--force` flag either; invocation IS the deliberate action.

| Flag | Required | Description |
|---|---|---|
| `--pbi-id ID` | yes | PBI directory name under `.ralph/current/`. |
| `--to {inbox,blocked}` | yes | Destination state folder. `inbox` resets the attempt counter; `blocked` preserves it. |
| `--workspace PATH` | no | Override `workspace_root`. |
| `--queue-repo URL` | no | Override `queue_repo`. |
| `--queue-branch BRANCH` | no | Override `queue_branch`. |
| `--no-push` | no | Commit but do not push. |

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
   uv run python skills/ralph-new/scripts/new.py \
       --non-interactive \
       --title "Smoke test PBI" \
       --type bug \
       --target-repo https://github.com/<owner>/<repo> \
       --body-file /tmp/smoke-body.md \
       --reproduce-file /tmp/smoke-repro.md
   ```

2. Confirm it lands in the queue:

   ```bash
   uv run python skills/ralph-status/scripts/status.py
   ```

   You should see `STATE = inbox` for `SMOKE-TEST-PBI`.

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
| Executor exits with `host environment ready: host=...` then a `HostSelectionError`. | `git_host` set but auth env vars missing (e.g. `$GH_TOKEN`). | Export the required env vars; or set `git_host = "github"` and rerun `gh auth login`. |
| `FileNotFoundError: claude` from inside the loop. | Claude CLI not on PATH for the executor process. | Install Claude Code, run `claude --version` in the same shell, or set `claude_binary` to an absolute path. |
| Loop exits immediately with `queue drained -- exiting after N consecutive idle iterations` and the queue has work. | The queue clone is stale (operator-side push race) or the executor is reading a different `queue_branch` than the operator skills are writing to. | Compare `cfg.queue_branch` (`ralph-executor doctor`) with the skill's resolved branch (`grep RALPH_QUEUE_BRANCH ~/.ralph/config.toml`). |
| `ConfigError: instance_id '<value>' must match ^[a-z0-9][a-z0-9_-]{0,62}$` | TOML key, env var, CLI flag, or sanitised hostname does not match the regex (uppercase, dots, spaces, leading hyphen, > 63 chars). | Pick a value satisfying the regex (e.g. `ralph-a`). If the default-from-hostname is what is failing, set `instance_id` explicitly in `~/.ralph/config.toml`. |
| `QueueCloneError: both legacy queue/ and queue-<instance_id>/ exist` | An aborted upgrade left both the legacy single-host clone and the namespaced one in `workspace_root/`. | Remove whichever is stale (typically the legacy `queue/`) and restart the executor. The legacy directory is only ever renamed once on the first startup of the multi-ralph build. |
| `LockfileError: another ralph already running on this workspace: {...}` | Another ralph process is already holding `<workspace>/queue-<instance_id>/.ralph.lock`. The error payload names the holding instance, hostname, and pid. | Stop the other ralph, or give this instance its own `workspace_root` AND its own `instance_id`. The OS releases the lock on process exit, so no manual cleanup is needed once the holder is dead. |
| `QueueError: malformed claim: ...` from `current_pbi()`. | A `current/<id>/` directory either has no `CLAIM.json` or its `CLAIM.json` will not parse. | Inspect the PBI directory directly. If the claim is legitimately orphaned (owner crashed), use `ralph-recover --pbi-id <id> --to inbox`. |
| `ralph-cancel: cannot cancel PBI claimed by '<other>'; use ralph-recover` / `ralph-promote: cannot promote PBI claimed by '<other>'; use ralph-recover` (exit 3). | The PBI's `CLAIM.json` names a different instance than the operator. | Confirm the owning instance is actually gone, then run `ralph-recover --pbi-id <id> --to inbox|blocked`. `ralph-cancel` and `ralph-promote` are intentionally non-destructive across instances. |
| `ralph-recover: halt sentinel active` (exit 4). | The executor's halt sentinel at `.ralph/state/halted` is present and unacknowledged. | Acknowledge (or delete) the halt sentinel before recovering claims. The halt is there because a safety net tripped; mutating the queue during the halt can mask the unresolved root cause. |

## 12. Running multiple ralphs

Multi-ralph (Scope 1) lets multiple ralph instances drain the same
upstream queue concurrently across separate hosts. Each instance
carries a unique `instance_id`, clones the queue into its own
namespaced workspace, and writes a `CLAIM.json` ownership marker
alongside every PBI it claims. The other instances see foreign claims
and skip them.

Source: `ralph_executor/config.py` (resolution + validator),
`ralph_executor/queue_clone.py` (namespaced path + legacy rename),
`ralph_executor/queue/claim.py` (`CLAIM.json` IO),
`ralph_executor/lockfile.py` (workspace lockfile).

### `instance_id` resolution chain

Highest precedence wins:

1. `--instance-id NAME` CLI flag (top-level parser only; all
   subcommands inherit).
2. `RALPH_INSTANCE_ID` environment variable.
3. `instance_id` key in `~/.ralph/config.toml`.
4. Sanitised hostname (`socket.gethostname()` lowercased, with every
   character outside `[a-z0-9_-]` replaced by `-`).

The resolved value is validated against
`^[a-z0-9][a-z0-9_-]{0,62}$` — filesystem-safe lowercase, 1–63
characters, must start with an alphanumeric. Empty strings reach the
validator (they do NOT silently fall through), so `--instance-id ""`
is an error, not a "use the next source" signal.

### Workspace layout

Each instance's queue clone lives at
`<workspace_root>/queue-<instance_id>/`. Two instances on the same host
must use different `workspace_root` values (or different `instance_id`s
on the same workspace, but the lockfile refuses concurrent acquires on
the same path regardless). Lockfile path is
`<workspace>/queue-<instance_id>/.ralph.lock` (POSIX `fcntl.flock`,
Windows `msvcrt.locking`).

### `CLAIM.json` schema

Each `current/<id>/CLAIM.json` is a JSON object with three required
string fields:

```json
{
  "claimed_at": "2026-06-01T12:34:56.789012+00:00",
  "hostname": "host-a",
  "instance_id": "ralph-a"
}
```

The claim is written into the same commit as the inbox→current
rename and the `status:` frontmatter flip, pinned to subject
`chore(queue): claim <pbi-id> for <instance_id>`. Rebase-race losers
drop their local claim commit cleanly and re-enter the next
iteration.

### Operator-skill behaviour under multi-ralph

- `ralph-status` shows the `OWNER` column.
- `ralph-cancel` and `ralph-promote` refuse on foreign `CLAIM.json`
  with exit `3` and direct the operator at `ralph-recover`.
- `ralph-recover` is the only sanctioned way to take over an orphan
  claim. It deletes `CLAIM.json`, appends a `HISTORY.md` audit line,
  and pushes one commit pinned to
  `chore(queue): recover <id> from <previous-instance>`. It refuses to
  run when the halt sentinel is active (exit `4`).
- The META-BUG file emitted on safety-halt carries a
  `tripped_by_instance: <instance_id>` frontmatter line so operators
  can trace which instance tripped the cycle detector.

### Upgrade procedure

The first iteration of the new executor binary atomically renames the
legacy `<workspace_root>/queue/` clone to
`<workspace_root>/queue-<instance_id>/`. If both paths already exist
the executor raises `QueueCloneError` and refuses to start —
intentional refusal, not a silent merge. Recommended steps:

1. Drain `current/` on the running ralph (let it finish in-flight
   PBIs or `ralph-cancel` them). Existing `current/` PBIs have no
   `CLAIM.json` and will trip the malformed-claim guard on the next
   iteration of the new binary.
2. Stop the executor cleanly.
3. Upgrade (`uv sync` or pull the new container image).
4. Set `instance_id` in `~/.ralph/config.toml` if you want anything
   other than the sanitised hostname.
5. Start the new executor. The legacy `queue/` is renamed to
   `queue-<instance_id>/` on the first iteration.

### Cross-host halt is not in Scope 1

The halt sentinel (`.ralph/state/halted`) is gitignored on the queue
clone; it is per-workspace, not per-fleet. An instance halting on
host A does not propagate to host B. Fleet-wide coordination is
Scope 2 (deferred). For now, an operator who wants the whole fleet
paused must stop each instance manually.
