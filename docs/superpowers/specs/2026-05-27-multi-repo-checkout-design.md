# Ralph reads `target_repo` and operates on that repo dynamically

**Date:** 2026-05-27
**Scope:** Ralph's loop reads `pbi.target_repo` from each claimed PBI's frontmatter, clones the target repo into the operator's workspace dir (once per target, refreshed via fetch on subsequent iterations), creates the per-PBI feature branch worktree inside that clone, and spawns Claude there. PR-skill subprocesses get `GH_OWNER` set per-call from the target's owner.
**Status:** Design — pending review.
**depends_on:** `["RALPH-PBI-TARGET-REPO-FIELD"]` — strict.

## Background

PBI 1 (`RALPH-PBI-TARGET-REPO-FIELD`) added a required `target_repo` field to every PBI's frontmatter. That PBI has zero behavior change — ralph still operates single-target. This PBI delivers the actual value-add: ralph reads `pbi.target_repo` and dynamically operates on the named repo. One ralph instance can serve many target repos.

Today's flow (after `STAGE-B-PLAN-20a-worktree` merged):
- Queue worktree at `<ralph-checkout>/.ralph-work/queue/` on `ralph-queue` branch.
- Per-PBI code worktree at `<ralph-checkout>/.ralph-work/repo-<PBI-ID>/` on `ralph/<PBI-ID>` branch.
- Feature branch always created from main of the same repo (`loop.py:296` already does this).
- All PR-skill subprocesses use the global `GH_OWNER` env var.

This PBI's change: the code worktree moves OUT of ralph's checkout, INTO a clone of `pbi.target_repo` at `$RALPH_WORKSPACE/clones/<owner>-<name>/.ralph-work/<PBI-ID>/`. The queue worktree stays where it is (inside ralph's checkout, on ralph-queue) — ralph repo continues to own the queue state.

## Decision

Three new modules + targeted modifications to the loop, claude_spawn, worktree, config, and git_ops:

1. **`ralph_executor/url_utils.py`** — `parse_target_repo(url) -> TargetRepoInfo` parses an HTTPS URL into `host` / `owner` / `name`. Generates `clone_url` (with `.git` suffix) and `slug` (`<owner>-<name>`) properties.
2. **`ralph_executor/target_clone.py`** — `ensure_clone(info, workspace_root) -> TargetClone` manages clone-once + fetch-each-iter lifecycle. Raises `TargetUnreachable` on git failure.
3. **`ralph_executor/git_ops.py` clone helper** — wraps `git clone <url> <dest>` with a timeout.
4. **Loop integration** — `_claim_pbi` reads `pbi.target_repo`, parses, host-checks (github only this PBI), ensures the clone, creates the per-PBI worktree inside the clone. New `_ClaimError` exception; `iterate_once` catches it and moves the PBI to `blocked/<id>/` with the reason in HISTORY.md.
5. **`claude_spawn.spawn_claude_p`** sets `GH_OWNER` per-subprocess from `pbi.target_info.owner` and uses the per-PBI worktree (in the target clone) as `cwd`.
6. **Sweep integration** — same env-bridge: when sweep iterates pending-pr PBIs, it injects `GH_OWNER` from each PBI's `target_repo` when calling pr-github show.
7. **Config** — new `workspace_root: Path` field with default `~/ralph-workspaces`. New `_resolve_path` helper.

### Settled decisions (from brainstorm)

| Decision | Choice |
|---|---|
| Self-host special case | None — always clone fresh into `$RALPH_WORKSPACE/clones/<slug>/` |
| Clone location | `$RALPH_WORKSPACE/clones/<owner>-<name>/` (operator-config dir; default `~/ralph-workspaces`) |
| Clone lifecycle | Clone once per target; `git fetch origin` on each iteration; per-PBI worktrees inside the clone; clone never auto-deleted |
| Auth | Single global `GH_TOKEN`; operator's responsibility to provision write scope on every target |
| PR-skill changes | Per-subprocess `GH_OWNER` env override (zero changes to skill scripts; `load_client()` reads `GH_OWNER` and `GH_TOKEN` as today) |
| Unreachable target | Move PBI to `blocked/` with reason `"target unreachable: <error>"` |
| Host scope | host_select runs per-PBI based on URL host parsing; non-github targets currently fail (pr-ado deferred) |
| Clone depth | Full clone (not shallow) — ralph needs `git diff main..` and full history |
| Worktree location | Inside the clone at `<clone-root>/.ralph-work/<PBI-ID>/` |
| Queue worktree | Unchanged: ralph repo's `.ralph-work/queue/` on `ralph-queue` |

## Architecture

### Disk layout (after this PBI)

```
$RALPH_WORKSPACE/                                 NEW (operator-config, default ~/ralph-workspaces)
   clones/
      emp3thy-ralph/                              clone of github.com/emp3thy/ralph
         .git/                                    real git dir; full clone
         .ralph-work/
            <PBI-ID>/                             per-PBI worktree on branch ralph/<PBI-ID>
            <PBI-ID-2>/                           another active PBI's worktree
      orgA-someproject/                           clone of github.com/orgA/someproject
         .git/
         .ralph-work/
            <PBI-ID>/

<ralph-repo>/                                     ralph's own checkout (deployed ralph runs from here)
   .ralph-work/
      queue/                                      queue worktree on ralph-queue branch (unchanged)
```

### Iteration flow (changed parts in bold)

1. Ralph reads next PBI from queue worktree at `<ralph-repo>/.ralph-work/queue/.ralph/inbox/...` (unchanged).
2. **Parse `pbi.target_repo` URL → `TargetRepoInfo(host, owner, name)`.**
3. **Host-check: github.com → proceed; other → raise `_ClaimError("unsupported host: <host>")`.**
4. **`target_clone.ensure_clone(info, workspace_root=cfg.workspace_root)`:**
   - If `<workspace_root>/clones/<slug>/` doesn't exist: `git clone <info.clone_url> <clone-root>` (timeout 120s).
   - If exists: `git -C <clone-root> fetch origin` (refresh main).
   - Any git failure → `TargetUnreachable`, caught upstream → PBI moves to blocked.
5. **Create per-PBI worktree at `<clone-root>/.ralph-work/<PBI-ID>/` on branch `ralph/<PBI-ID>` from `main` of the clone.**
6. Move PBI inbox → current on ralph-queue (queue worktree).
7. Spawn claude. **`cwd = <clone-root>/.ralph-work/<PBI-ID>/`.** **`env["GH_OWNER"] = info.owner`.** `RALPH_PBI_DIR` points at the queue worktree's `.ralph/current/<PBI-ID>/` (unchanged).
8. Claude edits code in the per-PBI worktree (the target clone's worktree). Pushes feature branch to target's origin.
9. PR opens via `pr-github create_pr.py` subprocess (GH_OWNER + --repo = target's name).
10. Sweep observes PR via target's API; queue state writes go to ralph repo's queue worktree (unchanged).
11. Terminal outcome: tear down per-PBI worktree; clone persists for next PBI on the same target.

### File map (delta)

```
ralph_executor/url_utils.py           NEW (parse_target_repo + TargetRepoInfo)
ralph_executor/target_clone.py        NEW (ensure_clone + TargetClone + TargetUnreachable)
ralph_executor/git_ops.py             MODIFY (new clone() helper with timeout)
ralph_executor/worktree.py            MODIFY (work_worktree_path takes clone_root parameter)
ralph_executor/config.py              MODIFY (workspace_root field + _resolve_path helper)
ralph_executor/setup_cmds.py          MODIFY (CONFIG_TOML_STUB documents workspace_root)
ralph_executor/loop.py                MODIFY (_claim_pbi reads target_repo, ensures clone, creates worktree in clone)
ralph_executor/claude_spawn.py        MODIFY (cwd = clone's per-PBI worktree; GH_OWNER from target)
ralph_executor/sweep/runner.py        MODIFY (GH_OWNER env override per pending-pr PBI when calling pr-github show)
ralph_executor/types.py               MODIFY (PBI dataclass gains target_info / work_worktree fields)

tests/executor/test_url_utils.py      NEW
tests/executor/test_target_clone.py   NEW
tests/executor/test_loop.py           MODIFY (multi-target claim scenarios)
tests/executor/test_claude_spawn.py   MODIFY (GH_OWNER + cwd assertions)
tests/executor/test_config_toml.py    MODIFY (workspace_root layering)
tests/executor/sweep/test_runner.py   MODIFY (GH_OWNER per-PBI in sweep subprocess calls)
```

### Component contracts

**`url_utils.py`:**

```python
@dataclass(frozen=True)
class TargetRepoInfo:
    host: str       # "github.com"
    owner: str      # "emp3thy"
    name: str       # "ralph" (no .git suffix)

    @property
    def clone_url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.name}.git"

    @property
    def slug(self) -> str:
        return f"{self.owner}-{self.name}"


def parse_target_repo(url: str) -> TargetRepoInfo:
    """Raises ValueError on invalid URL (matches PBI 1's validator)."""
```

ADO URLs (`https://dev.azure.com/myorg/myproj/_git/myrepo`) parse to `host=dev.azure.com, owner=myorg, name=myproj` — known wrong for ADO. Acceptable this PBI because host_select rejects non-github upstream. ADO support is a follow-up PBI that adds an ADO-shaped parser path.

**`target_clone.py`:**

```python
class TargetUnreachable(RuntimeError):
    """Raised on clone / fetch failure (404, auth, network)."""


@dataclass(frozen=True)
class TargetClone:
    info: TargetRepoInfo
    clone_root: Path   # filesystem path of the clone


def ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
    """Clone once + fetch each subsequent call. Layout:
    <workspace_root>/clones/<info.slug>/. Creates workspace_root + clones/
    on first call. Raises TargetUnreachable on git failure."""
```

**`git_ops.clone(url, dest, *, timeout=120.0)`:** new helper. `subprocess.run(["git", "clone", url, str(dest)], timeout=timeout, ...)`. Raises `GitCommandError` on non-zero exit or timeout.

**`worktree.py`** — modify `work_worktree_path` signature:

```python
# Today:
def work_worktree_path(repo_root: Path, pbi_id: str) -> Path: ...

# After:
def work_worktree_path(clone_root: Path, pbi_id: str) -> Path:
    """Per-PBI worktree path INSIDE the target's clone."""
    return clone_root / ".ralph-work" / pbi_id
```

Queue worktree path stays as `queue_worktree_path(ralph_repo_root)` — unchanged.

**`config.py`** — new field + resolver:

```python
@dataclass(frozen=True)
class ExecutorConfig:
    # ... existing fields ...
    workspace_root: Path = field(
        default_factory=lambda: Path.home() / "ralph-workspaces"
    )

def _resolve_path(*, name, env_name, toml_value, default, source_label) -> Path:
    """env > toml > default. Expands ~. Validates by stringifying."""
```

`workspace_root` TOML key + `RALPH_WORKSPACE` env override. Added to `_TOML_KNOWN_KEYS`. Documented in `CONFIG_TOML_STUB`.

**`loop._claim_pbi`** — claim sequence:

```python
def _claim_pbi(cfg, pbi):
    target_url = _read_target_repo_from_pbi(pbi)  # new helper, reads PBI.md/BUG.md frontmatter

    try:
        info = parse_target_repo(target_url)
    except ValueError as exc:
        raise _ClaimError(f"invalid target_repo: {exc}") from exc

    if info.host != "github.com":
        raise _ClaimError(f"unsupported host: {info.host}")

    try:
        clone = ensure_clone(info, workspace_root=cfg.workspace_root)
    except TargetUnreachable as exc:
        raise _ClaimError(f"target unreachable: {exc}") from exc

    # Move PBI inbox -> current on ralph-queue (existing logic).
    moved = move_inbox_to_current(...)

    # Create per-PBI worktree INSIDE the clone (not inside ralph's checkout).
    work_wt = worktree.work_worktree_path(clone.clone_root, moved.id)
    worktree.create_work_worktree(
        clone.clone_root, work_wt, moved.id, base_branch="main"
    )
    return moved.with_clone(clone, work_wt)


class _ClaimError(RuntimeError):
    """Raised when claim fails for a reason that warrants moving the PBI
    to blocked/ with a reason. Caught by iterate_once."""
```

`iterate_once` catches `_ClaimError`, moves the PBI to `blocked/<id>/` with the reason in HISTORY.md, continues to next iteration.

**`claude_spawn.spawn_claude_p`** — modified env + cwd:

```python
env = os.environ.copy()
env["RALPH_PBI_DIR"] = str(pbi.path)
env["BASH_MAX_TIMEOUT_MS"] = str(cfg.bash_max_timeout_ms)
# NEW: per-target owner for any pr-github subprocess
env["GH_OWNER"] = pbi.target_info.owner
proc = subprocess.Popen(
    argv,
    cwd=str(pbi.work_worktree),  # NEW: per-PBI worktree in target's clone
    env=env,
    ...
)
```

**Sweep** — same env-bridge per pending-pr PBI when calling `pr-github show`:

```python
# In sweep_pr_state.fetch (or wherever the subprocess is dispatched):
env = os.environ.copy()
env["GH_OWNER"] = pbi.target_info.owner  # NEW
subprocess.run([show_script, ...], env=env, ...)
```

## Error handling

| Failure | Where caught | Resolution |
|---|---|---|
| PBI missing `target_repo` field | `_read_target_repo_from_pbi` raises | `_ClaimError`; move PBI to blocked with reason "PBI missing target_repo field" |
| URL fails `parse_target_repo` | `parse_target_repo` raises `ValueError` | `_ClaimError`; move to blocked with reason "invalid target_repo URL: <err>" |
| Host not supported | host check in `_claim_pbi` | `_ClaimError`; move to blocked with reason "unsupported host: <host>" |
| `git clone` fails | `target_clone.ensure_clone` raises `TargetUnreachable` | `_ClaimError`; move to blocked with reason "target unreachable: <err>" |
| `git fetch origin` fails on existing clone | Same | Same |
| Per-PBI worktree creation fails | `worktree.create_work_worktree` raises | Existing failure path; matches today's same-repo behavior |
| GH_TOKEN lacks write on target | Surfaces during PR push | PBI moves to blocked via existing PR-failure path |

All four claim-time failures write the reason into the PBI's HISTORY.md before the move, so operators have an audit trail.

## Testing

### `tests/executor/test_url_utils.py` (NEW)

- `parse_target_repo("https://github.com/emp3thy/ralph")` → `host=github.com, owner=emp3thy, name=ralph, slug=emp3thy-ralph`
- `.git` suffix stripped
- `clone_url` adds `.git` back
- Rejects `http://...`, missing path, non-string

### `tests/executor/test_target_clone.py` (NEW)

- First call: `git clone` invoked, clone dir created
- Second call (clone exists): `git fetch origin` invoked, no clone
- Clone failure → `TargetUnreachable`
- Fetch failure → `TargetUnreachable`
- workspace_root + `clones/` subdir auto-created

### `tests/executor/test_loop.py` (EXTEND)

- PBI with valid github target_repo → clone created in workspace_root, worktree created in clone, claude spawned with correct cwd + GH_OWNER
- PBI with unparseable URL → blocked with reason "invalid target_repo"
- PBI with non-github host → blocked with reason "unsupported host"
- PBI with target_repo failing to clone → blocked with reason "target unreachable"
- PBI missing target_repo field → blocked with reason "PBI missing target_repo field"

### `tests/executor/test_claude_spawn.py` (EXTEND)

- Spawn sets `env["GH_OWNER"]` to `pbi.target_info.owner`
- Spawn uses `cwd=pbi.work_worktree` (per-PBI worktree in clone)
- Different PBIs targeting different repos → different GH_OWNER values; no cross-iteration env leak

### `tests/executor/test_config_toml.py` (EXTEND)

- TOML `workspace_root = "/some/path"` → `cfg.workspace_root == Path("/some/path")`
- env `RALPH_WORKSPACE` overrides TOML
- default: `cfg.workspace_root == Path.home() / "ralph-workspaces"`
- `~/` expansion via `expanduser()`

### `tests/executor/sweep/test_runner.py` (EXTEND)

- Pending-pr PBI's target_repo info → `GH_OWNER` set per subprocess call in sweep's pr-github show invocation
- Two pending-pr PBIs with different targets → each subprocess sees its own GH_OWNER

### End-to-end smoke

- Self-host case: ralph with `target_repo: https://github.com/emp3thy/ralph` on a PBI clones itself, branches, claude works, PR opens. Tested in `tests/safety/test_integration_loop.py` or similar.

## Acceptance

- `ralph_executor/url_utils.py` exists with `parse_target_repo` + `TargetRepoInfo`
- `ralph_executor/target_clone.py` exists with `ensure_clone` + `TargetClone` + `TargetUnreachable`
- `ralph_executor/git_ops.py` has `clone(url, dest, *, timeout=120.0)`
- `ralph_executor/worktree.py.work_worktree_path` takes `clone_root` parameter
- `ralph_executor/config.py` has `workspace_root: Path` with default `~/ralph-workspaces`, layered defaults < TOML < env
- `_resolve_path` helper exists in config.py
- `CONFIG_TOML_STUB` documents `workspace_root`
- `ralph_executor/loop._claim_pbi` reads `pbi.target_repo`, parses, host-checks, ensures the clone, creates the per-PBI worktree inside the clone
- `_ClaimError` exception class; `iterate_once` catches it and moves PBI to `blocked/` with the reason in HISTORY.md
- `ralph_executor/claude_spawn.spawn_claude_p` sets `env["GH_OWNER"]` per subprocess from `pbi.target_info.owner`; uses `cwd=pbi.work_worktree`
- Sweep injects `GH_OWNER` per pending-pr PBI when calling pr-github show
- Self-host smoke: ralph operates on a clone of itself at `$RALPH_WORKSPACE/clones/emp3thy-ralph/`
- pytest / ruff / mypy strict all green
- depends_on: `["RALPH-PBI-TARGET-REPO-FIELD"]`

## Out of scope

- ADO targets (pr-ado deferred; non-github URLs move to blocked)
- Per-target auth tokens (single global GH_TOKEN; operator manages scope)
- Concurrent iteration locking (ralph is single-threaded)
- Auto-cleanup of stale clones (operator manages disk)
- Shallow clones (always full clone)
- `target_branch` field (always branches from main)
- Migration tooling for switching existing operators (one-time clone is acceptable cost)
- Per-target host_select stagings of different PR skills

## Risks

| Risk | Mitigation |
|---|---|
| GH_TOKEN lacks write on some targets | Surfaces as `TargetUnreachable` on push; PBI moves to blocked/ |
| Workspace dir slug collision (same owner-name on different hosts) | Slug is `<owner>-<name>`; collision unlikely. If hit, extend slug to `<host>-<owner>-<name>`. YAGNI today. |
| Network flakiness during fetch → spurious blocked | TargetUnreachable surfaces transient errors; operator moves PBI back to inbox to retry. Auto-retry deferred. |
| Large clones eat disk on operator machine | Operator manages; runbook note. Workspace dir is operator-config so they can place it on a large volume. |
| Clone's main drifts from target's main between iterations | `git fetch origin` on every iteration refreshes; new feature branches always derived from the just-fetched main |
| Per-PBI worktree creation fails because branch already exists | Existing `worktree.py` handles this (same as today's same-repo case) |
| First-run cost: one extra clone for self-host operators | One-time. Doc note: "first run after this PBI installs creates a clone in $RALPH_WORKSPACE." |
| Subprocess GH_OWNER override leaks between iterations | Each subprocess gets a fresh env dict (`os.environ.copy() + overrides`); no global mutation. Tests assert no leak. |
| Sweep needs PBI's target_repo to call pr-github show — what if PBI in pending-pr lacks the field | Defensive: sweep skips with WARNING if field missing. PBI 1's migration ensures all existing PBIs have it. |
