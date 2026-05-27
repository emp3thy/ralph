# `ralph-doctor` Preflight Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `ralph-doctor`, the preflight gate that verifies the host environment (laptop OR pod) is ralph-safe BEFORE `ralph-executor` starts. The spec is unambiguous: every silent pod failure has the same root cause — `claude -p` hit a permission prompt because some hook or skill expected a user. The doctor catches that class of failure once, on cold start, and refuses to let the pod run if anything ralph-unsafe is configured. Seven checks land in v1, in this order: (1) `permissions.allow` covers Bash, Edit, Write, Read, Grep, Glob, Skill, plus the `pr` and `workitem-fetch` skills by exact name; (2) no active hook calls `AskUserQuestion` or blocks on stdin; (3) no installed skill calls `AskUserQuestion` in its main path; (4) MCP servers all use non-interactive auth (no OAuth refresh prompts); (5) Anthropic (or Bedrock under `RALPH_USE_BEDROCK=1`) auth resolves on cold start via a free, no-op probe; (6) the staged `pr/` and `workitem-fetch/` skill directories on disk match the configured `RALPH_GIT_HOST`; (7) the host-specific auth check — GitHub (Phase 1) or Azure DevOps (Phase 2) — selected dynamically by `RALPH_GIT_HOST`. Each check returns a `CheckResult`; the runner aggregates them, prints JSON to stdout, prints a human summary to stderr, exits 0 only if every `error`-severity check passes (`warn` logs but does not block, unless `--strict` is set).

**Architecture:** A Claude Code skill at `skills/ralph-doctor/` with one `SKILL.md` plus `scripts/check.py` (the runner) and a `scripts/checks/` package containing one module per check. The runner discovers checks at import time via `importlib.util.spec_from_file_location` (the parent directory name `ralph-doctor` contains a hyphen and is not a valid Python identifier — every load goes through `spec_from_file_location`). The settings path, skills directory, and per-check skip/only filters are CLI-configurable so tests can pass synthetic configs. Tests use `tmp_path` for synthetic settings.json and skill trees plus the `responses` library to mock the Anthropic, GitHub, and ADO REST endpoints.

**Host selection:** `ralph-doctor` reads `RALPH_GIT_HOST` (required; values `github` or `ado`) and dispatches to exactly ONE host-specific auth check per run (`github_auth` OR `ado_auth`, never both). The non-dispatched check is reported as `skipped`. The `host_staging` check ALWAYS runs regardless of host — it is the "did the executor stage the right skill bundle" gate, and its absence would mask the most common pod-time misconfiguration (pod built for GitHub, env var changed to ADO, or `host_select.py` never ran). If `RALPH_GIT_HOST` is unset or unknown, the runner fails fast with exit code 2 and an error message that points at the orchestrator's "Environment variables" table.

**Tech Stack:** Python 3.12+, `uv`, `requests`, `responses` (tests), `pytest`, ruff, mypy strict, Anthropic Messages API (token-count endpoint), GitHub REST API v3 (`/user` for auth + `/repos/{owner}/test-permissions` for scope), Azure DevOps REST API 7.1 (re-using `scripts.ado_client` from Plan 2 for shape, but calling `requests` directly so the doctor can distinguish 404 from other failures).

---

## Phases

Ralph supports two git-host backends; the doctor mirrors that split:

- **Phase 1 — GitHub (built NOW in this plan).** `github_auth.py` is implemented and tested here. It is the host check Ralph uses on the developer laptop and in Phase 1 pods.
- **Phase 2 — Azure DevOps (relocated NOW; pod-specific bits land later).** `ado_auth.py` is the relocated `checks/ado.py` from earlier drafts of this plan. The check logic is unchanged (probe ADO with a non-existent PR ID, expect HTTP 404). Phase 2 of the Ralph roadmap may add ADO-pod-specific environment knobs; those are out of scope for this plan.

The dispatcher in `check.py` reads `RALPH_GIT_HOST` once per invocation and runs exactly one of `github_auth` / `ado_auth`. Both files exist on disk from this plan onwards.

---

## File Structure

| Path | Responsibility |
|---|---|
| `skills/ralph-doctor/SKILL.md` | Frontmatter (`name: ralph-doctor`, `description: ...`) + body documenting purpose, inputs, env vars, exit codes, and invocation patterns. Read by Claude Code's Skill tool at discovery time. |
| `skills/ralph-doctor/scripts/__init__.py` | Empty package marker (`"""Empty package marker."""`). Tests import the runner via `importlib.util.spec_from_file_location`; the marker exists so ruff/mypy see the directory as a package. |
| `skills/ralph-doctor/scripts/check.py` | Runner. Argparse CLI; loads settings.json from `--settings` (default `~/.claude/settings.json`); applies `--skip` / `--only` filters; reads `RALPH_GIT_HOST` and resolves the host-auth check name (`github_auth` or `ado_auth`); imports each module in `scripts.checks.*` lazily; calls its `check(context)`; aggregates results; emits JSON to stdout; emits human summary to stderr unless `--json`; returns 0/1/2. |
| `skills/ralph-doctor/scripts/checks/__init__.py` | Defines `CheckContext` (the runner's input to each check), `CheckResult` (each check's return shape), `Severity` literal (`error`, `warn`), `Status` literal (`pass`, `fail`, `skipped`), and the `REGISTRY` tuple naming each check module in stable execution order (with both `github_auth` and `ado_auth` listed; the runner skips whichever does not match `RALPH_GIT_HOST`). |
| `skills/ralph-doctor/scripts/checks/permissions.py` | `check()` reads `settings.json["permissions"]["allow"]`. Asserts coverage for `Bash`, `Edit`, `Write`, `Read`, `Grep`, `Glob`, `Skill`, plus skills `pr` and `workitem-fetch` (the host-pure names staged by the executor). Wildcards (`*`, `Bash(*)`, `Skill(*)`) count as coverage. |
| `skills/ralph-doctor/scripts/checks/hooks.py` | `check()` walks `settings.json["hooks"]`, scanning each `command` for `AskUserQuestion`, `input(`, `read -p`, `Read-Host`. Matches on `async: true` hooks downgrade to warn severity. |
| `skills/ralph-doctor/scripts/checks/skills.py` | `check()` walks `~/.claude/skills/<skill>/SKILL.md` + `scripts/*.py` for `AskUserQuestion`. Honours `<!-- ralph-doctor: ignore -->` markers in markdown and `# noqa: ralph-doctor` in Python lines. Missing skills dir → pass with note. |
| `skills/ralph-doctor/scripts/checks/mcp.py` | `check()` reads `mcpServers` from settings.json and any sibling `.mcp.json`. Flags any server whose `command`, `args`, or `env` contains `oauth`/`OAuth`/`--auth`/`--login`/`BROWSER`. Empty config → pass. |
| `skills/ralph-doctor/scripts/checks/host_staging.py` | **NEW.** `check()` verifies the staged `pr/` and `workitem-fetch/` skill directories on disk match `RALPH_GIT_HOST`. Reads `<skills_dir>/pr/SKILL.md` and `<skills_dir>/workitem-fetch/SKILL.md`, parses YAML frontmatter, asserts `name:` equals `pr-<host>` and `workitem-fetch-<host>` respectively. Always runs (host-agnostic gate). |
| `skills/ralph-doctor/scripts/checks/auth.py` | `check()` POSTs a single-message body to `/v1/messages/count_tokens` (model `claude-haiku-4-5`, no quota consumed). 2xx → pass; else fail. Under `RALPH_USE_BEDROCK=1`, calls `boto3.client('bedrock').list_foundation_models()` with a 5s timeout. |
| `skills/ralph-doctor/scripts/checks/github_auth.py` | **NEW (Phase 1).** `check()` GETs `https://api.github.com/user` with `Authorization: Bearer $GH_TOKEN`. 2xx → auth works. Then GETs `https://api.github.com/repos/{GH_OWNER}/test-permissions` — 404 is fine (proves PAT can hit the repos surface); 403 → scope missing; other non-2xx/non-404 → fail. Only runs when `RALPH_GIT_HOST=github`. |
| `skills/ralph-doctor/scripts/checks/ado_auth.py` | **RELOCATED** from earlier `checks/ado.py`. `check()` GETs `git/repositories/{repo}/pullrequests/999999999`. 404 → pass (proves PAT auth + project routing). Any other status → fail. Requires `ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT`, `ADO_REPOSITORY`. Only runs when `RALPH_GIT_HOST=ado`. |
| `tests/skills/test_ralph_doctor.py` | Pytest tests. Synthetic settings.json files via `tmp_path`; `responses` mocks Anthropic + GitHub + ADO endpoints. Covers each pass/fail branch of every check plus the runner's exit-code matrix and the host dispatcher. |
| `pyproject.toml` | No change required — Plan 3 already added `skills/` and `tests/skills/` to mypy `files` and pytest `testpaths`. Re-asserted in Task 1. |

---

## Task 1 — Confirm preconditions and create the skill scaffold

**Files**
- Create: `skills/ralph-doctor/SKILL.md`
- Create: `skills/ralph-doctor/scripts/__init__.py`

**Steps**

- [x] 1. Confirm Plans 1, 2, and 3 are merged so the toolchain is wired correctly:
  ```
  uv run pytest tests/test_workspace_samples.py tests/test_ado_client.py tests/skills/test_ralph_add.py -v
  ```
  Expected: every test passes. If any fail, STOP — Plan 11 depends on `scripts.ado_client` (Plan 2) and the `skills/` package layout (Plan 3).

- [x] 2. Verify `pyproject.toml` already includes `skills` and `tests/skills` in mypy's `files` and pytest's `testpaths`:
  ```
  uv run python -c "import tomllib; data=tomllib.load(open('pyproject.toml','rb')); assert 'skills' in data['tool']['mypy']['files'], data['tool']['mypy']['files']; assert 'tests' in data['tool']['pytest']['ini_options']['testpaths']"
  ```
  Expected: exit code 0. If the assertion fails, STOP and rerun Plan 3 task 1.

- [x] 3. Create `skills/ralph-doctor/scripts/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [x] 4. Create `skills/ralph-doctor/SKILL.md` with the content below. The frontmatter is the contract Claude Code's Skill tool reads at discovery time. Keep the fenced markdown blocks inside `SKILL.md` (the JSON example, the table) — they are part of the documented contract.
  ````markdown
  ---
  name: ralph-doctor
  description: Verify the host environment (laptop or pod) is ralph-safe BEFORE the executor starts. Runs seven preflight checks — permissions.allow coverage; hooks free of AskUserQuestion / stdin reads; skills free of AskUserQuestion in their main path; MCP servers configured with non-interactive auth; Anthropic (or Bedrock) auth resolves on cold start; staged `pr/` + `workitem-fetch/` skills match RALPH_GIT_HOST; host-specific auth check (GitHub PAT for `github`, ADO PAT for `ado`) — and refuses to let Ralph start if any error-severity check fails. Reads ~/.claude/settings.json by default; the path is configurable via --settings for tests and alternative install layouts.
  ---

  # ralph-doctor

  ## What this skill does

  `ralph-doctor` is the preflight gate for `ralph-executor`. It catches the
  one class of failure that silently kills unattended pods: a hook or skill
  that expects a human (an `AskUserQuestion` call, a `read -p` prompt, an
  OAuth refresh) — and it now also catches the host-staging class of
  failure: a pod built for one git host that was started with the wrong
  `RALPH_GIT_HOST` value, or a pod whose `host_select.py` never ran. The
  spec is explicit about this — see
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
  | `--skip <name[,name...]>` | no | Check names to skip (e.g. `--skip auth,github_auth` for offline runs). |
  | `--only <name[,name...]>` | no | Check names to run; everything else is skipped. Mutually exclusive with `--skip`. |
  | `--json` | no | Suppress the human summary on stderr; emit JSON only. |
  | `--strict` | no | Treat warn-severity failures as errors. |

  ## Environment variables

  | Variable | Used by | Purpose |
  |---|---|---|
  | `RALPH_GIT_HOST` | runner | **Required.** `github` or `ado`. Selects the host-auth check. Unset → exit 2 with pointer to the orchestrator env-var table. |
  | `ANTHROPIC_API_KEY` | `auth` | Anthropic Messages API key. Required unless `RALPH_USE_BEDROCK=1`. |
  | `RALPH_USE_BEDROCK` | `auth` | Set to `1` to probe AWS Bedrock instead of Anthropic. |
  | `GH_TOKEN` | `github_auth` | GitHub PAT. Required when `RALPH_GIT_HOST=github`. |
  | `GH_OWNER` | `github_auth` | GitHub org or user. Required when `RALPH_GIT_HOST=github`. |
  | `ADO_PAT` | `ado_auth` | ADO Personal Access Token. Required when `RALPH_GIT_HOST=ado`. |
  | `ADO_ORG_URL` | `ado_auth` | ADO org URL. Required when `RALPH_GIT_HOST=ado`. |
  | `ADO_PROJECT` | `ado_auth` | ADO project name. Required when `RALPH_GIT_HOST=ado`. |
  | `ADO_REPOSITORY` | `ado_auth` | ADO repository name. Required when `RALPH_GIT_HOST=ado`. |
  | `RALPH_LOG_LEVEL` | runner | `INFO` (default), `DEBUG`, `WARNING`. Controls stderr verbosity. |

  ## Exit codes

  | Code | Meaning |
  |---|---|
  | `0` | Every error-severity check passed. |
  | `1` | At least one error-severity check failed. Pod must NOT start. |
  | `2` | Internal failure (missing/malformed settings.json, mutually-exclusive CLI flags, unknown check name, `RALPH_GIT_HOST` unset or unknown). |

  ## Output

  Stdout: single JSON document. Stderr: human summary (suppressed by
  `--json`). Example:

  ```json
  {
    "ok": true,
    "exit_code": 0,
    "summary": {"errors": 0, "warns": 0, "passes": 7, "skips": 1},
    "checks": [
      {"name": "permissions", "severity": "error", "status": "pass",
       "message": "permissions.allow covers all 7 required tools and 2 required skills."}
    ]
  }
  ```

  The `skips: 1` line reflects the non-dispatched host-auth check (e.g.
  `ado_auth` when `RALPH_GIT_HOST=github`).

  ## How it is invoked

  ```bash
  RALPH_GIT_HOST=github uv run python skills/ralph-doctor/scripts/check.py
  RALPH_GIT_HOST=github uv run python skills/ralph-doctor/scripts/check.py --skip auth --json
  RALPH_GIT_HOST=ado uv run python skills/ralph-doctor/scripts/check.py --strict --only permissions,hooks
  ```

  Tests live at `tests/skills/test_ralph_doctor.py`.

  ## The seven checks

  | Check | Severity | What it asserts | Runs when |
  |---|---|---|---|
  | `permissions` | error | `permissions.allow` covers Bash, Edit, Write, Read, Grep, Glob, Skill, and skills `pr`, `workitem-fetch` (wildcards honoured). | always |
  | `hooks` | error | No active hook contains `AskUserQuestion`, `input(`, `read -p`, or `Read-Host`. `async: true` matches → warn. | always |
  | `skills` | error | No installed skill's `SKILL.md` or `scripts/*.py` calls `AskUserQuestion` (heuristic substring scan). | always |
  | `mcp` | error | No MCP server requires OAuth / browser redirect (`oauth`, `--auth`, `--login`, `BROWSER`). | always |
  | `auth` | error | Anthropic (or Bedrock if `RALPH_USE_BEDROCK=1`) auth resolves on cold start via a no-op API call. | always |
  | `host_staging` | error | Staged `pr/SKILL.md` and `workitem-fetch/SKILL.md` have frontmatter `name:` equal to `pr-<RALPH_GIT_HOST>` and `workitem-fetch-<RALPH_GIT_HOST>`. | always |
  | `github_auth` | error | `GET /user` returns 2xx (PAT works); `GET /repos/{GH_OWNER}/test-permissions` returns 404 (fine) or 2xx (also fine); 403 → fail (scopes). | when `RALPH_GIT_HOST=github` |
  | `ado_auth` | error | `pullrequests/999999999` returns HTTP 404 (proves PAT auth + project routing). | when `RALPH_GIT_HOST=ado` |

  ## What this skill does NOT do

  - It does not modify settings.json. Findings are read-only.
  - It does not check whether `ralph-executor` itself is installed (Plan 12).
  - It does not check git remote access (Plan 12 covers `git ls-remote`).
  - It does not exercise `claude -p` itself — the auth probe is a cheap proxy.
  - It does not stage skills itself — that's `host_select.py` in Plan 7. `host_staging` only verifies the result.

  ## Trade-offs

  The `skills` check is heuristic. A skill that hides `AskUserQuestion`
  behind a dynamic call (`getattr(self, 'Ask' + 'UserQuestion')()`)
  evades the substring scan. v2 may add AST-based scanning; v1
  optimises for false-positive resistance by honouring
  `<!-- ralph-doctor: ignore -->` markers in markdown and
  `# noqa: ralph-doctor` in Python lines.
  ````
  Expected: file is exactly that content; the frontmatter block is bounded by the two `---` fences.

- [x] 5. Stage and commit:
  ```
  git add skills/ralph-doctor/SKILL.md skills/ralph-doctor/scripts/__init__.py
  git commit -m "chore(skills): scaffold ralph-doctor skill (SKILL.md, package marker)"
  ```

---

## Task 2 — Define the check protocol

**Files**
- Create: `skills/ralph-doctor/scripts/checks/__init__.py`

**Steps**

- [x] 1. Create `skills/ralph-doctor/scripts/checks/__init__.py` with exactly the content below. This module is the contract every individual check obeys. The frozen dataclasses are deliberate — checks must not mutate the context, and the runner must not mutate results after collection.

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
      git_host: str = ""
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
      "host_staging",
      "github_auth",
      "ado_auth",
  )
  """Module names under ``scripts.checks`` to execute, in order.

  Ordering rationale:
  1. Settings-only checks (permissions / hooks / skills / mcp) first —
     cheap and offline.
  2. ``auth`` — Anthropic (or Bedrock) probe; the AI surface must work
     before anything else matters.
  3. ``host_staging`` — host-agnostic gate that verifies the staged
     skill bundle matches ``RALPH_GIT_HOST``. Cheap (filesystem read).
  4. ``github_auth`` and ``ado_auth`` — host-specific network probes.
     The runner skips whichever does not match ``RALPH_GIT_HOST``.
  """

  HOST_AUTH_CHECKS: dict[str, str] = {
      "github": "github_auth",
      "ado": "ado_auth",
  }
  """Map of ``RALPH_GIT_HOST`` value to the host-auth check module name."""
  ```

- [x] 2. Verify mypy is clean on the new file:
  ```
  uv run mypy --config-file pyproject.toml skills/ralph-doctor
  ```
  Expected: no errors.

- [x] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/__init__.py
  git commit -m "feat(ralph-doctor): define CheckResult / CheckContext / REGISTRY contract"
  ```

---

## Task 3 — Write the failing test suite

**Files**
- Create: `tests/skills/test_ralph_doctor.py`

**Steps**

- [x] 1. Write `tests/skills/test_ralph_doctor.py` covering the runner and every check. The file must contain nine test classes — `TestRunnerExitCodes`, `TestHostDispatch`, `TestPermissionsCheck`, `TestHooksCheck`, `TestSkillsCheck`, `TestMcpCheck`, `TestAuthCheck`, `TestHostStagingCheck`, `TestGithubAuthCheck`, `TestAdoAuthCheck` — and the helpers / fixtures described below. The module-loading fixture intentionally fails first (the runner does not yet exist) — that is the red step.

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
  GITHUB_USER_URL = "https://api.github.com/user"
  GITHUB_OWNER = "example-org"
  GITHUB_TEST_REPO_URL = (
      f"https://api.github.com/repos/{GITHUB_OWNER}/test-permissions"
  )
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
  - `GOOD_SETTINGS` — `permissions.allow` lists `Bash(*)`, `Edit(*)`, `Write(*)`, `Read(*)`, `Grep(*)`, `Glob(*)`, `Skill(pr)`, `Skill(workitem-fetch)`, `Skill(ralph-doctor)`. One async PostToolUse hook running `python /opt/ralph/log_observation.py`. One SessionStart hook running a non-interactive script. One MCP server using `ADO_PAT` from env (no OAuth).
  - `BAD_PERMISSIONS_SETTINGS` — `permissions.allow` only contains `Bash(*)` and `Read(*)`. Empty hooks. Empty mcpServers.
  - `BAD_HOOK_SETTINGS` — `permissions` from `GOOD_SETTINGS`. One PreToolUse hook whose command contains the literal string `claude.AskUserQuestion('confirm?')`.
  - `ASYNC_HOOK_WITH_INPUT_SETTINGS` — `permissions` from `GOOD_SETTINGS`. One async PostToolUse hook whose command contains `input(`. This is the warn-severity case.
  - `BAD_MCP_OAUTH_SETTINGS` — `permissions` from `GOOD_SETTINGS`. One MCP server with `args: ["--auth", "oauth"]`.

  Helpers:
  ```python
  def _write_settings(path: Path, data: dict[str, object]) -> None:
      path.write_text(json.dumps(data), encoding="utf-8")


  def _build_skills_dir(
      tmp_path: Path,
      *,
      include_ask_user_question: bool = False,
      host: str = "github",
      stage_mismatch: bool = False,
      missing_skill: str | None = None,
  ) -> Path:
      """Build a synthetic ~/.claude/skills/ tree.

      Always writes clean ``pr/SKILL.md`` and ``workitem-fetch/SKILL.md``
      whose frontmatter ``name:`` matches ``pr-<host>`` / ``workitem-fetch-<host>``.

      If ``include_ask_user_question`` is True, also writes an
      ``asks-questions`` skill whose ``scripts/main.py`` imports and
      calls ``AskUserQuestion``.

      If ``stage_mismatch`` is True, writes ``pr/SKILL.md`` with
      ``name: pr-<other host>`` to simulate the "pod built for the wrong
      host" case.

      If ``missing_skill`` is set to ``"pr"`` or ``"workitem-fetch"``,
      that skill's directory is omitted entirely.
      """
      # ... mkdir + write SKILL.md and scripts/*.py per the description ...


  def _make_context(
      settings: Path, skills_dir: Path, *, git_host: str = "github"
  ) -> object:
      """Duck-typed CheckContext that avoids the hyphenated-package import.

      Each check accepts any object with .settings_path, .skills_dir,
      .strict, .git_host, .extra attributes.
      """
      class _Ctx:
          def __init__(self) -> None:
              self.settings_path = settings
              self.skills_dir = skills_dir
              self.strict = False
              self.git_host = git_host
              self.extra: dict[str, str] = {}
      return _Ctx()


  @pytest.fixture
  def good_env_github(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("RALPH_GIT_HOST", "github")
      monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
      monkeypatch.delenv("RALPH_USE_BEDROCK", raising=False)
      monkeypatch.setenv("GH_TOKEN", "ghp_fake")
      monkeypatch.setenv("GH_OWNER", GITHUB_OWNER)
      monkeypatch.delenv("ADO_PAT", raising=False)


  @pytest.fixture
  def good_env_ado(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("RALPH_GIT_HOST", "ado")
      monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
      monkeypatch.delenv("RALPH_USE_BEDROCK", raising=False)
      monkeypatch.setenv("ADO_PAT", "fake-pat")
      monkeypatch.setenv("ADO_ORG_URL", ADO_ORG)
      monkeypatch.setenv("ADO_PROJECT", ADO_PROJECT)
      monkeypatch.setenv("ADO_REPOSITORY", ADO_REPO)
      monkeypatch.delenv("GH_TOKEN", raising=False)
  ```

  **`TestRunnerExitCodes`** — eight tests against `runner_module.main([...])`. Each writes a settings.json into `tmp_path`, builds a skills dir for the active host, calls `main(argv)`, captures stdout via `capsys`, asserts on JSON shape and the returned exit code:
  - `test_all_pass_returns_zero_github` — `GOOD_SETTINGS` + `good_env_github` + matching skills dir; mocks Anthropic POST → 200, GitHub `GET /user` → 200, GitHub repos URL → 404. Asserts exit 0, `payload["ok"] is True`, `summary.passes == 7`, `summary.skips == 1` (ado_auth skipped), all eight check names present.
  - `test_missing_permission_returns_one` — `BAD_PERMISSIONS_SETTINGS` + `good_env_github`; runs with `--skip auth,github_auth,host_staging`. Asserts exit 1, the `permissions` entry has `status == "fail"`.
  - `test_skip_runs_subset` — `GOOD_SETTINGS` + `good_env_github`; `--skip auth,github_auth,host_staging`. Asserts exit 0; the three skipped entries have `status == "skipped"`.
  - `test_only_runs_named_subset` — `--only permissions`; everything else skipped.
  - `test_skip_and_only_are_mutually_exclusive` — passing both → exit 2 with `"mutually exclusive"` in stderr.
  - `test_missing_settings_path_returns_two` — `--settings <missing path>` → exit 2 with `"settings"` in stderr.
  - `test_malformed_settings_returns_two` — settings.json contains `{not json` → exit 2.
  - `test_strict_promotes_warn_to_error` — `ASYNC_HOOK_WITH_INPUT_SETTINGS` + `good_env_github` + `--strict --skip auth,github_auth,host_staging` → exit 1.

  **`TestHostDispatch`** — six tests against `runner_module.main([...])`. These specifically exercise the `RALPH_GIT_HOST` dispatcher logic:
  - `test_github_host_skips_ado_auth` — `good_env_github` + `GOOD_SETTINGS` + skills dir staged for github; mocks all required URLs. Asserts the `ado_auth` entry has `status == "skipped"` and its message names `RALPH_GIT_HOST`.
  - `test_ado_host_skips_github_auth` — `good_env_ado` + `GOOD_SETTINGS` + skills dir staged for ado; mocks Anthropic + ADO. Asserts the `github_auth` entry has `status == "skipped"`.
  - `test_unset_host_returns_two` — `monkeypatch.delenv("RALPH_GIT_HOST", raising=False)` → exit 2 with `"RALPH_GIT_HOST"` in stderr.
  - `test_unknown_host_returns_two` — `RALPH_GIT_HOST=gitea` → exit 2 with `"unknown"` or `"gitea"` in stderr.
  - `test_host_staging_runs_for_both_hosts` — repeat the github + ado happy paths, assert the `host_staging` entry is `pass` in both runs.
  - `test_host_dispatch_message_points_at_orchestrator` — `test_unset_host_returns_two` re-check; the stderr message contains `"RALPH_GIT_HOST"` and the orchestrator filename `"2026-05-24-00-orchestrator.md"` (or a substring close enough — `"orchestrator"` is acceptable).

  **`TestPermissionsCheck`** — five tests against `_load_check_module("permissions")`:
  - `test_all_required_tools_present_passes` — `GOOD_SETTINGS` → `pass`.
  - `test_missing_tool_fails` — `BAD_PERMISSIONS_SETTINGS` → `fail`; `result.details["missing"]` contains `Edit`, `Write`, `Grep`, `Glob`, `Skill`.
  - `test_global_wildcard_covers_everything` — `permissions.allow == ["*"]` → `pass`.
  - `test_missing_permissions_section_fails` — settings.json has no `permissions` key → `fail`.
  - `test_skill_subkey_recognised` — `Skill(pr)` + `Skill(workitem-fetch)` alone count as coverage for the host-pure skill names.

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
  - `test_anthropic_200_passes` — `good_env_github`; mock 200 → `pass`.
  - `test_anthropic_401_fails` — `good_env_github`; mock 401 → `fail`; message contains `"401"`.
  - `test_missing_api_key_fails` — `monkeypatch.delenv("ANTHROPIC_API_KEY")` and `RALPH_USE_BEDROCK` unset → `fail`; message contains `ANTHROPIC_API_KEY`.

  **`TestHostStagingCheck`** — seven tests against `_load_check_module("host_staging")`:
  - `test_github_staged_correctly_passes` — `_build_skills_dir(tmp_path, host="github")` + `git_host="github"` → `pass`; message contains `pr-github` and `workitem-fetch-github`.
  - `test_ado_staged_correctly_passes` — `_build_skills_dir(tmp_path, host="ado")` + `git_host="ado"` → `pass`.
  - `test_mismatch_fails` — `_build_skills_dir(tmp_path, host="github", stage_mismatch=True)` + `git_host="github"` → `fail`; message contains `pr-ado` (the actual frontmatter) and `pr-github` (the expected name) and the words `RALPH_GIT_HOST` and `host_select`.
  - `test_missing_pr_skill_fails` — `_build_skills_dir(tmp_path, missing_skill="pr")` + `git_host="github"` → `fail`; message contains `pr/SKILL.md` and `not found`.
  - `test_missing_workitem_fetch_skill_fails` — analogous with `missing_skill="workitem-fetch"` → `fail`; message contains `workitem-fetch/SKILL.md`.
  - `test_malformed_frontmatter_fails` — write a `pr/SKILL.md` with no frontmatter fences → `fail`; message contains `frontmatter` and `name`.
  - `test_unknown_git_host_in_context_fails` — `git_host="gitea"` → `fail`; message names the unknown host.

  **`TestGithubAuthCheck`** — five tests against `_load_check_module("github_auth")`. Each opens `responses.RequestsMock()` and configures `good_env_github`:
  - `test_user_200_and_repo_404_passes` — `GET /user` → 200, `GET /repos/{owner}/test-permissions` → 404 → `pass`; message contains `GH_TOKEN` and `404` or `auth ok`.
  - `test_user_401_fails` — `GET /user` → 401 → `fail`; message contains `401` and `GH_TOKEN`.
  - `test_user_200_repo_403_fails_with_scope_hint` — `GET /user` → 200, `GET /repos/...` → 403 → `fail`; message contains `403` and `scope`.
  - `test_user_network_error_fails` — `responses` raises `ConnectionError` → `fail`; message contains `raised`.
  - `test_missing_env_var_fails` — `monkeypatch.delenv("GH_TOKEN")` → `fail`; message contains `GH_TOKEN`.
  - `test_missing_gh_owner_fails` — `monkeypatch.delenv("GH_OWNER")` → `fail`; message contains `GH_OWNER`.

  **`TestAdoAuthCheck`** — four tests. Each mocks `f"{ADO_ORG}/{ADO_PROJECT}/_apis/git/repositories/{ADO_REPO}/pullrequests/{NON_EXISTENT_PR_ID}"`:
  - `test_404_passes` — `good_env_ado`; mock 404 → `pass`.
  - `test_401_fails` — mock 401 → `fail`; message contains `"401"`.
  - `test_200_means_routing_is_wrong_and_fails` — mock 200 → `fail`; message contains `"200"`.
  - `test_missing_env_var_fails` — `delenv("ADO_PAT")` → `fail`; message contains `ADO_PAT`.

- [x] 2. Run the new test file. Every test must fail because `check.py` and the check modules do not yet exist:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py -v
  ```
  Expected: the module-loading fixtures raise `AssertionError: missing entry script at ...` and `AssertionError: missing check module at ...`. This is the red step.

- [x] 3. Do NOT commit yet. The failing tests are consumed by Tasks 4–12. Holding the commit until Task 4 keeps the repo in a coherent state (red, with the runner missing) rather than red-and-impossible-to-import.

---

## Task 4 — Implement the runner (with host dispatch)

**Files**
- Create: `skills/ralph-doctor/scripts/check.py`

**Steps**

- [x] 1. Create `skills/ralph-doctor/scripts/check.py`. The runner has four responsibilities: load the check contract (via `importlib.util.spec_from_file_location` because the directory name contains a hyphen), parse CLI args, read `RALPH_GIT_HOST` and resolve the host-auth check (`github_auth` or `ado_auth`), then iterate `REGISTRY` invoking each check and skipping the non-dispatched host check.

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
  HOST_AUTH_CHECKS: dict[str, str] = (
      _checks_pkg.HOST_AUTH_CHECKS  # type: ignore[attr-defined]
  )

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

  Host resolution (fails fast with exit 2 on misconfig):
  ```python
  def _resolve_host_dispatch() -> tuple[str, str, frozenset[str]]:
      """Read RALPH_GIT_HOST and return (host, active_auth_check, skipped_auth_checks).

      Raises ``_CliError`` (-> exit 2) if RALPH_GIT_HOST is unset or unknown.
      """
      raw = os.environ.get("RALPH_GIT_HOST", "").strip().lower()
      if not raw:
          raise _CliError(
              "RALPH_GIT_HOST is not set. Set it to 'github' or 'ado'. "
              "See docs/superpowers/plans/2026-05-24-00-orchestrator.md "
              "(Environment variables table)."
          )
      if raw not in HOST_AUTH_CHECKS:
          raise _CliError(
              f"RALPH_GIT_HOST={raw!r} is not a known value (expected "
              f"one of {sorted(HOST_AUTH_CHECKS)}). See the orchestrator "
              "env-var table."
          )
      active = HOST_AUTH_CHECKS[raw]
      skipped = frozenset(
          name for host, name in HOST_AUTH_CHECKS.items() if host != raw
      )
      return raw, active, skipped
  ```

  Filter resolution (now aware of host-skipped checks):
  ```python
  def _determine_active_checks(
      args: argparse.Namespace, host_skipped: frozenset[str]
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
          # Host-skipped checks remain skipped even under --only, unless
          # explicitly named (which we still honour to keep tests simple).
          to_run = only_set - host_skipped
          to_skip = (set(REGISTRY) - only_set) | host_skipped
          return to_run, to_skip
      to_skip = skip_set | host_skipped
      return set(REGISTRY) - to_skip, to_skip
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


  def _make_context(args: argparse.Namespace, git_host: str) -> CheckContext:
      return CheckContext(
          settings_path=Path(args.settings).expanduser().resolve(),
          skills_dir=Path(args.skills_dir).expanduser().resolve(),
          strict=bool(args.strict),
          git_host=git_host,
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
              f"  {marker} {entry['name']:<14} "
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
          git_host, _active_auth, host_skipped = _resolve_host_dispatch()
          to_run, to_skip = _determine_active_checks(args, host_skipped)
          if any(name in to_run for name in ("permissions", "hooks", "mcp")):
              _load_settings(Path(args.settings).expanduser())
      except _CliError as exc:
          sys.stderr.write(f"ralph-doctor: {exc}\n")
          return 2

      context = _make_context(args, git_host)
      results: list[CheckResult] = []
      for name in REGISTRY:
          if name in host_skipped:
              results.append(
                  _skipped_result(
                      name,
                      f"not the active host check for RALPH_GIT_HOST={git_host}",
                  )
              )
              continue
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

- [x] 2. Run the runner-level + host-dispatch tests; per-check tests are still red because their modules are missing:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestRunnerExitCodes tests/skills/test_ralph_doctor.py::TestHostDispatch -v
  ```
  Expected: every `TestRunnerExitCodes` test fails with a `FileNotFoundError` raised inside `_load_check_module` — that is the documented behaviour when a referenced check is missing. The `test_unset_host_returns_two` and `test_unknown_host_returns_two` cases pass (they exit 2 before any check is loaded). The runner itself is correct.

- [x] 3. Stage and commit:
  ```
  git add tests/skills/test_ralph_doctor.py skills/ralph-doctor/scripts/check.py
  git commit -m "feat(ralph-doctor): add runner with host dispatch + failing test suite"
  ```
  Expected: `uv run mypy --config-file pyproject.toml skills/ralph-doctor` is clean.

---

## Task 5 — Implement the `permissions` check

**Files**
- Create: `skills/ralph-doctor/scripts/checks/permissions.py`

**Steps**

- [x] 1. Create `skills/ralph-doctor/scripts/checks/permissions.py`. Module structure:
  - Module docstring summarising the rule: coverage of `Bash`, `Edit`, `Write`, `Read`, `Grep`, `Glob`, `Skill`, plus the host-pure skills `pr` and `workitem-fetch`. An entry covers a tool if it is exactly the tool name, `Tool(...)` (anything between parens), or the global `*`.
  - Imports: `json`, `Path`, plus `CheckContext` / `CheckResult` from `. import` (relative).
  - Module constants:
    ```python
    REQUIRED_TOOLS: tuple[str, ...] = (
        "Bash", "Edit", "Write", "Read", "Grep", "Glob", "Skill",
    )
    REQUIRED_SKILLS: tuple[str, ...] = ("pr", "workitem-fetch")
    ```
  - `_load_allow_list(path: Path) -> list[str] | None` — reads + json.loads; returns the `permissions.allow` list, or `None` if the file is unreadable / missing / malformed / lacks the section.
  - `_tool_is_covered(tool: str, allow: list[str]) -> bool` — true if any entry is `*`, exactly `tool`, or starts with `f"{tool}("` and ends with `)`.
  - `_skill_is_covered(skill_name: str, allow: list[str]) -> bool` — true if any entry is `*`, `Skill`, `Skill(*)`, or `Skill({skill_name})`.
  - `check(context: CheckContext) -> CheckResult`:
    - If `_load_allow_list` returns None → `fail` with message `"settings.json is missing permissions.allow (or permissions is not a JSON object)."` and `details={"settings_path": str(context.settings_path)}`.
    - Compute `missing = [t for t in REQUIRED_TOOLS if not _tool_is_covered(t, allow)]` and `missing_skills = [s for s in REQUIRED_SKILLS if not _skill_is_covered(s, allow)]`.
    - If either non-empty → `fail` with details `{"missing": missing, "missing_skills": missing_skills, "allow": allow}` and a message naming both lists.
    - Otherwise → `pass` with message `f"permissions.allow covers all {len(REQUIRED_TOOLS)} required tools and {len(REQUIRED_SKILLS)} required skills."` and details `{"required_tools": list(REQUIRED_TOOLS), "required_skills": list(REQUIRED_SKILLS), "allow_entries": len(allow)}`.

- [x] 2. Run the permissions tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestPermissionsCheck -v
  ```
  Expected: every test passes. If any fail, fix the check — do NOT change the tests.

- [x] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/permissions.py
  git commit -m "feat(ralph-doctor): implement permissions check"
  ```

---

## Task 6 — Implement the `hooks` check

**Files**
- Create: `skills/ralph-doctor/scripts/checks/hooks.py`

**Steps**

- [x] 1. Create `skills/ralph-doctor/scripts/checks/hooks.py`. Structure:
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

- [x] 2. Run the hooks tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestHooksCheck -v
  ```
  Expected: every test passes.

- [x] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/hooks.py
  git commit -m "feat(ralph-doctor): implement hooks check (AskUserQuestion / stdin scan)"
  ```

---

## Task 7 — Implement the `skills` check

**Files**
- Create: `skills/ralph-doctor/scripts/checks/skills.py`

**Steps**

- [x] 1. Create `skills/ralph-doctor/scripts/checks/skills.py`. Structure:
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

- [x] 2. Run the skills tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestSkillsCheck -v
  ```
  Expected: every test passes.

- [x] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/skills.py
  git commit -m "feat(ralph-doctor): implement skills check (AskUserQuestion scan)"
  ```

---

## Task 8 — Implement the `mcp` check

**Files**
- Create: `skills/ralph-doctor/scripts/checks/mcp.py`

**Steps**

- [x] 1. Create `skills/ralph-doctor/scripts/checks/mcp.py`. Structure:
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

- [x] 2. Run the MCP tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestMcpCheck -v
  ```
  Expected: every test passes.

- [x] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/mcp.py
  git commit -m "feat(ralph-doctor): implement mcp check (no OAuth on cold start)"
  ```

---

## Task 9 — Implement the `auth` check (Anthropic / Bedrock cold start)

**Files**
- Create: `skills/ralph-doctor/scripts/checks/auth.py`

**Steps**

- [x] 1. Create `skills/ralph-doctor/scripts/checks/auth.py`. Structure:
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

- [x] 2. Run the auth tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestAuthCheck -v
  ```
  Expected: every test passes. `responses` intercepts the POST to `https://api.anthropic.com/v1/messages/count_tokens`.

- [x] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/auth.py
  git commit -m "feat(ralph-doctor): implement auth check (Anthropic + Bedrock cold start)"
  ```

---

## Task 10 — Implement the `host_staging` check (always runs)

**Files**
- Create: `skills/ralph-doctor/scripts/checks/host_staging.py`

**Steps**

- [x] 1. Create `skills/ralph-doctor/scripts/checks/host_staging.py`. This check is the "did the executor stage the right skill bundle" gate — it always runs regardless of `RALPH_GIT_HOST`. The check reads the YAML frontmatter of `<skills_dir>/pr/SKILL.md` and `<skills_dir>/workitem-fetch/SKILL.md` and asserts the frontmatter `name:` value equals `pr-<host>` and `workitem-fetch-<host>` respectively. Both skills are required; either missing or mismatched → fail.

  Structure:
  - Module docstring: explains the staging contract. The executor's `host_select.py` (Plan 7) copies or symlinks `skills/pr-<host>/` to `~/.claude/skills/pr/` and likewise for `workitem-fetch`. The frontmatter `name:` field is the load-bearing signal — Claude Code reads it at skill discovery time, and the doctor reads it here to verify the staging step worked.
  - Module constants:
    ```python
    REQUIRED_STAGED_SKILLS: tuple[str, ...] = ("pr", "workitem-fetch")
    KNOWN_HOSTS: tuple[str, ...] = ("github", "ado")
    ```
  - `_parse_frontmatter_name(path: Path) -> tuple[str | None, str]`:
    - Returns `(name, error_reason)`. On success, `name` is the parsed value and `error_reason` is the empty string.
    - Reads the file with `read_text(encoding="utf-8", errors="replace")`.
    - If the file does not start with `---\n` (after optional UTF-8 BOM stripping) → return `(None, "missing opening frontmatter fence")`.
    - Scans line by line for the closing `---` fence. If absent → `(None, "missing closing frontmatter fence")`.
    - Within the frontmatter block, find the FIRST line matching `^name\s*:\s*(.+?)\s*$` (case-sensitive `name`, the canonical Claude Code frontmatter key). Strip surrounding quotes (single or double) and trailing whitespace from the value.
    - If no `name:` line → `(None, "no 'name:' field in frontmatter")`.
    - Otherwise → `(value, "")`.
    - The parser is intentionally tiny — Claude Code's frontmatter format is a strict subset of YAML (top-level scalar fields only). Inlining the parser avoids a PyYAML dependency for the doctor and matches the same approach Plans 1 / 4 take in their PBI-frontmatter helpers (`scripts/pbi_frontmatter.py` if Plan 4's reconciliation rename landed; otherwise the validator's `_split_frontmatter`). If a shared parser exists at `scripts/pbi_frontmatter.py` at execution time, prefer reusing it via `importlib.util` rather than re-inlining.
  - `check(context: CheckContext) -> CheckResult`:
    - If `context.git_host` not in `KNOWN_HOSTS` → `fail` with message `f"host_staging cannot run: RALPH_GIT_HOST={context.git_host!r} is not a known host (expected one of {list(KNOWN_HOSTS)})."` and `details={"git_host": context.git_host}`. This branch should be unreachable via the runner (which fails fast on unknown hosts) but exists for direct-import test coverage.
    - For each `skill_name in REQUIRED_STAGED_SKILLS`:
      - `skill_md = context.skills_dir / skill_name / "SKILL.md"`
      - If not `skill_md.is_file()` → record offence `{"skill": skill_name, "path": str(skill_md), "reason": "SKILL.md not found at staged path"}`.
      - Else parse frontmatter:
        - `parsed, reason = _parse_frontmatter_name(skill_md)`
        - If `parsed is None` → record offence `{"skill": skill_name, "path": str(skill_md), "reason": f"could not parse frontmatter: {reason}"}`.
        - Expected name: `f"{skill_name}-{context.git_host}"`.
        - If `parsed != expected` → record offence `{"skill": skill_name, "path": str(skill_md), "frontmatter_name": parsed, "expected_name": expected, "reason": "frontmatter name does not match RALPH_GIT_HOST"}`.
    - If any offences → `fail` with message:
      ```
      f"Staged skills do not match RALPH_GIT_HOST={context.git_host}. "
      f"Likely cause: the pod was built for a different host and "
      f"RALPH_GIT_HOST was changed at runtime, OR host_select.py did "
      f"not run. Offending entries: {offences}"
      ```
      and `details={"offences": offences, "git_host": context.git_host, "skills_dir": str(context.skills_dir)}`.
    - Otherwise → `pass` with message `f"Staged 'pr' and 'workitem-fetch' skills match RALPH_GIT_HOST={context.git_host}."` and `details={"checked": [...names...], "git_host": context.git_host}`.

- [x] 2. Run the host_staging tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestHostStagingCheck -v
  ```
  Expected: every test passes.

- [x] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/host_staging.py
  git commit -m "feat(ralph-doctor): implement host_staging check (frontmatter vs RALPH_GIT_HOST)"
  ```

---

## Task 11 — Implement the `github_auth` check (Phase 1)

**Files**
- Create: `skills/ralph-doctor/scripts/checks/github_auth.py`

**Steps**

- [x] 1. Create `skills/ralph-doctor/scripts/checks/github_auth.py`. The check probes GitHub's REST API to prove (a) the PAT works at all (`GET /user`) and (b) the PAT has scopes covering the `/repos/...` surface that the `pr-github` skill needs (`GET /repos/{owner}/test-permissions` — a sentinel repo name; 404 means "auth and scopes work, this repo just doesn't exist", which is exactly the cheap proof we want).

  Structure:
  - Module docstring: explains the two-step probe. References the GitHub REST docs for `GET /user` (documented as the standard auth-test endpoint) and `GET /repos/{owner}/{repo}` (returns 404 for non-existent repos when scopes are sufficient; 403 when scopes are insufficient).
  - Module constants:
    ```python
    GITHUB_API_BASE_URL_ENV = "GITHUB_API_BASE_URL"
    DEFAULT_GITHUB_API_BASE = "https://api.github.com"
    USER_PATH = "/user"
    TEST_REPO_NAME = "test-permissions"
    REQUEST_TIMEOUT_SECONDS = 10.0
    REQUIRED_ENV: tuple[str, ...] = ("GH_TOKEN", "GH_OWNER")
    ```
  - `_missing_env_vars() -> list[str]` — returns the names of any required env vars that are unset or whitespace-only.
  - `_user_probe(base_url: str, token: str) -> tuple[bool, str, int]`:
    - Builds `{base_url}{USER_PATH}`.
    - Headers: `Authorization: Bearer {token}`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`.
    - Wraps `requests.get(...)` in try/except for `requests.RequestException` → returns `(False, f"GET /user raised: {exc!r}", 0)`.
    - If `200 <= status_code < 300` → returns `(True, "", status_code)`.
    - Otherwise → returns `(False, f"GET /user returned {status_code}: <body preview>", status_code)`.
  - `_repo_scope_probe(base_url: str, token: str, owner: str) -> tuple[bool, str, int]`:
    - Builds `{base_url}/repos/{owner}/{TEST_REPO_NAME}`.
    - Same headers as `_user_probe`.
    - Wraps in try/except → returns `(False, f"GET /repos/.../{TEST_REPO_NAME} raised: {exc!r}", 0)`.
    - Pass conditions (in this order):
      - `status_code == 404` → `(True, "404 — auth + scopes both work", 404)`.
      - `200 <= status_code < 300` → `(True, "repo exists; auth + scopes both work", status_code)`.
    - Fail conditions:
      - `status_code == 403` → `(False, "403 — PAT lacks 'repo' scope. Re-issue with the 'repo' scope (or fine-grained equivalent for the target repository).", 403)`.
      - `status_code == 401` → `(False, "401 — PAT is invalid (but /user passed earlier; rotate the token).", 401)`.
      - Otherwise → `(False, f"unexpected status {status_code}: <body preview>", status_code)`.
  - `check(context: CheckContext) -> CheckResult`:
    - Missing env vars → `fail` with message `f"github_auth cannot run; missing required env vars: {', '.join(missing)}"` and `details={"missing": missing}`.
    - `base_url = os.environ.get(GITHUB_API_BASE_URL_ENV, DEFAULT_GITHUB_API_BASE).rstrip("/")`.
    - `token = os.environ["GH_TOKEN"].strip()`; `owner = os.environ["GH_OWNER"].strip()`.
    - Step 1: call `_user_probe`. If `not ok` → `fail` with the returned message, details `{"step": "user", "status_code": status, "base_url": base_url}`.
    - Step 2: call `_repo_scope_probe`. If `not ok` → `fail` with message `f"GH_TOKEN auth ok (/user returned 2xx) but scope probe failed: {scope_msg}"`, details `{"step": "repo_scope", "status_code": scope_status, "owner": owner}`.
    - Otherwise → `pass` with message `f"GitHub auth resolved (/user 2xx; /repos/{owner}/{TEST_REPO_NAME} scope probe: {scope_msg})."` and details `{"user_status": user_status, "scope_status": scope_status, "owner": owner}`.

- [x] 2. Run the github_auth tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestGithubAuthCheck -v
  ```
  Expected: every test passes.

- [x] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/github_auth.py
  git commit -m "feat(ralph-doctor): implement github_auth check (Phase 1: /user + scope probe)"
  ```

---

## Task 12 — Implement the `ado_auth` check (relocated)

**Files**
- Create: `skills/ralph-doctor/scripts/checks/ado_auth.py`

**Steps**

- [x] 1. Create `skills/ralph-doctor/scripts/checks/ado_auth.py`. The check goes one level below `AdoClient` (Plan 2) and uses `requests` directly — `AdoClient.get` raises on 404, but the doctor needs 404-as-success. This file is the relocated, renamed `checks/ado.py` from earlier drafts; the probe logic is unchanged.

  Structure:
  - Module docstring: probes the ADO PR show endpoint against a non-existent PR ID. 404 → pass (PAT auth + project routing both work). Any other status / network failure → fail. Requires `ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT`, `ADO_REPOSITORY`. This check runs only when `RALPH_GIT_HOST=ado`; the runner skips it otherwise.
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
    - If `_missing_env_vars()` returns a non-empty list → `fail` with message `f"ado_auth check cannot run; missing required env vars: {', '.join(missing)}"` and `details={"missing": missing}`.
    - Build URL: `f"{org.rstrip('/')}/{project}/_apis/git/repositories/{repo}/pullrequests/{NON_EXISTENT_PR_ID}"`.
    - `requests.get(url, auth=("", pat), timeout=REQUEST_TIMEOUT_SECONDS, params={"api-version": "7.1"})` inside a try/except for `requests.RequestException` → `fail` with `f"ADO probe raised: {exc!r}"`.
    - If `response.status_code == 404` → `pass` with message `f"ADO PAT works (404 on non-existent PR {NON_EXISTENT_PR_ID}, as expected)."` and `details={"status_code": 404, "org": org, "project": project, "repo": repo}`.
    - Otherwise → `fail` with message `f"ADO probe returned {status} (expected 404); body preview: {preview!r}"` (preview is the first 200 chars of `response.text`, or `"<empty>"`), and `details={"status_code": status, "url": url}`.

- [x] 2. Run the ado_auth tests:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py::TestAdoAuthCheck -v
  ```
  Expected: every test passes.

- [x] 3. Stage and commit:
  ```
  git add skills/ralph-doctor/scripts/checks/ado_auth.py
  git commit -m "feat(ralph-doctor): implement ado_auth check (Phase 2 relocated; PAT 404 probe)"
  ```

---

## Task 13 — Full-suite verification, lint, and live smoke

**Files**
- None (verification only)

**Steps**

- [x] 1. Run the full skill test file:
  ```
  uv run pytest tests/skills/test_ralph_doctor.py -v
  ```
  Expected: every test (TestRunnerExitCodes + TestHostDispatch + TestPermissionsCheck + TestHooksCheck + TestSkillsCheck + TestMcpCheck + TestAuthCheck + TestHostStagingCheck + TestGithubAuthCheck + TestAdoAuthCheck) passes. If any fail, STOP and fix the check or runner — do NOT change the tests.

- [x] 2. Run the full project test suite to confirm no regression:
  ```
  uv run pytest -v
  ```
  Expected: every test in the repo passes.

- [x] 3. Run the lint / type / format gates:
  ```
  uv run ruff check skills/ralph-doctor tests/skills/test_ralph_doctor.py
  uv run ruff format --check skills/ralph-doctor tests/skills/test_ralph_doctor.py
  uv run mypy --config-file pyproject.toml skills/ralph-doctor
  ```
  Expected: all three commands pass with zero findings. Fix any inline; this is the conventions self-review step.

- [x] 4. Smoke the runner end-to-end against this developer's real `~/.claude/settings.json` (offline checks only). The developer's local laptop is Phase 1 — set `RALPH_GIT_HOST=github`:
  ```
  RALPH_GIT_HOST=github uv run python skills/ralph-doctor/scripts/check.py --skip auth,github_auth,host_staging
  ```
  Expected: JSON document on stdout with `summary.passes >= 4`; human summary on stderr listing each check; exit code 0 if the local config is ralph-safe. If it returns non-zero, that's a real finding — record it (the doctor is doing its job) but do NOT change the doctor to ignore it.

- [x] 5. Smoke the host-dispatcher behaviour (no real env needed — settings.json missing → exit 2 is expected, but we're checking the host dispatch wins first):
  ```
  uv run python skills/ralph-doctor/scripts/check.py
  echo "exit=$?"
  ```
  Expected: exit 2; stderr contains `RALPH_GIT_HOST is not set` and the pointer to the orchestrator file.

- [x] 6. Stage and commit any format / lint fixes from step 3 (skip if none):
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
  - `skills/ralph-doctor/scripts/checks/`: `__init__.py`, `permissions.py`, `hooks.py`, `skills.py`, `mcp.py`, `auth.py`, `host_staging.py`, `github_auth.py`, `ado_auth.py`

- [ ] 4. Sanity-check the host dispatcher fails fast on unset `RALPH_GIT_HOST`:
  ```
  uv run python skills/ralph-doctor/scripts/check.py
  echo "exit_code=$?"
  ```
  Expected: stderr contains `RALPH_GIT_HOST is not set`; final shell line shows `exit_code=2`.

- [ ] 5. Sanity-check exit code 1 on a deliberately broken settings.json under the github host:
  ```
  echo '{"permissions":{"allow":["Bash"]}}' > /tmp/bad_settings.json
  RALPH_GIT_HOST=github GH_TOKEN=fake GH_OWNER=fake \
  ANTHROPIC_API_KEY=fake \
  uv run python skills/ralph-doctor/scripts/check.py \
      --settings /tmp/bad_settings.json \
      --skip auth,github_auth,host_staging,skills,mcp \
      --json
  echo "exit_code=$?"
  rm /tmp/bad_settings.json
  ```
  Expected: JSON shows `"ok": false` and `"exit_code": 1`; the final shell line shows `exit_code=1`.

- [ ] 6. Sanity-check that staging mismatch is caught — build a tiny skills tree where `pr/SKILL.md` claims `name: pr-ado` while `RALPH_GIT_HOST=github`:
  ```
  STAGE=$(mktemp -d)
  mkdir -p "$STAGE/pr" "$STAGE/workitem-fetch"
  printf -- "---\nname: pr-ado\ndescription: stub\n---\n" > "$STAGE/pr/SKILL.md"
  printf -- "---\nname: workitem-fetch-github\ndescription: stub\n---\n" > "$STAGE/workitem-fetch/SKILL.md"
  echo '{"permissions":{"allow":["*"]}}' > "$STAGE/settings.json"
  RALPH_GIT_HOST=github GH_TOKEN=x GH_OWNER=x ANTHROPIC_API_KEY=x \
  uv run python skills/ralph-doctor/scripts/check.py \
      --settings "$STAGE/settings.json" \
      --skills-dir "$STAGE" \
      --only host_staging \
      --json
  echo "exit_code=$?"
  rm -rf "$STAGE"
  ```
  Expected: JSON shows the `host_staging` entry with `status: fail` and message containing `pr-ado` (the actual) and `pr-github` (the expected); `exit_code=1`.

- [ ] 7. Open a PR:
  ```
  git push -u origin HEAD
  gh pr create --title "feat(ralph-doctor): preflight skill (Plan 11)" --body "$(cat <<'EOF'
  ## Summary
  - Adds the ralph-doctor preflight skill (Plan 11 of 13)
  - Seven checks: permissions / hooks / skills / mcp / auth / host_staging / host-specific (github_auth Phase 1, ado_auth Phase 2)
  - Host-specific auth dispatched via RALPH_GIT_HOST
  - host_staging always runs (verifies executor staged the right skill bundle)
  - Refuses to let the pod start if any error-severity check fails

  ## Plan
  See `docs/superpowers/plans/2026-05-24-11-ralph-doctor.md`.

  ## Test plan
  - [ ] `uv run pytest tests/skills/test_ralph_doctor.py -v`
  - [ ] `uv run ruff check . && uv run mypy --config-file pyproject.toml ralph_executor scripts skills`
  - [ ] Smoke `RALPH_GIT_HOST=github uv run python skills/ralph-doctor/scripts/check.py --skip auth,github_auth,host_staging` against the developer's local settings.json
  - [ ] Smoke unset-host case: `uv run python skills/ralph-doctor/scripts/check.py` → exit 2

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

- `scripts.ado_client.AdoClient` is referenced in the Tech Stack and Task 12 description but NOT imported by `checks/ado_auth.py` (the doctor goes below the client to distinguish 404 from other errors). Tech Stack note re-clarifies this.
- `scripts/pbi_frontmatter.py` (if Plan 4's reconciliation rename of `_split_frontmatter` landed) is the preferred frontmatter parser source for `host_staging.py` — Task 10 step 1 says "if it exists, reuse via `importlib.util`; otherwise inline the tiny parser". Inlining is the safe default because `host_staging` only needs the `name:` field, which is a strict scalar.
- `pyproject.toml` `[tool.mypy].files` containing `skills` and `[tool.pytest.ini_options].testpaths` containing `tests` — Plan 3 established both; Task 1 step 2 re-asserts.
- `tests/skills/__init__.py` exists from Plan 3.
- `responses` library was added in Plan 2 (referenced in Plan 3 too). Task 3 uses it without re-adding.
- `RALPH_GIT_HOST` env var is documented in the orchestrator (`2026-05-24-00-orchestrator.md`, "Environment variables" table) — Plans 2, 3, 5, 7, 11, 12 all read it. The doctor's error message points at that file by name.
- Plan 7's `host_select.py` is the producer of the staging that `host_staging.py` verifies. The check's failure message names `host_select` as a likely cause when staging is wrong.
- GitHub REST endpoints used in `github_auth.py` are documented: `GET /user` (the canonical auth probe — returns 200 + the authenticated user's profile on success, 401 on bad token) and `GET /repos/{owner}/{repo}` (returns 200 if the repo exists and is accessible, 404 if the repo doesn't exist but auth + scopes are fine, 403 if scopes are insufficient).

### Step actionability

- Every numbered step either creates / modifies a file with the content specified, runs a named command with an expected outcome, or makes an assertion. No "consider doing X" or "you might want to" steps.

### Host-handling logic correctness

- `RALPH_GIT_HOST` is read exactly once per run (`_resolve_host_dispatch` in `check.py`). Failure to set or set-to-unknown returns exit 2 BEFORE any check loads, matching the "fail fast" requirement.
- `host_staging` is NOT host-specific itself — it runs for every host and is added to `REGISTRY` outside the `HOST_AUTH_CHECKS` map. The dispatcher only suppresses the non-matching entry in `HOST_AUTH_CHECKS` (i.e. one of `github_auth` / `ado_auth`).
- The skipped host-auth check is reported in the output with `status="skipped"` and a clear message, so a reader of the JSON output can tell which host was active.
- The check-loading loop guards against the host_staging vs host-auth ordering: `host_staging` runs BEFORE either host-auth check, so a wrong-staging configuration is surfaced even if the host-auth probe would also fail (giving the operator the most actionable error first).

### Conventional commits

Every commit message in this plan uses the conventional-commits format:
- `chore(skills): scaffold ralph-doctor skill (...)`
- `feat(ralph-doctor): define CheckResult / CheckContext / REGISTRY contract`
- `feat(ralph-doctor): add runner with host dispatch + failing test suite`
- `feat(ralph-doctor): implement permissions check`
- `feat(ralph-doctor): implement hooks check (...)`
- `feat(ralph-doctor): implement skills check (...)`
- `feat(ralph-doctor): implement mcp check (...)`
- `feat(ralph-doctor): implement auth check (...)`
- `feat(ralph-doctor): implement host_staging check (...)`
- `feat(ralph-doctor): implement github_auth check (Phase 1: /user + scope probe)`
- `feat(ralph-doctor): implement ado_auth check (Phase 2 relocated; PAT 404 probe)`
- `chore(ralph-doctor): apply ruff format + lint fixes`

Scope is `skills` for scaffolding and `ralph-doctor` for incremental check work — consistent with Plan 3.

### TDD discipline

- Task 3 writes the entire failing test suite first (red).
- Task 4 implements the runner with host dispatch; runner-level + host-dispatch tests stop failing on missing-runner errors but remain red because per-check modules are missing — that's the expected intermediate state.
- Tasks 5–12 each implement one check and turn its test class green:
  - Task 5: permissions
  - Task 6: hooks
  - Task 7: skills
  - Task 8: mcp
  - Task 9: auth (Anthropic / Bedrock)
  - Task 10: host_staging (new, runs always)
  - Task 11: github_auth (new Phase 1)
  - Task 12: ado_auth (relocated Phase 2)
- Task 13 confirms the whole file is green plus the wider gate.

### Known concerns (intentional trade-offs, not gaps)

1. **`skills` check is heuristic.** A skill that hides `AskUserQuestion` behind dynamic dispatch (e.g. `getattr(self, 'Ask' + 'UserQuestion')()`) evades the substring scan. Documented in `SKILL.md` under "Trade-offs". v2 may upgrade to AST-based scanning; v1 deliberately optimises for false-positive resistance by honouring `<!-- ralph-doctor: ignore -->` markers in markdown and `# noqa: ralph-doctor` lines in Python.
2. **`auth` probe uses `count_tokens`, not `messages`.** `count_tokens` requires a valid API key but does not consume token quota — the proof-of-auth property holds without burning credit. The trade-off is that some auth misconfigurations specific to the Messages endpoint (e.g. a key that has count-tokens scope but not messages scope) would slip through. In practice, Anthropic does not gate count_tokens separately from messages, so this is a theoretical concern only.
3. **`ado_auth` check bypasses `AdoClient.get` and uses `requests` directly.** `AdoClient.get` raises on 404 (per Plan 2), but the doctor needs 404-as-success. The check therefore re-implements the auth header construction (basic-auth with empty username and the PAT as password) inline. Documented in Task 12. The duplication is small (one `requests.get` call) and the alternative — adding a `raise_for_404=False` parameter to `AdoClient.get` — would push the special case into the wrong layer.
4. **`github_auth` uses a sentinel repo name (`test-permissions`) rather than a known-real repo.** The probe deliberately targets a repo that does not exist so the success criterion is "404 returned" — exactly the same shape as the ADO probe. The trade-off is that if a real `<owner>/test-permissions` repo ever exists, the 200/404 paths both pass (correctly — both prove auth + scopes). The check does not assume `test-permissions` is unique.
5. **`bedrock` probe uses the control-plane client, not bedrock-runtime.** Both clients use the same IAM credentials; `list_foundation_models` is the cheapest read operation that proves the role can authenticate. Documented in Task 9.
6. **`host_staging` inlines a minimal YAML-frontmatter parser.** Claude Code's frontmatter is a strict scalar-only subset of YAML, and `host_staging` only needs the `name:` field. Pulling in PyYAML for one field would be wrong; reusing `scripts/pbi_frontmatter.py` (if Plan 4's reconciliation landed) is preferred and called out in Task 10.

### Length

This plan is in the 1400–1700 line target band.
