# Multi-ralph — design

**Date:** 2026-05-31
**Status:** draft, awaiting user review
**Author:** Claude / gethin (brainstorm)

## Summary

Enable multiple `ralph-executor` instances to work the same `queue_repo` concurrently, each owning a distinct slice of in-flight PBIs. The executor moves from a hard-coded single-ralph workspace path (`<workspace_root>/queue/`) to a per-instance namespaced path (`<workspace_root>/queue-<instance-id>/`), and every claimed PBI carries a `CLAIM.json` marker that names the owning instance.

Coordination is purely via git operations on the queue branch — there is no new service, no heartbeat, no central manager. The existing `push_with_rebase` / `PushRebaseConflict` / `UncommittedSource` machinery already handles concurrent writers; this design extends it.

This is **Scope 1**: stable, unique, constant instance identity. Operators (or pod orchestrators) are responsible for ensuring each ralph has an `instance_id` that survives crash-and-restart. **Scope 2** — ephemeral pod identity, centralised fleet manager, heartbeat-over-HTTP, auto-reclaim of orphaned claims, fleet UI — is deferred to a separate spec.

## Goals

- N ralphs, distributed across hosts, can drain a single queue in parallel
- A crashed ralph that restarts with the same `instance_id` resumes its in-flight PBI without operator intervention
- Two ralphs accidentally launched on the same host against the same workspace cleanly refuse to co-exist
- N=1 single-ralph operation is the default and behaves identically to today from the operator's perspective (modulo a one-time path rename)
- No new long-lived service or external dependency

## Non-goals

- Ephemeral pod identity where a crashed pod is replaced by a fresh pod with a different name (Scope 2)
- Centralised fleet observability / dashboard (Scope 2)
- Auto-reclaim of orphaned claims via heartbeat / TTL (Scope 2)
- Same-host concurrent ralphs (lockfile makes this an explicit refusal; can be revisited if needed)
- Cross-instance event-log aggregation or cross-instance cycle detection
- Smarter conflict gates beyond existing `depends_on` (per "two devs / merge conflict / blocked" framing — git already models this)

## Architecture

### Identity resolution

The `instance_id` is resolved at executor startup, first match wins:

1. `--instance-id <name>` CLI flag
2. `RALPH_INSTANCE_ID` environment variable
3. `instance_id` key in TOML config
4. Hostname (transparent default for single-ralph workstation)

The hostname default is sanitised before use: lowercased, dots replaced with `-`, and any character outside `[a-z0-9_-]` replaced with `-`. This makes `MyBox.example.com` become `mybox-example-com` so workstations whose hostnames contain dots or uppercase still get a transparent first-run experience.

Validation (applied to the *resolved* id, including the sanitised hostname):

- Must match regex `^[a-z0-9][a-z0-9_-]{0,62}$` (filesystem-safe, fits in branch names if ever needed)
- Empty / missing after the full resolution chain → `ConfigError` with a message pointing at the TOML stanza
- Resolved id fails the regex after sanitisation → `ConfigError` instructing the operator to set `instance_id` explicitly
- No automatic fleet-wide dedup (operator discipline; same-host collisions are caught by the workspace lockfile)

The executor always operates in multi-ralph mode. N=1 is the default case — there is no separate single-ralph code path.

### Workspace layout

```
<workspace_root>/
  queue-<instance-id>/        # this ralph's queue clone (always namespaced)
    .ralph/                   # PBI state, events.db, halt sentinel
    .ralph.lock               # process lockfile (§ Safety)
    .git/
  clones/                     # one clone per target repo, local to this host
    <owner>/<name>/
      .git/
      .ralph-work/
        <pbi-id>/             # per-PBI worktree (today's pattern, unchanged)
```

- Queue clone path is always `queue-<instance-id>/`, even when only one ralph runs on the host
- Target clones remain local to each host. The current design assumes one ralph per host, so there is no inter-process sharing of target clones to worry about
- Per-PBI worktrees keep today's `<clone>/.ralph-work/<pbi-id>/` layout; no instance-id segment is introduced

### Concurrency surface

With one ralph per host, the only concurrent git surface is the **queue branch** at `origin`. Cross-host pushes race; the existing `push_with_rebase` plus the existing `PushRebaseConflict` / `UncommittedSource` handlers in `loop.iterate_once` cover it. No new retry helpers, no new locking primitives.

### Claim protocol

When a ralph claims an inbox PBI, the claim atomically performs:

1. `git mv inbox/<id>/ current/<id>/`
2. Write `current/<id>/CLAIM.json`
3. `git add current/<id>/CLAIM.json`
4. Single commit: `chore(queue): claim <id> for <instance-id>`
5. `push_with_rebase`

The move and the claim marker land in one commit. If `push_with_rebase` fails because another ralph just claimed the same PBI, the entire claim is aborted by the existing rebase-conflict path and the loop retries on the next iteration with a fresh inbox scan.

`CLAIM.json` schema:

```json
{
  "instance_id": "ralph-a",
  "claimed_at": "2026-05-31T12:34:56Z",
  "hostname": "box-a"
}
```

- `instance_id` — required, authoritative
- `claimed_at` — ISO-8601 UTC, set once at claim time, never updated
- `hostname` — diagnostic only, used by `ralph-status` for human-readable output

`pid` is intentionally omitted. With one ralph per host, hostname plus instance_id is enough to identify the owning process for an operator.

### `current_pbi()` semantics

`FilesystemQueueSource.current_pbi()` changes from "exactly one PBI in `current/`, or none" to "exactly one PBI in `current/` owned by *this* instance, or none":

```python
def current_pbi(self) -> PBI | None:
    """Return THIS instance's claimed PBI, or None.

    Scans .ralph/current/*/CLAIM.json. Returns the PBI whose
    CLAIM.json instance_id == cfg.instance_id. PBIs claimed by other
    instances are ignored. Raises QueueError if this instance owns
    more than one (one PBI per instance in flight at any time).
    """
```

- Multiple `current/<id>/` directories are now legal (one per live instance fleet-wide)
- A `current/<id>/` directory whose `CLAIM.json` names a foreign instance is silently skipped
- More than one own claim is a hard invariant violation → `QueueError`

### `pick_next` and dependency graph

`pick_next` is unchanged. The picker scans `inbox/` sorted by priority lane and `created_at`, and a candidate is eligible iff every id in its `depends_on` list is present in `done/`. PBIs claimed by other instances (sitting in `current/`) are not in `done/`, so they naturally block dependents — no extra wiring needed.

No new conflict signals (`mutex_group`, `touches`, same-target serialisation) are introduced. Two ralphs that pick PBIs that touch overlapping code in the same target repo behave like two developers doing the same thing: a git merge conflict surfaces at PR time, or the work is marked `STUCK` and routed to `blocked/`, and an operator triages.

### Sweep

The pending-pr sweep runs per-iteration on every ralph independently. Decisions land via `push_with_rebase`; the first writer wins. The N× `gh` API cost is accepted given small expected N and small expected pending-pr depth. No coordination protocol, no leader election.

### Halt sentinel

`.ralph/state/halted` lives on the queue branch. All ralphs see it on the next `_pull_queue` and refuse to iterate. The META-BUG written by the tripping instance includes a new field:

```yaml
tripped_by_instance: ralph-a
```

This is a one-line diagnostic addition; the rest of the halt flow is unchanged.

## Safety

### Workspace lockfile

`<workspace_root>/queue-<instance-id>/.ralph.lock` is an OS-level exclusive lockfile (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows). Acquired at executor startup, released by the OS on process exit (clean or crash).

JSON payload (informational only — the lock is the OS lock, not the JSON):

```json
{ "instance_id": "ralph-a", "hostname": "box-a", "pid": 12345, "started_at": "2026-05-31T..." }
```

If a second ralph process attempts to start against the same workspace, the lock acquisition fails and the process exits non-zero with:

```
another ralph already running on this workspace: pid=12345 started=2026-05-31T...
```

No stale-lock recovery logic is needed — OS-level locks release on process death by definition. The lockfile lives outside the `.git/` tree and outside `.ralph/`; it is never committed.

### Halt sentinel — cross-instance

Existing single-ralph behaviour: any ralph that pulls the queue and sees `.ralph/state/halted` raises `HaltedError` and exits. Unchanged. All instances halt on the next pull after one trips.

### Skill-level guards

- `ralph-status` gains an `OWNER` column derived from `CLAIM.json.instance_id` (or `—` for PBIs not in `current/`)
- `ralph-cancel` refuses if `CLAIM.json` shows a foreign `instance_id`; operator must use `ralph-recover` instead. Prevents accidental cross-instance cancel
- `ralph-promote` similarly refuses cross-instance ownership transfer

### `ralph-recover` skill — manual escape hatch

Single skill, operator-driven. Use cases:

- A `current/<id>/` whose owning instance is permanently dead (PC retired, instance renamed)
- An operator decides to forcibly take over or abandon another instance's claim

```
uv run python skills/ralph-recover/scripts/recover.py --pbi-id WI-1234 --to inbox
uv run python skills/ralph-recover/scripts/recover.py --pbi-id WI-1234 --to blocked
```

Steps performed:

1. Validate that the PBI exists in `current/`
2. Read existing `CLAIM.json` and print it to stderr for audit
3. `git mv current/<id>/ <destination>/<id>/`
4. Delete `CLAIM.json` (do not propagate stale claim marker to inbox or blocked)
5. Reset the attempt counter when `--to inbox` (an orphaned claim is not a failed attempt by the PBI)
6. Append `## Recovered from instance <X> by operator — <ISO timestamp>` to `HISTORY.md`
7. Single commit: `chore(queue): recover <id> from <instance>`
8. `push_with_rebase`

Refuses to run when:

- The PBI is not in `current/`
- The destination already contains a directory with the same PBI id
- The halt sentinel is active

No `--force` flag — invoking the skill is itself the deliberate operator action.

## Upgrade procedure

Operators are expected to drain the queue (let `current/` empty out) before upgrading the executor binary. No automatic legacy-state migration is performed inside the executor.

The one path-level migration the executor does perform on first startup: if `<workspace_root>/queue-<instance-id>/` does not exist but legacy `<workspace_root>/queue/` does exist, the legacy directory is renamed atomically to the new path. This is idempotent and fails loud if both paths exist.

Operators upgrading mid-flight (legacy `current/<id>/` without a `CLAIM.json`) are not supported — they must drain first. This is documented in the README's "Upgrading" section as part of this rollout.

## Component map

Code changes by file:

| File | Change |
|---|---|
| `ralph_executor/config.py` | New `instance_id` field; resolution chain (CLI/env/TOML/hostname); validation regex |
| `ralph_executor/cli.py` | `--instance-id` flag; pass through to config loader |
| `ralph_executor/user_config.py` | TOML schema includes `instance_id` |
| `ralph_executor/queue_clone.py` | Clone path becomes `queue-<instance-id>/`; legacy `queue/` rename on first startup |
| `ralph_executor/loop.py` | `_queue_repo_root` uses new path; `_claim_pbi` writes `CLAIM.json` atomically with the move; META-BUG carries `tripped_by_instance` |
| `ralph_executor/queue/filesystem.py` | `current_pbi()` filters by `instance_id` |
| New: `ralph_executor/lockfile.py` | OS exclusive lockfile acquire/release at startup |
| `skills/ralph-status/scripts/status.py` | Read `CLAIM.json`, add `OWNER` column |
| `skills/ralph-cancel/scripts/cancel.py` | Refuse on foreign `CLAIM.json` |
| `skills/ralph-promote/scripts/promote.py` | Refuse on foreign `CLAIM.json` (where applicable) |
| New: `skills/ralph-recover/scripts/recover.py` | Manual claim-recovery skill |
| `ralph_executor/setup_cmds.py` (init) | Prompt for `instance_id` (default hostname) |
| `docs/runbooks/ralph-setup.md`, `README.md` | Updated "Running multiple ralphs" section; upgrade procedure documented |

## Testing

### Unit

- `config.py` — instance_id resolution order, validation regex, missing-after-all-sources
- `filesystem.py::current_pbi()` — own / foreign / none / multiple-own cases
- `loop.py::_claim_pbi` — atomic commit contains both `git mv` and `CLAIM.json`; CLAIM.json content matches schema; legacy `queue/` → `queue-<instance>/` rename happens once and is idempotent
- `lockfile.py` — acquire / release; second acquire on the same workspace fails with the expected error
- `ralph-recover` — happy path, refuses-on-no-such-PBI, refuses-on-halt-sentinel, attempt counter reset on `--to inbox`, HISTORY append, no `CLAIM.json` left behind in destination

### Integration

Two `ExecutorConfig` instances with different `instance_id` values, sharing a single bare `queue_repo`. Drive interleaved `iterate_once` calls directly (no real subprocess required):

- T1 — ralph-a claims PBI-1 and ralph-b claims PBI-2 (two distinct PBIs in the inbox); both `current/<id>/` directories exist with the correct `CLAIM.json` after iteration
- T2 — Both ralphs race for the only inbox PBI; one wins, the loser observes `PushRebaseConflict` and recovers on the next iteration
- T3 — ralph-a's PBI promotes to `pending-pr/`; ralph-b's sweep runs and does not interfere with ralph-a's PBI
- T4 — ralph-a writes the halt sentinel; ralph-b's next `iterate_once` raises `HaltedError`
- T5 — Workspace lockfile contention: second process attempts to acquire the same `queue-<instance-id>/.ralph.lock` and exits non-zero

### Skill-level

- `ralph-status` populates the `OWNER` column from `CLAIM.json`
- `ralph-cancel` refuses on foreign `CLAIM.json` with a clear error
- `ralph-recover` end-to-end via subprocess against a real queue clone

No new smoke / E2E test is introduced. Multi-ralph is the same code path as N=1, and the existing E2E suite covers the N=1 default.

## Rollout

1. Land executor changes behind no feature flag — always-multi-ralph is the only code path
2. Update `ralph-executor init` to prompt for `instance_id` (default hostname)
3. Update `ralph-status`, `ralph-cancel`, `ralph-promote` skills
4. Add `ralph-recover` skill
5. Update README and runbooks: new "Running multiple ralphs" guidance, upgrade procedure
6. Release notes call out: drain `current/` before upgrading; new `instance_id` TOML key; new lockfile

## Out of scope (Scope 2 — separate future spec)

- Ephemeral pod identity (manager service, HTTP heartbeat, auto-reclaim of orphaned claims, fleet UI)
- Same-host concurrent ralphs beyond the explicit lockfile refusal
- Cross-instance event-log aggregation and cross-instance cycle detection
- Manager-mediated assignment / priority overrides / human-in-loop reclaim
