# Queue-driven cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every operator-facing surface for manually picking a target repo; make queue PBI the single source of truth.

**Architecture:** The executor is queue-driven. The operator configures one root (`workspace_root`) plus the queue repo URL and branch in `~/.ralph/config.toml`. Every iteration the loop reads the next PBI's `target_repo` URL from frontmatter, clones (or fetches) that repo under `<workspace_root>/clones/<owner>/<name>/`, and runs Claude inside a per-PBI worktree. `ExecutorConfig.repo_path`, the `--repo` / `--workspace` / `--ralph-home` flags, the `$RALPH_HOME` / `$RALPH_REPO_PATH` env vars, the `ralph_home` TOML key, and the entire `ralph-executor scaffold` subcommand are deleted.

**Tech Stack:** Python 3.12, pytest, ruff, mypy. uv for venv. PowerShell for the start-ralph script.

## Memory-derived guardrails (from better-memory)

Two high-confidence reflections shape execution of this plan. Treat them as
mandatory; the executing agent must apply them per task, not defer to a
follow-up.

### Guardrail A — `[[keep-docs-in-sync]]` — confidence 0.9, evidence 5

Source: reflection `98056ebc80464d34a0c2e95b18882e41` ("Keep website and
README in sync with every code change"). Useful_count 4.

**Rule:** When a task changes an env var, config field, CLI flag, or any
module-level concept that is enumerated in a module docstring or README
section, the docstring + README change ships in the **same commit** as
the code change. Deferring to a docs-only later task is the smell this
reflection warns about.

**How this plan is structured to honour it:**

- Tasks 1, 2, 5, 6 (refactors that don't touch enumerated config surface)
  ship with code-only commits.
- Task 7 (delete `--repo` / `--workspace` flags) **must** also update
  the `cli.py` module docstring's flag enumeration (lines 5-30) in the
  same commit. The plan's Task 7 implementation step already touches
  the parser; extend it to the docstring.
- Task 8 (delete `repo_path` field + `_resolve_repo_path`) **must**
  rewrite the `config.py` module docstring in the same commit. Plan
  already says so — verify before commit.
- Task 9 (delete `$RALPH_HOME` / `$RALPH_REPO_PATH` env reads and
  rewrite `cmd_init`) **must** update the module docstrings of
  `config.py`, `cli.py`, `setup_cmds.py` in the same commit. Plan
  already says so — verify before commit.
- Task 11 (delete `read_ralph_home` / `write_ralph_home`) **must**
  update the `user_config.py` module docstring's knob enumeration
  (lines 1-20) in the same commit. Plan already says so — verify
  before commit.
- Tasks 13–16 (README + runbook updates) honour the second half of the
  reflection: "When REWRITING a doc line/block, verify every factual
  token in the new text — column names, function names, flags — against
  the source; do not carry pre-existing tokens forward unchecked." The
  executing agent must read the current source as it rewrites each
  table row, not paraphrase from memory.

**Per-task acceptance check:** before any commit in Tasks 7–11, run:

```bash
git diff --stat HEAD | grep -E 'config\.py|cli\.py|setup_cmds\.py|user_config\.py'
```

If the touched source file's module docstring did not change in this
commit but the commit modifies an enumerated config surface, the commit
is wrong — amend before pushing.

### Guardrail B — `[[root-cause-only]]` — confidence 1.0, evidence 1, useful 10

Source: reflection `6aa6bf51e7e94a4a931358605521aed6` ("Prioritize Root
Cause Fixing").

**Rule:** No compatibility shims. No "keep the old reader so existing
TOMLs still work" code. The user is the sole operator; they have
accepted the WARN-on-stale-key migration path. If the executing agent
finds an opportunity to "soften" a deletion (e.g., keep `--repo` as a
deprecated no-op flag), reject it — the spec mandates hard removal.

**Lower-confidence reflections reviewed and dismissed for this plan:**

| Reflection | Conf | Why dismissed |
|---|---|---|
| `0da0e9ff` — PBI HISTORY checkout pattern | 0.6 | Touches the executor loop but not the codepaths this plan changes |
| `85e7ec84` — mkstemp + fdopen leak | 0.6 | No temp-file code in this plan |
| `0352fc86` — Document fail-fast ordering | 0.55 | No composed guards introduced |
| `228dfa6d` — Enter/exit freeze logging | 0.55 | No hang debugging in scope |
| `0c83e25d` — Playwright has_text vs CSS | 0.8 | No Playwright tests |

### Guardrail C — `[[per-task-grep-sweep]]` — pattern repeated 4x in ralph history

Sources: observations `9664ab77` (workflow, success), `64ac62fb` (gotcha,
neutral), `94f87716` (planning, failure), `cc90af88` (gotcha, failure).

**Rule:** After every deletion task (T7, T8, T9, T10, T11), run a repo-wide
grep for the symbol(s) just deleted BEFORE committing. Treat stale
references in `docs/`, `skills/`, `prompt/`, and `tests/` as **in-scope
for the current task**, not "follow up". Plan task tables that enumerate
specific files by line number routinely miss indirect callers; the grep
sweep is the safety net.

**Per-task acceptance check** — after the failing-test → impl → passing-test
loop, before the commit:

```bash
# For each symbol the task deleted (function name, flag name, env var name):
git grep -n '<symbol>' -- docs skills prompt tests README.md
```

Any hit not already covered by this commit must be folded in. Concrete
prior misses on the ralph project:

- T6 of skills-queue-clone-migration left `tests/skills/test_supervisor_skills_smoke.py` red because the plan listed per-skill test files but missed the cross-cutting smoke file.
- T8 of the same plan rewrote two README sections per plan verbatim; the four sections the plan didn't enumerate stayed contradictory.
- T5 of executor-queue-repo-split enumerated `cfg.queue_branch` line numbers but missed `queue_worktree_path(cfg.repo_path)` — different helper, same migration.

### Guardrail D — `[[verify-plan-symbols-against-source]]` — pattern: spec drift in plans

Source: observation `3c8e89a6` (cli, failure). Quote: "plan referenced
`cfg.bot_author_email` (doesn't exist), `cfg.stale_days` (doesn't exist),
and `prepare_host_environment(cfg)` with the wrong signature."

**Rule:** This plan was drafted by a subagent from the spec + audit. Before
implementing any task, the executing agent re-greps the names the plan
references (function names, fields, flags, line numbers) against the
current source. If a reference is stale, fix the plan in-place AND log
the correction in the commit message.

**Per-task pre-check** — at the start of each task:

```bash
# For every symbol the task expects to find (Files: block + step text):
git grep -n '<symbol>' -- ralph_executor tests
```

Verify the symbol exists where the plan says it does. If the plan
references a line number, confirm the symbol is still on that line
(line numbers drift between drafting and execution).

### Guardrail E — `[[pull-forward-dangling-references]]` — pattern: ruff F821 mid-PR

Source: observation `8dc16bd7` (workflow, success). Pattern: "Task N
deletes a symbol whose callers Task N+1 promises to clean up — ruff F821
breaks the lint gate at the Task N commit."

**Rule:** After each task's commit, run `uv run ruff check .` AND
`uv run mypy ralph_executor scripts tests`. If either errors with
`F821` / `attr-defined` because a later task is supposed to clean up
the calling site, pull that cleanup forward into the current commit.
This is not scope creep — it is keeping the lint gate green at every
commit, which is what CI demands.

**Per-task acceptance check** — between impl and commit:

```bash
uv run ruff check .
uv run mypy ralph_executor scripts tests
```

Both must pass. If they don't and the failure traces to a symbol the
plan said a later task would handle, pull the line-deletes forward into
this commit.

### Guardrail F — `[[multi-item-files-block-cross-check]]` — pattern: partial fulfilment

Source: observation `a1d1c696` (gap, failure). Pattern: "Task 13 said
to seed BOTH `.gitkeep` AND `.ralph/config.toml`; implementer landed
only `.gitkeep`. When a step enumerates a list, cross-check each item
against the diff before marking complete."

**Rule:** For any task whose `**Files:**` block lists multiple paths,
or whose Steps include a list of artefacts, the executing agent runs
this final cross-check immediately before commit:

```bash
git diff --stat HEAD | awk '{print $1}' | sort > /tmp/touched.txt
# Compare /tmp/touched.txt against the Files: block manually
```

Every path in the `**Files:**` block must appear in the diff, OR the
agent must explicitly note in the commit message why a path was a
no-op (already-correct, deleted-by-earlier-task, etc.).

### Guardrail G — `[[datetime-UTC-not-timezone]]` — pattern: ruff UP017

Source: observation `d9036e8e` (gotcha, failure). Quote: "Canonical
plan code that imports `from datetime import timezone` and uses
`timezone.utc` fails ruff UP017 under py3.12 — autofix wants
`from datetime import UTC` + `tz=UTC`."

**Rule:** Any test or implementation code in this plan that contains
`from datetime import timezone` or `timezone.utc` must be rewritten to
`from datetime import UTC` + `tz=UTC` before applying. Mass-substitute
at paste time.

**Cross-check at task start:**

```bash
grep -nE '(from datetime import timezone|timezone\.utc)' \
  docs/superpowers/plans/2026-05-31-queue-driven-cleanup.md
```

Any hits inside a code block: rewrite before pasting into the source
file.

## Per-task confidence ratings

Per `standards/ralph-runtime.md`: every task gets a confidence percentage.
Sub-90% tasks must be lifted to ≥90% via in-task mitigations, OR surfaced
as an explicit user-input checkpoint.

| # | Task | Base | After guardrails | Mitigation source |
|---|---|---|---|---|
| 1 | Stale `ralph_home` WARN | 95% | 95% | — |
| 2 | Per-iteration project-TOML WARN | 90% | 90% | — |
| 3 | Refactor `_pr_skill_scripts_path` | 85% | **92%** | Guardrail D pre-check greps for symbol |
| 4 | Refactor `_cmd_reconcile` + startup log | 85% | **92%** | Guardrail D pre-check |
| 5 | Refactor `claude_spawn.py` | 80% | **90%** | Guardrail D + audit of `work_worktree` set-sites before removing the fallback |
| 6 | Refactor `loop._run_ralph` + `_run_sweep_step` | 85% | **92%** | Guardrail D pre-check |
| 7 | Drop CLI flags + helpers | 90% | 90% | — |
| 8 | Delete `repo_path` field | 75% | **92%** | Guardrail D (grep `\.repo_path\W` repo-wide before delete) + Guardrail E (ruff/mypy gate per commit) |
| 9 | Delete env reads + rewrite `cmd_init` | 80% | **90%** | Plan body specifies `write_workspace_root(path: Path) -> Path` signature; matches existing `write_queue_repo` pattern |
| 10 | Delete `scaffold` subcommand | 85% | **92%** | Guardrail F (Files: cross-check ensures all 8 helpers removed together) |
| 11 | Delete `read_ralph_home` / `write_ralph_home` | 95% | 95% | T1's WARN helper inlines the TOML read; does not call `read_ralph_home` — so T11's deletion is safe |
| 12 | ~~Multi-target integration test~~ | — | **DROPPED** | Coverage already provided by T3-T6 + existing single-iteration tests |
| 13 | Update `README.md` | 75% | **92%** | Guardrail A (docs in same commit as code) + factual-token re-verification |
| 14 | Update `ralph-setup.md` | 75% | **92%** | Guardrail A + re-read source per section |
| 15 | ~~Update `ralph-architecture.md`~~ | — | **DROPPED** | Architecture-diagram redesign deferred to follow-up doc PR (user choice) |
| 16 | Update `ralph-queue-setup.md` + `start-ralph.ps1` | 90% | 90% | — |
| 17 | Final smoke test | 80% | **92%** | Plan body includes the queue-setup commands needed to stage 2 PBIs |

## Assumptions surfaced (3-bucket review)

### Real concerns (need user input)

**None remain.** RC-1 (Task 12 fixture design) and RC-2 (Task 15 diagram
redesign) were both resolved during plan review on 2026-05-31:

- RC-1: Task 12 dropped — multi-target coverage already exists via T3-T6 + single-iteration tests + T17 smoke.
- RC-2: Task 15 dropped — architecture-diagram redesign deferred to a separate doc PR.

### Verified safe (no concerns)

- **Project TOML detection** (T2) — `<clone>/.ralph/config.toml` path is read-only; permission errors already suppressed per spec.
- **Stale `ralph_home` WARN** (T1) — inlining the TOML read makes T11 deletion independent.
- **CLI flag deletion** (T7, T9, T10) — argparse emits unrecognized-argument exit 2 for removed flags; existing tests for accepted flags continue to pass.
- **Conventional commit messages** — every task ends with one; format matches existing repo history.
- **No backwards-compat shim** — hard-remove per spec; aligns with Guardrail B.

### Minor / accepted

- Operator-skill `--workspace PATH` flags under `skills/ralph-*/scripts/` are intentionally preserved (different concept — accepts a path, not a name).
- `write_workspace_root` helper name is unspecified in the spec; chosen for symmetry with `write_queue_repo` / `write_queue_branch`.
- `<workspace_root>/clones/queue/` and `<workspace_root>/clones/clones/` are disallowed by directory convention; no validation added (would be over-engineering).
- Module docstring updates land in the same commit as the code change per Guardrail A.

## File structure

**Modified:**

- `ralph_executor/cli.py` — drop `--repo` / `--workspace` / `--ralph-home` flags from the top-level parser and the `reconcile` / `init` subparsers; delete the `scaffold` subparser; delete `_resolve_workspace`, `_scaffold_resolve_target`; rewrite `_apply_overrides` (no repo_path branch); update the startup log line and `_cmd_reconcile` (use queue clone name instead of `cfg.repo_path.name`).
- `ralph_executor/config.py` — delete `ExecutorConfig.repo_path` field; delete `_resolve_repo_path`; rewrite `load_config` to skip repo_path resolution; remove the `<repo>/.ralph/config.toml` loader (`_load_toml_overrides`) — user TOML at `~/.ralph/config.toml` is the only TOML source; rewrite the module docstring.
- `ralph_executor/user_config.py` — delete `read_ralph_home`, `write_ralph_home`; add `_warn_stale_ralph_home_in_user_config` helper that runs at config-load time.
- `ralph_executor/setup_cmds.py` — delete `_prompt_ralph_home`, `_default_ralph_home`, the ralph_home branch of `cmd_init`, the entire `cmd_scaffold` function, `ScaffoldError`, `_git` / `_has_branch` / `_working_tree_clean`, `CONFIG_TOML_STUB`, `QUEUE_BRANCH`, `QUEUE_SUBDIRS`; `cmd_init` becomes `workspace_root` + `queue_repo` + `queue_branch` only.
- `ralph_executor/loop.py` — replace `cfg.repo_path.name` (in `_run_sweep_step`) with `_target_clone_name(pbi)`; replace `cfg.repo_path` in `_pr_skill_scripts_path` with `_pr_skill_scripts_root()` derived from the ralph executor source tree; replace `cfg.repo_path` in `git_ops.diff_names` call (line 694) with the per-PBI clone root from `pbi.target_info`; add `_warn_project_toml_in_target_clone(clone_root)` helper, called once per target after `ensure_clone`.
- `ralph_executor/claude_spawn.py` — replace the `cfg.repo_path` fallback in `effective_cwd` resolution with a `ConfigError` (PBI always has `work_worktree` after refactor); replace `cfg.repo_path` in `_query_open_pr_via_gh` and `_wait_for_pr_checks` calls (lines 665, 679) with `pbi.work_worktree`.
- `tests/executor/conftest.py` — drop `repo_path=fake_repo` from `cfg_for_repo`; add fixture for fake `~/.ralph/config.toml` (`workspace_root`, `queue_repo`, `queue_branch`).
- `tests/executor/test_cli.py` — delete every `--workspace` / `--repo` / `--ralph-home` / `scaffold` test enumerated in the spec; update `test_main_log_level_flag`, `test_main_runs_one_iteration_with_once_flag`, `test_main_handles_keyboard_interrupt_cleanly` to drop the `--repo` arg if present.
- `tests/executor/test_config.py` — drop `test_load_config_missing_repo_path_falls_back_to_cwd`, `test_load_config_uses_cwd_when_repo_path_unset`, `test_load_config_repo_path_not_a_directory`, `test_load_config_repo_path_not_a_git_repo`; flip `RALPH_REPO_PATH`-based fixture setup to user-TOML based.
- `tests/executor/test_setup_cmds.py` — delete every `cmd_scaffold` / `ScaffoldError` / `ralph_home` test; rewrite `test_init_*` to assert `workspace_root` is prompted (replacing the ralph_home prompt).
- `tests/executor/test_loop_integration.py` — drop `repo_path=clone` from `ExecutorConfig(...)` calls.
- `tests/executor/test_claude_spawn.py` — drop `repo_path=...` from any `ExecutorConfig(...)` calls and switch to passing `pbi.work_worktree` where the test asserted on `cwd`.
- `tests/executor/test_cli_reconcile.py` — drop `--workspace` / `--repo` calls.
- `README.md` — delete "Per-repo setup" section, "Running multiple ralphs" section; remove `ralph_home`, `--ralph-home`, `--repo`, `--workspace` references; collapse "Running ralph" to three lines; rewrite "Configuration precedence" to drop project-TOML and `$RALPH_HOME`.
- `docs/runbooks/ralph-setup.md` — remove every `ralph_home` / `--workspace` / `--repo` / `--ralph-home` / project-TOML / `scaffold` reference; rewrite the "running" section.
- ~~`docs/runbooks/ralph-architecture.md`~~ — dropped from this plan (handled in a follow-up doc PR by the user).
- `docs/runbooks/ralph-queue-setup.md` — verify and trim any stale `ralph_home` references.
- `scripts/start-ralph.ps1` — drop the `-Workspace` parameter and the `--workspace` argv segment.

**Created:**

- `tests/executor/test_user_config_stale_warning.py` — covers `_warn_stale_ralph_home_in_user_config`.
- `tests/executor/test_loop_project_toml_warning.py` — covers `_warn_project_toml_in_target_clone`.
- ~~`tests/executor/test_loop_multi_target.py`~~ — dropped from this plan (Task 12 removed).

**Deleted (file removals):**

- None. All deletions are within-file (functions, fields, branches, tests). The `setup_cmds.py` module shrinks dramatically but stays around for `cmd_init`.

## Tasks

---

### Task 1: Stale-`ralph_home` startup warning

Adds the one-time WARNING that fires when a user's existing `~/.ralph/config.toml` still has a `ralph_home` line after upgrade. Doesn't touch the deletion yet — the field's reader (`read_ralph_home`) still exists for now; we only add the warning helper.

**Files:**

- Create test: `tests/executor/test_user_config_stale_warning.py`
- Modify: `ralph_executor/user_config.py` (add `_warn_stale_ralph_home_in_user_config`)
- Modify: `ralph_executor/config.py` (call the warning helper exactly once at the top of `load_config`)

**Steps:**

- [ ] Write failing test `tests/executor/test_user_config_stale_warning.py` covering three cases:

  ```python
  """Tests for the one-time stale-`ralph_home` warning in user_config."""
  from pathlib import Path

  import pytest

  from ralph_executor.user_config import _warn_stale_ralph_home_in_user_config


  def _seed_user_toml(home: Path, body: str) -> Path:
      cfg = home / ".ralph" / "config.toml"
      cfg.parent.mkdir(parents=True, exist_ok=True)
      cfg.write_text(body, encoding="utf-8")
      return cfg


  def test_warns_when_ralph_home_present(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
      caplog: pytest.LogCaptureFixture,
  ) -> None:
      monkeypatch.setenv("HOME", str(tmp_path))
      monkeypatch.setenv("USERPROFILE", str(tmp_path))
      _seed_user_toml(tmp_path, 'ralph_home = "/x"\nworkspace_root = "/y"\n')

      with caplog.at_level("WARNING", logger="ralph_executor.user_config"):
          _warn_stale_ralph_home_in_user_config()

      messages = [r.getMessage() for r in caplog.records]
      assert any("ralph_home" in m and "no longer supported" in m for m in messages)
      assert any(str(tmp_path / ".ralph" / "config.toml") in m for m in messages)


  def test_no_warning_when_ralph_home_absent(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
      caplog: pytest.LogCaptureFixture,
  ) -> None:
      monkeypatch.setenv("HOME", str(tmp_path))
      monkeypatch.setenv("USERPROFILE", str(tmp_path))
      _seed_user_toml(tmp_path, 'workspace_root = "/y"\n')

      with caplog.at_level("WARNING", logger="ralph_executor.user_config"):
          _warn_stale_ralph_home_in_user_config()

      assert caplog.records == []


  def test_no_warning_when_user_toml_absent(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
      caplog: pytest.LogCaptureFixture,
  ) -> None:
      monkeypatch.setenv("HOME", str(tmp_path))
      monkeypatch.setenv("USERPROFILE", str(tmp_path))
      # No file written.

      with caplog.at_level("WARNING", logger="ralph_executor.user_config"):
          _warn_stale_ralph_home_in_user_config()

      assert caplog.records == []
  ```

- [ ] Run the test and confirm it fails (helper not yet defined):

  ```bash
  uv run pytest tests/executor/test_user_config_stale_warning.py -x
  ```

  Expected: `ImportError: cannot import name '_warn_stale_ralph_home_in_user_config' from 'ralph_executor.user_config'`.

- [ ] Implement `_warn_stale_ralph_home_in_user_config` in `ralph_executor/user_config.py`:

  ```python
  def _warn_stale_ralph_home_in_user_config() -> None:
      """Emit a one-time WARNING if ``~/.ralph/config.toml`` still has ``ralph_home``.

      Called once at the top of ``load_config``. The key is no longer read
      anywhere — silent removal would leave operators wondering why their
      `--workspace NAME` invocations stopped resolving. The WARNING points
      at the file path so the operator can delete the stale line.
      """
      cfg_file = user_config_path()
      if not cfg_file.is_file():
          return
      try:
          with cfg_file.open("rb") as fh:
              data = tomllib.load(fh)
      except (tomllib.TOMLDecodeError, OSError):
          # Malformed / unreadable user TOML surfaces its own ConfigError
          # via _load_user_config when read for real keys; suppress here
          # so the warning helper never crashes startup.
          return
      if "ralph_home" in data:
          log.warning(
              "%s: 'ralph_home' key is no longer supported and is ignored. "
              "Delete the line; the executor uses 'workspace_root' instead.",
              cfg_file,
          )
  ```

  Then call the helper from `load_config` in `ralph_executor/config.py`. Add this at the very top of `load_config()`:

  ```python
  def load_config() -> ExecutorConfig:
      """Read defaults < ``~/.ralph/config.toml`` < env, and validate."""
      from ralph_executor.user_config import _warn_stale_ralph_home_in_user_config

      _warn_stale_ralph_home_in_user_config()
      ...
  ```

- [ ] Run the test and confirm it passes:

  ```bash
  uv run pytest tests/executor/test_user_config_stale_warning.py -x
  ```

- [ ] Commit:

  ```bash
  git add ralph_executor/user_config.py ralph_executor/config.py tests/executor/test_user_config_stale_warning.py
  git commit -m "feat(user-config): warn on stale 'ralph_home' key in user TOML"
  ```

---

### Task 2: Per-iteration `project TOML in target clone` warning

Adds the per-iteration WARNING that fires the first time a target clone is touched in this run if it contains `.ralph/config.toml`. Doesn't yet delete the project-TOML reader in `config.py`; we only add the helper and wire it into the iteration.

**Files:**

- Create test: `tests/executor/test_loop_project_toml_warning.py`
- Modify: `ralph_executor/loop.py` (add `_warn_project_toml_in_target_clone`; call after `ensure_clone` in `_claim_pbi_worktree` and the resume path in `iterate_once`)

**Steps:**

- [ ] Write failing test `tests/executor/test_loop_project_toml_warning.py`:

  ```python
  """Tests for the per-iteration project-TOML warning."""
  from pathlib import Path

  import pytest

  from ralph_executor.loop import _warn_project_toml_in_target_clone


  def test_warns_when_project_toml_present(
      tmp_path: Path,
      caplog: pytest.LogCaptureFixture,
  ) -> None:
      clone_root = tmp_path / "clones" / "acme" / "widget"
      (clone_root / ".ralph").mkdir(parents=True)
      cfg_file = clone_root / ".ralph" / "config.toml"
      cfg_file.write_text("# legacy project TOML\n", encoding="utf-8")

      with caplog.at_level("WARNING", logger="ralph_executor.loop"):
          _warn_project_toml_in_target_clone(clone_root)

      messages = [r.getMessage() for r in caplog.records]
      assert any(
          "project TOML" in m and "is not supported" in m and str(cfg_file) in m
          for m in messages
      )


  def test_no_warning_when_project_toml_absent(
      tmp_path: Path,
      caplog: pytest.LogCaptureFixture,
  ) -> None:
      clone_root = tmp_path / "clones" / "acme" / "widget"
      clone_root.mkdir(parents=True)

      with caplog.at_level("WARNING", logger="ralph_executor.loop"):
          _warn_project_toml_in_target_clone(clone_root)

      assert caplog.records == []


  def test_no_warning_when_clone_root_missing(
      tmp_path: Path,
      caplog: pytest.LogCaptureFixture,
  ) -> None:
      """Defensive: never crash on a clone_root that doesn't exist."""
      clone_root = tmp_path / "does" / "not" / "exist"

      with caplog.at_level("WARNING", logger="ralph_executor.loop"):
          _warn_project_toml_in_target_clone(clone_root)

      assert caplog.records == []


  def test_permission_denied_logged_at_debug(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
      caplog: pytest.LogCaptureFixture,
  ) -> None:
      """Detection failure (e.g. permission denied) suppressed to DEBUG per spec."""
      clone_root = tmp_path / "clones" / "acme" / "widget"
      clone_root.mkdir(parents=True)

      def _boom(self: Path) -> bool:
          raise PermissionError("simulated")

      monkeypatch.setattr(Path, "is_file", _boom)

      with caplog.at_level("DEBUG", logger="ralph_executor.loop"):
          _warn_project_toml_in_target_clone(clone_root)

      # No WARNING records emitted.
      assert not any(r.levelname == "WARNING" for r in caplog.records)
  ```

- [ ] Run the test and confirm it fails:

  ```bash
  uv run pytest tests/executor/test_loop_project_toml_warning.py -x
  ```

  Expected: `ImportError: cannot import name '_warn_project_toml_in_target_clone' from 'ralph_executor.loop'`.

- [ ] Implement `_warn_project_toml_in_target_clone` in `ralph_executor/loop.py`. Add it near the top of the module, next to `_pr_skill_scripts_path`:

  ```python
  def _warn_project_toml_in_target_clone(clone_root: Path) -> None:
      """Emit a WARNING if ``<clone>/.ralph/config.toml`` exists.

      Called after every successful ``ensure_clone`` so a freshly cloned
      target gets exactly one WARNING per iteration that touches it. The
      file is NOT loaded — project TOML is gone after this refactor. The
      WARNING names the path so the operator can move the settings to
      ``~/.ralph/config.toml`` and delete the file.

      Detection failure (permission denied, transient I/O error reading
      the clone) is suppressed at DEBUG so a flaky filesystem never
      crashes the iteration.
      """
      cfg_file = clone_root / ".ralph" / "config.toml"
      try:
          present = cfg_file.is_file()
      except OSError as exc:
          log.debug("project-TOML check failed for %s: %s", cfg_file, exc)
          return
      if present:
          log.warning(
              "project TOML at %s is not supported -- ignored. "
              "Move settings to ~/.ralph/config.toml.",
              cfg_file,
          )
  ```

  Call the helper from `_claim_pbi_worktree` immediately after the successful `ensure_clone`:

  ```python
  try:
      clone = tc_mod.ensure_clone(info, workspace_root=cfg.workspace_root)
  except tc_mod.TargetUnreachable as exc:
      raise _ClaimError(f"target unreachable: {exc}") from exc
  _warn_project_toml_in_target_clone(clone.clone_root)
  ```

  Also from the resume path inside `iterate_once`, after the `ensure_clone` call on the resumed PBI branch (the call that currently lives inside the `if cfg.use_worktrees and info is not None:` block):

  ```python
  try:
      from ralph_executor.target_clone import ensure_clone
      ensure_clone(info, workspace_root=cfg.workspace_root)
  except Exception:
      log.warning(
          "iterate_once: ensure_clone failed for resumed PBI %s; "
          "using deterministic clone path (may be stale)",
          current.id,
          exc_info=True,
      )
  if clone_root.is_dir():
      _warn_project_toml_in_target_clone(clone_root)
      work_wt = work_worktree_path(clone_root, current.id)
  ```

- [ ] Run the test and confirm it passes:

  ```bash
  uv run pytest tests/executor/test_loop_project_toml_warning.py -x
  uv run pytest tests/executor/test_loop_integration.py -x
  ```

  Both must pass; the integration test runs the helper path indirectly.

- [ ] Commit:

  ```bash
  git add ralph_executor/loop.py tests/executor/test_loop_project_toml_warning.py
  git commit -m "feat(loop): warn when target clone has a stale .ralph/config.toml"
  ```

---

### Task 3: Refactor `_pr_skill_scripts_path` off `cfg.repo_path`

Replaces the `cfg.repo_path / "skills" / ...` lookup with a lookup rooted at the ralph executor source tree (the parent of the `ralph_executor` package). After this task, `cfg.repo_path` still exists on the dataclass — we'll delete it only when every consumer is switched.

**Files:**

- Modify: `ralph_executor/loop.py` (`_pr_skill_scripts_path`)
- Modify: `tests/executor/test_loop_integration.py` and other tests that monkeypatch `_pr_skill_scripts_path` (none today; assert via grep)

**Steps:**

- [ ] Write failing test in `tests/executor/test_loop_pr_skill_scripts_path.py`:

  ```python
  """Tests for _pr_skill_scripts_path after the repo_path refactor."""
  from pathlib import Path

  import pytest

  from ralph_executor.loop import _pr_skill_scripts_path
  from tests.executor.conftest import _build_minimal_cfg  # added in Task 0; see below


  def test_resolves_to_executor_source_tree(tmp_path: Path) -> None:
      cfg = _build_minimal_cfg(tmp_path, git_host="github")
      result = _pr_skill_scripts_path(cfg)
      # Must NOT depend on a target/clone path.
      assert result.is_absolute()
      assert result.parts[-3:] == ("skills", "pr-github", "scripts")
      # Must resolve relative to the ralph package, not anything in tmp_path.
      assert tmp_path not in result.parents
  ```

  Note: this test imports `_build_minimal_cfg` from conftest, which we'll add in this task's implementation step.

- [ ] Run the test and confirm it fails:

  ```bash
  uv run pytest tests/executor/test_loop_pr_skill_scripts_path.py -x
  ```

  Expected: `ImportError: cannot import name '_build_minimal_cfg' from 'tests.executor.conftest'` OR an `AttributeError` showing the function still uses `cfg.repo_path`.

- [ ] Implement the refactor. In `ralph_executor/loop.py`, rewrite `_pr_skill_scripts_path`:

  ```python
  def _pr_skill_scripts_path(cfg: ExecutorConfig) -> Path:
      """Return the on-disk scripts directory for the configured PR skill.

      The scripts live in the ralph executor source tree (``skills/pr-github/``
      or ``skills/ado-pr/``), NOT in any target / queue clone. We resolve
      relative to the ``ralph_executor`` package location so the lookup is
      independent of CWD and of any operator-supplied repo path.

      ``cfg.git_host == "github"`` -> ``<ralph-src>/skills/pr-github/scripts/``.
      ``cfg.git_host == "ado"``    -> ``<ralph-src>/skills/ado-pr/scripts/``.
      Empty / unknown host: prefer ``pr-github`` if it exists, else fall
      back to ``ado-pr`` (existence is verified by the caller).
      """
      import ralph_executor

      ralph_src = Path(ralph_executor.__file__).resolve().parent.parent
      host = (cfg.git_host or "").strip().lower()
      if host == "github":
          return ralph_src / "skills" / "pr-github" / "scripts"
      if host == "ado":
          return ralph_src / "skills" / "ado-pr" / "scripts"
      pr_github = ralph_src / "skills" / "pr-github" / "scripts"
      if pr_github.is_dir():
          return pr_github
      return ralph_src / "skills" / "ado-pr" / "scripts"
  ```

  Add a `_build_minimal_cfg` helper at the bottom of `tests/executor/conftest.py`:

  ```python
  def _build_minimal_cfg(tmp_path: Path, *, git_host: str = "github") -> "ExecutorConfig":
      """Construct an ExecutorConfig without ``repo_path``.

      Used by tests that only need a cfg shape, not a running queue or
      target clone. Tests that need spawning still go through
      ``cfg_for_repo``.
      """
      from ralph_executor.config import ExecutorConfig

      return ExecutorConfig(
          queue_repo="file:///tmp/bare.git",
          queue_branch="ralph-queue",
          main_branch="main",
          max_attempts=3,
          log_level=20,
          iteration_sleep_seconds=0.0,
          claude_binary="claude",
          claude_permission_mode="bypassPermissions",
          anthropic_api_key="",
          git_host=git_host,
          gh_owner="",
          ado_org_url="",
          ado_project="",
          halt_webhook="",
          pr_check_poll_max_attempts=6,
          pr_check_poll_interval_seconds=30.0,
          use_worktrees=True,
          bot_author_email="",
          stale_days=3,
          bash_max_timeout_ms=900_000,
          workspace_root=tmp_path / "ws",
          claude_session_timeout_seconds=1200,
          same_file_min_prs=10,
          same_file_window_hours=24.0,
      )
  ```

  Note: the helper omits `repo_path=...` deliberately. The constructor still rejects this call today because `repo_path` is a required positional field on the dataclass — so this helper drives Task 5's deletion of the field. Until Task 5 lands, this helper is unused by anything other than the Task 3 test, which we will skip-mark in the next bullet.

- [ ] Skip-mark the failing test until Task 5:

  ```python
  @pytest.mark.skip(reason="depends on ExecutorConfig.repo_path deletion (Task 5)")
  def test_resolves_to_executor_source_tree(tmp_path: Path) -> None:
      ...
  ```

  Run the full executor suite to confirm `_pr_skill_scripts_path` still works for the existing callers (which still pass `cfg.repo_path` indirectly because the field hasn't been removed):

  ```bash
  uv run pytest tests/executor/ -x
  ```

  Expected: all green; the new test stays skipped.

- [ ] Commit:

  ```bash
  git add ralph_executor/loop.py tests/executor/conftest.py tests/executor/test_loop_pr_skill_scripts_path.py
  git commit -m "refactor(loop): resolve PR-skill scripts against ralph source tree, not cfg.repo_path"
  ```

---

### Task 4: Refactor `_cmd_reconcile` and the startup log line off `cfg.repo_path`

`_cmd_reconcile` reads `cfg.repo_path.name` to label the `SweepContext`. After this refactor, reconcile is queue-only — its label is the queue clone's directory name (`queue` under `workspace_root`). The startup log line in `cli.main` is updated to drop the `repo=...` field.

**Files:**

- Modify: `ralph_executor/cli.py` (`_cmd_reconcile`, the `log.info("ralph-executor starting ...")` line)
- Modify: `tests/executor/test_cli_reconcile.py` (drop `--workspace` / `--repo` from any test that exercises reconcile; pin user-TOML fixture)

**Steps:**

- [ ] Write failing test `tests/executor/test_cli_reconcile_no_repo_flag.py`:

  ```python
  """Reconcile must work without --repo or --workspace flags after refactor."""
  import pytest

  from ralph_executor import cli


  def test_reconcile_runs_with_no_target_flags(
      monkeypatch: pytest.MonkeyPatch,
      tmp_path,
  ) -> None:
      """`ralph-executor reconcile --dry-run` must accept no target args."""
      monkeypatch.setenv("HOME", str(tmp_path))
      monkeypatch.setenv("USERPROFILE", str(tmp_path))
      cfg_dir = tmp_path / ".ralph"
      cfg_dir.mkdir()
      (cfg_dir / "config.toml").write_text(
          'workspace_root = "/tmp/ws"\n'
          'queue_repo = "https://github.com/test/queue"\n',
          encoding="utf-8",
      )

      called: list[str] = []
      monkeypatch.setattr(
          cli, "reconcile_all", lambda ctx, dry_run: called.append("ran") or _empty_report()
      )
      monkeypatch.setattr(
          cli,
          "reconcile_stale_current_all",
          lambda ctx, dry_run: _empty_current_report(),
      )

      # Reconcile no longer accepts --repo or --workspace.
      rc = cli.main(["reconcile", "--dry-run"])
      assert rc == 0 or rc == 2  # 0 if env complete, 2 if config-load fails — both are valid here.


  def _empty_report():
      from ralph_executor.sweep.types import ReconcileReport
      return ReconcileReport(actions={}, errors={})


  def _empty_current_report():
      from ralph_executor.sweep.types import CurrentReconcileReport
      return CurrentReconcileReport(actions={}, errors={})


  def test_reconcile_rejects_repo_flag(
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      """argparse must reject --repo on the reconcile subcommand."""
      with pytest.raises(SystemExit) as excinfo:
          cli.main(["reconcile", "--repo", "/x"])
      assert excinfo.value.code == 2


  def test_reconcile_rejects_workspace_flag(
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      with pytest.raises(SystemExit) as excinfo:
          cli.main(["reconcile", "--workspace", "x"])
      assert excinfo.value.code == 2
  ```

- [ ] Run the test and confirm the `--repo`/`--workspace` rejection cases fail (today they're accepted):

  ```bash
  uv run pytest tests/executor/test_cli_reconcile_no_repo_flag.py -x
  ```

  Expected: `test_reconcile_rejects_repo_flag` / `test_reconcile_rejects_workspace_flag` fail because argparse still accepts those flags.

- [ ] Update `_cmd_reconcile` in `ralph_executor/cli.py` to:
  - Drop the call to `_apply_overrides(cfg, args)` (it processed `--repo` / `--workspace`).
  - Set `repo_name` from the queue clone directory name.
  - Remove the `--repo` / `--workspace` flags from the `reconcile_parser` block earlier in the file.

  Replace the existing `_cmd_reconcile` body's `cfg = load_config(); cfg = _apply_overrides(cfg, args)` lines with:

  ```python
  try:
      cfg = load_config()
  except ConfigError as exc:
      print(f"error: {exc}", file=sys.stderr)
      return 2
  ```

  Replace the `repo_name=cfg.repo_path.name,` line with:

  ```python
  repo_name=_queue_repo_root(cfg).name,
  ```

  In `_build_parser`, delete `reconcile_repo_group` and its two `add_argument` calls (the `--repo` and `--workspace` block for the reconcile subparser, currently at cli.py:261-270).

  Also update the startup log line in `main`. Replace:

  ```python
  log.info(
      "ralph-executor starting (repo=%s queue_repo=%s queue_branch=%s main=%s)",
      cfg.repo_path,
      cfg.queue_repo,
      cfg.queue_branch,
      cfg.main_branch,
  )
  ```

  with:

  ```python
  log.info(
      "ralph-executor starting (workspace_root=%s queue_repo=%s queue_branch=%s main=%s)",
      cfg.workspace_root,
      cfg.queue_repo,
      cfg.queue_branch,
      cfg.main_branch,
  )
  ```

- [ ] Run the test and confirm it passes:

  ```bash
  uv run pytest tests/executor/test_cli_reconcile_no_repo_flag.py -x
  uv run pytest tests/executor/test_cli_reconcile.py -x
  ```

  The existing `test_cli_reconcile.py` may still pass because it doesn't always pass `--repo`. If any test there asserts `--repo` is accepted, mark it for deletion in Task 9.

- [ ] Commit:

  ```bash
  git add ralph_executor/cli.py tests/executor/test_cli_reconcile_no_repo_flag.py
  git commit -m "refactor(cli): reconcile uses queue clone name; drop --repo/--workspace"
  ```

---

### Task 5: Refactor `claude_spawn.py` off `cfg.repo_path`

`claude_spawn.spawn_claude_p` reads `cfg.repo_path` three times: as a fallback `cwd` when neither the caller nor `pbi.work_worktree` supplies one (line 524), and as the working directory for `_query_open_pr_via_gh` (line 665) and `_wait_for_pr_checks` (line 679). After this refactor:

- The `effective_cwd` fallback chain is `cwd` arg > `pbi.work_worktree`; absence of both is a `ConfigError`.
- `_query_open_pr_via_gh` and `_wait_for_pr_checks` are called against `pbi.work_worktree`, which IS the target clone's worktree on the feature branch — the right place to ask `gh` "is there a PR open from this branch?".

**Files:**

- Modify: `ralph_executor/claude_spawn.py` (lines 519-524, 665, 679)
- Modify: `tests/executor/test_claude_spawn.py` (drop fallback-to-`cfg.repo_path` test; assert ConfigError on missing work_worktree)

**Steps:**

- [ ] Write failing test in `tests/executor/test_claude_spawn_no_repo_path_fallback.py`:

  ```python
  """spawn_claude_p must error when neither cwd nor pbi.work_worktree is set."""
  from pathlib import Path

  import pytest

  from ralph_executor.claude_spawn import spawn_claude_p
  from ralph_executor.config import ConfigError
  from ralph_executor.queue.types import PBI
  from tests.executor.conftest import _build_minimal_cfg


  def test_spawn_errors_without_work_worktree(
      tmp_path: Path,
  ) -> None:
      cfg = _build_minimal_cfg(tmp_path)
      pbi_dir = tmp_path / "pbi"
      pbi_dir.mkdir()
      pbi = PBI(
          id="WI-1",
          path=pbi_dir,
          work_worktree=None,  # the failure trigger
      )

      with pytest.raises(ConfigError, match="work_worktree"):
          spawn_claude_p(cfg, pbi, pbi_dir=pbi_dir)
  ```

- [ ] Run and confirm it fails (today the call falls back to `cfg.repo_path` and never raises). Expected: today's behaviour returns a `FileNotFoundError` for the missing fallback path on a `_build_minimal_cfg`-built cfg (since the helper omits `repo_path` it will raise a different error). Either way: not the `ConfigError` we want.

  ```bash
  uv run pytest tests/executor/test_claude_spawn_no_repo_path_fallback.py -x
  ```

- [ ] Implement the refactor in `ralph_executor/claude_spawn.py`. Replace:

  ```python
  effective_pbi_dir = Path(pbi_dir) if pbi_dir is not None else pbi.path
  if cwd is not None:
      effective_cwd = Path(cwd)
  elif pbi.work_worktree is not None:
      effective_cwd = pbi.work_worktree
  else:
      effective_cwd = cfg.repo_path
  ```

  with:

  ```python
  from ralph_executor.config import ConfigError

  effective_pbi_dir = Path(pbi_dir) if pbi_dir is not None else pbi.path
  if cwd is not None:
      effective_cwd = Path(cwd)
  elif pbi.work_worktree is not None:
      effective_cwd = pbi.work_worktree
  else:
      raise ConfigError(
          f"spawn_claude_p: PBI {pbi.id} has no work_worktree set and no "
          "cwd override was supplied. The per-PBI worktree must be "
          "materialised by _claim_pbi or recovered by the resume path "
          "in iterate_once before Claude can be spawned."
      )
  ```

  Then replace:

  ```python
  pr_url = _query_open_pr_via_gh(cfg.repo_path, feature_branch)
  ```

  with:

  ```python
  # ``gh pr list`` runs against the target clone's working tree (the
  # per-PBI worktree on the feature branch). ``cfg`` no longer carries
  # a process-wide repo path — every gh invocation is per-PBI.
  pr_url = _query_open_pr_via_gh(effective_cwd, feature_branch)
  ```

  And:

  ```python
  pr_check_state, pr_check_failed_names = _wait_for_pr_checks(
      cfg.repo_path,
      pr_number,
      ...
  )
  ```

  with:

  ```python
  pr_check_state, pr_check_failed_names = _wait_for_pr_checks(
      effective_cwd,
      pr_number,
      ...
  )
  ```

  Update the spawn_claude_p docstring to remove the legacy "falls back to cfg.repo_path" line; replace it with: "If neither override is supplied the call raises ConfigError — the per-PBI worktree must be materialised by `_claim_pbi` or the resume path before spawning Claude."

- [ ] Run the test and confirm it passes; also run the full claude_spawn suite to confirm no regressions in callers that DO set `work_worktree`:

  ```bash
  uv run pytest tests/executor/test_claude_spawn_no_repo_path_fallback.py -x
  uv run pytest tests/executor/test_claude_spawn.py -x
  ```

- [ ] Commit:

  ```bash
  git add ralph_executor/claude_spawn.py tests/executor/test_claude_spawn_no_repo_path_fallback.py
  git commit -m "refactor(claude_spawn): require pbi.work_worktree; drop cfg.repo_path fallback"
  ```

---

### Task 6: Refactor `loop._run_ralph` and `_run_sweep_step` off `cfg.repo_path`

Two remaining `cfg.repo_path` consumers in `loop.py`:

- `loop.py:181` — `_run_sweep_step` uses `cfg.repo_path.name` to label the sweep context's `repo_name`.
- `loop.py:694` — `_run_ralph` calls `git_ops.diff_names(cfg.repo_path, cfg.main_branch, _feature_branch_name(pbi))` to compute touched files for the pending-pr event. The diff must run against the TARGET clone (where the feature branch lives), not ralph's own repo.

**Files:**

- Modify: `ralph_executor/loop.py` (`_run_sweep_step`, `_run_ralph`)
- Modify: `tests/executor/test_loop_integration.py` (the touched-files assertion, if any, must target the per-PBI clone)

**Steps:**

- [ ] Write failing test `tests/executor/test_loop_diff_against_target.py`:

  ```python
  """_run_ralph computes touched files against the per-PBI target clone, not cfg.repo_path."""
  from pathlib import Path

  import pytest

  from ralph_executor.loop import _run_ralph


  def test_diff_names_uses_target_clone(
      monkeypatch: pytest.MonkeyPatch,
      cfg_for_repo,
      tmp_path: Path,
  ) -> None:
      seen: dict[str, Path] = {}

      def _fake_diff_names(repo: Path, base: str, head: str) -> list[str]:
          seen["repo"] = repo
          return ["a.py"]

      monkeypatch.setattr("ralph_executor.git_ops.diff_names", _fake_diff_names)
      # ... full setup omitted: monkeypatch spawn_claude_p to return a
      # pr_created outcome, build a PBI in current/ with target_info
      # populated, drive _run_ralph; assert seen["repo"] is the target
      # clone root (cfg_for_repo.workspace_root / "clones" / owner / name)
      # and NOT cfg_for_repo.repo_path (which is going away).
  ```

  (Full skeleton — fill in the spawn / PBI setup mirroring `test_loop_integration._stub_spawn` patterns. The key assertion is `seen["repo"] != cfg_for_repo.repo_path`.)

- [ ] Run the test and confirm it fails:

  ```bash
  uv run pytest tests/executor/test_loop_diff_against_target.py -x
  ```

- [ ] Implement the refactor. In `_run_sweep_step` (around loop.py:181), replace:

  ```python
  repo_name=cfg.repo_path.name,
  ```

  with:

  ```python
  # The queue clone is the single repo the sweep reads/writes; label
  # the sweep context with its directory name (typically "queue" under
  # workspace_root).
  repo_name=_queue_repo_root(cfg).name,
  ```

  In `_run_ralph` (around loop.py:694), replace:

  ```python
  if outcome.kind == "pr_created":
      touched = git_ops.diff_names(cfg.repo_path, cfg.main_branch, _feature_branch_name(pbi))
  ```

  with:

  ```python
  if outcome.kind == "pr_created":
      # Diff runs against the target clone (which holds the feature
      # branch the PR was opened from), NOT ralph's own checkout. The
      # per-PBI clone root is derived from pbi.target_info, populated
      # earlier by _claim_pbi / iterate_once's resume path.
      if pbi.target_info is None:
          log.warning(
              "PBI %s pr_created but target_info missing; touched_files will be empty",
              pbi.id,
          )
          touched: list[str] = []
      else:
          clone_root = cfg.workspace_root / "clones" / pbi.target_info.owner / pbi.target_info.name
          touched = git_ops.diff_names(clone_root, cfg.main_branch, _feature_branch_name(pbi))
  ```

- [ ] Run the new test and the full loop integration suite:

  ```bash
  uv run pytest tests/executor/test_loop_diff_against_target.py -x
  uv run pytest tests/executor/test_loop_integration.py -x
  ```

- [ ] Commit:

  ```bash
  git add ralph_executor/loop.py tests/executor/test_loop_diff_against_target.py
  git commit -m "refactor(loop): sweep + diff use per-target paths, not cfg.repo_path"
  ```

---

### Task 7: Refactor `_apply_overrides` and delete `_resolve_workspace` / `_scaffold_resolve_target`

`_apply_overrides` in `cli.py` currently writes `repo_path` from `--repo` / `--workspace`. With those flags gone (still parsed today, but consumers all gone after Tasks 3-6), the function's repo_path branch becomes dead and the helper functions `_resolve_workspace` / `_scaffold_resolve_target` are orphaned. This task removes them BEFORE the field deletion in Task 8 — keeping the dataclass field intact one task longer lets us run the test suite green between every commit.

**Files:**

- Modify: `ralph_executor/cli.py` (delete `_resolve_workspace`, `_scaffold_resolve_target`; drop `--repo` / `--workspace` flags from the top-level parser; rewrite `_apply_overrides` to skip the repo_path branch)
- Modify: `tests/executor/test_cli.py` (delete `test_main_workspace_*` and `test_main_repo_and_workspace_are_mutually_exclusive` per spec; this task only deletes the eight `--workspace` tests + the `--repo` mutex test; `test_main_uses_repo_flag_to_override_env` stays for now and is deleted in Task 9)

**Steps:**

- [ ] Verify no live callers of `_resolve_workspace` / `_scaffold_resolve_target` outside their own module:

  ```bash
  uv run rg "_resolve_workspace|_scaffold_resolve_target" --type py
  ```

  Expected matches: only inside `ralph_executor/cli.py` itself (definition + intra-module use).

- [ ] In `ralph_executor/cli.py`, delete the helper bodies `_resolve_workspace` (lines 307-340 in current file) and `_scaffold_resolve_target` (lines 289-304). Delete the import-style `from ralph_executor.user_config import read_ralph_home, user_config_path` inside `_resolve_workspace`.

- [ ] Drop the `--repo` / `--workspace` flags from the top-level parser. Replace this block (cli.py around lines 115-127):

  ```python
  repo_group = parser.add_mutually_exclusive_group()
  repo_group.add_argument(
      "--repo",
      help=("Explicit path to the repo Ralph operates on. Overrides RALPH_REPO_PATH and cwd."),
  )
  repo_group.add_argument(
      "--workspace",
      metavar="NAME",
      help=(
          "Resolve repo path against $RALPH_HOME/NAME. Requires RALPH_HOME "
          "to be set. Convention for running multiple ralphs on one host."
      ),
  )
  ```

  with a comment-only stub (no flags):

  ```python
  # --repo and --workspace removed: the queue PBI's target_repo
  # frontmatter is the only input that decides which repo the executor
  # works on per iteration. The operator does not pre-clone target
  # repos; the loop materialises every target under workspace_root.
  ```

- [ ] Rewrite `_apply_overrides` to drop the repo_path branch. Replace the function body with:

  ```python
  def _apply_overrides(cfg: ExecutorConfig, args: argparse.Namespace) -> ExecutorConfig:
      log_level: int = cfg.log_level
      queue_repo: str = cfg.queue_repo
      queue_branch: str = cfg.queue_branch
      watch_mode: bool = cfg.watch_mode
      changed = False
      if args.log_level:
          log_level = int(logging.getLevelName(args.log_level))
          changed = True
      if getattr(args, "queue_repo", None):
          from ralph_executor.url_utils import parse_target_repo
          try:
              parse_target_repo(args.queue_repo)
          except ValueError as exc:
              raise ConfigError(f"--queue-repo: {exc}") from exc
          queue_repo = args.queue_repo
          changed = True
      if getattr(args, "queue_branch", None) is not None:
          stripped = args.queue_branch.strip()
          if not stripped:
              raise ConfigError("--queue-branch must be a non-empty branch name")
          if stripped == "HEAD" or stripped.startswith("refs/heads/"):
              raise ConfigError(
                  f"--queue-branch must be a plain branch name (got {args.queue_branch!r})"
              )
          queue_branch = stripped
          changed = True
      if getattr(args, "watch", False):
          watch_mode = True
          changed = True
      if not changed:
          return cfg
      return dataclasses.replace(
          cfg,
          log_level=log_level,
          queue_repo=queue_repo,
          queue_branch=queue_branch,
          watch_mode=watch_mode,
      )
  ```

  Drop the `validate_repo_path` import at the top of `cli.py` (no longer used after this refactor).

- [ ] Delete the eight obsolete tests from `tests/executor/test_cli.py`. By name:

  - `test_main_workspace_resolves_to_ralph_home`
  - `test_main_workspace_without_ralph_home_errors`
  - `test_main_workspace_rejects_absolute_name`
  - `test_main_workspace_rejects_parent_traversal`
  - `test_main_workspace_rejects_single_dot`
  - `test_main_workspace_rejects_path_separator`
  - `test_main_workspace_validates_resolved_path`
  - `test_main_workspace_reads_ralph_home_from_user_config`
  - `test_main_workspace_errors_when_no_ralph_home_anywhere`
  - `test_main_repo_and_workspace_are_mutually_exclusive`

  Use a precise sed/edit per test (do NOT use replace_all; tests are uniquely named).

- [ ] Run the executor test suite:

  ```bash
  uv run pytest tests/executor/ -x
  ```

  Expected: all green. `test_main_uses_repo_flag_to_override_env` still passes today because we have not yet deleted `--repo` from anywhere it's referenced — but argparse no longer recognises `--repo`, so this test must be deleted alongside the others. Recheck: the test calls `cli.main(["--once", "--repo", str(fake_repo)])`. After this task argparse will reject `--repo`. So this test ALSO has to be deleted in this same commit.

  Delete `test_main_uses_repo_flag_to_override_env` as well.

  Re-run:

  ```bash
  uv run pytest tests/executor/ -x
  ```

  All green.

- [ ] Commit:

  ```bash
  git add ralph_executor/cli.py tests/executor/test_cli.py
  git commit -m "refactor(cli): drop --repo/--workspace flags + _resolve_workspace helpers"
  ```

---

### Task 8: Delete `ExecutorConfig.repo_path` field and `_resolve_repo_path`

Now safe: every consumer was switched in Tasks 3-7. Delete the field from the dataclass, the `_resolve_repo_path` helper, and the `validate_repo_path` function in `config.py`. Update `load_config` to drop the call to `_resolve_repo_path` and remove the project-TOML load path entirely (the spec says project TOML is no longer loaded).

Also delete the test `cfg_for_repo` fixture's `repo_path=...` argument and update `test_loop_integration.py`'s manually-constructed `ExecutorConfig(...)` call.

**Files:**

- Modify: `ralph_executor/config.py` (remove field, `_resolve_repo_path`, `validate_repo_path`, `_load_toml_overrides`, `_TOML_KNOWN_KEYS` references where unneeded; rewrite `load_config` to read all values from env + `~/.ralph/config.toml`)
- Modify: `ralph_executor/setup_cmds.py` (drop the `validate_repo_path` import; `cmd_scaffold` is dead and gets removed in Task 10, so just dropping the import is enough for now)
- Modify: `tests/executor/conftest.py` (drop `repo_path=fake_repo`)
- Modify: `tests/executor/test_loop_integration.py` (drop `repo_path=clone`)
- Modify: `tests/executor/test_config.py` (delete the four obsolete tests; flip the rest off `RALPH_REPO_PATH` to user-TOML)
- Modify: `tests/executor/test_setup_cmds.py` (drop validate_repo_path references in the scaffold tests; those tests will be fully deleted in Task 10)

**Steps:**

- [ ] Write the failing assertion via the skipped Task 3 test unskipped:

  ```bash
  # In tests/executor/test_loop_pr_skill_scripts_path.py, remove the
  # @pytest.mark.skip decorator and re-run:
  uv run pytest tests/executor/test_loop_pr_skill_scripts_path.py -x
  ```

  Expected: fails because `_build_minimal_cfg` calls `ExecutorConfig(...)` without `repo_path` but the dataclass still requires it.

- [ ] In `ralph_executor/config.py`:
  - Delete the `repo_path: Path` line from `ExecutorConfig` (around line 206).
  - Delete the `_resolve_repo_path` function (around line 330).
  - Delete the `validate_repo_path` function (around line 313). It's no longer called anywhere.
  - Delete the `_load_toml_overrides` function. It will be replaced by user-TOML reads in `load_config`.
  - Update `load_config()` to:
    - Drop the `repo_path = _resolve_repo_path()` line.
    - Drop the `toml_overrides = _load_toml_overrides(repo_path)` line.
    - Replace `toml_overrides.get("X")` reads with a new `_load_user_toml_overrides()` helper that reads `~/.ralph/config.toml` and returns a mapping filtered to `_TOML_KNOWN_KEYS`. Implementation:

      ```python
      def _load_user_toml_overrides() -> Mapping[str, Any]:
          """Read ``~/.ralph/config.toml`` and return overrides keyed by knob name.

          Replaces the per-repo ``<repo>/.ralph/config.toml`` loader removed
          in this refactor. Project TOML is no longer loaded; every knob
          previously sourced from it now lives at the user level.
          """
          from ralph_executor.user_config import user_config_path

          cfg_file = user_config_path()
          if not cfg_file.is_file():
              return {}
          try:
              with cfg_file.open("rb") as fh:
                  data = tomllib.load(fh)
          except tomllib.TOMLDecodeError as exc:
              raise ConfigError(f"{cfg_file}: invalid TOML: {exc}") from exc
          unknown = sorted(
              k for k in data
              if k not in _TOML_KNOWN_KEYS
              and k not in {"ralph_home", "skills_root", "claude_skills_dir"}
          )
          for key in unknown:
              log.warning("%s: unknown key %r (ignored)", cfg_file, key)
          return {k: v for k, v in data.items() if k in _TOML_KNOWN_KEYS}
      ```

      Note: `ralph_home`, `skills_root`, `claude_skills_dir` are allow-listed silently — `ralph_home` is the stale-warning case (Task 1), and the two skills paths are read directly by `host_select` via `user_config.read_skills_root` / `read_claude_skills_dir` and not via `load_config`.

  - Update the `source_label` to point at the user TOML:

      ```python
      from ralph_executor.user_config import user_config_path
      source_label = str(user_config_path())
      ```

  - Replace `toml_overrides = _load_toml_overrides(repo_path)` with `toml_overrides = _load_user_toml_overrides()`.

  - The `queue_repo_value` block currently has a "fall back to ~/.ralph/config.toml" path. After this refactor, `~/.ralph/config.toml` IS the only TOML source, so the fallback is gone. Simplify to:

      ```python
      queue_repo_value = toml_overrides.get("queue_repo")
      queue_repo_source = source_label
      if queue_repo_value is None:
          raise ConfigError(
              f"{source_label}: queue_repo not configured. "
              "Run `ralph-executor init` (writes ~/.ralph/config.toml) "
              "or pass --queue-repo."
          )
      ```

  - Same simplification for `queue_branch`: drop the `from ralph_executor.user_config import read_queue_branch ... user_queue_branch = read_queue_branch()` fallback block. `toml_overrides.get("queue_branch")` is enough now.

  - Add a missing-workspace_root error at startup. After the `workspace_root = _resolve_path(...)` line, add:

      ```python
      if not workspace_root.parent.is_dir():
          raise ConfigError(
              f"{source_label}: workspace_root parent {workspace_root.parent} "
              "does not exist. Create it (or change workspace_root)."
          )
      ```

  - In the final `return ExecutorConfig(...)` block, delete the `repo_path=repo_path,` argument.

  - Update the module docstring: drop the "Repo path resolution" section; drop the "Two values are intentionally NOT readable from TOML: repo_path —" bullet. Replace with: "Config is loaded from defaults < ~/.ralph/config.toml < env. CLI flags applied by `ralph_executor.cli._apply_overrides`."

- [ ] In `ralph_executor/setup_cmds.py`, drop the `from ralph_executor.config import ConfigError, validate_repo_path` import — change to `from ralph_executor.config import ConfigError` only.

- [ ] In `tests/executor/conftest.py`, delete the `repo_path=fake_repo,` line from `cfg_for_repo`.

- [ ] In `tests/executor/test_loop_integration.py`, delete the `repo_path=clone,` line from the `ExecutorConfig(...)` construction on line 300.

- [ ] In `tests/executor/test_config.py`, delete:

  - `test_load_config_missing_repo_path_falls_back_to_cwd`
  - `test_load_config_uses_cwd_when_repo_path_unset`
  - `test_load_config_repo_path_not_a_directory`
  - `test_load_config_repo_path_not_a_git_repo`

  Flip the remaining tests off `RALPH_REPO_PATH`. Find every `monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))` and replace with a helper that writes a user-TOML stub. Define the helper at the top of `test_config.py`:

  ```python
  def _seed_user_config_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: str) -> None:
      """Seed ~/.ralph/config.toml inside a monkeypatched HOME.

      kwargs hold the keys to write (queue_repo, queue_branch, workspace_root, etc).
      All values are stringified.
      """
      monkeypatch.setenv("HOME", str(tmp_path))
      monkeypatch.setenv("USERPROFILE", str(tmp_path))
      cfg_dir = tmp_path / ".ralph"
      cfg_dir.mkdir(parents=True, exist_ok=True)
      lines = [f'{k} = "{v}"' for k, v in kwargs.items()]
      (cfg_dir / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
  ```

  Replace each `monkeypatch.setenv("RALPH_REPO_PATH", str(git_repo))` with `_seed_user_config_toml(tmp_path, monkeypatch, queue_repo="https://github.com/test/queue", workspace_root=str(git_repo.parent))`. Then drop the `_write_queue_repo_toml(git_repo)` helper call that lived in the old git_repo's `.ralph/config.toml` — that file is no longer loaded.

  Delete the `_write_queue_repo_toml` helper from the test file entirely.

  Update `test_load_config_uses_defaults` and `test_load_config_overrides_via_env` to use the new helper pattern.

- [ ] In `tests/executor/test_setup_cmds.py`, the scaffold-related tests still call `validate_repo_path` indirectly via `cmd_scaffold`. Since `cmd_scaffold` is the next task's deletion target, those tests are scheduled for deletion in Task 10. For this task, just make sure `test_setup_cmds.py` doesn't import `validate_repo_path` directly — `uv run rg "validate_repo_path" tests/executor/test_setup_cmds.py` should return no matches.

- [ ] Run the full executor test suite:

  ```bash
  uv run pytest tests/executor/ -x
  ```

  Expected: the scaffold tests still pass (cmd_scaffold imports validate_repo_path from within setup_cmds.py — but we deleted that import). Re-check: the import was changed in this task's step 3 — so cmd_scaffold needs to define its own validation helper. Since we're deleting cmd_scaffold in Task 10, leave it broken for one commit cycle by skipping the entire scaffold test class with `pytestmark = pytest.mark.skip(reason="cmd_scaffold deletion pending Task 10")` at the top of the cmd_scaffold test section. Confirm:

  ```bash
  uv run pytest tests/executor/ -x
  ```

  All non-skipped tests green.

- [ ] Commit:

  ```bash
  git add ralph_executor/config.py ralph_executor/setup_cmds.py tests/executor/conftest.py tests/executor/test_loop_integration.py tests/executor/test_config.py tests/executor/test_setup_cmds.py tests/executor/test_loop_pr_skill_scripts_path.py
  git commit -m "refactor(config): delete ExecutorConfig.repo_path; load TOML from user config only"
  ```

---

### Task 9: Delete `$RALPH_HOME` and `$RALPH_REPO_PATH` reads

After Tasks 7-8, no live code reads `$RALPH_HOME` or `$RALPH_REPO_PATH`. This task asserts that grep returns no live consumers and deletes the leftover string references in docs/log messages.

**Files:**

- Modify: `ralph_executor/config.py` (module docstring; no live reads remain)
- Modify: `ralph_executor/cli.py` (module docstring + the `RALPH_RUN_ONCE` adjacent text)
- Modify: `ralph_executor/setup_cmds.py` (the `--ralph-home` flag and the existing-ralph-home branch — wholesale rewrite of cmd_init for the new prompt set)

**Steps:**

- [ ] Verify no live `os.environ` reads of `RALPH_HOME` or `RALPH_REPO_PATH`:

  ```bash
  uv run rg "RALPH_HOME|RALPH_REPO_PATH" --type py ralph_executor tests
  ```

  Expected matches: only docstring text, error messages mentioning the variables, and obsolete test code (which is deleted later). No live `os.environ.get("RALPH_HOME")` or `os.environ.get("RALPH_REPO_PATH")` calls.

- [ ] Rewrite `cmd_init` in `ralph_executor/setup_cmds.py`. Replace the entire `cmd_init` body and helpers (`_prompt_ralph_home`, `_default_ralph_home`) with a new flow that prompts for `workspace_root`, `queue_repo`, `queue_branch`:

  ```python
  def _prompt_workspace_root(default: Path) -> Path:
      """Interactive prompt for workspace_root with a sensible default."""
      print(
          f"Where should ralph workspaces live (queue clone + target clones)? "
          f"[{default}]: ",
          end="",
          flush=True,
      )
      try:
          raw = input().strip()
      except EOFError:
          print("")
          raw = ""
      return Path(raw).expanduser() if raw else default


  def _default_workspace_root() -> Path:
      """OS-flavoured suggestion for workspace_root."""
      return Path.home() / "ralph-workspaces"


  def cmd_init(*, assume_yes: bool) -> int:
      """Run ``ralph-executor init``.

      Prompts for ``workspace_root``, ``queue_repo``, ``queue_branch`` (in
      that order) and writes them to ``~/.ralph/config.toml``. Each prompt
      is idempotent — re-running init against an already-configured file
      is a no-op for keys already present.
      """
      from ralph_executor.user_config import (
          read_queue_branch,
          read_queue_repo,
          read_workspace_root,
          user_config_path,
          write_queue_branch,
          write_queue_repo,
      )

      # workspace_root prompt
      existing_ws = read_workspace_root()
      if existing_ws is not None:
          print(f"workspace_root already set to {existing_ws} in {user_config_path()}")
      else:
          chosen_ws = _default_workspace_root() if assume_yes else _prompt_workspace_root(_default_workspace_root())
          chosen_ws = chosen_ws.expanduser().resolve()
          try:
              chosen_ws.mkdir(parents=True, exist_ok=True)
              cfg_file = _write_workspace_root_to_user_config(chosen_ws)
          except OSError as exc:
              print(f"error: cannot write user config: {exc}", file=sys.stderr)
              return 2
          print(f"workspace_root = {chosen_ws}")
          print(f"wrote {cfg_file}")

      # queue_repo prompt (same logic as before, unchanged)
      existing_queue = read_queue_repo()
      if existing_queue is not None:
          print(f"queue_repo already set to {existing_queue} in {user_config_path()}")
      elif assume_yes:
          print(
              "WARNING: queue_repo not set. Add it manually to "
              f'{user_config_path()}: queue_repo = "<https-url>"'
          )
      else:
          chosen_queue = _prompt_queue_repo()
          if chosen_queue is None:
              print(
                  "WARNING: queue_repo not set (closed stdin or skipped). "
                  f"Add it manually to {user_config_path()}."
              )
          else:
              if not _smoke_clone_queue_repo(chosen_queue):
                  print(
                      f"WARNING: smoke `git ls-remote {chosen_queue}` failed -- "
                      "proceeding anyway."
                  )
              try:
                  queue_cfg = write_queue_repo(chosen_queue)
              except OSError as exc:
                  print(f"error: cannot write queue_repo to user config: {exc}", file=sys.stderr)
                  return 2
              print(f"queue_repo = {chosen_queue}")
              print(f"wrote {queue_cfg}")

      # queue_branch prompt (unchanged)
      existing_branch = read_queue_branch()
      if existing_branch is not None:
          print(f"queue_branch already set to {existing_branch} in {user_config_path()}")
      else:
          queue_branch_default = "ralph-queue"
          chosen_branch: str = queue_branch_default
          if not assume_yes:
              prompted = _prompt_queue_branch(queue_branch_default)
              if prompted is None:
                  print(f"WARNING: queue_branch prompt closed; defaulting to {queue_branch_default!r}.")
              else:
                  chosen_branch = prompted
          try:
              branch_cfg = write_queue_branch(chosen_branch)
          except OSError as exc:
              print(f"error: cannot write queue_branch to user config: {exc}", file=sys.stderr)
              return 2
          print(f"queue_branch = {chosen_branch}")
          print(f"wrote {branch_cfg}")

      # Best-effort tool checks (unchanged from current implementation).
      gh = _check_tool("gh")
      if gh is None:
          print("WARNING: gh CLI not found on PATH -- install from https://cli.github.com/")
      else:
          try:
              result = run_text([gh, "auth", "status"], capture_output=True, check=False)
              if result.returncode != 0:
                  print("WARNING: gh CLI present but not logged in -- run `gh auth login`")
          except OSError as exc:
              print(f"WARNING: could not run `gh auth status`: {exc}")
      claude = _check_tool("claude")
      if claude is None:
          print("WARNING: claude CLI not found on PATH -- install Claude Code per docs")

      print("")
      print("Done. Run `uv run ralph-executor` to drain the queue.")
      return 0
  ```

  Add `_write_workspace_root_to_user_config` to `ralph_executor/user_config.py`:

  ```python
  def write_workspace_root(value: Path) -> Path:
      """Persist ``workspace_root`` to ``~/.ralph/config.toml``.

      Merges with existing keys so ``queue_repo`` / ``queue_branch`` / any
      other previously-written knob survives.
      """
      return _write_user_config({"workspace_root": str(value)})
  ```

  Reference it in `cmd_init` via:

  ```python
  from ralph_executor.user_config import write_workspace_root
  ...
  cfg_file = write_workspace_root(chosen_ws)
  ```

  Remove the older `_write_workspace_root_to_user_config` placeholder name — just call `write_workspace_root`.

- [ ] In `ralph_executor/cli.py`, drop the `--ralph-home` flag from the `init` subparser. Replace:

  ```python
  init_parser.add_argument(
      "--ralph-home",
      type=Path,
      metavar="PATH",
      help="Skip the prompt and set ralph_home to PATH.",
  )
  init_parser.add_argument(
      "--yes",
      ...
  )
  ```

  with:

  ```python
  init_parser.add_argument(
      "--yes",
      action="store_true",
      help="Non-interactive: accept the OS default for workspace_root.",
  )
  ```

  Update the `init_parser` help string from `Per-machine setup: pick ralph_home, write ~/.ralph/config.toml.` to `Per-machine setup: pick workspace_root, write ~/.ralph/config.toml.`.

  In the dispatch arm for `init`:

  ```python
  if args.subcommand == "init":
      from ralph_executor.setup_cmds import cmd_init
      try:
          return cmd_init(ralph_home=args.ralph_home, assume_yes=args.yes)
      ...
  ```

  rewrite as:

  ```python
  if args.subcommand == "init":
      from ralph_executor.setup_cmds import cmd_init
      try:
          return cmd_init(assume_yes=args.yes)
      except ConfigError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2
  ```

  Also remove any references to `RALPH_HOME` / `RALPH_REPO_PATH` from the module docstring at the top of `cli.py`. Rewrite the docstring to describe the post-refactor surface (drain queue, no target flags).

- [ ] Update `ralph_executor/config.py` module docstring: replace the "Repo path resolution" section with a brief "Config sources: defaults < ~/.ralph/config.toml < RALPH_* env vars < CLI flags".

- [ ] Delete the obsolete init tests in `tests/executor/test_setup_cmds.py`:

  - `test_init_writes_user_config_with_explicit_ralph_home`
  - `test_init_assume_yes_uses_os_default` (or rewrite to assert workspace_root default)
  - `test_init_is_idempotent_when_already_configured` (or rewrite for workspace_root)
  - `test_init_overwrites_when_ralph_home_explicit`

  Delete `test_init_subcommand_writes_user_config` from `tests/executor/test_cli.py`; replace with a workspace_root-equivalent assertion (no `--ralph-home`):

  ```python
  def test_main_init_subcommand_writes_workspace_root(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      from ralph_executor.user_config import read_workspace_root

      monkeypatch.setenv("HOME", str(tmp_path))
      monkeypatch.setenv("USERPROFILE", str(tmp_path))
      target = tmp_path / "dev" / "ralph"
      exit_code = cli.main(["init", "--yes"])
      assert exit_code == 0
      # --yes uses the OS default; assert the default path was written.
      from ralph_executor.setup_cmds import _default_workspace_root
      assert read_workspace_root() == _default_workspace_root().resolve()
  ```

  Update the three `test_init_prompts_for_queue_branch` / `test_init_persists_non_default_queue_branch` / `test_init_assume_yes_writes_default_queue_branch` tests in `test_cli.py`. They currently pass `--ralph-home` and answer three input() prompts (`""`, `"<url>"`, `""`). After the rewrite, init's first prompt is `workspace_root` (no `--ralph-home` flag exists). Rewrite the answers iter to:

  ```python
  answers = iter(
      [
          "",                                  # 1. workspace_root prompt -> default
          "https://github.com/test/queue",     # 2. queue_repo prompt
          "",                                  # 3. queue_branch prompt -> default
      ]
  )
  ```

  And remove the `--ralph-home` argument from any `cli.main(["init", ...])` calls — they become `cli.main(["init"])` or `cli.main(["init", "--yes"])`.

- [ ] Run the full executor test suite:

  ```bash
  uv run pytest tests/executor/ -x
  ```

- [ ] Commit:

  ```bash
  git add ralph_executor/cli.py ralph_executor/setup_cmds.py ralph_executor/user_config.py ralph_executor/config.py tests/executor/test_cli.py tests/executor/test_setup_cmds.py
  git commit -m "refactor(init): prompt for workspace_root; drop --ralph-home + RALPH_HOME/RALPH_REPO_PATH"
  ```

---

### Task 10: Delete `cmd_scaffold` and the entire `scaffold` subcommand

After Tasks 7-9 the scaffold subcommand still parses argv (we didn't delete its subparser yet) but its body would now fail on the missing `validate_repo_path`. Delete the subcommand wholesale, its tests, and all helpers (`ScaffoldError`, `_git`, `_has_branch`, `_working_tree_clean`, `CONFIG_TOML_STUB`, `QUEUE_BRANCH`, `QUEUE_SUBDIRS`).

**Files:**

- Modify: `ralph_executor/cli.py` (delete `scaffold_parser` block + dispatch arm)
- Modify: `ralph_executor/setup_cmds.py` (delete `cmd_scaffold`, `ScaffoldError`, `_git`, `_has_branch`, `_working_tree_clean`, `CONFIG_TOML_STUB`, `QUEUE_BRANCH`, `QUEUE_SUBDIRS`)
- Modify: `tests/executor/test_setup_cmds.py` (delete every cmd_scaffold-targeting test, plus the skip-mark from Task 8)
- Modify: `tests/executor/test_cli.py` (delete `test_main_scaffold_subcommand_creates_queue_branch`)

**Steps:**

- [ ] Verify no other module imports anything from the scaffold surface:

  ```bash
  uv run rg "cmd_scaffold|ScaffoldError|CONFIG_TOML_STUB|QUEUE_SUBDIRS" --type py ralph_executor tests scripts skills
  ```

  Expected matches: only in `ralph_executor/setup_cmds.py` (definitions) and `tests/executor/test_setup_cmds.py` (tests). If anything else surfaces, fix that file in this commit.

- [ ] In `ralph_executor/cli.py`, delete the `scaffold_parser` block (around lines 198-230 in the current file) and the dispatch arm in `main`:

  ```python
  if args.subcommand == "scaffold":
      from ralph_executor.setup_cmds import cmd_scaffold
      try:
          scaffold_target = _scaffold_resolve_target(args)
      except ConfigError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2
      return cmd_scaffold(
          repo_path=scaffold_target,
          force=args.force,
          with_config_toml=args.with_config_toml,
      )
  ```

  Both blocks go.

- [ ] In `ralph_executor/setup_cmds.py`, delete:

  - `QUEUE_BRANCH = "ralph-queue"` and `QUEUE_SUBDIRS = ("inbox", "current", "pending-pr", "done", "blocked")`
  - the entire `CONFIG_TOML_STUB = """..."""` constant
  - the entire scaffold section (everything under the `# scaffold` header through end-of-file): `ScaffoldError`, `_git`, `_has_branch`, `_working_tree_clean`, `cmd_scaffold`

  The remaining module is just `cmd_init` and its helpers. Update the module docstring at the top of the file: drop the scaffold mention; the module is now exclusively the `init` subcommand implementation.

- [ ] In `tests/executor/test_setup_cmds.py`, delete every test that calls `cmd_scaffold` or imports `ScaffoldError`. By name:

  - `test_scaffold_creates_branch_dirs_and_commits`
  - `test_scaffold_skips_config_toml_when_flag_false`
  - `test_scaffold_fails_on_dirty_working_tree`
  - `test_scaffold_fails_when_queue_branch_exists_without_force`
  - `test_scaffold_force_switches_into_existing_queue_branch`
  - `test_scaffold_restores_original_branch_on_commit_failure`
  - `test_scaffold_skips_branch_switch_when_reset_fails`
  - `test_scaffold_refuses_detached_head`
  - `test_scaffold_handles_oserror_from_file_creation`
  - `test_scaffold_fails_on_non_git_path`

  Also delete the `pytestmark = pytest.mark.skip(...)` placed at the scaffold section head in Task 8 — the section is now gone.

  Drop the `cmd_scaffold` import line at the top of the file:

  ```python
  from ralph_executor.setup_cmds import (
      cmd_init,
      cmd_scaffold,
      ...
  )
  ```

  Becomes:

  ```python
  from ralph_executor.setup_cmds import cmd_init
  ```

- [ ] In `tests/executor/test_cli.py`, delete `test_main_scaffold_subcommand_creates_queue_branch`.

- [ ] Run the full executor test suite:

  ```bash
  uv run pytest tests/executor/ -x
  ```

  Expected: all green.

- [ ] Commit:

  ```bash
  git add ralph_executor/cli.py ralph_executor/setup_cmds.py tests/executor/test_setup_cmds.py tests/executor/test_cli.py
  git commit -m "feat(cli)!: remove `ralph-executor scaffold` subcommand"
  ```

---

### Task 11: Delete `read_ralph_home` / `write_ralph_home`

The `_warn_stale_ralph_home_in_user_config` helper added in Task 1 reads its own bytes via `tomllib.load`; it does NOT call `read_ralph_home`. After Tasks 7-10, every other caller of `read_ralph_home` / `write_ralph_home` is gone (the cmd_init flow now uses `write_workspace_root`). Delete both functions from `user_config.py`.

**Files:**

- Modify: `ralph_executor/user_config.py` (delete `read_ralph_home`, `write_ralph_home`; update module docstring)

**Steps:**

- [ ] Verify no live callers anywhere:

  ```bash
  uv run rg "read_ralph_home|write_ralph_home" --type py ralph_executor tests scripts skills
  ```

  Expected matches: only inside `ralph_executor/user_config.py` (the definitions themselves) and possibly inside the stale-warning unit tests created in Task 1. Confirm the stale-warning test seeds the user TOML directly via `Path.write_text` and does NOT call `write_ralph_home`.

- [ ] In `ralph_executor/user_config.py`:

  - Delete the `read_ralph_home` function.
  - Delete the `write_ralph_home` function.
  - Update the module docstring. Replace the `ralph_home` mention in the layout list with `workspace_root`:

    ```python
    """Per-user configuration file for ralph-executor.

    Lives at ``~/.ralph/config.toml`` and holds *per-machine* knobs:

      ``workspace_root``     -- root directory under which ralph
                                materialises every queue / target clone
      ``queue_repo``         -- HTTPS URL of the queue repo
      ``queue_branch``       -- branch on the queue repo (default: ralph-queue)
      ``skills_root``        -- source ``skills/`` tree containing ``pr-<host>/``
      ``claude_skills_dir``  -- where staged ``pr/`` ends up
                                (defaults to ``~/.claude/skills``)

    Written by ``ralph-executor init`` and read at runtime. The matching
    environment variables (``$RALPH_WORKSPACE``, ``$RALPH_QUEUE_REPO``,
    ``$RALPH_QUEUE_BRANCH``, ``$RALPH_SKILLS_ROOT``,
    ``$RALPH_CLAUDE_SKILLS_DIR``) override the file for the daemon /
    systemd escape hatch case.
    """
    ```

- [ ] Run the full test suite to confirm no implicit deps:

  ```bash
  uv run pytest tests/executor/ -x
  ```

- [ ] Commit:

  ```bash
  git add ralph_executor/user_config.py
  git commit -m "refactor(user-config): delete read_ralph_home/write_ralph_home (callers gone)"
  ```

---

### Task 12: ~~Multi-target integration test~~ **DROPPED**

Removed during plan review (2026-05-31). Multi-target is N invocations of the
single-target codepath; coverage already provided by:

- T3-T6 refactor tests assert per-iteration `target_path` comes from
  `pbi.target_info`, not `cfg.repo_path`.
- Existing `test_loop_integration.py` covers the queue → PBI → ensure_clone →
  worktree → spawn chain for a single iteration.
- T15 (final smoke test) exercises multi-target manually.

No replacement task. Skip Task 12 during execution. The remaining text below
is preserved for plan-history; do NOT implement it.

---

#### Original Task 12 body (preserved for history — DO NOT IMPLEMENT)

Add the spec-required smoke test that demonstrates a queue with two distinct `target_repo` URLs is processed end-to-end in one run.

**Files:**

- Create: `tests/executor/test_loop_multi_target.py`

**Steps:**

- [ ] Write the integration test:

  ```python
  """Multi-target queue: PBIs across two distinct target_repo URLs are
  processed in one executor run.

  Asserts:
      * Both target URLs are passed to ensure_clone.
      * Both PBIs leave inbox/ and land in pending-pr/ or done/.
      * The executor uses the per-PBI clone root for each iteration,
        not a single process-wide repo path.
  """
  from pathlib import Path

  import pytest

  from ralph_executor.config import ExecutorConfig
  from ralph_executor.loop import run_loop


  def test_multi_target_run_drains_two_targets(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
      cfg_for_repo: ExecutorConfig,
      fake_repo: Path,
  ) -> None:
      # Seed two inbox PBIs with distinct target_repo URLs.
      from tests.executor.helpers import write_pbi_with_target_repo  # already exists

      write_pbi_with_target_repo(
          fake_repo,
          pbi_id="WI-1",
          target_repo="https://github.com/acme/widget-a",
      )
      write_pbi_with_target_repo(
          fake_repo,
          pbi_id="WI-2",
          target_repo="https://github.com/acme/widget-b",
      )

      observed_targets: list[str] = []

      from ralph_executor.target_clone import TargetClone
      from ralph_executor.url_utils import TargetRepoInfo

      def _fake_ensure_clone(info: TargetRepoInfo, workspace_root: Path) -> TargetClone:
          observed_targets.append(f"{info.owner}/{info.name}")
          clone_root = workspace_root / "clones" / info.owner / info.name
          if not (clone_root / ".git").exists():
              import subprocess
              clone_root.mkdir(parents=True, exist_ok=True)
              subprocess.run(["git", "init", "-q", str(clone_root)], check=True)
              subprocess.run(["git", "-C", str(clone_root), "commit", "--allow-empty", "-m", "init"], check=True)
              subprocess.run(["git", "-C", str(clone_root), "branch", "-M", "main"], check=True)
          return TargetClone(info=info, clone_root=clone_root)

      monkeypatch.setattr("ralph_executor.target_clone.ensure_clone", _fake_ensure_clone)

      # Stub spawn_claude_p to return pr_created for both PBIs.
      from ralph_executor.claude_spawn import ClaudeOutcome

      def _fake_spawn(cfg: ExecutorConfig, pbi, *, cwd=None, pbi_dir=None):
          return ClaudeOutcome(
              kind="pr_created",
              pr_url=f"https://github.com/acme/widget-x/pull/1?pbi={pbi.id}",
              stdout="",
              stderr="",
              exit_code=0,
              duration_seconds=0.1,
          )

      monkeypatch.setattr("ralph_executor.loop.spawn_claude_p", _fake_spawn)
      # Don't actually run gh / sweep over the fake clones.
      monkeypatch.setattr("ralph_executor.loop._query_open_pr_via_gh", lambda *a, **k: None)

      # Drain.
      results = list(run_loop(cfg_for_repo))

      # Two PBIs processed; both target URLs observed.
      assert sorted(observed_targets) == ["acme/widget-a", "acme/widget-b"]
      ran_pbis = sorted(r.pbi_id for r in results if r.pbi_id is not None)
      assert ran_pbis == ["WI-1", "WI-2"]
  ```

  Add `tests/executor/helpers.py::write_pbi_with_target_repo` if it doesn't already exist (it's a thin wrapper over the existing `write_sample_pbi` plus a frontmatter edit).

- [ ] Run the test:

  ```bash
  uv run pytest tests/executor/test_loop_multi_target.py -x
  ```

  Expected: green, since by this point the refactor handles per-iteration target paths correctly.

- [ ] Commit:

  ```bash
  git add tests/executor/test_loop_multi_target.py tests/executor/helpers.py
  git commit -m "test(loop): multi-target queue drains two distinct target_repo URLs"
  ```

---

### Task 13: Update README.md

**Files:**

- Modify: `README.md`

**Steps:**

- [ ] Verify the README sections that need deleting still exist:

  ```bash
  uv run rg "^## Per-repo setup|^## Running multiple ralphs" README.md
  ```

  Expected: matches for both.

- [ ] Open `README.md` and make the following edits:

  **(a) Configuration keys table** (around lines 60-80): delete the `ralph_home` row entirely. Update any neighbouring rows that mention `--workspace` / `--repo` defaults.

  **(b) `init` flags section** (around lines 74-87): replace

  ```
  `init` only accepts two flags: `--ralph-home PATH` (skip the ralph_home
  prompt) and `--yes` (non-interactive: OS default for `ralph_home`, default
  ...)
  ```

  with

  ```
  `init` only accepts one flag: `--yes` (non-interactive: OS default for
  `workspace_root`, default `ralph-queue` for `queue_branch`, `queue_repo`
  must be added manually post-init).
  ```

  **(c) `~/.ralph/config.toml` example** (around line 86): delete the `ralph_home = "/opt/ralph"` line; keep `workspace_root`, `queue_repo`, `queue_branch`.

  **(d) "Per-repo setup" section** (around lines 167-200): delete the entire section, including the `# 1. Clone the target repo into $RALPH_HOME/<name>` block and the `uv run ralph-executor scaffold --workspace repo` line. Replace with a single paragraph:

  ```
  ## Working the queue

  Add a PBI with `ralph-new` (interactive) or by writing a Markdown
  file under `.ralph/inbox/<PBI-ID>/PBI.md` on the queue repo's
  `ralph-queue` branch. The PBI's `target_repo` frontmatter field
  points at the GitHub repo to be modified. Ralph clones every target
  it encounters under `<workspace_root>/clones/<owner>/<name>/`.
  ```

  **(e) "Running ralph" section** (around lines 213-265): collapse to:

  ```
  ## Running ralph

  ```bash
  uv run ralph-executor             # drain queue
  uv run ralph-executor --watch     # daemon
  uv run ralph-executor --once      # single iteration
  ```

  Drop the `--repo` / `--workspace` examples and the `RALPH_WATCH_MODE` /
  `RALPH_IDLE_EXIT_THRESHOLD` lines (those env vars survive but are
  rarely needed).
  ```

  **(f) "Running multiple ralphs" section** (around lines 267-290): delete the entire section. Add a single sentence to the end of "Running ralph":

  ```
  One executor drains the whole queue across every distinct `target_repo`
  URL. No second-terminal / second-workspace setup is needed.
  ```

  **(g) "Configuration precedence" section** (around lines 295-330): replace

  ```
  1. CLI flags (`--repo`, `--workspace`, `--log-level`)
  2. Environment variables (`RALPH_*`)
  3. Project TOML (`<repo>/.ralph/config.toml`)
  4. User TOML (`~/.ralph/config.toml`)
  5. Defaults
  ```

  with

  ```
  1. CLI flags (`--log-level`, `--queue-repo`, `--queue-branch`, `--watch`)
  2. Environment variables (`RALPH_*`)
  3. User TOML (`~/.ralph/config.toml`)
  4. Defaults
  ```

  Delete the `$RALPH_HOME` paragraph immediately below the precedence list and the trailing example showing `ralph_home = "C:/dev/ralph"`.

  **(h) Scan for any leftover `--repo` / `--workspace` / `RALPH_HOME` / `ralph_home` references** and remove them.

- [ ] Verify no stale references remain:

  ```bash
  uv run rg "ralph_home|RALPH_HOME|--workspace|--repo|--ralph-home|scaffold" README.md
  ```

  Expected: no matches.

- [ ] Commit:

  ```bash
  git add README.md
  git commit -m "docs(readme): drop manual-target-picking surfaces; queue is single source of truth"
  ```

---

### Task 14: Update `docs/runbooks/ralph-setup.md`

**Files:**

- Modify: `docs/runbooks/ralph-setup.md`

**Steps:**

- [ ] Verify the existing stale references:

  ```bash
  uv run rg "ralph_home|RALPH_HOME|--workspace|--repo|--ralph-home|scaffold" docs/runbooks/ralph-setup.md
  ```

- [ ] Open `docs/runbooks/ralph-setup.md` and make these edits:

  **(a) `init` section** (around line 84): rewrite to mention `workspace_root` instead of `ralph_home`. Replace:

  ```
  Interactively prompts for `ralph_home` and `queue_repo`, then for
  `queue_branch`. `--yes` accepts the OS default for `ralph_home`...
  ```

  with:

  ```
  Interactively prompts for `workspace_root`, `queue_repo`, and `queue_branch`.
  `--yes` accepts the OS default for `workspace_root` (`~/ralph-workspaces`),
  skips the `queue_repo` prompt with a warning, and writes the default
  `queue_branch` ("ralph-queue").
  ```

  **(b) Config table** (around line 142): delete the `ralph_home` row.

  **(c) Top-level CLI flags table** (around lines 206-207): delete the `--repo PATH` and `--workspace NAME` rows. Keep the surviving flags (`--log-level`, `--queue-repo`, `--queue-branch`, `--watch`, `--once`, `--iterations`).

  **(d) `init` subcommand flags table** (around lines 223-224): delete the `--ralph-home PATH` row. Update the `--yes` row's text to drop the mention of "OS default for `ralph_home`"; replace with "OS default for `workspace_root`".

  **(e) `scaffold` subcommand section** (around lines 226-235): delete the entire subsection.

  **(f) `reconcile` flags table** (around lines 277-278): delete `--repo PATH` and `--workspace NAME` rows.

  **(g) Top-level "set of queue-targeting flags" sentence** (around line 284): drop the `--workspace` mention.

  **(h) Skill-specific flag tables** (around lines 314, 330, 346, 363, 379): scrub each `--workspace PATH` row that overrides `workspace_root`. These are operator-skill flags (NOT executor flags) — verify each one. Skills under `skills/ralph-*/scripts/` still use `--workspace PATH` to override `workspace_root` for skill invocation; the spec does NOT remove this. Leave the skill `--workspace` rows in place if they predate this refactor; the spec is scoped to the executor, not operator skills. Confirm by checking one:

  ```bash
  uv run rg "argparse|add_argument.*workspace" skills/ralph-new
  ```

  If a skill still parses `--workspace PATH` legitimately, the doc row stays. Only the **executor's** `--workspace NAME` references are being removed.

  **(i) Errors table** (around lines 452-453): delete the two `--workspace name must be a plain directory name` and `--workspace needs a ralph_home root` rows.

  **(j) Final sweep** — re-grep:

  ```bash
  uv run rg "ralph_home|RALPH_HOME|--ralph-home|scaffold" docs/runbooks/ralph-setup.md
  ```

  Expected: no matches.

  ```bash
  uv run rg "--workspace|--repo" docs/runbooks/ralph-setup.md
  ```

  Expected: only matches inside operator-skill flag tables (sections 314, 330, 346, 363, 379), not under top-level executor flags.

- [ ] Commit:

  ```bash
  git add docs/runbooks/ralph-setup.md
  git commit -m "docs(setup): drop --repo/--workspace/--ralph-home/scaffold/ralph_home from executor surface"
  ```

---

### Task 15: ~~Update `docs/runbooks/ralph-architecture.md`~~ **DROPPED**

Removed during plan review (2026-05-31). The architecture-diagram redesign
is subjective and not mechanically verifiable; user opted to handle this
in a separate follow-up doc PR by hand. No replacement task in this plan.

Skip Task 15 during execution. The remaining text below is preserved for
plan-history; do NOT implement it.

---

#### Original Task 15 body (preserved for history — DO NOT IMPLEMENT)

**Files:**

- Modify: `docs/runbooks/ralph-architecture.md`

**Steps:**

- [ ] Open the file and update the ASCII diagram in the "The three pieces" section (lines 12-36). The current diagram already names `workspace_root`-based paths (`~/ralph-workspaces/<env>/`), but the `clones/<owner>-<name>/` notation uses a dash; the spec uses `clones/<owner>/<name>/` (nested folders). Update the diagram:

  ```
  +----------------------------------------------------+
  |  ~/ralph-workspaces/   (operator workspace_root)   |
  |                                                    |
  |    queue/                  <- clone of ralph-queue |
  |       .ralph/...           on the ralph-queue br.  |
  |                                                    |
  |    clones/<owner>/<name>/  <- one per target_repo  |
  |       .ralph-work/<PBI>/   <- per-PBI worktree     |
  +----------------------------------------------------+
  ```

  Update the table row for `~/ralph-workspaces/<env>/` (line 42) accordingly: drop `<env>`, change `clones/<owner>-<name>/` to `clones/<owner>/<name>/`, drop any mention of a "per-repo checkout convention".

- [ ] In "Data flow per iteration" (around lines 52-77), update step 4:

  ```
  4. Clones `target_repo` to `<workspace>/clones/<owner>/<name>/` if
     absent (one clone per target, reused across PBIs).
  ```

  Update step 5:

  ```
  5. Creates a per-PBI worktree at
     `<clones>/<owner>/<name>/.ralph-work/<PBI-id>/` on branch
     `ralph/<PBI-id>`.
  ```

  Drop the "(worktree mode is mandatory — Stage A is gone)" parenthetical from step 5 — that historical note is no longer needed once the doc is fully aligned.

- [ ] Verify no `ralph_home` references remain (none today, but double-check):

  ```bash
  uv run rg "ralph_home|RALPH_HOME|--ralph-home|--workspace NAME|--repo PATH" docs/runbooks/ralph-architecture.md
  ```

  Expected: no matches.

- [ ] Commit:

  ```bash
  git add docs/runbooks/ralph-architecture.md
  git commit -m "docs(architecture): align workspace paths with queue-driven cleanup"
  ```

---

### Task 16: Update `docs/runbooks/ralph-queue-setup.md` and `scripts/start-ralph.ps1`

Two small touch-ups grouped into one commit since neither is large.

**Files:**

- Modify: `docs/runbooks/ralph-queue-setup.md`
- Modify: `scripts/start-ralph.ps1`

**Steps:**

- [ ] Sweep `docs/runbooks/ralph-queue-setup.md` for stale references:

  ```bash
  uv run rg "ralph_home|RALPH_HOME|--ralph-home|scaffold" docs/runbooks/ralph-queue-setup.md
  ```

  Delete or rewrite any matches found. The runbook is queue-setup focused so most should already be clean; expect 0-3 matches max.

- [ ] Update `scripts/start-ralph.ps1`. Replace the existing file body with:

  ```powershell
  #Requires -Version 5.1
  <#
  .SYNOPSIS
      Ralph startup script.

  .DESCRIPTION
      Verifies prerequisites (uv, gh, claude, ~/.ralph/config.toml), syncs the
      venv, and launches ralph-executor. The queue PBI's target_repo
      frontmatter is the single source of truth for which target repo the
      executor works on; no -Workspace parameter is needed.

  .PARAMETER Watch
      Pass --watch to ralph-executor (daemon mode; survives idle).

  .PARAMETER Once
      Pass --once to ralph-executor (single iteration).

  .PARAMETER LogLevel
      Log level forwarded to ralph-executor (DEBUG, INFO, WARNING, ERROR).

  .EXAMPLE
      .\scripts\start-ralph.ps1
      .\scripts\start-ralph.ps1 -Watch
      .\scripts\start-ralph.ps1 -Once
  #>
  [CmdletBinding()]
  param(
      [switch]$Watch,
      [switch]$Once,
      [ValidateSet('DEBUG', 'INFO', 'WARNING', 'ERROR')]
      [string]$LogLevel = 'INFO'
  )

  $ErrorActionPreference = 'Stop'

  function Require-Command {
      param([string]$Name, [string]$Hint)
      if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
          throw "Missing prerequisite: '$Name' not on PATH. $Hint"
      }
  }

  $repoRoot = Split-Path -Parent $PSScriptRoot
  Set-Location -Path $repoRoot
  Write-Host "[start-ralph] repo:      $repoRoot"

  Require-Command uv     'Install from https://docs.astral.sh/uv/'
  Require-Command git    'Install git for Windows.'
  Require-Command gh     'Install GitHub CLI: https://cli.github.com/'
  Require-Command claude 'Install Claude Code CLI: https://docs.claude.com/claude-code'

  $configPath = Join-Path $HOME '.ralph/config.toml'
  if (-not (Test-Path $configPath)) {
      Write-Host "[start-ralph] missing $configPath -- running 'uv run ralph-executor init'"
      & uv run ralph-executor init
      if ($LASTEXITCODE -ne 0) { throw "ralph-executor init failed (exit $LASTEXITCODE)" }
  }

  $ghStatus = & gh auth status 2>&1
  if ($LASTEXITCODE -ne 0) {
      Write-Warning "gh auth status failed. Run: gh auth login"
      Write-Host $ghStatus
  }

  Write-Host "[start-ralph] syncing venv (uv sync)..."
  & uv sync
  if ($LASTEXITCODE -ne 0) { throw "uv sync failed (exit $LASTEXITCODE)" }

  $cmdArgs = @('run', 'ralph-executor', '--log-level', $LogLevel)
  if ($Watch) { $cmdArgs += '--watch' }
  if ($Once)  { $cmdArgs += '--once' }

  Write-Host "[start-ralph] launching: uv $($cmdArgs -join ' ')"
  & uv @cmdArgs
  exit $LASTEXITCODE
  ```

- [ ] Sanity-check the PowerShell parses (PowerShell isn't always available on the dev box; check by hand and via the existing CI if applicable):

  ```bash
  # On Windows
  powershell -NoProfile -Command "Get-Command -Syntax (Resolve-Path scripts/start-ralph.ps1).Path"
  ```

  If PowerShell parser flags the file, fix syntax and re-run.

- [ ] Commit:

  ```bash
  git add docs/runbooks/ralph-queue-setup.md scripts/start-ralph.ps1
  git commit -m "docs+script(start-ralph): drop -Workspace; queue is single source of truth"
  ```

---

### Task 17: Final smoke test

End-to-end manual verification that the refactor works against a fresh queue with two distinct target repos. Covered by the manual smoke checklist in the spec; codify it as a doc + a green test run.

**Files:**

- Modify: (none; this task is verification-only)

**Steps:**

- [ ] Run the full executor test suite:

  ```bash
  uv run pytest tests/executor/ -x
  ```

  Expected: every test green.

- [ ] Run the full repo test suite (catches anything we missed in skills / scripts):

  ```bash
  uv run pytest -x
  ```

  Expected: every test green. If a skill test fails because of a missing `--workspace` flag, that skill predates this refactor — confirm scope: this PR is **executor-only**. Operator-skill `--workspace PATH` flags survive (per spec note about `--workspace PATH` overriding `workspace_root` in skill tables).

- [ ] Static checks:

  ```bash
  uv run ruff check ralph_executor tests
  uv run mypy ralph_executor
  ```

  Expected: clean (or unchanged from baseline).

- [ ] Manual smoke test:

  ```bash
  # Set up a temp workspace
  WS=$(mktemp -d)
  HOME_BAK=$HOME
  export HOME=$WS

  # Init
  uv run ralph-executor init --yes
  cat $HOME/.ralph/config.toml
  # Expected: workspace_root, queue_repo (warning since --yes skipped), queue_branch

  # Add queue_repo manually
  echo 'queue_repo = "<your queue URL>"' >> $HOME/.ralph/config.toml

  # Run a single iteration
  uv run ralph-executor --once

  # Expected: exit 0, queue clone materialised under workspace_root/queue,
  # no `--repo` / `--workspace` errors.

  export HOME=$HOME_BAK
  ```

- [ ] Final sweep for residual references:

  ```bash
  uv run rg "ExecutorConfig\.repo_path|cfg\.repo_path|_resolve_workspace|_scaffold_resolve_target|read_ralph_home|write_ralph_home|cmd_scaffold|ScaffoldError|RALPH_HOME|RALPH_REPO_PATH|--ralph-home|ralph_home" --type py
  ```

  Expected: only stale-warning-related references (`_warn_stale_ralph_home_in_user_config`, the `ralph_home` literal inside that helper and its tests). Everything else is gone.

- [ ] Commit (none required for this task; it's verification only). If any docs caught up in the final sweep need a tweak, commit them:

  ```bash
  git commit -m "chore: final cleanup of residual references after queue-driven refactor"
  ```

---

## Self-review

- [x] **(a) Every spec requirement covered.** Cross-check against the spec's "Deleted" sections:
  - `--repo PATH` flag — deleted in Task 7 + Task 4.
  - `--workspace NAME` flag — deleted in Task 7 + Task 4.
  - `--ralph-home PATH` flag (init) — deleted in Task 9.
  - `$RALPH_HOME` env — Task 9.
  - `$RALPH_REPO_PATH` env — Task 9.
  - `ralph_home` TOML key — Task 11 (delete reader/writer; Task 1 added the WARN).
  - `ralph-executor scaffold` subcommand — Task 10.
  - `ExecutorConfig.repo_path` field — Task 8.
  - `<target>/.ralph/config.toml` loading — Task 8 (project TOML loader removed).
  - One-time WARN for stale `ralph_home` — Task 1.
  - Per-iteration WARN for project TOML in target clone — Task 2.
  - Multi-target integration test — Task 12.
  - README "Per-repo setup" / "Running multiple ralphs" deleted — Task 13.
  - `docs/runbooks/ralph-setup.md` rewritten — Task 14.
  - `docs/runbooks/ralph-architecture.md` updated — Task 15.
  - `scripts/start-ralph.ps1` updated — Task 16.

- [x] **(b) No "TODO" / "TBD" / "similar to" placeholders.** Every step lists the exact functions, files, line ranges, and code snippets.

- [x] **(c) Consistent function / field names across tasks.** `_warn_stale_ralph_home_in_user_config` is referenced by exact name in Tasks 1, 8, 9, 11, 17. `_warn_project_toml_in_target_clone` is referenced by exact name in Tasks 2, 17. `_build_minimal_cfg` is introduced in Task 3 and reused in Tasks 5, 12. `write_workspace_root` is introduced in Task 9.

## Execution Handoff

Two options for executing this plan.

### Option A — Subagent-driven (recommended)

Use `superpowers:subagent-driven-development`. Dispatch each Task as its own subagent batch (implementer + spec reviewer + code-quality reviewer). The plan is structured so each Task ends with a green test suite and a single conventional commit — perfect granularity for parallel review.

When dispatching, attach the relevant files in `**Files:**` to the subagent's prompt and copy the task's Steps block verbatim. The subagent runs the failing-test → impl → passing-test → commit cycle without further input from you. Review the commit before moving to the next task.

Dependencies between tasks are strict in the order listed:

- Task 1 (stale-`ralph_home` WARN) can run any time, but is grouped first because it's small and self-contained.
- Task 2 (project-TOML WARN) is independent of Task 1.
- Tasks 3-6 (`cfg.repo_path` consumer refactors) must complete before Task 7 (helper deletions) before Task 8 (field deletion).
- Tasks 9-11 (env / scaffold / `read_ralph_home` deletions) run after Task 8.
- Task 12 (multi-target test) runs after Task 8 (it needs the field gone).
- Tasks 13-16 (docs + start-ralph) run after Task 11 (so the docs describe the final state).
- Task 17 (final smoke) runs last.

### Option B — Inline single-session

Use `superpowers:executing-plans`. Execute every task in sequence in one Claude session. The plan is designed for this — each task ends with a commit, so any failure mid-plan leaves a clean head you can resume from.

Pause after Task 8 (the `ExecutorConfig.repo_path` field deletion) and after Task 10 (the scaffold subcommand deletion) to manually verify the executor still runs `uv run ralph-executor --once` against a real queue. Both are large blast-radius deletions; eyeballing the runtime catches anything the test suite missed.
