---
id: EXECUTOR-PERMISSION-MODE-SCOPED
type: feature
status: current
severity: normal
attempts: 0
created_at: 2026-05-27T18:55:00+00:00
updated_at: 2026-05-27T22:15:53+00:00
depends_on: []
target_repo: https://github.com/emp3thy/ralph
---

# Scope permission-mode to ralph-executor's Claude subprocess

## Problem

`ralph-executor` currently inherits whatever `defaultMode` the host's `~/.claude/settings.json` has. The ROSA packaging work (STAGE-B-PLAN-12) requires writing `.claude/settings.json` containing `dangerouslySkipPermissions=true`, which the auto-mode safety classifier refuses to Write. As an operator workaround, the host's global `defaultMode` was switched from `"auto"` to `"bypassPermissions"` so the executor's Claude subprocess bypasses the classifier.

That fix is **too broad**: every interactive Claude session in every project on this host now runs without the classifier. The scope should be limited to the executor's spawned subprocesses.

## What to do

Make `ralph-executor` pass an explicit permission-mode argument (or env var) when it spawns Claude, instead of relying on the host's global default. Then revert `~/.claude/settings.json` `defaultMode` to `"auto"` (and re-enable `skipAutoPermissionPrompt` / `skipDangerousModePermissionPrompt` to their prior values).

Recommended approach:
1. Locate the spawn site in `ralph_executor/` where the `claude` CLI is invoked (likely a `subprocess.run(["claude", "-p", ...])` call).
2. Add a `--permission-mode bypassPermissions` flag to the argv (verify the exact flag name against `claude --help` — the env variable form `CLAUDE_PERMISSION_MODE` may be cleaner if it exists).
3. Surface the chosen mode through `ExecutorConfig` (default: `"bypassPermissions"`) so operators can override it via TOML if they want stricter behavior for a particular run.
4. Add a unit test that asserts the spawn argv contains the expected `--permission-mode` value.
5. Update the host operator docs (`README.md` and / or `docs/superpowers/` ops guides) noting the executor now sets its own permission mode and the host's global `defaultMode` no longer needs to be relaxed.

## Acceptance criteria

- The `claude` subprocess spawned by `ralph_executor` runs with `permissionMode=bypassPermissions` regardless of the host's global `defaultMode`.
- `ExecutorConfig` exposes the permission-mode value (with a sensible default) so operators can override it without code changes.
- Unit test asserts the argv (or env) carries the expected mode.
- Operator docs updated.
- Plan-defined tests pass: `uv run pytest`
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy ralph_executor scripts skills tests` clean
- PR opened against `main` from `ralph/EXECUTOR-PERMISSION-MODE-SCOPED`
