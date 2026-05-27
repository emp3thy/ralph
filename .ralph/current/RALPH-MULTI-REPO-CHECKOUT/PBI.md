---
id: RALPH-MULTI-REPO-CHECKOUT
type: feature
status: current
severity: normal
attempts: 0
created_at: 2026-05-27T00:05:00+00:00
updated_at: 2026-05-27T23:06:08+00:00
depends_on: ["RALPH-PBI-TARGET-REPO-FIELD"]
target_repo: https://github.com/emp3thy/ralph
---

# Ralph reads target_repo and operates on it dynamically

PBI 2 of the multi-target redesign. The actual value-add: ralph's loop reads `pbi.target_repo` from each claimed PBI and operates on that repo, not on ralph's own checkout.

- Clones target into `$RALPH_WORKSPACE/clones/<owner>-<name>/` (default `~/ralph-workspaces`) — once per target, refreshed via `git fetch origin` on subsequent iterations
- Creates per-PBI feature branch worktree INSIDE the clone at `<clone-root>/.ralph-work/<PBI-ID>/`
- Spawns Claude there with `cwd = work_worktree` and `env["GH_OWNER"]` set per-subprocess from the target's owner
- Sweep applies the same env-bridge when calling pr-github show for each pending-pr PBI

After this PBI: one ralph instance can serve many target repos. Self-host case = no special path; ralph clones itself into the workspace dir like any other target.

**Strict depends_on `RALPH-PBI-TARGET-REPO-FIELD`** — that PBI adds the `target_repo` frontmatter field; this PBI consumes it. Cannot start until PBI 1 lands in `done/`.

Spec: `docs/superpowers/specs/2026-05-27-multi-repo-checkout-design.md`
Plan: `docs/superpowers/plans/2026-05-27-multi-repo-checkout-plan.md`

See `PLAN.md` for the implementation plan pointer.

## Acceptance criteria

- `ralph_executor/url_utils.py` exists with `parse_target_repo` + `TargetRepoInfo`
- `ralph_executor/target_clone.py` exists with `ensure_clone` + `TargetClone` + `TargetUnreachable`
- `ralph_executor/git_ops.py` has `clone(url, dest, *, timeout=120.0)` helper
- `ralph_executor/worktree.py.work_worktree_path` takes `clone_root` parameter
- `ralph_executor/config.py` has `workspace_root: Path` default `~/ralph-workspaces`, layered defaults < TOML < env
- `_resolve_path` helper exists in `config.py`
- `CONFIG_TOML_STUB` documents `workspace_root`
- `PBI` dataclass in `types.py` carries `target_repo`, `target_info`, `work_worktree` fields
- `ralph_executor/loop._claim_pbi` reads `pbi.target_repo`, parses, host-checks, ensures clone, creates per-PBI worktree inside the clone
- `_ClaimError` exception class exists; `iterate_once` catches and moves PBI to `blocked/<id>/` with reason in HISTORY.md
- `ralph_executor/claude_spawn.spawn_claude_p` sets `env["GH_OWNER"]` per subprocess from `pbi.target_info.owner`; uses `cwd=pbi.work_worktree`
- Sweep injects `GH_OWNER` per pending-pr PBI when calling pr-github show
- Self-host smoke: ralph operates on a clone of itself at `$RALPH_WORKSPACE/clones/emp3thy-ralph/`
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/RALPH-MULTI-REPO-CHECKOUT`
