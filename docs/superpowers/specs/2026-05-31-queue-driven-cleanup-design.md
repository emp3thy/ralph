# Queue-driven cleanup design

**Date:** 2026-05-31
**Status:** Draft — design approved section-by-section in brainstorming; pending user review.

## Summary

Ralph is queue-driven: every Product Backlog Item (PBI) in the queue carries
its own `target_repo` URL, and the executor decides what to work on by
reading the queue. Today the executor also accepts a parallel set of CLI
flags, environment variables, and TOML keys whose purpose is to let an
operator manually pick a target repo for the run. That parallel surface
predates the queue-repo split and now contradicts the queue-driven model.

This design removes every manual-target-picking surface, makes
`workspace_root` the single root the operator configures, and routes all
per-iteration target resolution through the PBI's `target_repo` frontmatter
field.

## Goals

1. The queue's PBI `target_repo` is the only input that decides which
   repo the executor works on. The operator cannot override it.
2. The operator runs `uv run ralph-executor` with no target flags. The
   executor drains every PBI across every distinct `target_repo` URL in
   one run.
3. `<workspace_root>` is the only path the operator configures. The
   executor materialises every clone it touches under
   `<workspace_root>/queue/` and `<workspace_root>/clones/<owner>/<name>/`.
4. The target repo never knows it is being worked on by ralph. No config
   file inside the target tree is loaded.

## Non-goals

- Changing how the queue repo is structured (state folders, PBI shape,
  branch model). Unchanged.
- Changing the per-PBI worktree convention
  (`<clone>/.ralph-work/<PBI-id>/`). Unchanged.
- Changing the auto-clone mechanism in
  `ralph_executor/target_clone.py`. Unchanged.
- Auto-discovering target repos that have no PBI. The queue remains
  authoritative.

## Single source of truth

The queue PBI's `target_repo` frontmatter field is the only input that
decides which repo the executor works on for a given iteration. No
operator-supplied flag, env var, TOML key, or cwd-derived path can
override or supplement it. The process-level concept of "the repo for
this run" is removed: there is no `ExecutorConfig.repo_path` field.

## Operator-visible surface (after refactor)

### CLI subcommands

| Subcommand | Purpose | Flags |
|---|---|---|
| `ralph-executor` (default) | drain queue | `--watch`, `--once`, `--log-level`, `--max-iterations` |
| `ralph-executor init` | first-run setup | (no flags; interactive prompts for `queue_repo`, `queue_branch`, `workspace_root`) |
| `ralph-executor reconcile` | sweep orphaned PRs | `--dry-run` only |
| `ralph-executor health` | unchanged | unchanged |
| `ralph-executor doctor` | unchanged | unchanged |
| `ralph-executor migrate-queue` | unchanged | unchanged |

### Deleted CLI surface

- `ralph-executor scaffold` subcommand — entirely. Predated the queue-repo split.
- `--repo PATH` — every subcommand.
- `--workspace NAME` — every subcommand.
- `--ralph-home PATH` — on `init`.

### Deleted env vars

- `$RALPH_HOME`
- `$RALPH_REPO_PATH`

(Existing `$RALPH_WORKSPACE`, `$RALPH_QUEUE_REPO`, `$RALPH_QUEUE_BRANCH`,
`$RALPH_WATCH_MODE`, `$RALPH_IDLE_EXIT_THRESHOLD`, `$RALPH_CLAUDE_PERMISSION_MODE`,
`$RALPH_SKILLS_ROOT`, `$RALPH_CLAUDE_SKILLS_DIR` and other unrelated env
vars are unchanged.)

### User TOML (`~/.ralph/config.toml`) after refactor

```toml
workspace_root = "..."
queue_repo     = "..."
queue_branch   = "..."

# optional, host-only:
skills_root        = "..."
claude_skills_dir  = "..."

# optional, process-level knobs that previously lived in project TOML:
watch_mode               = false
idle_exit_threshold      = 2
claude_permission_mode   = "bypassPermissions"
halt_webhook             = "https://..."
```

The `ralph_home` key is removed. Stale `ralph_home` in an existing file is
ignored and surfaces a one-time deprecation warning at startup.

### Project TOML

The file `<target>/.ralph/config.toml` is no longer loaded. If detected
inside a freshly cloned target, the executor logs a per-iteration WARNING
naming the path: `project TOML at <path> is not supported -- ignored.
Move settings to ~/.ralph/config.toml.`

## Runtime data flow

```
operator: `uv run ralph-executor`
        |
        v
load config from ~/.ralph/config.toml + RALPH_* env
        |
        v
ensure_queue_clone(workspace_root, queue_repo, queue_branch)
  -> <workspace_root>/queue/  (pull on existing)
        |
        v
loop (until idle_exit_threshold idle iterations OR --once):
  1. read .ralph/inbox/ and .ralph/current/ from queue clone
  2. pick next PBI
  3. read PBI's target_repo URL from frontmatter
  4. ensure_clone(target_repo, workspace_root)
       -> <workspace_root>/clones/<owner>/<name>/
  5. detect <clone>/.ralph/config.toml -> if present, WARN and ignore
  6. create per-PBI worktree at <clone>/.ralph-work/<PBI-id>/
  7. run claude inside the worktree
  8. push, open PR, update queue
  9. tear down worktree
```

### Invariants

- Active target path (`target_clone_root`, `worktree_path`) is derived
  per-iteration from the PBI's `target_repo` URL.
- `ExecutorConfig` no longer carries a `repo_path` field.
- `loop.py`, `claude_spawn.py`, and reconcile / sweep code receive the
  active target path as an argument from the per-iteration scope, never
  from `cfg`.
- The operator does not pre-clone target repos. The executor materialises
  every target it touches.

### Multi-target runs

A queue with PBIs for multiple distinct `target_repo` URLs is processed
end-to-end in one executor run. The loop walks PBIs in queue order and
clones / re-uses the appropriate target per iteration. There is no
operator-supplied filter to limit work to one target.

## Error handling

### Config-load errors (startup, fatal — exit 2)

- `~/.ralph/config.toml` missing → `run \`ralph-executor init\``.
- `workspace_root` missing or unreadable → error names the key.
- `queue_repo` missing → error names the key.
- `queue_repo` clone fails → existing behaviour (retry next iteration in
  watch mode; exit in drain mode).

### Per-iteration errors (recoverable, log + move on)

- PBI frontmatter missing `target_repo` → log ERROR, move PBI to
  `blocked/` with reason, continue.
- `target_repo` URL malformed → same as missing.
- `ensure_clone` fails (network, auth) → log ERROR, leave PBI in
  `current/`, exit iteration; retries next iteration.

### Warnings (non-fatal)

- Stale `ralph_home` key in user TOML → WARNING once at startup.
- Project TOML inside target clone → WARNING per-iteration when that
  target is touched.
- Project TOML detection failure (e.g. permission denied reading
  `.ralph/`) → DEBUG, suppress.

### init failures

- User interrupts during prompts → exit 1, no partial TOML written.
- Disk full / permission denied writing TOML → ConfigError, exit 2.

### No silent fallbacks

Missing config keys cause refusal to start. No cwd fallback. No env-var
fallback substituting for absent user-TOML keys (other than the existing
documented `RALPH_*` overrides).

## Testing

### New tests

- Config load with valid user TOML only (no project TOML) → succeeds.
- Config load with stale `ralph_home` key → succeeds + WARNING logged once.
- Config load missing `workspace_root` or `queue_repo` → exits 2; error
  names the missing key.
- Iteration where target clone contains `.ralph/config.toml` → WARNING
  logged, file not loaded.
- Iteration with PBI missing `target_repo` → PBI routed to `blocked/`,
  ERROR logged.
- Iteration with malformed `target_repo` URL → same.
- Multi-target queue: 3 PBIs across 2 distinct `target_repo` URLs →
  both targets cloned, all 3 PBIs processed in one run.

### Deleted tests

In `tests/executor/test_cli.py`:

- `test_main_workspace_resolves_to_ralph_home`
- `test_main_workspace_without_ralph_home_errors`
- `test_main_workspace_rejects_absolute_name`
- `test_main_workspace_rejects_parent_traversal`
- `test_main_workspace_rejects_dot`
- `test_main_workspace_reads_ralph_home_from_user_config`
- `test_main_workspace_errors_when_no_ralph_home_anywhere`
- `test_main_workspace_non_git_dir_errors`
- All `--repo`-specific tests across subcommands.
- Any test asserting cwd-fallback behaviour.
- `init` tests that assert `ralph_home` prompt or `--ralph-home` flag.
- Scaffold subcommand tests (whole subcommand goes).

### Updated tests

- Any test that constructs `ExecutorConfig(repo_path=...)` directly →
  drop the field.
- `loop.py` tests passing `cfg.repo_path` → switch to per-iteration
  `target_path` fixture.

### Smoke test (manual)

Fresh queue with 2 PBIs naming different target repos →
`uv run ralph-executor` → both targets get cloned under
`<workspace_root>/clones/`, both PBIs land in `pending-pr/` or `done/`,
exit 0.

## Documentation

### README.md

- "Prerequisites" — unchanged.
- "Install" — unchanged.
- "One-time setup" — remove `ralph_home` row from the config-keys table;
  remove `--ralph-home` flag mention; remove manual-TOML example's
  `ralph_home` line.
- "Per-repo setup" — **DELETED entirely**. No manual cloning step exists.
- "Working the queue" — unchanged.
- "Running ralph" — collapses to:
  ```bash
  uv run ralph-executor             # drain queue
  uv run ralph-executor --watch     # daemon
  uv run ralph-executor --once      # single iteration
  ```
  Drop `--repo`, `--workspace`, multi-terminal multi-ralph guidance.
- "Running multiple ralphs" — **DELETED**. One executor drains the
  whole queue across all targets.
- "Configuration precedence" — collapses to:
  CLI flags > `RALPH_*` env > `~/.ralph/config.toml`. Drop project TOML
  row. Drop `$RALPH_HOME` line.
- "Claude subprocess permission mode" — knob moves from project TOML to
  user TOML + env.

### Runbook docs

- `docs/runbooks/ralph-setup.md` — remove every `ralph_home`,
  `--workspace`, `--repo`, and project-TOML reference; rewrite the
  "running" section.
- `docs/runbooks/ralph-architecture.md` — update "three pieces" section:
  executor source / queue repo / `workspace_root` with `clones/`
  underneath. No per-repo checkout convention.
- `docs/runbooks/ralph-queue-setup.md` — verify; likely already
  queue-aligned.

### Startup script (`scripts/start-ralph.ps1`)

- Drop `-Workspace` parameter.
- Drop `--workspace` from the executor invocation.
- README's mention of the script becomes:
  ```powershell
  .\scripts\start-ralph.ps1            # drain
  .\scripts\start-ralph.ps1 -Watch     # daemon
  .\scripts\start-ralph.ps1 -Once      # single iter
  ```

## Future work (out of scope for this design)

Several env vars and TOML keys survive this refactor but are technically
redundant under a strict queue-driven model because they are derivable
from the PBI's `target_repo` URL:

- `$RALPH_GIT_HOST` / `git_host` — derivable from hostname (`github.com`
  → `github`, `dev.azure.com` → `ado`).
- `$GH_OWNER` / `gh_owner` — derivable from URL path.
- `$ADO_ORG_URL`, `$ADO_PROJECT` / `ado_org_url`, `ado_project` —
  derivable from URL path.

A future spec should decide whether these become derived-only or remain
as explicit overrides (e.g. for fork workflows). Out of scope for this
PR because eliminating them touches host-selection plumbing
(`host_select`, PR-skill staging) that is otherwise stable.

## Migration

Single user, hard-remove. After upgrade:

- Stale `ralph_home` line in `~/.ralph/config.toml` triggers a one-time
  WARNING. Operator deletes the line.
- Old `$RALPH_HOME/<name>/` checkouts are orphaned. Operator deletes
  them by hand. The executor recreates everything it needs under
  `<workspace_root>/clones/<owner>/<name>/`.
- Any `.ralph/config.toml` inside an active target clone triggers a
  per-iteration WARNING when that target is touched. Operator moves
  settings to user TOML and deletes the file.
