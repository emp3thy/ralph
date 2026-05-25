# `ralph-add` Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first supervisor skill, `ralph-add`, as a **host-agnostic orchestrator** that delegates the host-specific "fetch the work item" step to a sibling skill (`workitem-fetch-github/` in Phase 1, `workitem-fetch-ado/` in Phase 2). Given a work item ID (e.g. `WI-1234` for ADO, `#42` or `42` for GitHub), `ralph-add` shells out to the staged `workitem-fetch/` skill, reads back a **normalised JSON blob** describing the work item, packages the result into a PBI directory using the conventions Plan 1 established, then commits and pushes that PBI directory to the `ralph-queue` branch of a target service repo. Supports two modes — `--repo <path>` against a pre-cloned working copy, or `--repo-url <url>` against a temporary clone the skill creates. Adds a `--expand-children` flag that walks the work item's child links and writes one child PBI per child (each frontmatter-tagged with `parent_id`). The skill includes a placeholder `--via-mcp` flag that raises `NotImplementedError` so the future MCP path is explicit but not built in v1.

**Architecture:** The work splits into a host-agnostic **orchestrator** (`skills/ralph-add/`) and one host-specific **fetcher** per supported git host (`skills/workitem-fetch-github/` for Phase 1, `skills/workitem-fetch-ado/` for Phase 2). The orchestrator never imports `ado_client`, `gh_client`, or any host SDK; it locates the staged fetcher on disk and invokes it via `subprocess.run`. The fetcher emits a single **normalised JSON document** on stdout (schema defined below). The orchestrator parses that JSON, decides PBI type/severity from the normalised fields, builds `PBI.md` / `BUG.md` / `PLAN.md` / `REPRODUCE.md` / `HISTORY.md` plus attachments, and runs the git commit/push. This shape is the load-bearing structural change relative to the previous draft of this plan: there are no `if host == "github"` branches anywhere in `ralph-add/scripts/add.py`.

**Tech Stack:** Python 3.12+, `uv`, `requests`, `responses` (tests), `pyyaml`, `pytest`, ruff, mypy strict, `git` (called via `subprocess`), GitHub REST API v3 (Phase 1), Azure DevOps REST API 7.1 (Phase 2). The Phase 1 fetcher reuses `scripts.gh_client` from Plan 2 (`RALPH_GIT_HOST=github` branch); the Phase 2 fetcher reuses `scripts.ado_client` from Plan 2.

---

## Phases

This plan is delivered in two phases, mirroring the host-selection architecture in `2026-05-24-00-orchestrator.md` ("Host selection architecture" section):

| Phase | What ships | Skills produced |
|---|---|---|
| **Phase 1 — GitHub (this plan, mandatory)** | Host-agnostic `ralph-add` orchestrator + `workitem-fetch-github/` fetcher. End-to-end submission works for GitHub Issues. | `skills/ralph-add/` (orchestrator), `skills/workitem-fetch-github/` (fetcher), `skills/workitem-fetch-mock/` (test fixture) |
| **Phase 2 — ADO (Ralph-built later, deferred)** | `workitem-fetch-ado/` fetcher, same JSON contract as Phase 1, calling `scripts.ado_client` for ADO REST. | `skills/workitem-fetch-ado/` |

Phase 2 tasks in this plan are clearly labelled and **DEFERRED** — they document the work but should not block Plan 3's completion. Phase 1 alone satisfies the orchestrator's Verification gate for Plan 3.

### Orchestrator / fetcher split — the rules

1. **`ralph-add` knows nothing about any git host.** Its `scripts/add.py` MUST NOT import `scripts.gh_client`, `scripts.ado_client`, `requests`, `responses`, or any host SDK. The orchestrator's tests mock the fetcher subprocess, not the host API.
2. **Each fetcher is host-pure.** It calls exactly one host's REST API. It writes one normalised JSON document to stdout. Progress goes to stderr. Exit 0 on success, 2 on validation/IO error.
3. **The fetcher is located by convention.** At runtime, `ralph-add` resolves the fetcher script via:
   - `RALPH_WORKITEM_FETCH_SCRIPT` env var, if set (used by tests + advanced overrides)
   - else: `~/.claude/skills/workitem-fetch/scripts/fetch.py` (the install location the executor's `host_select` stages from Plan 7)
   - else: `<repo>/skills/workitem-fetch-github/scripts/fetch.py` if `RALPH_GIT_HOST=github`; `<repo>/skills/workitem-fetch-ado/scripts/fetch.py` if `RALPH_GIT_HOST=ado` (dev fallback when running directly from the repo without staging)
   - else: error with code 2, message `"could not locate workitem-fetch fetcher; set RALPH_GIT_HOST or stage the skill first"`
4. **The JSON contract is the boundary.** Both fetchers produce the same JSON shape. If a field is not applicable to a host, the fetcher emits `null` or `""` or `[]` (per the schema), never omits the key.
5. **No fetcher uses `argparse` flags that the orchestrator wouldn't reasonably set.** The orchestrator passes only the work item id and (optionally) `--include-children`; everything else (auth, org, project, owner, repo) comes from the fetcher's own environment.

### Normalised work-item JSON schema

This schema is the contract both fetchers implement. It is defined as a Python `TypedDict` in `skills/workitem-fetch-github/scripts/schema.py` (Phase 1) and re-exported verbatim from `skills/workitem-fetch-ado/scripts/schema.py` (Phase 2). Documenting once, used twice:

```json
{
  "id": "WI-1234",
  "type": "feature",
  "severity": "normal",
  "title": "Add /healthz endpoint to service-auth",
  "body_markdown": "Platform requires every service to expose a liveness probe...",
  "acceptance_markdown": "- GET /healthz returns 200.\n- Body is JSON {\"status\":\"ok\"}.",
  "repro_steps_markdown": "",
  "attachments": [
    {
      "name": "design.png",
      "url": "https://user-images.githubusercontent.com/.../design.png",
      "content_base64": "iVBORw0KGgoAAAANSUhEUgAA..."
    }
  ],
  "child_ids": ["WI-9001", "WI-9002"],
  "parent_id": null,
  "source_url": "https://github.com/example-org/service-auth/issues/1234",
  "source_host": "github",
  "raw": {
    "work_item_type": "User Story",
    "labels": ["enhancement", "priority/normal"],
    "priority": null
  }
}
```

| Key | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Canonical Ralph PBI id form: `WI-<number>` for ADO, `WI-<number>` for GitHub (issue number; both hosts use the `WI-` prefix on disk for uniform queue naming). |
| `type` | enum (`feature`\|`bug`) | yes | PBI type. `pr-feedback` is NEVER produced here (those come from the executor's sweep). |
| `severity` | enum (`critical`\|`high`\|`normal`\|`low`) | yes | Derived from host-specific signals (labels on GitHub, priority/severity fields on ADO). |
| `title` | string | yes | One-line work item title. |
| `body_markdown` | string | yes | Body in markdown. GitHub returns markdown natively; ADO HTML is converted by the ADO fetcher. May be empty string. |
| `acceptance_markdown` | string | yes | Optional acceptance criteria, markdown. Empty string if none. |
| `repro_steps_markdown` | string | yes | Optional reproducer (used for bug PBIs). Empty string if none. |
| `attachments` | array | yes | Each: `{name, url, content_base64}`. `content_base64` is the base64-encoded binary body of the attachment (the fetcher downloads it). May be empty array. |
| `child_ids` | array of string | yes | List of child PBI ids (same `WI-<n>` form). Empty array if none. |
| `parent_id` | string \| null | yes | Parent PBI id when emitted as a child during `--expand-children`. The fetcher itself never sets this; the orchestrator stamps it into the JSON before writing the child PBI. The schema includes it so `parent_id` flows through one well-typed channel. |
| `source_url` | string | yes | Permalink to the work item on its host. |
| `source_host` | enum (`github`\|`ado`) | yes | Which fetcher produced this JSON. |
| `raw` | object | yes | Host-specific extras for debugging. Keys are loose; `ralph-add` does not consume them. |

### Label-to-PBI-type and label-to-severity mapping (GitHub, Phase 1)

The GitHub fetcher applies these rules in order. The first match wins. The mapping is documented inline in `workitem-fetch-github/scripts/fetch.py`:

**Type:**
| Label (case-insensitive) | PBI `type` |
|---|---|
| `bug` | `bug` |
| `enhancement`, `feature` | `feature` |
| (none of the above) | `feature` (default) |

**Severity:**
| Label (case-insensitive) | PBI `severity` |
|---|---|
| `priority/critical`, `severity/critical`, `critical` | `critical` |
| `priority/high`, `severity/high`, `high` | `high` |
| `priority/low`, `severity/low`, `low` | `low` |
| `priority/normal`, `severity/normal`, `normal`, (or no priority label) | `normal` (default) |

### Label-to-PBI-type and label-to-severity mapping (ADO, Phase 2)

Documented for completeness; implemented in Phase 2:

| ADO `System.WorkItemType` | PBI `type` |
|---|---|
| `Bug` | `bug` |
| anything else | `feature` |

Severity is derived from `Microsoft.VSTS.Common.Severity` (Bug) or `Microsoft.VSTS.Common.Priority` (1=critical, 2=high, 3=normal, 4=low) — the existing logic from the previous draft of this plan, relocated into the ADO fetcher.

---

## File Structure

| Path | Phase | Responsibility |
|---|---|---|
| `skills/ralph-add/SKILL.md` | 1 | Skill frontmatter + body explaining the orchestrator. Documents that it delegates the fetch step to a sibling `workitem-fetch/` skill. |
| `skills/ralph-add/scripts/__init__.py` | 1 | Empty package marker. |
| `skills/ralph-add/scripts/add.py` | 1 | The host-agnostic orchestrator. Argparse CLI; resolves the staged fetcher; runs `subprocess.run([python, fetcher, "--work-item", id, ...])`; parses the normalised JSON; builds PBI directory; commits + pushes. |
| `skills/__init__.py` | 1 | Empty package marker. |
| `skills/workitem-fetch-github/SKILL.md` | 1 | Skill frontmatter + body documenting the GitHub fetcher and its env contract. |
| `skills/workitem-fetch-github/scripts/__init__.py` | 1 | Empty package marker. |
| `skills/workitem-fetch-github/scripts/schema.py` | 1 | The `WorkItemJson` and `AttachmentJson` `TypedDict` definitions. Both fetchers import these (Phase 2 fetcher re-exports). |
| `skills/workitem-fetch-github/scripts/fetch.py` | 1 | GitHub Issues fetcher. Calls `GET /repos/{owner}/{repo}/issues/{number}` via `scripts.gh_client`; downloads attachment binaries; emits the normalised JSON on stdout. |
| `skills/workitem-fetch-mock/SKILL.md` | 1 | Test-fixture skill used by `ralph-add`'s orchestrator tests. Frontmatter only; the fetcher script is generated in-place by tests using `monkeypatch` or a tmp script — see Task 5 for the test pattern. |
| `skills/workitem-fetch-ado/SKILL.md` | 2 | (Deferred) Skill frontmatter + body documenting the ADO fetcher. |
| `skills/workitem-fetch-ado/scripts/__init__.py` | 2 | (Deferred) Empty package marker. |
| `skills/workitem-fetch-ado/scripts/schema.py` | 2 | (Deferred) `from skills.workitem_fetch_github.scripts.schema import *` re-export. |
| `skills/workitem-fetch-ado/scripts/fetch.py` | 2 | (Deferred) ADO fetcher. Calls `scripts.ado_client.AdoClient.get("wit/workitems/{id}?$expand=relations")`; downloads attachments; converts HTML to markdown; emits the normalised JSON on stdout. This is the relocation of the ADO fetch logic from the previous draft of this plan. |
| `tests/skills/__init__.py` | 1 | Empty package marker. |
| `tests/skills/test_workitem_fetch_github.py` | 1 | Pytest tests for the GitHub fetcher. Mocks the GitHub REST surface with `responses` and asserts the produced JSON matches the schema. |
| `tests/skills/test_ralph_add.py` | 1 | Pytest tests for the orchestrator. Uses a mock fetcher (a tiny Python script written into `tmp_path`) so the orchestrator runs without touching any real host. Verifies feature/bug/expand-children/dry-run/severity-override/no-push/missing-repo flows. |
| `tests/skills/test_workitem_fetch_ado.py` | 2 | (Deferred) Pytest tests for the ADO fetcher. The body of these tests is preserved from the previous draft of this plan; only the import path and the JSON-output assertion shape change. |
| `pyproject.toml` | 1 | Modified to include `skills` and `tests/skills` in `[tool.mypy].files` and `[tool.pytest.ini_options].testpaths`. |

---

## Phase 1 — GitHub fetcher + host-agnostic orchestrator (this is what Plan 3 ships)

### Task 1 — Wire `skills/` into the toolchain and scaffold the two skill packages

**Files**
- Modify: `pyproject.toml`
- Create: `skills/__init__.py`
- Create: `skills/ralph-add/SKILL.md`
- Create: `skills/ralph-add/scripts/__init__.py`
- Create: `skills/workitem-fetch-github/SKILL.md`
- Create: `skills/workitem-fetch-github/scripts/__init__.py`
- Create: `skills/workitem-fetch-mock/SKILL.md`
- Create: `tests/skills/__init__.py`

**Steps**

- [ ] 1. Confirm Plans 1 and 2 are merged. Run:
  ```
  uv run pytest tests/test_workspace_samples.py tests/test_ado_client.py tests/test_gh_client.py tests/test_setup_ralph_queue.py -v
  ```
  Expected: every test passes. If any fail, STOP — Plan 3 depends on `scripts.gh_client` (Plan 2, Phase 1 deliverable) and the canonical sample conventions (Plan 1). (`tests/test_gh_client.py` only exists after Plan 2 Phase 1 is merged; if it doesn't, treat that as the missing dependency.)

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
  Expected: no errors. (Ruff doesn't lint `pyproject.toml`; the command confirms the file is well-formed TOML.)

- [ ] 3. Create `skills/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 4. Create `skills/ralph-add/scripts/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```
  Note: the directory name is `ralph-add` (Claude Code skill convention), which is NOT a valid Python identifier. Tests therefore import the script via `importlib.util.spec_from_file_location` (see Task 4).

- [ ] 5. Create `skills/workitem-fetch-github/scripts/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 6. Create `tests/skills/__init__.py` containing exactly:
  ```python
  """Empty package marker."""
  ```

- [ ] 7. Create `skills/ralph-add/SKILL.md` with the exact content below. The frontmatter is the contract Claude Code's Skill tool reads at skill-discovery time:
  ```markdown
  ---
  name: ralph-add
  description: Submit a work item to a service repo's Ralph queue. Host-agnostic orchestrator. Calls the staged workitem-fetch skill (GitHub or ADO) to fetch the work item title, body, acceptance criteria, and attachments, packages them into a PBI directory using the canonical Ralph conventions, then commits and pushes the PBI to the ralph-queue branch of the target service repo. Optional --expand-children expands a parent work item into one child PBI per linked child work item.
  ---

  # ralph-add

  ## What this skill does

  `ralph-add` is the BA / PM submission surface for Ralph. It is **host-agnostic** — it does not call GitHub or Azure DevOps directly. Instead, it shells out to a sibling **`workitem-fetch/`** skill that the executor (Plan 7) has staged from one of `workitem-fetch-github/` or `workitem-fetch-ado/` based on `RALPH_GIT_HOST`. The fetcher returns a normalised JSON document; `ralph-add` packages that into a PBI directory and pushes it to `ralph-queue`.

  Given a work item id (e.g. `WI-1234` for ADO, `42` or `#42` for GitHub) and a target service repo, `ralph-add`:

  1. Locates the staged `workitem-fetch/` skill (see [How it finds the fetcher](#how-it-finds-the-fetcher) below).
  2. Invokes the fetcher via `subprocess.run`, passing the work item id.
  3. Parses the fetcher's stdout as JSON, validates the shape against the normalised schema.
  4. Classifies the PBI type and severity from the normalised `type` / `severity` fields (the fetcher has already done the host-specific derivation).
  5. Writes a PBI directory under `.ralph/inbox/<WI-id>/` on the `ralph-queue` branch of the target repo, following the conventions in `docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md`.
  6. Commits the new directory with a conventional-commit message and pushes the `ralph-queue` branch.

  ## When to use it

  Use `ralph-add` when a human (BA, PM, triager) wants to route a work item to a service's Ralph queue. This is the canonical entry point; never hand-edit PBI directories on `ralph-queue` directly.

  ## Inputs

  | Flag | Required | Description |
  |---|---|---|
  | `--work-item <id>` | yes | Work item id. ADO form: bare integer (`1234`) or `WI-` prefixed (`WI-1234`). GitHub form: bare integer (`42`) or `#`-prefixed (`#42`). |
  | `--repo <path>` | one of `--repo` / `--repo-url` | Path to an existing checkout of the target service repo. The skill switches that checkout onto `ralph-queue`, mutates it, and pushes. |
  | `--repo-url <url>` | one of `--repo` / `--repo-url` | Git clone URL for the target service repo. Reserved — not implemented in v1 (the orchestrator exits with code 2 and a clear message). |
  | `--severity <level>` | no | Override the severity returned by the fetcher (`critical`, `high`, `normal`, `low`). |
  | `--expand-children` | no | If the work item has child links (issue references on GitHub; Hierarchy-Forward relations on ADO), write one PBI per child instead of one PBI for the parent. Each child PBI carries `parent_id: <parent-WI-id>` in its frontmatter. |
  | `--via-mcp` | no | Reserved for v2. Raises `NotImplementedError` (exit 2) in v1. |
  | `--branch <name>` | no | Queue branch name. Default: `ralph-queue` (overridable via `RALPH_QUEUE_BRANCH` env var). |
  | `--no-push` | no | Commit the PBI directory but do not push to the remote. |
  | `--dry-run` | no | Compute everything but do not write files, commit, or push. Prints the JSON summary describing what would have happened. |

  ## Environment variables

  `ralph-add` reads only one env var directly:

  | Variable | Purpose |
  |---|---|
  | `RALPH_QUEUE_BRANCH` | Default queue branch name; overridden by `--branch`. Optional; defaults to `ralph-queue`. |
  | `RALPH_WORKITEM_FETCH_SCRIPT` | Optional override pointing at the fetcher script (used by tests). |
  | `RALPH_GIT_HOST` | Used only as a fallback hint when no staged fetcher is found (see "How it finds the fetcher"). |

  All host-specific env vars (`GH_TOKEN`, `GH_OWNER`, `ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT`) are read by the **fetcher**, not by `ralph-add`. `ralph-add` does not validate them; it inherits them into the fetcher subprocess's environment.

  ## How it finds the fetcher

  `ralph-add` resolves the fetcher script in this order, taking the first that exists:

  1. `RALPH_WORKITEM_FETCH_SCRIPT` env var (used by tests and advanced overrides).
  2. `~/.claude/skills/workitem-fetch/scripts/fetch.py` — the install location the executor's host-select (Plan 7) stages from one of `workitem-fetch-github/` or `workitem-fetch-ado/`.
  3. If `RALPH_GIT_HOST=github`: `<ralph-repo-root>/skills/workitem-fetch-github/scripts/fetch.py` (dev fallback).
  4. If `RALPH_GIT_HOST=ado`: `<ralph-repo-root>/skills/workitem-fetch-ado/scripts/fetch.py` (dev fallback, Phase 2).
  5. Otherwise: exit 2 with the message `could not locate workitem-fetch fetcher; set RALPH_GIT_HOST or stage the skill first`.

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
    "dry_run": false,
    "source_host": "github"
  }
  ```

  Progress messages go to stderr; the JSON on stdout is machine-readable and is the source of truth for downstream automation.

  ## How it is invoked

  ```bash
  uv run python skills/ralph-add/scripts/add.py \
      --work-item 1234 \
      --repo /path/to/service-auth
  ```

  Tests for the script live at `tests/skills/test_ralph_add.py` and use a mock fetcher script written into the test's `tmp_path` so the orchestrator is exercised without touching any real host.

  ## What this skill does NOT do

  - It does not call any git host's REST API directly. The fetcher does that.
  - It does not create or modify the `ralph-queue` branch's policy (Plan 2 — see `docs/runbooks/ralph-queue-setup.md`).
  - It does not invoke Ralph or `claude -p`; it only writes to the queue.
  - It does not handle PR-feedback PBIs — those are generated by the executor's sweep (Plan 8).
  - It does not edit the executor-managed frontmatter fields (`status`, `attempts`) beyond setting their initial values (`status: inbox`, `attempts: 0`).
  ```

- [ ] 8. Create `skills/workitem-fetch-github/SKILL.md` with the exact content below:
  ```markdown
  ---
  name: workitem-fetch-github
  description: Fetch a GitHub Issue and emit a normalised work-item JSON document on stdout. Used by ralph-add as the GitHub-specific fetcher. Reads GH_TOKEN and GH_OWNER from the environment; the repo name is passed as a flag or derived from --repo. Downloads inline images and attachments as base64. Reads issue labels to derive PBI type (bug / feature) and severity (critical / high / normal / low).
  ---

  # workitem-fetch-github

  ## What this skill does

  Fetches one GitHub Issue and emits the **normalised work-item JSON** schema documented in `2026-05-24-03-ralph-add-skill.md` (and replicated in `skills/workitem-fetch-github/scripts/schema.py`). This is the Phase 1 GitHub-specific fetcher used by the host-agnostic `ralph-add` orchestrator.

  The fetcher is host-pure: it knows about GitHub and only GitHub. The sibling `workitem-fetch-ado/` skill (Phase 2) provides the same surface for Azure DevOps.

  ## Inputs

  | Flag | Required | Description |
  |---|---|---|
  | `--work-item <id>` | yes | Issue number (bare integer `42` or `#42`). |
  | `--repo <owner/name>` | no | `owner/name` slug. If omitted, derives from `GH_OWNER` env + the issue's containing repo from `--repo-name`. |
  | `--repo-name <name>` | no | Repo name only (combined with `GH_OWNER` env if `--repo` not given). |
  | `--include-children` | no | Walk linked sub-issues (referenced as `#N` in the issue body or as GitHub task-list items) and emit their ids in `child_ids`. Does NOT recursively fetch them — `ralph-add` does that one level at a time. |

  ## Environment variables

  | Variable | Required | Purpose |
  |---|---|---|
  | `GH_TOKEN` | yes | GitHub PAT (or fine-grained token) with `repo` (private) or `public_repo` (public) scope. |
  | `GH_OWNER` | when `--repo` not given | GitHub org or user (e.g. `example-org`). |

  ## Output

  A single JSON document on stdout matching the normalised schema. Progress on stderr. Exit 0 on success, 2 on validation/IO error.

  ## GitHub REST endpoints used

  | Operation | Endpoint |
  |---|---|
  | Fetch issue | `GET /repos/{owner}/{repo}/issues/{number}` |
  | Download attachment | `GET <asset-url>` (the URL appears inline in the issue body's `![](...)` markdown image tags or in the issue's `body` field as plain URLs). The fetcher resolves each, downloads the bytes, and base64-encodes them. |

  ## Label-to-PBI-type / severity mapping

  See the table in `2026-05-24-03-ralph-add-skill.md`. The fetcher applies it in order; first match wins; default `feature` / `normal`.

  ## How it is invoked

  ```bash
  GH_TOKEN=ghp_... GH_OWNER=example-org \
    uv run python skills/workitem-fetch-github/scripts/fetch.py \
      --work-item 42 \
      --repo example-org/service-auth
  ```
  ```

- [ ] 9. Create `skills/workitem-fetch-mock/SKILL.md` (a test-only fixture skill, frontmatter only — the fetcher script is generated by tests in `tmp_path`):
  ```markdown
  ---
  name: workitem-fetch-mock
  description: Test fixture only. Not a real fetcher. The mock fetcher script is generated into a tmp_path by the orchestrator tests; this SKILL.md exists so the package layout is consistent and discoverable.
  ---

  # workitem-fetch-mock

  This is a placeholder for the test-fixture skill. The orchestrator tests in `tests/skills/test_ralph_add.py` create a temporary fetcher script in `tmp_path` and point `RALPH_WORKITEM_FETCH_SCRIPT` at it. There is no real `fetch.py` checked in here.
  ```

- [ ] 10. Stage and commit:
  ```
  git add pyproject.toml \
    skills/__init__.py \
    skills/ralph-add/SKILL.md skills/ralph-add/scripts/__init__.py \
    skills/workitem-fetch-github/SKILL.md skills/workitem-fetch-github/scripts/__init__.py \
    skills/workitem-fetch-mock/SKILL.md \
    tests/skills/__init__.py
  git commit -m "chore(skills): scaffold host-agnostic ralph-add + workitem-fetch-github + mock fixture"
  ```

---

### Task 2 — Define the normalised work-item schema (TypedDict)

**Files**
- Create: `skills/workitem-fetch-github/scripts/schema.py`

**Steps**

- [ ] 1. Write `skills/workitem-fetch-github/scripts/schema.py` with the exact content below. This is the single source of truth for the normalised JSON shape. The Phase 2 ADO fetcher re-exports from here:
  ```python
  """Normalised work-item JSON schema.

  Both the GitHub fetcher (Phase 1) and the ADO fetcher (Phase 2) emit this
  exact shape. ``ralph-add``'s orchestrator parses it without caring which
  host produced it.
  """
  from __future__ import annotations

  from typing import Literal, TypedDict


  PBIType = Literal["feature", "bug"]
  Severity = Literal["critical", "high", "normal", "low"]
  SourceHost = Literal["github", "ado"]


  class AttachmentJson(TypedDict):
      """One attached file. ``content_base64`` is the raw bytes encoded."""

      name: str
      url: str
      content_base64: str


  class WorkItemJson(TypedDict):
      """The normalised work-item document emitted by every fetcher."""

      id: str
      type: PBIType
      severity: Severity
      title: str
      body_markdown: str
      acceptance_markdown: str
      repro_steps_markdown: str
      attachments: list[AttachmentJson]
      child_ids: list[str]
      parent_id: str | None
      source_url: str
      source_host: SourceHost
      raw: dict[str, object]


  REQUIRED_KEYS: tuple[str, ...] = (
      "id",
      "type",
      "severity",
      "title",
      "body_markdown",
      "acceptance_markdown",
      "repro_steps_markdown",
      "attachments",
      "child_ids",
      "parent_id",
      "source_url",
      "source_host",
      "raw",
  )
  ALLOWED_TYPES: tuple[str, ...] = ("feature", "bug")
  ALLOWED_SEVERITIES: tuple[str, ...] = ("critical", "high", "normal", "low")
  ALLOWED_HOSTS: tuple[str, ...] = ("github", "ado")


  def validate(document: object) -> list[str]:
      """Return a list of validation errors. Empty list means valid."""
      errors: list[str] = []
      if not isinstance(document, dict):
          return [f"document must be a JSON object, got {type(document).__name__}"]

      for key in REQUIRED_KEYS:
          if key not in document:
              errors.append(f"missing required key: {key}")
      if errors:
          return errors

      if document["type"] not in ALLOWED_TYPES:
          errors.append(f"type must be one of {ALLOWED_TYPES}, got {document['type']!r}")
      if document["severity"] not in ALLOWED_SEVERITIES:
          errors.append(
              f"severity must be one of {ALLOWED_SEVERITIES}, got {document['severity']!r}"
          )
      if document["source_host"] not in ALLOWED_HOSTS:
          errors.append(
              f"source_host must be one of {ALLOWED_HOSTS}, got {document['source_host']!r}"
          )
      if not isinstance(document["attachments"], list):
          errors.append("attachments must be a list")
      if not isinstance(document["child_ids"], list):
          errors.append("child_ids must be a list")
      parent_id = document["parent_id"]
      if parent_id is not None and not isinstance(parent_id, str):
          errors.append("parent_id must be a string or null")
      return errors
  ```

- [ ] 2. Smoke-check the module is importable and lints clean:
  ```
  uv run python -c "from skills.workitem_fetch_github.scripts.schema import WorkItemJson, validate, REQUIRED_KEYS; print('keys:', len(REQUIRED_KEYS))"
  ```
  Note: this will FAIL because of the hyphen in `workitem-fetch-github`. That's expected — the package marker is for static tooling; downstream code imports via `importlib.util.spec_from_file_location`. Instead verify:
  ```
  uv run mypy skills/workitem-fetch-github/scripts/schema.py
  uv run ruff check skills/workitem-fetch-github/scripts/schema.py
  ```
  Expected: both clean.

- [ ] 3. Commit:
  ```
  git add skills/workitem-fetch-github/scripts/schema.py
  git commit -m "feat(workitem-fetch): define normalised work-item JSON schema"
  ```

---

### Task 3 — Write the failing test for the GitHub fetcher

**Files**
- Create: `tests/skills/test_workitem_fetch_github.py`

**Steps**

- [ ] 1. Write `tests/skills/test_workitem_fetch_github.py` with the exact content below. The test imports the entry script via `importlib`; the import fails for the expected reason (script doesn't exist yet):
  ```python
  """Tests for the ``workitem-fetch-github`` skill's fetcher.

  The fetcher lives at ``skills/workitem-fetch-github/scripts/fetch.py``.
  All GitHub REST endpoints are mocked with ``responses``; no network.
  """
  from __future__ import annotations

  import base64
  import importlib.util
  import json
  from collections.abc import Iterator
  from pathlib import Path
  from types import ModuleType

  import pytest
  import responses


  REPO_ROOT = Path(__file__).resolve().parents[2]
  SCRIPT_PATH = REPO_ROOT / "skills" / "workitem-fetch-github" / "scripts" / "fetch.py"

  GH_API = "https://api.github.com"
  OWNER = "example-org"
  REPO_NAME = "service-auth"
  TOKEN = "ghp_fake_token"
  ISSUE_NUMBER = 42
  CHILD_A = 101
  CHILD_B = 102


  @pytest.fixture(scope="module")
  def fetch_module() -> ModuleType:
      """Load ``skills/workitem-fetch-github/scripts/fetch.py`` as a module."""
      assert SCRIPT_PATH.is_file(), f"missing fetcher script at {SCRIPT_PATH}"
      spec = importlib.util.spec_from_file_location("workitem_fetch_github", SCRIPT_PATH)
      assert spec is not None and spec.loader is not None
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      return module


  @pytest.fixture
  def env(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("GH_TOKEN", TOKEN)
      monkeypatch.setenv("GH_OWNER", OWNER)


  def _issue_url(number: int) -> str:
      return f"{GH_API}/repos/{OWNER}/{REPO_NAME}/issues/{number}"


  def _register_feature_issue() -> None:
      responses.add(
          responses.GET,
          _issue_url(ISSUE_NUMBER),
          json={
              "number": ISSUE_NUMBER,
              "title": "Add /healthz endpoint to service-auth",
              "body": (
                  "Platform requires every service to expose a liveness probe "
                  "at `GET /healthz`.\n\n"
                  "## Acceptance criteria\n\n"
                  "- GET /healthz returns 200.\n"
                  "- Body is JSON `{\"status\":\"ok\"}`.\n\n"
                  "![design](https://user-images.githubusercontent.com/1/design.png)\n"
              ),
              "labels": [
                  {"name": "enhancement"},
                  {"name": "priority/normal"},
              ],
              "html_url": (
                  f"https://github.com/{OWNER}/{REPO_NAME}/issues/{ISSUE_NUMBER}"
              ),
              "user": {"login": "someone"},
          },
          status=200,
      )
      responses.add(
          responses.GET,
          "https://user-images.githubusercontent.com/1/design.png",
          body=b"\x89PNG\r\n\x1a\nfake-png-bytes",
          status=200,
          content_type="image/png",
      )


  def _register_bug_issue() -> None:
      responses.add(
          responses.GET,
          _issue_url(ISSUE_NUMBER),
          json={
              "number": ISSUE_NUMBER,
              "title": "service-auth pod crashloops on deploy",
              "body": (
                  "AccessDenied calling STS via IRSA.\n\n"
                  "## Reproduce\n\n"
                  "1. kubectl get pods\n"
                  "2. Observe CrashLoopBackOff\n"
              ),
              "labels": [
                  {"name": "bug"},
                  {"name": "priority/critical"},
              ],
              "html_url": (
                  f"https://github.com/{OWNER}/{REPO_NAME}/issues/{ISSUE_NUMBER}"
              ),
              "user": {"login": "someone"},
          },
          status=200,
      )


  def _register_issue_with_children() -> None:
      responses.add(
          responses.GET,
          _issue_url(ISSUE_NUMBER),
          json={
              "number": ISSUE_NUMBER,
              "title": "Onboarding flow rewrite",
              "body": (
                  "Parent feature.\n\n"
                  "## Tasks\n\n"
                  f"- [ ] #{CHILD_A}\n"
                  f"- [ ] #{CHILD_B}\n"
              ),
              "labels": [{"name": "enhancement"}],
              "html_url": (
                  f"https://github.com/{OWNER}/{REPO_NAME}/issues/{ISSUE_NUMBER}"
              ),
              "user": {"login": "someone"},
          },
          status=200,
      )


  # ----------------------------------------------------------------------
  # Tests
  # ----------------------------------------------------------------------

  @responses.activate
  def test_feature_issue_produces_normalised_feature_json(
      env: None,
      fetch_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_feature_issue()
      exit_code = fetch_module.main(
          [
              "--work-item",
              str(ISSUE_NUMBER),
              "--repo",
              f"{OWNER}/{REPO_NAME}",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["id"] == f"WI-{ISSUE_NUMBER}"
      assert payload["type"] == "feature"
      assert payload["severity"] == "normal"
      assert payload["title"] == "Add /healthz endpoint to service-auth"
      assert "/healthz" in payload["body_markdown"]
      assert payload["source_host"] == "github"
      assert payload["source_url"].endswith(f"/issues/{ISSUE_NUMBER}")
      assert payload["parent_id"] is None
      assert payload["child_ids"] == []
      assert len(payload["attachments"]) == 1
      att = payload["attachments"][0]
      assert att["name"] == "design.png"
      decoded = base64.b64decode(att["content_base64"])
      assert decoded.startswith(b"\x89PNG")


  @responses.activate
  def test_bug_issue_produces_normalised_bug_json(
      env: None,
      fetch_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_bug_issue()
      exit_code = fetch_module.main(
          [
              "--work-item",
              f"#{ISSUE_NUMBER}",
              "--repo",
              f"{OWNER}/{REPO_NAME}",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["type"] == "bug"
      assert payload["severity"] == "critical"
      assert "AccessDenied" in payload["body_markdown"]
      assert "kubectl get pods" in payload["repro_steps_markdown"]


  @responses.activate
  def test_include_children_populates_child_ids(
      env: None,
      fetch_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_issue_with_children()
      exit_code = fetch_module.main(
          [
              "--work-item",
              str(ISSUE_NUMBER),
              "--repo",
              f"{OWNER}/{REPO_NAME}",
              "--include-children",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["child_ids"] == [f"WI-{CHILD_A}", f"WI-{CHILD_B}"]


  def test_missing_token_exits_nonzero(
      monkeypatch: pytest.MonkeyPatch,
      fetch_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      monkeypatch.delenv("GH_TOKEN", raising=False)
      monkeypatch.setenv("GH_OWNER", OWNER)
      exit_code = fetch_module.main(
          [
              "--work-item",
              str(ISSUE_NUMBER),
              "--repo",
              f"{OWNER}/{REPO_NAME}",
          ]
      )
      assert exit_code == 2
      assert "GH_TOKEN" in capsys.readouterr().err


  @responses.activate
  def test_schema_validation_runs_before_emit(
      env: None,
      fetch_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      """The fetcher MUST emit a document that ``schema.validate`` accepts."""
      _register_feature_issue()
      exit_code = fetch_module.main(
          [
              "--work-item",
              str(ISSUE_NUMBER),
              "--repo",
              f"{OWNER}/{REPO_NAME}",
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      # Import the schema validator directly.
      schema_path = (
          REPO_ROOT / "skills" / "workitem-fetch-github" / "scripts" / "schema.py"
      )
      spec = importlib.util.spec_from_file_location("wi_schema", schema_path)
      assert spec is not None and spec.loader is not None
      schema_mod = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(schema_mod)
      errors = schema_mod.validate(payload)
      assert errors == [], f"fetcher emitted invalid document: {errors}"
  ```

- [ ] 2. Run the test; it must fail because the fetcher script does not exist:
  ```
  uv run pytest tests/skills/test_workitem_fetch_github.py -v
  ```
  Expected: `AssertionError: missing fetcher script at <SCRIPT_PATH>`. This is the red step.

- [ ] 3. Do NOT commit yet — the failing test is consumed in Task 4.

---

### Task 4 — Implement the GitHub fetcher

**Files**
- Create: `skills/workitem-fetch-github/scripts/fetch.py`

**Steps**

- [ ] 1. Write `skills/workitem-fetch-github/scripts/fetch.py` with the exact content below. The script imports `scripts.gh_client` (Plan 2) by manipulating `sys.path` so it works both as a module and when invoked directly:
  ```python
  """``workitem-fetch-github`` skill entry point.

  Fetches one GitHub Issue and emits the normalised work-item JSON document
  (defined in ``schema.py``) on stdout. Host-pure: knows nothing about ADO.
  """
  from __future__ import annotations

  import argparse
  import base64
  import importlib.util
  import json
  import os
  import re
  import sys
  from pathlib import Path
  from typing import Any

  # Make ``scripts.gh_client`` (Plan 2) importable when invoked directly.
  _REPO_ROOT = Path(__file__).resolve().parents[3]
  if str(_REPO_ROOT) not in sys.path:
      sys.path.insert(0, str(_REPO_ROOT))

  from scripts.gh_client import GhClient, GhError  # noqa: E402

  # Load the local ``schema`` module without going through the dashed package name.
  _SCHEMA_PATH = Path(__file__).with_name("schema.py")
  _spec = importlib.util.spec_from_file_location("wi_schema", _SCHEMA_PATH)
  assert _spec is not None and _spec.loader is not None
  _schema = importlib.util.module_from_spec(_spec)
  _spec.loader.exec_module(_schema)
  validate = _schema.validate  # type: ignore[attr-defined]


  # ----------------------------------------------------------------------
  # Label-to-type / label-to-severity mapping (documented in Plan 3 head)
  # ----------------------------------------------------------------------

  TYPE_LABELS_BUG: frozenset[str] = frozenset({"bug"})
  TYPE_LABELS_FEATURE: frozenset[str] = frozenset({"enhancement", "feature"})

  SEVERITY_LABELS: dict[str, str] = {
      # critical
      "priority/critical": "critical",
      "severity/critical": "critical",
      "critical": "critical",
      # high
      "priority/high": "high",
      "severity/high": "high",
      "high": "high",
      # low
      "priority/low": "low",
      "severity/low": "low",
      "low": "low",
      # normal
      "priority/normal": "normal",
      "severity/normal": "normal",
      "normal": "normal",
  }

  ACCEPTANCE_HEADING_RE = re.compile(
      r"^\s*##+\s*(acceptance criteria|acceptance)\s*$",
      flags=re.IGNORECASE | re.MULTILINE,
  )
  REPRO_HEADING_RE = re.compile(
      r"^\s*##+\s*(reproduce|reproduction|steps to reproduce|repro)\s*$",
      flags=re.IGNORECASE | re.MULTILINE,
  )
  NEXT_HEADING_RE = re.compile(r"^\s*##+\s+\S", flags=re.MULTILINE)
  IMAGE_MD_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>https?://[^)]+)\)")
  TASK_REF_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*#(?P<num>\d+)", flags=re.MULTILINE)


  class _FatalError(RuntimeError):
      """Raised internally to signal a clean exit with code 2."""


  # ----------------------------------------------------------------------
  # Arg parsing + env
  # ----------------------------------------------------------------------

  def _parse_args(argv: list[str]) -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          prog="workitem-fetch-github",
          description=(
              "Fetch a GitHub Issue and emit a normalised work-item JSON "
              "document on stdout. Requires GH_TOKEN (and GH_OWNER if --repo "
              "is not given) in the environment."
          ),
      )
      parser.add_argument("--work-item", required=True, help="Issue number (e.g. 42 or #42).")
      parser.add_argument(
          "--repo",
          help="Repo slug 'owner/name'. If omitted, uses GH_OWNER + --repo-name.",
      )
      parser.add_argument("--repo-name", help="Repo name (combined with GH_OWNER).")
      parser.add_argument(
          "--include-children",
          action="store_true",
          help="Populate child_ids from task-list references (- [ ] #N).",
      )
      return parser.parse_args(argv)


  def _require_env(name: str) -> str:
      value = os.environ.get(name, "").strip()
      if not value:
          raise _FatalError(f"environment variable {name} is required")
      return value


  def _resolve_repo(args: argparse.Namespace) -> tuple[str, str]:
      if args.repo:
          parts = args.repo.split("/", 1)
          if len(parts) != 2 or not all(parts):
              raise _FatalError(f"--repo must be 'owner/name', got {args.repo!r}")
          return parts[0], parts[1]
      owner = _require_env("GH_OWNER")
      if not args.repo_name:
          raise _FatalError("either --repo OR --repo-name (with GH_OWNER env) must be given")
      return owner, args.repo_name


  def _parse_issue_number(raw: str) -> int:
      raw = raw.strip().lstrip("#")
      if raw.lower().startswith("wi-"):
          raw = raw[3:]
      if not raw.isdigit():
          raise _FatalError(f"issue number {raw!r} must be a positive integer")
      value = int(raw)
      if value <= 0:
          raise _FatalError(f"issue number must be > 0, got {value}")
      return value


  # ----------------------------------------------------------------------
  # Type + severity derivation
  # ----------------------------------------------------------------------

  def _classify_type(labels: list[str]) -> str:
      lowered = [label.lower() for label in labels]
      for label in lowered:
          if label in TYPE_LABELS_BUG:
              return "bug"
      for label in lowered:
          if label in TYPE_LABELS_FEATURE:
              return "feature"
      return "feature"


  def _classify_severity(labels: list[str]) -> str:
      for label in labels:
          mapped = SEVERITY_LABELS.get(label.lower())
          if mapped:
              return mapped
      return "normal"


  # ----------------------------------------------------------------------
  # Body parsing
  # ----------------------------------------------------------------------

  def _extract_section(body: str, heading_re: re.Pattern[str]) -> str:
      match = heading_re.search(body)
      if not match:
          return ""
      start = match.end()
      next_match = NEXT_HEADING_RE.search(body, pos=start)
      end = next_match.start() if next_match else len(body)
      return body[start:end].strip()


  def _extract_children(body: str) -> list[int]:
      return [int(m.group("num")) for m in TASK_REF_RE.finditer(body)]


  def _extract_attachment_urls(body: str) -> list[tuple[str, str]]:
      """Return [(filename, url)] for markdown image references in the body."""
      results: list[tuple[str, str]] = []
      for match in IMAGE_MD_RE.finditer(body):
          url = match.group("url")
          name = url.rsplit("/", 1)[-1].split("?", 1)[0] or "attachment"
          results.append((name, url))
      return results


  # ----------------------------------------------------------------------
  # Orchestration
  # ----------------------------------------------------------------------

  def _download(client: GhClient, url: str) -> bytes:
      response = client.session.get(url, timeout=client.timeout)
      if not (200 <= response.status_code < 300):
          raise GhError(status_code=response.status_code, body=response.text, url=url)
      return response.content


  def _build_document(
      *,
      issue: dict[str, Any],
      owner: str,
      repo: str,
      include_children: bool,
      client: GhClient,
  ) -> dict[str, Any]:
      labels = [
          (label.get("name") if isinstance(label, dict) else str(label))
          for label in (issue.get("labels") or [])
      ]
      labels = [label for label in labels if isinstance(label, str)]
      body = str(issue.get("body") or "")
      title = str(issue.get("title") or f"Issue {issue.get('number')}")
      number = int(issue["number"])

      pbi_type = _classify_type(labels)
      severity = _classify_severity(labels)

      acceptance = _extract_section(body, ACCEPTANCE_HEADING_RE)
      repro = _extract_section(body, REPRO_HEADING_RE)

      attachments: list[dict[str, str]] = []
      for name, url in _extract_attachment_urls(body):
          print(f"downloading attachment {name} from {url}...", file=sys.stderr)
          content = _download(client, url)
          attachments.append(
              {
                  "name": name,
                  "url": url,
                  "content_base64": base64.b64encode(content).decode("ascii"),
              }
          )

      child_ids: list[str] = []
      if include_children:
          child_ids = [f"WI-{n}" for n in _extract_children(body)]

      source_url = str(
          issue.get("html_url") or f"https://github.com/{owner}/{repo}/issues/{number}"
      )

      return {
          "id": f"WI-{number}",
          "type": pbi_type,
          "severity": severity,
          "title": title,
          "body_markdown": body,
          "acceptance_markdown": acceptance,
          "repro_steps_markdown": repro,
          "attachments": attachments,
          "child_ids": child_ids,
          "parent_id": None,
          "source_url": source_url,
          "source_host": "github",
          "raw": {
              "labels": labels,
              "work_item_type": "issue",
              "user": (issue.get("user") or {}).get("login"),
          },
      }


  def main(argv: list[str] | None = None) -> int:
      args = _parse_args(argv if argv is not None else sys.argv[1:])
      try:
          token = _require_env("GH_TOKEN")
          owner, repo = _resolve_repo(args)
          number = _parse_issue_number(args.work_item)

          print(f"fetching GitHub issue {owner}/{repo}#{number}...", file=sys.stderr)
          client = GhClient(token=token)
          issue = client.get(f"repos/{owner}/{repo}/issues/{number}")
          if not isinstance(issue, dict):
              raise _FatalError("GitHub issue response was not a JSON object")

          document = _build_document(
              issue=issue,
              owner=owner,
              repo=repo,
              include_children=args.include_children,
              client=client,
          )
          errors = validate(document)
          if errors:
              raise _FatalError(
                  "fetcher produced an invalid document:\n  - " + "\n  - ".join(errors)
              )
          print(json.dumps(document, indent=2, sort_keys=True))
          return 0
      except _FatalError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2
      except GhError as exc:
          print(f"error: GitHub request failed: {exc}", file=sys.stderr)
          return 2


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

  > **Note on `GhClient`:** this script assumes Plan 2's `scripts.gh_client` exposes a `GhClient(token=...)` constructor with a `.get(path)` method that returns parsed JSON (raising `GhError` on non-2xx), and a `.session` attribute (the underlying `requests.Session` with the `Authorization` header pre-set), and a `.timeout` attribute. If Plan 2's actual surface differs at execution time, adjust this script to match.

- [ ] 2. Run the GitHub fetcher tests; they must now pass:
  ```
  uv run pytest tests/skills/test_workitem_fetch_github.py -v
  ```
  Expected: all five tests pass.

- [ ] 3. Lint and type-check:
  ```
  uv run ruff check skills/workitem-fetch-github/scripts/fetch.py tests/skills/test_workitem_fetch_github.py
  uv run mypy skills/workitem-fetch-github/scripts/fetch.py tests/skills/test_workitem_fetch_github.py
  ```
  Expected: both clean.

- [ ] 4. Commit:
  ```
  git add skills/workitem-fetch-github/scripts/fetch.py tests/skills/test_workitem_fetch_github.py
  git commit -m "feat(workitem-fetch-github): implement GitHub Issues fetcher with normalised JSON output"
  ```

---

### Task 5 — Write the failing test for the host-agnostic orchestrator

**Files**
- Create: `tests/skills/test_ralph_add.py`

**Steps**

- [ ] 1. Write `tests/skills/test_ralph_add.py` with the exact content below. The orchestrator tests use a **mock fetcher** (a tiny Python script created in `tmp_path`) so the orchestrator is exercised without touching any real host. The mock fetcher emits a canned normalised JSON document. This proves the orchestrator works with ANY backend that respects the schema.
  ```python
  """Tests for the host-agnostic ``ralph-add`` orchestrator.

  The orchestrator delegates the work-item fetch step to a sibling
  ``workitem-fetch/`` skill via ``subprocess.run``. These tests stub the
  fetcher with a tiny Python script written into ``tmp_path`` and point
  ``RALPH_WORKITEM_FETCH_SCRIPT`` at it, so the orchestrator's behaviour
  can be verified end-to-end without invoking GitHub or ADO.
  """
  from __future__ import annotations

  import importlib.util
  import json
  import subprocess
  from collections.abc import Iterator
  from pathlib import Path
  from types import ModuleType

  import pytest


  REPO_ROOT = Path(__file__).resolve().parents[2]
  SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-add" / "scripts" / "add.py"

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
  def git_repo(tmp_path: Path) -> Iterator[Path]:
      """Build a local bare repo + a working clone with a ralph-queue branch."""
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


  def _write_mock_fetcher(
      tmp_path: Path,
      *,
      documents_by_id: dict[int, dict[str, object]],
  ) -> Path:
      """Write a mock fetcher script that emits canned JSON for given ids.

      The script reads ``--work-item`` and returns the matching document from
      a JSON map written next to it. Unknown ids exit non-zero.
      """
      script = tmp_path / "mock_fetch.py"
      map_path = tmp_path / "mock_docs.json"
      # Keys must be strings in JSON.
      map_path.write_text(
          json.dumps({str(k): v for k, v in documents_by_id.items()}),
          encoding="utf-8",
      )
      script.write_text(
          "import argparse, json, sys\n"
          "from pathlib import Path\n"
          "MAP_PATH = Path(__file__).with_name('mock_docs.json')\n"
          "def main():\n"
          "    parser = argparse.ArgumentParser()\n"
          "    parser.add_argument('--work-item', required=True)\n"
          "    parser.add_argument('--include-children', action='store_true')\n"
          "    parser.add_argument('--repo')\n"
          "    parser.add_argument('--repo-name')\n"
          "    args = parser.parse_args()\n"
          "    raw = args.work_item.lstrip('#').strip()\n"
          "    if raw.lower().startswith('wi-'):\n"
          "        raw = raw[3:]\n"
          "    docs = json.loads(MAP_PATH.read_text(encoding='utf-8'))\n"
          "    doc = docs.get(raw)\n"
          "    if doc is None:\n"
          "        print(f'unknown work item {raw!r}', file=sys.stderr)\n"
          "        sys.exit(2)\n"
          "    print(json.dumps(doc))\n"
          "    sys.exit(0)\n"
          "if __name__ == '__main__':\n"
          "    main()\n",
          encoding="utf-8",
      )
      return script


  def _doc(
      *,
      number: int,
      pbi_type: str = "feature",
      severity: str = "normal",
      title: str = "Sample work item",
      body: str = "Sample body",
      acceptance: str = "",
      repro: str = "",
      attachments: list[dict[str, str]] | None = None,
      child_ids: list[str] | None = None,
      source_host: str = "github",
  ) -> dict[str, object]:
      return {
          "id": f"WI-{number}",
          "type": pbi_type,
          "severity": severity,
          "title": title,
          "body_markdown": body,
          "acceptance_markdown": acceptance,
          "repro_steps_markdown": repro,
          "attachments": attachments or [],
          "child_ids": child_ids or [],
          "parent_id": None,
          "source_url": f"https://example.invalid/issues/{number}",
          "source_host": source_host,
          "raw": {},
      }


  # ----------------------------------------------------------------------
  # Tests
  # ----------------------------------------------------------------------

  def test_feature_work_item_writes_feature_pbi(
      tmp_path: Path,
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      import base64

      png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-png-bytes").decode("ascii")
      pdf = base64.b64encode(b"%PDF-1.4 fake-pdf-bytes").decode("ascii")
      doc = _doc(
          number=WORK_ITEM_ID,
          pbi_type="feature",
          title="Add /healthz endpoint to service-auth",
          body="Platform requires every service to expose a liveness probe at GET /healthz.",
          acceptance="- GET /healthz returns 200.\n- Body is JSON {\"status\":\"ok\"}.",
          attachments=[
              {"name": "design.png", "url": "https://x/design.png", "content_base64": png},
              {"name": "context.pdf", "url": "https://x/context.pdf", "content_base64": pdf},
          ],
      )
      fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

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
      assert payload["source_host"] == "github"

      _git(git_repo, "checkout", "ralph-queue")
      pbi_dir = git_repo / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}"
      assert pbi_dir.is_dir()
      pbi_md = (pbi_dir / "PBI.md").read_text(encoding="utf-8")
      assert pbi_md.startswith("---\n")
      assert "type: feature" in pbi_md
      assert "/healthz" in pbi_md
      assert "GET /healthz returns 200" in pbi_md
      assert "design.png" in pbi_md
      assert (pbi_dir / "PLAN.md").is_file()
      assert (pbi_dir / "HISTORY.md").is_file()
      assert (pbi_dir / "attachments" / "design.png").read_bytes().startswith(b"\x89PNG")
      assert (pbi_dir / "attachments" / "context.pdf").read_bytes().startswith(b"%PDF")


  def test_bug_work_item_writes_bug_pbi(
      tmp_path: Path,
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      doc = _doc(
          number=WORK_ITEM_ID,
          pbi_type="bug",
          severity="critical",
          title="service-auth pod crashloops on deploy",
          body="AccessDenied calling STS via IRSA.",
          repro="1. kubectl get pods\n2. Observe CrashLoopBackOff",
      )
      fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

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
      assert "severity: critical" in bug_md
      reproduce = (pbi_dir / "REPRODUCE.md").read_text(encoding="utf-8")
      assert "kubectl get pods" in reproduce
      assert "CrashLoopBackOff" in reproduce


  def test_expand_children_writes_one_pbi_per_child(
      tmp_path: Path,
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      parent = _doc(
          number=PARENT_ID,
          title="Onboarding flow rewrite",
          child_ids=[f"WI-{CHILD_A_ID}", f"WI-{CHILD_B_ID}"],
      )
      child_a = _doc(number=CHILD_A_ID, title="Step 1: shell")
      child_b = _doc(number=CHILD_B_ID, title="Step 2: API")
      fetcher = _write_mock_fetcher(
          tmp_path,
          documents_by_id={
              PARENT_ID: parent,
              CHILD_A_ID: child_a,
              CHILD_B_ID: child_b,
          },
      )
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

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
      child_a_pbi = inbox / f"WI-{CHILD_A_ID}" / "PBI.md"
      child_b_pbi = inbox / f"WI-{CHILD_B_ID}" / "PBI.md"
      assert child_a_pbi.is_file()
      assert child_b_pbi.is_file()
      assert f"parent_id: WI-{PARENT_ID}" in child_a_pbi.read_text(encoding="utf-8")
      assert f"parent_id: WI-{PARENT_ID}" in child_b_pbi.read_text(encoding="utf-8")
      # The parent itself is NOT written when --expand-children is used.
      assert not (inbox / f"WI-{PARENT_ID}").exists()


  def test_via_mcp_raises_not_implemented(
      tmp_path: Path,
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      fetcher = _write_mock_fetcher(tmp_path, documents_by_id={})
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
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


  def test_repo_path_must_be_git_repo(
      tmp_path: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      not_a_repo = tmp_path / "not_a_repo"
      not_a_repo.mkdir()
      fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: _doc(number=WORK_ITEM_ID)})
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

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


  def test_dry_run_makes_no_writes_or_pushes(
      tmp_path: Path,
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      doc = _doc(number=WORK_ITEM_ID)
      fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

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

      _git(git_repo, "checkout", "ralph-queue")
      assert not (git_repo / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}").exists()


  def test_severity_override(
      tmp_path: Path,
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      doc = _doc(number=WORK_ITEM_ID, severity="normal")
      fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

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


  def test_no_push_commits_but_does_not_push(
      tmp_path: Path,
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      doc = _doc(number=WORK_ITEM_ID)
      fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

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
      assert remote_sha_before == remote_sha_after


  def test_fetcher_failure_propagates(
      tmp_path: Path,
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      """If the fetcher exits non-zero, ``ralph-add`` surfaces the error."""
      fetcher = _write_mock_fetcher(tmp_path, documents_by_id={})  # empty map → unknown id
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
      exit_code = add_module.main(
          [
              "--work-item",
              str(WORK_ITEM_ID),
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 2
      err = capsys.readouterr().err
      assert "workitem-fetch" in err or "fetcher" in err


  def test_fetcher_invalid_json_rejected(
      tmp_path: Path,
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      """A fetcher emitting malformed JSON must trigger a clean failure."""
      script = tmp_path / "broken_fetcher.py"
      script.write_text(
          "import sys\nprint('this is not json')\nsys.exit(0)\n",
          encoding="utf-8",
      )
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(script))
      exit_code = add_module.main(
          [
              "--work-item",
              str(WORK_ITEM_ID),
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 2
      err = capsys.readouterr().err
      assert "json" in err.lower() or "invalid" in err.lower()


  def test_fetcher_invalid_schema_rejected(
      tmp_path: Path,
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      """A fetcher emitting JSON missing required keys must trigger a clean failure."""
      bad_doc = {"id": "WI-9", "title": "missing the rest"}
      fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: bad_doc})
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
      exit_code = add_module.main(
          [
              "--work-item",
              str(WORK_ITEM_ID),
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 2
      err = capsys.readouterr().err.lower()
      assert "schema" in err or "missing" in err or "invalid" in err
  ```

- [ ] 2. Run the orchestrator tests; they must fail because the orchestrator script does not yet exist:
  ```
  uv run pytest tests/skills/test_ralph_add.py -v
  ```
  Expected: `AssertionError: missing entry script at <SCRIPT_PATH>`. This is the red step.

- [ ] 3. Do NOT commit yet — the failing test is consumed in Task 6.

---

### Task 6 — Implement the host-agnostic orchestrator `ralph-add/scripts/add.py`

**Files**
- Create: `skills/ralph-add/scripts/add.py`

**Steps**

- [ ] 1. Write `skills/ralph-add/scripts/add.py` with the exact content below. The orchestrator does NOT import any host SDK; it only knows how to find and invoke a fetcher script:
  ```python
  """``ralph-add`` skill entry point — host-agnostic orchestrator.

  Delegates the work-item fetch to a sibling ``workitem-fetch/`` skill
  (either ``workitem-fetch-github`` in Phase 1 or ``workitem-fetch-ado`` in
  Phase 2). Knows nothing about GitHub or Azure DevOps APIs. Reads the
  fetcher's normalised JSON output, packages it into a PBI directory, and
  commits + pushes to ``ralph-queue``.
  """
  from __future__ import annotations

  import argparse
  import base64
  import importlib.util
  import json
  import os
  import subprocess
  import sys
  from dataclasses import asdict, dataclass
  from datetime import datetime, timezone
  from pathlib import Path
  from typing import Any


  DEFAULT_QUEUE_BRANCH = "ralph-queue"
  ATTACHMENT_SUBDIR = "attachments"
  ALLOWED_SEVERITIES = ("critical", "high", "normal", "low")

  _REPO_ROOT = Path(__file__).resolve().parents[3]

  # Load the schema validator from the GitHub fetcher (single source of truth).
  _SCHEMA_PATH = (
      _REPO_ROOT / "skills" / "workitem-fetch-github" / "scripts" / "schema.py"
  )


  def _load_schema_validator() -> Any:
      spec = importlib.util.spec_from_file_location("wi_schema", _SCHEMA_PATH)
      if spec is None or spec.loader is None:
          # Schema module not present (e.g. running outside a checkout). Fall
          # back to a permissive validator that only checks for required top-level keys.
          def _fallback_validate(doc: object) -> list[str]:
              if not isinstance(doc, dict):
                  return ["document must be a JSON object"]
              required = (
                  "id", "type", "severity", "title", "body_markdown",
                  "acceptance_markdown", "repro_steps_markdown",
                  "attachments", "child_ids", "parent_id", "source_url",
                  "source_host", "raw",
              )
              return [f"missing required key: {k}" for k in required if k not in doc]
          return _fallback_validate
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      return module.validate  # type: ignore[attr-defined]


  validate = _load_schema_validator()


  # ----------------------------------------------------------------------
  # Data shapes
  # ----------------------------------------------------------------------

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
      source_host: str


  class _FatalError(RuntimeError):
      """Raised internally to signal a clean exit with code 2."""


  # ----------------------------------------------------------------------
  # Argument parsing
  # ----------------------------------------------------------------------

  def _parse_args(argv: list[str]) -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          prog="ralph-add",
          description=(
              "Host-agnostic orchestrator. Fetches a work item via the staged "
              "workitem-fetch/ skill and submits it as a PBI to the ralph-queue "
              "branch of a target service repo."
          ),
      )
      parser.add_argument("--work-item", required=True, help="Work item id.")
      group = parser.add_mutually_exclusive_group(required=True)
      group.add_argument("--repo", help="Path to an existing checkout of the target repo.")
      group.add_argument(
          "--repo-url",
          help=(
              "Git URL of the target repo. Reserved — not implemented in v1."
          ),
      )
      parser.add_argument(
          "--severity",
          choices=ALLOWED_SEVERITIES,
          help="Override severity returned by the fetcher.",
      )
      parser.add_argument(
          "--expand-children",
          action="store_true",
          help="Write one PBI per child instead of one for the parent.",
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
      parser.add_argument("--no-push", action="store_true", help="Commit but do not push.")
      parser.add_argument("--dry-run", action="store_true", help="Compute without mutating.")
      return parser.parse_args(argv)


  # ----------------------------------------------------------------------
  # Fetcher resolution
  # ----------------------------------------------------------------------

  def _resolve_fetcher() -> Path:
      override = os.environ.get("RALPH_WORKITEM_FETCH_SCRIPT", "").strip()
      if override:
          path = Path(override).expanduser().resolve()
          if not path.is_file():
              raise _FatalError(
                  f"RALPH_WORKITEM_FETCH_SCRIPT points at non-existent file: {path}"
              )
          return path

      home_staged = (
          Path.home() / ".claude" / "skills" / "workitem-fetch" / "scripts" / "fetch.py"
      )
      if home_staged.is_file():
          return home_staged

      host = os.environ.get("RALPH_GIT_HOST", "").strip().lower()
      if host == "github":
          candidate = (
              _REPO_ROOT / "skills" / "workitem-fetch-github" / "scripts" / "fetch.py"
          )
          if candidate.is_file():
              return candidate
      if host == "ado":
          candidate = (
              _REPO_ROOT / "skills" / "workitem-fetch-ado" / "scripts" / "fetch.py"
          )
          if candidate.is_file():
              return candidate

      raise _FatalError(
          "could not locate workitem-fetch fetcher; set RALPH_GIT_HOST or stage "
          "the skill first (see SKILL.md → 'How it finds the fetcher')"
      )


  def _invoke_fetcher(fetcher: Path, work_item: str) -> dict[str, Any]:
      """Run the fetcher and parse its stdout as a normalised JSON document."""
      print(f"invoking fetcher {fetcher}...", file=sys.stderr)
      result = subprocess.run(
          [sys.executable, str(fetcher), "--work-item", work_item],
          capture_output=True,
          text=True,
      )
      if result.returncode != 0:
          stderr = result.stderr.strip() or "(no stderr)"
          raise _FatalError(
              f"workitem-fetch fetcher exited {result.returncode}: {stderr}"
          )
      try:
          parsed = json.loads(result.stdout)
      except json.JSONDecodeError as exc:
          raise _FatalError(
              f"workitem-fetch fetcher produced invalid JSON: {exc}"
          ) from exc
      errors = validate(parsed)
      if errors:
          raise _FatalError(
              "workitem-fetch fetcher produced a schema-invalid document:\n  - "
              + "\n  - ".join(errors)
          )
      if not isinstance(parsed, dict):
          raise _FatalError("fetcher output was not a JSON object")
      return parsed


  # ----------------------------------------------------------------------
  # ID helpers
  # ----------------------------------------------------------------------

  def _normalise_work_item_arg(raw: str) -> str:
      stripped = raw.strip().lstrip("#")
      if stripped.lower().startswith("wi-"):
          return f"WI-{stripped[3:]}"
      return stripped  # let the fetcher decide

  # ----------------------------------------------------------------------
  # PBI rendering
  # ----------------------------------------------------------------------

  def _now_iso() -> str:
      return (
          datetime.now(tz=timezone.utc)
          .replace(microsecond=0)
          .isoformat()
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


  def _render_feature_body(doc: dict[str, Any]) -> str:
      title = str(doc["title"]).strip()
      description = str(doc["body_markdown"]).strip()
      acceptance = str(doc["acceptance_markdown"]).strip()
      attachments = doc["attachments"]
      source_url = str(doc["source_url"])
      source_host = str(doc["source_host"])

      parts: list[str] = [f"# {title}", ""]
      if description:
          parts.extend(["## Context", "", description, ""])
      if acceptance:
          parts.extend(["## Acceptance criteria", "", acceptance, ""])
      if attachments:
          parts.extend(["## Attachments", ""])
          for att in attachments:
              parts.append(
                  f"- `{ATTACHMENT_SUBDIR}/{att['name']}` (from {att['url']})"
              )
          parts.append("")
      parts.extend(
          [
              "## Source",
              "",
              f"- {source_host}: <{source_url}>",
              f"- Submitted by `ralph-add` at {_now_iso()}.",
              "",
          ]
      )
      return "\n".join(parts).rstrip() + "\n"


  def _render_bug_body(doc: dict[str, Any]) -> str:
      title = str(doc["title"]).strip()
      description = str(doc["body_markdown"]).strip()
      attachments = doc["attachments"]
      source_url = str(doc["source_url"])
      source_host = str(doc["source_host"])

      parts: list[str] = [f"# {title}", ""]
      if description:
          parts.extend(["## Failure context", "", description, ""])
      if attachments:
          parts.extend(["## Attachments", ""])
          for att in attachments:
              parts.append(
                  f"- `{ATTACHMENT_SUBDIR}/{att['name']}` (from {att['url']})"
              )
          parts.append("")
      parts.extend(
          [
              "## Source",
              "",
              f"- {source_host}: <{source_url}>",
              f"- Submitted by `ralph-add` at {_now_iso()}.",
              "",
          ]
      )
      return "\n".join(parts).rstrip() + "\n"


  def _render_reproduce(doc: dict[str, Any]) -> str:
      steps = str(doc["repro_steps_markdown"]).strip()
      pbi_id = str(doc["id"])
      header = f"# Reproduce — {pbi_id}\n\n"
      if not steps:
          return (
              header
              + "_No reproducer was supplied with the work item. "
              "Add reproduction steps before Ralph can investigate._\n"
          )
      return header + steps + "\n"


  def _render_plan(doc: dict[str, Any]) -> str:
      title = str(doc["title"]).strip()
      pbi_id = str(doc["id"])
      return (
          f"# Implementation plan — {pbi_id} — {title}\n\n"
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
      doc: dict[str, Any],
      severity: str,
      parent_id: str | None,
  ) -> Path:
      pbi_id = str(doc["id"])
      pbi_type = str(doc["type"])
      pbi_dir = base_dir / ".ralph" / "inbox" / pbi_id
      pbi_dir.mkdir(parents=True, exist_ok=True)

      frontmatter = _render_frontmatter(
          pbi_id=pbi_id,
          pbi_type=pbi_type,
          severity=severity,
          parent_id=parent_id,
      )
      if pbi_type == "bug":
          (pbi_dir / "BUG.md").write_text(
              frontmatter + _render_bug_body(doc), encoding="utf-8"
          )
          (pbi_dir / "REPRODUCE.md").write_text(
              _render_reproduce(doc), encoding="utf-8"
          )
      else:
          (pbi_dir / "PBI.md").write_text(
              frontmatter + _render_feature_body(doc), encoding="utf-8"
          )
          (pbi_dir / "PLAN.md").write_text(_render_plan(doc), encoding="utf-8")
      (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")

      attachments = doc["attachments"]
      if attachments:
          attachments_dir = pbi_dir / ATTACHMENT_SUBDIR
          attachments_dir.mkdir(exist_ok=True)
          for att in attachments:
              name = str(att["name"])
              try:
                  payload = base64.b64decode(att["content_base64"], validate=True)
              except (ValueError, TypeError) as exc:
                  raise _FatalError(
                      f"attachment {name!r} has invalid base64 payload: {exc}"
                  ) from exc
              (attachments_dir / name).write_bytes(payload)
      return pbi_dir


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
      local_branches = _run_git(repo, "branch", "--list", branch)
      if local_branches.strip():
          _run_git(repo, "checkout", branch)
      else:
          remote_branches = _run_git(repo, "branch", "-r", "--list", f"origin/{branch}")
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
  # Orchestrator entry
  # ----------------------------------------------------------------------

  def main(argv: list[str] | None = None) -> int:
      args = _parse_args(argv if argv is not None else sys.argv[1:])
      try:
          if args.via_mcp:
              raise _FatalError(
                  "NotImplemented: --via-mcp is reserved for v2; remove the flag."
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

          fetcher = _resolve_fetcher()
          work_item_arg = _normalise_work_item_arg(args.work_item)

          if not args.dry_run:
              _checkout_queue_branch(repo, args.branch)

          # Fetch the (root) work item.
          root_doc = _invoke_fetcher(fetcher, work_item_arg)
          severity_override = args.severity

          attachments_total = 0
          children_expanded = 0
          last_commit_sha = ""
          first_pbi_for_report: tuple[str, str, str] | None = None
          pushed = False

          if args.expand_children:
              child_ids = list(root_doc.get("child_ids") or [])
              if not child_ids:
                  raise _FatalError(
                      f"work item {root_doc['id']} has no child links; "
                      "nothing to expand"
                  )
              for child_id in child_ids:
                  child_doc = _invoke_fetcher(fetcher, child_id)
                  child_doc["parent_id"] = str(root_doc["id"])
                  child_severity = severity_override or str(child_doc["severity"])
                  if not args.dry_run:
                      pbi_dir = _write_pbi_directory(
                          base_dir=repo,
                          doc=child_doc,
                          severity=child_severity,
                          parent_id=str(root_doc["id"]),
                      )
                  else:
                      pbi_dir = repo / ".ralph" / "inbox" / str(child_doc["id"])
                  attachments_total += len(child_doc.get("attachments") or [])
                  children_expanded += 1
                  if first_pbi_for_report is None:
                      first_pbi_for_report = (
                          str(child_doc["id"]),
                          str(child_doc["type"]),
                          str(pbi_dir.relative_to(repo)).replace("\\", "/"),
                      )
                  if not args.dry_run:
                      message = (
                          f"feat(ralph-queue): add {child_doc['id']} "
                          f"(child of {root_doc['id']})"
                      )
                      last_commit_sha = _commit_pbi(repo, pbi_dir, message)
          else:
              severity = severity_override or str(root_doc["severity"])
              if not args.dry_run:
                  pbi_dir = _write_pbi_directory(
                      base_dir=repo,
                      doc=root_doc,
                      severity=severity,
                      parent_id=None,
                  )
              else:
                  pbi_dir = repo / ".ralph" / "inbox" / str(root_doc["id"])
              attachments_total = (
                  len(root_doc.get("attachments") or []) if not args.dry_run else 0
              )
              first_pbi_for_report = (
                  str(root_doc["id"]),
                  str(root_doc["type"]),
                  str(pbi_dir.relative_to(repo)).replace("\\", "/"),
              )
              if not args.dry_run:
                  last_commit_sha = _commit_pbi(
                      repo,
                      pbi_dir,
                      f"feat(ralph-queue): add {root_doc['id']}",
                  )

          if not args.dry_run and not args.no_push:
              print(f"pushing {args.branch} to origin...", file=sys.stderr)
              _push(repo, args.branch)
              pushed = True

          assert first_pbi_for_report is not None
          report_pbi_id, report_pbi_type, report_pbi_path = first_pbi_for_report
          result = AddResult(
              work_item_id=str(root_doc["id"]).removeprefix("WI-"),
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
              source_host=str(root_doc["source_host"]),
          )
          print(json.dumps(asdict(result), indent=2, sort_keys=True))
          return 0
      except _FatalError as exc:
          print(f"error: {exc}", file=sys.stderr)
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

- [ ] 2. Run the orchestrator tests; they must now pass:
  ```
  uv run pytest tests/skills/test_ralph_add.py -v
  ```
  Expected: all twelve tests pass.

- [ ] 3. Run ruff and mypy:
  ```
  uv run ruff check skills/ralph-add/scripts/add.py tests/skills/test_ralph_add.py
  uv run mypy skills/ralph-add/scripts/add.py tests/skills/test_ralph_add.py
  ```
  Expected: both clean.

- [ ] 4. Commit:
  ```
  git add skills/ralph-add/scripts/add.py tests/skills/test_ralph_add.py
  git commit -m "feat(ralph-add): implement host-agnostic orchestrator delegating to workitem-fetch skill"
  ```

---

### Task 7 — Cross-validate generated PBIs against the workspace validator

**Files**
- Modify: `tests/skills/test_ralph_add.py` (append one test)

**Steps**

- [ ] 1. Append the following test to the bottom of `tests/skills/test_ralph_add.py`. It uses the same mock-fetcher pattern, then runs Plan 1's validator over the produced directory:
  ```python
  def test_generated_pbi_passes_plan1_validator(
      tmp_path: Path,
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      """PBI directories produced by ``ralph-add`` must satisfy the canonical
      PBI schema enforced by ``scripts.validate_samples``.

      The validator requires the directory name to start with ``feature-``,
      ``bug-``, or ``pr-feedback-``. ``ralph-add`` writes directories named
      ``WI-<n>``, so this test mirrors the directory into a typed-name copy
      and validates that copy — confirming the **frontmatter + sibling
      files** are schema-compliant without forcing the in-tree layout to
      embed the type in the directory name. (See orchestrator reconciliation
      note 3 in ``2026-05-24-00-orchestrator.md``.)
      """
      from scripts.validate_samples import validate_sample

      import base64
      png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")
      doc = _doc(
          number=WORK_ITEM_ID,
          title="Add /healthz endpoint to service-auth",
          body="Platform requires every service to expose a liveness probe at GET /healthz.",
          acceptance="- GET /healthz returns 200.",
          attachments=[
              {"name": "design.png", "url": "https://x/design.png", "content_base64": png}
          ],
      )
      fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
      monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

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

      # Mirror into a feature-prefixed sibling for the validator's check.
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

- [ ] 2. Run the full file:
  ```
  uv run pytest tests/skills/test_ralph_add.py -v
  ```
  Expected: thirteen tests pass.

- [ ] 3. Lint:
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

---

### Task 8 — Full Phase 1 toolchain pass

**Files**
- (none — toolchain-only step)

**Steps**

- [ ] 1. Run the whole local gate:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  uv run pytest
  ```
  Expected: each command exits 0.

- [ ] 2. Confirm both skills' `--help` work:
  ```
  uv run python skills/ralph-add/scripts/add.py --help
  uv run python skills/workitem-fetch-github/scripts/fetch.py --help
  ```
  Expected: help text for `ralph-add` lists `--work-item`, `--repo`, `--repo-url`, `--severity`, `--expand-children`, `--via-mcp`, `--branch`, `--no-push`, `--dry-run`. Help for `workitem-fetch-github` lists `--work-item`, `--repo`, `--repo-name`, `--include-children`.

- [ ] 3. Confirm conventional-commit history:
  ```
  git log --oneline -10
  ```
  Expected: Plan-3 commits at the top with conventional prefixes:
  - `chore(skills): scaffold host-agnostic ralph-add + workitem-fetch-github + mock fixture`
  - `feat(workitem-fetch): define normalised work-item JSON schema`
  - `feat(workitem-fetch-github): implement GitHub Issues fetcher with normalised JSON output`
  - `feat(ralph-add): implement host-agnostic orchestrator delegating to workitem-fetch skill`
  - `test(ralph-add): cross-check generated PBIs against Plan 1 schema validator`

---

## Phase 2 — ADO fetcher (Ralph-built, deferred)

> **Status: deferred.** Phase 1 is sufficient to close Plan 3's orchestrator gate. The Phase 2 tasks below document the ADO fetcher work so that Ralph (or a future engineer) can pick it up as a standalone PBI. None of the Phase 2 tasks are required for Plan 3's verification gate to pass.

The Phase 2 work relocates the ADO-specific fetch logic from the previous draft of this plan (`fetch_work_item`, `fetch_attachment`, `_strip_html`, `_classify_pbi_type`, `_infer_severity`) into the new `workitem-fetch-ado/` skill while keeping its output schema identical to Phase 1's.

### Phase 2 — ADO — Task A: scaffold `workitem-fetch-ado/`

**Files**
- Create: `skills/workitem-fetch-ado/SKILL.md`
- Create: `skills/workitem-fetch-ado/scripts/__init__.py`
- Create: `skills/workitem-fetch-ado/scripts/schema.py`

**Steps**

- [ ] 1. Create `skills/workitem-fetch-ado/SKILL.md` with frontmatter describing the ADO fetcher (mirror Phase 1's `workitem-fetch-github/SKILL.md` structure). Document env vars (`ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT`), endpoints (`GET /_apis/wit/workitems/{id}?$expand=relations`, attachment download), and the type/severity mapping table.

- [ ] 2. Create `skills/workitem-fetch-ado/scripts/__init__.py`:
  ```python
  """Empty package marker."""
  ```

- [ ] 3. Create `skills/workitem-fetch-ado/scripts/schema.py` as a re-export:
  ```python
  """Re-export the normalised work-item schema from the GitHub fetcher."""
  from __future__ import annotations

  import importlib.util
  from pathlib import Path

  _SOURCE = (
      Path(__file__).resolve().parents[2]
      / "workitem-fetch-github"
      / "scripts"
      / "schema.py"
  )
  _spec = importlib.util.spec_from_file_location("wi_schema_canonical", _SOURCE)
  assert _spec is not None and _spec.loader is not None
  _mod = importlib.util.module_from_spec(_spec)
  _spec.loader.exec_module(_mod)

  WorkItemJson = _mod.WorkItemJson  # type: ignore[attr-defined]
  AttachmentJson = _mod.AttachmentJson  # type: ignore[attr-defined]
  validate = _mod.validate  # type: ignore[attr-defined]
  REQUIRED_KEYS = _mod.REQUIRED_KEYS  # type: ignore[attr-defined]
  ALLOWED_TYPES = _mod.ALLOWED_TYPES  # type: ignore[attr-defined]
  ALLOWED_SEVERITIES = _mod.ALLOWED_SEVERITIES  # type: ignore[attr-defined]
  ALLOWED_HOSTS = _mod.ALLOWED_HOSTS  # type: ignore[attr-defined]
  ```

- [ ] 4. Commit:
  ```
  git add skills/workitem-fetch-ado/
  git commit -m "chore(workitem-fetch-ado): scaffold Phase 2 ADO fetcher skill (Ralph-built, deferred)"
  ```

### Phase 2 — ADO — Task B: write failing tests for the ADO fetcher

**Files**
- Create: `tests/skills/test_workitem_fetch_ado.py`

**Steps**

- [ ] 1. Write `tests/skills/test_workitem_fetch_ado.py` with the exact content below. Adapts the ADO mocks from the previous draft of this plan; asserts on the fetcher's JSON output (NOT on PBI files on disk — that's the orchestrator's job):
  ```python
  """Tests for the ``workitem-fetch-ado`` skill's fetcher.

  Mocks ADO REST endpoints with ``responses``. The fetcher emits the
  normalised work-item JSON document; we assert on the JSON shape and
  validate it against the shared schema.
  """
  from __future__ import annotations

  import base64
  import importlib.util
  import json
  from pathlib import Path
  from types import ModuleType

  import pytest
  import responses


  REPO_ROOT = Path(__file__).resolve().parents[2]
  SCRIPT_PATH = REPO_ROOT / "skills" / "workitem-fetch-ado" / "scripts" / "fetch.py"

  ORG = "https://dev.azure.com/example-org"
  PROJECT = "example-project"
  PAT = "fake-pat-value"
  WORK_ITEM_ID = 1234
  CHILD_A_ID = 9001
  CHILD_B_ID = 9002


  @pytest.fixture(scope="module")
  def fetch_module() -> ModuleType:
      assert SCRIPT_PATH.is_file(), f"missing fetcher script at {SCRIPT_PATH}"
      spec = importlib.util.spec_from_file_location("workitem_fetch_ado", SCRIPT_PATH)
      assert spec is not None and spec.loader is not None
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      return module


  @pytest.fixture
  def env(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("ADO_PAT", PAT)
      monkeypatch.setenv("ADO_ORG_URL", ORG)
      monkeypatch.setenv("ADO_PROJECT", PROJECT)


  def _wit_url(work_item_id: int) -> str:
      return f"{ORG}/{PROJECT}/_apis/wit/workitems/{work_item_id}"


  def _attachment_url(attachment_id: str, file_name: str = "design.png") -> str:
      return f"{ORG}/{PROJECT}/_apis/wit/attachments/{attachment_id}?fileName={file_name}"


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
                      "url": _attachment_url("att-1", "design.png"),
                      "attributes": {"name": "design.png"},
                  },
              ],
          },
          status=200,
      )
      responses.add(
          responses.GET,
          _attachment_url("att-1", "design.png"),
          body=b"\x89PNG\r\n\x1a\nfake-png-bytes",
          status=200,
          content_type="application/octet-stream",
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
                  "System.Title": "service-auth pod crashloops on deploy",
                  "System.Description": (
                      "<p>AccessDenied calling STS via IRSA.</p>"
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


  def _register_parent_with_children() -> None:
      responses.add(
          responses.GET,
          _wit_url(WORK_ITEM_ID),
          json={
              "id": WORK_ITEM_ID,
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


  # ----------------------------------------------------------------------
  # Tests
  # ----------------------------------------------------------------------

  @responses.activate
  def test_feature_work_item_produces_normalised_feature_json(
      env: None,
      fetch_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_feature_work_item()
      exit_code = fetch_module.main(["--work-item", str(WORK_ITEM_ID)])
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["id"] == f"WI-{WORK_ITEM_ID}"
      assert payload["type"] == "feature"
      assert payload["severity"] == "high"  # priority 2 → high
      assert payload["source_host"] == "ado"
      assert "/healthz" in payload["body_markdown"]
      assert "GET /healthz returns 200" in payload["acceptance_markdown"]
      assert payload["parent_id"] is None
      assert payload["child_ids"] == []
      assert len(payload["attachments"]) == 1
      att = payload["attachments"][0]
      assert att["name"] == "design.png"
      decoded = base64.b64decode(att["content_base64"])
      assert decoded.startswith(b"\x89PNG")


  @responses.activate
  def test_bug_work_item_produces_normalised_bug_json(
      env: None,
      fetch_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_bug_work_item()
      exit_code = fetch_module.main(["--work-item", f"WI-{WORK_ITEM_ID}"])
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["type"] == "bug"
      assert payload["severity"] == "critical"
      assert "AccessDenied" in payload["body_markdown"]
      assert "kubectl get pods" in payload["repro_steps_markdown"]


  @responses.activate
  def test_include_children_populates_child_ids(
      env: None,
      fetch_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_parent_with_children()
      exit_code = fetch_module.main(
          ["--work-item", str(WORK_ITEM_ID), "--include-children"]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["child_ids"] == [f"WI-{CHILD_A_ID}", f"WI-{CHILD_B_ID}"]


  def test_missing_env_var_exits_nonzero(
      monkeypatch: pytest.MonkeyPatch,
      fetch_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      monkeypatch.delenv("ADO_PAT", raising=False)
      monkeypatch.setenv("ADO_ORG_URL", ORG)
      monkeypatch.setenv("ADO_PROJECT", PROJECT)
      exit_code = fetch_module.main(["--work-item", str(WORK_ITEM_ID)])
      assert exit_code == 2
      assert "ADO_PAT" in capsys.readouterr().err


  @responses.activate
  def test_schema_validation_runs_before_emit(
      env: None,
      fetch_module: ModuleType,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      _register_feature_work_item()
      exit_code = fetch_module.main(["--work-item", str(WORK_ITEM_ID)])
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      schema_path = (
          REPO_ROOT / "skills" / "workitem-fetch-ado" / "scripts" / "schema.py"
      )
      spec = importlib.util.spec_from_file_location("wi_schema_ado_check", schema_path)
      assert spec is not None and spec.loader is not None
      schema_mod = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(schema_mod)
      errors = schema_mod.validate(payload)
      assert errors == [], f"fetcher emitted invalid document: {errors}"
  ```

- [ ] 2. Run the tests — they must fail because `fetch.py` does not yet exist:
  ```
  uv run pytest tests/skills/test_workitem_fetch_ado.py -v
  ```
  Expected: `AssertionError: missing fetcher script at <SCRIPT_PATH>`.

- [ ] 3. Do NOT commit yet — consumed in Task C.

### Phase 2 — ADO — Task C: implement the ADO fetcher

**Files**
- Create: `skills/workitem-fetch-ado/scripts/fetch.py`

**Steps**

- [ ] 1. Write `skills/workitem-fetch-ado/scripts/fetch.py` with the exact content below. This is the relocation of the ADO-specific functions from the previous draft of this plan, plus the base64-encoding step required by the normalised JSON contract:
  ```python
  """``workitem-fetch-ado`` skill entry point.

  Fetches one Azure DevOps work item and emits the normalised work-item JSON
  document on stdout. Host-pure: knows nothing about GitHub. Sibling of
  ``workitem-fetch-github``; both produce the same JSON shape so the
  ``ralph-add`` orchestrator is host-agnostic.
  """
  from __future__ import annotations

  import argparse
  import base64
  import html
  import importlib.util
  import json
  import os
  import re
  import sys
  from pathlib import Path
  from typing import Any

  # Make ``scripts.ado_client`` (Plan 2) importable when invoked directly.
  _REPO_ROOT = Path(__file__).resolve().parents[3]
  if str(_REPO_ROOT) not in sys.path:
      sys.path.insert(0, str(_REPO_ROOT))

  from scripts.ado_client import AdoClient, AdoError  # noqa: E402

  # Load the local ``schema`` module (re-exported from the GitHub fetcher).
  _SCHEMA_PATH = Path(__file__).with_name("schema.py")
  _spec = importlib.util.spec_from_file_location("wi_schema_ado", _SCHEMA_PATH)
  assert _spec is not None and _spec.loader is not None
  _schema = importlib.util.module_from_spec(_spec)
  _spec.loader.exec_module(_schema)
  validate = _schema.validate  # type: ignore[attr-defined]


  WORK_ITEM_TYPE_BUG = "Bug"
  PRIORITY_TO_SEVERITY: dict[int, str] = {
      1: "critical",
      2: "high",
      3: "normal",
      4: "low",
  }

  _BLOCK_TAGS = ("</p>", "</div>", "<br>", "<br/>", "<br />", "</li>")
  _LIST_ITEM_RE = re.compile(r"<li[^>]*>", flags=re.IGNORECASE)
  _LIST_OPEN_RE = re.compile(r"<(ul|ol)[^>]*>", flags=re.IGNORECASE)
  _LIST_CLOSE_RE = re.compile(r"</(ul|ol)>", flags=re.IGNORECASE)
  _TAG_RE = re.compile(r"<[^>]+>")


  class _FatalError(RuntimeError):
      """Raised internally to signal a clean exit with code 2."""


  # ----------------------------------------------------------------------
  # Argument parsing + env
  # ----------------------------------------------------------------------

  def _parse_args(argv: list[str]) -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          prog="workitem-fetch-ado",
          description=(
              "Fetch an ADO work item and emit a normalised work-item JSON "
              "document on stdout. Requires ADO_PAT, ADO_ORG_URL, ADO_PROJECT "
              "in the environment."
          ),
      )
      parser.add_argument("--work-item", required=True, help="ADO work item id.")
      parser.add_argument(
          "--include-children",
          action="store_true",
          help="Populate child_ids from Hierarchy-Forward relations.",
      )
      return parser.parse_args(argv)


  def _require_env(name: str) -> str:
      value = os.environ.get(name, "").strip()
      if not value:
          raise _FatalError(f"environment variable {name} is required")
      return value


  def _parse_work_item_id(raw: str) -> int:
      stripped = raw.strip()
      if stripped.lower().startswith("wi-"):
          stripped = stripped[3:]
      if not stripped.isdigit():
          raise _FatalError(
              f"work item id {raw!r} must be a positive integer "
              "(optionally prefixed with 'WI-')"
          )
      value = int(stripped)
      if value <= 0:
          raise _FatalError(f"work item id must be > 0, got {value}")
      return value


  # ----------------------------------------------------------------------
  # HTML → markdown
  # ----------------------------------------------------------------------

  def _strip_html(raw: str) -> str:
      """Convert ADO's HTML descriptions into something readable as markdown."""
      if not raw:
          return ""
      text = raw
      for tag in _BLOCK_TAGS:
          text = re.sub(re.escape(tag), "\n", text, flags=re.IGNORECASE)
      text = _LIST_ITEM_RE.sub("\n- ", text)
      text = _LIST_OPEN_RE.sub("\n", text)
      text = _LIST_CLOSE_RE.sub("\n", text)
      text = _TAG_RE.sub("", text)
      text = html.unescape(text)
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
  # Classification
  # ----------------------------------------------------------------------

  def _classify_pbi_type(work_item_type: str) -> str:
      return "bug" if work_item_type == WORK_ITEM_TYPE_BUG else "feature"


  def _classify_severity(
      priority: int | None,
      severity_label: str | None,
  ) -> str:
      if severity_label:
          label = severity_label.lower()
          if "critical" in label or label.startswith("1"):
              return "critical"
          if "high" in label or label.startswith("2"):
              return "high"
          if "low" in label or label.startswith("4"):
              return "low"
          if "medium" in label or "normal" in label or label.startswith("3"):
              return "normal"
      if priority is not None:
          mapped = PRIORITY_TO_SEVERITY.get(priority)
          if mapped:
              return mapped
      return "normal"


  # ----------------------------------------------------------------------
  # Relation parsing
  # ----------------------------------------------------------------------

  def _filename_from_url(url: str) -> str | None:
      match = re.search(r"fileName=([^&]+)", url)
      if match:
          return match.group(1)
      return None


  def _safe_filename(name: str) -> str:
      cleaned = re.sub(r"[\\/\x00-\x1f]+", "_", name).strip(" .")
      return cleaned or "attachment"


  def _id_from_work_item_url(url: str) -> int | None:
      match = re.search(r"/workitems/(\d+)", url, flags=re.IGNORECASE)
      if not match:
          return None
      return int(match.group(1))


  def _split_relations(
      relations: list[dict[str, Any]],
  ) -> tuple[list[tuple[str, str]], list[int]]:
      """Return ([(name, url) attachments], [child ids])."""
      attachments: list[tuple[str, str]] = []
      child_ids: list[int] = []
      for rel in relations:
          rel_type = rel.get("rel")
          url = rel.get("url", "")
          attrs = rel.get("attributes", {}) or {}
          if rel_type == "AttachedFile":
              name = str(attrs.get("name") or _filename_from_url(url) or "attachment")
              attachments.append((_safe_filename(name), url))
          elif rel_type == "System.LinkTypes.Hierarchy-Forward":
              child = _id_from_work_item_url(url)
              if child is not None:
                  child_ids.append(child)
      return attachments, child_ids


  # ----------------------------------------------------------------------
  # Orchestration
  # ----------------------------------------------------------------------

  def _download_attachment(client: AdoClient, url: str) -> bytes:
      """Fetch an attachment binary using the client's authenticated session."""
      session = client._session  # noqa: SLF001 — direct session reuse is intentional
      response = session.get(url, timeout=client.timeout)
      if not (200 <= response.status_code < 300):
          raise AdoError(status_code=response.status_code, body=response.text, url=url)
      return response.content


  def _build_document(
      *,
      data: dict[str, Any],
      include_children: bool,
      client: AdoClient,
      org_url: str,
      project: str,
  ) -> dict[str, Any]:
      fields = data.get("fields", {}) or {}
      relations = data.get("relations", []) or []

      attachment_refs, child_ids = _split_relations(relations)

      work_item_type = str(fields.get("System.WorkItemType") or "User Story")
      pbi_type = _classify_pbi_type(work_item_type)
      priority_raw = fields.get("Microsoft.VSTS.Common.Priority")
      priority = int(priority_raw) if isinstance(priority_raw, int) else None
      severity_label_raw = fields.get("Microsoft.VSTS.Common.Severity")
      severity_label = (
          str(severity_label_raw) if isinstance(severity_label_raw, str) else None
      )
      severity = _classify_severity(priority, severity_label)

      attachments_json: list[dict[str, str]] = []
      for name, url in attachment_refs:
          print(f"downloading attachment {name}...", file=sys.stderr)
          content = _download_attachment(client, url)
          attachments_json.append(
              {
                  "name": name,
                  "url": url,
                  "content_base64": base64.b64encode(content).decode("ascii"),
              }
          )

      body_markdown = _strip_html(str(fields.get("System.Description") or ""))
      acceptance_markdown = _strip_html(
          str(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria") or "")
      )
      repro_markdown = _strip_html(
          str(fields.get("Microsoft.VSTS.TCM.ReproSteps") or "")
      )

      number = int(data["id"])
      source_url = f"{org_url.rstrip('/')}/{project}/_workitems/edit/{number}"

      return {
          "id": f"WI-{number}",
          "type": pbi_type,
          "severity": severity,
          "title": str(fields.get("System.Title") or f"WI-{number}"),
          "body_markdown": body_markdown,
          "acceptance_markdown": acceptance_markdown,
          "repro_steps_markdown": repro_markdown,
          "attachments": attachments_json,
          "child_ids": [f"WI-{cid}" for cid in child_ids] if include_children else [],
          "parent_id": None,
          "source_url": source_url,
          "source_host": "ado",
          "raw": {
              "work_item_type": work_item_type,
              "priority": priority,
              "severity_label": severity_label,
          },
      }


  def main(argv: list[str] | None = None) -> int:
      args = _parse_args(argv if argv is not None else sys.argv[1:])
      try:
          pat = _require_env("ADO_PAT")
          org_url = _require_env("ADO_ORG_URL")
          project = _require_env("ADO_PROJECT")
          work_item_id = _parse_work_item_id(args.work_item)

          print(f"fetching ADO work item {work_item_id}...", file=sys.stderr)
          client = AdoClient(org_url=org_url, project=project, pat=pat)
          data = client.get(
              f"wit/workitems/{work_item_id}",
              params={"$expand": "relations"},
          )
          if not isinstance(data, dict):
              raise _FatalError("ADO work item response was not a JSON object")

          document = _build_document(
              data=data,
              include_children=args.include_children,
              client=client,
              org_url=org_url,
              project=project,
          )
          errors = validate(document)
          if errors:
              raise _FatalError(
                  "fetcher produced an invalid document:\n  - "
                  + "\n  - ".join(errors)
              )
          print(json.dumps(document, indent=2, sort_keys=True))
          return 0
      except _FatalError as exc:
          print(f"error: {exc}", file=sys.stderr)
          return 2
      except AdoError as exc:
          print(f"error: ADO request failed: {exc}", file=sys.stderr)
          return 2


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] 2. Run tests; they must pass.

- [ ] 3. Lint and type-check.

- [ ] 4. Commit:
  ```
  git add skills/workitem-fetch-ado/scripts/fetch.py tests/skills/test_workitem_fetch_ado.py
  git commit -m "feat(workitem-fetch-ado): implement ADO Work Items fetcher with normalised JSON output"
  ```

### Phase 2 — ADO — Task D: cross-host integration check — `ralph-add` against the ADO fetcher

**Files**
- Modify: `tests/skills/test_ralph_add.py` (append one test)

**Steps**

- [ ] 1. Append the following test to `tests/skills/test_ralph_add.py`. It exercises the orchestrator-fetcher contract with a real (ADO-shaped) backend by pointing `RALPH_WORKITEM_FETCH_SCRIPT` at the actual `workitem-fetch-ado/scripts/fetch.py` and mocking ADO REST responses. This is the proof that the orchestrator is host-agnostic — same orchestrator, different fetcher, same outcome:
  ```python
  import responses as _responses


  @_responses.activate
  def test_orchestrator_works_with_real_ado_fetcher(
      git_repo: Path,
      add_module: ModuleType,
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      """Cross-host integration: ``ralph-add`` orchestrator + real ADO fetcher."""
      ado_org = "https://dev.azure.com/example-org"
      ado_project = "example-project"
      ado_wi = 4242
      monkeypatch.setenv("ADO_PAT", "fake-pat")
      monkeypatch.setenv("ADO_ORG_URL", ado_org)
      monkeypatch.setenv("ADO_PROJECT", ado_project)
      monkeypatch.setenv(
          "RALPH_WORKITEM_FETCH_SCRIPT",
          str(
              REPO_ROOT
              / "skills"
              / "workitem-fetch-ado"
              / "scripts"
              / "fetch.py"
          ),
      )
      _responses.add(
          _responses.GET,
          f"{ado_org}/{ado_project}/_apis/wit/workitems/{ado_wi}",
          json={
              "id": ado_wi,
              "rev": 1,
              "fields": {
                  "System.WorkItemType": "User Story",
                  "System.Title": "Cross-host integration test",
                  "System.Description": "<p>Test the orchestrator contract end-to-end.</p>",
                  "Microsoft.VSTS.Common.Priority": 3,
              },
              "relations": [],
          },
          status=200,
      )

      exit_code = add_module.main(
          [
              "--work-item",
              str(ado_wi),
              "--repo",
              str(git_repo),
          ]
      )
      assert exit_code == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["pbi_id"] == f"WI-{ado_wi}"
      assert payload["pbi_type"] == "feature"
      assert payload["source_host"] == "ado"
      _git(git_repo, "checkout", "ralph-queue")
      pbi_md = (
          git_repo / ".ralph" / "inbox" / f"WI-{ado_wi}" / "PBI.md"
      ).read_text(encoding="utf-8")
      assert "Cross-host integration test" in pbi_md
      assert "ado:" in pbi_md  # source host noted in body
  ```

- [ ] 2. Run the test:
  ```
  uv run pytest tests/skills/test_ralph_add.py::test_orchestrator_works_with_real_ado_fetcher -v
  ```
  Expected: pass.

- [ ] 3. Run the full toolchain pass (as in Phase 1 Task 8):
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  uv run pytest
  ```
  Expected: all clean. Total test count is now Phase 1 + the four ADO fetcher tests + the cross-host integration test.

- [ ] 4. Commit:
  ```
  git add tests/skills/test_ralph_add.py
  git commit -m "test(ralph-add): cross-host integration check with real ADO fetcher"
  ```

---

## Verification

This is the orchestrator's Plan 3 verification gate. The gate command from `2026-05-24-00-orchestrator.md` is:

> `uv run pytest tests/skills/test_ralph_add.py -v` — All pass

**Phase 1 satisfies this gate by itself.** Phase 2 is deferred; do not block Plan 3's completion on it. The verification has two parts: artifact gate (no network, hermetic) and a manual rehearsal against a real GitHub repository.

### Part 1 — Phase 1 artifact gate (no network)

- [ ] 1. Run the per-skill test selections in isolation:
  ```
  uv run pytest tests/skills/test_ralph_add.py tests/skills/test_workitem_fetch_github.py -v
  ```
  Expected: thirteen tests in `test_ralph_add.py` + five tests in `test_workitem_fetch_github.py` = eighteen passing.

- [ ] 2. Run the full repo gate to confirm Plan 3 didn't disturb Plans 1 or 2:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts skills tests
  uv run pytest
  ```
  Expected: every command exits 0.

- [ ] 3. Confirm both skills are discoverable as Claude Code skills (their `SKILL.md` frontmatter parses):
  ```
  uv run python -c "import yaml; from pathlib import Path; \
    for p in ['skills/ralph-add/SKILL.md', 'skills/workitem-fetch-github/SKILL.md']: \
      text = Path(p).read_text(encoding='utf-8'); lines = text.splitlines(); \
      assert lines[0] == '---', f'{p} must start with frontmatter fence'; \
      end = next(i for i, line in enumerate(lines[1:], start=1) if line == '---'); \
      fm = yaml.safe_load('\n'.join(lines[1:end])); \
      assert isinstance(fm['name'], str) and isinstance(fm['description'], str); \
      print(p, '→', fm['name'])"
  ```
  Expected output: two lines confirming both `name` fields.

- [ ] 4. Confirm the orchestrator/fetcher contract: prove `ralph-add` works with a non-GitHub backend by pointing it at a fake fetcher script in tmp_path that emits a valid normalised document (this is what the `workitem-fetch-mock` fixture skill represents). The Task 5 tests already exercise this — record it as an explicit verification checkpoint here so the orchestrator-vs-fetcher split is provably load-bearing.

### Part 2 — Phase 1 orchestrator gate on a real GitHub issue (manual; once per environment)

- [ ] 5. From a checkout where Plan 2's GitHub runbook has been executed (so `origin/ralph-queue` exists on the chosen test repo), export the env vars:
  ```
  export GH_TOKEN="<your GitHub PAT>"
  export GH_OWNER="<your GitHub org or user>"
  export RALPH_GIT_HOST=github
  ```

- [ ] 6. Pick a small GitHub issue on a service repo and submit it:
  ```
  uv run python skills/ralph-add/scripts/add.py \
      --work-item <issue-number> \
      --repo /path/to/checked-out/service-repo
  ```
  Expected: exit code 0; JSON summary with `"pushed": true`, `"source_host": "github"`, and `"commit_sha"` set; the `.ralph/inbox/WI-<n>/` directory exists on the `ralph-queue` branch of the service repo.

- [ ] 7. Pull the branch and validate the entry:
  ```
  uv run python scripts/validate_samples.py /path/to/service-repo/.ralph/inbox
  ```
  (Note: per orchestrator reconciliation note 3, the validator's directory-name-prefix check is enforced only on the `samples/` tree; the on-disk `WI-<n>` form is canonical and the frontmatter-driven validation in Task 7 is the strict check.)

- [ ] 8. Re-run the same command and confirm idempotency: `git commit` fails because the inbox entry exists; the skill exits 2 with a clear git error. This is the expected behaviour — `ralph-add` is single-submission per work item.

### Part 3 — Phase 2 (deferred)

Phase 2's verification is identical in shape but exercises the ADO fetcher against a real ADO instance with `RALPH_GIT_HOST=ado`. Run after Phase 2 tasks A–D are complete. Phase 2 is NOT required for Plan 3's gate.

If Parts 1 + 2 all pass, **Plan 3 is complete**. The next plan in the dependency graph is Plan 10 (the remaining supervisor skills, which reuse the `ralph-add` scaffolding pattern); Plan 4 (`ralph-status`, which reads from `ralph-queue`) can proceed in parallel as soon as Plan 2 is verified.
