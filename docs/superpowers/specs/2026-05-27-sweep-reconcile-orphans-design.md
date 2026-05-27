# Sweep reconciles orphan pending-pr/ entries

**Date:** 2026-05-27
**Scope:** Add a `lookup_by_branch` operation to the `pr-github` skill, a new `sweep/reconcile.py` module, and a `ralph-executor reconcile` CLI subcommand. Sweep auto-reconciles `pending-pr/<PBI-ID>/` directories that are missing `PR-LINK.md` instead of giving up with a warning.
**Status:** Design — pending review.

## Background

Sweep walks `.ralph/pending-pr/` every iteration. For each PBI dir it reads `PR-LINK.md` to get the PR id, queries the host for state, and acts (move to `done/`, etc.). Today, if `PR-LINK.md` is missing, sweep raises `"PR-LINK.md is missing; cannot determine PR id"` and skips the dir. The dir stays in `pending-pr/` forever and the warning repeats every iteration.

Root cause: manual squash-merges via `gh` (during operator-driven babysit cycles) and older PBIs predating PR-LINK.md introduction leave on-disk `pending-pr/` dirs without the link file. Sweep has no fallback path.

Current observed orphans: 8 entries in `.ralph/pending-pr/` — 6 already-merged PRs, 2 still-open PRs.

## Decision

Reconcile orphans via the host's API (look up PR by source branch), then move the dir based on the discovered state. Architecture B (chosen over inline-in-runner and CLI-only): a new `sweep/reconcile.py` module, shared by the sweep loop and a new `ralph-executor reconcile` CLI.

The `lookup_by_branch` operation is a 6th sub-op on the `pr` skill — sibling of `create_pr`, `read_threads`, `reply`, `set_status`, `show`. Pr-github gets the implementation this PBI; pr-ado is deferred (Phase 2 of `2026-05-24-05-ado-pr-skill.md` plan plus a separate follow-up tracker for adding `lookup_by_branch` to pr-ado when that phase lands).

## State machine

For each orphan `.ralph/pending-pr/<PBI-ID>/` with no `PR-LINK.md`:

1. Call `lookup_by_branch.py --branch ralph/<PBI-ID> --include-branch-check`
2. Map the result:

| Lookup result | Branch exists? | Destination | Write PR-LINK.md? |
|---|---|---|---|
| PR merged | n/a | `done/` | yes |
| PR open | n/a | stay in `pending-pr/` | yes (so future sweeps track it) |
| PR closed (unmerged) | n/a | `blocked/` | yes |
| No PR | yes | `blocked/` | no (no URL to write) |
| No PR | no | `inbox/` | no |
| Lookup API error (exit 3) | n/a | stay in `pending-pr/` | no |
| Lookup programming error (exit 2) | n/a | stay; raise `ReconcileError` | no |

## Architecture

**File layout (delta):**

```
skills/pr-github/scripts/
  lookup_by_branch.py          NEW

ralph_executor/sweep/
  reconcile.py                 NEW
  runner.py                    MODIFY (replace missing-PR-LINK error path)
  types.py                     MODIFY (add ReconcileAction, ReconcileReport)

ralph_executor/
  cli.py                       MODIFY (add `reconcile` subcommand)

tests/skills/pr_github/
  test_lookup_by_branch.py     NEW

tests/executor/sweep/
  test_reconcile.py            NEW

tests/executor/
  test_cli_reconcile.py        NEW
  test_runner.py               MODIFY (update missing-PR-LINK assertion)
```

### `lookup_by_branch.py` contract

```
Args:   --branch <name>                   (required)
        --repo <repo-name>                (required; matches existing
                                           pr-github scripts which all take
                                           --repo as a required flag)
        --include-branch-check            (optional flag; default off)

Env:    GH_TOKEN (required), GH_OWNER (required) — same as the rest of pr-github

Stdout: JSON, one document per call:
  {
    "pr": {
      "state": "open" | "closed" | "merged",
      "url": "https://github.com/<owner>/<repo>/pull/<n>",
      "pr_id": <int>,
      "merged_at": "2026-05-27T01:23:45Z" | null
    } | null,
    "branch_exists": true | false | null   // null when --include-branch-check off
  }

Exit:   0 success, 2 validation / arg / auth error, 3 GitHub HTTP error
```

GitHub mapping:
- `state == "open"` → `"open"`
- `state == "closed" AND merged_at != null` → `"merged"`
- `state == "closed" AND merged_at == null` → `"closed"`

This is **distinct** from `show.py`'s cross-host `active/completed/abandoned` vocab because reconcile cares about merged-vs-closed-unmerged, which `completed` collapses. Reconcile gets its own vocabulary (`open/merged/closed`) so the state mapping table above is unambiguous.

### `reconcile.py` API

```python
# ralph_executor/sweep/reconcile.py

from ralph_executor.sweep.types import ReconcileAction, ReconcileReport


class ReconcileError(RuntimeError):
    """Raised on a programming-error response from lookup_by_branch
    (exit 2). API errors (exit 3) do NOT raise — they return
    KEEP_API_ERROR so a transient GitHub outage doesn't crash sweep."""


def reconcile_orphan(
    pbi_dir: Path,
    ctx: SweepContext,
    *,
    dry_run: bool = False,
) -> ReconcileAction:
    """Reconcile a single orphan. Calls the staged lookup_by_branch
    skill via subprocess, parses JSON, moves the dir, optionally writes
    PR-LINK.md. Returns the chosen action.

    Errors:
      - subprocess exit 2 → ReconcileError (caller logs + adds to
        sweep's per-PBI error dict, matching today's shape)
      - subprocess exit 3 → returns KEEP_API_ERROR (caller logs WARNING,
        leaves orphan in place for next iteration)
      - Malformed JSON → ReconcileError
    """


def reconcile_all(
    ctx: SweepContext,
    *,
    dry_run: bool = False,
) -> ReconcileReport:
    """Iterate every dir in .ralph/pending-pr/ that has no PR-LINK.md
    and call reconcile_orphan on each. Per-PBI failures are isolated
    (matching existing _SweepPbiError semantics) and reported in the
    returned ReconcileReport without aborting the pass.

    Returns counts per action plus per-PBI detail.
    """
```

### Types (in `sweep/types.py`)

```python
class ReconcileAction(StrEnum):
    MOVED_TO_DONE = "moved_to_done"
    MOVED_TO_BLOCKED = "moved_to_blocked"
    MOVED_TO_INBOX = "moved_to_inbox"
    KEEP_PENDING = "keep_pending"          # PR still open; PR-LINK.md written
    KEEP_API_ERROR = "keep_api_error"      # subprocess exit 3; warn + retry next iter


@dataclass(frozen=True)
class ReconcileReport:
    actions: Mapping[str, ReconcileAction]   # pbi_id → action
    errors: Mapping[str, str]                # pbi_id → human-readable error
```

### Sweep loop integration

In `runner.py`, where the code currently raises `_SweepPbiError("PR-LINK.md is missing; cannot determine PR id")`, instead:

```python
action = reconcile.reconcile_orphan(pbi_dir, ctx)
if action == ReconcileAction.KEEP_API_ERROR:
    log.warning("sweep: reconcile API error for %s; will retry next iteration",
                pbi_dir.name)
# Otherwise the move has already happened (or KEEP_PENDING which means the
# orphan now has PR-LINK.md and will be picked up by normal flow next iter).
```

`reconcile_orphan` raising `ReconcileError` is caught by the existing per-PBI try/except in the sweep loop and added to `errors` — same shape as today's missing-PR-LINK error. No change to sweep's outer return contract.

### CLI `ralph-executor reconcile`

```
$ ralph-executor reconcile [--dry-run] [--repo <path>] [--workspace <name>]

PBI-ID                              Branch    PR     Action
----------------------------------- --------- ------ ----------------
STAGE-B-PLAN-03                     exists    #25    moved to done/
STAGE-B-PLAN-04                     exists    #26    moved to done/
STAGE-B-PLAN-11                     exists    #28    stays (open)
STAGE-B-PLAN-20a-worktree           exists    #29    stays (open)
WI-1234                             missing   none   moved to inbox/

8 orphans processed: 6 moved, 2 stays, 0 errors.
```

`--dry-run`: prints same table prefixed with `would: ` per row; no filesystem moves. Exit 0.
Real run: exits 0 if `reconcile_all` returned (errors recorded in report but not fatal). Exits 2 if `cfg.git_host == ""`. Exits 3 if `reconcile_all` itself crashes (programming bug).

### Skill invocation

Subprocess to the staged `~/.claude/skills/pr/scripts/lookup_by_branch.py` (or equivalent project-local skill dir per host_select). Sweep gets the path from `ctx.ado_pr_scripts_path` (existing field — the name is historical, the field is host-neutral). subprocess.run with check=False; parse JSON from stdout; map exit code to error class.

## Error handling

| Failure | Exit code | Reconcile response |
|---|---|---|
| Bad args (missing --branch, etc.) | 2 | `ReconcileError` — caller adds to sweep errors dict |
| GitHub auth missing (`GH_TOKEN` unset) | 2 | `ReconcileError` — but `cfg`-load already validated; should be unreachable unless env mutated mid-run |
| GitHub 5xx / network | 3 | `KEEP_API_ERROR` — warn + retry |
| GitHub rate limit (403 with `X-RateLimit-Remaining: 0`) | 3 | `KEEP_API_ERROR` — warn with reset time + retry |
| Malformed JSON on stdout | n/a | `ReconcileError` |

Filesystem moves use `shutil.move` wrapped in try/except OSError → `_SweepPbiError`. Per-PBI isolation matches existing sweep code (same pattern as `_emit_feedback_pbi`).

## Testing

`tests/skills/pr_github/test_lookup_by_branch.py` (uses `responses` library — already a dep):

- PR found, state=open → `{"pr": {"state": "open", ...}}`
- PR found, state=closed, merged_at=set → `{"pr": {"state": "merged", ...}}`
- PR found, state=closed, merged_at=null → `{"pr": {"state": "closed", ...}}`
- Empty PR list → `{"pr": null}`
- With `--include-branch-check`: branch 200 → `branch_exists: true`
- With `--include-branch-check`: branch 404 → `branch_exists: false`
- Without `--include-branch-check`: `branch_exists: null`
- Missing `GH_TOKEN` → exit 2 + stderr
- GitHub 500 → exit 3 + stderr
- GitHub 403 rate-limit → exit 3 + stderr
- Missing `--branch` arg → exit 2

`tests/executor/sweep/test_reconcile.py`:

- Merged PR → orphan moves to `done/`; PR-LINK.md written with URL
- Open PR → orphan stays; PR-LINK.md written
- Closed-unmerged → orphan moves to `blocked/`; PR-LINK.md written
- No PR + branch exists → orphan moves to `blocked/`; no PR-LINK.md
- No PR + branch missing → orphan moves to `inbox/`; no PR-LINK.md
- Subprocess exit 3 → orphan stays; WARNING logged; returns `KEEP_API_ERROR`
- Subprocess exit 2 → orphan stays; raises `ReconcileError`
- Malformed JSON on stdout → raises `ReconcileError`
- `reconcile_all` with mixed-state pending-pr/: 3 orphans, each different state, report counts match
- `reconcile_all(dry_run=True)`: no filesystem moves; report still populated
- `reconcile_all`: one PBI fails mid-batch → others still processed; failure recorded in report.errors

`tests/executor/test_cli_reconcile.py`:

- `ralph-executor reconcile` calls `reconcile.reconcile_all` with the right ctx
- `--dry-run` flag flows through
- Output table contains one row per PBI
- Exit code 0 on success
- Exit code 2 when `cfg.git_host == ""`

`tests/executor/sweep/test_runner.py` (modify existing test):

- "missing PR-LINK.md raises sweep error" test → update to assert reconcile is called instead, and that the orphan was moved (no error in sweep result)
- Add: reconcile API error path → sweep result has WARNING but no fatal error in the errors dict

## Acceptance

- `skills/pr-github/scripts/lookup_by_branch.py` exists with JSON contract above + exit codes 0/2/3
- `ralph_executor/sweep/reconcile.py` exposes `reconcile_orphan` + `reconcile_all`
- Sweep auto-reconciles orphans; missing-PR-LINK no longer surfaces as a sweep error
- `ralph-executor reconcile [--dry-run]` exists
- PR-LINK.md written retroactively on `done/` + `blocked/` moves (open PRs that stay in pending-pr/ also get PR-LINK.md written so the next sweep iteration uses the normal path)
- The 8 current orphans clear on first sweep iteration after this PBI lands
- pytest / ruff / mypy strict all green
- `responses` library used for all skill HTTP mocking (no network in CI)
- GitHub issue filed on emp3thy/ralph tracking ADO `lookup_by_branch` follow-up (blocked on Phase 2 pr-ado skill)

## Out of scope

- ADO `lookup_by_branch.py` — github-only this PBI; ADO via Phase 2 of `docs/superpowers/plans/2026-05-24-05-ado-pr-skill.md` + new follow-up issue
- Reconciling `current/` or other PBI directories — only `pending-pr/`
- Backfilling PR-LINK.md for already-`done/` PBIs from earlier history — only on reconcile-induced moves
- New skill operations beyond `lookup_by_branch`

## Risks

| Risk | Mitigation |
|---|---|
| Branch-existence API check adds latency | Gated behind `--include-branch-check`; sweep enables it (~8 calls one-time, amortizes to zero); GitHub 5000/hr limit covers tens of thousands of orphans |
| Subprocess invocation pattern not currently used in sweep | New code path; covered by tests with mocked `subprocess.run`. `_pr_skill_scripts_path(cfg)` already gives sweep the staged skill location |
| Reconcile moves a PBI to wrong dir | Each move + PR-LINK.md write wrapped in try/except OSError → `_SweepPbiError`; per-PBI isolation matches existing sweep error semantics |
| Open PR transitions to merged between reconcile and next sweep iteration | Reconcile writes PR-LINK.md when state is open. Next sweep iteration reads PR-LINK.md, queries fresh state via `show.py`, moves to done if merged. No double-handling. |
| host_select staging hasn't run when CLI invoked | `ralph-executor reconcile` calls the same `load_config` + `prepare_host_environment` startup path as the normal loop entry. Skills are staged before reconcile_all runs |

## depends_on

None. Files touched (`sweep/runner.py`, `sweep/reconcile.py`, `sweep/types.py`, `cli.py`, new skill script + tests) don't overlap with the two queued config PBIs (`config.py`, `loop.py`, `conftest.py`). Ralph processes one PBI at a time anyway.
