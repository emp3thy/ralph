---
name: ralph-status
description: Read-only view of Ralph's queue across one or more service repos. Enumerates the .ralph/{inbox,current,pending-pr,done,blocked} folders on each repo's ralph-queue branch, parses each PBI's frontmatter, and renders a terminal-friendly table to stdout. Supports filtering by state and JSON output for downstream automation.

---

# ralph-status

## What this skill does

The `ralph-status` skill is the BA / PM / triager workboard for Ralph. It
reads (never writes) the queue state from one or more service repos and
renders it as a terminal-friendly table. The data sources are the
`.ralph/` directories on each repo's `ralph-queue` branch — exactly the
same trees `ralph-add` writes to.

For each configured repo, the skill:

1. Creates a short-lived `git worktree` on the `ralph-queue` branch (or
   the branch named via `--branch`). The worktree lives in a temp
   directory and is torn down on exit so the user's working tree is
   untouched.
2. Walks the five state folders: `inbox/`, `current/`, `pending-pr/`,
   `done/`, `blocked/`.
3. For each PBI directory, reads its entry file (`PBI.md`, `BUG.md`,
   `FEEDBACK.md`) and parses the YAML frontmatter against the canonical
   schema defined in Plan 1.
4. Renders one row per PBI to stdout, optionally filtered by `--state`
   and optionally serialised as JSON via `--json`.

## When to use it

Use `ralph-status` to answer "what is Ralph doing right now?" or "what
is queued on service X?". It replaces the kanban UI a v2 web supervisor
would expose.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--repo <path>` | one of `--repo` / `--repos-file` | Path to a checkout (or any working tree) of a service repo. The skill creates a worktree on `ralph-queue` from this repo. |
| `--repos-file <path>` | one of `--repo` / `--repos-file` | Path to a config file listing multiple repos (one repo path per non-blank line; lines starting with `#` are ignored). |
| `--state <state>` | no | Filter rows to a single state. Allowed values: `inbox`, `current`, `pending-pr`, `done`, `blocked`. Default: all five. |
| `--branch <name>` | no | Queue branch name. Default: `ralph-queue` (also picked up from `RALPH_QUEUE_BRANCH` if set). |
| `--json` | no | Emit JSON to stdout instead of a fixed-width table. |
| `--no-cleanup` | no | Keep the temporary worktree(s) on disk after the command exits. Useful for debugging the data the skill saw. |

## Environment variables

| Variable | Purpose |
|---|---|
| `RALPH_QUEUE_BRANCH` | Default queue branch name; overridden by `--branch`. Optional. |

## Output

### Table mode (default)

```
REPO                STATE         ID            TYPE        SEVERITY  AGE      TITLE
service-auth        inbox         WI-1234       feature     normal    2h       Add /healthz endpoint
service-auth        current       WI-1235       bug         critical  1h       Pod crashloops on ROSA
service-billing     pending-pr    WI-980        feature     high      6h       Migrate invoices to v2
```

Columns are width-aligned to the widest value in the rendered set. The
`AGE` column shows the time since `created_at` (`m` minutes, `h` hours,
`d` days; `?` if `created_at` is missing or unparseable).

Malformed PBIs (missing entry file, invalid YAML, missing required
fields) render as a `?` row with the directory name in the ID column
and the parse error in the TITLE column — they never abort the command.

### JSON mode (`--json`)

```json
{
  "rows": [
    {
      "repo": "/path/to/service-auth",
      "repo_name": "service-auth",
      "state": "inbox",
      "id": "WI-1234",
      "type": "feature",
      "severity": "normal",
      "created_at": "2026-05-24T09:15:00+00:00",
      "updated_at": "2026-05-24T09:15:00+00:00",
      "attempts": 0,
      "title": "Add /healthz endpoint",
      "pbi_dir": ".ralph/inbox/WI-1234",
      "error": null
    }
  ],
  "errors": [],
  "repos": [
    {"path": "/path/to/service-auth", "name": "service-auth", "branch": "ralph-queue"}
  ]
}
```

Malformed PBIs appear in `rows` with `error` set to a string describing
the parse failure. Repos that could not be inspected at all (path
missing, branch missing) appear in the top-level `errors` array and
cause exit code 2 — they are not soft-failed like individual PBIs.

## How it is invoked

```bash
uv run python skills/ralph-status/scripts/status.py --repo /path/to/service-auth
uv run python skills/ralph-status/scripts/status.py --repos-file ~/.config/ralph/repos
uv run python skills/ralph-status/scripts/status.py --repo /path/to/svc --state current
uv run python skills/ralph-status/scripts/status.py --repo /path/to/svc --json
```

Tests live at `tests/skills/test_ralph_status.py` and use local bare +
worktree git fixtures (the same pattern as the `ralph-add` skill's
tests) — no network, no real ADO, no real remote.

## What this skill does NOT do

- It does not mutate `ralph-queue`, the service repo's working tree,
  or the user's current branch. The temporary worktree is removed on
  exit unless `--no-cleanup` is set.
- It does not call ADO or any other remote API. It only reads git.
- It does not invoke `claude -p` or otherwise touch the executor.
- It does not aggregate PR status (CI green / red, reviewer decisions).
  PR state lives elsewhere; the queue folder a PBI sits in is the
  truth surface this skill reads.
