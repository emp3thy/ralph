<!-- Executor appends attempt records here. Do not delete — required by the PBI directory schema. -->

## Iteration 1 — 2026-05-28T10:15:00+00:00

- Task 0 spike: grep enumerated 28 files referencing `queue_branch`; categorised CONFIG/LOOP/CLI/TEST/OUT-OF-SCOPE (skills + docs deferred to PBI 2).
- Task 1: replaced `queue_branch` with required `queue_repo` field on `ExecutorConfig`. Validated via `parse_target_repo`. Updated `tests/executor/conftest.py` fixture, `test_config.py`, `test_config_toml.py`.
- Tests: `uv run pytest tests/executor/test_config.py tests/executor/test_config_toml.py` → 83 passed.
- Lint: `uv run ruff check` + `ruff format` clean on touched files.
- mypy on `config.py` clean; loop.py/cli.py still reference `cfg.queue_branch` — that's intentional, handled by Tasks 3–7.
- Commit: `668ccef config(executor): EXECUTOR-QUEUE-REPO-SPLIT — replace queue_branch with queue_repo`
- Next iteration: Task 2 — create `ralph_executor/queue_clone.py` with `ensure_queue_clone` + tests.

## Iteration 2 — 2026-05-28T11:00:00+00:00

- Task 2: created `ralph_executor/queue_clone.py` mirroring `target_clone.ensure_clone`. `ensure_queue_clone(workspace_root, queue_repo)` clones to `<workspace_root>/queue/` on first call; fetches + ff-only pulls `main` on subsequent calls. Raises `QueueCloneError` on any git failure (clone, fetch, or non-ff pull) with operator hint pointing at `gh auth login`.
- Tests: added `tests/executor/test_queue_clone.py` with 3 cases (first-call clones, second-call fetches + pulls new commit, bad-URL → QueueCloneError). Uses local bare git repos under tmp_path — no network required.
- `uv run pytest tests/executor/test_queue_clone.py` → 3 passed.
- Lint: `uv run ruff check` clean; `ruff format` applied.
- mypy: clean on `queue_clone.py`. Pre-existing `cfg.queue_branch` attr-defined errors in `loop.py`/`movements.py`/`cli.py` remain (intentional — Tasks 3–7 fix).
- Commit: forthcoming.
- Next iteration: Task 3 — wire `loop._pull_queue` to call `ensure_queue_clone` and remove `_ensure_on_queue_branch`.

## Iteration 3 — 2026-05-28T11:45:00+00:00

- Task 3: imported `ensure_queue_clone` into `loop.py`; replaced `_pull_queue` body with a single call to `ensure_queue_clone(cfg.workspace_root, cfg.queue_repo)`; collapsed `_queue_repo_root` to always return `cfg.workspace_root / "queue"`; deleted the `_ensure_on_queue_branch` function.
- Lifted three orphaned `_ensure_on_queue_branch(cfg)` callers (formerly in `_persist_iteration_writes`, `_run_ralph` after the push-conflict catch, and `iterate_once` after the claim cycle-detector branch) in the same commit — keeping the function would have required either a no-op stub or letting ruff fail `F821`. Plan flagged these for Task 4; pulling forward 3 line-deletions preserves a green lint gate at every commit.
- Added `test_pull_queue_calls_ensure_queue_clone` in `tests/executor/test_loop.py` (monkeypatches `loop.ensure_queue_clone`, asserts `_pull_queue(cfg)` calls it with `(workspace_root, queue_repo)`).
- Tests: `uv run pytest tests/executor/test_loop.py::test_pull_queue_calls_ensure_queue_clone tests/executor/test_queue_clone.py tests/executor/test_config.py tests/executor/test_config_toml.py` → 87 passed.
- Lint: `uv run ruff check ralph_executor/loop.py tests/executor/test_loop.py` → All checks passed. `ruff format` → no changes.
- mypy / pyright: many existing `cfg.queue_branch` attr-defined errors persist in `loop.py` — intentional; Tasks 4–7 sweep them.
- Next iteration: Task 4 — replace `cfg.queue_branch` references with `"main"` in `_persist_iteration_writes` push site (line ~305) plus the remaining `_pull_queue`-legacy lines and `queue/movements.py`.

## Iteration 4 — 2026-05-28T12:30:00+00:00

- Task 4: stripped every `cfg.queue_branch` reference inside the `_persist_iteration_writes` / `_run_ralph` push paths and the `queue/movements.py` mover.
  - `loop.py:_persist_iteration_writes` — docstring rewritten (no more worktree-vs-legacy fork), `queue_repo = _queue_repo_root(cfg)`, push to `branch="main"`, comment refreshed to point at `origin/main`.
  - `loop.py:iterate_once` — push-conflict catch block comment + log.warning purged of `cfg.queue_branch`, now references the queue repo's `main`.
  - `queue/movements.py` — module docstring rewritten (no branch-switch step), `_queue_repo` simplified to `cfg.workspace_root / "queue"` (dropped `queue_worktree_path` import), legacy `if not cfg.use_worktrees: git_ops.checkout(...)` block deleted, push to `branch="main"`, comments updated.
- Lines 565 + 588 in `_claim_pbi` / `_claim_pbi_worktree` still hold `cfg.queue_branch` — intentional, Task 5 owns those (legacy claim path deletion + worktree-branch arg removal).
- Tests: `uv run pytest tests/executor/test_loop.py::test_pull_queue_calls_ensure_queue_clone tests/executor/test_queue_clone.py tests/executor/test_config.py tests/executor/test_config_toml.py -q` → 87 passed. `tests/executor/test_movements.py` fails because `cfg_for_repo` fixture still points `workspace_root` at the real `~/ralph-workspaces` instead of a tmp dir (and the legacy `cfg.repo_path` queue-on-branch model). Per plan Task 4 Step 2, those failures are explicitly deferred to Task 9 (conftest fixture sweep) — not a regression of this commit.
- Lint: `uv run ruff check ralph_executor/loop.py ralph_executor/queue/movements.py` → clean. `ruff format` → no changes.
- mypy / pyright on touched files: only the Task-5-scoped `cfg.queue_branch` attr-defined errors at L565/L588 remain; all `_persist`/`_move`/`iterate_once` references are gone.
- Next iteration: Task 5 — drop the legacy `use_worktrees=False` branch out of `_claim_pbi` and the queue-worktree `ensure_worktree` call in `_claim_pbi_worktree`; add the `use_worktrees=False` rejection to `load_config`.

## Iteration 5 — 2026-05-28T13:15:00+00:00

- Task 5: completed worktree-only claim path + `use_worktrees=False` rejection.
  - `config.py:load_config` — added `if not use_worktrees: raise ConfigError(...)` immediately after the `_resolve_bool` for `use_worktrees`. Message names the migration (single-checkout branch-dance gone; queue is its own clone). Both env-var (`RALPH_USE_WORKTREES=false`) and TOML (`use_worktrees = false`) paths now raise.
  - `loop.py:_claim_pbi` — deleted the `cfg.use_worktrees` if/else fork; the function now always delegates to `_claim_pbi_worktree` after the multi-target prelude. Removed the legacy `event_log = open_log(...) / _pull_main / branch checkout / checkout(cfg.queue_branch)` block (≈20 LOC). Docstring rewritten to drop the "Legacy single-checkout mode" section.
  - `loop.py:_claim_pbi_worktree` — deleted `queue_wt = queue_worktree_path(cfg.repo_path)` + `ensure_worktree(...queue_branch)` (the queue clone IS the working tree now; `_pull_queue → ensure_queue_clone` materialises it earlier in the iteration). Docstring updated.
  - Dead-code sweep: removed `_pull_main`, `_switch_to_feature_branch` (only callers were the deleted legacy branch). Dropped the now-unused `queue_worktree_path` import. `_run_ralph` spawn site swapped `queue_worktree_path(cfg.repo_path) / ".ralph"` → `_queue_repo_root(cfg) / ".ralph"` so Claude reads its PBI dir from the queue clone, not the obsolete `.ralph-work/queue/` worktree. Updated `_run_ralph` and `iterate_once` docstrings to drop the `cfg.queue_branch` / "working tree on ralph-queue" framing; refreshed the `_run_sweep` comment that still mentioned `cfg.use_worktrees`.
- Tests added: `test_load_config_use_worktrees_env_false_rejected`, `test_load_config_use_worktrees_toml_false_rejected` in `tests/executor/test_config.py`. Existing `test_load_config_use_worktrees_env_false` was repurposed (renamed + asserts ConfigError).
- `uv run pytest tests/executor/test_config.py -v` → 22 passed.
- `uv run pytest tests/executor/ -k "claim or pbi" -q` → 14 failed / 46 passed. All failures are the conftest `cfg_for_repo` fixture (`use_worktrees=False`, `workspace_root=~/ralph-workspaces`) — explicitly deferred to Task 9 (fixture sweep) per the plan's Task 5 Step 4 note.
- Lint: `uv run ruff check ralph_executor/loop.py ralph_executor/config.py tests/executor/test_config.py` clean; `ruff format` reformatted `tests/executor/test_config.py` (string-arg layout, no semantic change).
- mypy: `ralph_executor/loop.py` + `ralph_executor/config.py` clean. `cli.py:516` still holds the legacy `cfg.queue_branch` log argument — intentional, Task 7 owns the CLI rework.
- Commit: forthcoming (`fix(loop,config): EXECUTOR-QUEUE-REPO-SPLIT — _claim_pbi worktree-only; drop legacy single-checkout mode`).
- Next iteration: Task 6 — sweep `cfg.queue_branch` literal in `queue/movements.py` (one remaining call site if not already covered by Task 4) and confirm the rest of the file is `"main"`-only.

## Iteration 6 — 2026-05-28T14:00:00+00:00

- Task 6 verification: `grep -n "queue_branch" ralph_executor/queue/movements.py` returns zero hits. Work was rolled into Iteration 4's commit `a74e14d` (the `_persist_iteration_writes` / `iterate_once` / `queue/movements.py` push-target sweep). Plan boxes for Task 6 Steps 1–4 ticked in `docs/superpowers/plans/2026-05-28-executor-queue-repo-split-plan.md`; added a note pointing at the Iter-4 commit.
- Task 7a: created `ralph_executor/migrate_queue.py` exposing `MigrateQueueError` + `copy_queue_tree_filtered(source, dest) -> dict[str, int]`. Helper iterates `_STATE_DIRS = ("inbox", "current", "pending-pr", "blocked", "archive")` — `done/` and `state/` are simply not in the list and are therefore skipped. Drops `META-cycle-*.md` sentinels from `blocked/`. Copies `.ralph/config.toml` if present. Writes a skeleton `.gitignore` (`.ralph/state/\n`) at the dest root.
- Test: `tests/executor/test_migrate_queue.py::test_copy_filtered_excludes_done` — seeds an `inbox/WI-1`, `blocked/WI-9` + META sentinel, `done/WI-old`, `state/events.db`, `config.toml`; asserts done/state/META excluded, inbox/blocked/config kept, counts == {inbox:1, current:0, pending-pr:0, blocked:1, archive:0}. Module docstring annotates that `main` + `_target_is_empty` arrive in Task 7b.
- `uv run pytest tests/executor/test_migrate_queue.py -v` → 1 passed.
- Lint: `uv run ruff check` + `ruff format --check` clean on both new files.
- mypy on `ralph_executor/migrate_queue.py` → only error is the pre-existing `cli.py:516 cfg.queue_branch` attr-defined — Task 7 owns the CLI rework. `migrate_queue.py` itself has zero errors.
- Commit: forthcoming (`feat(migrate-queue): EXECUTOR-QUEUE-REPO-SPLIT — copy_queue_tree_filtered helper`).
- Next iteration: Task 7b — add `_target_is_empty` + `main(argv)` to `migrate_queue.py`, wire `migrate-queue` subcommand + `--queue-repo` top-level flag into `cli.py`, add the two integration tests (empty-target push smoke + non-empty-target refusal).

## Iteration 7 — 2026-05-28T14:45:00+00:00

- Task 7b: completed the `migrate-queue` subcommand end-to-end.
  - `ralph_executor/migrate_queue.py` — added `_run_git`, `_target_is_empty(target_url)` (via `git ls-remote --heads`; returncode != 0 raises `MigrateQueueError` with `gh auth login` hint), `_build_parser()` (argparse with required `--source`/`--target`), and `main(argv)` doing: validate source has `.ralph/inbox/` → assert target empty → `tempfile.TemporaryDirectory` stage → `copy_queue_tree_filtered(src, stage)` → `git init --initial-branch=main` / `add .` / `commit` / `remote add origin` / `push origin main` via `_run_git` with `-c user.email/name=ralph@local/ralph` (no host git identity required). Success print summary lists per-state PBI counts plus the three operator follow-up commands (TOML, branch delete, worktree remove).
  - `ralph_executor/cli.py` — added `--queue-repo URL` top-level override flag; added `migrate-queue` subparser (`--source` / `--target`); wired dispatch in `main()` that catches `MigrateQueueError` and returns exit 2 with a clean `error: …` line. Updated startup log line from `cfg.queue_branch` → `cfg.queue_repo` (the last legacy attr-defined reference; mypy on `cli.py` now clean). `_apply_overrides` now also propagates the optional `args.queue_repo` into the cfg via `dataclasses.replace`.
- Tests: extended `tests/executor/test_migrate_queue.py` with `test_migrate_main_pushes_to_empty_target` (full smoke: source → bare remote → clone-and-verify the inbox/blocked PBIs plus the skeleton `.gitignore` survive the round trip, and the `done/` + META sentinels are absent), `test_migrate_refuses_nonempty_target` (seeded bare remote → `MigrateQueueError("not empty")`), and `test_migrate_rejects_missing_source_inbox` (source lacking `.ralph/inbox/` → `MigrateQueueError("inbox")`). Extended `tests/executor/test_cli.py` with `test_main_migrate_queue_dispatches_to_module` (forwards `--source`/`--target` verbatim), `test_main_migrate_queue_surfaces_errors` (MigrateQueueError → exit 2), and `test_main_queue_repo_flag_overrides_cfg` (`--queue-repo X` reaches the spawned iteration's `cfg.queue_repo`).
- `uv run pytest tests/executor/test_migrate_queue.py tests/executor/test_cli.py -v` → 29 passed.
- Lint: `uv run ruff check` clean after `--fix` (ruff split the migrate-test import block and minor format adjustments). `uv run ruff format` reformatted the four touched files (whitespace only).
- mypy: `uv run mypy ralph_executor/migrate_queue.py ralph_executor/cli.py` → no issues found in 2 source files.
- `uv run ralph-executor migrate-queue --help` and top-level `--help` both render the new flags/subcommand.
- Commit: forthcoming (`feat(cli): EXECUTOR-QUEUE-REPO-SPLIT — migrate-queue subcommand + --queue-repo flag`).
- Next iteration: Task 8 — `setup_cmds.init` prompts for `queue_repo` (after `workspace_root`) with smoke-clone validation; update the `CONFIG_TOML_STUB` queue-branch comment to a `queue_repo` example.

## Iteration 8 — 2026-05-28T15:30:00+00:00

- Task 8: wired `queue_repo` into the `init` flow + extended `user_config` to persist it alongside `ralph_home`.
  - `ralph_executor/user_config.py` — added `read_queue_repo() -> str | None`; refactored `write_ralph_home` onto a new `_write_user_config(updates)` merge helper so subsequent writes preserve sibling keys (previously a `write_ralph_home` after a `write_queue_repo` would clobber `queue_repo`); added `write_queue_repo(url)`. Helper `_render_toml_value` covers the scalar types user_config persists (string / bool / int / float).
  - `ralph_executor/setup_cmds.py` — new `_prompt_queue_repo()` (no default; reprompts on empty / invalid URL via `parse_target_repo`; returns `None` on `EOFError` so closed stdin warns instead of crashing) and `_smoke_clone_queue_repo(url)` (timeout-bounded `git ls-remote --heads`; False on non-zero exit / `git` missing / timeout). `cmd_init` now: writes `ralph_home`, then handles `queue_repo` (already-set → idempotent print; `assume_yes` → warning to set manually; otherwise prompt → smoke → write; smoke failure is a WARNING never a blocker). Refactored the `ralph_home` early-return into an if/else so the `queue_repo` block always runs.
  - `CONFIG_TOML_STUB` — replaced the legacy `# queue_branch = "ralph-queue"` line with a `queue_repo = "https://github.com/<owner>/<name>-queue"` example + a note pointing at `~/.ralph/config.toml` as the actual home for the value.
- Tests: added 6 cmd_init cases to `tests/executor/test_setup_cmds.py` (happy path, reprompt on invalid URL, smoke-clone failure warns + still writes, `--yes` warning, already-set skip — boom-on-input-call to prove no reprompt, closed-stdin EOFError handling). Added module-level autouse fixture `_default_closed_stdin` so existing cmd_init tests that don't explicitly drive the new prompt fall through deterministically (input → EOFError → warning → continue) instead of blocking on real stdin. Added `_smoke_ok` fixture for tests that want a guaranteed-pass smoke without touching the network.
- `uv run pytest tests/executor/test_setup_cmds.py tests/executor/test_user_config.py -v` → 40 passed.
- Lint: `uv run ruff check .` clean. `ruff format` reformatted `ralph_executor/user_config.py` (one long-line wrap).
- mypy: `uv run mypy ralph_executor/setup_cmds.py ralph_executor/user_config.py` → no issues. Full `uv run mypy ralph_executor scripts skills tests` surfaces only the three pre-existing errors deferred to Task 9 (two `queue_branch=` fixture call-args in `tests/safety/test_integration_loop.py` + `tests/safety/test_cycle_detector.py`, plus the unrelated `test_claude_spawn.py:856` Popen-vs-_FakeProc overlap check). All three are documented in earlier iterations as Task-9 / out-of-scope.
- Full pytest suite: 75 fail / 796 pass / 4 skipped — same set of failures HISTORY iter-4/5 deferred to Task 9 (the `cfg_for_repo` fixture still points `workspace_root` at the real `~/ralph-workspaces`, so `_pull_queue → ensure_queue_clone` tries to clone over a non-empty path). Not regressions from this iteration; explicitly Task 9's sweep work.
- Gap noted (out of Task 8 scope): `load_config` still reads `queue_repo` from `<repo>/.ralph/config.toml`, but `cmd_init` now writes to `~/.ralph/config.toml` per spec. Bridging the read path to user_config (per spec §Executor: "TOML key in `~/.ralph/config.toml`") is a follow-up — needs `load_config` to fall back to `read_queue_repo()` when the per-repo TOML lacks the key, plus a test-fixture sweep. Flagging for Task 9 or a follow-up PBI; the operator gate in PBI.md ("Add `queue_repo` to `~/.ralph/config.toml`. Restart") will not work end-to-end until that fix lands.
- Commit: forthcoming (`feat(init): EXECUTOR-QUEUE-REPO-SPLIT — prompt + smoke clone for queue_repo`).
- Next iteration: Task 9 — sweep remaining `queue_branch` references in tests; fix `cfg_for_repo` to use tmp_path workspace; bridge `load_config` to read `queue_repo` from user_config so the operator gate works.
