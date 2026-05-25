# `ralph-queue` Branch + ADO Branch Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ralph-queue` available on a service repo with the right protections in place. Deliver two artifacts: (a) `docs/runbooks/ralph-queue-setup.md` — a manual setup runbook that lets a human bootstrap `ralph-queue` on any Azure DevOps service repo from the UI or `az` CLI, and (b) `scripts/setup_ralph_queue.py` — a Python helper that automates the same flow against the ADO REST API using a PAT, prints a JSON summary on stdout, and is fully unit-tested with a mocked ADO REST surface.

**Architecture:** A thin imperative script in `scripts/setup_ralph_queue.py` that talks to ADO REST 7.1. It performs four discrete REST calls in sequence: look up the repository ID by name, read `main`'s tip SHA, create the `ralph-queue` ref off that SHA, then create (or update) the branch policy entries that (1) require pull requests on `main` so that no PR can come *from* `ralph-queue` to `main` via auto-merge — this is enforced by listing `refs/heads/ralph-queue` as a `filenamePatterns` exclusion on a `Require a Merge Strategy` / `Required Reviewer` policy — and (2) restrict push access on `refs/heads/ralph-queue` to a declared service-principal identity via an ACL entry on the repository's security namespace. The script does no git operations — it never clones the repo. All side effects are ADO API calls. JSON output to stdout is the source of truth for "what happened"; stderr carries human-readable progress. Tests use `responses` to mock every endpoint the script touches; no network in CI.

**Tech Stack:** Python 3.12+, `uv`, `requests` (HTTP), `responses` (test-only HTTP mock), pytest, ruff, mypy strict, Azure DevOps REST API 7.1.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Add `requests` to runtime dependencies and `responses` to the dev group. No other change. |
| `scripts/setup_ralph_queue.py` | The helper. Argparse CLI; reads `ADO_PAT` / `ADO_ORG_URL` / `ADO_PROJECT` from env; creates the branch, applies policies, verifies, and prints a JSON summary. |
| `scripts/ado_client.py` | Tiny dependency-light wrapper around `requests.Session` that handles base URL composition, PAT auth, JSON serialization, and `Accept: application/json;api-version=7.1` headers. Used by `setup_ralph_queue.py`; will be reused by Plan 5 (`ado-pr` skill) and Plan 3 (`ralph-add` skill). |
| `tests/test_setup_ralph_queue.py` | Pytest tests covering: happy path, branch-already-exists, repo-not-found, missing-env-var, idempotent re-run, JSON output shape, exit codes. Uses `responses` to mock every ADO endpoint. |
| `tests/test_ado_client.py` | Unit tests for `ado_client.AdoClient`: URL composition, PAT header, request retries, error mapping. |
| `docs/runbooks/ralph-queue-setup.md` | Human-facing runbook: what `ralph-queue` is, prerequisites, manual steps in the ADO UI, equivalent `az` CLI steps, verification. |

---

## Task 1 — Add HTTP dependencies

**Files**
- Modify: `pyproject.toml`

**Steps**

- [ ] 1. Confirm Plan 1 was completed (you should be standing on a tree that has the workspace bootstrap already merged):
  ```
  uv run pytest tests/ -k workspace
  ```
  Expected: all Plan 1 tests pass. If they do not, STOP — Plan 2 depends on Plan 1's bootstrap.

- [ ] 2. Add `requests` to the runtime dependency set. Run:
  ```
  uv add requests
  ```
  Expected: `pyproject.toml` is updated with a `requests>=2.32` line in `[project].dependencies`; `uv.lock` is written; no errors.

- [ ] 3. Add `responses` and `types-requests` to the dev dependency group. Run:
  ```
  uv add --dev responses types-requests
  ```
  Expected: both packages appear under `[dependency-groups].dev` in `pyproject.toml`.

- [ ] 4. Re-sync and verify the new packages import cleanly:
  ```
  uv sync
  uv run python -c "import requests, responses; print(requests.__version__, responses.__version__)"
  ```
  Expected: a version line such as `2.32.3 0.25.3` (exact versions may differ). No `ImportError`.

- [ ] 5. Commit the dependency change:
  ```
  git add pyproject.toml uv.lock
  git commit -m "chore(deps): add requests, responses, types-requests for ADO REST helper"
  ```
  Expected: commit succeeds.

---

## Task 2 — Write the failing `ado_client` test

**Files**
- Create: `tests/test_ado_client.py`

**Steps**

- [ ] 1. Write `tests/test_ado_client.py` with the exact content below. The test imports `scripts.ado_client`, which does not yet exist, so the failure mode is `ModuleNotFoundError`:
  ```python
  """Unit tests for ``scripts.ado_client.AdoClient``.

  The client wraps ``requests.Session`` with PAT auth and ADO's REST 7.1
  conventions. These tests use the ``responses`` library to mock every
  HTTP exchange.
  """
  from __future__ import annotations

  import base64

  import pytest
  import responses

  from scripts.ado_client import AdoClient, AdoError


  ORG = "https://dev.azure.com/example-org"
  PROJECT = "example-project"
  PAT = "fake-pat-value"


  @responses.activate
  def test_get_composes_url_and_sets_pat_header() -> None:
      responses.add(
          responses.GET,
          f"{ORG}/{PROJECT}/_apis/git/repositories",
          json={"value": []},
          status=200,
          match=[
              responses.matchers.query_param_matcher({"api-version": "7.1"}),
          ],
      )

      client = AdoClient(org_url=ORG, project=PROJECT, pat=PAT)
      result = client.get("git/repositories")

      assert result == {"value": []}
      call = responses.calls[0]
      expected_auth = "Basic " + base64.b64encode(f":{PAT}".encode()).decode()
      assert call.request.headers["Authorization"] == expected_auth
      assert call.request.headers["Accept"].startswith("application/json")


  @responses.activate
  def test_post_sends_json_body() -> None:
      responses.add(
          responses.POST,
          f"{ORG}/{PROJECT}/_apis/git/repositories/abc/refs",
          json={"value": [{"name": "refs/heads/ralph-queue"}]},
          status=200,
      )

      client = AdoClient(org_url=ORG, project=PROJECT, pat=PAT)
      result = client.post(
          "git/repositories/abc/refs",
          json_body=[{"name": "refs/heads/ralph-queue", "oldObjectId": "0" * 40, "newObjectId": "a" * 40}],
      )

      assert result == {"value": [{"name": "refs/heads/ralph-queue"}]}
      call = responses.calls[0]
      assert call.request.headers["Content-Type"] == "application/json"


  @responses.activate
  def test_non_2xx_raises_ado_error_with_status_and_body() -> None:
      responses.add(
          responses.GET,
          f"{ORG}/{PROJECT}/_apis/git/repositories/nope",
          json={"message": "TF401019: The Git repository nope does not exist."},
          status=404,
      )

      client = AdoClient(org_url=ORG, project=PROJECT, pat=PAT)
      with pytest.raises(AdoError) as excinfo:
          client.get("git/repositories/nope")

      assert excinfo.value.status_code == 404
      assert "TF401019" in str(excinfo.value)


  def test_strips_trailing_slash_from_org_url() -> None:
      client = AdoClient(org_url=ORG + "/", project=PROJECT, pat=PAT)
      assert client.org_url == ORG


  def test_rejects_empty_pat() -> None:
      with pytest.raises(ValueError, match="pat must be a non-empty string"):
          AdoClient(org_url=ORG, project=PROJECT, pat="")
  ```

- [ ] 2. Run the test to confirm the expected red:
  ```
  uv run pytest tests/test_ado_client.py -v
  ```
  Expected: collection error or test failure with `ModuleNotFoundError: No module named 'scripts.ado_client'`.

- [ ] 3. Do NOT commit yet — the failing test is consumed in Task 3.

---

## Task 3 — Implement `ado_client`

**Files**
- Create: `scripts/ado_client.py`

**Steps**

- [ ] 1. Write `scripts/ado_client.py` with the exact content below:
  ```python
  """Tiny ADO REST 7.1 client used by the Ralph setup and skill scripts.

  Wraps ``requests.Session`` with:
    - PAT authentication (Basic with empty username, PAT in password slot)
    - the default ``api-version=7.1`` query parameter
    - JSON encoding of bodies
    - a ``AdoError`` raised on non-2xx responses
  """
  from __future__ import annotations

  import base64
  from typing import Any
  from urllib.parse import urljoin

  import requests


  DEFAULT_API_VERSION = "7.1"
  DEFAULT_TIMEOUT_SECONDS = 30.0


  class AdoError(RuntimeError):
      """Raised when the ADO REST API returns a non-2xx status."""

      def __init__(self, status_code: int, body: str, url: str) -> None:
          super().__init__(f"ADO {status_code} from {url}: {body}")
          self.status_code = status_code
          self.body = body
          self.url = url


  class AdoClient:
      """Minimal ADO REST client. One instance per (org, project) pair."""

      def __init__(
          self,
          org_url: str,
          project: str,
          pat: str,
          *,
          api_version: str = DEFAULT_API_VERSION,
          timeout: float = DEFAULT_TIMEOUT_SECONDS,
          session: requests.Session | None = None,
      ) -> None:
          if not isinstance(pat, str) or not pat.strip():
              raise ValueError("pat must be a non-empty string")
          if not isinstance(org_url, str) or not org_url.strip():
              raise ValueError("org_url must be a non-empty string")
          if not isinstance(project, str) or not project.strip():
              raise ValueError("project must be a non-empty string")

          self.org_url = org_url.rstrip("/")
          self.project = project
          self.api_version = api_version
          self.timeout = timeout
          self._session = session or requests.Session()

          token = base64.b64encode(f":{pat}".encode()).decode()
          self._session.headers.update(
              {
                  "Authorization": f"Basic {token}",
                  "Accept": f"application/json;api-version={api_version}",
              }
          )

      def _build_url(self, path: str, *, project_scoped: bool = True) -> str:
          path = path.lstrip("/")
          if project_scoped:
              base = f"{self.org_url}/{self.project}/_apis/"
          else:
              base = f"{self.org_url}/_apis/"
          return urljoin(base, path)

      def _request(
          self,
          method: str,
          path: str,
          *,
          json_body: Any = None,
          params: dict[str, str] | None = None,
          project_scoped: bool = True,
          extra_headers: dict[str, str] | None = None,
      ) -> Any:
          url = self._build_url(path, project_scoped=project_scoped)
          merged_params: dict[str, str] = {"api-version": self.api_version}
          if params:
              merged_params.update(params)

          headers: dict[str, str] = {}
          if json_body is not None:
              headers["Content-Type"] = "application/json"
          if extra_headers:
              headers.update(extra_headers)

          response = self._session.request(
              method,
              url,
              params=merged_params,
              json=json_body,
              headers=headers or None,
              timeout=self.timeout,
          )
          if not (200 <= response.status_code < 300):
              raise AdoError(response.status_code, response.text, response.url)
          if not response.content:
              return None
          return response.json()

      def get(
          self,
          path: str,
          *,
          params: dict[str, str] | None = None,
          project_scoped: bool = True,
      ) -> Any:
          return self._request("GET", path, params=params, project_scoped=project_scoped)

      def post(
          self,
          path: str,
          *,
          json_body: Any,
          params: dict[str, str] | None = None,
          project_scoped: bool = True,
      ) -> Any:
          return self._request(
              "POST",
              path,
              json_body=json_body,
              params=params,
              project_scoped=project_scoped,
          )

      def put(
          self,
          path: str,
          *,
          json_body: Any,
          params: dict[str, str] | None = None,
          project_scoped: bool = True,
      ) -> Any:
          return self._request(
              "PUT",
              path,
              json_body=json_body,
              params=params,
              project_scoped=project_scoped,
          )

      def patch(
          self,
          path: str,
          *,
          json_body: Any,
          params: dict[str, str] | None = None,
          project_scoped: bool = True,
      ) -> Any:
          return self._request(
              "PATCH",
              path,
              json_body=json_body,
              params=params,
              project_scoped=project_scoped,
          )
  ```

- [ ] 2. Run the `ado_client` tests; they must now pass:
  ```
  uv run pytest tests/test_ado_client.py -v
  ```
  Expected: all five tests in `tests/test_ado_client.py` pass. Exit code 0.

- [ ] 3. Run ruff and mypy against the new files:
  ```
  uv run ruff check scripts/ado_client.py tests/test_ado_client.py
  uv run mypy scripts/ado_client.py tests/test_ado_client.py
  ```
  Expected: ruff: `All checks passed!`. Mypy: `Success: no issues found in 2 source files`.

- [ ] 4. Commit:
  ```
  git add scripts/ado_client.py tests/test_ado_client.py
  git commit -m "feat(ado): add minimal PAT-authenticated ADO REST 7.1 client"
  ```
  Expected: commit succeeds.

---

## Task 4 — Write the failing `setup_ralph_queue` test

**Files**
- Create: `tests/test_setup_ralph_queue.py`

**Steps**

- [ ] 1. Write `tests/test_setup_ralph_queue.py` with the exact content below. It imports `scripts.setup_ralph_queue`, which does not yet exist:
  ```python
  """Tests for ``scripts.setup_ralph_queue``.

  Every ADO REST endpoint the script touches is mocked with ``responses``.
  No network calls happen during the test run.
  """
  from __future__ import annotations

  import json
  from typing import Any

  import pytest
  import responses

  from scripts import setup_ralph_queue


  ORG = "https://dev.azure.com/example-org"
  PROJECT = "example-project"
  REPO = "service-auth"
  REPO_ID = "11111111-2222-3333-4444-555555555555"
  PAT = "fake-pat-value"
  MAIN_SHA = "a" * 40
  SERVICE_PRINCIPAL_DESCRIPTOR = "aad.ZTU2NjBmZmEtNzhjMC03NDQ3LTk1Y2QtZjM4YTE0YjU2YjE0"


  REPOS_URL = f"{ORG}/{PROJECT}/_apis/git/repositories"
  REFS_URL = f"{ORG}/{PROJECT}/_apis/git/repositories/{REPO_ID}/refs"
  POLICY_CONFIG_URL = f"{ORG}/{PROJECT}/_apis/policy/configurations"
  IDENTITIES_URL = f"{ORG}/_apis/identities"


  @pytest.fixture
  def env(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("ADO_PAT", PAT)
      monkeypatch.setenv("ADO_ORG_URL", ORG)
      monkeypatch.setenv("ADO_PROJECT", PROJECT)


  def _register_repo_lookup(name: str = REPO, repo_id: str = REPO_ID) -> None:
      responses.add(
          responses.GET,
          f"{REPOS_URL}/{name}",
          json={"id": repo_id, "name": name, "defaultBranch": "refs/heads/main"},
          status=200,
      )


  def _register_main_tip(sha: str = MAIN_SHA, repo_id: str = REPO_ID) -> None:
      responses.add(
          responses.GET,
          f"{ORG}/{PROJECT}/_apis/git/repositories/{repo_id}/refs",
          json={"value": [{"name": "refs/heads/main", "objectId": sha}]},
          status=200,
          match=[
              responses.matchers.query_param_matcher(
                  {"api-version": "7.1", "filter": "heads/main"}
              ),
          ],
      )


  def _register_queue_branch_absent(repo_id: str = REPO_ID) -> None:
      responses.add(
          responses.GET,
          f"{ORG}/{PROJECT}/_apis/git/repositories/{repo_id}/refs",
          json={"value": []},
          status=200,
          match=[
              responses.matchers.query_param_matcher(
                  {"api-version": "7.1", "filter": "heads/ralph-queue"}
              ),
          ],
      )


  def _register_queue_branch_create(sha: str = MAIN_SHA, repo_id: str = REPO_ID) -> None:
      responses.add(
          responses.POST,
          f"{ORG}/{PROJECT}/_apis/git/repositories/{repo_id}/refs",
          json={
              "value": [
                  {
                      "name": "refs/heads/ralph-queue",
                      "newObjectId": sha,
                      "success": True,
                  }
              ]
          },
          status=200,
      )


  def _register_identity_lookup(descriptor: str = SERVICE_PRINCIPAL_DESCRIPTOR) -> None:
      responses.add(
          responses.GET,
          IDENTITIES_URL,
          json={
              "value": [
                  {
                      "descriptor": descriptor,
                      "providerDisplayName": "ralph-service-principal",
                  }
              ]
          },
          status=200,
      )


  def _register_policy_list_empty() -> None:
      responses.add(
          responses.GET,
          POLICY_CONFIG_URL,
          json={"value": []},
          status=200,
      )


  def _register_policy_create(policy_id: int) -> None:
      responses.add(
          responses.POST,
          POLICY_CONFIG_URL,
          json={"id": policy_id, "isEnabled": True},
          status=200,
      )


  @responses.activate
  def test_happy_path_returns_zero_and_prints_json(
      env: None, capsys: pytest.CaptureFixture[str]
  ) -> None:
      _register_repo_lookup()
      _register_main_tip()
      _register_queue_branch_absent()
      _register_queue_branch_create()
      _register_identity_lookup()
      _register_policy_list_empty()
      _register_policy_create(policy_id=4242)

      exit_code = setup_ralph_queue.main(
          [
              "--repo",
              REPO,
              "--service-principal",
              "ralph-service-principal@example.com",
          ]
      )
      assert exit_code == 0

      captured = capsys.readouterr()
      payload: dict[str, Any] = json.loads(captured.out)
      assert payload["repo"] == REPO
      assert payload["repo_id"] == REPO_ID
      assert payload["branch_created"] is True
      assert payload["branch_name"] == "ralph-queue"
      assert payload["branch_base_sha"] == MAIN_SHA
      assert payload["policies_created"] == [4242]
      assert payload["service_principal_descriptor"] == SERVICE_PRINCIPAL_DESCRIPTOR


  @responses.activate
  def test_idempotent_when_branch_already_exists(
      env: None, capsys: pytest.CaptureFixture[str]
  ) -> None:
      _register_repo_lookup()
      _register_main_tip()
      responses.add(
          responses.GET,
          f"{ORG}/{PROJECT}/_apis/git/repositories/{REPO_ID}/refs",
          json={
              "value": [
                  {"name": "refs/heads/ralph-queue", "objectId": MAIN_SHA}
              ]
          },
          status=200,
          match=[
              responses.matchers.query_param_matcher(
                  {"api-version": "7.1", "filter": "heads/ralph-queue"}
              ),
          ],
      )
      _register_identity_lookup()
      responses.add(
          responses.GET,
          POLICY_CONFIG_URL,
          json={
              "value": [
                  {
                      "id": 4242,
                      "type": {"id": "fa4ab4cc-9c8c-4d7b-9d23-3a6c2c0a13c0"},
                      "settings": {
                          "scope": [
                              {
                                  "repositoryId": REPO_ID,
                                  "refName": "refs/heads/ralph-queue",
                                  "matchKind": "Exact",
                              }
                          ]
                      },
                      "isEnabled": True,
                  }
              ]
          },
          status=200,
      )

      exit_code = setup_ralph_queue.main(
          [
              "--repo",
              REPO,
              "--service-principal",
              "ralph-service-principal@example.com",
          ]
      )
      assert exit_code == 0

      payload = json.loads(capsys.readouterr().out)
      assert payload["branch_created"] is False
      assert payload["branch_existed"] is True
      assert payload["policies_created"] == []
      assert payload["policies_existing"] == [4242]


  @responses.activate
  def test_repo_not_found_exits_nonzero(env: None, capsys: pytest.CaptureFixture[str]) -> None:
      responses.add(
          responses.GET,
          f"{REPOS_URL}/{REPO}",
          json={"message": "TF401019: The Git repository service-auth does not exist."},
          status=404,
      )

      exit_code = setup_ralph_queue.main(
          [
              "--repo",
              REPO,
              "--service-principal",
              "ralph-service-principal@example.com",
          ]
      )
      assert exit_code == 2
      stderr = capsys.readouterr().err
      assert "TF401019" in stderr


  def test_missing_env_var_exits_nonzero(
      monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
  ) -> None:
      monkeypatch.delenv("ADO_PAT", raising=False)
      monkeypatch.setenv("ADO_ORG_URL", ORG)
      monkeypatch.setenv("ADO_PROJECT", PROJECT)

      exit_code = setup_ralph_queue.main(
          [
              "--repo",
              REPO,
              "--service-principal",
              "ralph-service-principal@example.com",
          ]
      )
      assert exit_code == 2
      assert "ADO_PAT" in capsys.readouterr().err


  @responses.activate
  def test_dry_run_makes_no_mutations(
      env: None, capsys: pytest.CaptureFixture[str]
  ) -> None:
      _register_repo_lookup()
      _register_main_tip()
      _register_queue_branch_absent()
      _register_identity_lookup()
      _register_policy_list_empty()

      exit_code = setup_ralph_queue.main(
          [
              "--repo",
              REPO,
              "--service-principal",
              "ralph-service-principal@example.com",
              "--dry-run",
          ]
      )
      assert exit_code == 0

      payload = json.loads(capsys.readouterr().out)
      assert payload["dry_run"] is True
      assert payload["branch_created"] is False
      assert payload["policies_created"] == []
      # No POSTs should have been issued in dry-run mode.
      assert all(call.request.method == "GET" for call in responses.calls)
  ```

- [ ] 2. Run the test to confirm the expected red:
  ```
  uv run pytest tests/test_setup_ralph_queue.py -v
  ```
  Expected: collection failure with `ModuleNotFoundError: No module named 'scripts.setup_ralph_queue'`.

- [ ] 3. Do NOT commit yet — the failing test is consumed in Task 5.

---

## Task 5 — Implement `setup_ralph_queue.py`

**Files**
- Create: `scripts/setup_ralph_queue.py`

**Steps**

- [ ] 1. Write `scripts/setup_ralph_queue.py` with the exact content below:
  ```python
  """Set up the ``ralph-queue`` branch and its policies on an ADO repo.

  Reads ``ADO_PAT``, ``ADO_ORG_URL``, and ``ADO_PROJECT`` from the environment.
  Performs (in order):
    1. Resolve the repository ID by name.
    2. Read the tip commit of ``main``.
    3. Create ``refs/heads/ralph-queue`` off that tip (skip if it exists).
    4. Resolve the service-principal identity descriptor.
    5. Read existing branch policies scoped to ``refs/heads/ralph-queue``.
    6. Create a ``Require a Reviewer`` policy (which guards merges to a branch)
       scoped to ``refs/heads/ralph-queue`` if no equivalent policy exists.
    7. Print a JSON summary to stdout and exit 0.

  ADO REST endpoints used (all api-version=7.1):

      GET    {org}/{project}/_apis/git/repositories/{repo}
      GET    {org}/{project}/_apis/git/repositories/{repoId}/refs?filter=heads/main
      GET    {org}/{project}/_apis/git/repositories/{repoId}/refs?filter=heads/ralph-queue
      POST   {org}/{project}/_apis/git/repositories/{repoId}/refs
      GET    {org}/_apis/identities?searchFilter=General&filterValue={principal}
      GET    {org}/{project}/_apis/policy/configurations
      POST   {org}/{project}/_apis/policy/configurations

  See https://learn.microsoft.com/en-us/rest/api/azure/devops/git/refs and
  https://learn.microsoft.com/en-us/rest/api/azure/devops/policy/configurations
  for the upstream documentation of the request/response shapes.
  """
  from __future__ import annotations

  import argparse
  import json
  import os
  import sys
  from dataclasses import asdict, dataclass, field
  from typing import Any

  from scripts.ado_client import AdoClient, AdoError


  QUEUE_BRANCH = "ralph-queue"
  MAIN_BRANCH = "main"
  ZERO_SHA = "0" * 40

  # The "Require a Reviewer" policy type id is stable across ADO instances.
  # Documented at
  # https://learn.microsoft.com/en-us/rest/api/azure/devops/policy/types/list
  POLICY_TYPE_REQUIRE_REVIEWER = "fa4ab4cc-9c8c-4d7b-9d23-3a6c2c0a13c0"


  @dataclass
  class SetupResult:
      repo: str
      repo_id: str
      branch_name: str
      branch_base_sha: str
      branch_created: bool
      branch_existed: bool
      service_principal: str
      service_principal_descriptor: str
      policies_created: list[int] = field(default_factory=list)
      policies_existing: list[int] = field(default_factory=list)
      dry_run: bool = False


  def _require_env(name: str) -> str:
      value = os.environ.get(name, "").strip()
      if not value:
          raise SystemExit(_fail(f"environment variable {name} is required"))
      return value


  def _fail(message: str) -> int:
      print(f"error: {message}", file=sys.stderr)
      return 2


  def _lookup_repo(client: AdoClient, repo_name: str) -> dict[str, Any]:
      return client.get(f"git/repositories/{repo_name}")


  def _read_branch_tip(
      client: AdoClient, repo_id: str, branch: str
  ) -> str | None:
      """Return the tip ``objectId`` of ``refs/heads/{branch}`` or ``None``."""
      data = client.get(
          f"git/repositories/{repo_id}/refs",
          params={"filter": f"heads/{branch}"},
      )
      refs = data.get("value", []) if isinstance(data, dict) else []
      for ref in refs:
          if ref.get("name") == f"refs/heads/{branch}":
              return str(ref["objectId"])
      return None


  def _create_branch(
      client: AdoClient, repo_id: str, branch: str, base_sha: str
  ) -> None:
      payload = [
          {
              "name": f"refs/heads/{branch}",
              "oldObjectId": ZERO_SHA,
              "newObjectId": base_sha,
          }
      ]
      response = client.post(
          f"git/repositories/{repo_id}/refs",
          json_body=payload,
      )
      value = response.get("value", []) if isinstance(response, dict) else []
      if not value or not value[0].get("success", False):
          raise AdoError(
              status_code=500,
              body=json.dumps(response),
              url=f"git/repositories/{repo_id}/refs",
          )


  def _resolve_identity_descriptor(client: AdoClient, principal: str) -> str:
      data = client.get(
          "identities",
          params={"searchFilter": "General", "filterValue": principal},
          project_scoped=False,
      )
      identities = data.get("value", []) if isinstance(data, dict) else []
      if not identities:
          raise AdoError(
              status_code=404,
              body=f"identity not found for principal {principal!r}",
              url="identities",
          )
      descriptor = identities[0].get("descriptor")
      if not descriptor:
          raise AdoError(
              status_code=500,
              body=f"identity response missing 'descriptor' for {principal!r}",
              url="identities",
          )
      return str(descriptor)


  def _existing_queue_policy_ids(
      client: AdoClient, repo_id: str, branch: str
  ) -> list[int]:
      data = client.get("policy/configurations")
      configs = data.get("value", []) if isinstance(data, dict) else []
      matched: list[int] = []
      target_ref = f"refs/heads/{branch}"
      for cfg in configs:
          type_info = cfg.get("type", {})
          if type_info.get("id") != POLICY_TYPE_REQUIRE_REVIEWER:
              continue
          settings = cfg.get("settings", {}) or {}
          for scope in settings.get("scope", []) or []:
              if (
                  scope.get("repositoryId") == repo_id
                  and scope.get("refName") == target_ref
              ):
                  matched.append(int(cfg["id"]))
                  break
      return matched


  def _create_queue_policy(
      client: AdoClient,
      repo_id: str,
      branch: str,
      reviewer_descriptor: str,
  ) -> int:
      payload = {
          "isEnabled": True,
          "isBlocking": True,
          "type": {"id": POLICY_TYPE_REQUIRE_REVIEWER},
          "settings": {
              "requiredReviewerIds": [reviewer_descriptor],
              "minimumApproverCount": 1,
              "creatorVoteCounts": False,
              "scope": [
                  {
                      "repositoryId": repo_id,
                      "refName": f"refs/heads/{branch}",
                      "matchKind": "Exact",
                  }
              ],
          },
      }
      response = client.post("policy/configurations", json_body=payload)
      if not isinstance(response, dict) or "id" not in response:
          raise AdoError(
              status_code=500,
              body=json.dumps(response),
              url="policy/configurations",
          )
      return int(response["id"])


  def _parse_args(argv: list[str]) -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          description=(
              "Create the ralph-queue branch on an ADO repo and apply the "
              "required policies. Reads ADO_PAT, ADO_ORG_URL, ADO_PROJECT "
              "from the environment."
          )
      )
      parser.add_argument(
          "--repo",
          required=True,
          help="Name of the ADO Git repository (not the project).",
      )
      parser.add_argument(
          "--service-principal",
          required=True,
          help=(
              "Identifier (UPN, group name, or descriptor) of the identity "
              "scoped as the queue-branch reviewer / writer. The script "
              "resolves it via the identities REST endpoint."
          ),
      )
      parser.add_argument(
          "--branch",
          default=QUEUE_BRANCH,
          help=f"Queue branch name (default: {QUEUE_BRANCH}).",
      )
      parser.add_argument(
          "--base-branch",
          default=MAIN_BRANCH,
          help=f"Base branch the queue branch is created from (default: {MAIN_BRANCH}).",
      )
      parser.add_argument(
          "--dry-run",
          action="store_true",
          help="Read state but do not POST any mutations.",
      )
      return parser.parse_args(argv)


  def main(argv: list[str] | None = None) -> int:
      args = _parse_args(argv if argv is not None else sys.argv[1:])

      try:
          pat = _require_env("ADO_PAT")
          org_url = _require_env("ADO_ORG_URL")
          project = _require_env("ADO_PROJECT")
      except SystemExit as exc:
          return int(exc.code) if isinstance(exc.code, int) else 2

      client = AdoClient(org_url=org_url, project=project, pat=pat)

      try:
          print(f"resolving repo {args.repo!r}...", file=sys.stderr)
          repo = _lookup_repo(client, args.repo)
          repo_id = str(repo["id"])

          print(f"reading tip of {args.base_branch}...", file=sys.stderr)
          base_sha = _read_branch_tip(client, repo_id, args.base_branch)
          if base_sha is None:
              return _fail(
                  f"base branch {args.base_branch!r} has no tip in repo "
                  f"{args.repo!r}"
              )

          print(
              f"checking whether {args.branch} already exists...",
              file=sys.stderr,
          )
          existing_tip = _read_branch_tip(client, repo_id, args.branch)
          branch_existed = existing_tip is not None
          branch_created = False
          if not branch_existed:
              if args.dry_run:
                  print(
                      f"DRY-RUN would create {args.branch} off {base_sha}",
                      file=sys.stderr,
                  )
              else:
                  print(
                      f"creating {args.branch} off {base_sha}...",
                      file=sys.stderr,
                  )
                  _create_branch(client, repo_id, args.branch, base_sha)
                  branch_created = True

          print(
              f"resolving identity for {args.service_principal!r}...",
              file=sys.stderr,
          )
          descriptor = _resolve_identity_descriptor(
              client, args.service_principal
          )

          print("listing existing branch policies...", file=sys.stderr)
          existing_policies = _existing_queue_policy_ids(
              client, repo_id, args.branch
          )
          created_policy_ids: list[int] = []
          if not existing_policies:
              if args.dry_run:
                  print(
                      "DRY-RUN would create Require-Reviewer policy on "
                      f"refs/heads/{args.branch}",
                      file=sys.stderr,
                  )
              else:
                  print("creating Require-Reviewer policy...", file=sys.stderr)
                  policy_id = _create_queue_policy(
                      client, repo_id, args.branch, descriptor
                  )
                  created_policy_ids.append(policy_id)
          else:
              print(
                  f"found {len(existing_policies)} existing policies for "
                  f"refs/heads/{args.branch}; skipping creation",
                  file=sys.stderr,
              )

          result = SetupResult(
              repo=args.repo,
              repo_id=repo_id,
              branch_name=args.branch,
              branch_base_sha=base_sha,
              branch_created=branch_created,
              branch_existed=branch_existed,
              service_principal=args.service_principal,
              service_principal_descriptor=descriptor,
              policies_created=created_policy_ids,
              policies_existing=existing_policies,
              dry_run=args.dry_run,
          )
          print(json.dumps(asdict(result), indent=2, sort_keys=True))
          return 0

      except AdoError as exc:
          return _fail(str(exc))


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] 2. Run the `setup_ralph_queue` tests; they must now all pass:
  ```
  uv run pytest tests/test_setup_ralph_queue.py -v
  ```
  Expected: all five tests pass. Exit code 0.

- [ ] 3. Run ruff and mypy:
  ```
  uv run ruff check scripts/setup_ralph_queue.py tests/test_setup_ralph_queue.py
  uv run mypy scripts/setup_ralph_queue.py tests/test_setup_ralph_queue.py
  ```
  Expected: ruff: `All checks passed!`. Mypy: `Success: no issues found in 2 source files`.

- [ ] 4. Commit:
  ```
  git add scripts/setup_ralph_queue.py tests/test_setup_ralph_queue.py
  git commit -m "feat(setup): add ralph-queue branch + policy helper script"
  ```
  Expected: commit succeeds.

---

## Task 6 — Write the manual setup runbook

**Files**
- Create: `docs/runbooks/ralph-queue-setup.md`

**Steps**

- [ ] 1. Ensure the runbooks directory exists. Run (PowerShell):
  ```
  New-Item -ItemType Directory -Force -Path docs/runbooks | Out-Null
  ```
  Or (bash):
  ```
  mkdir -p docs/runbooks
  ```
  Expected: the directory exists; no error.

- [ ] 2. Write `docs/runbooks/ralph-queue-setup.md` with the exact content below:
  ```markdown
  # `ralph-queue` setup runbook

  This runbook covers bootstrapping the `ralph-queue` branch and its protections
  on an Azure DevOps Git repository so that Ralph (the per-repo autonomous
  coding loop) can use it as the queue substrate.

  ## What `ralph-queue` is for

  Ralph keeps its queue state — the `.ralph/` folder tree containing
  `inbox/`, `current/`, `pending-pr/`, `done/`, and `blocked/` — on a dedicated
  long-lived branch called `ralph-queue` in the same project repo as the code
  Ralph is working on. The branch is mutated by two parties only: the human
  supervisor skills (`ralph-add`, `ralph-status`, `ralph-cancel`, `ralph-promote`,
  `ralph-triage`) and the executor itself. PRs are never opened from
  `ralph-queue` to `main`; the branch exists purely to keep queue churn out of
  `main`'s history while still benefiting from git's durability and
  reproducibility.

  ## Prerequisites

  - **An ADO repository.** This runbook assumes the repo already exists and
    has a `main` branch with at least one commit.
  - **A Personal Access Token (PAT).** Scopes required:
    - `Code (Read & Write)` — to create the branch ref.
    - `Code (Status)` — only required if your team uses status policies.
    - `Project & Team (Read)` — to resolve identity descriptors.
    - `Identity (Read)` — to look up the service-principal descriptor.
  - **A service principal (or group) for Ralph.** This is the identity that
    the executor pod and the supervisor skills will authenticate as when
    pushing to `ralph-queue`. The runbook uses the placeholder
    `ralph-service-principal@example.com`; substitute your own.
  - **Environment variables for the helper script** (Task 5 of Plan 2):
    - `ADO_PAT` — the PAT value above.
    - `ADO_ORG_URL` — e.g. `https://dev.azure.com/example-org`.
    - `ADO_PROJECT` — the ADO project name (not the repository name).

  ## Option A — the helper script (recommended)

  Run from a checkout of the ralph repo:

  ```bash
  export ADO_PAT="..."
  export ADO_ORG_URL="https://dev.azure.com/example-org"
  export ADO_PROJECT="example-project"

  uv run python scripts/setup_ralph_queue.py \
      --repo service-auth \
      --service-principal ralph-service-principal@example.com
  ```

  The script is idempotent: re-running it against a repo where `ralph-queue`
  already exists and the policy is already in place is a no-op that exits 0
  and prints a JSON summary describing the existing state.

  Add `--dry-run` to see what would happen without making any changes:

  ```bash
  uv run python scripts/setup_ralph_queue.py \
      --repo service-auth \
      --service-principal ralph-service-principal@example.com \
      --dry-run
  ```

  Sample successful JSON summary (printed to stdout):

  ```json
  {
    "branch_base_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "branch_created": true,
    "branch_existed": false,
    "branch_name": "ralph-queue",
    "dry_run": false,
    "policies_created": [4242],
    "policies_existing": [],
    "repo": "service-auth",
    "repo_id": "11111111-2222-3333-4444-555555555555",
    "service_principal": "ralph-service-principal@example.com",
    "service_principal_descriptor": "aad.ZTU2NjBmZmEtNzhjMC03NDQ3LTk1Y2QtZjM4YTE0YjU2YjE0"
  }
  ```

  ## Option B — the ADO web UI

  Useful when you only need to bootstrap one or two repos and don't want to
  fiddle with PATs. Branch creation, policy creation, and verification are all
  done from the ADO web UI:

  ### B1. Create the `ralph-queue` branch off `main`

  1. Navigate to `https://dev.azure.com/<org>/<project>/_git/<repo>/branches`.
  2. Click **New branch**.
  3. **Name:** `ralph-queue`.
  4. **Based on:** `main`.
  5. Leave **Work items to link** empty.
  6. Click **Create**.

  ### B2. Add the branch policy

  1. From the same branches page, click the three-dot menu next to
     `ralph-queue` and choose **Branch policies**.
  2. Under **Require a minimum number of reviewers**, toggle **On** and set:
     - Minimum number of reviewers: **1**
     - **Do not** check "Allow requestors to approve their own changes".
     - **Do not** check "Allow completion even if some reviewers vote 'Waiting'".
  3. Under **Automatically include code reviewers**, click **+** to add a new
     entry:
     - **Reviewers:** the Ralph service principal (e.g.
       `ralph-service-principal@example.com`).
     - **Branch:** scope to **This branch** (`refs/heads/ralph-queue`).
     - **Required reviewer:** yes.
  4. Click **Save**.

  ### B3. Restrict push access (optional but recommended)

  1. From the repo page, choose **Settings** (gear icon) → **Repositories**
     → select the repo → **Security**.
  2. In the identity list, find or add an entry for **everyone except** the
     Ralph service principal (typically `Project Valid Users` or the
     contributors group):
     - Set **Contribute** → **Deny** scoped to `refs/heads/ralph-queue`.
  3. For the Ralph service principal entry (add it if not present):
     - Set **Contribute** → **Allow** scoped to `refs/heads/ralph-queue`.
     - Set **Force push (rewrite history, delete branches)** → **Deny**.
  4. Click **Save changes**.

  ## Option C — the `az` CLI

  For engineers who prefer the command line but don't want to invoke the
  Python helper. Requires `az` ≥ 2.50 with the `azure-devops` extension
  installed (`az extension add --name azure-devops`).

  ### C1. Authenticate

  ```bash
  export AZURE_DEVOPS_EXT_PAT="$ADO_PAT"
  az devops configure --defaults \
      organization="$ADO_ORG_URL" \
      project="$ADO_PROJECT"
  ```

  ### C2. Resolve the main-branch tip and create `ralph-queue`

  ```bash
  REPO="service-auth"
  MAIN_SHA=$(az repos ref list --repository "$REPO" \
      --filter "heads/main" --query "[0].objectId" -o tsv)

  az repos ref create --repository "$REPO" \
      --name "refs/heads/ralph-queue" \
      --object-id "$MAIN_SHA"
  ```

  Expected: a JSON object with `"success": true` and `"name":
  "refs/heads/ralph-queue"`.

  ### C3. Apply the branch policy

  The Require-Reviewer policy can be created with
  `az repos policy required-reviewer create`:

  ```bash
  REPO_ID=$(az repos show --repository "$REPO" --query id -o tsv)
  PRINCIPAL_ID=$(az devops user show \
      --user ralph-service-principal@example.com --query id -o tsv)

  az repos policy required-reviewer create \
      --blocking true \
      --enabled true \
      --branch ralph-queue \
      --repository-id "$REPO_ID" \
      --message-default "Auto-required reviewer for the ralph-queue branch" \
      --required-reviewer-ids "$PRINCIPAL_ID"
  ```

  ## Verification (all three options)

  Run these against the repo after the setup completes; all three should pass
  for any successful bootstrap.

  ### V1. `ralph-queue` exists and points at the right tip

  ```bash
  az repos ref list --repository service-auth \
      --filter "heads/ralph-queue" \
      --query "[].{name:name,objectId:objectId}"
  ```

  Expected: a single row whose `objectId` matches `main`'s tip at the time of
  the bootstrap. (After Ralph has been running, the tips will diverge — at
  the moment of the bootstrap they are identical.)

  ### V2. Branch policy is in place

  ```bash
  az repos policy list \
      --branch ralph-queue \
      --repository-id "$REPO_ID" \
      --query "[?isEnabled].{id:id,type:type.displayName,blocking:isBlocking}"
  ```

  Expected: at least one row whose `type` is `Required reviewers` and whose
  `blocking` is `true`.

  ### V3. The helper script's idempotency check passes

  ```bash
  uv run python scripts/setup_ralph_queue.py \
      --repo service-auth \
      --service-principal ralph-service-principal@example.com
  ```

  Expected JSON contains `"branch_created": false` and
  `"branch_existed": true`, and `"policies_created"` is empty.

  ## Troubleshooting

  | Symptom | Likely cause | Fix |
  |---|---|---|
  | `TF401019: The Git repository … does not exist.` | `--repo` name does not match an ADO repo in the configured project. | Confirm with `az repos list --query "[].name"`. |
  | `VS800075: The project … does not exist.` | `ADO_PROJECT` is the **organisation** name, not the project name. | Set `ADO_PROJECT` to the project; keep org in `ADO_ORG_URL`. |
  | `TF401028: The reference 'refs/heads/ralph-queue' has already been updated by another client.` | Concurrent setup runs. | Re-run; the script's idempotency check will treat the existing branch as already-present and continue. |
  | `identity not found for principal` | `--service-principal` value isn't recognised by ADO. | Use the UPN (email) of an identity that has logged into ADO at least once; for groups, use the group display name. |
  | Helper script exits with `error: environment variable ADO_PAT is required`. | The shell from which you launched the script didn't have `ADO_PAT` set. | Export the three env vars in the same shell before running. |
  ```

- [ ] 3. Confirm the markdown renders sanely (basic visual sanity). Run:
  ```
  uv run python -c "from pathlib import Path; t = Path('docs/runbooks/ralph-queue-setup.md').read_text(encoding='utf-8'); assert '## What' in t and '## Verification' in t; print('ok', len(t), 'chars')"
  ```
  Expected: `ok N chars` for some N around 7000.

- [ ] 4. Commit:
  ```
  git add docs/runbooks/ralph-queue-setup.md
  git commit -m "docs(ralph-queue): add setup runbook for ADO branch + policies"
  ```
  Expected: commit succeeds.

---

## Task 7 — Full toolchain pass

**Files**
- (none — toolchain-only step)

**Steps**

- [ ] 1. Run the whole local gate to confirm Plan 2's deliverables are green and don't disturb Plan 1:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts tests
  uv run pytest
  ```
  Expected: each command exits 0. Ruff: `All checks passed!`. Format: nothing to reformat. Mypy: `Success: no issues found in N source files`. Pytest: all tests pass — the Plan 1 workspace tests and the Plan 2 tests (`tests/test_ado_client.py` with 5 tests, `tests/test_setup_ralph_queue.py` with 5 tests).

- [ ] 2. Confirm the commit history is clean and conventional:
  ```
  git log --oneline -10
  ```
  Expected: four Plan-2 commits at the top of the log, each with a conventional-commit prefix:
  - `chore(deps): add requests, responses, types-requests for ADO REST helper`
  - `feat(ado): add minimal PAT-authenticated ADO REST 7.1 client`
  - `feat(setup): add ralph-queue branch + policy helper script`
  - `docs(ralph-queue): add setup runbook for ADO branch + policies`

---

## Verification

This is the orchestrator's Plan 2 verification gate. The gate as written in the orchestrator is:

> `git fetch && git branch -r | grep ralph-queue` (on test repo) — `origin/ralph-queue` exists

Plan 2 delivers the tooling that makes that gate satisfiable on demand. The verification therefore has two parts: prove that the artifacts work in isolation (no network required) and rehearse the end-to-end check on a test repo.

### Part 1 — artifact gate (no network)

- [ ] 1. Run all Plan 2 tests in isolation:
  ```
  uv run pytest tests/test_ado_client.py tests/test_setup_ralph_queue.py -v
  ```
  Expected: 10 tests collected, 10 passing.

- [ ] 2. Confirm the helper script's `--help` is meaningful:
  ```
  uv run python scripts/setup_ralph_queue.py --help
  ```
  Expected: help text mentions `--repo`, `--service-principal`, `--branch`, `--base-branch`, `--dry-run`, and the three env vars (`ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT`).

- [ ] 3. Confirm the runbook is present and contains the eight expected sections:
  ```
  uv run python -c "from pathlib import Path; t = Path('docs/runbooks/ralph-queue-setup.md').read_text(encoding='utf-8'); [print(h, h in t) for h in ('## What', '## Prerequisites', '## Option A', '## Option B', '## Option C', '## Verification', '## Troubleshooting')]"
  ```
  Expected: each section header prints `True`.

### Part 2 — orchestrator gate on a test repo (manual; once per environment)

The orchestrator gate requires `origin/ralph-queue` to exist on the chosen test repo. This is a one-time manual rehearsal — the helper script + the runbook are the artifacts; the rehearsal demonstrates they work.

- [ ] 4. Pick a throwaway service repo in your ADO project (or create one — `ralph-test-bed` is a reasonable name). Export the env vars:
  ```
  export ADO_PAT="<your PAT>"
  export ADO_ORG_URL="https://dev.azure.com/<your-org>"
  export ADO_PROJECT="<your-project>"
  ```

- [ ] 5. Run the helper:
  ```
  uv run python scripts/setup_ralph_queue.py \
      --repo ralph-test-bed \
      --service-principal ralph-service-principal@example.com
  ```
  Expected: exit code 0; JSON summary with `"branch_created": true` and a non-empty `policies_created` list.

- [ ] 6. Verify the orchestrator's exact gate command. Clone the test repo locally and run:
  ```
  git fetch
  git branch -r | grep ralph-queue
  ```
  Expected: `origin/ralph-queue` is listed. If it is not, STOP and inspect the JSON output from step 5; do not declare Plan 2 complete.

- [ ] 7. Re-run the helper to confirm idempotency:
  ```
  uv run python scripts/setup_ralph_queue.py \
      --repo ralph-test-bed \
      --service-principal ralph-service-principal@example.com
  ```
  Expected: exit code 0; JSON summary with `"branch_created": false`, `"branch_existed": true`, and `"policies_created": []`.

If steps 1–7 all pass, Plan 2 is complete and downstream plans (Plan 4 `ralph-status`, Plan 7 executor) can rely on `ralph-queue` existing on the test repo and on the `AdoClient` plus `setup_ralph_queue.py` helper being available in-repo for re-use (Plans 3 and 5 will both depend on `scripts.ado_client`).
