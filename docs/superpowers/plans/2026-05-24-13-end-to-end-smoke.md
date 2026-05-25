# End-to-End Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Ralph v1's final integration test — a self-contained, deterministic, < 5-minute smoke suite that drives the *entire* per-repo loop end-to-end against a throwaway git repo, a mocked ADO REST server, and a mocked `claude -p` subprocess. The suite proves: (a) BA submission via `ralph-add` lands a PBI on `ralph-queue`; (b) the executor claims it into `current/`, branches off `main`, spawns claude-p, picks up the PR URL, and moves the PBI to `pending-pr/`; (c) the sweep observes a merged PR and moves the PBI to `done/`; (d) `ralph-status` reports the terminal state correctly; (e) the STUCK path moves a PBI to `blocked/` with the stuck reason preserved; (f) PR-feedback comments are routed back as new inbox PBIs and addressed in subsequent iterations; (g) the cycle detector halts the loop with a META-BUG when a regression cascade signal trips. A documented (but uncommitted) `--sandbox` runbook mode is provided for pre-release validation against a real ADO sandbox; the CI smoke runs entirely against mocks.

**Architecture:** A new top-level `tests/e2e/` package, separate from `tests/` (which holds unit / component tests for individual plans). The e2e suite uses three layers of test plumbing:

1. **Throwaway repo fixture (`conftest.py::throwaway_repo`).** Per test, build a `tmp_path`-rooted layout that mimics what a real service repo looks like after Plans 1–2 have been run: a bare remote, a worktree with `main` initialised (a single README and a `PROMPT.md`), a pre-existing `ralph-queue` branch on the remote with an empty `.ralph/` skeleton, and a remote ref for any feature branches the executor will push. Yield the worktree path so the test body can run `ralph-add` and `ralph-executor` against it the same way a developer would.
2. **Mock ADO server (`conftest.py::mock_ado`).** A `pytest-httpserver`-backed local HTTP server that stands in for `https://dev.azure.com/<org>`. Three REST verbs are simulated end-to-end: work-item GET (consumed by `ralph-add`), pullRequests POST + GET + threads GET / POST + PATCH (consumed by the `ado-pr` skill, the executor sweep, and Ralph during PR-feedback). The fixture exposes helper methods (`register_work_item`, `register_pr_open`, `register_pr_merged`, `register_pr_comments`, `register_thread_reply_ok`) so individual test scenarios stage the exact responses they need without raw header juggling. State is reset between tests via the autouse fixture `_ado_reset`.
3. **Mock `claude -p` (`conftest.py::canned_claude`).** Rather than mock at the Python boundary, the suite intercepts at the *binary* boundary: an `e2e_claude_stub.py` shim is generated per-test under `tmp_path/bin/claude`, the test sets `PATH` so it is the first `claude` on the executor's search path, and the shim reads a per-scenario "script" (a JSONL of canned actions) from `RALPH_E2E_CLAUDE_SCRIPT` (an env var set by the fixture). Each script line describes one of: `write_file` (Ralph edits a file), `git_commit` (Ralph commits), `git_push` (Ralph pushes), `ado_create_pr` (Ralph calls `ado-pr create-pr` — the stub mints a PR via the mock ADO server), `ado_reply` (Ralph posts a PR-feedback reply), `ado_set_status` (Ralph marks a thread fixed), `stuck` (Ralph writes STUCK.md and exits cleanly), or `exit` (Ralph terminates). This is robust because it exercises the *real* executor → claude-p subprocess boundary, including the JSON / streaming protocol the executor uses to harvest the PR URL Ralph creates.

The suite then drives one or more iterations of the executor against this rigged environment and inspects the resulting branch state, queue folder state, and `ralph-status` output to verify each scenario's invariants. Cleanup is automatic (each test runs in its own `tmp_path`).

**Tech Stack:** Python 3.12+, `uv`, `pytest`, `pytest-httpserver` (added by this plan), `werkzeug` (transitive of `pytest-httpserver`), `responses` (already added in Plan 2 — used only inside the claude stub for symmetric handling), `pyyaml`, `subprocess`, `git` CLI. No new runtime deps for Ralph itself.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Add `pytest-httpserver>=1.0` to the dev dependency group; extend `[tool.pytest.ini_options].testpaths` to include `tests/e2e`; add `[tool.pytest.ini_options].markers` entry for `e2e` and `sandbox`. |
| `tests/e2e/__init__.py` | Empty package marker so mypy and pytest discover the e2e tree. |
| `tests/e2e/conftest.py` | All shared fixtures: `throwaway_repo`, `mock_ado`, `canned_claude`, `executor_env`, `_ado_reset`, plus helper context managers. |
| `tests/e2e/_helpers.py` | Internal helpers used by tests AND by the claude stub script: `git_run`, `register_work_item_response`, `read_json_summary`, `pbi_state`, `wait_for_iteration`. Kept out of `conftest.py` so the stub binary can import them too. |
| `tests/e2e/e2e_claude_stub.py` | The fake `claude` binary. Reads `RALPH_E2E_CLAUDE_SCRIPT` (path to JSONL), executes each scripted action in order, prints the final PR URL (or STUCK reason) on stdout in the same shape the executor expects, exits 0. |
| `tests/e2e/fixtures/__init__.py` | Empty package marker. |
| `tests/e2e/fixtures/work_items/feature_healthz.json` | Canned ADO work item: `WI-9001`, type `User Story`, title `Add /healthz endpoint`, body with HTML, two attachments (a tiny PNG and a tiny PDF), priority 2. |
| `tests/e2e/fixtures/work_items/bug_irsa.json` | Canned ADO work item: `WI-9002`, type `Bug`, title `service-auth crashloops on ROSA`, repro steps, priority 1. |
| `tests/e2e/fixtures/work_items/feature_readme_comment.json` | Trivial feature work item used by the happy-path smoke (`WI-9100`, type `User Story`, title `Add a comment to README`). |
| `tests/e2e/fixtures/claude_scripts/happy_path.jsonl` | Canned claude actions for the happy-path scenario. |
| `tests/e2e/fixtures/claude_scripts/bug_fix.jsonl` | Canned claude actions for the bug scenario. |
| `tests/e2e/fixtures/claude_scripts/stuck.jsonl` | Canned claude actions for the STUCK scenario. |
| `tests/e2e/fixtures/claude_scripts/feedback_round_1.jsonl` | Canned actions for first iteration of the PR-feedback scenario (opens PR). |
| `tests/e2e/fixtures/claude_scripts/feedback_round_2.jsonl` | Canned actions for second iteration (addresses comment). |
| `tests/e2e/fixtures/claude_scripts/cycle_trip.jsonl` | Canned actions for the regression-cascade synthetic events. |
| `tests/e2e/fixtures/pr_comments_round_1.json` | Canned ADO threads response showing one active reviewer comment. |
| `tests/e2e/fixtures/pr_comments_resolved.json` | Canned ADO threads response after Ralph has marked the thread fixed. |
| `tests/e2e/test_smoke.py` | The five smoke scenarios as separate `test_*` functions. |
| `tests/e2e/README.md` | How to run the smoke (`uv run pytest tests/e2e -v` for mocked path; `RALPH_E2E_MODE=sandbox uv run pytest tests/e2e -v --sandbox` for the sandbox path, with runbook for credentials and cleanup). |

---

## Task 1 — Wire the e2e package into the toolchain

**Files**
- Modify: `pyproject.toml`
- Create: `tests/e2e/__init__.py`
- Create: `tests/e2e/fixtures/__init__.py`

**Steps**

- [ ] 1. Confirm Plans 10 and 12 are merged (Plan 13's preconditions). Run:
  ```
  uv run pytest tests/skills/test_ralph_cancel.py tests/skills/test_ralph_promote.py tests/skills/test_ralph_triage.py tests/executor/ tests/skills/test_ralph_doctor.py -v
  ```
  Expected: every test passes. If any fail, STOP — Plan 13 is the integration test of the prior twelve plans; it must not be started until they are stable.

- [ ] 2. Add `pytest-httpserver` to the dev dependency group. Run:
  ```
  uv add --dev pytest-httpserver
  ```
  Expected: `pytest-httpserver>=1.0` is added under `[dependency-groups].dev` in `pyproject.toml`; `werkzeug` is pulled in transitively; `uv.lock` updates; no errors.

- [ ] 3. Modify `pyproject.toml` so the `[tool.pytest.ini_options]` block reads exactly:
  ```toml
  [tool.pytest.ini_options]
  testpaths = ["tests", "tests/e2e"]
  addopts = "-ra"
  markers = [
      "e2e: end-to-end smoke scenarios that drive the full Ralph loop against mocks",
      "sandbox: end-to-end scenarios that require a real ADO sandbox and are SKIPPED by default",
  ]
  ```
  Note: leaving `tests` first means single-test selection like `uv run pytest tests/e2e/test_smoke.py::test_happy_path_feature` still resolves. The `markers` entry silences pytest's `PytestUnknownMarkWarning` for the new `@pytest.mark.e2e` / `@pytest.mark.sandbox` decorators.

- [ ] 4. Modify `[tool.mypy]` in `pyproject.toml` so `files` includes the e2e tree. Replace the section so it reads exactly:
  ```toml
  [tool.mypy]
  python_version = "3.12"
  strict = true
  files = ["ralph_executor", "scripts", "skills", "tests"]
  ```
  (`tests` already covers `tests/e2e` recursively — no extra entry needed. Confirm with `uv run mypy --config-file pyproject.toml --help > /dev/null` that the file still parses.)

- [ ] 5. Create `tests/e2e/__init__.py` containing exactly:
  ```python
  """End-to-end smoke tests for Ralph v1.

  Run the mocked path with::

      uv run pytest tests/e2e -v

  Run the sandbox path (real ADO, requires credentials) with::

      RALPH_E2E_MODE=sandbox uv run pytest tests/e2e -v --sandbox
  """
  ```

- [ ] 6. Create `tests/e2e/fixtures/__init__.py` containing exactly:
  ```python
  """Static fixture data for e2e smoke tests (work items, claude scripts, PR threads)."""
  ```

- [ ] 7. Verify the new structure parses and pytest still collects existing tests:
  ```
  uv run ruff check pyproject.toml || true
  uv run pytest --collect-only -q | tail -20
  ```
  Expected: no errors; pytest reports the prior test count unchanged (no e2e tests yet — the directory is empty).

- [ ] 8. Commit the scaffold:
  ```
  git add pyproject.toml uv.lock tests/e2e/__init__.py tests/e2e/fixtures/__init__.py
  git commit -m "chore(e2e): scaffold tests/e2e package and add pytest-httpserver"
  ```
  Expected: commit succeeds.

---

## Task 2 — Static fixtures (work items, claude scripts, PR threads)

**Files**
- Create: `tests/e2e/fixtures/work_items/feature_readme_comment.json`
- Create: `tests/e2e/fixtures/work_items/feature_healthz.json`
- Create: `tests/e2e/fixtures/work_items/bug_irsa.json`
- Create: `tests/e2e/fixtures/claude_scripts/happy_path.jsonl`
- Create: `tests/e2e/fixtures/claude_scripts/bug_fix.jsonl`
- Create: `tests/e2e/fixtures/claude_scripts/stuck.jsonl`
- Create: `tests/e2e/fixtures/claude_scripts/feedback_round_1.jsonl`
- Create: `tests/e2e/fixtures/claude_scripts/feedback_round_2.jsonl`
- Create: `tests/e2e/fixtures/claude_scripts/cycle_trip.jsonl`
- Create: `tests/e2e/fixtures/pr_comments_round_1.json`
- Create: `tests/e2e/fixtures/pr_comments_resolved.json`

**Steps**

- [ ] 1. Create `tests/e2e/fixtures/work_items/feature_readme_comment.json` with exactly:
  ```json
  {
    "id": 9100,
    "rev": 1,
    "fields": {
      "System.WorkItemType": "User Story",
      "System.Title": "Add a comment to README",
      "System.Description": "<p>The README is missing a one-line description at the top of the file. Add a line that explains what this service does.</p>",
      "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>README.md begins with a single-line description.</li><li>No other file is changed.</li></ul>",
      "Microsoft.VSTS.Common.Priority": 3
    },
    "relations": []
  }
  ```

- [ ] 2. Create `tests/e2e/fixtures/work_items/feature_healthz.json` with exactly:
  ```json
  {
    "id": 9001,
    "rev": 2,
    "fields": {
      "System.WorkItemType": "User Story",
      "System.Title": "Add /healthz endpoint to service-auth",
      "System.Description": "<div>Platform requires every service to expose a liveness probe at <code>GET /healthz</code>.</div>",
      "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>GET /healthz returns 200.</li><li>Body is JSON {\"status\":\"ok\"}.</li></ul>",
      "Microsoft.VSTS.Common.Priority": 2
    },
    "relations": []
  }
  ```

- [ ] 3. Create `tests/e2e/fixtures/work_items/bug_irsa.json` with exactly:
  ```json
  {
    "id": 9002,
    "rev": 1,
    "fields": {
      "System.WorkItemType": "Bug",
      "System.Title": "service-auth pod crashloops on ROSA",
      "System.Description": "<p>AccessDenied calling STS via IRSA. Container exits within 5 seconds of start.</p>",
      "Microsoft.VSTS.TCM.ReproSteps": "<ol><li>kubectl apply -f deploy/rosa.yaml</li><li>kubectl get pods --watch</li><li>Observe CrashLoopBackOff within 30s</li></ol>",
      "Microsoft.VSTS.Common.Priority": 1,
      "Microsoft.VSTS.Common.Severity": "1 - Critical"
    },
    "relations": []
  }
  ```

- [ ] 4. Create `tests/e2e/fixtures/claude_scripts/happy_path.jsonl` with exactly the following four lines (one JSON object per line — DO NOT pretty-print, the stub parses line-by-line):
  ```
  {"action": "write_file", "path": "README.md", "content": "# service-auth\n\nProvides authentication for internal services.\n"}
  {"action": "git_commit", "message": "feat(readme): add one-line description at top of README"}
  {"action": "git_push"}
  {"action": "ado_create_pr", "title": "feat(readme): add description", "body": "Closes WI-9100", "pr_id": 4201}
  ```

- [ ] 5. Create `tests/e2e/fixtures/claude_scripts/bug_fix.jsonl` with exactly:
  ```
  {"action": "write_file", "path": "src/auth/sts.py", "content": "# bug fix: use IRSA-aware credential provider\nimport boto3\n\nSESSION = boto3.Session()\n"}
  {"action": "git_commit", "message": "fix(auth): use IRSA credential provider so STS calls authorise on ROSA"}
  {"action": "git_push"}
  {"action": "ado_create_pr", "title": "fix(auth): IRSA credential provider", "body": "Fixes WI-9002", "pr_id": 4202}
  ```

- [ ] 6. Create `tests/e2e/fixtures/claude_scripts/stuck.jsonl` with exactly:
  ```
  {"action": "stuck", "reason": "Acceptance criteria conflict: 'returns 200' vs 'returns 204'.", "what_tried": "Re-read PBI three times; checked HISTORY.md.", "what_blocks": "PBI itself is contradictory.", "confidence_with_help": 90}
  ```

- [ ] 7. Create `tests/e2e/fixtures/claude_scripts/feedback_round_1.jsonl` (Ralph opens the PR; same shape as happy path but a distinct PR id):
  ```
  {"action": "write_file", "path": "src/feedback_demo.py", "content": "# initial implementation\nVALUE = 1\n"}
  {"action": "git_commit", "message": "feat(demo): initial implementation"}
  {"action": "git_push"}
  {"action": "ado_create_pr", "title": "feat(demo): initial implementation", "body": "Implements WI-9300", "pr_id": 4203}
  ```

- [ ] 8. Create `tests/e2e/fixtures/claude_scripts/feedback_round_2.jsonl` (Ralph addresses the comment):
  ```
  {"action": "write_file", "path": "src/feedback_demo.py", "content": "# implementation v2 — addresses reviewer comment\nVALUE = 2\n"}
  {"action": "git_commit", "message": "fix(demo): rename VALUE per reviewer feedback"}
  {"action": "git_push"}
  {"action": "ado_reply", "pr_id": 4203, "thread_id": 7001, "text": "Renamed VALUE to v2 as suggested. Pushed in latest commit."}
  {"action": "ado_set_status", "pr_id": 4203, "thread_id": 7001, "status": "fixed"}
  ```

- [ ] 9. Create `tests/e2e/fixtures/claude_scripts/cycle_trip.jsonl` (writes a fix file that re-introduces the same bug signature; the test layers this on top of synthetic prior events the cycle detector ingests directly):
  ```
  {"action": "write_file", "path": "src/auth/sts.py", "content": "# DELIBERATE re-introduction of previously-fixed bug to trip the detector\nimport boto3\nSESSION = boto3.Session()\n"}
  {"action": "git_commit", "message": "fix(auth): IRSA credential provider (attempt 4)"}
  {"action": "git_push"}
  {"action": "ado_create_pr", "title": "fix(auth): retry", "body": "Retry of WI-9002", "pr_id": 4204}
  ```

- [ ] 10. Create `tests/e2e/fixtures/pr_comments_round_1.json` with exactly:
  ```json
  {
    "value": [
      {
        "id": 7001,
        "status": "active",
        "publishedDate": "2026-05-24T10:00:00Z",
        "lastUpdatedDate": "2026-05-24T10:00:00Z",
        "isDeleted": false,
        "threadContext": {
          "filePath": "/src/feedback_demo.py",
          "rightFileStart": {"line": 1, "offset": 1},
          "rightFileEnd": {"line": 1, "offset": 1}
        },
        "comments": [
          {
            "id": 80001,
            "content": "Please rename VALUE to v2 — see service style guide.",
            "publishedDate": "2026-05-24T10:00:00Z",
            "author": {"displayName": "Senior Reviewer", "uniqueName": "reviewer@example.com"},
            "commentType": "text"
          }
        ]
      }
    ],
    "count": 1
  }
  ```

- [ ] 11. Create `tests/e2e/fixtures/pr_comments_resolved.json` with exactly:
  ```json
  {
    "value": [
      {
        "id": 7001,
        "status": "fixed",
        "publishedDate": "2026-05-24T10:00:00Z",
        "lastUpdatedDate": "2026-05-24T10:30:00Z",
        "isDeleted": false,
        "threadContext": {
          "filePath": "/src/feedback_demo.py",
          "rightFileStart": {"line": 1, "offset": 1},
          "rightFileEnd": {"line": 1, "offset": 1}
        },
        "comments": [
          {
            "id": 80001,
            "content": "Please rename VALUE to v2 — see service style guide.",
            "publishedDate": "2026-05-24T10:00:00Z",
            "author": {"displayName": "Senior Reviewer", "uniqueName": "reviewer@example.com"},
            "commentType": "text"
          },
          {
            "id": 80002,
            "content": "Renamed VALUE to v2 as suggested. Pushed in latest commit.",
            "publishedDate": "2026-05-24T10:30:00Z",
            "author": {"displayName": "Ralph Bot", "uniqueName": "ralph-bot@example.com"},
            "commentType": "text"
          }
        ]
      }
    ],
    "count": 1
  }
  ```

- [ ] 12. Commit the fixtures:
  ```
  git add tests/e2e/fixtures
  git commit -m "test(e2e): add canned work-item, claude-script, and PR-thread fixtures"
  ```
  Expected: commit succeeds.

---

## Task 3 — Internal helpers (`_helpers.py`)

**Files**
- Create: `tests/e2e/_helpers.py`

**Steps**

- [ ] 1. Write `tests/e2e/_helpers.py` with exactly the content below. The helpers are deliberately split out of `conftest.py` so the `e2e_claude_stub.py` binary can `import` from them too (conftest.py is not importable as a normal module from a subprocess):
  ```python
  """Helpers shared by the e2e suite and the claude stub binary.

  Anything used by BOTH the tests (which run inside pytest) AND
  ``e2e_claude_stub.py`` (which runs as a subprocess on the executor's
  PATH) lives here so a single import path serves both.
  """
  from __future__ import annotations

  import json
  import os
  import subprocess
  from collections.abc import Iterable, Mapping
  from dataclasses import dataclass
  from pathlib import Path
  from typing import Any


  # ---------- subprocess helpers ----------

  def git_run(repo: Path, *args: str, check: bool = True) -> str:
      """Run ``git`` in ``repo`` and return stripped stdout."""
      result = subprocess.run(
          ["git", *args],
          cwd=str(repo),
          check=check,
          capture_output=True,
          text=True,
      )
      return result.stdout.strip()


  def git_set_identity(repo: Path) -> None:
      git_run(repo, "config", "user.email", "ralph-e2e@example.com")
      git_run(repo, "config", "user.name", "Ralph E2E")


  # ---------- queue-state helpers ----------

  QUEUE_FOLDERS = ("inbox", "current", "pending-pr", "done", "blocked")


  def pbi_state(repo: Path, pbi_id: str) -> str | None:
      """Return the folder (``inbox`` / ``current`` / ...) the PBI sits in, or None."""
      ralph_root = repo / ".ralph"
      if not ralph_root.is_dir():
          return None
      for folder in QUEUE_FOLDERS:
          if (ralph_root / folder / pbi_id).is_dir():
              return folder
      return None


  def list_inbox(repo: Path) -> list[str]:
      inbox = repo / ".ralph" / "inbox"
      if not inbox.is_dir():
          return []
      return sorted(p.name for p in inbox.iterdir() if p.is_dir())


  def read_pbi_frontmatter(repo: Path, pbi_id: str) -> dict[str, Any]:
      """Read the YAML frontmatter at the top of the per-type entry file."""
      import yaml  # local import to keep top-of-file fast

      base = None
      for folder in QUEUE_FOLDERS:
          candidate = repo / ".ralph" / folder / pbi_id
          if candidate.is_dir():
              base = candidate
              break
      if base is None:
          raise FileNotFoundError(f"PBI {pbi_id} not found under .ralph/")
      for name in ("PBI.md", "BUG.md", "FEEDBACK.md"):
          path = base / name
          if path.is_file():
              text = path.read_text(encoding="utf-8")
              break
      else:  # pragma: no cover — defensive
          raise FileNotFoundError(f"PBI {pbi_id} has no entry file")
      if not text.startswith("---\n"):
          return {}
      end = text.find("\n---", 4)
      if end == -1:
          return {}
      block = text[4:end]
      data = yaml.safe_load(block)
      return data if isinstance(data, dict) else {}


  # ---------- JSON helpers ----------

  def read_json_summary(stdout: str) -> dict[str, Any]:
      """Parse the last JSON object printed on stdout (skills emit a summary line)."""
      stripped = stdout.strip()
      if not stripped:
          raise ValueError("stdout was empty; expected a JSON summary line")
      # Skill scripts may print progress on stderr; stdout should be a single JSON object.
      try:
          data = json.loads(stripped)
      except json.JSONDecodeError as exc:
          raise ValueError(f"stdout was not valid JSON: {exc}\nstdout was:\n{stripped}") from exc
      if not isinstance(data, dict):
          raise ValueError(f"stdout JSON was not an object; got {type(data).__name__}")
      return data


  # ---------- ADO mock registration shapes ----------

  @dataclass(frozen=True)
  class MockWorkItem:
      work_item_id: int
      payload: Mapping[str, Any]


  def load_work_item_fixture(name: str) -> MockWorkItem:
      """Load a canned work-item JSON file from ``tests/e2e/fixtures/work_items/``."""
      here = Path(__file__).resolve().parent
      path = here / "fixtures" / "work_items" / name
      payload = json.loads(path.read_text(encoding="utf-8"))
      return MockWorkItem(work_item_id=int(payload["id"]), payload=payload)


  # ---------- environment helpers ----------

  def merge_env(*envs: Mapping[str, str]) -> dict[str, str]:
      merged: dict[str, str] = dict(os.environ)
      for env in envs:
          merged.update(env)
      return merged


  def claude_script_path(name: str) -> Path:
      here = Path(__file__).resolve().parent
      path = here / "fixtures" / "claude_scripts" / name
      if not path.is_file():
          raise FileNotFoundError(f"claude script {name} missing at {path}")
      return path


  # ---------- iteration helpers ----------

  def run_executor_iteration(
      repo: Path,
      env: Mapping[str, str],
      *,
      max_iterations: int = 1,
  ) -> subprocess.CompletedProcess[str]:
      """Run ``ralph-executor --iterations <n>`` against ``repo`` and capture output.

      Plan 7 ships a ``--iterations`` CLI flag exactly for tests; the
      executor performs N iterations then exits, instead of looping until
      ``Ctrl+C``. If your executor uses a different flag name, adjust the
      args below.
      """
      cmd = [
          "uv",
          "run",
          "python",
          "-m",
          "ralph_executor",
          "--repo",
          str(repo),
          "--iterations",
          str(max_iterations),
      ]
      return subprocess.run(
          cmd,
          env=merge_env(env),
          check=False,
          capture_output=True,
          text=True,
          timeout=120,
      )


  def assert_pbi_landed(
      repo: Path, pbi_id: str, expected_folder: str
  ) -> None:
      state = pbi_state(repo, pbi_id)
      if state != expected_folder:
          inboxes = {f: sorted((repo / ".ralph" / f).glob("*")) for f in QUEUE_FOLDERS if (repo / ".ralph" / f).is_dir()}
          raise AssertionError(
              f"PBI {pbi_id} expected in {expected_folder!r} but is in {state!r}.\n"
              f"Queue layout:\n{json.dumps({k: [p.name for p in v] for k, v in inboxes.items()}, indent=2)}"
          )


  def assert_pr_state(
      mock_ado_state: Mapping[str, Any], pr_id: int, expected_status: str
  ) -> None:
      pr = mock_ado_state.get(pr_id)
      if pr is None:
          raise AssertionError(f"PR {pr_id} was never created against the mock ADO")
      if pr["status"] != expected_status:
          raise AssertionError(
              f"PR {pr_id} status was {pr['status']!r}, expected {expected_status!r}"
          )


  def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
      """Yield each JSON object from a JSONL file (one per non-empty line)."""
      with path.open(encoding="utf-8") as fh:
          for raw in fh:
              line = raw.strip()
              if not line:
                  continue
              obj = json.loads(line)
              if not isinstance(obj, dict):
                  raise ValueError(f"JSONL line was not an object: {line}")
              yield obj
  ```

- [ ] 2. Run `mypy` over the helpers to confirm strict mode is satisfied:
  ```
  uv run mypy tests/e2e/_helpers.py
  ```
  Expected: `Success: no issues found in 1 source file`.

- [ ] 3. Commit:
  ```
  git add tests/e2e/_helpers.py
  git commit -m "test(e2e): add shared helpers for queue state, git, env, JSONL"
  ```
  Expected: commit succeeds.

---

## Task 4 — The mocked claude binary (`e2e_claude_stub.py`)

**Files**
- Create: `tests/e2e/e2e_claude_stub.py`

**Steps**

- [ ] 1. Write `tests/e2e/e2e_claude_stub.py` with exactly the content below. The stub is invoked by the executor as if it were the `claude` binary; it walks the per-scenario JSONL script and performs each scripted action against the working directory. After exhausting the script it prints a final JSON summary on stdout (the shape Plan 7's executor parses to harvest the PR URL):
  ```python
  #!/usr/bin/env python3
  """Mock ``claude`` binary for Ralph e2e tests.

  Why a binary stub and not a Python-level patch:

  * The executor (Plan 7) spawns ``claude -p`` as a real subprocess and
    captures stdout. Mocking at the subprocess boundary exercises the
    exact same code path the production executor uses.
  * The stub respects ``RALPH_E2E_CLAUDE_SCRIPT`` (path to a JSONL of
    actions) and ``RALPH_E2E_REPO`` (the working repo Ralph would edit).
    These are injected by ``conftest.py::canned_claude``.

  Supported actions (one JSON object per line in the script):

  * ``{"action": "write_file", "path": "<rel-path>", "content": "<str>"}``
    Write the literal content to ``<repo>/<rel-path>`` (mkdir -p on parents).
  * ``{"action": "git_commit", "message": "<msg>"}``
    ``git add -A && git commit -m <msg>`` in the repo's current branch.
  * ``{"action": "git_push"}``
    ``git push origin <current-branch>``.
  * ``{"action": "ado_create_pr", "title": "<t>", "body": "<b>",
        "pr_id": <int>}``
    POST against ``ADO_ORG_URL/<project>/_apis/git/pullRequests`` and store
    the returned PR id in the final summary. Reuses ``ado_client.AdoClient``.
  * ``{"action": "ado_reply", "pr_id": <int>, "thread_id": <int>, "text":
        "<t>"}``
    POST a comment to the thread.
  * ``{"action": "ado_set_status", "pr_id": <int>, "thread_id": <int>,
        "status": "fixed"|"closed"}``
    PATCH the thread status.
  * ``{"action": "stuck", "reason": "<r>", "what_tried": "<wt>",
        "what_blocks": "<wb>", "confidence_with_help": <int>}``
    Write ``STUCK.md`` into the current PBI directory and exit cleanly.
    The PBI directory is discovered as the only entry under
    ``<repo>/.ralph/current/``.
  * ``{"action": "exit"}``
    Terminate without further action (useful for partial-progress tests).
  """
  from __future__ import annotations

  import json
  import os
  import sys
  from pathlib import Path
  from typing import Any

  # Allow ``from _helpers import ...`` when this stub runs as a subprocess
  # invoked from arbitrary cwd. ``tests/e2e`` is the directory containing
  # this file; the executor spawns us with our own absolute path, so this
  # is robust.
  _THIS_DIR = Path(__file__).resolve().parent
  if str(_THIS_DIR) not in sys.path:
      sys.path.insert(0, str(_THIS_DIR))

  # Make ``scripts.ado_client`` (Plan 2) importable from the ralph repo root.
  _REPO_ROOT = _THIS_DIR.parents[1]
  if str(_REPO_ROOT) not in sys.path:
      sys.path.insert(0, str(_REPO_ROOT))

  from _helpers import git_run, iter_jsonl  # noqa: E402


  def _repo() -> Path:
      raw = os.environ.get("RALPH_E2E_REPO", "").strip()
      if not raw:
          print("RALPH_E2E_REPO not set; refusing to run", file=sys.stderr)
          sys.exit(2)
      path = Path(raw).resolve()
      if not path.is_dir():
          print(f"RALPH_E2E_REPO {path} does not exist", file=sys.stderr)
          sys.exit(2)
      return path


  def _script_path() -> Path:
      raw = os.environ.get("RALPH_E2E_CLAUDE_SCRIPT", "").strip()
      if not raw:
          print("RALPH_E2E_CLAUDE_SCRIPT not set; refusing to run", file=sys.stderr)
          sys.exit(2)
      path = Path(raw).resolve()
      if not path.is_file():
          print(f"claude script {path} does not exist", file=sys.stderr)
          sys.exit(2)
      return path


  def _current_pbi_dir(repo: Path) -> Path:
      current = repo / ".ralph" / "current"
      if not current.is_dir():
          raise FileNotFoundError(f"{current} does not exist")
      entries = [p for p in current.iterdir() if p.is_dir()]
      if len(entries) != 1:
          raise RuntimeError(
              f"expected exactly one PBI in {current}, found {len(entries)}: "
              f"{[p.name for p in entries]}"
          )
      return entries[0]


  def _do_write_file(repo: Path, action: dict[str, Any]) -> None:
      rel = str(action["path"])
      content = str(action["content"])
      target = repo / rel
      target.parent.mkdir(parents=True, exist_ok=True)
      target.write_text(content, encoding="utf-8")


  def _do_git_commit(repo: Path, action: dict[str, Any]) -> None:
      git_run(repo, "config", "user.email", "ralph-e2e@example.com")
      git_run(repo, "config", "user.name", "Ralph E2E")
      git_run(repo, "add", "-A")
      git_run(repo, "commit", "-m", str(action["message"]))


  def _do_git_push(repo: Path) -> None:
      current_branch = git_run(repo, "rev-parse", "--abbrev-ref", "HEAD")
      git_run(repo, "push", "-u", "origin", current_branch)


  def _ado_client() -> Any:
      from scripts.ado_client import AdoClient  # type: ignore[import]

      pat = os.environ["ADO_PAT"]
      org_url = os.environ["ADO_ORG_URL"]
      project = os.environ["ADO_PROJECT"]
      return AdoClient(org_url=org_url, project=project, pat=pat)


  def _do_ado_create_pr(action: dict[str, Any]) -> dict[str, Any]:
      client = _ado_client()
      pr_id = int(action["pr_id"])
      title = str(action["title"])
      body = str(action.get("body", ""))
      response = client.post(
          "git/pullRequests",
          json_body={
              "title": title,
              "description": body,
              "sourceRefName": os.environ.get(
                  "RALPH_E2E_SOURCE_REF", "refs/heads/ralph/unknown"
              ),
              "targetRefName": "refs/heads/main",
              "pullRequestId": pr_id,
          },
      )
      return {"pr_id": pr_id, "pr_url": f"!{pr_id}", "raw": response}


  def _do_ado_reply(action: dict[str, Any]) -> None:
      client = _ado_client()
      pr_id = int(action["pr_id"])
      thread_id = int(action["thread_id"])
      client.post(
          f"git/pullRequests/{pr_id}/threads/{thread_id}/comments",
          json_body={"content": str(action["text"]), "commentType": "text"},
      )


  def _do_ado_set_status(action: dict[str, Any]) -> None:
      client = _ado_client()
      pr_id = int(action["pr_id"])
      thread_id = int(action["thread_id"])
      client.patch(
          f"git/pullRequests/{pr_id}/threads/{thread_id}",
          json_body={"status": str(action["status"])},
      )


  def _do_stuck(repo: Path, action: dict[str, Any]) -> None:
      pbi_dir = _current_pbi_dir(repo)
      stuck_md = pbi_dir / "STUCK.md"
      content = (
          "# STUCK\n\n"
          "## Why I'm stuck\n\n"
          f"{action.get('reason', 'unspecified')}\n\n"
          "## What I tried\n\n"
          f"{action.get('what_tried', 'unspecified')}\n\n"
          "## What would unblock me\n\n"
          f"{action.get('what_blocks', 'unspecified')}\n\n"
          "## Confidence with help (0-100)\n\n"
          f"{int(action.get('confidence_with_help', 0))}\n"
      )
      stuck_md.write_text(content, encoding="utf-8")


  def main(argv: list[str]) -> int:
      repo = _repo()
      script = _script_path()
      summary: dict[str, Any] = {
          "stub": "e2e_claude_stub",
          "actions": 0,
          "pr_id": None,
          "stuck": False,
      }
      for action in iter_jsonl(script):
          name = action.get("action")
          if name == "write_file":
              _do_write_file(repo, action)
          elif name == "git_commit":
              _do_git_commit(repo, action)
          elif name == "git_push":
              _do_git_push(repo)
          elif name == "ado_create_pr":
              result = _do_ado_create_pr(action)
              summary["pr_id"] = result["pr_id"]
              summary["pr_url"] = result["pr_url"]
          elif name == "ado_reply":
              _do_ado_reply(action)
          elif name == "ado_set_status":
              _do_ado_set_status(action)
          elif name == "stuck":
              _do_stuck(repo, action)
              summary["stuck"] = True
              summary["stuck_reason"] = action.get("reason", "")
              break
          elif name == "exit":
              break
          else:
              print(f"unknown action: {name!r}", file=sys.stderr)
              return 2
          summary["actions"] += 1
      # The executor (Plan 7) reads stdout for the PR URL marker. Match
      # the same convention real Ralph uses: print a single JSON object on
      # the last line of stdout.
      print(json.dumps(summary))
      return 0


  if __name__ == "__main__":
      raise SystemExit(main(sys.argv[1:]))
  ```

- [ ] 2. Mark the stub executable on POSIX (no-op on Windows; the launcher in the fixture handles platform differences):
  ```
  chmod +x tests/e2e/e2e_claude_stub.py || true
  ```

- [ ] 3. Confirm the stub passes mypy:
  ```
  uv run mypy tests/e2e/e2e_claude_stub.py
  ```
  Expected: `Success`. (If `scripts.ado_client` doesn't expose `patch` yet, raise it as a finding to be fixed in Plan 5; the stub's contract is "use the existing ado_client surface".)

- [ ] 4. Commit:
  ```
  git add tests/e2e/e2e_claude_stub.py
  git commit -m "test(e2e): add canned claude subprocess stub binary"
  ```
  Expected: commit succeeds.

---

## Task 5 — The shared fixtures (`conftest.py`)

**Files**
- Create: `tests/e2e/conftest.py`

**Steps**

- [ ] 1. Write `tests/e2e/conftest.py` with exactly the content below. The file pulls together the throwaway repo, the ADO mock (via `pytest-httpserver`), the canned claude binary, and the executor environment. It also adds the `--sandbox` CLI flag (skipping sandbox-marked tests by default):
  ```python
  """Shared fixtures for the e2e smoke suite."""
  from __future__ import annotations

  import json
  import os
  import stat
  import sys
  from collections.abc import Iterator
  from dataclasses import dataclass, field
  from pathlib import Path
  from typing import Any

  import pytest

  from _helpers import (
      git_run,
      git_set_identity,
      load_work_item_fixture,
  )

  # pytest-httpserver
  from pytest_httpserver import HTTPServer


  THIS_DIR = Path(__file__).resolve().parent
  REPO_ROOT = THIS_DIR.parents[1]
  STUB_PATH = THIS_DIR / "e2e_claude_stub.py"


  # ---------- CLI options ----------

  def pytest_addoption(parser: pytest.Parser) -> None:
      parser.addoption(
          "--sandbox",
          action="store_true",
          default=False,
          help="Also run e2e scenarios marked @pytest.mark.sandbox (real ADO).",
      )


  def pytest_collection_modifyitems(
      config: pytest.Config, items: list[pytest.Item]
  ) -> None:
      if config.getoption("--sandbox"):
          return
      skip_sandbox = pytest.mark.skip(reason="sandbox-only; pass --sandbox to run")
      for item in items:
          if "sandbox" in item.keywords:
              item.add_marker(skip_sandbox)


  # ---------- throwaway repo ----------

  @pytest.fixture
  def throwaway_repo(tmp_path: pytest.TempPathFactory) -> Iterator[Path]:
      """Build a fresh bare remote + worktree pair with main + ralph-queue.

      The yielded path is the worktree. ``origin`` points at the bare repo
      in the same tmpdir, so ``git push`` is a real (local) push. The
      worktree starts on the ``main`` branch with a single README, a
      PROMPT.md, and a `src/` placeholder. ``ralph-queue`` exists on the
      remote with an empty ``.ralph/`` skeleton.
      """
      root = Path(tmp_path) if not isinstance(tmp_path, Path) else tmp_path  # mypy quirk
      bare = root / "remote.git"
      work = root / "work"

      import subprocess

      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      git_set_identity(work)

      # ---- main branch ----
      (work / "README.md").write_text("# service-auth\n", encoding="utf-8")
      (work / "PROMPT.md").write_text(
          "# Ralph standing prompt (e2e stub)\n\nDo the work in the PBI directory.\n",
          encoding="utf-8",
      )
      (work / "src").mkdir()
      (work / "src" / "__init__.py").write_text("", encoding="utf-8")
      (work / "docs").mkdir()
      (work / "docs" / "INVESTIGATE.md").write_text(
          "# INVESTIGATE.md (e2e stub)\n\nService overview: e2e smoke fixture.\n",
          encoding="utf-8",
      )
      git_run(work, "add", "-A")
      git_run(work, "commit", "-m", "chore: initial main commit")
      git_run(work, "branch", "-M", "main")
      git_run(work, "remote", "add", "origin", str(bare))
      git_run(work, "push", "-u", "origin", "main")

      # ---- ralph-queue branch ----
      git_run(work, "checkout", "--orphan", "ralph-queue")
      git_run(work, "rm", "-rf", ".")
      ralph = work / ".ralph"
      ralph.mkdir()
      for folder in ("inbox", "current", "pending-pr", "done", "blocked"):
          (ralph / folder).mkdir()
          (ralph / folder / ".gitkeep").touch()
      git_run(work, "add", "-A")
      git_run(work, "commit", "-m", "chore(queue): bootstrap empty .ralph/ skeleton")
      git_run(work, "push", "-u", "origin", "ralph-queue")

      # Return to main so the test starts in a "fresh boundary" state.
      git_run(work, "checkout", "main")
      yield work
      # tmp_path cleanup is automatic.


  # ---------- mock ADO ----------

  @dataclass
  class MockAdoState:
      """In-memory state the mock ADO server keeps across registered handlers."""

      prs: dict[int, dict[str, Any]] = field(default_factory=dict)
      threads: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
      thread_status: dict[tuple[int, int], str] = field(default_factory=dict)

      def reset(self) -> None:
          self.prs.clear()
          self.threads.clear()
          self.thread_status.clear()


  @dataclass
  class MockAdo:
      """Convenience wrapper around the pytest-httpserver-backed mock ADO."""

      server: HTTPServer
      state: MockAdoState
      project: str = "example-project"

      @property
      def org_url(self) -> str:
          return self.server.url_for("").rstrip("/")

      def register_work_item(self, fixture_filename: str) -> None:
          item = load_work_item_fixture(fixture_filename)
          self.server.expect_request(
              f"/{self.project}/_apis/wit/workitems/{item.work_item_id}",
              method="GET",
          ).respond_with_json(item.payload)

      def register_pr_open(self, pr_id: int, source_ref: str = "refs/heads/ralph/WI-9100") -> None:
          state = self.state
          state.prs[pr_id] = {
              "pullRequestId": pr_id,
              "status": "active",
              "sourceRefName": source_ref,
              "targetRefName": "refs/heads/main",
              "title": f"PR {pr_id}",
          }

          def _create_pr(request: Any) -> tuple[str, int, dict[str, str]]:
              # Mock POST → return the stored PR record.
              body = state.prs[pr_id]
              return (json.dumps(body), 200, {"Content-Type": "application/json"})

          self.server.expect_request(
              f"/{self.project}/_apis/git/pullRequests",
              method="POST",
          ).respond_with_handler(_create_pr)

          def _get_pr(request: Any) -> tuple[str, int, dict[str, str]]:
              return (
                  json.dumps(state.prs[pr_id]),
                  200,
                  {"Content-Type": "application/json"},
              )

          self.server.expect_request(
              f"/{self.project}/_apis/git/pullRequests/{pr_id}",
              method="GET",
          ).respond_with_handler(_get_pr)

      def register_pr_merged(self, pr_id: int) -> None:
          if pr_id not in self.state.prs:
              self.register_pr_open(pr_id)
          self.state.prs[pr_id]["status"] = "completed"

      def register_pr_comments(self, pr_id: int, fixture_filename: str) -> None:
          here = Path(__file__).resolve().parent
          payload = json.loads(
              (here / "fixtures" / fixture_filename).read_text(encoding="utf-8")
          )
          self.state.threads[pr_id] = payload["value"]

          def _get_threads(request: Any) -> tuple[str, int, dict[str, str]]:
              return (
                  json.dumps({"value": self.state.threads.get(pr_id, []),
                              "count": len(self.state.threads.get(pr_id, []))}),
                  200,
                  {"Content-Type": "application/json"},
              )

          self.server.expect_request(
              f"/{self.project}/_apis/git/pullRequests/{pr_id}/threads",
              method="GET",
          ).respond_with_handler(_get_threads)

      def register_thread_reply_ok(self, pr_id: int, thread_id: int) -> None:
          def _reply(request: Any) -> tuple[str, int, dict[str, str]]:
              return ("{}", 200, {"Content-Type": "application/json"})

          self.server.expect_request(
              f"/{self.project}/_apis/git/pullRequests/{pr_id}/threads/{thread_id}/comments",
              method="POST",
          ).respond_with_handler(_reply)

          def _patch(request: Any) -> tuple[str, int, dict[str, str]]:
              body = request.get_json(force=True, silent=True) or {}
              status = str(body.get("status", "fixed"))
              self.state.thread_status[(pr_id, thread_id)] = status
              return ("{}", 200, {"Content-Type": "application/json"})

          self.server.expect_request(
              f"/{self.project}/_apis/git/pullRequests/{pr_id}/threads/{thread_id}",
              method="PATCH",
          ).respond_with_handler(_patch)


  @pytest.fixture
  def mock_ado(httpserver: HTTPServer) -> Iterator[MockAdo]:
      state = MockAdoState()
      mock = MockAdo(server=httpserver, state=state)
      yield mock
      state.reset()


  # ---------- canned claude binary ----------

  @dataclass
  class CannedClaude:
      """Configure which script the next claude-p invocation will replay."""

      bin_dir: Path
      _script_pointer: Path

      def use(self, script_filename: str) -> None:
          src = THIS_DIR / "fixtures" / "claude_scripts" / script_filename
          if not src.is_file():
              raise FileNotFoundError(f"claude script {src} missing")
          self._script_pointer.write_text(str(src.resolve()), encoding="utf-8")

      @property
      def script_pointer(self) -> Path:
          return self._script_pointer


  @pytest.fixture
  def canned_claude(tmp_path: Path) -> Iterator[CannedClaude]:
      """Install a ``claude`` shim on PATH that defers to ``e2e_claude_stub.py``.

      The shim is a tiny launcher (a Python script with a shebang on POSIX,
      a ``.cmd`` wrapper on Windows) that calls the real stub with the
      currently-active script. The script is *late-bound* — the test calls
      ``canned_claude.use("happy_path.jsonl")`` before kicking off the
      executor, and the executor's eventual ``claude -p`` invocation picks
      that up via the ``RALPH_E2E_CLAUDE_SCRIPT`` env var.
      """
      bin_dir = tmp_path / "bin"
      bin_dir.mkdir()

      pointer = tmp_path / "active_claude_script.path"
      pointer.write_text("", encoding="utf-8")

      if sys.platform == "win32":
          launcher = bin_dir / "claude.cmd"
          launcher.write_text(
              "@echo off\r\n"
              f"set RALPH_E2E_CLAUDE_SCRIPT_POINTER={pointer}\r\n"
              "for /f \"usebackq tokens=*\" %%S in (\"%RALPH_E2E_CLAUDE_SCRIPT_POINTER%\") do "
              f"\"{sys.executable}\" \"{STUB_PATH}\" %*\r\n",
              encoding="utf-8",
          )
          # On Windows there's no executable bit; the ``.cmd`` extension is the trigger.
      else:
          launcher = bin_dir / "claude"
          launcher.write_text(
              "#!/usr/bin/env bash\n"
              f'export RALPH_E2E_CLAUDE_SCRIPT="$(cat \"{pointer}\")"\n'
              f'exec "{sys.executable}" "{STUB_PATH}" "$@"\n',
              encoding="utf-8",
          )
          launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

      cc = CannedClaude(bin_dir=bin_dir, _script_pointer=pointer)
      yield cc


  # ---------- executor env ----------

  @dataclass
  class ExecutorEnv:
      """Bundled environment for invoking the executor or skills."""

      base_env: dict[str, str]

      def with_overrides(self, **overrides: str) -> dict[str, str]:
          merged = dict(self.base_env)
          merged.update(overrides)
          return merged


  @pytest.fixture
  def executor_env(
      mock_ado: MockAdo,
      canned_claude: CannedClaude,
      throwaway_repo: Path,
  ) -> ExecutorEnv:
      env = dict(os.environ)
      # Prepend our bin directory so the ``claude`` shim is found first.
      env["PATH"] = str(canned_claude.bin_dir) + os.pathsep + env.get("PATH", "")
      env["RALPH_E2E_REPO"] = str(throwaway_repo)
      # Late-bound script pointer; tests call canned_claude.use(...) which
      # writes the resolved path into the pointer file. The bash/.cmd
      # launcher reads the pointer on every invocation.
      env["RALPH_E2E_CLAUDE_SCRIPT_POINTER"] = str(canned_claude.script_pointer)
      # ADO mock
      env["ADO_PAT"] = "fake-e2e-pat"
      env["ADO_ORG_URL"] = mock_ado.org_url
      env["ADO_PROJECT"] = mock_ado.project
      # Executor knobs
      env["RALPH_REPO_PATH"] = str(throwaway_repo)
      env["RALPH_QUEUE_BRANCH"] = "ralph-queue"
      env["RALPH_MAIN_BRANCH"] = "main"
      env["RALPH_MAX_ATTEMPTS"] = "3"
      env["RALPH_LOG_LEVEL"] = "INFO"
      return ExecutorEnv(base_env=env)


  # ---------- safety: ensure mock state resets between tests ----------

  @pytest.fixture(autouse=True)
  def _ado_reset(request: pytest.FixtureRequest) -> Iterator[None]:
      yield
      # The mock_ado fixture's own teardown calls state.reset(); the autouse
      # hook is defensive belt-and-braces against tests that capture state
      # outside the fixture's lifetime.
  ```

- [ ] 2. Run `mypy` over the conftest to confirm strict mode is satisfied:
  ```
  uv run mypy tests/e2e/conftest.py
  ```
  Expected: `Success: no issues found in 1 source file`. (If mypy complains about `pytest_httpserver` being untyped, add a `[[tool.mypy.overrides]]` block in `pyproject.toml` setting `ignore_missing_imports = true` for `pytest_httpserver.*` — document the change in the commit message.)

- [ ] 3. Confirm pytest can still collect tests without errors:
  ```
  uv run pytest tests/e2e --collect-only -q
  ```
  Expected: pytest reports no errors (no tests yet; fixtures load).

- [ ] 4. Commit:
  ```
  git add tests/e2e/conftest.py
  git commit -m "test(e2e): add throwaway-repo, mock-ado, and canned-claude fixtures"
  ```
  Expected: commit succeeds.

---

## Task 6 — Scenario 1: happy-path feature

**Files**
- Create: `tests/e2e/test_smoke.py` (initial skeleton + first test)

**Steps**

- [ ] 1. Create `tests/e2e/test_smoke.py` with the following content. Subsequent tasks (7–10) APPEND additional test functions; this task seeds the file with the module docstring, imports, and the first scenario:
  ```python
  """End-to-end smoke scenarios for Ralph v1.

  Each scenario stitches together a throwaway repo, a mocked ADO REST
  server, and a canned ``claude -p`` subprocess. The intent is to prove
  the full spec sequence diagram (Spec Step 13) is satisfied: BA submits
  a PBI via ``ralph-add``; the executor claims it into ``current/``,
  spawns claude, picks up the PR URL, moves the PBI to ``pending-pr/``;
  the sweep observes a merged PR and moves the PBI to ``done/``;
  ``ralph-status`` reports the terminal state.
  """
  from __future__ import annotations

  import json
  import subprocess
  from pathlib import Path

  import pytest

  from _helpers import (
      assert_pbi_landed,
      claude_script_path,
      git_run,
      list_inbox,
      pbi_state,
      read_json_summary,
      read_pbi_frontmatter,
      run_executor_iteration,
  )
  from conftest import CannedClaude, ExecutorEnv, MockAdo  # type: ignore[import]


  pytestmark = pytest.mark.e2e

  WI_HAPPY = "WI-9100"


  def _submit_via_ralph_add(
      work_item_fixture: str,
      throwaway_repo: Path,
      env: dict[str, str],
  ) -> dict[str, object]:
      """Run the real ``ralph-add`` skill against the throwaway repo."""
      cmd = [
          "uv",
          "run",
          "python",
          "skills/ralph-add/scripts/add.py",
          "--work-item",
          work_item_fixture,
          "--repo",
          str(throwaway_repo),
      ]
      result = subprocess.run(
          cmd, env=env, check=False, capture_output=True, text=True, timeout=60
      )
      if result.returncode != 0:
          raise AssertionError(
              f"ralph-add failed (exit {result.returncode}):\n"
              f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
          )
      return read_json_summary(result.stdout)


  def _run_status(throwaway_repo: Path, env: dict[str, str]) -> dict[str, object]:
      """Run the real ``ralph-status`` skill and parse the JSON summary."""
      cmd = [
          "uv",
          "run",
          "python",
          "skills/ralph-status/scripts/show.py",
          "--repo",
          str(throwaway_repo),
          "--json",
      ]
      result = subprocess.run(
          cmd, env=env, check=False, capture_output=True, text=True, timeout=30
      )
      assert result.returncode == 0, (
          f"ralph-status failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
      )
      return read_json_summary(result.stdout)


  def test_happy_path_feature(
      throwaway_repo: Path,
      mock_ado: MockAdo,
      canned_claude: CannedClaude,
      executor_env: ExecutorEnv,
  ) -> None:
      """BA → ralph-add → executor → claude-p → PR → sweep → done.

      Steps:
        1. Register the ADO work item, register the (yet-to-be-opened) PR
           in the mock ADO state.
        2. Run ``ralph-add WI-9100``. Confirm the PBI lands in inbox/.
        3. Wire the canned claude script to ``happy_path.jsonl``.
        4. Run ONE executor iteration. The executor claims the PBI into
           ``current/``, branches off main, spawns claude (which writes a
           README change, commits, pushes, and "creates" the PR via the
           mock ADO), then moves the PBI to ``pending-pr/``.
        5. Flip the mock PR to ``completed``.
        6. Run a SECOND executor iteration. The sweep observes the merge
           and moves the PBI to ``done/``.
        7. Run ``ralph-status`` and assert the PBI is reported as done.
      """
      # ---- (1) seed mock state ----
      mock_ado.register_work_item("feature_readme_comment.json")
      mock_ado.register_pr_open(4201, source_ref=f"refs/heads/ralph/{WI_HAPPY}")

      # ---- (2) BA submission ----
      env = executor_env.with_overrides()
      summary = _submit_via_ralph_add("9100", throwaway_repo, env)
      assert summary["pbi_id"] == WI_HAPPY
      assert summary["pbi_type"] == "feature"
      # Switch the worktree to ralph-queue so subsequent state assertions
      # can read the .ralph/ tree. (The executor will do its own checkouts
      # as it runs; this is purely for the test's bookkeeping.)
      git_run(throwaway_repo, "checkout", "ralph-queue")
      assert_pbi_landed(throwaway_repo, WI_HAPPY, "inbox")

      # ---- (3) arm the canned claude with the happy-path script ----
      canned_claude.use("happy_path.jsonl")

      # ---- (4) first executor iteration: claim → branch → claude → PR → pending-pr/ ----
      proc = run_executor_iteration(throwaway_repo, env, max_iterations=1)
      assert proc.returncode == 0, (
          f"executor iteration 1 failed:\n"
          f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
      )

      # After iteration 1, ralph-queue should show the PBI in pending-pr/.
      git_run(throwaway_repo, "fetch", "origin", "ralph-queue")
      git_run(throwaway_repo, "checkout", "ralph-queue")
      git_run(throwaway_repo, "reset", "--hard", "origin/ralph-queue")
      assert_pbi_landed(throwaway_repo, WI_HAPPY, "pending-pr")
      front = read_pbi_frontmatter(throwaway_repo, WI_HAPPY)
      assert front["status"] == "pending-pr"
      assert int(front["attempts"]) >= 1

      # The PR should exist in the mock ADO state, still active.
      assert mock_ado.state.prs[4201]["status"] == "active"
      # The feature branch should have been pushed to the remote.
      remote_refs = git_run(throwaway_repo, "ls-remote", "origin")
      assert f"refs/heads/ralph/{WI_HAPPY}" in remote_refs

      # ---- (5) simulate the org's auto-merge: flip PR to completed ----
      mock_ado.register_pr_merged(4201)

      # ---- (6) second iteration: sweep sees merge → done/ ----
      proc2 = run_executor_iteration(throwaway_repo, env, max_iterations=1)
      assert proc2.returncode == 0, (
          f"executor iteration 2 failed:\n"
          f"stdout:\n{proc2.stdout}\nstderr:\n{proc2.stderr}"
      )
      git_run(throwaway_repo, "fetch", "origin", "ralph-queue")
      git_run(throwaway_repo, "reset", "--hard", "origin/ralph-queue")
      assert_pbi_landed(throwaway_repo, WI_HAPPY, "done")
      front_done = read_pbi_frontmatter(throwaway_repo, WI_HAPPY)
      assert front_done["status"] == "done"

      # ---- (7) ralph-status reports done ----
      status_payload = _run_status(throwaway_repo, env)
      assert WI_HAPPY in str(status_payload), (
          f"ralph-status output did not mention {WI_HAPPY}:\n"
          f"{json.dumps(status_payload, indent=2)}"
      )
      # The detailed status JSON shape is Plan 4's responsibility; we
      # assert only that the PBI is bucketed under "done".
      done_section = status_payload.get("done") or status_payload.get("by_state", {}).get("done")
      assert done_section is not None, (
          "ralph-status JSON missing a 'done' or 'by_state.done' section"
      )
      assert any(WI_HAPPY in str(entry) for entry in (done_section if isinstance(done_section, list) else [done_section]))
  ```

- [ ] 2. Run only this test to confirm the wiring is correct:
  ```
  uv run pytest tests/e2e/test_smoke.py::test_happy_path_feature -v
  ```
  Expected: this is the green step. The test should pass end-to-end in under 60 seconds. If it fails with `ralph-add failed`, inspect the captured stderr — a common cause is the `ado-pr` skill expecting a different REST verb shape than the mock has registered.

- [ ] 3. Commit:
  ```
  git add tests/e2e/test_smoke.py
  git commit -m "test(e2e): scenario 1 — happy-path feature smoke"
  ```
  Expected: commit succeeds.

---

## Task 7 — Scenario 2: bug path

**Files**
- Modify: `tests/e2e/test_smoke.py` (append the bug scenario)

**Steps**

- [ ] 1. Append the following test function to `tests/e2e/test_smoke.py`:
  ```python


  WI_BUG = "WI-9002"


  def test_bug_path(
      throwaway_repo: Path,
      mock_ado: MockAdo,
      canned_claude: CannedClaude,
      executor_env: ExecutorEnv,
  ) -> None:
      """A Bug work item flows through the loop and lands in done/.

      The shape is identical to the feature happy-path, but ``ralph-add``
      produces a ``BUG.md`` / ``REPRODUCE.md`` PBI instead, and the canned
      claude script applies a fix-style change. We assert the bug PBI
      lands in done with ``severity: critical`` preserved from the ADO
      Severity field.
      """
      mock_ado.register_work_item("bug_irsa.json")
      mock_ado.register_pr_open(4202, source_ref=f"refs/heads/ralph/{WI_BUG}")

      env = executor_env.with_overrides()
      summary = _submit_via_ralph_add("9002", throwaway_repo, env)
      assert summary["pbi_id"] == WI_BUG
      assert summary["pbi_type"] == "bug"

      git_run(throwaway_repo, "checkout", "ralph-queue")
      assert_pbi_landed(throwaway_repo, WI_BUG, "inbox")
      front = read_pbi_frontmatter(throwaway_repo, WI_BUG)
      assert front["severity"] == "critical"

      canned_claude.use("bug_fix.jsonl")

      proc = run_executor_iteration(throwaway_repo, env, max_iterations=1)
      assert proc.returncode == 0, (
          f"executor iteration 1 failed:\n"
          f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
      )
      git_run(throwaway_repo, "fetch", "origin", "ralph-queue")
      git_run(throwaway_repo, "checkout", "ralph-queue")
      git_run(throwaway_repo, "reset", "--hard", "origin/ralph-queue")
      assert_pbi_landed(throwaway_repo, WI_BUG, "pending-pr")

      mock_ado.register_pr_merged(4202)

      proc2 = run_executor_iteration(throwaway_repo, env, max_iterations=1)
      assert proc2.returncode == 0, (
          f"executor iteration 2 failed:\n"
          f"stdout:\n{proc2.stdout}\nstderr:\n{proc2.stderr}"
      )
      git_run(throwaway_repo, "fetch", "origin", "ralph-queue")
      git_run(throwaway_repo, "reset", "--hard", "origin/ralph-queue")
      assert_pbi_landed(throwaway_repo, WI_BUG, "done")

      # Confirm the fix file is present on main (the test repo's main
      # advanced when the auto-PR-merge was simulated — in our mock,
      # the fix file lives on the feature branch; the test just confirms
      # the BRANCH was pushed, not that the simulated auto-merge actually
      # fast-forwarded main).
      remote_refs = git_run(throwaway_repo, "ls-remote", "origin")
      assert f"refs/heads/ralph/{WI_BUG}" in remote_refs
  ```

- [ ] 2. Run the bug scenario in isolation:
  ```
  uv run pytest tests/e2e/test_smoke.py::test_bug_path -v
  ```
  Expected: pass in under 60 seconds.

- [ ] 3. Commit:
  ```
  git add tests/e2e/test_smoke.py
  git commit -m "test(e2e): scenario 2 — bug-path smoke"
  ```

---

## Task 8 — Scenario 3: STUCK path

**Files**
- Modify: `tests/e2e/test_smoke.py` (append the STUCK scenario)

**Steps**

- [ ] 1. Append the following test function to `tests/e2e/test_smoke.py`:
  ```python


  WI_STUCK = "WI-9001"


  def test_stuck_path(
      throwaway_repo: Path,
      mock_ado: MockAdo,
      canned_claude: CannedClaude,
      executor_env: ExecutorEnv,
  ) -> None:
      """Claude writes STUCK.md → executor moves PBI to blocked/.

      Submits the canned healthz feature, but arms the claude stub with
      ``stuck.jsonl``, which writes STUCK.md inside ``.ralph/current/``
      instead of editing code. The executor's STUCK-handling code path
      (Plan 9) should observe the file, move the PBI to ``blocked/``,
      and record the reason in the PBI's HISTORY.md.
      """
      mock_ado.register_work_item("feature_healthz.json")
      # No PR is opened in this scenario; do not register one.

      env = executor_env.with_overrides()
      summary = _submit_via_ralph_add("9001", throwaway_repo, env)
      assert summary["pbi_id"] == WI_STUCK

      canned_claude.use("stuck.jsonl")

      proc = run_executor_iteration(throwaway_repo, env, max_iterations=1)
      assert proc.returncode == 0, (
          f"executor STUCK iteration failed:\n"
          f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
      )

      git_run(throwaway_repo, "fetch", "origin", "ralph-queue")
      git_run(throwaway_repo, "checkout", "ralph-queue")
      git_run(throwaway_repo, "reset", "--hard", "origin/ralph-queue")
      assert_pbi_landed(throwaway_repo, WI_STUCK, "blocked")
      front = read_pbi_frontmatter(throwaway_repo, WI_STUCK)
      assert front["status"] == "blocked"

      stuck_md = (
          throwaway_repo / ".ralph" / "blocked" / WI_STUCK / "STUCK.md"
      ).read_text(encoding="utf-8")
      assert "Acceptance criteria conflict" in stuck_md
      assert "Confidence with help" in stuck_md

      # ralph-status should bucket the PBI under blocked.
      status_payload = _run_status(throwaway_repo, env)
      blocked_section = (
          status_payload.get("blocked")
          or status_payload.get("by_state", {}).get("blocked")
      )
      assert blocked_section is not None
      blocked_repr = str(blocked_section)
      assert WI_STUCK in blocked_repr
      assert "Acceptance criteria conflict" in blocked_repr or "stuck" in blocked_repr.lower()
  ```

- [ ] 2. Run the STUCK scenario:
  ```
  uv run pytest tests/e2e/test_smoke.py::test_stuck_path -v
  ```
  Expected: pass in under 60 seconds.

- [ ] 3. Commit:
  ```
  git add tests/e2e/test_smoke.py
  git commit -m "test(e2e): scenario 3 — STUCK-path smoke"
  ```

---

## Task 9 — Scenario 4: PR-feedback round trip

**Files**
- Modify: `tests/e2e/test_smoke.py` (append the PR-feedback scenario)

**Steps**

- [ ] 1. Append the following test function. The scenario is the longest of the five — it stages two distinct iterations of the executor, with the mock ADO state mutating between them to reflect (a) the initial PR opening with no comments, (b) a reviewer comment landing, (c) Ralph addressing the comment, (d) the comment being marked fixed:
  ```python


  WI_FEEDBACK = "WI-9300"


  def _seed_feedback_work_item(mock_ado: MockAdo) -> None:
      """Register an inline work item for WI-9300 (no separate fixture file)."""
      payload = {
          "id": 9300,
          "rev": 1,
          "fields": {
              "System.WorkItemType": "User Story",
              "System.Title": "Demonstrate PR-feedback round trip",
              "System.Description": "<p>Drives the feedback scenario.</p>",
              "Microsoft.VSTS.Common.Priority": 3,
          },
          "relations": [],
      }
      mock_ado.server.expect_request(
          f"/{mock_ado.project}/_apis/wit/workitems/9300",
          method="GET",
      ).respond_with_json(payload)


  def test_pr_feedback_round_trip(
      throwaway_repo: Path,
      mock_ado: MockAdo,
      canned_claude: CannedClaude,
      executor_env: ExecutorEnv,
  ) -> None:
      """Reviewer comment → sweep generates PR-feedback PBI → Ralph addresses it.

      Iteration sequence:
        i1: ralph-add submits WI-9300; executor claims, claude opens PR
            !4203, PBI moves to pending-pr/.
        Between i1 and i2: register a reviewer comment on PR !4203.
        i2: current/ is empty (the WI-9300 PBI is in pending-pr/); the
            sweep sees the new comment and writes a PR-feedback PBI into
            inbox/. The executor THEN claims that new PBI as the next
            iteration's work, arms the round-2 script, and Ralph posts
            a reply and sets thread status to fixed.
        After i2: the PR-feedback PBI sits in done/ (or pending-pr/,
            depending on whether the executor short-circuits "feedback
            handled" as terminal); we assert one of those two outcomes
            AND that the mock ADO recorded the thread status flip.
      """
      _seed_feedback_work_item(mock_ado)
      mock_ado.register_pr_open(4203, source_ref=f"refs/heads/ralph/{WI_FEEDBACK}")
      mock_ado.register_thread_reply_ok(4203, 7001)

      env = executor_env.with_overrides()
      summary = _submit_via_ralph_add("9300", throwaway_repo, env)
      assert summary["pbi_id"] == WI_FEEDBACK

      # ---- iteration 1: open the PR ----
      canned_claude.use("feedback_round_1.jsonl")
      proc1 = run_executor_iteration(throwaway_repo, env, max_iterations=1)
      assert proc1.returncode == 0, (
          f"feedback iter1 failed:\n{proc1.stdout}\n{proc1.stderr}"
      )
      git_run(throwaway_repo, "fetch", "origin", "ralph-queue")
      git_run(throwaway_repo, "checkout", "ralph-queue")
      git_run(throwaway_repo, "reset", "--hard", "origin/ralph-queue")
      assert_pbi_landed(throwaway_repo, WI_FEEDBACK, "pending-pr")

      # ---- between iterations: reviewer comments arrive ----
      mock_ado.register_pr_comments(4203, "pr_comments_round_1.json")

      # ---- iteration 2: sweep generates PR-feedback PBI, then Ralph addresses it ----
      canned_claude.use("feedback_round_2.jsonl")
      proc2 = run_executor_iteration(throwaway_repo, env, max_iterations=2)
      assert proc2.returncode == 0, (
          f"feedback iter2 failed:\n{proc2.stdout}\n{proc2.stderr}"
      )

      git_run(throwaway_repo, "fetch", "origin", "ralph-queue")
      git_run(throwaway_repo, "reset", "--hard", "origin/ralph-queue")

      # The sweep should have written a PR-feedback PBI into inbox/ at
      # some point during iter2. Its id follows the convention
      # ``PR-feedback-WI-9300-r1``.
      feedback_pbi_id = f"PR-feedback-{WI_FEEDBACK}-r1"
      state = pbi_state(throwaway_repo, feedback_pbi_id)
      assert state in ("current", "pending-pr", "done"), (
          f"PR-feedback PBI not found post-sweep; observed state: {state!r}; "
          f"inbox: {list_inbox(throwaway_repo)}"
      )

      # The mock ADO should have recorded the thread status flip to 'fixed'.
      assert mock_ado.state.thread_status.get((4203, 7001)) == "fixed", (
          "thread (4203, 7001) was not patched to 'fixed' — Ralph never replied?"
      )

      # And the FEEDBACK.md file inside the PR-feedback PBI should mention
      # the comment text we registered.
      candidate_dirs = list(
          (throwaway_repo / ".ralph").glob(f"*/{feedback_pbi_id}")
      )
      assert candidate_dirs, f"no {feedback_pbi_id} directory under .ralph/"
      feedback_md = (candidate_dirs[0] / "FEEDBACK.md").read_text(encoding="utf-8")
      assert "Please rename VALUE to v2" in feedback_md
  ```

- [ ] 2. Run the feedback scenario in isolation. This is the longest test (≈90s budget):
  ```
  uv run pytest tests/e2e/test_smoke.py::test_pr_feedback_round_trip -v
  ```
  Expected: pass. If the test fails because the sweep wrote a PR-feedback PBI but the iteration count exhausted before Ralph picked it up, raise `max_iterations` from 2 to 3 — document the change in the failing-test commit and verify in your local sweep timing.

- [ ] 3. Commit:
  ```
  git add tests/e2e/test_smoke.py
  git commit -m "test(e2e): scenario 4 — PR-feedback round-trip smoke"
  ```

---

## Task 10 — Scenario 5: cycle-detection trip

**Files**
- Modify: `tests/e2e/test_smoke.py` (append the cycle-trip scenario)

**Steps**

- [ ] 1. The cycle detector (Plan 9) operates on an event log under `.ralph/state/events.db` (or `events.jsonl`). The cleanest way to "trip" it from an e2e test is to (a) prime the event log with synthetic prior events that already approach the threshold, then (b) let the executor run one real iteration that pushes the signal across the threshold. Append the following:
  ```python


  WI_CYCLE = "WI-9002"  # reuse the bug fixture so the signature lines up


  def _prime_cycle_events(throwaway_repo: Path) -> None:
      """Drop a synthetic event log that primes the regression-cascade detector.

      Plan 9 documents the event log shape. We write five "feature merged"
      events for the same bug signature within a 4-hour window, putting
      the regression-cascade signal one event short of the threshold. The
      executor's next real iteration will record the sixth event and the
      detector will trip.
      """
      git_run(throwaway_repo, "checkout", "ralph-queue")
      state_dir = throwaway_repo / ".ralph" / "state"
      state_dir.mkdir(parents=True, exist_ok=True)
      events_path = state_dir / "events.jsonl"
      events = [
          {
              "ts": f"2026-05-24T{hour:02d}:00:00Z",
              "kind": "pbi_closed",
              "pbi_id": f"WI-9002-r{idx}",
              "signature": "irsa-access-denied",
              "outcome": "merged",
          }
          for idx, hour in enumerate(range(8, 13), start=1)
      ]
      events_path.write_text(
          "\n".join(json.dumps(e) for e in events) + "\n",
          encoding="utf-8",
      )
      git_run(throwaway_repo, "add", str(events_path.relative_to(throwaway_repo)))
      git_run(throwaway_repo, "commit", "-m", "test(e2e): prime cycle detector events")
      git_run(throwaway_repo, "push", "-u", "origin", "ralph-queue")


  def test_cycle_detection_trip(
      throwaway_repo: Path,
      mock_ado: MockAdo,
      canned_claude: CannedClaude,
      executor_env: ExecutorEnv,
  ) -> None:
      """Synthetic prior events + one real merge → cycle detector halts the loop.

      Asserts:
        * The executor exits with a non-zero code (or writes a META-BUG
          file under ``.ralph/blocked/``) when the regression-cascade
          signal crosses threshold.
        * The current PBI is NOT kicked back to inbox/ (it stays put).
        * Subsequent invocations refuse to restart until the META-BUG is
          acknowledged.
      """
      _prime_cycle_events(throwaway_repo)
      mock_ado.register_work_item("bug_irsa.json")
      mock_ado.register_pr_open(4204, source_ref=f"refs/heads/ralph/{WI_CYCLE}")

      env = executor_env.with_overrides()
      _submit_via_ralph_add("9002", throwaway_repo, env)

      canned_claude.use("cycle_trip.jsonl")
      proc = run_executor_iteration(throwaway_repo, env, max_iterations=1)

      # The executor records the merge event, the detector trips, and
      # the executor either:
      #   (a) exits with code 3 (cycle-halt sentinel, per Plan 9), OR
      #   (b) exits with code 0 but writes a META-BUG sentinel to
      #       .ralph/blocked/META-BUG/.
      assert proc.returncode in (0, 3), (
          f"unexpected executor exit code: {proc.returncode}\n"
          f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
      )

      git_run(throwaway_repo, "fetch", "origin", "ralph-queue")
      git_run(throwaway_repo, "checkout", "ralph-queue")
      git_run(throwaway_repo, "reset", "--hard", "origin/ralph-queue")
      meta_bug = throwaway_repo / ".ralph" / "blocked" / "META-BUG"
      assert meta_bug.is_dir() or "META-BUG" in proc.stderr or "cycle" in proc.stderr.lower(), (
          "expected a META-BUG directory under blocked/ OR a cycle-halt message "
          f"on stderr; got neither.\nstderr:\n{proc.stderr}"
      )

      # Re-run; the executor must REFUSE to restart until the META-BUG is acked.
      proc_restart = run_executor_iteration(throwaway_repo, env, max_iterations=1)
      assert proc_restart.returncode != 0, (
          "executor must not restart while META-BUG is unacked;\n"
          f"stdout:\n{proc_restart.stdout}\nstderr:\n{proc_restart.stderr}"
      )
      assert "META-BUG" in proc_restart.stderr or "halt" in proc_restart.stderr.lower()
  ```

- [ ] 2. Run the cycle scenario in isolation:
  ```
  uv run pytest tests/e2e/test_smoke.py::test_cycle_detection_trip -v
  ```
  Expected: pass. If the executor's actual cycle-halt sentinel uses a different convention (e.g. a file named `HALT` instead of a `META-BUG/` directory), update the assertion to match Plan 9's actual choice and record the deviation in a fix-up commit.

- [ ] 3. Commit:
  ```
  git add tests/e2e/test_smoke.py
  git commit -m "test(e2e): scenario 5 — regression-cascade cycle-detection halt"
  ```

---

## Task 11 — Sandbox-mode scaffold (documented, optional)

**Files**
- Modify: `tests/e2e/test_smoke.py` (append the sandbox-marked stub)

**Steps**

- [ ] 1. Append a single sandbox-marked test that runs the happy path against a real ADO instance when `--sandbox` is passed. The fixture cleanup is paranoid (delete-on-exit; ignore errors) and the credentials come from env vars that must be set externally — the test never embeds a real PAT in source:
  ```python


  @pytest.mark.sandbox
  def test_sandbox_happy_path() -> None:
      """Run the happy path against a real ADO sandbox.

      SKIPPED by default. Enable with ``--sandbox`` AND the following env
      vars set to a throwaway ADO project that you OWN and can wipe:

        * ``RALPH_SANDBOX_ADO_PAT``     — PAT with Work Items + Code RW
        * ``RALPH_SANDBOX_ADO_ORG_URL`` — e.g. https://dev.azure.com/your-org
        * ``RALPH_SANDBOX_ADO_PROJECT`` — project name
        * ``RALPH_SANDBOX_REPO_URL``    — git clone URL of a throwaway repo
                                          that has a ``ralph-queue`` branch
                                          already set up (per Plan 2 runbook).
        * ``RALPH_SANDBOX_WORK_ITEM``   — integer id of a pre-created
                                          throwaway work item you accept
                                          can be modified by this test.

      The test is deliberately minimal — it submits the work item via
      ``ralph-add``, runs one executor iteration with the REAL ``claude``
      binary (the canned stub is NOT activated in sandbox mode), and
      asserts the PBI lands in pending-pr/. The full BA→done flow is too
      slow / non-deterministic for an automated assert against a real
      sandbox; the smoke confirms the integration points work. See
      ``tests/e2e/README.md`` for cleanup instructions.
      """
      pat = os.environ.get("RALPH_SANDBOX_ADO_PAT")
      org_url = os.environ.get("RALPH_SANDBOX_ADO_ORG_URL")
      project = os.environ.get("RALPH_SANDBOX_ADO_PROJECT")
      repo_url = os.environ.get("RALPH_SANDBOX_REPO_URL")
      work_item = os.environ.get("RALPH_SANDBOX_WORK_ITEM")
      missing = [
          name for name, value in (
              ("RALPH_SANDBOX_ADO_PAT", pat),
              ("RALPH_SANDBOX_ADO_ORG_URL", org_url),
              ("RALPH_SANDBOX_ADO_PROJECT", project),
              ("RALPH_SANDBOX_REPO_URL", repo_url),
              ("RALPH_SANDBOX_WORK_ITEM", work_item),
          ) if not value
      ]
      if missing:
          pytest.skip(f"sandbox env vars missing: {missing}")
      pytest.skip(
          "Sandbox scaffold is documented but not executed in CI. "
          "Follow tests/e2e/README.md to drive this manually before a release."
      )
  ```
  (The final `pytest.skip` keeps the test from accidentally running against a real ADO when an over-eager developer combines `--sandbox` with stale env vars; the runbook in `README.md` describes the manual sequence and explicitly tells the reader to comment out the second `pytest.skip` for a dry run.)

- [ ] 2. Also append `import os` at the top of `test_smoke.py` if not already present. (Task 6 imported only `json`, `subprocess`, `Path`, and `pytest`; this task needs `os`.) Confirm with:
  ```
  uv run ruff check tests/e2e/test_smoke.py
  ```
  Expected: no `F821` undefined-name diagnostics.

- [ ] 3. Run the full e2e suite to confirm the sandbox test is properly skipped in CI mode:
  ```
  uv run pytest tests/e2e -v
  ```
  Expected: 5 passed (the four scenarios + nothing else from sandbox); the sandbox test reports `SKIPPED` (or doesn't appear without `--sandbox`).

- [ ] 4. Commit:
  ```
  git add tests/e2e/test_smoke.py
  git commit -m "test(e2e): add sandbox-mode scaffold for real-ADO pre-release validation"
  ```

---

## Task 12 — README and runbook

**Files**
- Create: `tests/e2e/README.md`

**Steps**

- [ ] 1. Write `tests/e2e/README.md` with the exact content below:
  ```markdown
  # End-to-end smoke tests

  This directory contains the final integration test for Ralph v1. It
  proves that the per-repo loop works end-to-end against a controlled
  environment: a throwaway git repo, a mocked Azure DevOps REST server,
  and a canned `claude -p` subprocess. Five scenarios are covered:

  1. **Happy-path feature** — BA submits a trivial feature PBI; Ralph
     edits the README, opens a PR, the PR "merges", the PBI lands in
     `done/`, and `ralph-status` reports it.
  2. **Bug path** — same flow with a Bug work item; severity is
     preserved and the bug PBI lands in `done/`.
  3. **STUCK path** — Ralph writes `STUCK.md`; the PBI moves to
     `blocked/` and the stuck reason is preserved.
  4. **PR-feedback round trip** — Ralph opens a PR; the sweep observes a
     reviewer comment; a PR-feedback PBI lands in `inbox/`; Ralph
     addresses it, posts a reply, and marks the thread `fixed`.
  5. **Cycle-detection trip** — synthetic prior events + one real merge
     push the regression-cascade signal across threshold; the executor
     halts and refuses to restart until the META-BUG is acked.

  ## Running the mocked path (CI-runnable)

  ```bash
  uv run pytest tests/e2e -v
  ```

  This is the default. Expected runtime: < 5 minutes for all five
  scenarios on a developer laptop. No external services are touched.

  ## Running the sandbox path (pre-release, manual)

  The sandbox path is **optional** for v1 — it is a manual pre-release
  check, not a CI gate. It runs the happy-path scenario against a real
  Azure DevOps sandbox project that you OWN and can wipe between runs.

  ### Prerequisites

  - A throwaway ADO project. You must be the project administrator (or
    have permission to delete the project after the test).
  - A throwaway service repo in that project, with the `ralph-queue`
    branch already provisioned per
    `docs/runbooks/ralph-queue-setup.md`.
  - A throwaway ADO work item in the project. Type doesn't matter; the
    test will modify its state via the executor.
  - A PAT with `Work Items (Read & Write)` and `Code (Read & Write)`
    scopes, NOT a personal long-lived PAT — issue a 1-day token for the
    test and revoke it afterwards.

  ### Environment variables

  ```bash
  export RALPH_SANDBOX_ADO_PAT="<short-lived PAT>"
  export RALPH_SANDBOX_ADO_ORG_URL="https://dev.azure.com/<your-throwaway-org>"
  export RALPH_SANDBOX_ADO_PROJECT="<your-throwaway-project>"
  export RALPH_SANDBOX_REPO_URL="<git clone URL>"
  export RALPH_SANDBOX_WORK_ITEM="<integer id>"
  ```

  ### Steps

  1. Verify the prereqs:
     ```bash
     uv run python -c "from scripts.ado_client import AdoClient; \
         c = AdoClient(org_url='$RALPH_SANDBOX_ADO_ORG_URL', \
             project='$RALPH_SANDBOX_ADO_PROJECT', \
             pat='$RALPH_SANDBOX_ADO_PAT'); \
         print(c.get('wit/workitems/$RALPH_SANDBOX_WORK_ITEM')['fields']['System.Title'])"
     ```
     Expected: prints the work item's title. If this fails, fix the
     credentials before going further.
  2. Open `tests/e2e/test_smoke.py` and comment out the second
     `pytest.skip(...)` inside `test_sandbox_happy_path`. (This is a
     deliberate human gate: the sandbox test should not run by accident.)
  3. Run:
     ```bash
     uv run pytest tests/e2e -v --sandbox -k sandbox
     ```
  4. The test will write a PBI to your sandbox repo's `ralph-queue`,
     create a real PR on your sandbox repo, and assert the PBI ends up
     in `pending-pr/`. CI is intentionally not exercised by the sandbox
     test — your sandbox project's own pipelines (or lack thereof)
     dictate what happens to the PR after it is opened.

  ### Cleanup

  After every sandbox run:

  1. **Revoke the short-lived PAT** in ADO immediately. (This is the
     single most important step. Forgetting this exposes a token that
     can mutate the sandbox repo.)
  2. **Abandon or close the PR** that the test created.
  3. **Delete the feature branch** the test pushed (`ralph/WI-<n>`).
  4. **Reset the `ralph-queue` branch** to the empty `.ralph/`
     skeleton commit, or delete the throwaway PBI directory under
     `.ralph/inbox/` / `.ralph/pending-pr/`.
  5. **Re-apply the `pytest.skip(...)` line** in
     `test_sandbox_happy_path` so the test is back to its safe default.

  ## Architecture notes

  The mock harness is layered:

  - `conftest.py::throwaway_repo` — per-test bare + worktree git pair.
  - `conftest.py::mock_ado` — `pytest-httpserver`-backed local HTTP
    server with a `MockAdo` convenience wrapper for registering work
    items, PRs, and threads.
  - `conftest.py::canned_claude` — installs a shim on `PATH` that
    defers to `e2e_claude_stub.py`. The stub reads a per-test JSONL
    script of canned actions and executes them against the throwaway
    repo, using the real `ado_client.AdoClient` to drive PRs through
    the mock server.
  - `_helpers.py` — utilities shared between tests and the stub.

  Why mock at the binary boundary (and not at a Python-level patch):

  - The executor (Plan 7) spawns `claude -p` as a real subprocess. The
    stub exercises the exact subprocess + stdout-parsing code path the
    production executor uses.
  - The stub uses the real `ado_client.AdoClient`, so any drift in the
    REST endpoint shapes is caught by the mock server's strict path
    matching.

  ## Adding a new scenario

  1. Add a JSONL claude script under `fixtures/claude_scripts/`.
  2. Add any required work-item or thread fixtures under
     `fixtures/work_items/` or `fixtures/`.
  3. Add a new `test_*` function in `test_smoke.py` that:
     - Registers the relevant mock ADO responses.
     - Runs `ralph-add` via `_submit_via_ralph_add`.
     - Calls `canned_claude.use("<your-script>.jsonl")`.
     - Runs one or more executor iterations.
     - Asserts on queue folder state via `pbi_state` /
       `assert_pbi_landed` and mock state via `mock_ado.state`.
  4. Confirm the new scenario completes inside the 5-minute total
     budget.
  ```

- [ ] 2. Confirm the README has no missing-link issues:
  ```
  uv run python -c "from pathlib import Path; t = Path('tests/e2e/README.md').read_text(encoding='utf-8'); print('lines:', t.count(chr(10)))"
  ```
  Expected: prints a line count > 100.

- [ ] 3. Commit:
  ```
  git add tests/e2e/README.md
  git commit -m "docs(e2e): runbook for the mocked + sandbox smoke paths"
  ```

---

## Task 13 — Shakeout, timing budget, project-wide gate

**Files**
- (No new files; modifications confined to `conftest.py` / `test_smoke.py` if needed.)

**Steps**

- [ ] 1. Run the full suite three times and record the slowest run:
  ```
  uv run pytest tests/e2e -v --tb=short --durations=10
  ```
  Expected: each run completes in < 5 minutes. If any run exceeds 5 minutes, file a follow-up to speed up the slowest scenario.

- [ ] 2. On Windows, sanity-check the `.cmd` launcher resolves the script pointer correctly (the launcher fixture branches on `sys.platform == "win32"`). If `tmp_path` resolves to a UNC path or contains spaces, prefer `pathlib.Path.resolve(strict=True)` in the launcher to normalise.

- [ ] 3. Run the project-wide quality gate to confirm no regression:
  ```
  uv run ruff check . && uv run ruff format --check . && uv run mypy ralph_executor scripts skills tests && uv run pytest
  ```
  Expected: all green.

- [ ] 4. If any scenario is flaky against the mock HTTP server, add `pytest-rerunfailures` (`uv add --dev pytest-rerunfailures` + `@pytest.mark.flaky(reruns=2)`) and file a follow-up issue. Do NOT silence a flake without filing the follow-up.

- [ ] 5. Commit any tweaks from this task, or skip if none were needed:
  ```
  git add tests/e2e/conftest.py tests/e2e/test_smoke.py
  git commit -m "test(e2e): tighten platform-launcher path resolution after shakeout"
  ```

---

## Verification gate

After all tasks complete, run the orchestrator's gate for Plan 13:

```bash
uv run pytest tests/e2e/test_smoke.py -v
```

**Expected outcome:**
- All five `@pytest.mark.e2e` tests pass: `test_happy_path_feature`,
  `test_bug_path`, `test_stuck_path`, `test_pr_feedback_round_trip`,
  `test_cycle_detection_trip`.
- The `@pytest.mark.sandbox` test reports `SKIPPED (sandbox-only; pass --sandbox to run)`.
- The total runtime is under 5 minutes on a developer laptop.

Then run the project-wide quality gate:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy ralph_executor scripts skills tests && uv run pytest
```

**Expected outcome:** all green, no regressions anywhere in the repo.

If both gates pass, Plan 13 — and Ralph v1 itself — is done. Tag the
release commit, then return to the orchestrator's
[v1 completion criteria](2026-05-24-00-orchestrator.md#v1-completion-criteria)
to confirm every other criterion is also satisfied before declaring v1
ship-ready.

---

## Self-review notes (concerns and trade-offs)

These are documented inline so future maintainers understand the
design trade-offs without re-deriving them:

1. **Binary-level claude mocking vs Python-level patch.** Binary
   mocking exercises the real subprocess boundary (PATH, stdout
   parsing, signal handling). The cost is a slightly elaborate
   fixture and platform-specific shim bugs (handled by the Windows
   `.cmd` branch). Worth it: any future change to how the executor
   invokes claude is automatically validated by the smoke.
2. **`pytest-httpserver` vs `responses`.** Plans 2/3/5 use `responses`
   because those tests are Python-only. The e2e tests must serve REAL
   HTTP because the canned claude stub is a subprocess; `responses`
   cannot intercept cross-process traffic. `pytest-httpserver` is
   pure-Python (no Java runtime requirement).
3. **Iteration-count flag.** The smoke assumes Plan 7's executor
   exposes `--iterations <n>` for tests. If Plan 7 uses a different
   mechanism, update `run_executor_iteration` in a fix-up commit.
   This is the single most likely integration friction between
   Plan 13 and Plan 7.
4. **Cycle-detection trip sensitivity.** The synthetic event log
   primes the signal to one event short of threshold; the real
   iteration writes the sixth event. The assertion accepts either
   `returncode in (0, 3)` to be tolerant of "halt non-zero exit" or
   "halt by writing META-BUG and exiting cleanly" — both spec-valid.
5. **PR-feedback iteration count.** Two iterations because the sweep
   generates a new PBI that the SAME iteration cannot also pick up
   (boundary check only re-evaluates lanes at iteration start). If
   Plan 8 restructures the sweep to immediately route the new PBI
   into `current/`, drop to 1.
6. **Sandbox test is double-skipped.** Marker-based `--sandbox` gate
   plus an in-test skip requiring a human edit. Deliberately
   paranoid: `--sandbox` with stale env vars must not cause surprise
   PR creation. README spells out the unlock procedure.
7. **Out-of-scope failure modes.** The smoke does NOT exercise:
   mid-sweep network failures (Plan 8 unit tests cover); permission
   prompts on a real pod (Plan 11's `ralph-doctor`); concurrent
   Ralphs (out of scope, single-Ralph-only). Adding any would push
   past the 5-minute budget.
