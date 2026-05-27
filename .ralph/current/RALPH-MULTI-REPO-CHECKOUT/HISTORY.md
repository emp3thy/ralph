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
