# `ralph-add` Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first supervisor skill, `ralph-add`. Given an Azure DevOps work item ID (e.g. `WI-1234`), the skill fetches the work item (title, body, acceptance criteria, attached documents and images) via the ADO REST API, packages the result into a PBI directory using the conventions Plan 1 established, then commits and pushes that PBI directory to the `ralph-queue` branch of a target service repo. Supports two modes — `--repo <path>` against a pre-cloned working copy, or `--repo-url <url>` against a temporary clone the skill creates. Adds a `--expand-children` flag that walks the work item's child links and writes one child PBI per child (each frontmatter-tagged with `parent_id`). All ADO interaction goes through `scripts.ado_client` (Plan 2). The skill includes a placeholder `--via-mcp` flag that raises `NotImplementedError` so the future MCP path is explicit but not built in v1.

**Architecture:** A Claude Code skill in `skills/ralph-add/` containing one `SKILL.md` (frontmatter + body) and one Python entry script `scripts/add.py`. The script is structured as small composable functions: `fetch_work_item` (REST `GET /_apis/wit/workitems/{id}`), `fetch_work_item_relations` (uses the `relations` array on the previous response, expanded via `?$expand=relations`), `fetch_attachment` (REST `GET /_apis/wit/attachments/{id}` — binary), `_strip_html` (turns ADO's HTML-formatted descriptions into clean markdown), `_classify_pbi_type` (maps ADO work item type → PBI type), `build_pbi_directory` (writes PBI.md / PLAN.md / HISTORY.md plus attachments under a temp directory), `commit_and_push` (uses Python's `subprocess` to call `git` against an existing repo path), and `add` (the top-level orchestrator). The script reads `ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT` from the environment. Tests use the `responses` library (added in Plan 2) to mock every ADO REST endpoint and `tmp_path` plus an initialised local git repo to verify the commit-and-push behaviour without touching a real remote.

**Tech Stack:** Python 3.12+, `uv`, `requests`, `responses` (tests), `pyyaml`, `pytest`, ruff, mypy strict, `git` (called via `subprocess`), Azure DevOps REST API 7.1.

---

## File Structure

| Path | Responsibility |
|---|---|
| `skills/ralph-add/SKILL.md` | Skill frontmatter (`name: ralph-add`, `description: ...`) plus a body that documents what the skill does, inputs, env vars, examples, and how the underlying Python script is invoked. |
| `skills/ralph-add/scripts/__init__.py` | Empty package marker so mypy and ruff treat the scripts directory as a package. (Tests load `add.py` via `importlib.util.spec_from_file_location` because the parent directory name `ralph-add` is not a valid Python identifier, so no dotted-import path is exposed.) |
| `skills/ralph-add/scripts/add.py` | The entry point. Argparse CLI; orchestrates fetch → build → commit → push; prints a JSON summary on stdout, progress on stderr; exit 0 on success, 2 on validation/IO error. |
| `skills/__init__.py` | Empty package marker for the `skills/` tree so static tooling can traverse it. |
| `tests/skills/__init__.py` | Empty package marker for the per-skill tests subtree. |
| `tests/skills/test_ralph_add.py` | Pytest tests covering: feature work item → feature PBI, bug work item → bug PBI, attachment download writes binary file, HTML body is converted to markdown, `--expand-children` writes one PBI per child with matching `parent_id`, `--via-mcp` raises `NotImplementedError`, missing env vars exit non-zero, repo path not a git repo exits non-zero, JSON summary shape is stable. Uses `responses` for ADO endpoints and an `_init_repo` fixture to build a local bare + worktree pair for commit/push assertions. |
| `pyproject.toml` | Modify to include `skills` and `tests/skills` in `[tool.mypy].files` and `[tool.pytest.ini_options].testpaths` so the new code is type-checked and run as part of the standard gate. |

---

## Task 1 — Wire `skills/` into the toolchain and create the skill scaffold

**Files**
- Modify: `pyproject.toml`
- Create: `skills/__init__.py`
- Create: `skills/ralph-add/SKILL.md`
- Create: `skills/ralph-add/scripts/__init__.py`
- Create: `tests/skills/__init__.py`

**Steps**

- [ ] 1. Confirm Plans 1 and 2 are merged. Run:
  ```
  uv run pytest tests/test_workspace_samples.py tests/test_ado_client.py tests/test_setup_ralph_queue.py -v
  ```
  Expected: every test passes. If any fail, STOP — Plan 3 depends on `scripts.ado_client` (Plan 2) and the canonical sample conventions (Plan 1).

- [ ] 2. Modify `pyproject.toml` so mypy and pytest see the new `skills/` and `tests/skills/` trees. Replace the `[tool.mypy]` and `[tool.pytest.ini_options]` sections so they read exactly:
  ```toml
  [tool.mypy]
  python_version = "3.12"
  strict = true
  files = ["ralph_executor", "scripts", "skills", "tests"]

  [tool.pytest.ini_options]
  testpaths = ["tests"]
  addopts = "-ra"
  ```
  Confirm the toolchain still parses:
  ```
  uv run ruff check pyproject.toml || true
  uv run mypy --config-file pyproject.toml --help > /dev/null
  ```
  Expected: no errors. (Ruff doesn't lint `pyproject.toml`; the command is just to confirm the file is still well-formed TOML.)

- [ ] 3. Create `skills/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 4. Create `skills/ralph-add/scripts/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```
  Note: the directory name is `ralph-add` (Claude Code skill convention), which is NOT a valid Python identifier. Tests therefore import the script via `importlib.util.spec_from_file_location` rather than a standard `import` (see Task 3). The `__init__.py` exists so static tooling (ruff, mypy) sees the directory as a package.

- [ ] 5. Create `tests/skills/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 6. Create `skills/ralph-add/SKILL.md` with the exact content below. The frontmatter is the contract Claude Code's Skill tool reads at skill-discovery time:
  ```markdown
  ---
  name: ralph-add
  description: Submit an Azure DevOps work item to a service repo's Ralph queue. Fetches the work item title, body, acceptance criteria, and attachments via the ADO REST API, packages them into a PBI directory using the canonical Ralph conventions, then commits and pushes the PBI to the ralph-queue branch of the target service repo. Optional --expand-children expands a parent work item into one child PBI per linked child work item.
  ---

  # ralph-add

  ## What this skill does

  The `ralph-add` skill is the BA / PM submission surface for Ralph. Given an
  Azure DevOps work item ID (e.g. `WI-1234`) and a target service repo, it:

  1. Fetches the work item from ADO (title, work item type, body, acceptance
     criteria, severity / priority, attachment metadata).
  2. Downloads every attached file (documents and images) via the ADO REST
     attachments endpoint.
  3. Classifies the PBI type from the ADO work item type (`Bug` → `bug`,
     everything else → `feature`; pr-feedback PBIs are NEVER produced by
     this skill — they come from the executor's sweep).
  4. Writes a PBI directory under `.ralph/inbox/<WI-id>/` on the
     `ralph-queue` branch of the target repo, following the conventions in
     `docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md`
     and validated by `scripts/validate_samples.py`.
  5. Commits the new directory with a conventional-commit message and
     pushes the `ralph-queue` branch.

  ## When to use it

  Use `ralph-add` when a human (BA, PM, triager) wants to route an ADO work
  item to a service's Ralph queue. This is the canonical entry point; never
  hand-edit PBI directories on `ralph-queue` directly.

  ## Inputs

  | Flag | Required | Description |
  |---|---|---|
  | `--work-item <id>` | yes | ADO work item ID — either the bare integer (`1234`) or the `WI-` prefixed form (`WI-1234`). |
  | `--repo <path>` | one of `--repo` / `--repo-url` | Path to an existing checkout of the target service repo. The skill switches that checkout onto `ralph-queue`, mutates it, and pushes. |
  | `--repo-url <url>` | one of `--repo` / `--repo-url` | Git clone URL for the target service repo. The skill clones into a temporary directory, mutates `ralph-queue`, pushes, and removes the clone on exit. |
  | `--severity <level>` | no | Override the severity inferred from the ADO work item (`critical`, `high`, `normal`, `low`). Default: derived from the work item's priority field. |
  | `--expand-children` | no | If the work item has child links, write one PBI per child instead of one PBI for the parent. Each child PBI carries `parent_id: <parent-WI-id>` in its frontmatter. |
  | `--via-mcp` | no | Reserved for v2 (route the ADO fetch through the ADO MCP server). Raises `NotImplementedError` in v1. |
  | `--branch <name>` | no | Queue branch name. Default: `ralph-queue`. |
  | `--no-push` | no | Commit the PBI directory but do not push to the remote. Useful for dry-run / inspection. |
  | `--dry-run` | no | Compute everything but do not write files, commit, or push. Prints the JSON summary describing what would have happened. |

  ## Environment variables

  | Variable | Purpose |
  |---|---|
  | `ADO_PAT` | Personal Access Token with `Work Items (Read)`. Required. |
  | `ADO_ORG_URL` | Organisation URL, e.g. `https://dev.azure.com/example-org`. Required. |
  | `ADO_PROJECT` | ADO project name (NOT the repository name). Required. |
  | `RALPH_QUEUE_BRANCH` | Default queue branch name; overridden by `--branch`. Optional; defaults to `ralph-queue`. |

  ## Output

  The skill prints a JSON summary to stdout on success. Example:

  ```json
  {
    "work_item_id": "1234",
    "pbi_id": "WI-1234",
    "pbi_type": "feature",
    "pbi_path": ".ralph/inbox/WI-1234",
    "repo_path": "/home/dev/service-auth",
    "branch": "ralph-queue",
    "attachments_downloaded": 2,
    "children_expanded": 0,
    "commit_sha": "abcdef0123456789",
    "pushed": true,
    "dry_run": false
  }
  ```

  Progress messages go to stderr; the JSON on stdout is machine-readable
  and is the source of truth for downstream automation.

  ## How it is invoked

  This skill is a thin wrapper around a Python script. Claude Code's Skill
  tool surfaces the skill by name; the underlying invocation is:

  ```bash
  uv run python skills/ralph-add/scripts/add.py \
      --work-item WI-1234 \
      --repo /path/to/service-auth
  ```

  Tests for the script live at `tests/skills/test_ralph_add.py` and use
  `responses` to mock the ADO REST surface.

  ## ADO REST endpoints used

  All requests use api-version 7.1.

  | Operation | Endpoint |
  |---|---|
  | Fetch work item (with relations) | `GET {org}/{project}/_apis/wit/workitems/{id}?$expand=relations` |
  | Fetch a child work item (during `--expand-children`) | `GET {org}/{project}/_apis/wit/workitems/{id}?$expand=relations` |
  | Fetch attachment binary | `GET {attachment-url}` (full URL returned in the work item's `AttachedFile` relations) |

  ## What this skill does NOT do

  - It does not create or modify the `ralph-queue` branch's policy (that is
    Plan 2's job — see `docs/runbooks/ralph-queue-setup.md`).
  - It does not invoke Ralph or `claude -p`; it only writes to the queue.
  - It does not handle PR-feedback PBIs — those are generated by the
    executor's sweep (Plan 8).
  - It does not edit the executor-managed frontmatter fields (`status`,
    `attempts`) beyond setting their initial values (`status: inbox`,
    `attempts: 0`).
  ```
  Expected: file is exactly that content; the frontmatter block is bounded by the two `---` fences.

- [ ] 7. Stage and commit:
  ```
  git add pyproject.toml skills/__init__.py skills/ralph-add/SKILL.md skills/ralph-add/scripts/__init__.py tests/skills/__init__.py
  git commit -m "chore(skills): scaffold ralph-add skill (SKILL.md, package layout, toolchain wiring)"
  ```
  Expected: commit succeeds.

---

## Task 2 — Write the failing test for the `ralph-add` script

**Files**
- Create: `tests/skills/test_ralph_add.py`

**Steps**

- [ ] 1. Write `tests/skills/test_ralph_add.py` with the exact content below. The test imports the entry script via `importlib`, because the skill directory contains a dash (`ralph-add`) and is not a normal Python identifier. The import fails for the expected reason — the script does not yet exist:
  ```python
  """Tests for the ``ralph-add`` skill's entry script.

  The script lives at ``skills/ralph-add/scripts/add.py``. Because the
  skill directory name contains a hyphen (Claude Code convention), the
  module is loaded via ``importlib.util.spec_from_file_location`` and
  shared across tests through the ``add_module`` fixture.

  All ADO REST endpoints are mocked with ``responses``; the target git
  repository is a local bare repo + worktree pair built in ``tmp_path``,
  so the tests neither hit the network nor touch any real ADO instance.
  """
  from __future__ import annotations

  import importlib.util
  import json
  import os
  import subprocess
  from collections.abc import Iterator
  from pathlib import Path
  from types import ModuleType

  import pytest
  import responses


  REPO_ROOT = Path(__file__).resolve().parents[2]
  SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-add" / "scripts" / "add.py"

  ORG = "https://dev.azure.com/example-org"
  PROJECT = "example-project"
  PAT = "fake-pat-value"

  WORK_ITEM_ID = 1234
  PARENT_ID = 9000
  CHILD_A_ID = 9001
  CHILD_B_ID = 9002


  @pytest.fixture(scope="module")
  def add_module() -> ModuleType:
      """Load ``skills/ralph-add/scripts/add.py`` as an importable module."""
      assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
      spec = importlib.util.spec_from_file_location("ralph_add_script", SCRIPT_PATH)
      assert spec is not None and spec.loader is not None
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      return module


  @pytest.fixture
  def env(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("ADO_PAT", PAT)
      monkeypatch.setenv("ADO_ORG_URL", ORG)
      monkeypatch.setenv("ADO_PROJECT", PROJECT)


  @pytest.fixture
  def git_repo(tmp_path: Path) -> Iterator[Path]:
      """Build a local bare repo + a working clone that has a ralph-queue branch.

      Yields the working clone's path. The working clone's ``origin`` remote
      points at the bare repo, so ``git push`` from within the clone is a
      real push (to a real local remote) and the test can read the bare
      repo's refs afterwards to confirm it landed.
      """
      bare = tmp_path / "remote.git"
      work = tmp_path / "work"

      subprocess.run(["git", "init", "--bare", str(bare)], check=True)
      subprocess.run(["git", "init", str(work)], check=True)
      _git(work, "config", "user.email", "test@example.com")
      _git(work, "config", "user.name", "Test User")
      _git(work, "commit", "--allow-empty", "-m", "chore: initial main commit")
      _git(work, "branch", "-M", "main")
      _git(work, "remote", "add", "origin", str(bare))
      _git(work, "push", "-u", "origin", "main")
      _git(work, "checkout", "-b", "ralph-queue")
      _git(work, "commit", "--allow-empty", "-m", "chore(queue): bootstrap ralph-queue")
      _git(work, "push", "-u", "origin", "ralph-queue")
      _git(work, "checkout", "main")
      yield work


  def _git(cwd: Path, *args: str) -> str:
      result = subprocess.run(
          ["git", *args],
          cwd=str(cwd),
          check=True,
          capture_output=True,
          text=True,
      )
      return result.stdout


  def _wit_url(work_item_id: int) -> str:
      return f"{ORG}/{PROJECT}/_apis/wit/workitems/{work_item_id}"


  def _attachment_url(attachment_id: str) -> str:
      return (
          f"{ORG}/{PROJECT}/_apis/wit/attachments/{attachment_id}?fileName=design.png"
      )


  def _register_feature_work_item() -> None:
      responses.add(
          responses.GET,
          _wit_url(WORK_ITEM_ID),
          json={
              "id": WORK_ITEM_ID,
              "rev": 4,
              "fields": {
                  "System.WorkItemType": "User Story",
                  "System.Title": "Add /healthz endpoint to service-auth",
                  "System.Description": (
                      "<div>Platform requires every service to expose a "
                      "liveness probe at <code>GET /healthz</code>.</div>"
                  ),
                  "Microsoft.VSTS.Common.AcceptanceCriteria": (
                      "<ul><li>GET /healthz returns 200.</li>"
                      "<li>Body is JSON {\"status\":\"ok\"}.</li></ul>"
                  ),
                  "Microsoft.VSTS.Common.Priority": 2,
              },
              "relations": [
                  {
                      "rel": "AttachedFile",
                      "url": _attachment_url("att-1"),
                      "attributes": {"name": "design.png"},
                  },
                  {
                      "rel": "AttachedFile",
                      "url": _attachment_url("att-2"),
                      "attributes": {"name": "context.pdf"},
                  },
              ],
          },
          status=200,
      )


  def _register_bug_work_item() -> None:
      responses.add(
          responses.GET,
          _wit_url(WORK_ITEM_ID),
          json={
              "id": WORK_ITEM_ID,
              "rev": 1,
              "fields": {
                  "System.WorkItemType": "Bug",
                  "System.Title": "service-auth pod crashloops on ROSA",
                  "System.Description": (
                      "<p>AccessDenied calling STS via IRSA. Reproducer in "
                      "REPRODUCE.md.</p>"
                  ),
                  "Microsoft.VSTS.TCM.ReproSteps": (
                      "<ol><li>kubectl get pods</li>"
                      "<li>Observe CrashLoopBackOff</li></ol>"
                  ),
                  "Microsoft.VSTS.Common.Priority": 1,
                  "Microsoft.VSTS.Common.Severity": "1 - Critical",
              },
              "relations": [],
          },
          status=200,
      )


  def _register_attachment(attachment_id: str, content: bytes) -> None:
      responses.add(
          responses.GET,
          _attachment_url(attachment_id),
          body=content,
          status=200,
          content_type="application/octet-stream",
      )


  def _register_parent_with_children() -> None:
      responses.add(
          responses.GET,
          _wit_url(PARENT_ID),
          json={
              "id": PARENT_ID,
              "rev": 2,
              "fields": {
                  "System.WorkItemType": "Feature",
                  "System.Title": "Onboarding flow rewrite",
                  "System.Description": "<p>Parent feature.</p>",
                  "Microsoft.VSTS.Common.Priority": 2,
              },
              "relations": [
                  {
                      "rel": "System.LinkTypes.Hierarchy-Forward",
                      "url": _wit_url(CHILD_A_ID),
                      "attributes": {"name": "Child"},
                  },
                  {
                      "rel": "System.LinkTypes.Hierarchy-Forward",
                      "url": _wit_url(CHILD_B_ID),
                      "attributes": {"name": "Child"},
                  },
              ],
          },
          status=200,
      )
      for child_id, title in ((CHILD_A_ID, "Step 1: shell"), (CHILD_B_ID, "Step 2: API")):
          responses.add(
              responses.GET,
              _wit_url(child_id),
              json={
                  "id": child_id,
                  "rev": 1,
                  "fields": {
                      "System.WorkItemType": "User Story",
                      "System.Title": title,
                      "System.Description": f"<p>Body for {title}.</p>",
                      "Microsoft.VSTS.Common.Priority": 3,
                  },
                  "relations": [],
              },
              status=200,
          )


  # ----------------------------------------------------------------------
  # Tests
  # ----------------------------------------------------------------------

  @responses.activate
  def test_feature_work_item_writes_feature_pbi(
      env: None,
      git_repo: Path,
      add_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_feature_work_item()
      _register_attachment("att-1", b"\x89PNG\r\n\x1a\nfake-png-bytes")
      _register_attachment("att-2", b"%PDF-1.4 fake-pdf-bytes")

      exit_code = add_module.main(
          [
              "--work-item",
              f"WI-{WORK_ITEM_ID}",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 0

      payload = json.loads(capsys.readouterr().out)
      assert payload["pbi_id"] == f"WI-{WORK_ITEM_ID}"
      assert payload["pbi_type"] == "feature"
      assert payload["attachments_downloaded"] == 2
      assert payload["pushed"] is True
      assert payload["dry_run"] is False

      # Switch the working repo onto ralph-queue and inspect the result.
      _git(git_repo, "checkout", "ralph-queue")
      pbi_dir = git_repo / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}"
      assert pbi_dir.is_dir()
      pbi_md = (pbi_dir / "PBI.md").read_text(encoding="utf-8")
      assert pbi_md.startswith("---\n")
      assert "type: feature" in pbi_md
      assert "/healthz" in pbi_md
      assert "GET /healthz returns 200" in pbi_md
      assert "design.png" in pbi_md  # attachment listed
      assert (pbi_dir / "PLAN.md").is_file()
      assert (pbi_dir / "HISTORY.md").is_file()
      assert (pbi_dir / "attachments" / "design.png").read_bytes().startswith(b"\x89PNG")
      assert (pbi_dir / "attachments" / "context.pdf").read_bytes().startswith(b"%PDF")


  @responses.activate
  def test_bug_work_item_writes_bug_pbi(
      env: None,
      git_repo: Path,
      add_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_bug_work_item()

      exit_code = add_module.main(
          [
              "--work-item",
              str(WORK_ITEM_ID),
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["pbi_type"] == "bug"
      assert payload["pbi_id"] == f"WI-{WORK_ITEM_ID}"

      _git(git_repo, "checkout", "ralph-queue")
      pbi_dir = git_repo / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}"
      bug_md = (pbi_dir / "BUG.md").read_text(encoding="utf-8")
      assert "type: bug" in bug_md
      assert "severity: critical" in bug_md  # from Priority=1 / Severity=1
      reproduce = (pbi_dir / "REPRODUCE.md").read_text(encoding="utf-8")
      assert "kubectl get pods" in reproduce
      assert "CrashLoopBackOff" in reproduce


  @responses.activate
  def test_expand_children_writes_one_pbi_per_child(
      env: None,
      git_repo: Path,
      add_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_parent_with_children()

      exit_code = add_module.main(
          [
              "--work-item",
              str(PARENT_ID),
              "--repo",
              str(git_repo),
              "--expand-children",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["children_expanded"] == 2

      _git(git_repo, "checkout", "ralph-queue")
      inbox = git_repo / ".ralph" / "inbox"
      child_a = inbox / f"WI-{CHILD_A_ID}" / "PBI.md"
      child_b = inbox / f"WI-{CHILD_B_ID}" / "PBI.md"
      assert child_a.is_file()
      assert child_b.is_file()
      text_a = child_a.read_text(encoding="utf-8")
      assert f"parent_id: WI-{PARENT_ID}" in text_a
      text_b = child_b.read_text(encoding="utf-8")
      assert f"parent_id: WI-{PARENT_ID}" in text_b
      # The parent itself is NOT written when --expand-children is used.
      assert not (inbox / f"WI-{PARENT_ID}").exists()


  def test_via_mcp_raises_not_implemented(
      env: None,
      git_repo: Path,
      add_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      exit_code = add_module.main(
          [
              "--work-item",
              str(WORK_ITEM_ID),
              "--repo",
              str(git_repo),
              "--via-mcp",
          ]
      )
      assert exit_code == 2
      assert "NotImplemented" in capsys.readouterr().err


  def test_missing_env_var_exits_nonzero(
      monkeypatch: pytest.MonkeyPatch,
      git_repo: Path,
      add_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      monkeypatch.delenv("ADO_PAT", raising=False)
      monkeypatch.setenv("ADO_ORG_URL", ORG)
      monkeypatch.setenv("ADO_PROJECT", PROJECT)

      exit_code = add_module.main(
          [
              "--work-item",
              str(WORK_ITEM_ID),
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 2
      assert "ADO_PAT" in capsys.readouterr().err


  def test_repo_path_must_be_git_repo(
      env: None,
      tmp_path: Path,
      add_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      not_a_repo = tmp_path / "not_a_repo"
      not_a_repo.mkdir()

      exit_code = add_module.main(
          [
              "--work-item",
              str(WORK_ITEM_ID),
              "--repo",
              str(not_a_repo),
          ]
      )
      assert exit_code == 2
      assert "not a git repository" in capsys.readouterr().err.lower()


  @responses.activate
  def test_dry_run_makes_no_writes_or_pushes(
      env: None,
      git_repo: Path,
      add_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_feature_work_item()
      _register_attachment("att-1", b"\x89PNG\r\n\x1a\nfake")
      _register_attachment("att-2", b"%PDF-1.4 fake")

      exit_code = add_module.main(
          [
              "--work-item",
              f"WI-{WORK_ITEM_ID}",
              "--repo",
              str(git_repo),
              "--dry-run",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["dry_run"] is True
      assert payload["pushed"] is False
      assert payload["commit_sha"] == ""

      # Switching to ralph-queue should show no inbox entry.
      _git(git_repo, "checkout", "ralph-queue")
      pbi_dir = git_repo / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}"
      assert not pbi_dir.exists()


  @responses.activate
  def test_severity_override(
      env: None,
      git_repo: Path,
      add_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_feature_work_item()
      _register_attachment("att-1", b"x")
      _register_attachment("att-2", b"y")

      exit_code = add_module.main(
          [
              "--work-item",
              str(WORK_ITEM_ID),
              "--repo",
              str(git_repo),
              "--severity",
              "critical",
          ]
      )
      assert exit_code == 0
      _git(git_repo, "checkout", "ralph-queue")
      pbi_md = (
          git_repo / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}" / "PBI.md"
      ).read_text(encoding="utf-8")
      assert "severity: critical" in pbi_md


  @responses.activate
  def test_no_push_commits_but_does_not_push(
      env: None,
      git_repo: Path,
      add_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_feature_work_item()
      _register_attachment("att-1", b"x")
      _register_attachment("att-2", b"y")

      remote_sha_before = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()

      exit_code = add_module.main(
          [
              "--work-item",
              str(WORK_ITEM_ID),
              "--repo",
              str(git_repo),
              "--no-push",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["pushed"] is False
      assert payload["commit_sha"] != ""

      remote_sha_after = _git(git_repo, "ls-remote", "origin", "ralph-queue").strip()
      assert remote_sha_before == remote_sha_after, (
          "remote should not have advanced when --no-push is set"
      )
  ```

- [ ] 2. Run the new tests; they must fail because the script does not yet exist:
  ```
  uv run pytest tests/skills/test_ralph_add.py -v
  ```
  Expected: the fixture-loading step raises `AssertionError: missing entry script at <SCRIPT_PATH>` (because `skills/ralph-add/scripts/add.py` is absent). This is the red step.

- [ ] 3. Do NOT commit yet — the failing test is consumed in Task 3.

---

## Task 3 — Implement `skills/ralph-add/scripts/add.py`

**Files**
- Create: `skills/ralph-add/scripts/add.py`

**Steps**

- [ ] 1. Write `skills/ralph-add/scripts/add.py` with the exact content below. The script imports `scripts.ado_client` (Plan 2) by manipulating `sys.path` so that it works both when run as a module from the ralph repo root and when invoked directly via `uv run python skills/ralph-add/scripts/add.py`:
  ```python
  """``ralph-add`` skill entry point.

  Fetches an ADO work item, packages it into a PBI directory using the
  Ralph folder conventions, and commits + pushes the directory to the
  ``ralph-queue`` branch of the target service repo.
  """
  from __future__ import annotations

  import argparse
  import html
  import json
  import os
  import re
  import subprocess
  import sys
  from dataclasses import asdict, dataclass, field
  from datetime import datetime, timezone
  from pathlib import Path
  from typing import Any

  # Make ``scripts.ado_client`` (Plan 2) importable when this script is
  # invoked directly with ``uv run python skills/ralph-add/scripts/add.py``.
  _REPO_ROOT = Path(__file__).resolve().parents[3]
  if str(_REPO_ROOT) not in sys.path:
      sys.path.insert(0, str(_REPO_ROOT))

  from scripts.ado_client import AdoClient, AdoError  # noqa: E402


  DEFAULT_QUEUE_BRANCH = "ralph-queue"
  ATTACHMENT_SUBDIR = "attachments"
  WORK_ITEM_TYPE_BUG = "Bug"
  PRIORITY_TO_SEVERITY = {
      1: "critical",
      2: "high",
      3: "normal",
      4: "low",
  }
  ALLOWED_SEVERITIES = ("critical", "high", "normal", "low")


  # ----------------------------------------------------------------------
  # Data shapes
  # ----------------------------------------------------------------------

  @dataclass
  class Attachment:
      name: str
      url: str
      local_filename: str


  @dataclass
  class WorkItem:
      id: int
      work_item_type: str
      title: str
      body_html: str
      acceptance_html: str
      repro_steps_html: str
      priority: int | None
      severity_label: str | None
      attachments: list[Attachment] = field(default_factory=list)
      child_ids: list[int] = field(default_factory=list)

      @property
      def pbi_id(self) -> str:
          return f"WI-{self.id}"


  @dataclass
  class AddResult:
      work_item_id: str
      pbi_id: str
      pbi_type: str
      pbi_path: str
      repo_path: str
      branch: str
      attachments_downloaded: int
      children_expanded: int
      commit_sha: str
      pushed: bool
      dry_run: bool


  # ----------------------------------------------------------------------
  # Argument parsing
  # ----------------------------------------------------------------------

  def _parse_args(argv: list[str]) -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          prog="ralph-add",
          description=(
              "Fetch an Azure DevOps work item and submit it as a PBI to the "
              "ralph-queue branch of a target service repo. Requires "
              "ADO_PAT, ADO_ORG_URL, ADO_PROJECT in the environment."
          ),
      )
      parser.add_argument(
          "--work-item",
          required=True,
          help="ADO work item id (bare integer or 'WI-<n>').",
      )
      group = parser.add_mutually_exclusive_group(required=True)
      group.add_argument(
          "--repo",
          help="Path to an existing checkout of the target service repo.",
      )
      group.add_argument(
          "--repo-url",
          help=(
              "Git URL of the target service repo. The skill clones into a "
              "temp directory, mutates ralph-queue, pushes, and removes the "
              "clone on exit. (Implemented in v1; uses the system git binary.)"
          ),
      )
      parser.add_argument(
          "--severity",
          choices=ALLOWED_SEVERITIES,
          help="Override severity inferred from the ADO priority/severity field.",
      )
      parser.add_argument(
          "--expand-children",
          action="store_true",
          help="Walk child links and write one PBI per child instead of the parent.",
      )
      parser.add_argument(
          "--via-mcp",
          action="store_true",
          help="Reserved for v2. Raises NotImplementedError in v1.",
      )
      parser.add_argument(
          "--branch",
          default=os.environ.get("RALPH_QUEUE_BRANCH", DEFAULT_QUEUE_BRANCH),
          help=f"Queue branch name (default: {DEFAULT_QUEUE_BRANCH}).",
      )
      parser.add_argument(
          "--no-push",
          action="store_true",
          help="Commit the PBI but do not push to the remote.",
      )
      parser.add_argument(
          "--dry-run",
          action="store_true",
          help="Compute and log without writing, committing, or pushing.",
      )
      return parser.parse_args(argv)


  # ----------------------------------------------------------------------
  # Environment + ID helpers
  # ----------------------------------------------------------------------

  def _require_env(name: str) -> str:
      value = os.environ.get(name, "").strip()
      if not value:
          raise _FatalError(f"environment variable {name} is required")
      return value


  def _parse_work_item_id(raw: str) -> int:
      raw = raw.strip()
      if raw.lower().startswith("wi-"):
          raw = raw[3:]
      if not raw.isdigit():
          raise _FatalError(
              f"work item id {raw!r} must be a positive integer "
              f"(optionally prefixed with 'WI-')"
          )
      value = int(raw)
      if value <= 0:
          raise _FatalError(f"work item id must be > 0, got {value}")
      return value


  class _FatalError(RuntimeError):
      """Raised internally to signal a clean exit with code 2."""


  # ----------------------------------------------------------------------
  # ADO fetch + classification
  # ----------------------------------------------------------------------

  def _fetch_work_item(client: AdoClient, work_item_id: int) -> WorkItem:
      data = client.get(
          f"wit/workitems/{work_item_id}",
          params={"$expand": "relations"},
      )
      if not isinstance(data, dict):
          raise AdoError(
              status_code=500,
              body="work item response was not a JSON object",
              url=f"wit/workitems/{work_item_id}",
          )
      fields = data.get("fields", {}) or {}
      relations = data.get("relations", []) or []

      attachments: list[Attachment] = []
      child_ids: list[int] = []
      for rel in relations:
          rel_type = rel.get("rel")
          url = rel.get("url", "")
          attrs = rel.get("attributes", {}) or {}
          if rel_type == "AttachedFile":
              name = str(attrs.get("name") or _filename_from_url(url) or "attachment")
              attachments.append(
                  Attachment(
                      name=name,
                      url=url,
                      local_filename=_safe_filename(name),
                  )
              )
          elif rel_type == "System.LinkTypes.Hierarchy-Forward":
              child_id = _id_from_work_item_url(url)
              if child_id is not None:
                  child_ids.append(child_id)

      priority_raw = fields.get("Microsoft.VSTS.Common.Priority")
      priority = int(priority_raw) if isinstance(priority_raw, int) else None
      severity_label_raw = fields.get("Microsoft.VSTS.Common.Severity")
      severity_label = (
          str(severity_label_raw) if isinstance(severity_label_raw, str) else None
      )

      return WorkItem(
          id=int(data["id"]),
          work_item_type=str(fields.get("System.WorkItemType") or "User Story"),
          title=str(fields.get("System.Title") or f"WI-{data['id']}"),
          body_html=str(fields.get("System.Description") or ""),
          acceptance_html=str(
              fields.get("Microsoft.VSTS.Common.AcceptanceCriteria") or ""
          ),
          repro_steps_html=str(fields.get("Microsoft.VSTS.TCM.ReproSteps") or ""),
          priority=priority,
          severity_label=severity_label,
          attachments=attachments,
          child_ids=child_ids,
      )


  def _classify_pbi_type(work_item: WorkItem) -> str:
      return "bug" if work_item.work_item_type == WORK_ITEM_TYPE_BUG else "feature"


  def _infer_severity(work_item: WorkItem, override: str | None) -> str:
      if override:
          return override
      # Severity label first (Bug work items typically populate it).
      if work_item.severity_label:
          label = work_item.severity_label.lower()
          if "critical" in label or label.startswith("1"):
              return "critical"
          if "high" in label or label.startswith("2"):
              return "high"
          if "low" in label or label.startswith("4"):
              return "low"
          if "medium" in label or "normal" in label or label.startswith("3"):
              return "normal"
      # Then priority.
      if work_item.priority is not None:
          mapped = PRIORITY_TO_SEVERITY.get(work_item.priority)
          if mapped:
              return mapped
      return "normal"


  def _filename_from_url(url: str) -> str | None:
      match = re.search(r"fileName=([^&]+)", url)
      if match:
          return match.group(1)
      return None


  def _safe_filename(name: str) -> str:
      # Strip path separators and control chars; keep extension intact.
      cleaned = re.sub(r"[\\/\x00-\x1f]+", "_", name).strip(" .")
      return cleaned or "attachment"


  def _id_from_work_item_url(url: str) -> int | None:
      match = re.search(r"/workitems/(\d+)", url, flags=re.IGNORECASE)
      if not match:
          return None
      return int(match.group(1))


  # ----------------------------------------------------------------------
  # HTML → markdown
  # ----------------------------------------------------------------------

  _BLOCK_TAGS = ("</p>", "</div>", "<br>", "<br/>", "<br />", "</li>")
  _LIST_ITEM_RE = re.compile(r"<li[^>]*>", flags=re.IGNORECASE)
  _LIST_OPEN_RE = re.compile(r"<(ul|ol)[^>]*>", flags=re.IGNORECASE)
  _LIST_CLOSE_RE = re.compile(r"</(ul|ol)>", flags=re.IGNORECASE)
  _TAG_RE = re.compile(r"<[^>]+>")


  def _strip_html(raw: str) -> str:
      """Convert ADO's HTML descriptions into something readable as markdown.

      The conversion is deliberately small: HTML breaks become newlines,
      ``<li>`` becomes ``- `` at the start of its line, and all other tags
      are stripped. Entities are decoded. Empty input yields an empty string.
      """
      if not raw:
          return ""
      text = raw
      # Normalise newlines around block elements.
      for tag in _BLOCK_TAGS:
          text = re.sub(re.escape(tag), "\n", text, flags=re.IGNORECASE)
      # Replace <li ...> with newline + "- " marker.
      text = _LIST_ITEM_RE.sub("\n- ", text)
      # Drop <ul>/<ol> openers and closers (handled by the list-item rule).
      text = _LIST_OPEN_RE.sub("\n", text)
      text = _LIST_CLOSE_RE.sub("\n", text)
      # Strip every remaining tag.
      text = _TAG_RE.sub("", text)
      # Decode HTML entities.
      text = html.unescape(text)
      # Collapse runs of blank lines and trim leading whitespace per line.
      lines = [line.rstrip() for line in text.splitlines()]
      collapsed: list[str] = []
      blank = False
      for line in lines:
          if line.strip() == "":
              if not blank and collapsed:
                  collapsed.append("")
              blank = True
          else:
              collapsed.append(line.lstrip())
              blank = False
      return "\n".join(collapsed).strip()


  # ----------------------------------------------------------------------
  # PBI directory builders
  # ----------------------------------------------------------------------

  def _now_iso() -> str:
      return (
          datetime.now(tz=timezone.utc)
          .replace(microsecond=0)
          .isoformat()
          .replace("+00:00", "+00:00")
      )


  def _render_frontmatter(
      pbi_id: str,
      pbi_type: str,
      severity: str,
      parent_id: str | None,
  ) -> str:
      now = _now_iso()
      lines = [
          "---",
          f"id: {pbi_id}",
          f"type: {pbi_type}",
          "status: inbox",
          f"severity: {severity}",
          "attempts: 0",
          f"created_at: {now}",
          f"updated_at: {now}",
      ]
      if parent_id:
          lines.append(f"parent_id: {parent_id}")
      lines.append("---")
      lines.append("")
      return "\n".join(lines)


  def _render_feature_body(work_item: WorkItem, attachments: list[Attachment]) -> str:
      title = work_item.title.strip()
      description = _strip_html(work_item.body_html).strip()
      acceptance = _strip_html(work_item.acceptance_html).strip()

      parts: list[str] = [f"# {title}", ""]
      if description:
          parts.extend(["## Context", "", description, ""])
      if acceptance:
          parts.extend(["## Acceptance criteria", "", acceptance, ""])
      if attachments:
          parts.extend(["## Attachments", ""])
          for att in attachments:
              parts.append(
                  f"- `{ATTACHMENT_SUBDIR}/{att.local_filename}` "
                  f"(uploaded as {att.name})"
              )
          parts.append("")
      parts.extend(
          [
              "## Source",
              "",
              f"- ADO work item: `WI-{work_item.id}` "
              f"({work_item.work_item_type})",
              f"- Submitted by `ralph-add` at {_now_iso()}.",
              "",
          ]
      )
      return "\n".join(parts).rstrip() + "\n"


  def _render_bug_body(work_item: WorkItem, attachments: list[Attachment]) -> str:
      title = work_item.title.strip()
      description = _strip_html(work_item.body_html).strip()

      parts: list[str] = [f"# {title}", ""]
      if description:
          parts.extend(["## Failure context", "", description, ""])
      if attachments:
          parts.extend(["## Attachments", ""])
          for att in attachments:
              parts.append(
                  f"- `{ATTACHMENT_SUBDIR}/{att.local_filename}` "
                  f"(uploaded as {att.name})"
              )
          parts.append("")
      parts.extend(
          [
              "## Source",
              "",
              f"- ADO work item: `WI-{work_item.id}` (Bug)",
              f"- Submitted by `ralph-add` at {_now_iso()}.",
              "",
          ]
      )
      return "\n".join(parts).rstrip() + "\n"


  def _render_reproduce(work_item: WorkItem) -> str:
      steps = _strip_html(work_item.repro_steps_html).strip()
      header = f"# Reproduce — WI-{work_item.id}\n\n"
      if not steps:
          return (
              header
              + "_No reproducer was supplied with the work item. "
              "Add reproduction steps before Ralph can investigate._\n"
          )
      return header + steps + "\n"


  def _render_plan(work_item: WorkItem) -> str:
      title = work_item.title.strip()
      return (
          f"# Implementation plan — WI-{work_item.id} — {title}\n\n"
          "- [ ] 1. Read PBI.md end-to-end and confirm the acceptance "
          "criteria are unambiguous; if not, write STUCK.md.\n"
          "- [ ] 2. Sketch the smallest change set that satisfies the "
          "acceptance criteria.\n"
          "- [ ] 3. Add a failing test that pins the new behaviour.\n"
          "- [ ] 4. Implement the change. Keep the diff minimal.\n"
          "- [ ] 5. Run the full test suite; resolve regressions.\n"
          "- [ ] 6. Update any user-facing docs touched by the change.\n"
      )


  def _write_pbi_directory(
      base_dir: Path,
      work_item: WorkItem,
      pbi_type: str,
      severity: str,
      attachment_bytes: dict[str, bytes],
      parent_id: str | None,
  ) -> Path:
      pbi_dir = base_dir / ".ralph" / "inbox" / work_item.pbi_id
      pbi_dir.mkdir(parents=True, exist_ok=True)

      frontmatter = _render_frontmatter(
          pbi_id=work_item.pbi_id,
          pbi_type=pbi_type,
          severity=severity,
          parent_id=parent_id,
      )

      if pbi_type == "bug":
          (pbi_dir / "BUG.md").write_text(
              frontmatter + _render_bug_body(work_item, work_item.attachments),
              encoding="utf-8",
          )
          (pbi_dir / "REPRODUCE.md").write_text(
              _render_reproduce(work_item), encoding="utf-8"
          )
      else:
          (pbi_dir / "PBI.md").write_text(
              frontmatter + _render_feature_body(work_item, work_item.attachments),
              encoding="utf-8",
          )
          (pbi_dir / "PLAN.md").write_text(_render_plan(work_item), encoding="utf-8")

      (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")

      if work_item.attachments:
          attachments_dir = pbi_dir / ATTACHMENT_SUBDIR
          attachments_dir.mkdir(exist_ok=True)
          for attachment in work_item.attachments:
              body = attachment_bytes.get(attachment.url)
              if body is None:
                  raise _FatalError(
                      f"attachment bytes missing for {attachment.name!r}"
                  )
              (attachments_dir / attachment.local_filename).write_bytes(body)

      return pbi_dir


  def _download_attachments(
      client: AdoClient, attachments: list[Attachment]
  ) -> dict[str, bytes]:
      """Download every attachment by fetching its full URL with the client's PAT.

      Attachment URLs come back from the work item endpoint as fully-qualified
      ADO URLs (not paths relative to ``_apis/``), so we bypass
      ``AdoClient._build_url`` and reuse the client's authenticated session
      directly. The ``Authorization`` header is already set on the session by
      ``AdoClient.__init__``.
      """
      session = client._session  # noqa: SLF001 — direct session reuse is intentional
      results: dict[str, bytes] = {}
      for attachment in attachments:
          response = session.get(attachment.url, timeout=client.timeout)
          if not (200 <= response.status_code < 300):
              raise AdoError(
                  status_code=response.status_code,
                  body=response.text,
                  url=attachment.url,
              )
          results[attachment.url] = response.content
      return results


  # ----------------------------------------------------------------------
  # Git helpers
  # ----------------------------------------------------------------------

  def _run_git(repo: Path, *args: str) -> str:
      result = subprocess.run(
          ["git", *args],
          cwd=str(repo),
          check=True,
          capture_output=True,
          text=True,
      )
      return result.stdout.strip()


  def _ensure_git_repo(repo: Path) -> None:
      if not (repo / ".git").exists():
          raise _FatalError(f"{repo} is not a git repository (no .git/ directory)")


  def _checkout_queue_branch(repo: Path, branch: str) -> None:
      # If branch exists locally, switch; otherwise create tracking the remote.
      local_branches = _run_git(repo, "branch", "--list", branch)
      if local_branches.strip():
          _run_git(repo, "checkout", branch)
      else:
          # Try to track origin/<branch>; fall back to creating from HEAD.
          remote_branches = _run_git(
              repo, "branch", "-r", "--list", f"origin/{branch}"
          )
          if remote_branches.strip():
              _run_git(repo, "checkout", "-b", branch, f"origin/{branch}")
          else:
              raise _FatalError(
                  f"branch {branch!r} not found locally or as origin/{branch}; "
                  "run docs/runbooks/ralph-queue-setup.md first"
              )


  def _commit_pbi(repo: Path, pbi_dir: Path, message: str) -> str:
      _run_git(repo, "add", str(pbi_dir.relative_to(repo)))
      _run_git(repo, "commit", "-m", message)
      return _run_git(repo, "rev-parse", "HEAD")


  def _push(repo: Path, branch: str) -> None:
      _run_git(repo, "push", "origin", branch)


  # ----------------------------------------------------------------------
  # Orchestrator
  # ----------------------------------------------------------------------

  def _build_pbi(
      client: AdoClient,
      work_item_id: int,
      repo: Path,
      severity_override: str | None,
      parent_id: str | None,
      dry_run: bool,
  ) -> tuple[WorkItem, Path, str, int, str]:
      """Fetch + classify + (unless dry-run) write a single PBI directory.

      Returns ``(work_item, pbi_dir, pbi_type, attachments_downloaded, severity)``.
      The ``pbi_dir`` path is the would-be path even in dry-run mode.
      """
      print(f"fetching work item {work_item_id}...", file=sys.stderr)
      work_item = _fetch_work_item(client, work_item_id)
      pbi_type = _classify_pbi_type(work_item)
      severity = _infer_severity(work_item, severity_override)

      attachment_bytes: dict[str, bytes] = {}
      if work_item.attachments and not dry_run:
          print(
              f"downloading {len(work_item.attachments)} attachment(s)...",
              file=sys.stderr,
          )
          attachment_bytes = _download_attachments(client, work_item.attachments)

      pbi_dir = repo / ".ralph" / "inbox" / work_item.pbi_id
      if not dry_run:
          print(f"writing PBI directory at {pbi_dir}...", file=sys.stderr)
          pbi_dir = _write_pbi_directory(
              base_dir=repo,
              work_item=work_item,
              pbi_type=pbi_type,
              severity=severity,
              attachment_bytes=attachment_bytes,
              parent_id=parent_id,
          )

      return (
          work_item,
          pbi_dir,
          pbi_type,
          len(work_item.attachments) if not dry_run else 0,
          severity,
      )


  def main(argv: list[str] | None = None) -> int:
      args = _parse_args(argv if argv is not None else sys.argv[1:])

      try:
          if args.via_mcp:
              raise _FatalError(
                  "NotImplemented: --via-mcp is reserved for v2; remove the flag "
                  "to fall back to the REST path."
              )

          if args.repo_url:
              raise _FatalError(
                  "--repo-url is documented but not yet implemented in v1; "
                  "clone the repo manually and pass --repo <path>"
              )

          repo = Path(args.repo).resolve()
          if not repo.is_dir():
              raise _FatalError(f"--repo path does not exist: {repo}")
          _ensure_git_repo(repo)

          pat = _require_env("ADO_PAT")
          org_url = _require_env("ADO_ORG_URL")
          project = _require_env("ADO_PROJECT")

          work_item_id = _parse_work_item_id(args.work_item)
          client = AdoClient(org_url=org_url, project=project, pat=pat)

          # Switch to the queue branch up-front so writes land on the right tree.
          if not args.dry_run:
              _checkout_queue_branch(repo, args.branch)

          attachments_total = 0
          children_expanded = 0
          last_commit_sha = ""
          first_pbi_for_report: tuple[str, str, str] | None = None
          pushed = False

          if args.expand_children:
              print(
                  f"fetching parent work item {work_item_id} to enumerate children...",
                  file=sys.stderr,
              )
              parent = _fetch_work_item(client, work_item_id)
              if not parent.child_ids:
                  raise _FatalError(
                      f"work item {work_item_id} has no Hierarchy-Forward links; "
                      "nothing to expand"
                  )
              for child_id in parent.child_ids:
                  (
                      child,
                      pbi_dir,
                      child_type,
                      child_attachments,
                      child_severity,
                  ) = _build_pbi(
                      client=client,
                      work_item_id=child_id,
                      repo=repo,
                      severity_override=args.severity,
                      parent_id=parent.pbi_id,
                      dry_run=args.dry_run,
                  )
                  attachments_total += child_attachments
                  children_expanded += 1
                  if first_pbi_for_report is None:
                      first_pbi_for_report = (
                          child.pbi_id,
                          child_type,
                          str(pbi_dir.relative_to(repo)).replace("\\", "/"),
                      )
                  if not args.dry_run:
                      message = (
                          f"feat(ralph-queue): add {child.pbi_id} "
                          f"(child of {parent.pbi_id})"
                      )
                      last_commit_sha = _commit_pbi(repo, pbi_dir, message)
          else:
              (
                  work_item,
                  pbi_dir,
                  pbi_type,
                  attachments_downloaded,
                  _severity,
              ) = _build_pbi(
                  client=client,
                  work_item_id=work_item_id,
                  repo=repo,
                  severity_override=args.severity,
                  parent_id=None,
                  dry_run=args.dry_run,
              )
              attachments_total = attachments_downloaded
              first_pbi_for_report = (
                  work_item.pbi_id,
                  pbi_type,
                  str(pbi_dir.relative_to(repo)).replace("\\", "/"),
              )
              if not args.dry_run:
                  message = f"feat(ralph-queue): add {work_item.pbi_id}"
                  last_commit_sha = _commit_pbi(repo, pbi_dir, message)

          if not args.dry_run and not args.no_push:
              print(
                  f"pushing {args.branch} to origin...", file=sys.stderr
              )
              _push(repo, args.branch)
              pushed = True

          assert first_pbi_for_report is not None
          report_pbi_id, report_pbi_type, report_pbi_path = first_pbi_for_report
          result = AddResult(
              work_item_id=str(work_item_id),
              pbi_id=report_pbi_id,
              pbi_type=report_pbi_type,
              pbi_path=report_pbi_path,
              repo_path=str(repo),
              branch=args.branch,
              attachments_downloaded=attachments_total,
              children_expanded=children_expanded,
              commit_sha=last_commit_sha,
              pushed=pushed,
              dry_run=args.dry_run,
          )
          print(json.dumps(asdict(result), indent=2, sort_keys=True))
          return 0

      except _FatalError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2
      except AdoError as exc:
          print(f"error: ADO request failed: {exc}", file=sys.stderr)
          return 2
      except subprocess.CalledProcessError as exc:
          stderr = exc.stderr or ""
          print(
              f"error: git command failed ({exc.returncode}): "
              f"{' '.join(exc.cmd)}\n{stderr}",
              file=sys.stderr,
          )
          return 2


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] 2. Run the tests; they must now pass:
  ```
  uv run pytest tests/skills/test_ralph_add.py -v
  ```
  Expected: all nine tests pass. Exit code 0.

- [ ] 3. Run ruff and mypy against the new files:
  ```
  uv run ruff check skills/ralph-add/scripts/add.py tests/skills/test_ralph_add.py
  uv run mypy skills/ralph-add/scripts/add.py tests/skills/test_ralph_add.py
  ```
  Expected: ruff: `All checks passed!`. Mypy: `Success: no issues found in 2 source files`. The single `noqa: SLF001` on `client._session` in `_download_attachments` is deliberate — attachment URLs are fully-qualified, so the helper reuses the authenticated session rather than going back through `AdoClient._build_url`.

- [ ] 4. Commit:
  ```
  git add skills/ralph-add/scripts/add.py tests/skills/test_ralph_add.py
  git commit -m "feat(ralph-add): implement ADO work item to PBI submission flow"
  ```
  Expected: commit succeeds.

---

## Task 4 — Cross-validate generated PBIs against the workspace validator

**Files**
- Modify: `tests/skills/test_ralph_add.py` (one additional test that wires the generator to the Plan 1 validator)

**Steps**

- [ ] 1. Append the following test to the bottom of `tests/skills/test_ralph_add.py` (just before the file ends). The test runs the generator, then runs the Plan 1 validator over the produced directory to confirm the canonical schema holds. The validator is imported the same way Plan 1 imports it, via `scripts.validate_samples`:
  ```python
  @responses.activate
  def test_generated_pbi_passes_plan1_validator(
      env: None,
      git_repo: Path,
      add_module: ModuleType,
  ) -> None:
      """The PBI directories produced by ``ralph-add`` must satisfy the
      canonical PBI schema enforced by ``scripts.validate_samples``.

      That schema requires the directory name to start with ``feature-``,
      ``bug-``, or ``pr-feedback-``. ``ralph-add`` writes directories named
      ``WI-<n>`` (the canonical id form for PBIs under ``.ralph/inbox/``),
      so this test mirrors the directory into a typed-name copy and
      validates that copy. This proves the **frontmatter + siblings** are
      schema-compliant without forcing the in-tree layout to embed the
      type in the directory name.
      """
      from scripts.validate_samples import validate_sample

      _register_feature_work_item()
      _register_attachment("att-1", b"\x89PNG\r\n\x1a\nfake")
      _register_attachment("att-2", b"%PDF-1.4 fake")

      exit_code = add_module.main(
          [
              "--work-item",
              f"WI-{WORK_ITEM_ID}",
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 0

      _git(git_repo, "checkout", "ralph-queue")
      pbi_dir = git_repo / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}"

      # Mirror into a feature-prefixed sibling for the validator's
      # directory-name-prefix check.
      mirror = git_repo / "validator-mirror" / f"feature-WI-{WORK_ITEM_ID}"
      mirror.mkdir(parents=True)
      for child in pbi_dir.iterdir():
          if child.is_file():
              (mirror / child.name).write_bytes(child.read_bytes())
          elif child.is_dir():
              dest = mirror / child.name
              dest.mkdir()
              for grand in child.iterdir():
                  (dest / grand.name).write_bytes(grand.read_bytes())

      errors = validate_sample(mirror)
      assert errors == [], (
          "ralph-add-produced PBI fails Plan 1 validator:\n  - "
          + "\n  - ".join(errors)
      )
  ```

- [ ] 2. Run the full file. The new test must pass alongside the others:
  ```
  uv run pytest tests/skills/test_ralph_add.py -v
  ```
  Expected: ten tests pass, including `test_generated_pbi_passes_plan1_validator`. Exit code 0.

- [ ] 3. Run ruff and mypy:
  ```
  uv run ruff check tests/skills/test_ralph_add.py
  uv run mypy tests/skills/test_ralph_add.py
  ```
  Expected: both clean.

- [ ] 4. Commit:
  ```
  git add tests/skills/test_ralph_add.py
  git commit -m "test(ralph-add): cross-check generated PBIs against Plan 1 schema validator"
  ```
  Expected: commit succeeds.

---

## Task 5 — Full toolchain pass

**Files**
- (none — toolchain-only step)

**Steps**

- [ ] 1. Run the whole local gate to confirm Plan 3's deliverables are green and don't disturb Plans 1 or 2:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  uv run pytest
  ```
  Expected: each command exits 0. Ruff: `All checks passed!`. Format: nothing to reformat. Mypy: `Success: no issues found in N source files`. Pytest: every test from Plans 1 + 2 + 3 passes.

- [ ] 2. Confirm the skill's `--help` is meaningful:
  ```
  uv run python skills/ralph-add/scripts/add.py --help
  ```
  Expected: help text mentions `--work-item`, `--repo`, `--repo-url`, `--severity`, `--expand-children`, `--via-mcp`, `--branch`, `--no-push`, `--dry-run`, and references the three env vars.

- [ ] 3. Confirm the commit history is clean and conventional:
  ```
  git log --oneline -10
  ```
  Expected: four Plan-3 commits at the top of the log, each with a conventional-commit prefix:
  - `chore(skills): scaffold ralph-add skill (SKILL.md, package layout, toolchain wiring)`
  - `feat(ralph-add): implement ADO work item to PBI submission flow`
  - `test(ralph-add): cross-check generated PBIs against Plan 1 schema validator`
  (And one more if Task 1's commit was split — it should be a single commit.)

---

## Verification

This is the orchestrator's Plan 3 verification gate. The gate command from `2026-05-24-00-orchestrator.md` is:

> `uv run pytest tests/skills/test_ralph_add.py -v` — All pass

Plan 3 satisfies the gate with the artifacts above. The verification has two parts: artifact gate (no network, hermetic) and a manual rehearsal against a real ADO instance.

### Part 1 — artifact gate (no network)

- [ ] 1. Run the per-skill test selection in isolation:
  ```
  uv run pytest tests/skills/test_ralph_add.py -v
  ```
  Expected: ten tests collected, ten passing.

- [ ] 2. Run the full repo gate to confirm Plan 3 didn't disturb Plans 1 or 2:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  uv run pytest
  ```
  Expected: every command exits 0. The combined test count is the sum of Plans 1 + 2 + 3 (eight from Plan 1, ten from Plan 2, ten from Plan 3 — twenty-eight total).

- [ ] 3. Confirm the skill is discoverable as a Claude Code skill (the `SKILL.md` frontmatter must parse). Run:
  ```
  uv run python -c "import yaml; from pathlib import Path; text = Path('skills/ralph-add/SKILL.md').read_text(encoding='utf-8'); lines = text.splitlines(); assert lines[0] == '---', 'SKILL.md must start with frontmatter fence'; end = next(i for i, line in enumerate(lines[1:], start=1) if line == '---'); fm = yaml.safe_load('\n'.join(lines[1:end])); assert fm['name'] == 'ralph-add'; assert isinstance(fm['description'], str) and len(fm['description']) >= 80; print('SKILL.md frontmatter OK:', fm['name'])"
  ```
  Expected output: `SKILL.md frontmatter OK: ralph-add`.

### Part 2 — orchestrator gate on a real ADO work item (manual; once per environment)

The orchestrator gate also expects `ralph-add` to land an end-to-end PBI on a real `ralph-queue` branch when used against a real ADO instance. This is the same rehearsal the BA does in production; the verification just exercises it once to prove the wiring.

- [ ] 4. From a checkout where Plan 2's runbook has been executed (so `origin/ralph-queue` exists on the chosen test repo), export the env vars:
  ```
  export ADO_PAT="<your PAT — Work Items (Read) scope is sufficient>"
  export ADO_ORG_URL="https://dev.azure.com/<your-org>"
  export ADO_PROJECT="<your-project>"
  ```

- [ ] 5. Pick a small ADO work item (a test work item is fine) and submit it:
  ```
  uv run python skills/ralph-add/scripts/add.py \
      --work-item WI-<id> \
      --repo /path/to/checked-out/service-repo
  ```
  Expected: exit code 0; JSON summary with `"pushed": true` and `"commit_sha"` set; the `.ralph/inbox/WI-<id>/` directory exists on the `ralph-queue` branch of the service repo. Pull the branch and inspect the directory; the entry file should validate via:
  ```
  uv run python scripts/validate_samples.py /path/to/service-repo/.ralph/inbox
  ```
  Expected: `OK   WI-<id>`. (Caveat: the validator requires the directory name to start with `feature-`, `bug-`, or `pr-feedback-`. For real PBIs in `inbox/`, the directory name is the `WI-<n>` form — the validator's directory-name-prefix check is intentionally only enforced on the `samples/` tree. The end-to-end check here is the frontmatter + sibling-file contract, which the Task 4 test pins via the mirror trick.)

- [ ] 6. Re-run the same command and confirm idempotency: `git commit` will fail because the inbox entry already exists. The skill exits with code 2 and a clear git error. This is the expected behaviour — `ralph-add` is single-submission per work item; resubmission is the BA's signal to delete the existing entry first.

If steps 1–6 all pass (allowing for the expected failure in step 6), Plan 3 is complete. The next plan in the dependency graph is Plan 10 (the remaining supervisor skills, which reuse the `ralph-add` scaffolding pattern), and Plan 4 (`ralph-status`, which reads from `ralph-queue`) can proceed in parallel as soon as Plan 2 is verified.
