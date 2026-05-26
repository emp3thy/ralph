<!-- Executor appends attempt records here. Do not delete -->

## Iteration 2 — 2026-05-27 — Task 1 fix + Task 2 schema

- Task 1 follow-up: added missing `skills/ralph-add/scripts/__init__.py` and
  `skills/workitem-fetch-github/scripts/__init__.py` package markers (commit
  `8a6a21c`). Prior iteration's `ec6372d` shipped Task 1 SKILL.md files but
  skipped the `__init__.py` files from plan Steps 4 + 5.
- Task 2: wrote `skills/workitem-fetch-github/scripts/schema.py` with
  `WorkItemJson` / `AttachmentJson` TypedDicts, `REQUIRED_KEYS`,
  `ALLOWED_TYPES` / `ALLOWED_SEVERITIES` / `ALLOWED_HOSTS`, and `validate()`
  (commit `41b5002`).
- Lint: `uv run ruff check skills/workitem-fetch-github/scripts/schema.py`
  green (one import-sort fix applied automatically).
- Format: `uv run ruff format --check .` clean (63 files).
- Type-check: `uv run mypy skills/workitem-fetch-github/scripts/schema.py`
  clean.
- Prereq smoke (Task 1 Step 1):
  `uv run pytest tests/test_workspace_samples.py tests/test_gh_client.py tests/test_setup_ralph_queue_github.py -q`
  → 38 passed.
- Notes: Plan 3 has 8 tasks in Phase 1; remaining: Task 3 (failing test for
  GitHub fetcher), Task 4 (implement GitHub fetcher), Task 5 (failing
  orchestrator test), Task 6 (implement orchestrator), Task 7 (cross-validate
  PBIs against workspace validator), Task 8 (full toolchain pass + PR). Next
  iteration: Task 3.

## Iteration 3 — 2026-05-27 — Tasks 3+4 GitHub fetcher (red → green)

- Task 3: wrote `tests/skills/test_workitem_fetch_github.py` (5 tests, all
  using `responses` to mock GitHub REST). Red step confirmed:
  `AssertionError: missing fetcher script at ...fetch.py`.
- Task 4: wrote `skills/workitem-fetch-github/scripts/fetch.py`. Host-pure
  GitHub fetcher: argparse CLI; resolves GH_TOKEN + repo (`--repo owner/name`
  or `GH_OWNER` + `--repo-name`); supports `#42` / `42` / `WI-42` work-item
  forms; calls `scripts.gh_client.GhClient.get` for the issue; classifies
  PBI type from `bug` / `enhancement` labels; severity from
  `priority/*` / `severity/*` labels; extracts `## Acceptance criteria` and
  `## Reproduce` sections via regex; downloads markdown image attachments
  via `client._session.get` and base64-encodes; runs `schema.validate`
  before emit; exits 2 on `_FatalError` or `GhError`. Both tests + impl
  shipped in one TDD commit per plan Task 4 Step 4 (`0ba2fa3`).
- Tests: `uv run pytest tests/skills/test_workitem_fetch_github.py -v`
  → 5/5 passed. Full `uv run pytest tests/skills/ -v` → 52/52 passed.
- Lint: `uv run ruff check .` → All checks passed.
- Format: `uv run ruff format --check .` → 65 files clean (one auto-format
  applied during iteration on the two new files).
- Type-check scoped: `uv run mypy skills/workitem-fetch-github/scripts/fetch.py
  tests/skills/test_workitem_fetch_github.py` → no issues.
- Type-check full project: `uv run mypy ralph_executor scripts skills tests`
  FAILS with `Duplicate module named "scripts" (also at "scripts\__init__.py")`
  caused by `skills/ralph-add/scripts/__init__.py` colliding with the
  top-level `scripts/` package. Pre-existing issue introduced by Task 1's
  `pyproject.toml` change adding `skills` to `[tool.mypy].files`. Needs
  `explicit_package_bases = true` or `namespace_packages = true` (or
  exclude pattern) in `[tool.mypy]`. Park for Task 8 (full toolchain pass).
- Notes: Plan 3 remaining: Task 5 (failing orchestrator test), Task 6
  (implement orchestrator), Task 7 (cross-validate PBIs against workspace
  validator), Task 8 (fix mypy duplicate-module + full toolchain + PR).
  Next iteration: Task 5.

## Iteration 4 — 2026-05-27 — Tasks 5+6 orchestrator (red → green) [committed without HISTORY update]

- Task 5: wrote `tests/skills/test_ralph_add.py` (11 tests using a tmp-path
  mock fetcher; bare git repo + working clone + `ralph-queue` branch
  fixture). Red step confirmed via `AssertionError: missing entry script
  at ...add.py`.
- Task 6: wrote `skills/ralph-add/scripts/add.py` (commit `67ad77d`).
  Host-agnostic orchestrator: argparse CLI; resolves fetcher via
  `RALPH_WORKITEM_FETCH_SCRIPT` → `~/.claude/skills/workitem-fetch/...`
  → `RALPH_GIT_HOST`-keyed dev fallback; invokes fetcher with
  `subprocess.run`; parses + schema-validates JSON; writes PBI under
  `.ralph/inbox/<WI-id>/` with frontmatter + body (PBI.md for feature,
  BUG.md + REPRODUCE.md for bug) + PLAN.md (feature only) + HISTORY.md
  + attachments/<name>; supports `--expand-children`,
  `--severity` override, `--dry-run`, `--no-push`, `--branch`;
  `--via-mcp` and `--repo-url` exit 2 in v1; commits + pushes via
  `subprocess.run([git, ...])`.
- Tests + impl shipped in one TDD commit per plan Task 6 Step 4.
- HISTORY.md was not updated by Iteration 4 itself (executor's
  `_persist_iteration_writes` ran on a clean tree and was a no-op).
  Iteration 5 below records both Iter 4 and Iter 5 work.

## Iteration 5 — 2026-05-27 — Tasks 7+8 validator test + toolchain + PR

- Task 7: appended `test_generated_pbi_passes_plan1_validator` to
  `tests/skills/test_ralph_add.py`. Mirrors the produced
  `WI-<n>` directory into a `feature-WI-<n>` sibling and runs
  `scripts.validate_samples.validate_sample`; asserts empty error list.
  Docstring rewritten to reflect reconciliation #3 (validator derives
  type from frontmatter, not directory prefix) rather than the plan's
  stale claim that the prefix is required. Commit `426a7b0`.
- Task 8: resolved the mypy duplicate-module collision parked in
  Iter 3 by adding an `exclude` regex `skills/[^/]+/scripts/__init__\.py$`
  to `[tool.mypy]` in `pyproject.toml`. The inner `scripts/__init__.py`
  markers are decorative — directory names contain hyphens (`ralph-add`,
  `workitem-fetch-github`) so the inner packages aren't importable via
  dotted path anyway (tests use `importlib.util.spec_from_file_location`).
  Commit `d5abe9e`.
- Tests: `uv run pytest` → 394 passed, 2 skipped (opt-in
  `RALPH_PROMPT_SMOKE`).
- Lint: `uv run ruff check .` → All checks passed!
- Format: `uv run ruff format --check .` → 67 files already formatted.
- Type-check: `uv run mypy ralph_executor scripts skills tests` →
  Success: no issues found in 65 source files.
- Help text: both `ralph-add --help` and
  `workitem-fetch-github --help` list every flag the plan documents.
- PR created via `pr` skill: `#25` —
  https://github.com/emp3thy/ralph/pull/25, branch
  `ralph/STAGE-B-PLAN-03` → `main`, title
  "STAGE-B-PLAN-03: ralph-add skill + workitem-fetch-github (Phase 1)".
- Notes: all 8 Phase 1 plan tasks complete. Phase 2 (ADO fetcher) is
  documented in the plan and deferred to a follow-up PBI. PR shipped;
  PBI ready to move from `current/` to `pending-pr/`.

## Iteration 5 — 2026-05-27 — PR created

- PR: #25
- Branch: ralph/STAGE-B-PLAN-03
- Title: STAGE-B-PLAN-03: ralph-add skill + workitem-fetch-github (Phase 1)
