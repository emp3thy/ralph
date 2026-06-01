---
name: ralph-status
description: Read-only view of the Ralph queue. Reads the single queue clone at <workspace_root>/queue/, walks the .ralph/{inbox,current,pending-pr,done,blocked} state folders, parses each PBI's frontmatter against the canonical schema, and renders a fixed-width table grouped by target_repo. The OWNER column shows which ralph instance claimed each current/<id>/ PBI (from CLAIM.json::instance_id). Supports filtering by --state and --target-repo, plus --json for downstream automation.

---

# ralph-status

## What this skill does

The `ralph-status` skill is the BA / PM / triager workboard for Ralph. It
reads (never writes) the queue state from the **single queue clone** at
`<workspace_root>/queue/` and renders it as a terminal-friendly table
grouped by the PBI's `target_repo` field. The data source is the
`.ralph/` directory on the queue clone's `main` branch — exactly the
same tree `ralph-new`, `ralph-cancel`, `ralph-promote`, and
`ralph-triage` write to.

For each invocation, the skill:

1. Resolves `workspace_root` and `queue_repo` from
   `~/.ralph/config.toml` (overridable via `--workspace` /
   `--queue-repo`).
2. Calls `acquire_queue_clone(workspace_root, queue_repo)` — clone on
   first call, fast-forward pull on subsequent calls. Always on `main`.
3. Walks the five state folders inside the clone: `inbox/`, `current/`,
   `pending-pr/`, `done/`, `blocked/`.
4. For each PBI directory, reads its entry file (`PBI.md`, `BUG.md`,
   `FEEDBACK.md`) and parses the YAML frontmatter against the canonical
   schema defined in Plan 1.
5. Applies `--state` and `--target-repo` filters, then stable-sorts rows
   by `(target_repo, state, created_at)` so output is grouped.
6. Renders one row per PBI to stdout, either as a fixed-width table or
   as JSON via `--json`.

## When to use it

Use `ralph-status` to answer "what is Ralph doing right now?" or "what
is queued for service X?". It replaces the kanban UI a v2 web supervisor
would expose.

## Inputs

| Flag | Required | Description |
|---|---|---|
| `--state <state>` | no | Filter rows to a single state. Allowed values: `inbox`, `current`, `pending-pr`, `done`, `blocked`. Default: all five. |
| `--target-repo <url>` | no | Filter rows to PBIs whose `target_repo` frontmatter field equals this URL exactly. |
| `--json` | no | Emit JSON to stdout instead of a fixed-width table. |
| `--workspace <path>` | no | Override `workspace_root` from `~/.ralph/config.toml`. |
| `--queue-repo <url>` | no | Override `queue_repo` from `~/.ralph/config.toml`. |

## Configuration

`ralph-status` reads `workspace_root` and `queue_repo` from
`~/.ralph/config.toml` (populated by `ralph-executor init`). The CLI
flags above override the TOML values one-by-one. If `queue_repo` is
neither set in TOML nor passed via `--queue-repo`, the skill exits 2
with a clear error.

## Output

### Table mode (default)

```
TARGET                                  STATE       OWNER    ID        TYPE     SEVERITY  AGE   TITLE
https://github.com/emp3thy/svc-auth     inbox       —        WI-1234   feature  normal    2h    Add /healthz endpoint
https://github.com/emp3thy/svc-auth     current     ralph-a  WI-1235   bug      critical  1h    Pod crashloops on ROSA
https://github.com/emp3thy/svc-billing  pending-pr  —        WI-980    feature  high      6h    Migrate invoices to v2
```

Rows are grouped by `target_repo` then `state` then `created_at`, so all
PBIs for a given target sit on contiguous lines. Columns are
width-aligned to the widest value in the rendered set. The `TARGET`
column truncates URLs longer than 50 characters with a trailing `...`.
The `AGE` column shows the time since `created_at` (`s` seconds, `m`
minutes, `h` hours, `d` days; `?` if `created_at` is missing or
unparseable).

The `OWNER` column is the `instance_id` of the ralph that claimed the
PBI, read from `current/<id>/CLAIM.json`. It renders as `—` (em-dash)
when the PBI is not in `current/`, when `CLAIM.json` is missing, or when
`CLAIM.json` is unreadable / malformed — a corrupted claim file never
crashes the status view.

Malformed PBIs (missing entry file, invalid YAML, missing required
fields) render as a row with `?` for TARGET / TYPE / SEVERITY / AGE,
the directory name in the ID column, and `(parse error) <msg>` in the
TITLE column — they never abort the command.

A one-line summary (`# N PBI(s) in <queue-clone> (states: ...)`) is
written to stderr after the table so stdout stays purely tabular.

### JSON mode (`--json`)

```json
{
  "errors": [],
  "rows": [
    {
      "attempts": 0,
      "created_at": "2026-05-24T09:15:00+00:00",
      "error": null,
      "id": "WI-1234",
      "owner": null,
      "pbi_dir": ".ralph/inbox/WI-1234",
      "severity": "normal",
      "state": "inbox",
      "target_repo": "https://github.com/emp3thy/svc-auth",
      "title": "Add /healthz endpoint",
      "type": "feature",
      "updated_at": "2026-05-24T09:15:00+00:00"
    }
  ]
}
```

The envelope is `{"rows": [...], "errors": []}` — there is no `repos`
key (the model has a single queue). The `owner` field carries
`CLAIM.json::instance_id` for PBIs in `current/`, or `null` for any
other state (and for current PBIs with missing / malformed CLAIM.json).
Malformed PBIs appear in `rows` with `error` set to the parse-failure
message and `target_repo` / `type` / `severity` / `attempts` /
`created_at` / `updated_at` / `title` all `null`. Top-level failures
(queue clone could not be materialised, config missing) print to stderr
and exit 2 — they do not appear in the JSON envelope.

## How it is invoked

```bash
uv run python skills/ralph-status/scripts/status.py
uv run python skills/ralph-status/scripts/status.py --state current
uv run python skills/ralph-status/scripts/status.py --target-repo https://github.com/emp3thy/svc-auth
uv run python skills/ralph-status/scripts/status.py --json
```

Tests live at `tests/skills/test_ralph_status.py` and use a bare git
remote as the queue plus a temp workspace (the same pattern as the
other queue-clone skills) — no network, no real host, no real remote.

## What this skill does NOT do

- It does not mutate the queue clone, the queue remote, or anything
  else on disk beyond `acquire_queue_clone`'s standard
  clone-or-fast-forward.
- It does not touch any target service repo. The `target_repo` field
  is read from each PBI's frontmatter purely for grouping and
  filtering.
- It does not call GitHub, Azure DevOps, or any other remote API.
- It does not invoke `claude -p` or otherwise touch the executor.
- It does not aggregate PR status (CI green / red, reviewer decisions).
  PR state lives elsewhere; the queue folder a PBI sits in is the truth
  surface this skill reads.
