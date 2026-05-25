# `ralph-doctor` Preflight Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `ralph-doctor`, the preflight gate that verifies the host environment (laptop OR pod) is ralph-safe BEFORE `ralph-executor` starts. The spec is unambiguous: every silent pod failure has the same root cause — `claude -p` hit a permission prompt because some hook or skill expected a user. The doctor catches that class of failure once, on cold start, and refuses to let the pod run if anything ralph-unsafe is configured. Six checks land in v1, in this order: (1) `permissions.allow` covers Bash, Edit, Write, Read, Grep, Glob, Skill, and the `ado-pr` skill by exact name; (2) no active hook calls `AskUserQuestion` or blocks on stdin; (3) no installed skill calls `AskUserQuestion` in its main path; (4) MCP servers all use non-interactive auth (no OAuth refresh prompts); (5) Anthropic (or Bedrock under `RALPH_USE_BEDROCK=1`) auth resolves on cold start via a free, no-op probe; (6) the ADO PAT works by calling pull-request-show against a non-existent PR ID and asserting HTTP 404. Each check returns a `CheckResult`; the runner aggregates them, prints JSON to stdout, prints a human summary to stderr, exits 0 only if every `error`-severity check passes (`warn` logs but does not block, unless `--strict` is set).

**Architecture:** A Claude Code skill at `skills/ralph-doctor/` with one `SKILL.md` plus `scripts/check.py` (the runner) and a `scripts/checks/` package containing one module per check. The runner discovers checks at import time via `importlib.util.spec_from_file_location` (the parent directory name `ralph-doctor` contains a hyphen and is not a valid Python identifier — every load goes through `spec_from_file_location`). The settings path, skills directory, and per-check skip/only filters are CLI-configurable so tests can pass synthetic configs. Tests use `tmp_path` for synthetic settings.json and skill trees plus the `responses` library to mock the Anthropic and ADO REST endpoints.

**Tech Stack:** Python 3.12+, `uv`, `requests`, `responses` (tests), `pytest`, ruff, mypy strict, Anthropic Messages API (token-count endpoint), Azure DevOps REST API 7.1 (re-using `scripts.ado_client` from Plan 2 for shape, but calling `requests` directly so the doctor can distinguish 404 from other failures).

---

## File Structure

| Path | Responsibility |
|---|---|
| `skills/ralph-doctor/SKILL.md` | Frontmatter (`name: ralph-doctor`, `description: ...`) + body documenting purpose, inputs, env vars, exit codes, and invocation patterns. Read by Claude Code's Skill tool at discovery time. |
| `skills/ralph-doctor/scripts/__init__.py` | Empty package marker (`"""Empty package marker."""`). Tests import the runner via `importlib.util.spec_from_file_location`; the marker exists so ruff/mypy see the directory as a package. |
| `skills/ralph-doctor/scripts/check.py` | Runner. Argparse CLI; loads settings.json from `--settings` (default `~/.claude/settings.json`); applies `--skip` / `--only` filters; imports each module in `scripts.checks.*` lazily; calls its `check(context)`; aggregates results; emits JSON to stdout; emits human summary to stderr unless `--json`; returns 0/1/2. |
| `skills/ralph-doctor/scripts/checks/__init__.py` | Defines `CheckContext` (the runner's input to each check), `CheckResult` (each check's return shape), `Severity` literal (`error`, `warn`), `Status` literal (`pass`, `fail`, `skipped`), and the `REGISTRY` tuple naming each check module in stable execution order. |
| `skills/ralph-doctor/scripts/checks/permissions.py` | `check()` reads `settings.json["permissions"]["allow"]`. Asserts coverage for `Bash`, `Edit`, `Write`, `Read`, `Grep`, `Glob`, `Skill`, plus skill `ado-pr`. Wildcards (`*`, `Bash(*)`, `Skill(*)`) count as coverage. |
| `skills/ralph-doctor/scripts/checks/hooks.py` | `check()` walks `settings.json["hooks"]`, scanning each `command` for `AskUserQuestion`, `input(`, `read -p`, `Read-Host`. Matches on `async: true` hooks downgrade to warn severity. |
| `skills/ralph-doctor/scripts/checks/skills.py` | `check()` walks `~/.claude/skills/<skill>/SKILL.md` + `scripts/*.py` for `AskUserQuestion`. Honours `<!-- ralph-doctor: ignore -->` markers in markdown and `# noqa: ralph-doctor` in Python lines. Missing skills dir → pass with note. |
| `skills/ralph-doctor/scripts/checks/mcp.py` | `check()` reads `mcpServers` from settings.json and any sibling `.mcp.json`. Flags any server whose `command`, `args`, or `env` contains `oauth`/`OAuth`/`--auth`/`--login`/`BROWSER`. Empty config → pass. |
| `skills/ralph-doctor/scripts/checks/auth.py` | `check()` POSTs a single-message body to `/v1/messages/count_tokens` (model `claude-haiku-4-5`, no quota consumed). 2xx → pass; else fail. Under `RALPH_USE_BEDROCK=1`, calls `boto3.client('bedrock').list_foundation_models()` with a 5s timeout. |
| `skills/ralph-doctor/scripts/checks/ado.py` | `check()` GETs `git/repositories/{repo}/pullrequests/999999999`. 404 → pass (proves PAT auth + project routing). Any other status → fail. Requires `ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT`, `ADO_REPOSITORY`. |
| `tests/skills/test_ralph_doctor.py` | Pytest tests. Synthetic settings.json files via `tmp_path`; `responses` mocks Anthropic + ADO endpoints. Covers each pass/fail branch of every check plus the runner's exit-code matrix. |
| `pyproject.toml` | No change required — Plan 3 already added `skills/` and `tests/skills/` to mypy `files` and pytest `testpaths`. Re-asserted in Task 1. |

---

## Task 1 — Confirm preconditions and create the skill scaffold

**Files**
- Create: `skills/ralph-doctor/SKILL.md`
- Create: `skills/ralph-doctor/scripts/__init__.py`

**Steps**

- [ ] 1. Confirm Plans 1, 2, and 3 are merged so the toolchain is wired correctly:
  ```
  uv run pytest tests/test_workspace_samples.py tests/test_ado_client.py tests/skills/test_ralph_add.py -v
  ```
  Expected: every test passes. If any fail, STOP — Plan 11 depends on `scripts.ado_client` (Plan 2) and the `skills/` package layout (Plan 3).

- [ ] 2. Verify `pyproject.toml` already includes `skills` and `tests/skills` in mypy's `files` and pytest's `testpaths`:
  ```
  uv run python -c "import tomllib; data=tomllib.load(open('pyproject.toml','rb')); assert 'skills' in data['tool']['mypy']['files'], data['tool']['mypy']['files']; assert 'tests' in data['tool']['pytest']['ini_options']['testpaths']"
  ```
  Expected: exit code 0. If the assertion fails, STOP and rerun Plan 3 task 1.

- [ ] 3. Create `skills/ralph-doctor/scripts/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 4. Create `skills/ralph-doctor/SKILL.md` with the content below. The frontmatter is the contract Claude Code's Skill tool reads at discovery time. Keep the fenced markdown blocks inside `SKILL.md` (the JSON example, the table) — they are part of the documented contract.
  ````markdown
  ---
  name: ralph-doctor
  description: Verify the host environment (laptop or pod) is ralph-safe BEFORE the executor starts. Runs six preflight checks — permissions.allow coverage, hooks free of AskUserQuestion / stdin reads, skills free of AskUserQuestion in their main path, MCP servers configured with non-interactive auth, Anthropic (or Bedrock) auth resolves on cold start, ADO PAT has read + create-PR scope — and refuses to let Ralph start if any error-severity check fails. Reads ~/.claude/settings.json by default; the path is configurable via --settings for tests and alternative install layouts.
  ---

  # ralph-doctor

  ## What this skill does

  `ralph-doctor` is the preflight gate for `ralph-executor`. It catches the
  one class of failure that silently kills unattended pods: a hook or skill
  that expects a human (an `AskUserQuestion` call, a `read -p` prompt, an
  OAuth refresh). The spec is explicit about this — see
  `docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md`
  section "ralph-doctor checks". If `ralph-doctor` cannot pass on the
  target container image, the pod does not start.

  ## When to use it

  - Manually on a developer laptop before pushing a container image:
    `python skills/ralph-doctor/scripts/check.py`
  - Automatically as the container's entrypoint's first step:
    `python /opt/ralph/skills/ralph-doctor/scripts/check.py && exec
    ralph-executor`. Non-zero exit terminates the entrypoint before the
    executor spawns.
  - In CI as a gating job on the ralph repo and on consumer service repos.

  ## Inputs

  | Flag | Required | Description |
  |---|---|---|
  | `--settings <path>` | no | Path to settings.json. Default `~/.claude/settings.json`. |
  | `--skills-dir <path>` | no | Path to installed skills. Default `~/.claude/skills`. |
  | `--skip <name[,name...]>` | no | Check names to skip (e.g. `--skip auth,ado` for offline runs). |
  | `--only <name[,name...]>` | no | Check names to run; everything else is skipped. Mutually exclusive with `--skip`. |
  | `--json` | no | Suppress the human summary on stderr; emit JSON only. |
  | `--strict` | no | Treat warn-severity failures as errors. |

  ## Environment variables

  | Variable | Used by | Purpose |
  |---|---|---|
  | `ANTHROPIC_API_KEY` | `auth` | Anthropic Messages API key. Required unless `RALPH_USE_BEDROCK=1`. |
  | `RALPH_USE_BEDROCK` | `auth` | Set to `1` to probe AWS Bedrock instead of Anthropic. |
  | `ADO_PAT` | `ado` | ADO Personal Access Token. Required. |
  | `ADO_ORG_URL` | `ado` | ADO org URL. Required. |
  | `ADO_PROJECT` | `ado` | ADO project name. Required. |
  | `ADO_REPOSITORY` | `ado` | ADO repository name (the probe targets this repo). Required. |
  | `RALPH_LOG_LEVEL` | runner | `INFO` (default), `DEBUG`, `WARNING`. Controls stderr verbosity. |

  ## Exit codes

  | Code | Meaning |
  |---|---|
  | `0` | Every error-severity check passed. |
  | `1` | At least one error-severity check failed. Pod must NOT start. |
  | `2` | Internal failure (missing/malformed settings.json, mutually-exclusive CLI flags, unknown check name). |

  ## Output

  Stdout: single JSON document. Stderr: human summary (suppressed by
  `--json`). Example:

  ```json
  {
    "ok": true,
    "exit_code": 0,
    "summary": {"errors": 0, "warns": 0, "passes": 6, "skips": 0},
    "checks": [
      {"name": "permissions", "severity": "error", "status": "pass",
       "message": "permissions.allow covers all 7 required tools and 1 required skill."}
    ]
  }
  ```

  ## How it is invoked

  ```bash
  uv run python skills/ralph-doctor/scripts/check.py
  uv run python skills/ralph-doctor/scripts/check.py --skip auth,ado --json
  uv run python skills/ralph-doctor/scripts/check.py --strict --only permissions,hooks
  ```

  Tests live at `tests/skills/test_ralph_doctor.py`.

  ## The six checks

  | Check | Severity | What it asserts |
  |---|---|---|
  | `permissions` | error | `permissions.allow` covers Bash, Edit, Write, Read, Grep, Glob, Skill, and `ado-pr` (wildcards honoured). |
  | `hooks` | error | No active hook contains `AskUserQuestion`, `input(`, `read -p`, or `Read-Host`. `async: true` matches → warn. |
  | `skills` | error | No installed skill's `SKILL.md` or `scripts/*.py` calls `AskUserQuestion` (heuristic substring scan). |
  | `mcp` | error | No MCP server requires OAuth / browser redirect (`oauth`, `--auth`, `--login`, `BROWSER`). |
  | `auth` | error | Anthropic (or Bedrock if `RALPH_USE_BEDROCK=1`) auth resolves on cold start via a no-op API call. |
  | `ado` | error | `pullrequests/999999999` returns HTTP 404 (proves PAT auth + project routing). |

  ## What this skill does NOT do

  - It does not modify settings.json. Findings are read-only.
  - It does not check whether `ralph-executor` itself is installed (Plan 12).
  - It does not check git remote access (Plan 12 covers `git ls-remote`).
  - It does not exercise `claude -p` itself — the auth probe is a cheap proxy.

  ## Trade-offs

  The `skills` check is heuristic. A skill that hides `AskUserQuestion`
  behind a dynamic call (`getattr(self, 'Ask' + 'UserQuestion')()`)
  evades the substring scan. v2 may add AST-based scanning; v1
  optimises for false-positive resistance by honouring
  `<!-- ralph-doctor: ignore -->` markers in markdown and
  `# noqa: ralph-doctor` in Python lines.
  ````
  Expected: file is exactly that content; the frontmatter block is bounded by the two `---` fences.

- [ ] 5. Stage and commit:
  ```
  git add skills/ralph-doctor/SKILL.md skills/ralph-doctor/scripts/__init__.py
  git commit -m "chore(skills): scaffold ralph-doctor skill (SKILL.md, package marker)"
  ```

---

## Task 2 — Define the check protocol

**Files**
- Create: `skills/ralph-doctor/scripts/checks/__init__.py`

**Steps**

- [ ] 1. Create `skills/ralph-doctor/scripts/checks/__init__.py` with exactly the content below. This module is the contract every individual check obeys. The frozen dataclasses are deliberate — checks must not mutate the context, and the runner must not mutate results after collection.

  ```python
  """Check protocol and registry for the ``ralph-doctor`` skill.

  Every check module under ``scripts.checks`` exports a single
  function:

      def check(context: CheckContext) -> CheckResult: ...

  The runner calls every module listed in :data:`REGISTRY` in order and
  collects the :class:`CheckResult` for each.
  """
  from __future__ import annotations

  from dataclasses import dataclass, field
  from pathlib import Path
  from typing import Literal

  Severity = Literal["error", "warn"]
  Status = Literal["pass", "fail", "skipped"]


  @dataclass(frozen=True)
  class CheckContext:
      settings_path: Path
      skills_dir: Path
      strict: bool = False
      extra: dict[str, str] = field(default_factory=dict)


  @dataclass(frozen=True)
  class CheckResult:
      name: str
      severity: Severity
      status: Status
      message: str
      details: dict[str, object] = field(default_factory=dict)


  REGISTRY: tuple[str, ...] = (
      "permissions",
      "hooks",
      "skills",
      "mcp",
      "auth",
      "ado",
  )
  """Module names under ``scripts.checks`` to execute, in order.

  Ordering: settings-only checks (permissions/hooks/skills/mcp) first —
  cheap and offline. Network probes (auth/ado) last — slow and may fail
  for reasons unrelated to ralph-safety.
  """
  ```

- [ ] 2. Verify mypy is clean on the new file:
  ```
  uv run mypy --config-file pyproject.toml skills/ralph-doctor
  ```
  Expected: no errors.

- [ ] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/__init__.py
  git commit -m "feat(ralph-doctor): define CheckResult / CheckContext / REGISTRY contract"
  ```

---

## Task 3 — Write the failing test suite

**Files**
- Create: `tests/skills/test_ralph_doctor.py`

**Steps**

- [ ] 1. Write `tests/skills/test_ralph_doctor.py` covering the runner and every check. The file must contain seven test classes — `TestRunnerExitCodes`, `TestPermissionsCheck`, `TestHooksCheck`, `TestSkillsCheck`, `TestMcpCheck`, `TestAuthCheck`, `TestAdoCheck` — and the helpers / fixtures described below. The module-loading fixture intentionally fails first (the runner does not yet exist) — that is the red step.

  Top of file:
  ```python
  """Tests for the ``ralph-doctor`` skill.

  Loads ``skills/ralph-doctor/scripts/check.py`` via importlib because the
  parent directory name contains a hyphen. All network calls are mocked
  with ``responses``; the filesystem-only checks read synthetic
  settings.json files built in ``tmp_path``.
  """
  from __future__ import annotations

  import importlib.util
  import json
  from pathlib import Path
  from types import ModuleType

  import pytest
  import responses

  REPO_ROOT = Path(__file__).resolve().parents[2]
  RUNNER_PATH = REPO_ROOT / "skills" / "ralph-doctor" / "scripts" / "check.py"
  CHECKS_DIR = REPO_ROOT / "skills" / "ralph-doctor" / "scripts" / "checks"

  ANTHROPIC_TOKEN_URL = "https://api.anthropic.com/v1/messages/count_tokens"
  ADO_ORG = "https://dev.azure.com/example-org"
  ADO_PROJECT = "example-project"
  ADO_REPO = "service-auth"
  NON_EXISTENT_PR_ID = 999999999
  ```

  Module-loading fixtures (module-scoped for `runner_module`; helper for individual checks):
  ```python
  @pytest.fixture(scope="module")
  def runner_module() -> ModuleType:
      assert RUNNER_PATH.is_file(), f"missing entry script at {RUNNER_PATH}"
      spec = importlib.util.spec_from_file_location(
          "ralph_doctor_runner", RUNNER_PATH
      )
      assert spec is not None and spec.loader is not None
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      return module


  def _load_check_module(name: str) -> ModuleType:
      path = CHECKS_DIR / f"{name}.py"
      assert path.is_file(), f"missing check module at {path}"
      spec = importlib.util.spec_from_file_location(
          f"ralph_doctor_check_{name}", path
      )
      assert spec is not None and spec.loader is not None
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      return module
  ```

  Synthetic settings.json factories (define these as module-level constants — the test classes import them directly):
  - `GOOD_SETTINGS` — `permissions.allow` lists `Bash(*)`, `Edit(*)`, `Write(*)`, `Read(*)`, `Grep(*)`, `Glob(*)`, `Skill(ado-pr)`, `Skill(ralph-doctor)`. One async PostToolUse hook running `python /opt/ralph/log_observation.py`. One SessionStart hook running a non-interactive script. One MCP server using `ADO_PAT` from env (no OAuth).
  - `BAD_PERMISSIONS_SETTINGS` — `permissions.allow` only contains `Bash(*)` and `Read(*)`. Empty hooks. Empty mcpServers.
  - `BAD_HOOK_SETTINGS` — `permissions` from `GOOD_SETTINGS`. One PreToolUse hook whose command contains the literal string `claude.AskUserQuestion('confirm?')`.
  - `ASYNC_HOOK_WITH_INPUT_SETTINGS` — `permissions` from `GOOD_SETTINGS`. One async PostToolUse hook whose command contains `input(`. This is the warn-severity case.
  - `BAD_MCP_OAUTH_SETTINGS` — `permissions` from `GOOD_SETTINGS`. One MCP server with `args: ["--auth", "oauth"]`.

  Helpers:
  ```python
  def _write_settings(path: Path, data: dict[str, object]) -> None:
      path.write_text(json.dumps(data), encoding="utf-8")


  def _build_skills_dir(
      tmp_path: Path, *, include_ask_user_question: bool = False
  ) -> Path:
      """Build a synthetic ~/.claude/skills/ tree.

      Always writes a clean ``ado-pr`` skill (SKILL.md + scripts/show.py).
      If ``include_ask_user_question`` is True, also writes an
      ``asks-questions`` skill whose ``scripts/main.py`` imports and
      calls ``AskUserQuestion``.
      """
      # ... mkdir + write SKILL.md and scripts/*.py per the description ...


  def _make_context(settings: Path, skills_dir: Path) -> object:
      """Duck-typed CheckContext that avoids the hyphenated-package import.

      Each check accepts any object with .settings_path, .skills_dir,
      .strict, .extra attributes.
      """
      class _Ctx:
          def __init__(self) -> None:
              self.settings_path = settings
              self.skills_dir = skills_dir
              self.strict = False
              self.extra: dict[str, str] = {}
      return _Ctx()


  @pytest.fixture
  def good_env(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
      monkeypatch.delenv("RALPH_USE_BEDROCK", raising=False)
      monkeypatch.setenv("ADO_PAT", "fake-pat")
      monkeypatch.setenv("ADO_ORG_URL", ADO_ORG)
      monkeypatch.setenv("ADO_PROJECT", ADO_PROJECT)
      monkeypatch.setenv("ADO_REPOSITORY", ADO_REPO)
  ```

  **`TestRunnerExitCodes`** — eight tests against `runner_module.main([...])`. Each writes a settings.json into `tmp_path`, calls `main(argv)`, captures stdout via `capsys`, asserts on JSON shape and the returned exit code:
  - `test_all_pass_returns_zero` — `GOOD_SETTINGS` + `good_env`; mocks Anthropic POST → 200 and ADO GET (pullrequests/999999999) → 404. Asserts exit 0, `payload["ok"] is True`, `summary.passes == 6`, all six check names present.
  - `test_missing_permission_returns_one` — `BAD_PERMISSIONS_SETTINGS`; runs with `--skip auth,ado`. Asserts exit 1, the `permissions` entry has `status == "fail"`.
  - `test_skip_runs_subset` — `GOOD_SETTINGS`; `--skip auth,ado`. Asserts exit 0; the two skipped entries have `status == "skipped"`.
  - `test_only_runs_named_subset` — `--only permissions`; everything else skipped.
  - `test_skip_and_only_are_mutually_exclusive` — passing both → exit 2 with `"mutually exclusive"` in stderr.
  - `test_missing_settings_path_returns_two` — `--settings <missing path>` → exit 2 with `"settings"` in stderr.
  - `test_malformed_settings_returns_two` — settings.json contains `{not json` → exit 2.
  - `test_strict_promotes_warn_to_error` — `ASYNC_HOOK_WITH_INPUT_SETTINGS` + `--strict --skip auth,ado` → exit 1.

  **`TestPermissionsCheck`** — five tests against `_load_check_module("permissions")`:
  - `test_all_required_tools_present_passes` — `GOOD_SETTINGS` → `pass`.
  - `test_missing_tool_fails` — `BAD_PERMISSIONS_SETTINGS` → `fail`; `result.details["missing"]` contains `Edit`, `Write`, `Grep`, `Glob`, `Skill`.
  - `test_global_wildcard_covers_everything` — `permissions.allow == ["*"]` → `pass`.
  - `test_missing_permissions_section_fails` — settings.json has no `permissions` key → `fail`.
  - `test_skill_subkey_recognised` — `Skill(ado-pr)` alone counts as coverage for the `ado-pr` skill.

  **`TestHooksCheck`** — four tests:
  - `test_clean_hooks_pass` — `GOOD_SETTINGS` → `pass`.
  - `test_ask_user_question_in_hook_fails` — `BAD_HOOK_SETTINGS` → `fail`; message contains `AskUserQuestion`.
  - `test_read_p_in_hook_fails` — settings with `bash -c 'read -p "go?" reply'` → `fail`.
  - `test_async_hook_with_input_is_warn` — `ASYNC_HOOK_WITH_INPUT_SETTINGS` → `status == "fail"` AND `severity == "warn"`.

  **`TestSkillsCheck`** — three tests:
  - `test_clean_skills_dir_passes` — `_build_skills_dir(tmp_path)` (no offending skill) → `pass`.
  - `test_ask_user_question_in_skill_fails` — `_build_skills_dir(tmp_path, include_ask_user_question=True)` → `fail`; message contains `asks-questions`.
  - `test_missing_skills_dir_is_pass_with_note` — skills_dir does not exist → `pass`; message contains `"no skills directory"` (case-insensitive).

  **`TestMcpCheck`** — three tests:
  - `test_non_oauth_mcp_passes` — `GOOD_SETTINGS` → `pass`.
  - `test_oauth_mcp_fails` — `BAD_MCP_OAUTH_SETTINGS` → `fail`.
  - `test_empty_mcp_is_pass` — `mcpServers: {}` → `pass`.

  **`TestAuthCheck`** — three tests. Each opens `responses.RequestsMock()` and uses `rsps.add(responses.POST, ANTHROPIC_TOKEN_URL, ..., status=...)`:
  - `test_anthropic_200_passes` — `good_env`; mock 200 → `pass`.
  - `test_anthropic_401_fails` — `good_env`; mock 401 → `fail`; message contains `"401"`.
  - `test_missing_api_key_fails` — `monkeypatch.delenv("ANTHROPIC_API_KEY")` and `RALPH_USE_BEDROCK` unset → `fail`; message contains `ANTHROPIC_API_KEY`.

  **`TestAdoCheck`** — four tests. Each mocks `f"{ADO_ORG}/{ADO_PROJECT}/_apis/git/repositories/{ADO_REPO}/pullrequests/{NON_EXISTENT_PR_ID}"`:
  - `test_404_passes` — `good_env`; mock 404 → `pass`.
  - `test_401_fails` — mock 401 → `fail`; message contains `"401"`.
  - `test_200_means_routing_is_wrong_and_fails` — mock 200 → `fail`; message contains `"200"`.
  - `test_missing_env_var_fails` — `delenv("ADO_PAT")` → `fail`; message contains `ADO_PAT`.

- [ ] 2. Run the new test file. Every test must fail because `check.py` and the check modules do not yet exist:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py -v
  ```
  Expected: the module-loading fixtures raise `AssertionError: missing entry script at ...` and `AssertionError: missing check module at ...`. This is the red step.

- [ ] 3. Do NOT commit yet. The failing tests are consumed by Tasks 4–10. Holding the commit until Task 4 keeps the repo in a coherent state (red, with the runner missing) rather than red-and-impossible-to-import.

---

## Task 4 — Implement the runner

**Files**
- Create: `skills/ralph-doctor/scripts/check.py`

**Steps**

- [ ] 1. Create `skills/ralph-doctor/scripts/check.py`. The runner has three responsibilities: load the check contract (via `importlib.util.spec_from_file_location` because the directory name contains a hyphen), parse CLI args, then iterate `REGISTRY` invoking each check.

  Imports + constants + logging setup:
  ```python
  """``ralph-doctor`` skill entry point."""
  from __future__ import annotations

  import argparse
  import importlib.util
  import json
  import logging
  import os
  import sys
  from dataclasses import asdict
  from pathlib import Path
  from types import ModuleType
  from typing import Any

  _HERE = Path(__file__).resolve().parent
  _CHECKS_DIR = _HERE / "checks"

  _checks_init = importlib.util.spec_from_file_location(
      "ralph_doctor_checks", _CHECKS_DIR / "__init__.py"
  )
  if _checks_init is None or _checks_init.loader is None:
      raise RuntimeError(
          f"could not load checks contract from {_CHECKS_DIR / '__init__.py'}"
      )
  _checks_pkg = importlib.util.module_from_spec(_checks_init)
  _checks_init.loader.exec_module(_checks_pkg)

  CheckContext = _checks_pkg.CheckContext  # type: ignore[attr-defined]
  CheckResult = _checks_pkg.CheckResult  # type: ignore[attr-defined]
  REGISTRY: tuple[str, ...] = _checks_pkg.REGISTRY  # type: ignore[attr-defined]

  _LOG = logging.getLogger("ralph_doctor")


  def _configure_logging() -> None:
      level_name = os.environ.get("RALPH_LOG_LEVEL", "INFO").upper()
      level = getattr(logging, level_name, logging.INFO)
      handler = logging.StreamHandler(sys.stderr)
      handler.setFormatter(logging.Formatter("%(message)s"))
      _LOG.handlers.clear()
      _LOG.addHandler(handler)
      _LOG.setLevel(level)


  class _CliError(RuntimeError):
      """Raised internally on invalid CLI usage; runner converts to exit 2."""
  ```

  CLI parsing — `_parse_args(argv)` builds an `argparse.ArgumentParser(prog="ralph-doctor", description="Preflight checks for a ralph-safe environment.")` with these arguments (default values shown):
  - `--settings` → `str(Path.home() / ".claude" / "settings.json")`
  - `--skills-dir` → `str(Path.home() / ".claude" / "skills")`
  - `--skip` → `""`
  - `--only` → `""`
  - `--json` → `action="store_true"`
  - `--strict` → `action="store_true"`

  Filter resolution:
  ```python
  def _determine_active_checks(
      args: argparse.Namespace,
  ) -> tuple[set[str], set[str]]:
      skip_set = {s.strip() for s in args.skip.split(",") if s.strip()}
      only_set = {s.strip() for s in args.only.split(",") if s.strip()}
      if skip_set and only_set:
          raise _CliError("--skip and --only are mutually exclusive")
      unknown = (skip_set | only_set) - set(REGISTRY)
      if unknown:
          raise _CliError(
              f"unknown check name(s): {sorted(unknown)} "
              f"(valid: {list(REGISTRY)})"
          )
      if only_set:
          return only_set, set(REGISTRY) - only_set
      return set(REGISTRY) - skip_set, skip_set
  ```

  Check loading + context construction:
  ```python
  def _load_check_module(name: str) -> ModuleType:
      path = _CHECKS_DIR / f"{name}.py"
      if not path.is_file():
          raise FileNotFoundError(f"check module {name} not found at {path}")
      spec = importlib.util.spec_from_file_location(
          f"ralph_doctor_check_{name}", path
      )
      if spec is None or spec.loader is None:
          raise RuntimeError(f"could not create spec for {path}")
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      return module


  def _make_context(args: argparse.Namespace) -> CheckContext:
      return CheckContext(
          settings_path=Path(args.settings).expanduser().resolve(),
          skills_dir=Path(args.skills_dir).expanduser().resolve(),
          strict=bool(args.strict),
      )


  def _skipped_result(name: str, reason: str) -> CheckResult:
      return CheckResult(
          name=name, severity="error", status="skipped",
          message=reason, details={},
      )
  ```

  Aggregation + output:
  ```python
  def _result_to_dict(result: CheckResult) -> dict[str, Any]:
      payload = asdict(result)
      details = payload.get("details") or {}
      # Coerce non-JSON-safe values to strings so json.dumps never raises.
      payload["details"] = {
          str(k): (v if _json_safe(v) else str(v)) for k, v in details.items()
      }
      return payload


  def _json_safe(value: object) -> bool:
      try:
          json.dumps(value)
      except TypeError:
          return False
      return True


  def _summarise(results: list[CheckResult]) -> dict[str, int]:
      return {
          "errors": sum(1 for r in results if r.status == "fail" and r.severity == "error"),
          "warns": sum(1 for r in results if r.status == "fail" and r.severity == "warn"),
          "passes": sum(1 for r in results if r.status == "pass"),
          "skips": sum(1 for r in results if r.status == "skipped"),
      }


  def _compute_exit_code(results: list[CheckResult], strict: bool) -> int:
      for r in results:
          if r.status != "fail":
              continue
          if r.severity == "error":
              return 1
          if strict and r.severity == "warn":
              return 1
      return 0


  def _emit(payload: dict[str, Any], json_only: bool) -> None:
      sys.stdout.write(json.dumps(payload, indent=2, sort_keys=False))
      sys.stdout.write("\n")
      if json_only:
          return
      header = (
          f"ralph-doctor: {payload['summary']['passes']} pass, "
          f"{payload['summary']['errors']} error, "
          f"{payload['summary']['warns']} warn, "
          f"{payload['summary']['skips']} skipped"
      )
      lines = [header]
      for entry in payload["checks"]:
          marker = {"pass": "  ok ", "fail": "FAIL ", "skipped": "skip "}.get(
              entry["status"], "  ?  "
          )
          lines.append(
              f"  {marker} {entry['name']:<12} "
              f"[{entry['severity']:<5}] {entry['message']}"
          )
      sys.stderr.write("\n".join(lines))
      sys.stderr.write("\n")
  ```

  Early-fail settings validation (so the runner returns exit 2 instead of failing every check):
  ```python
  def _load_settings(path: Path) -> dict[str, Any]:
      if not path.is_file():
          raise _CliError(f"settings file not found: {path}")
      try:
          text = path.read_text(encoding="utf-8")
      except OSError as exc:
          raise _CliError(f"could not read settings file {path}: {exc}") from exc
      try:
          data = json.loads(text)
      except json.JSONDecodeError as exc:
          raise _CliError(
              f"settings file {path} is not valid JSON: {exc}"
          ) from exc
      if not isinstance(data, dict):
          raise _CliError(
              f"settings file {path} must be a JSON object at top level"
          )
      return data
  ```

  Orchestrator:
  ```python
  def main(argv: list[str] | None = None) -> int:
      _configure_logging()
      try:
          args = _parse_args(argv if argv is not None else sys.argv[1:])
          to_run, to_skip = _determine_active_checks(args)
          if any(name in to_run for name in ("permissions", "hooks", "mcp")):
              _load_settings(Path(args.settings).expanduser())
      except _CliError as exc:
          sys.stderr.write(f"ralph-doctor: {exc}\n")
          return 2

      context = _make_context(args)
      results: list[CheckResult] = []
      for name in REGISTRY:
          if name in to_skip:
              results.append(_skipped_result(name, "skipped via --skip / --only"))
              continue
          try:
              module = _load_check_module(name)
              result = module.check(context)
          except Exception as exc:  # pylint: disable=broad-except
              _LOG.exception("check %s raised", name)
              result = CheckResult(
                  name=name, severity="error", status="fail",
                  message=f"check raised an exception: {exc!r}",
                  details={"exception": str(exc)},
              )
          if not isinstance(result, CheckResult):
              result = CheckResult(
                  name=name, severity="error", status="fail",
                  message=(
                      f"check module {name} returned non-CheckResult "
                      f"value (type={type(result).__name__})"
                  ),
              )
          results.append(result)

      exit_code = _compute_exit_code(results, strict=context.strict)
      summary = _summarise(results)
      payload = {
          "ok": exit_code == 0,
          "exit_code": exit_code,
          "summary": summary,
          "checks": [_result_to_dict(r) for r in results],
      }
      _emit(payload, json_only=bool(args.json))
      return exit_code


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] 2. Run the runner-level tests; per-check tests are still red because their modules are missing:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestRunnerExitCodes -v
  ```
  Expected: every `TestRunnerExitCodes` test fails with a `FileNotFoundError` raised inside `_load_check_module` — that is the documented behaviour when a referenced check is missing. The runner itself is correct.

- [ ] 3. Stage and commit:
  ```
  git add tests/skills/test_ralph_doctor.py skills/ralph-doctor/scripts/check.py
  git commit -m "feat(ralph-doctor): add runner + failing test suite"
  ```
  Expected: `uv run mypy --config-file pyproject.toml skills/ralph-doctor` is clean.

---

## Task 5 — Implement the `permissions` check

**Files**
- Create: `skills/ralph-doctor/scripts/checks/permissions.py`

**Steps**

- [ ] 1. Create `skills/ralph-doctor/scripts/checks/permissions.py`. Module structure:
  - Module docstring summarising the rule: coverage of `Bash`, `Edit`, `Write`, `Read`, `Grep`, `Glob`, `Skill`, plus skill `ado-pr`. An entry covers a tool if it is exactly the tool name, `Tool(...)` (anything between parens), or the global `*`.
  - Imports: `json`, `Path`, plus `CheckContext` / `CheckResult` from `. import` (relative).
  - Module constants:
    ```python
    REQUIRED_TOOLS: tuple[str, ...] = (
        "Bash", "Edit", "Write", "Read", "Grep", "Glob", "Skill",
    )
    REQUIRED_SKILLS: tuple[str, ...] = ("ado-pr",)
    ```
  - `_load_allow_list(path: Path) -> list[str] | None` — reads + json.loads; returns the `permissions.allow` list, or `None` if the file is unreadable / missing / malformed / lacks the section.
  - `_tool_is_covered(tool: str, allow: list[str]) -> bool` — true if any entry is `*`, exactly `tool`, or starts with `f"{tool}("` and ends with `)`.
  - `_skill_is_covered(skill_name: str, allow: list[str]) -> bool` — true if any entry is `*`, `Skill`, `Skill(*)`, or `Skill({skill_name})`.
  - `check(context: CheckContext) -> CheckResult`:
    - If `_load_allow_list` returns None → `fail` with message `"settings.json is missing permissions.allow (or permissions is not a JSON object)."` and `details={"settings_path": str(context.settings_path)}`.
    - Compute `missing = [t for t in REQUIRED_TOOLS if not _tool_is_covered(t, allow)]` and `missing_skills = [s for s in REQUIRED_SKILLS if not _skill_is_covered(s, allow)]`.
    - If either non-empty → `fail` with details `{"missing": missing, "missing_skills": missing_skills, "allow": allow}` and a message naming both lists.
    - Otherwise → `pass` with message `f"permissions.allow covers all {len(REQUIRED_TOOLS)} required tools and {len(REQUIRED_SKILLS)} required skills."` and details `{"required_tools": list(REQUIRED_TOOLS), "required_skills": list(REQUIRED_SKILLS), "allow_entries": len(allow)}`.

- [ ] 2. Run the permissions tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestPermissionsCheck -v
  ```
  Expected: every test passes. If any fail, fix the check — do NOT change the tests.

- [ ] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/permissions.py
  git commit -m "feat(ralph-doctor): implement permissions check"
  ```

---

## Task 6 — Implement the `hooks` check

**Files**
- Create: `skills/ralph-doctor/scripts/checks/hooks.py`

**Steps**

- [ ] 1. Create `skills/ralph-doctor/scripts/checks/hooks.py`. Structure:
  - Module docstring: scans every `command` string in `settings.json["hooks"]` for interactive indicators. Matches on `async: true` hooks downgrade from error to warn (async hooks cannot block claude but are still a smell).
  - Module constant:
    ```python
    INTERACTIVE_INDICATORS: tuple[str, ...] = (
        "AskUserQuestion", "input(", "read -p", "Read-Host",
    )
    ```
  - `_iter_hook_commands(hooks_section: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]` — flattens the nested structure (`event -> [{matcher, hooks: [{command, async, ...}]}]`) into `(event_name, hook_dict)` pairs. Skip any non-list / non-dict containers defensively — settings.json can be hand-edited.
  - `_matches_indicator(command: str) -> tuple[bool, str]` — returns `(True, needle)` on first hit, else `(False, "")`.
  - `check(context: CheckContext) -> CheckResult`:
    - Open settings.json; if read/parse fails, return `fail` with the exception message.
    - Pull `data.get("hooks", {})`; if it is not a dict, return `fail` with `"settings.json[\"hooks\"] is not a JSON object."`.
    - Walk flattened entries. For each entry whose `command` matches an indicator, append to `blocking_offences` if `async` is not `True`, else `async_offences`. Each record: `{"event": event, "needle": needle, "command": command}`.
    - If `blocking_offences` non-empty → return `severity="error"`, `status="fail"`, message listing the number of offending hooks and the matched needles, `details={"blocking": blocking_offences, "async": async_offences}`.
    - Else if `async_offences` non-empty → return `severity="warn"`, `status="fail"`, message listing async offences, `details={"async": async_offences}`.
    - Otherwise → `severity="error"`, `status="pass"`, message `"No hook calls AskUserQuestion or blocks on stdin."`, `details={"hooks_checked": len(flattened)}`.

- [ ] 2. Run the hooks tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestHooksCheck -v
  ```
  Expected: every test passes.

- [ ] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/hooks.py
  git commit -m "feat(ralph-doctor): implement hooks check (AskUserQuestion / stdin scan)"
  ```

---

## Task 7 — Implement the `skills` check

**Files**
- Create: `skills/ralph-doctor/scripts/checks/skills.py`

**Steps**

- [ ] 1. Create `skills/ralph-doctor/scripts/checks/skills.py`. Structure:
  - Module docstring: heuristic substring scan over `SKILL.md` body and `scripts/*.py`. Markdown regions wrapped with `<!-- ralph-doctor: ignore -->` ... `<!-- /ralph-doctor: ignore -->` are dropped before the search. Python lines containing `# noqa: ralph-doctor` are dropped.
  - Module constants:
    ```python
    NEEDLE = "AskUserQuestion"
    IGNORE_MARK_OPEN = "<!-- ralph-doctor: ignore -->"
    IGNORE_MARK_CLOSE = "<!-- /ralph-doctor: ignore -->"
    PY_IGNORE = "# noqa: ralph-doctor"
    ```
  - `_strip_ignored_markdown_regions(text: str) -> str` — linear scan removing any region between the open/close markers. If a region is unclosed, drop the rest defensively.
  - `_python_lines_without_noqa(text: str) -> str` — filter `splitlines()` excluding any line containing `PY_IGNORE`.
  - `_scan_skill(skill_dir: Path) -> list[str]` — returns offending file paths (string form). Reads `SKILL.md` (if present) through `_strip_ignored_markdown_regions`; reads every `scripts/**/*.py` (rglob) through `_python_lines_without_noqa`. Use `read_text(encoding="utf-8", errors="replace")` to survive binary or non-UTF8 files without raising.
  - `check(context: CheckContext) -> CheckResult`:
    - If `context.skills_dir` is not a directory → `pass` with message `f"no skills directory at {skills_dir}; nothing to scan."` and details `{"skills_dir": str(skills_dir)}`. (This is the laptop case where the test config doesn't match the local layout — it's not an error.)
    - Iterate top-level entries that are directories. For each, accumulate `_scan_skill` offences and increment a `skills_seen` counter.
    - If offences non-empty: dedupe + sort. Derive a short list of skill names from the offending paths (the immediate parent directory if `parent.name == "scripts"`, otherwise the parent itself). Return `severity="error"`, `status="fail"`, message naming the skills, `details={"offending_files": offences}`.
    - Otherwise: `pass` with message `f"No installed skill calls AskUserQuestion in its main path (scanned {skills_seen} skill(s))."` and `details={"skills_scanned": skills_seen}`.

- [ ] 2. Run the skills tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestSkillsCheck -v
  ```
  Expected: every test passes.

- [ ] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/skills.py
  git commit -m "feat(ralph-doctor): implement skills check (AskUserQuestion scan)"
  ```

---

## Task 8 — Implement the `mcp` check

**Files**
- Create: `skills/ralph-doctor/scripts/checks/mcp.py`

**Steps**

- [ ] 1. Create `skills/ralph-doctor/scripts/checks/mcp.py`. Structure:
  - Module docstring: walks `mcpServers` from settings.json AND any sibling `.mcp.json`. Flags any server whose `command`, `args`, or `env` contains an OAuth indicator.
  - Module constant:
    ```python
    OAUTH_INDICATORS: tuple[str, ...] = (
        "oauth", "OAuth", "OAUTH", "--auth", "--login", "BROWSER",
    )
    ```
  - `_read_json(path: Path) -> dict[str, Any] | None` — returns the parsed dict, or `None` if missing / unreadable / malformed / not a top-level object.
  - `_extract_mcp_servers(settings, mcp_file) -> dict[str, dict[str, Any]]` — merge `mcpServers` (or legacy `mcp_servers`) from both sources. Settings.json takes precedence on key collision; values that are not dicts are dropped.
  - `_server_uses_oauth(server: dict[str, Any]) -> tuple[bool, str]` — concatenate `command`, every entry of `args`, and `f"{k}={v}"` for every entry of `env`. Return `(True, needle)` on the first indicator hit, else `(False, "")`.
  - `check(context: CheckContext) -> CheckResult`:
    - Read settings.json + `context.settings_path.parent / ".mcp.json"`.
    - Build the merged server map.
    - If empty → `pass` with message `f"no MCP servers configured (checked {settings_path} and {mcp_path})."` and the two paths in details.
    - Otherwise: walk every server. If any return `True` from `_server_uses_oauth`, collect `{name: needle}` into `offenders`.
    - If offenders non-empty → `fail` with message listing each offender + the matched needle, `details={"offenders": offenders, "total_servers": len(servers)}`.
    - Otherwise → `pass` with message `f"All {len(servers)} MCP server(s) use non-interactive auth."` and `details={"total_servers": len(servers)}`.

- [ ] 2. Run the MCP tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestMcpCheck -v
  ```
  Expected: every test passes.

- [ ] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/mcp.py
  git commit -m "feat(ralph-doctor): implement mcp check (no OAuth on cold start)"
  ```

---

## Task 9 — Implement the `auth` check (Anthropic / Bedrock cold start)

**Files**
- Create: `skills/ralph-doctor/scripts/checks/auth.py`

**Steps**

- [ ] 1. Create `skills/ralph-doctor/scripts/checks/auth.py`. Structure:
  - Module docstring: POSTs a tiny single-message body to `/v1/messages/count_tokens` — the endpoint requires the API key but does not consume quota. 2xx → pass; non-2xx → fail. Under `RALPH_USE_BEDROCK=1`, probes Bedrock via boto3.
  - Module constants:
    ```python
    ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"
    DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"
    TOKEN_COUNT_PATH = "/v1/messages/count_tokens"
    PROBE_MODEL = "claude-haiku-4-5"
    ANTHROPIC_VERSION = "2023-06-01"
    REQUEST_TIMEOUT_SECONDS = 10.0
    BEDROCK_TIMEOUT_SECONDS = 5.0
    ```
  - `_anthropic_probe() -> CheckResult`:
    - Read `ANTHROPIC_API_KEY` from env (strip whitespace). If empty → `fail` with message `"ANTHROPIC_API_KEY is not set (and RALPH_USE_BEDROCK is not enabled)."`.
    - Build URL from `ANTHROPIC_BASE_URL` (or default) + `TOKEN_COUNT_PATH`.
    - POST `{"model": PROBE_MODEL, "messages": [{"role": "user", "content": "ping"}]}` with headers `x-api-key`, `anthropic-version: 2023-06-01`, `content-type: application/json` and a 10-second timeout.
    - On `requests.RequestException` → `fail` with `f"Anthropic auth call raised: {exc!r}"`.
    - 200–299 → `pass` with message `f"Anthropic auth resolved on cold start (model={PROBE_MODEL})."` and `details={"status_code": ...}`.
    - Otherwise → `fail` with message containing the status code, a body preview (first 200 chars), and the status_code in details.
  - `_bedrock_probe() -> CheckResult`:
    - `try: import boto3` — on `ImportError`, return `fail` with `"RALPH_USE_BEDROCK=1 but boto3 is not installed: {exc}. Install boto3 in the pod image."`.
    - Lazily import `botocore.config.Config` for a 5-second connect/read timeout with `retries={"max_attempts": 1}`.
    - `client = boto3.client("bedrock", config=cfg); response = client.list_foundation_models()`. Note: this uses the `bedrock` control-plane client (not `bedrock-runtime`) because `list_foundation_models` is a control-plane operation — it requires the IAM role to have `bedrock:ListFoundationModels`, which is the same auth surface the executor will use.
    - Any exception → `fail` with `f"Bedrock auth failed: {exc!r}"`.
    - Validate `isinstance(response, dict) and "modelSummaries" in response` — otherwise `fail` with `"Bedrock auth call succeeded but response is malformed."`.
    - Otherwise → `pass` with message `f"Bedrock auth resolved on cold start ({len(response['modelSummaries'])} foundation models)."`.
  - `check(context: CheckContext) -> CheckResult`:
    ```python
    if os.environ.get("RALPH_USE_BEDROCK", "").strip() == "1":
        return _bedrock_probe()
    return _anthropic_probe()
    ```

- [ ] 2. Run the auth tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestAuthCheck -v
  ```
  Expected: every test passes. `responses` intercepts the POST to `https://api.anthropic.com/v1/messages/count_tokens`.

- [ ] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/auth.py
  git commit -m "feat(ralph-doctor): implement auth check (Anthropic + Bedrock cold start)"
  ```

---

## Task 10 — Implement the `ado` check

**Files**
- Create: `skills/ralph-doctor/scripts/checks/ado.py`

**Steps**

- [ ] 1. Create `skills/ralph-doctor/scripts/checks/ado.py`. The check goes one level below `AdoClient` (Plan 2) and uses `requests` directly — `AdoClient.get` raises on 404, but the doctor needs 404-as-success.

  Structure:
  - Module docstring: probes the ADO PR show endpoint against a non-existent PR ID. 404 → pass (PAT auth + project routing both work). Any other status / network failure → fail. Requires `ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT`, `ADO_REPOSITORY`.
  - Module constants:
    ```python
    NON_EXISTENT_PR_ID = 999999999
    REQUEST_TIMEOUT_SECONDS = 10.0
    REQUIRED_ENV: tuple[str, ...] = (
        "ADO_PAT", "ADO_ORG_URL", "ADO_PROJECT", "ADO_REPOSITORY",
    )
    ```
  - `_missing_env_vars() -> list[str]` — returns the names of any required env vars that are unset or whitespace-only.
  - `check(context: CheckContext) -> CheckResult`:
    - If `_missing_env_vars()` returns a non-empty list → `fail` with message `f"ADO check cannot run; missing required env vars: {', '.join(missing)}"` and `details={"missing": missing}`.
    - Build URL: `f"{org.rstrip('/')}/{project}/_apis/git/repositories/{repo}/pullrequests/{NON_EXISTENT_PR_ID}"`.
    - `requests.get(url, auth=("", pat), timeout=REQUEST_TIMEOUT_SECONDS, params={"api-version": "7.1"})` inside a try/except for `requests.RequestException` → `fail` with `f"ADO probe raised: {exc!r}"`.
    - If `response.status_code == 404` → `pass` with message `f"ADO PAT works (404 on non-existent PR {NON_EXISTENT_PR_ID}, as expected)."` and `details={"status_code": 404, "org": org, "project": project, "repo": repo}`.
    - Otherwise → `fail` with message `f"ADO probe returned {status} (expected 404); body preview: {preview!r}"` (preview is the first 200 chars of `response.text`, or `"<empty>"`), and `details={"status_code": status, "url": url}`.

- [ ] 2. Run the ADO tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestAdoCheck -v
  ```
  Expected: every test passes.

- [ ] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/ado.py
  git commit -m "feat(ralph-doctor): implement ado check (PAT scope via 404 probe)"
  ```

---

## Task 11 — Full-suite verification, lint, and live smoke

**Files**
- None (verification only)

**Steps**

- [ ] 1. Run the full skill test file:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py -v
  ```
  Expected: every test (TestRunnerExitCodes + TestPermissionsCheck + TestHooksCheck + TestSkillsCheck + TestMcpCheck + TestAuthCheck + TestAdoCheck) passes. If any fail, STOP and fix the check or runner — do NOT change the tests.

- [ ] 2. Run the full project test suite to confirm no regression:
  ```
  uv run pytest -v
  ```
  Expected: every test in the repo passes.

- [ ] 3. Run the lint / type / format gates:
  ```
  uv run ruff check skills/ralph-doctor tests/skills/test_ralph_doctor.py
  uv run ruff format --check skills/ralph-doctor tests/skills/test_ralph_doctor.py
  uv run mypy --config-file pyproject.toml skills/ralph-doctor
  ```
  Expected: all three commands pass with zero findings. Fix any inline; this is the conventions self-review step.

- [ ] 4. Smoke the runner end-to-end against this developer's real `~/.claude/settings.json` (offline checks only):
  ```
  uv run python skills/ralph-doctor/scripts/check.py --skip auth,ado
  ```
  Expected: JSON document on stdout with `summary.passes >= 4`; human summary on stderr listing each check; exit code 0 if the local config is ralph-safe. If it returns non-zero, that's a real finding — record it (the doctor is doing its job) but do NOT change the doctor to ignore it.

- [ ] 5. Stage and commit any format / lint fixes from step 3 (skip if none):
  ```
  git add -A
  git commit -m "chore(ralph-doctor): apply ruff format + lint fixes"
  ```

---

## Verification gate (Plan 11 — final)

This gate corresponds to the orchestrator's verification table for Plan 11.

- [ ] 1. From the ralph repo root, run:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py -v
  ```
  Expected: every test passes.

- [ ] 2. Run the wider gate from the orchestrator:
  ```
  uv run ruff check . && uv run ruff format --check . && uv run mypy --config-file pyproject.toml ralph_executor scripts skills && uv run pytest
  ```
  Expected: every gate passes.

- [ ] 3. Confirm the skill directory layout matches the file structure in this plan:
  ```
  ls -1 skills/ralph-doctor/
  ls -1 skills/ralph-doctor/scripts/
  ls -1 skills/ralph-doctor/scripts/checks/
  ```
  Expected (order may vary):
  - `skills/ralph-doctor/`: `SKILL.md`, `scripts`
  - `skills/ralph-doctor/scripts/`: `__init__.py`, `check.py`, `checks`
  - `skills/ralph-doctor/scripts/checks/`: `__init__.py`, `permissions.py`, `hooks.py`, `skills.py`, `mcp.py`, `auth.py`, `ado.py`

- [ ] 4. Sanity-check exit code 1 on a deliberately broken settings.json:
  ```
  echo '{"permissions":{"allow":["Bash"]}}' > /tmp/bad_settings.json
  ANTHROPIC_API_KEY=fake \
  ADO_PAT=fake ADO_ORG_URL=https://x ADO_PROJECT=p ADO_REPOSITORY=r \
  uv run python skills/ralph-doctor/scripts/check.py \
      --settings /tmp/bad_settings.json \
      --skip auth,ado,skills,mcp \
      --json
  echo "exit_code=$?"
  rm /tmp/bad_settings.json
  ```
  Expected: JSON shows `"ok": false` and `"exit_code": 1`; the final shell line shows `exit_code=1`.

- [ ] 5. Open a PR:
  ```
  git push -u origin HEAD
  gh pr create --title "feat(ralph-doctor): preflight skill (Plan 11)" --body "$(cat <<'EOF'
  ## Summary
  - Adds the ralph-doctor preflight skill (Plan 11 of 13)
  - Six checks: permissions / hooks / skills / mcp / auth / ado
  - Refuses to let the pod start if any error-severity check fails

  ## Plan
  See `docs/superpowers/plans/2026-05-24-11-ralph-doctor.md`.

  ## Test plan
  - [ ] `uv run pytest tests/skills/test_ralph_doctor.py -v`
  - [ ] `uv run ruff check . && uv run mypy --config-file pyproject.toml ralph_executor scripts skills`
  - [ ] Smoke `uv run python skills/ralph-doctor/scripts/check.py --skip auth,ado` against the developer's local settings.json

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```
  Expected: PR opens; the auto code review job runs.

---

## Self-review notes

This section is the writing-plans self-review loop. Re-read the plan with these questions before signing off.

### Placeholders

- No `TODO`, `TBD`, `FIXME`, `???` tokens.
- Angle-bracket templates appear only inside fenced code blocks (e.g. `<PBI-ID>` inside the SKILL.md PR body template). No step instruction contains an unresolved placeholder.

### References

- `scripts.ado_client.AdoClient` is referenced in the Tech Stack and Task 10 description but NOT imported by `checks/ado.py` (the doctor goes below the client to distinguish 404 from other errors). Tech Stack note re-clarifies this.
- `pyproject.toml` `[tool.mypy].files` containing `skills` and `[tool.pytest.ini_options].testpaths` containing `tests` — Plan 3 established both; Task 1 step 2 re-asserts.
- `tests/skills/__init__.py` exists from Plan 3.
- `responses` library was added in Plan 2 (referenced in Plan 3 too). Task 3 uses it without re-adding.

### Step actionability

- Every numbered step either creates / modifies a file with the content specified, runs a named command with an expected outcome, or makes an assertion. No "consider doing X" or "you might want to" steps.

### Conventional commits

Every commit message in this plan uses the conventional-commits format:
- `chore(skills): scaffold ralph-doctor skill (...)`
- `feat(ralph-doctor): define CheckResult / CheckContext / REGISTRY contract`
- `feat(ralph-doctor): add runner + failing test suite`
- `feat(ralph-doctor): implement permissions check`
- `feat(ralph-doctor): implement hooks check (...)`
- `feat(ralph-doctor): implement skills check (...)`
- `feat(ralph-doctor): implement mcp check (...)`
- `feat(ralph-doctor): implement auth check (...)`
- `feat(ralph-doctor): implement ado check (...)`
- `chore(ralph-doctor): apply ruff format + lint fixes`

Scope is `skills` for scaffolding and `ralph-doctor` for incremental check work — consistent with Plan 3.

### TDD discipline

- Task 3 writes the entire failing test suite first (red).
- Task 4 implements the runner; runner-level tests stop failing on missing-runner errors but remain red because per-check modules are missing — that's the expected intermediate state.
- Tasks 5–10 each implement one check and turn its test class green.
- Task 11 confirms the whole file is green plus the wider gate.

### Known concerns (intentional trade-offs, not gaps)

1. **`skills` check is heuristic.** A skill that hides `AskUserQuestion` behind dynamic dispatch (e.g. `getattr(self, 'Ask' + 'UserQuestion')()`) evades the substring scan. Documented in `SKILL.md` under "Trade-offs". v2 may upgrade to AST-based scanning; v1 deliberately optimises for false-positive resistance by honouring `<!-- ralph-doctor: ignore -->` markers in markdown and `# noqa: ralph-doctor` lines in Python.
2. **`auth` probe uses `count_tokens`, not `messages`.** `count_tokens` requires a valid API key but does not consume token quota — the proof-of-auth property holds without burning credit. The trade-off is that some auth misconfigurations specific to the Messages endpoint (e.g. a key that has count-tokens scope but not messages scope) would slip through. In practice, Anthropic does not gate count_tokens separately from messages, so this is a theoretical concern only.
3. **`ado` check bypasses `AdoClient.get` and uses `requests` directly.** `AdoClient.get` raises on 404 (per Plan 2), but the doctor needs 404-as-success. The check therefore re-implements the auth header construction (basic-auth with empty username and the PAT as password) inline. Documented in Task 10. The duplication is small (one `requests.get` call) and the alternative — adding a `raise_for_404=False` parameter to `AdoClient.get` — would push the special case into the wrong layer.
4. **`bedrock` probe uses the control-plane client, not bedrock-runtime.** Both clients use the same IAM credentials; `list_foundation_models` is the cheapest read operation that proves the role can authenticate. Documented in Task 9.

### Length

This plan is in the 1000–1500 line target band.
