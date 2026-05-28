<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-27T23:09:03Z

- Step: Task 1 — `ralph_executor/url_utils.py` + `tests/executor/test_url_utils.py`
- Tests: green (7/7) `uv run pytest tests/executor/test_url_utils.py`
- Lint: ruff check + ruff format --check + mypy --strict all clean
- Notes: Next iteration tackles Task 2 (`git_ops.clone` helper). Watch: existing `GitCommandError.__init__` takes `(argv, returncode, stderr)` not a single string — plan's draft `clone()` impl raises with one string arg and will break; substitute the 3-arg constructor when implementing.

## Iteration 2 — 2026-05-28T00:00:00Z

- Step: Task 2 — `clone()` helper in `ralph_executor/git_ops.py` + 3 tests in `tests/executor/test_git_ops.py`
- Tests: green (17/17) `uv run pytest tests/executor/test_git_ops.py`
- Lint: ruff check + ruff format (1 reformat applied) + mypy --strict all clean
- Notes: Used the 3-arg `GitCommandError(argv, returncode, stderr)` form instead of the plan's draft single-string raise (per iter-1 warning). Falls back to `stdout.strip()` when `stderr` is empty. Next iteration: Task 3 (`target_clone.py` with `ensure_clone` + `TargetClone` + `TargetUnreachable`). The plan's draft target_clone module imports `git_ops` and calls `git_ops.fetch(clone_root)` / `git_ops.clone(info.clone_url, clone_root)` — both now exist.

## Iteration 3 — 2026-05-28T00:30:00Z

- Step: Task 3 — `ralph_executor/target_clone.py` (`ensure_clone` + `TargetClone` + `TargetUnreachable`) + 5 tests in `tests/executor/test_target_clone.py`
- Tests: green (5/5) `uv run pytest tests/executor/test_target_clone.py`
- Lint: ruff check + ruff format (auto-applied) + mypy --strict all clean
- Notes: Plan's draft tests raised `GitCommandError("authentication failed")` — substituted 3-arg form `GitCommandError(argv, returncode, stderr)` (matches the real constructor; iter-1+2 same pattern). Wrapping `TargetUnreachable` includes the `GitCommandError` str repr, which includes the stderr — `match=` patterns still hit. Next iteration: Task 4 (`_resolve_path` + `workspace_root` in `config.py`, `CONFIG_TOML_STUB` doc, conftest `cfg_for_repo` fixture). Plan Step 4.9 requires checking the `conftest.py` `cfg_for_repo` for the existing pattern before constructing the workspace_root default.

## Iteration 4 — 2026-05-28T01:30:00Z

- Step: Task 4 — `_resolve_path` + `workspace_root` field on `ExecutorConfig` + load_config wiring + `RALPH_WORKSPACE` env override + `CONFIG_TOML_STUB` doc + conftest `cfg_for_repo` fixture
- Tests: green (5/5 targeted) `uv run pytest tests/executor/test_config_toml.py -k workspace_root`; wider sweep `uv run pytest tests/executor tests/safety` 459/459 green
- Lint: ruff check + ruff format (auto-applied to config.py + test file) + mypy --strict on config.py all clean
- Notes: Plan's draft test `f'workspace_root = "{target}"'` blew up under Windows tmp paths — `\U` / `\t` in the path interpolate as TOML escape sequences (`Invalid hex value`). Fix: `.as_posix()` the path when writing the TOML literal so it round-trips through `Path()` identically. Recorded as a better-memory gotcha (id 397b291f). Next iteration: Task 5 — refactor `worktree.work_worktree_path` to take a `clone_root` parameter (sub-90% confidence step per plan; mitigation = chase all call sites first).

## Iteration 5 — 2026-05-28T02:00:00Z

- Step: Tasks 5 + 6 landed in earlier sessions (commits ff0a383, d2c5f31) but were not recorded in HISTORY.md. Verified on-disk state: `work_worktree_path(clone_root, pbi_id)` signature already refactored in `ralph_executor/worktree.py:38`; `PBI` dataclass already carries `target_repo`/`target_info`/`work_worktree` fields in `ralph_executor/types.py:54-56`. No code changes this iteration — just back-fill.
- Tests: not run (no code change)
- Notes: Catching up the log. Next iteration starts Task 7 (largest task — split into sub-steps 7A/7B/7C per plan mitigation).

## Iteration 6 — 2026-05-28T02:30:00Z

- Step: Task 7 sub-step 7A — `_ClaimError` exception class + `_read_target_repo_from_pbi` helper in `ralph_executor/loop.py` + 4 tests in `tests/executor/test_loop.py` (frontmatter read; missing target_repo raises; missing entry file raises; BUG.md entry file path)
- Tests: green (4/4 targeted) `uv run pytest tests/executor/test_loop.py -k "read_target_repo_from_pbi" -v`; full test_loop.py 35/35 green
- Lint: ruff check + ruff format --check + mypy --strict on loop.py all clean
- Notes: Plan test stub `pbi = PBI(...)` without target_repo defaults — the field defaults to `""` per Task 6's design so legacy construction still compiles, which the helper rejects via the `if not target_repo:` branch (treats empty string same as missing). Added a 4th test (BUG.md probe) not in the plan to exercise the entry-file probe order. Next iteration: sub-step 7B — `parse_target_repo` + host check inside `_claim_pbi`, with 2 negative-path tests (non-github host, malformed URL).

## Iteration 7 — 2026-05-28T03:00:00Z

- Step: Task 7 sub-step 7B — wired `_read_target_repo_from_pbi` + `parse_target_repo` + github-only host gate into `_claim_pbi` prelude (runs before legacy/worktree fork). Added 2 negative-path tests (`test_claim_raises_claim_error_for_non_github_host`, `test_claim_raises_claim_error_for_invalid_url`) plus `_write_pbi_with_target` helper.
- Tests: green (6/6 targeted) `uv run pytest tests/executor/test_loop.py -k "claim_raises or read_target_repo" -v`; full `tests/executor tests/safety` sweep 467/467 green.
- Lint: ruff check + ruff format clean project-wide; mypy --strict on loop.py clean.
- Notes: To keep the existing test_loop suite passing through the new prelude, updated `tests/executor/conftest.py:write_sample_pbi` to default `target_repo="https://github.com/test/repo"` (added as a kwarg so future tests can override). Existing fixtures + the worktree-mode tests inherit the github default and pass the host gate. ensure_clone wiring + worktree creation deferred to sub-step 7C. Next iteration: sub-step 7C — happy-path multi-target claim test (clone + worktree in clone) and TargetUnreachable → _ClaimError test, plus the actual ensure_clone + worktree.work_worktree_path(clone.clone_root, …) wiring inside `_claim_pbi`.

## Iteration 8 — 2026-05-28T04:00:00Z

- Step: Task 7 sub-step 7C — wired `target_clone.ensure_clone` into `_claim_pbi_worktree`. New signature `_claim_pbi_worktree(cfg, pbi, *, target_url, info)`. Clone is materialised first; `TargetUnreachable` → `_ClaimError("target unreachable: …")`. Per-PBI work worktree now lives at `<clone_root>/.ralph-work/<PBI-ID>/` via `work_worktree_path(clone.clone_root, moved.id)` + `ensure_worktree(clone.clone_root, ..., create_branch_from="origin/main")`. Removed the now-redundant `git_ops.fetch(cfg.repo_path)` (ensure_clone fetches the clone). Returned PBI carries `target_repo`/`target_info`/`work_worktree`; legacy single-checkout path also populates `target_repo`+`target_info` (no work_worktree).
- Tests: 2 new tests in test_loop.py (`test_claim_in_worktree_mode_clones_target_and_creates_worktree_in_clone`, `test_claim_in_worktree_mode_raises_claim_error_when_clone_unreachable`). Both green. Full `tests/executor tests/safety` sweep 469/469 green.
- Lint: ruff check + ruff format clean project-wide; mypy --strict on loop.py clean.
- Notes: To keep the existing worktree-mode tests in test_loop.py passing without setting up a real clone, extended the `cfg_for_repo_worktree` fixture to monkeypatch `ralph_executor.target_clone.ensure_clone` to return `TargetClone(clone_root=fake_repo)`. So `work_worktree_path(clone.clone_root, id) == work_worktree_path(fake_repo, id)` for those tests, preserving their assertions. The 2 new 7C tests override the monkeypatch in-test to a distinct clone_root and verify ensure_worktree is invoked against the clone, not `cfg.repo_path`. Also: monkeypatch ordering matters — `_pull_queue` must run BEFORE installing the no-op `ensure_worktree` stub, otherwise `git pull` errors on a non-existent queue worktree directory (NotADirectoryError on Windows). Next iteration: Step 7.8 — `iterate_once` catches `_ClaimError` and moves the offending PBI to `blocked/<id>/` with the reason appended to HISTORY.md, plus the `_move_to_blocked_with_reason` helper.

## Iteration 9 — 2026-05-28T05:00:00Z

- Step: Task 7 sub-step 7.8 (back-fill of commit 5c77800 from an earlier session). `iterate_once` now catches `_ClaimError` from `_claim_pbi` and calls `_move_to_blocked_with_reason`, which appends a "## Claim failed — <ISO>" block (carrying the failure reason) to the PBI's HISTORY.md BEFORE `git mv`, so the single commit captures both the relocation and the record. Added `move_inbox_to_blocked` movement helper mirroring `move_current_to_blocked` with `expected_state="inbox"` (the PBI is still in inbox/ at the catch site). Added `"claim_failed"` to the `IterationOutcome` literal; the catch path returns `IterationResult(outcome="claim_failed", pbi_id=...)` and skips the cycle-detector (the move is terminal, not a retry signal).
- Tests: 2 new tests in test_loop.py exercise both the legacy mode (non-github host) and worktree mode (TargetUnreachable) catch paths end-to-end through `iterate_once`, asserting blocked/<id>/ landing, inbox/ removal, HISTORY.md append, and outcome. All green.
- Lint: ruff check + ruff format clean; mypy --strict on loop.py + queue/movements.py clean.
- Notes: No code change this iteration — Task 7 fully closed. Next iteration: Task 8 — `claude_spawn` cwd + GH_OWNER per subprocess.

## Iteration 10 — 2026-05-28T05:30:00Z

- Step: Task 8 — `spawn_claude_p` (`ralph_executor/claude_spawn.py`) now sets `env["GH_OWNER"] = pbi.target_info.owner` per-subprocess when `pbi.target_info` is populated, and falls back to `pbi.work_worktree` for `cwd` when the explicit `cwd` kwarg is omitted. `iterate_once` worktree-mode call site (loop.py:641) drops the legacy `work_worktree_path(cfg.repo_path, pbi.id)` calculation + `cwd=` kwarg — the spawner now reads `pbi.work_worktree` (populated by `_claim_pbi`) directly, retiring the `# TASK 7 TODO` comment.
- Tests: 3 new tests in `test_claude_spawn.py` (`test_spawn_uses_pbi_work_worktree_as_cwd_when_set`, `test_spawn_sets_gh_owner_env_from_target_info`, `test_spawn_omits_gh_owner_when_target_info_absent`). All green. Full `tests/executor tests/safety` sweep 474/474 green (was 471 before adding the 3).
- Lint: ruff check + ruff format applied (1 reformat to test file) + mypy --strict on touched modules all clean.
- Notes: Used the existing fake-claude-binary fixture pattern (dump env / cwd to a tmp file from inside the spawned script) rather than the plan's monkeypatch-Popen pattern — keeps the new tests in line with the surrounding `test_claude_spawn.py` style. `_cleanup_work_worktree` at loop.py:411 still carries a TASK 7 TODO (computes `work_worktree_path(cfg.repo_path, pbi_id)` because it only receives `pbi_id`, not `pbi`); that is a separate, narrower fix and is not on the plan task list — leaving for follow-up. Next iteration: Task 9 — sweep injects `GH_OWNER` per pending-pr PBI when calling pr-github show.

## Iteration 11 — 2026-05-28T06:00:00Z

- Step: Task 9 — sweep injects per-PBI `GH_OWNER` + `--repo` into the three pr-github subprocess sites it owns (show.py + read_threads.py via `pr_state.fetch`, merge_pr.py via `_invoke_merge_pr`, lookup_by_branch.py via reconcile `_invoke_lookup`). New module `ralph_executor/sweep/target.py` with `read_target_info(pbi_dir)` — reads + parses `target_repo` from frontmatter, returns `TargetRepoInfo | None` (None on missing entry / no target_repo for legacy fixtures; raises `ValueError` on malformed URL for caller-mapped soft fail). New helper `_per_pbi_subprocess_overrides(target_info, ctx)` in `runner.py` computes `(env, repo_name)` — `env = {**os.environ, "GH_OWNER": info.owner}` when target present, else `(None, ctx.repo_name or queue_root.parent.name)` for legacy compat. Threaded `target_info` through `_dispatch` → `_dispatch_merge_pr`; added `env=` kwarg to `pr_state._invoke_skill` + `pr_state.fetch`; added `env=` + `repo_name=` kwargs to `_invoke_merge_pr`; added `env=` to reconcile `_invoke_lookup` and wired reconcile_orphan to compute overrides.
- Tests: 3 new tests in `tests/executor/sweep/test_runner.py`: `test_sweep_sets_gh_owner_and_repo_arg_from_pbi_target_repo` (custom capturing shim — show + read_threads record argv + GH_OWNER per invocation; asserts both override), `test_sweep_passes_per_pbi_repo_arg_to_merge_pr` (auto-merge path; merge_pr.py shim records same; asserts orgZ/repoW override), `test_sweep_reports_invalid_target_repo_url_as_error` (malformed URL → per-PBI error in result.errors, PBI not moved). Full `tests/executor tests/safety` sweep 477/477 green (was 474 before adding the 3).
- Lint: ruff format auto-applied to runner.py + target.py + test_runner.py (long-line wrap on `read_target_info`'s `close_idx` next-call). ruff check clean across `ralph_executor/sweep` + `tests/executor/sweep`. mypy --strict on `ralph_executor/sweep` clean (8 source files).
- Notes: `subprocess.run(env=)` semantic is REPLACE, not merge — passed `{**os.environ, "GH_OWNER": info.owner}` instead of `{"GH_OWNER": ...}` so the child still inherits PATH / GH_TOKEN / shim env vars. Recorded as a better-memory gotcha. `read_target_info` duplicates loop.py's `_read_target_repo_from_pbi` logic in shape but differs in contract (returns `TargetRepoInfo | None`, raises `ValueError`; loop's raises `_ClaimError` with a string) — folded into the new sweep-local module rather than shared. Reconcile orphans now also get per-target `--repo` via `_per_pbi_subprocess_overrides`. Next iteration: Task 10 — full-suite gate + smoke pass (the literal-PBI / SweepConfig grep sweep, project-wide ruff check, project-wide mypy --strict, optional self-host smoke), then ship the PR.
