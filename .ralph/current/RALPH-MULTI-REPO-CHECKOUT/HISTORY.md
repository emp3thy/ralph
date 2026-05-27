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

## Iteration 5 — 2026-05-28T02:30:00Z

- Step: Task 5 — refactor `work_worktree_path(repo_root, pbi_id)` → `(clone_root, pbi_id)`, drop legacy `repo-` prefix so path shape = `<clone_root>/.ralph-work/<pbi_id>`; add `test_work_worktree_path_takes_clone_root`; annotate the three `loop.py` callers with `# TASK 7 TODO` placeholders (still pass `cfg.repo_path` until Task 7 threads `TargetClone` through); update stale `_cleanup_work_worktree` docstring path.
- Tests: green (6/6) `uv run pytest tests/executor/test_worktree.py`; wider sweep `uv run pytest tests/executor` 390/390 green
- Lint: ruff check + ruff format --check + mypy --strict on worktree.py all clean
- Notes: Plan Step 5.3 said "function body doesn't need to change — only the parameter NAME and docstring change" but Step 5.2's verbatim test asserts `clone_root / ".ralph-work" / "PBI-XYZ"` (no `repo-` prefix), matching the PBI acceptance criterion `<clone-root>/.ralph-work/<PBI-ID>/`. Step 5.2's test is authoritative; body changes too. No existing test asserted the legacy `repo-` prefix, so symmetric path-shape change. Next iteration: Task 6 — `PBI` dataclass gains `target_repo` / `target_info` / `work_worktree` fields.

## Iteration 6 — 2026-05-28T02:45:00Z

- Step: Task 6 — `PBI` dataclass in `ralph_executor/types.py` gains three optional fields: `target_repo: str = ""`, `target_info: TargetRepoInfo | None = None`, `work_worktree: Path | None = None`; forward-ref import of `TargetRepoInfo` under `TYPE_CHECKING`; new failing-then-passing test `test_pbi_carries_target_repo_and_target_info_fields` in `tests/executor/test_types.py`.
- Tests: green (5/5) `uv run pytest tests/executor/test_types.py`; combined pbi_reader + types (18/18) green; wider sweep `uv run pytest tests/executor tests/safety` 461/461 green; `uv run mypy ralph_executor scripts skills tests` clean; `uv run ruff check .` + `uv run ruff format --check .` clean.
- Notes: Plan Step 6.1 said "append to `tests/test_pbi_reader.py` (or wherever PBI dataclass construction is exercised)" — the real `PBI` dataclass tests live in `tests/executor/test_types.py` (not `tests/test_pbi_reader.py` which only tests `scripts.pbi_reader.PBIRow`). Appended there. Defaults are required because `depends_on` already has a default and the new fields must come after. Next iteration: Task 7 — `loop._claim_pbi` multi-target refactor (sub-steps 7A/7B/7C: read target_repo from PBI → parse + host check → ensure clone + worktree). Largest task in the plan; broken into 3 sub-steps per plan's mitigation. The three `# TASK 7 TODO` markers in `loop.py` from iter-5 are the callers to thread `TargetClone` through.
