# `ralph-queue` Branch + Host Branch Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ralph-queue` available on a service repo with the right protections in place across BOTH supported git hosts. Deliver:

- **Phase 1 — GitHub (built now, by humans):**
  - `scripts/gh_client.py` — tiny GitHub REST wrapper (PAT auth via `GH_TOKEN`).
  - `scripts/setup_ralph_queue_github.py` — creates `ralph-queue` off `main` and applies branch protection.
  - Phase 1 portion of `docs/runbooks/ralph-queue-setup.md`.
  - Tests for both (`tests/test_gh_client.py`, `tests/test_setup_ralph_queue_github.py`).
- **Phase 2 — ADO (deferred; likely Ralph-built):**
  - `scripts/ado_client.py` — tiny ADO REST 7.1 wrapper (PAT auth).
  - `scripts/setup_ralph_queue_ado.py` — creates `ralph-queue` off `main` and applies the ADO branch policy.
  - Phase 2 portion of `docs/runbooks/ralph-queue-setup.md`.
  - Tests for both (`tests/test_ado_client.py`, `tests/test_setup_ralph_queue_ado.py`).

**Architecture:** Each host gets a dedicated, host-pure helper script. Both scripts are thin imperative Python — they make REST calls only, never run `git` locally, and print a JSON summary on stdout (source of truth) with human-readable progress on stderr. The GitHub variant performs three discrete REST calls (read `main` tip ref → create `refs/heads/ralph-queue` → PUT branch protection). The ADO variant performs four (look up repo by name → read `main` tip → create the queue ref → create the Require-Reviewer policy). Tests use `responses` to mock every endpoint either script touches; no network in CI.

**Tech Stack:** Python 3.12+, `uv`, `requests` (HTTP), `responses` (test-only HTTP mock), pytest, ruff, mypy strict, GitHub REST API (`api.github.com`), Azure DevOps REST API 7.1.

---

## Phases

This plan is split into two execution phases. Phase 1 is delivered now; Phase 2 is deferred and is itself a candidate for being built BY Ralph running on Phase 1's GitHub flow (see the orchestrator's "Host selection architecture — GitHub (Phase 1) and ADO (Phase 2)" section).

| Phase | Host | When | Artifacts |
|---|---|---|---|
| 1 | GitHub | Now (humans at home) | `scripts/gh_client.py`, `scripts/setup_ralph_queue_github.py`, `tests/test_gh_client.py`, `tests/test_setup_ralph_queue_github.py`, Phase 1 section of `docs/runbooks/ralph-queue-setup.md` |
| 2 | Azure DevOps | Deferred (likely Ralph-built) | `scripts/ado_client.py`, `scripts/setup_ralph_queue_ado.py`, `tests/test_ado_client.py`, `tests/test_setup_ralph_queue_ado.py`, Phase 2 section of `docs/runbooks/ralph-queue-setup.md` |

**Cross-phase env vars (per the orchestrator):**

| Var | Phase | Purpose |
|---|---|---|
| `RALPH_GIT_HOST` | both | `github` or `ado`; the executor reads this to decide which host's skills to stage. Plan 2's helper scripts do NOT read it — each script targets exactly one host by design. |
| `GH_TOKEN` | 1 | GitHub PAT with `repo` scope (full control of private repositories) — needed for branch creation AND for branch protection administration. |
| `GH_OWNER` | 1 | GitHub org or user that owns the repo (e.g. `emp3thy`). |
| `ADO_PAT` | 2 | Azure DevOps PAT. |
| `ADO_ORG_URL` | 2 | e.g. `https://dev.azure.com/example-org`. |
| `ADO_PROJECT` | 2 | ADO project name. |

Both phases share `pyproject.toml` dependencies — Task 1 (add `requests` + `responses` + `types-requests`) is paid once up front and benefits both.

---

## File Structure

| Path | Phase | Responsibility |
|---|---|---|
| `pyproject.toml` | both | Add `requests` to runtime dependencies and `responses` + `types-requests` to the dev group. No other change. |
| `scripts/gh_client.py` | 1 | Minimal GitHub REST wrapper around `requests.Session`. PAT auth via `Authorization: Bearer $GH_TOKEN`. Default `Accept: application/vnd.github+json` and `X-GitHub-Api-Version: 2022-11-28`. Used by `setup_ralph_queue_github.py`; reusable by Plan 5 (`pr-github` skill) and Plan 3 (`workitem-fetch-github` skill). |
| `scripts/setup_ralph_queue_github.py` | 1 | The GitHub helper. Argparse CLI; reads `GH_TOKEN` and `GH_OWNER` from env. Creates the `ralph-queue` branch off `main`'s tip SHA and applies branch protection on `ralph-queue`. JSON summary on stdout, progress on stderr. |
| `tests/test_gh_client.py` | 1 | Unit tests for `gh_client.GhClient`: URL composition, PAT header, JSON body handling, status-code-to-exception mapping. |
| `tests/test_setup_ralph_queue_github.py` | 1 | Pytest tests: happy path, branch-already-exists, repo-not-found, missing-env-var, dry-run, JSON output shape, exit codes. All HTTP mocked with `responses`. |
| `scripts/ado_client.py` | 2 | Tiny ADO REST 7.1 wrapper around `requests.Session`. PAT auth (Basic with empty username). Reusable by `setup_ralph_queue_ado.py` and by Plan 5 (`pr-ado` skill). |
| `scripts/setup_ralph_queue_ado.py` | 2 | The ADO helper. Argparse CLI; reads `ADO_PAT` / `ADO_ORG_URL` / `ADO_PROJECT` from env. Creates the branch, applies policies, verifies, JSON summary. |
| `tests/test_ado_client.py` | 2 | Unit tests for `ado_client.AdoClient`. |
| `tests/test_setup_ralph_queue_ado.py` | 2 | Pytest tests for the ADO helper. All HTTP mocked with `responses`. |
| `docs/runbooks/ralph-queue-setup.md` | both | Human-facing runbook with explicit "Phase 1 — GitHub" and "Phase 2 — ADO" sections, prerequisites, helper-script usage, UI/CLI fallbacks, verification, and troubleshooting. |

---

## Task 1 — Add HTTP dependencies (shared by both phases)

**Files**
- Modify: `pyproject.toml`

**Steps**

- [ ] 1. Confirm Plan 1 was completed (you should be standing on a tree that has the workspace bootstrap already merged):
  ```
  uv run pytest tests/ -k workspace
  ```
  Expected: all Plan 1 tests pass. If they do not, STOP — Plan 2 depends on Plan 1's bootstrap.

- [ ] 2. Add `requests` to the runtime dependency set:
  ```
  uv add requests
  ```
  Expected: `pyproject.toml` is updated with a `requests>=2.32` line in `[project].dependencies`; `uv.lock` is written; no errors.

- [ ] 3. Add `responses` and `types-requests` to the dev dependency group:
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
  git commit -m "chore(deps): add requests, responses, types-requests for host REST helpers"
  ```
  Expected: commit succeeds.

---

# Phase 1 — GitHub (built now)

The following four tasks (2 through 5) deliver Phase 1. They produce a working, tested GitHub helper plus the GitHub portion of the runbook. After Task 5 the Phase 1 verification gate (see "Verification" at the bottom of this plan) is satisfiable.

---

## Task 2 — Write the failing `gh_client` test (Phase 1)

**Files**
- Create: `tests/test_gh_client.py`

**Steps**

- [ ] 1. Write `tests/test_gh_client.py` with the exact content below. The test imports `scripts.gh_client`, which does not yet exist, so the failure mode is `ModuleNotFoundError`:
  ```python
  """Unit tests for ``scripts.gh_client.GhClient``.

  The client wraps ``requests.Session`` with PAT auth (Bearer token) and
  GitHub REST conventions (api.github.com base, ``Accept:
  application/vnd.github+json``, ``X-GitHub-Api-Version: 2022-11-28``).
  These tests use the ``responses`` library to mock every HTTP exchange.
  """
  from __future__ import annotations

  import pytest
  import responses

  from scripts.gh_client import GhClient, GhError


  BASE = "https://api.github.com"
  OWNER = "example-org"
  REPO = "service-auth"
  TOKEN = "ghp_fake_token_value"


  @responses.activate
  def test_get_composes_url_and_sets_bearer_header() -> None:
      responses.add(
          responses.GET,
          f"{BASE}/repos/{OWNER}/{REPO}",
          json={"id": 12345, "name": REPO, "default_branch": "main"},
          status=200,
      )

      client = GhClient(token=TOKEN)
      result = client.get(f"/repos/{OWNER}/{REPO}")

      assert result == {"id": 12345, "name": REPO, "default_branch": "main"}
      call = responses.calls[0]
      assert call.request.headers["Authorization"] == f"Bearer {TOKEN}"
      assert call.request.headers["Accept"] == "application/vnd.github+json"
      assert call.request.headers["X-GitHub-Api-Version"] == "2022-11-28"


  @responses.activate
  def test_post_sends_json_body() -> None:
      responses.add(
          responses.POST,
          f"{BASE}/repos/{OWNER}/{REPO}/git/refs",
          json={"ref": "refs/heads/ralph-queue", "object": {"sha": "a" * 40}},
          status=201,
      )

      client = GhClient(token=TOKEN)
      result = client.post(
          f"/repos/{OWNER}/{REPO}/git/refs",
          json_body={"ref": "refs/heads/ralph-queue", "sha": "a" * 40},
      )

      assert result["ref"] == "refs/heads/ralph-queue"
      call = responses.calls[0]
      assert call.request.headers["Content-Type"] == "application/json"


  @responses.activate
  def test_put_sends_json_body() -> None:
      responses.add(
          responses.PUT,
          f"{BASE}/repos/{OWNER}/{REPO}/branches/ralph-queue/protection",
          json={"url": "https://api.github.com/.../protection"},
          status=200,
      )

      client = GhClient(token=TOKEN)
      result = client.put(
          f"/repos/{OWNER}/{REPO}/branches/ralph-queue/protection",
          json_body={
              "required_status_checks": None,
              "enforce_admins": True,
              "required_pull_request_reviews": None,
              "restrictions": None,
          },
      )

      assert "url" in result
      call = responses.calls[0]
      assert call.request.method == "PUT"
      assert call.request.headers["Content-Type"] == "application/json"


  @responses.activate
  def test_patch_sends_json_body() -> None:
      responses.add(
          responses.PATCH,
          f"{BASE}/repos/{OWNER}/{REPO}",
          json={"name": REPO, "description": "updated"},
          status=200,
      )

      client = GhClient(token=TOKEN)
      result = client.patch(
          f"/repos/{OWNER}/{REPO}",
          json_body={"description": "updated"},
      )

      assert result["description"] == "updated"


  @responses.activate
  def test_delete_returns_none_on_204() -> None:
      responses.add(
          responses.DELETE,
          f"{BASE}/repos/{OWNER}/{REPO}/branches/ralph-queue/protection",
          status=204,
      )

      client = GhClient(token=TOKEN)
      result = client.delete(
          f"/repos/{OWNER}/{REPO}/branches/ralph-queue/protection"
      )

      assert result is None


  @responses.activate
  def test_non_2xx_raises_gh_error_with_status_and_body() -> None:
      responses.add(
          responses.GET,
          f"{BASE}/repos/{OWNER}/nope",
          json={"message": "Not Found", "documentation_url": "https://docs.github.com/..."},
          status=404,
      )

      client = GhClient(token=TOKEN)
      with pytest.raises(GhError) as excinfo:
          client.get(f"/repos/{OWNER}/nope")

      assert excinfo.value.status_code == 404
      assert "Not Found" in str(excinfo.value)


  def test_rejects_empty_token() -> None:
      with pytest.raises(ValueError, match="token must be a non-empty string"):
          GhClient(token="")


  def test_strips_trailing_slash_from_base_url() -> None:
      client = GhClient(token=TOKEN, base_url=BASE + "/")
      assert client.base_url == BASE


  @responses.activate
  def test_path_without_leading_slash_is_handled() -> None:
      responses.add(
          responses.GET,
          f"{BASE}/repos/{OWNER}/{REPO}",
          json={"id": 1},
          status=200,
      )

      client = GhClient(token=TOKEN)
      result = client.get(f"repos/{OWNER}/{REPO}")
      assert result == {"id": 1}
  ```

- [ ] 2. Run the test to confirm the expected red:
  ```
  uv run pytest tests/test_gh_client.py -v
  ```
  Expected: collection error or test failure with `ModuleNotFoundError: No module named 'scripts.gh_client'`.

- [ ] 3. Do NOT commit yet — the failing test is consumed in Task 3.

---

## Task 3 — Implement `gh_client` (Phase 1)

**Files**
- Create: `scripts/gh_client.py`

**Steps**

- [ ] 1. Write `scripts/gh_client.py` with the exact content below:
  ```python
  """Tiny GitHub REST client used by the Ralph setup and skill scripts.

  Wraps ``requests.Session`` with:
    - Bearer-token authentication using a Personal Access Token (PAT)
      taken from the ``token`` constructor argument (typically
      sourced from the ``GH_TOKEN`` env var by the caller).
    - GitHub's recommended request headers
      (``Accept: application/vnd.github+json`` and
      ``X-GitHub-Api-Version: 2022-11-28``).
    - JSON body encoding for POST/PUT/PATCH.
    - A ``GhError`` raised on non-2xx responses.

  Reference: https://docs.github.com/en/rest/overview/authenticating-to-the-rest-api
  """
  from __future__ import annotations

  from typing import Any

  import requests


  DEFAULT_BASE_URL = "https://api.github.com"
  DEFAULT_API_VERSION = "2022-11-28"
  DEFAULT_TIMEOUT_SECONDS = 30.0


  class GhError(RuntimeError):
      """Raised when the GitHub REST API returns a non-2xx status."""

      def __init__(self, status_code: int, body: str, url: str) -> None:
          super().__init__(f"GitHub {status_code} from {url}: {body}")
          self.status_code = status_code
          self.body = body
          self.url = url


  class GhClient:
      """Minimal GitHub REST client. One instance per token."""

      def __init__(
          self,
          token: str,
          *,
          base_url: str = DEFAULT_BASE_URL,
          api_version: str = DEFAULT_API_VERSION,
          timeout: float = DEFAULT_TIMEOUT_SECONDS,
          session: requests.Session | None = None,
      ) -> None:
          if not isinstance(token, str) or not token.strip():
              raise ValueError("token must be a non-empty string")
          if not isinstance(base_url, str) or not base_url.strip():
              raise ValueError("base_url must be a non-empty string")

          self.base_url = base_url.rstrip("/")
          self.api_version = api_version
          self.timeout = timeout
          self._session = session or requests.Session()

          self._session.headers.update(
              {
                  "Authorization": f"Bearer {token}",
                  "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": api_version,
                  "User-Agent": "ralph-setup-script",
              }
          )

      def _build_url(self, path: str) -> str:
          path = path.lstrip("/")
          return f"{self.base_url}/{path}"

      def _request(
          self,
          method: str,
          path: str,
          *,
          json_body: Any = None,
          params: dict[str, str] | None = None,
          extra_headers: dict[str, str] | None = None,
      ) -> Any:
          url = self._build_url(path)

          headers: dict[str, str] = {}
          if json_body is not None:
              headers["Content-Type"] = "application/json"
          if extra_headers:
              headers.update(extra_headers)

          response = self._session.request(
              method,
              url,
              params=params,
              json=json_body,
              headers=headers or None,
              timeout=self.timeout,
          )
          if not (200 <= response.status_code < 300):
              raise GhError(response.status_code, response.text, response.url)
          if response.status_code == 204 or not response.content:
              return None
          return response.json()

      def get(
          self,
          path: str,
          *,
          params: dict[str, str] | None = None,
      ) -> Any:
          return self._request("GET", path, params=params)

      def post(
          self,
          path: str,
          *,
          json_body: Any,
          params: dict[str, str] | None = None,
      ) -> Any:
          return self._request("POST", path, json_body=json_body, params=params)

      def put(
          self,
          path: str,
          *,
          json_body: Any,
          params: dict[str, str] | None = None,
      ) -> Any:
          return self._request("PUT", path, json_body=json_body, params=params)

      def patch(
          self,
          path: str,
          *,
          json_body: Any,
          params: dict[str, str] | None = None,
      ) -> Any:
          return self._request("PATCH", path, json_body=json_body, params=params)

      def delete(
          self,
          path: str,
          *,
          params: dict[str, str] | None = None,
      ) -> Any:
          return self._request("DELETE", path, params=params)
  ```

- [ ] 2. Run the `gh_client` tests; they must now pass:
  ```
  uv run pytest tests/test_gh_client.py -v
  ```
  Expected: all nine tests in `tests/test_gh_client.py` pass. Exit code 0.

- [ ] 3. Run ruff and mypy against the new files:
  ```
  uv run ruff check scripts/gh_client.py tests/test_gh_client.py
  uv run mypy scripts/gh_client.py tests/test_gh_client.py
  ```
  Expected: ruff: `All checks passed!`. Mypy: `Success: no issues found in 2 source files`.

- [ ] 4. Commit:
  ```
  git add scripts/gh_client.py tests/test_gh_client.py
  git commit -m "feat(gh_client): add minimal Bearer-token GitHub REST client"
  ```
  Expected: commit succeeds.

---

## Task 4 — Write the failing `setup_ralph_queue_github` test (Phase 1)

**Files**
- Create: `tests/test_setup_ralph_queue_github.py`

**Steps**

- [ ] 1. Write `tests/test_setup_ralph_queue_github.py` with the exact content below. It imports `scripts.setup_ralph_queue_github`, which does not yet exist:
  ```python
  """Tests for ``scripts.setup_ralph_queue_github``.

  Every GitHub REST endpoint the script touches is mocked with ``responses``.
  No network calls happen during the test run.
  """
  from __future__ import annotations

  import json
  from typing import Any

  import pytest
  import responses

  from scripts import setup_ralph_queue_github


  BASE = "https://api.github.com"
  OWNER = "example-org"
  REPO = "service-auth"
  TOKEN = "ghp_fake_token_value"
  MAIN_SHA = "a" * 40


  REPO_URL = f"{BASE}/repos/{OWNER}/{REPO}"
  MAIN_REF_URL = f"{REPO_URL}/git/ref/heads/main"
  QUEUE_REF_URL = f"{REPO_URL}/git/ref/heads/ralph-queue"
  REFS_CREATE_URL = f"{REPO_URL}/git/refs"
  PROTECTION_URL = f"{REPO_URL}/branches/ralph-queue/protection"


  @pytest.fixture
  def env(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("GH_TOKEN", TOKEN)
      monkeypatch.setenv("GH_OWNER", OWNER)


  def _register_repo_lookup() -> None:
      responses.add(
          responses.GET,
          REPO_URL,
          json={
              "id": 12345,
              "name": REPO,
              "full_name": f"{OWNER}/{REPO}",
              "default_branch": "main",
          },
          status=200,
      )


  def _register_main_tip(sha: str = MAIN_SHA) -> None:
      responses.add(
          responses.GET,
          MAIN_REF_URL,
          json={
              "ref": "refs/heads/main",
              "object": {"sha": sha, "type": "commit"},
          },
          status=200,
      )


  def _register_queue_branch_absent() -> None:
      responses.add(
          responses.GET,
          QUEUE_REF_URL,
          json={"message": "Not Found"},
          status=404,
      )


  def _register_queue_branch_present(sha: str = MAIN_SHA) -> None:
      responses.add(
          responses.GET,
          QUEUE_REF_URL,
          json={
              "ref": "refs/heads/ralph-queue",
              "object": {"sha": sha, "type": "commit"},
          },
          status=200,
      )


  def _register_queue_branch_create(sha: str = MAIN_SHA) -> None:
      responses.add(
          responses.POST,
          REFS_CREATE_URL,
          json={
              "ref": "refs/heads/ralph-queue",
              "object": {"sha": sha, "type": "commit"},
          },
          status=201,
      )


  def _register_protection_put() -> None:
      responses.add(
          responses.PUT,
          PROTECTION_URL,
          json={"url": PROTECTION_URL, "enabled": True},
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
      _register_protection_put()

      exit_code = setup_ralph_queue_github.main(["--repo", REPO])
      assert exit_code == 0

      captured = capsys.readouterr()
      payload: dict[str, Any] = json.loads(captured.out)
      assert payload["repo"] == REPO
      assert payload["owner"] == OWNER
      assert payload["branch_name"] == "ralph-queue"
      assert payload["branch_base_sha"] == MAIN_SHA
      assert payload["branch_created"] is True
      assert payload["branch_existed"] is False
      assert payload["protection_applied"] is True
      assert payload["dry_run"] is False


  @responses.activate
  def test_idempotent_when_branch_already_exists(
      env: None, capsys: pytest.CaptureFixture[str]
  ) -> None:
      _register_repo_lookup()
      _register_main_tip()
      _register_queue_branch_present()
      _register_protection_put()

      exit_code = setup_ralph_queue_github.main(["--repo", REPO])
      assert exit_code == 0

      payload = json.loads(capsys.readouterr().out)
      assert payload["branch_created"] is False
      assert payload["branch_existed"] is True
      assert payload["protection_applied"] is True


  @responses.activate
  def test_repo_not_found_exits_nonzero(
      env: None, capsys: pytest.CaptureFixture[str]
  ) -> None:
      responses.add(
          responses.GET,
          REPO_URL,
          json={"message": "Not Found"},
          status=404,
      )

      exit_code = setup_ralph_queue_github.main(["--repo", REPO])
      assert exit_code == 2
      stderr = capsys.readouterr().err
      assert "Not Found" in stderr


  def test_missing_gh_token_exits_nonzero(
      monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
  ) -> None:
      monkeypatch.delenv("GH_TOKEN", raising=False)
      monkeypatch.setenv("GH_OWNER", OWNER)

      exit_code = setup_ralph_queue_github.main(["--repo", REPO])
      assert exit_code == 2
      assert "GH_TOKEN" in capsys.readouterr().err


  def test_missing_gh_owner_exits_nonzero(
      monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
  ) -> None:
      monkeypatch.setenv("GH_TOKEN", TOKEN)
      monkeypatch.delenv("GH_OWNER", raising=False)

      exit_code = setup_ralph_queue_github.main(["--repo", REPO])
      assert exit_code == 2
      assert "GH_OWNER" in capsys.readouterr().err


  @responses.activate
  def test_dry_run_makes_no_mutations(
      env: None, capsys: pytest.CaptureFixture[str]
  ) -> None:
      _register_repo_lookup()
      _register_main_tip()
      _register_queue_branch_absent()

      exit_code = setup_ralph_queue_github.main(["--repo", REPO, "--dry-run"])
      assert exit_code == 0

      payload = json.loads(capsys.readouterr().out)
      assert payload["dry_run"] is True
      assert payload["branch_created"] is False
      assert payload["protection_applied"] is False
      assert all(call.request.method == "GET" for call in responses.calls)


  @responses.activate
  def test_protection_payload_shape(
      env: None, capsys: pytest.CaptureFixture[str]
  ) -> None:
      """The PUT to /branches/ralph-queue/protection sends the documented shape."""
      _register_repo_lookup()
      _register_main_tip()
      _register_queue_branch_absent()
      _register_queue_branch_create()
      _register_protection_put()

      exit_code = setup_ralph_queue_github.main(["--repo", REPO])
      assert exit_code == 0

      # Find the PUT call.
      put_calls = [c for c in responses.calls if c.request.method == "PUT"]
      assert len(put_calls) == 1
      body = json.loads(put_calls[0].request.body)
      assert body["enforce_admins"] is True
      assert body["allow_force_pushes"] is False
      assert body["allow_deletions"] is False
      # Required-status-checks and restrictions may be null OR an object;
      # both are documented as accepted by the GitHub API. The script
      # MUST include each top-level key (even if value is null) per the docs.
      assert "required_status_checks" in body
      assert "required_pull_request_reviews" in body
      assert "restrictions" in body
  ```

- [ ] 2. Run the test to confirm the expected red:
  ```
  uv run pytest tests/test_setup_ralph_queue_github.py -v
  ```
  Expected: collection failure with `ModuleNotFoundError: No module named 'scripts.setup_ralph_queue_github'`.

- [ ] 3. Do NOT commit yet — the failing test is consumed in Task 5.

---

## Task 5 — Implement `setup_ralph_queue_github.py` (Phase 1)

**Files**
- Create: `scripts/setup_ralph_queue_github.py`

**Steps**

- [ ] 1. Write `scripts/setup_ralph_queue_github.py` with the exact content below. The implementation uses real, documented GitHub REST endpoints:

  - `GET /repos/{owner}/{repo}` — confirms the repo exists and we have access.
    Docs: <https://docs.github.com/en/rest/repos/repos#get-a-repository>
  - `GET /repos/{owner}/{repo}/git/ref/heads/{branch}` — reads a branch tip
    or returns 404 if the branch is absent.
    Docs: <https://docs.github.com/en/rest/git/refs#get-a-reference>
  - `POST /repos/{owner}/{repo}/git/refs` — creates a new ref.
    Docs: <https://docs.github.com/en/rest/git/refs#create-a-reference>
  - `PUT /repos/{owner}/{repo}/branches/{branch}/protection` — sets branch
    protection. Documented payload includes `required_status_checks`,
    `enforce_admins`, `required_pull_request_reviews`, `restrictions`,
    `allow_force_pushes`, `allow_deletions`, `required_conversation_resolution`,
    `lock_branch`, `allow_fork_syncing`.
    Docs: <https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection>

  ```python
  """Set up the ``ralph-queue`` branch and its protection on a GitHub repo.

  Reads ``GH_TOKEN`` and ``GH_OWNER`` from the environment.

  Operations (in order):

      1. GET /repos/{owner}/{repo}
         — confirm the repository exists and is accessible.
      2. GET /repos/{owner}/{repo}/git/ref/heads/main
         — read the tip SHA of main.
      3. GET /repos/{owner}/{repo}/git/ref/heads/ralph-queue
         — detect whether ralph-queue already exists (404 means absent).
      4. POST /repos/{owner}/{repo}/git/refs
         — create refs/heads/ralph-queue off main's tip (skipped if it exists).
      5. PUT /repos/{owner}/{repo}/branches/ralph-queue/protection
         — apply branch protection (skipped in --dry-run).
      6. Print a JSON summary to stdout, exit 0.

  GitHub REST documentation:
      https://docs.github.com/en/rest/repos/repos#get-a-repository
      https://docs.github.com/en/rest/git/refs#get-a-reference
      https://docs.github.com/en/rest/git/refs#create-a-reference
      https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection
  """
  from __future__ import annotations

  import argparse
  import json
  import os
  import sys
  from dataclasses import asdict, dataclass
  from typing import Any

  from scripts.gh_client import GhClient, GhError


  QUEUE_BRANCH = "ralph-queue"
  MAIN_BRANCH = "main"


  @dataclass
  class SetupResult:
      owner: str
      repo: str
      branch_name: str
      branch_base_sha: str
      branch_created: bool
      branch_existed: bool
      protection_applied: bool
      dry_run: bool


  def _fail(message: str) -> int:
      print(f"error: {message}", file=sys.stderr)
      return 2


  def _require_env(name: str) -> str:
      value = os.environ.get(name, "").strip()
      if not value:
          raise _MissingEnv(name)
      return value


  class _MissingEnv(Exception):
      def __init__(self, name: str) -> None:
          super().__init__(name)
          self.name = name


  def _lookup_repo(client: GhClient, owner: str, repo: str) -> dict[str, Any]:
      return client.get(f"/repos/{owner}/{repo}")


  def _read_branch_tip(
      client: GhClient, owner: str, repo: str, branch: str
  ) -> str | None:
      """Return the tip SHA of ``refs/heads/{branch}`` or ``None`` if absent."""
      try:
          data = client.get(f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
      except GhError as exc:
          if exc.status_code == 404:
              return None
          raise
      if not isinstance(data, dict):
          return None
      obj = data.get("object") or {}
      sha = obj.get("sha")
      return str(sha) if sha else None


  def _create_branch(
      client: GhClient, owner: str, repo: str, branch: str, base_sha: str
  ) -> None:
      payload = {
          "ref": f"refs/heads/{branch}",
          "sha": base_sha,
      }
      client.post(f"/repos/{owner}/{repo}/git/refs", json_body=payload)


  def _apply_protection(
      client: GhClient, owner: str, repo: str, branch: str
  ) -> None:
      """PUT branch protection on ``refs/heads/{branch}``.

      The payload follows the documented shape at
      https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection.

      Intent:
        - ``enforce_admins=True``: admins can't bypass the rules.
        - ``allow_force_pushes=False`` and ``allow_deletions=False``: nothing
          rewrites or removes the queue branch.
        - ``required_status_checks=None``: no CI checks are required at
          bootstrap time. Per-repo overrides can layer them in later.
        - ``required_pull_request_reviews=None``: the queue branch is
          mutated directly by the executor; PR review is not part of the
          queue's lifecycle.
        - ``restrictions=None``: no push-actor restrictions at this layer.
          Push restrictions are enforced via a separate org/repo-level
          policy (documented in the runbook) because the
          ``restrictions`` object is only honoured on organisation-owned
          repos and requires team/app/user lists, which are out of scope
          for the bootstrap script. The runbook covers how to layer
          restrictions in for org repos.
      """
      payload = {
          "required_status_checks": None,
          "enforce_admins": True,
          "required_pull_request_reviews": None,
          "restrictions": None,
          "allow_force_pushes": False,
          "allow_deletions": False,
          "required_conversation_resolution": False,
          "lock_branch": False,
          "allow_fork_syncing": False,
      }
      client.put(
          f"/repos/{owner}/{repo}/branches/{branch}/protection",
          json_body=payload,
      )


  def _parse_args(argv: list[str]) -> argparse.Namespace:
      parser = argparse.ArgumentParser(
          description=(
              "Create the ralph-queue branch on a GitHub repo and apply "
              "branch protection. Reads GH_TOKEN and GH_OWNER from the "
              "environment."
          )
      )
      parser.add_argument(
          "--repo",
          required=True,
          help="Name of the GitHub repository (without the owner prefix).",
      )
      parser.add_argument(
          "--branch",
          default=QUEUE_BRANCH,
          help=f"Queue branch name (default: {QUEUE_BRANCH}).",
      )
      parser.add_argument(
          "--base-branch",
          default=MAIN_BRANCH,
          help=(
              f"Base branch the queue branch is created from "
              f"(default: {MAIN_BRANCH})."
          ),
      )
      parser.add_argument(
          "--dry-run",
          action="store_true",
          help="Read state but do not POST/PUT any mutations.",
      )
      return parser.parse_args(argv)


  def main(argv: list[str] | None = None) -> int:
      args = _parse_args(argv if argv is not None else sys.argv[1:])

      try:
          token = _require_env("GH_TOKEN")
          owner = _require_env("GH_OWNER")
      except _MissingEnv as exc:
          return _fail(f"environment variable {exc.name} is required")

      client = GhClient(token=token)

      try:
          print(f"resolving repo {owner}/{args.repo!r}...", file=sys.stderr)
          _lookup_repo(client, owner, args.repo)

          print(f"reading tip of {args.base_branch}...", file=sys.stderr)
          base_sha = _read_branch_tip(client, owner, args.repo, args.base_branch)
          if base_sha is None:
              return _fail(
                  f"base branch {args.base_branch!r} has no tip in "
                  f"{owner}/{args.repo}"
              )

          print(
              f"checking whether {args.branch} already exists...",
              file=sys.stderr,
          )
          existing_tip = _read_branch_tip(client, owner, args.repo, args.branch)
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
                  _create_branch(
                      client, owner, args.repo, args.branch, base_sha
                  )
                  branch_created = True

          protection_applied = False
          if args.dry_run:
              print(
                  f"DRY-RUN would PUT protection on refs/heads/{args.branch}",
                  file=sys.stderr,
              )
          else:
              print(
                  f"applying branch protection on {args.branch}...",
                  file=sys.stderr,
              )
              _apply_protection(client, owner, args.repo, args.branch)
              protection_applied = True

          result = SetupResult(
              owner=owner,
              repo=args.repo,
              branch_name=args.branch,
              branch_base_sha=base_sha,
              branch_created=branch_created,
              branch_existed=branch_existed,
              protection_applied=protection_applied,
              dry_run=args.dry_run,
          )
          print(json.dumps(asdict(result), indent=2, sort_keys=True))
          return 0

      except GhError as exc:
          return _fail(str(exc))


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] 2. Run the `setup_ralph_queue_github` tests; they must now all pass:
  ```
  uv run pytest tests/test_setup_ralph_queue_github.py -v
  ```
  Expected: all seven tests pass. Exit code 0.

- [ ] 3. Run ruff and mypy:
  ```
  uv run ruff check scripts/setup_ralph_queue_github.py tests/test_setup_ralph_queue_github.py
  uv run mypy scripts/setup_ralph_queue_github.py tests/test_setup_ralph_queue_github.py
  ```
  Expected: ruff: `All checks passed!`. Mypy: `Success: no issues found in 2 source files`.

- [ ] 4. Commit:
  ```
  git add scripts/setup_ralph_queue_github.py tests/test_setup_ralph_queue_github.py
  git commit -m "feat(setup_github): add GitHub ralph-queue branch + protection helper"
  ```
  Expected: commit succeeds.

---

## Task 6 — Write the Phase 1 portion of the runbook

**Files**
- Create: `docs/runbooks/ralph-queue-setup.md` (Phase 1 sections only; Phase 2 sections are added by Task 11)

**Steps**

- [ ] 1. Ensure the runbooks directory exists. PowerShell:
  ```
  New-Item -ItemType Directory -Force -Path docs/runbooks | Out-Null
  ```
  Or bash:
  ```
  mkdir -p docs/runbooks
  ```
  Expected: the directory exists; no error.

- [ ] 2. Write `docs/runbooks/ralph-queue-setup.md` with the exact content below. The file contains Phase 1 sections in full plus a "Phase 2 — ADO (deferred)" placeholder block that Task 11 will expand:

  ```markdown
  # `ralph-queue` setup runbook

  This runbook covers bootstrapping the `ralph-queue` branch and its
  protections on a service repository so that Ralph (the per-repo
  autonomous coding loop) can use it as the queue substrate.

  Ralph supports two git hosts. The branch model is identical across hosts;
  only the protection mechanism differs. Use the section that matches your
  pod's `RALPH_GIT_HOST` value.

  - **Phase 1 — GitHub** (`RALPH_GIT_HOST=github`): use
    `scripts/setup_ralph_queue_github.py`. Covered fully below.
  - **Phase 2 — Azure DevOps** (`RALPH_GIT_HOST=ado`): use
    `scripts/setup_ralph_queue_ado.py`. Phase 2 is deferred; the
    section below stubs the runbook and is expanded when Phase 2
    is implemented.

  ## What `ralph-queue` is for

  Ralph keeps its queue state — the `.ralph/` folder tree containing
  `inbox/`, `current/`, `pending-pr/`, `done/`, `blocked/`, and
  `archive/` — on a dedicated long-lived branch called `ralph-queue` in
  the same project repo as the code Ralph is working on. The branch is
  mutated by two parties only: the human supervisor skills (`ralph-add`,
  `ralph-status`, `ralph-cancel`, `ralph-promote`, `ralph-triage`) and
  the executor itself. PRs are never opened from `ralph-queue` to
  `main`; the branch exists purely to keep queue churn out of `main`'s
  history while still benefiting from git's durability and
  reproducibility.

  ---

  # Phase 1 — GitHub

  ## Prerequisites (GitHub)

  - **A GitHub repository.** Existing repo with a `main` branch and at
    least one commit.
  - **A Personal Access Token (PAT) with `repo` scope.** A fine-grained
    PAT works too — it must grant *Contents: Read and write* and
    *Administration: Read and write* on the target repo (administration
    is required to PUT branch protection).
  - **Environment variables for the helper script:**
    - `GH_TOKEN` — the PAT value above.
    - `GH_OWNER` — the GitHub org or user that owns the repo (e.g.
      `emp3thy`).

  ## Option A — the helper script (recommended)

  Run from a checkout of the ralph repo:

  ```bash
  export GH_TOKEN="..."
  export GH_OWNER="emp3thy"

  uv run python scripts/setup_ralph_queue_github.py --repo service-auth
  ```

  The script is idempotent: re-running it against a repo where
  `ralph-queue` already exists is a no-op for the branch (it reports
  `branch_created: false`, `branch_existed: true`) and a re-PUT of the
  protection payload (GitHub's PUT is idempotent for branch protection).

  Add `--dry-run` to see what would happen without making any changes:

  ```bash
  uv run python scripts/setup_ralph_queue_github.py \
      --repo service-auth \
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
    "owner": "emp3thy",
    "protection_applied": true,
    "repo": "service-auth"
  }
  ```

  ## Option B — the GitHub web UI

  Useful when you only need to bootstrap one or two repos and don't want
  to fiddle with PATs.

  ### B1. Create the `ralph-queue` branch off `main`

  1. Navigate to `https://github.com/<owner>/<repo>/branches`.
  2. Click **New branch**.
  3. **New branch name:** `ralph-queue`.
  4. **Source:** `main`.
  5. Click **Create branch**.

  ### B2. Add the branch protection rule

  1. Navigate to `https://github.com/<owner>/<repo>/settings/branches`.
  2. Click **Add branch protection rule**.
  3. **Branch name pattern:** `ralph-queue`.
  4. Configure:
     - **Require a pull request before merging:** off (the queue is
       mutated directly).
     - **Require status checks to pass before merging:** off (can be
       enabled later if you add CI for queue mutations).
     - **Require conversation resolution before merging:** off.
     - **Require signed commits:** at your discretion.
     - **Require linear history:** off.
     - **Require deployments to succeed before merging:** off.
     - **Lock branch:** off (Ralph and supervisor skills need to push).
     - **Do not allow bypassing the above settings:** ON
       (this is the `enforce_admins=True` toggle).
     - **Restrict who can push to matching branches:** ON if you have
       an org plan; add the Ralph service principal and the supervisor
       group as the only allowed actors.
     - **Allow force pushes:** OFF.
     - **Allow deletions:** OFF.
  5. Click **Create**.

  ### B3. Restrict push access (org repos, optional)

  For org-owned repos, the **Restrict who can push to matching
  branches** option in B2 is the canonical way to enforce push
  restrictions. The helper script leaves this option off because it
  requires team/app/user lists that vary per org; add them manually
  from the UI for any org-owned repo.

  ## Option C — the `gh` CLI

  For engineers who prefer the command line but don't want to invoke
  the Python helper. Requires `gh` ≥ 2.40 with `gh auth login`
  completed.

  ### C1. Authenticate

  ```bash
  gh auth login --hostname github.com
  ```

  Or, for headless / scripted use, export `GH_TOKEN` and `gh` will pick
  it up automatically.

  ### C2. Resolve the main-branch tip and create `ralph-queue`

  ```bash
  OWNER="emp3thy"
  REPO="service-auth"

  MAIN_SHA=$(gh api "repos/$OWNER/$REPO/git/ref/heads/main" \
      --jq .object.sha)

  gh api "repos/$OWNER/$REPO/git/refs" \
      --method POST \
      --field ref="refs/heads/ralph-queue" \
      --field sha="$MAIN_SHA"
  ```

  Expected: a JSON object with `"ref": "refs/heads/ralph-queue"` and an
  `object.sha` matching `$MAIN_SHA`.

  ### C3. Apply branch protection

  ```bash
  gh api "repos/$OWNER/$REPO/branches/ralph-queue/protection" \
      --method PUT \
      --raw-field required_status_checks=null \
      --raw-field required_pull_request_reviews=null \
      --raw-field restrictions=null \
      -F enforce_admins=true \
      -F allow_force_pushes=false \
      -F allow_deletions=false
  ```

  Expected: a JSON object whose `url` ends in
  `/branches/ralph-queue/protection`.

  ## Verification (GitHub, all three options)

  ### V1. `ralph-queue` exists and points at the right tip

  ```bash
  gh api "repos/$OWNER/$REPO/git/ref/heads/ralph-queue" \
      --jq '{ref: .ref, sha: .object.sha}'
  ```

  Expected: `ref: refs/heads/ralph-queue` and a `sha` matching `main`'s
  tip at bootstrap time.

  ### V2. Branch protection is in place

  ```bash
  gh api "repos/$OWNER/$REPO/branches/ralph-queue/protection" \
      --jq '{
        enforce_admins: .enforce_admins.enabled,
        allow_force_pushes: .allow_force_pushes.enabled,
        allow_deletions: .allow_deletions.enabled
      }'
  ```

  Expected:

  ```json
  {
    "enforce_admins": true,
    "allow_force_pushes": false,
    "allow_deletions": false
  }
  ```

  ### V3. The helper script's idempotency check passes

  ```bash
  uv run python scripts/setup_ralph_queue_github.py --repo service-auth
  ```

  Expected JSON contains `"branch_created": false` and
  `"branch_existed": true`, `"protection_applied": true`.

  ## Troubleshooting (GitHub)

  | Symptom | Likely cause | Fix |
  |---|---|---|
  | `GitHub 404 from .../repos/<owner>/<repo>: Not Found` | `--repo` name does not match a repo accessible to `GH_TOKEN`, OR `GH_OWNER` is wrong. | Confirm with `gh repo list <owner>`. |
  | `GitHub 401 from ...: Bad credentials` | `GH_TOKEN` is expired or missing the `repo` scope. | Regenerate the PAT with `repo` scope (or, for fine-grained, *Contents: R/W* + *Administration: R/W* on the target repo). |
  | `GitHub 403 from .../protection: ...Resource not accessible by integration` | Fine-grained PAT lacks *Administration: Read and write*. | Edit the PAT, grant administration write, re-run. |
  | `GitHub 422 from .../git/refs: Reference already exists` | A concurrent setup run created `ralph-queue` between this script's GET-ref and POST-ref. | Re-run; the second run's GET will see the ref and skip creation. |
  | Helper script exits with `error: environment variable GH_TOKEN is required`. | Shell didn't have `GH_TOKEN` exported. | Export `GH_TOKEN` and `GH_OWNER` in the same shell before running. |

  ---

  # Phase 2 — Azure DevOps (deferred)

  Phase 2 is the ADO equivalent of Phase 1 and is built later (likely
  by Ralph itself, running against Phase 1's GitHub setup). When Phase
  2 is delivered, the artifacts will be:

  - `scripts/ado_client.py` — minimal PAT-authenticated ADO REST 7.1 client.
  - `scripts/setup_ralph_queue_ado.py` — creates `ralph-queue` off `main`
    and creates a Require-Reviewer policy scoped to
    `refs/heads/ralph-queue`.

  The Phase 2 prerequisites, helper-script invocation, ADO web UI
  steps, and `az` CLI fallback are written into this same runbook
  file by Plan 2 Task 11 (deferred). Until then, do NOT bootstrap an
  ADO repo for Ralph — the Phase 2 tooling is the only supported
  path.
  ```

- [ ] 3. Confirm the markdown renders sanely:
  ```
  uv run python -c "from pathlib import Path; t = Path('docs/runbooks/ralph-queue-setup.md').read_text(encoding='utf-8'); assert '# Phase 1 — GitHub' in t and '# Phase 2 — Azure DevOps (deferred)' in t and '## Prerequisites (GitHub)' in t; print('ok', len(t), 'chars')"
  ```
  Expected: `ok N chars` for some N around 6500–7500.

- [ ] 4. Commit:
  ```
  git add docs/runbooks/ralph-queue-setup.md
  git commit -m "docs(ralph-queue): add Phase 1 (GitHub) setup runbook"
  ```
  Expected: commit succeeds.

---

## Task 7 — Phase 1 toolchain pass

**Files**
- (none — toolchain-only step)

**Steps**

- [ ] 1. Run the whole local gate to confirm Plan 2 Phase 1's deliverables are green and don't disturb Plan 1:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts tests
  uv run pytest
  ```
  Expected: each command exits 0. Ruff: `All checks passed!`. Format: nothing to reformat. Mypy: `Success: no issues found in N source files`. Pytest: all Plan 1 workspace tests pass, plus the Phase 1 tests (`tests/test_gh_client.py` with 9 tests, `tests/test_setup_ralph_queue_github.py` with 7 tests).

- [ ] 2. Confirm the commit history is clean and conventional:
  ```
  git log --oneline -10
  ```
  Expected: four Phase-1 commits at the top of the log, each with a conventional-commit prefix:
  - `chore(deps): add requests, responses, types-requests for host REST helpers`
  - `feat(gh_client): add minimal Bearer-token GitHub REST client`
  - `feat(setup_github): add GitHub ralph-queue branch + protection helper`
  - `docs(ralph-queue): add Phase 1 (GitHub) setup runbook`

---

# Phase 2 — ADO (Ralph-built, defer)

> **DEFER notice:** the following four tasks (8 through 11) are Phase 2. They are not executed in the same session as Phase 1. They are likely picked up later by Ralph itself, running on the Phase 1 GitHub flow, as one of the first work items Ralph closes. The tasks are written in the same TDD shape as Phase 1 so they can be dispatched cleanly when the time comes.

---

## Task 8 — Write the failing `ado_client` test (Phase 2 — ADO, Ralph-built, defer)

**Files**
- Create: `tests/test_ado_client.py`

**Steps**

- [ ] 1. Write `tests/test_ado_client.py` with the exact content below. The test imports `scripts.ado_client`, which does not yet exist:
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

- [ ] 3. Do NOT commit yet — the failing test is consumed in Task 9.

---

## Task 9 — Implement `ado_client` (Phase 2 — ADO, Ralph-built, defer)

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
  git commit -m "feat(ado_client): add minimal PAT-authenticated ADO REST 7.1 client"
  ```
  Expected: commit succeeds.

---

## Task 10 — Write the failing `setup_ralph_queue_ado` test (Phase 2 — ADO, Ralph-built, defer)

**Files**
- Create: `tests/test_setup_ralph_queue_ado.py`

**Steps**

- [ ] 1. Write `tests/test_setup_ralph_queue_ado.py` with the exact content below. It imports `scripts.setup_ralph_queue_ado`, which does not yet exist:
  ```python
  """Tests for ``scripts.setup_ralph_queue_ado``.

  Every ADO REST endpoint the script touches is mocked with ``responses``.
  No network calls happen during the test run.
  """
  from __future__ import annotations

  import json
  from typing import Any

  import pytest
  import responses

  from scripts import setup_ralph_queue_ado


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

      exit_code = setup_ralph_queue_ado.main(
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

      exit_code = setup_ralph_queue_ado.main(
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

      exit_code = setup_ralph_queue_ado.main(
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

      exit_code = setup_ralph_queue_ado.main(
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

      exit_code = setup_ralph_queue_ado.main(
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
  uv run pytest tests/test_setup_ralph_queue_ado.py -v
  ```
  Expected: collection failure with `ModuleNotFoundError: No module named 'scripts.setup_ralph_queue_ado'`.

- [ ] 3. Do NOT commit yet — the failing test is consumed in Task 11.

---

## Task 11 — Implement `setup_ralph_queue_ado.py` and finish the runbook (Phase 2 — ADO, Ralph-built, defer)

**Files**
- Create: `scripts/setup_ralph_queue_ado.py`
- Modify: `docs/runbooks/ralph-queue-setup.md` (replace the Phase 2 placeholder section with the full ADO runbook content)

**Steps**

- [ ] 1. Write `scripts/setup_ralph_queue_ado.py` with the exact content below:
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

- [ ] 2. Run the `setup_ralph_queue_ado` tests; they must now all pass:
  ```
  uv run pytest tests/test_setup_ralph_queue_ado.py -v
  ```
  Expected: all five tests pass. Exit code 0.

- [ ] 3. Run ruff and mypy:
  ```
  uv run ruff check scripts/setup_ralph_queue_ado.py tests/test_setup_ralph_queue_ado.py
  uv run mypy scripts/setup_ralph_queue_ado.py tests/test_setup_ralph_queue_ado.py
  ```
  Expected: ruff: `All checks passed!`. Mypy: `Success: no issues found in 2 source files`.

- [ ] 4. Replace the "Phase 2 — Azure DevOps (deferred)" placeholder block in `docs/runbooks/ralph-queue-setup.md` with the full ADO content below. Find this block:

  ```markdown
  # Phase 2 — Azure DevOps (deferred)

  Phase 2 is the ADO equivalent of Phase 1 and is built later (likely
  by Ralph itself, running against Phase 1's GitHub setup). When Phase
  2 is delivered, the artifacts will be:
  ...
  ADO repo for Ralph — the Phase 2 tooling is the only supported
  path.
  ```

  Replace the entire block with:

  ```markdown
  # Phase 2 — Azure DevOps

  ## Prerequisites (ADO)

  - **An ADO repository.** Existing repo with a `main` branch and at
    least one commit.
  - **A Personal Access Token (PAT).** Scopes required:
    - `Code (Read & Write)` — to create the branch ref.
    - `Code (Status)` — only required if your team uses status policies.
    - `Project & Team (Read)` — to resolve identity descriptors.
    - `Identity (Read)` — to look up the service-principal descriptor.
  - **A service principal (or group) for Ralph.** Substitute your own
    UPN / group name for the placeholder
    `ralph-service-principal@example.com` below.
  - **Environment variables for the helper script:**
    - `ADO_PAT` — the PAT value above.
    - `ADO_ORG_URL` — e.g. `https://dev.azure.com/example-org`.
    - `ADO_PROJECT` — the ADO project name (not the repository name).

  ## Option A — the helper script (recommended)

  ```bash
  export ADO_PAT="..."
  export ADO_ORG_URL="https://dev.azure.com/example-org"
  export ADO_PROJECT="example-project"

  uv run python scripts/setup_ralph_queue_ado.py \
      --repo service-auth \
      --service-principal ralph-service-principal@example.com
  ```

  The script is idempotent: re-running it against a repo where
  `ralph-queue` already exists and the policy is already in place is a
  no-op that exits 0 and prints a JSON summary describing the existing
  state.

  Add `--dry-run` to see what would happen without making any changes:

  ```bash
  uv run python scripts/setup_ralph_queue_ado.py \
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

  ### B1. Create the `ralph-queue` branch off `main`

  1. Navigate to `https://dev.azure.com/<org>/<project>/_git/<repo>/branches`.
  2. Click **New branch**.
  3. **Name:** `ralph-queue`. **Based on:** `main`. Click **Create**.

  ### B2. Add the branch policy

  1. From the same branches page, click the three-dot menu next to
     `ralph-queue` and choose **Branch policies**.
  2. Under **Require a minimum number of reviewers**, toggle **On** and set:
     - Minimum number of reviewers: **1**
     - **Do not** check "Allow requestors to approve their own changes".
     - **Do not** check "Allow completion even if some reviewers vote 'Waiting'".
  3. Under **Automatically include code reviewers**, click **+**:
     - **Reviewers:** the Ralph service principal.
     - **Branch:** scope to **This branch** (`refs/heads/ralph-queue`).
     - **Required reviewer:** yes.
  4. Click **Save**.

  ### B3. Restrict push access (optional but recommended)

  1. From the repo page: **Settings** → **Repositories** → select the
     repo → **Security**.
  2. For everyone except the Ralph service principal (typically
     `Project Valid Users`): set **Contribute** → **Deny** scoped to
     `refs/heads/ralph-queue`.
  3. For the Ralph service principal entry:
     - **Contribute** → **Allow** scoped to `refs/heads/ralph-queue`.
     - **Force push (rewrite history, delete branches)** → **Deny**.
  4. Click **Save changes**.

  ## Option C — the `az` CLI

  Requires `az` >= 2.50 with `az extension add --name azure-devops`.

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

  ### C3. Apply the branch policy

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

  ## Verification (ADO, all three options)

  ### V1. `ralph-queue` exists and points at the right tip

  ```bash
  az repos ref list --repository service-auth \
      --filter "heads/ralph-queue" \
      --query "[].{name:name,objectId:objectId}"
  ```

  Expected: a single row whose `objectId` matches `main`'s tip at
  bootstrap time.

  ### V2. Branch policy is in place

  ```bash
  az repos policy list \
      --branch ralph-queue \
      --repository-id "$REPO_ID" \
      --query "[?isEnabled].{id:id,type:type.displayName,blocking:isBlocking}"
  ```

  Expected: at least one row whose `type` is `Required reviewers` and
  whose `blocking` is `true`.

  ### V3. The helper script's idempotency check passes

  ```bash
  uv run python scripts/setup_ralph_queue_ado.py \
      --repo service-auth \
      --service-principal ralph-service-principal@example.com
  ```

  Expected JSON contains `"branch_created": false`,
  `"branch_existed": true`, and `"policies_created": []`.

  ## Troubleshooting (ADO)

  | Symptom | Likely cause | Fix |
  |---|---|---|
  | `TF401019: The Git repository ... does not exist.` | `--repo` name does not match an ADO repo in the configured project. | Confirm with `az repos list --query "[].name"`. |
  | `VS800075: The project ... does not exist.` | `ADO_PROJECT` is the organisation name, not the project name. | Set `ADO_PROJECT` to the project; keep org in `ADO_ORG_URL`. |
  | `TF401028: The reference 'refs/heads/ralph-queue' has already been updated by another client.` | Concurrent setup runs. | Re-run; idempotency handles it. |
  | `identity not found for principal` | `--service-principal` value isn't recognised by ADO. | Use the UPN of an identity that has logged into ADO at least once; for groups, use the group display name. |
  | Helper script exits with `error: environment variable ADO_PAT is required`. | Shell didn't have `ADO_PAT` set. | Export the three env vars in the same shell. |
  ```

- [ ] 5. Confirm the markdown still renders sanely and now contains both phases in full:
  ```
  uv run python -c "from pathlib import Path; t = Path('docs/runbooks/ralph-queue-setup.md').read_text(encoding='utf-8'); [print(h, h in t) for h in ('# Phase 1 — GitHub', '# Phase 2 — Azure DevOps', '## Prerequisites (GitHub)', '## Prerequisites (ADO)', '## Verification (GitHub, all three options)', '## Verification (ADO, all three options)', '## Troubleshooting (GitHub)', '## Troubleshooting (ADO)')]"
  ```
  Expected: each section header prints `True`.

- [ ] 6. Commit (single commit covering the ADO script + the runbook expansion):
  ```
  git add scripts/setup_ralph_queue_ado.py tests/test_setup_ralph_queue_ado.py docs/runbooks/ralph-queue-setup.md
  git commit -m "feat(setup_ado): add ADO ralph-queue branch + policy helper and Phase 2 runbook"
  ```
  Expected: commit succeeds.

---

## Verification

This is the orchestrator's Plan 2 verification gate, split per phase. The gate as written in the orchestrator is:

> `git fetch && git branch -r | grep ralph-queue` (on test repo) — `origin/ralph-queue` exists

Plan 2 delivers the tooling that makes that gate satisfiable on demand for the chosen host.

### Phase 1 verification — artifact gate (no network)

- [ ] 1. Run all Phase 1 tests in isolation:
  ```
  uv run pytest tests/test_gh_client.py tests/test_setup_ralph_queue_github.py -v
  ```
  Expected: 16 tests collected, 16 passing.

- [ ] 2. Confirm the GitHub helper script's `--help` is meaningful:
  ```
  uv run python scripts/setup_ralph_queue_github.py --help
  ```
  Expected: help text mentions `--repo`, `--branch`, `--base-branch`, `--dry-run`, and the two env vars (`GH_TOKEN`, `GH_OWNER`).

- [ ] 3. Confirm the runbook's Phase 1 section is present and contains the expected sub-sections:
  ```
  uv run python -c "from pathlib import Path; t = Path('docs/runbooks/ralph-queue-setup.md').read_text(encoding='utf-8'); [print(h, h in t) for h in ('# Phase 1 — GitHub', '## Prerequisites (GitHub)', '## Option A — the helper script (recommended)', '## Option B — the GitHub web UI', '## Option C — the `gh` CLI', '## Verification (GitHub, all three options)', '## Troubleshooting (GitHub)')]"
  ```
  Expected: each section header prints `True`.

### Phase 1 verification — orchestrator gate on a test GitHub repo (manual; once per environment)

The orchestrator gate requires `origin/ralph-queue` to exist on the chosen test repo. This is a one-time manual rehearsal — the helper script + the runbook are the artifacts; the rehearsal demonstrates they work.

- [ ] 4. Pick a throwaway service repo under your GitHub owner (or create one — `ralph-test-bed` is a reasonable name). Export the env vars:
  ```
  export GH_TOKEN="<your PAT>"
  export GH_OWNER="<your owner>"
  ```

- [ ] 5. Run the helper:
  ```
  uv run python scripts/setup_ralph_queue_github.py --repo ralph-test-bed
  ```
  Expected: exit code 0; JSON summary with `"branch_created": true` and `"protection_applied": true`.

- [ ] 6. Verify the orchestrator's exact gate command. Clone the test repo locally and run:
  ```
  git fetch
  git branch -r | grep ralph-queue
  ```
  Expected: `origin/ralph-queue` is listed. If it is not, STOP and inspect the JSON output from step 5; do not declare Plan 2 Phase 1 complete.

- [ ] 7. Re-run the helper to confirm idempotency:
  ```
  uv run python scripts/setup_ralph_queue_github.py --repo ralph-test-bed
  ```
  Expected: exit code 0; JSON summary with `"branch_created": false`, `"branch_existed": true`, `"protection_applied": true`.

If Phase 1 steps 1–7 all pass, **Phase 1 of Plan 2 is complete** and downstream plans (Plan 4 `ralph-status`, Plan 7 executor) can rely on `ralph-queue` existing on a GitHub test repo and on `scripts.gh_client` / `scripts.setup_ralph_queue_github` being available in-repo for re-use (Plan 5's `pr-github` skill will depend on `scripts.gh_client`).

### Phase 2 verification — deferred

Phase 2 verification mirrors Phase 1 but targets ADO. It is **not part of the Phase 1 completion gate** — do not run it until Tasks 8–11 are executed (likely by Ralph). The full Phase 2 gate, once Phase 2 is implemented, is:

- [ ] (deferred) Run all Phase 2 tests:
  ```
  uv run pytest tests/test_ado_client.py tests/test_setup_ralph_queue_ado.py -v
  ```
  Expected: 10 tests collected, 10 passing.

- [ ] (deferred) Confirm the ADO helper script's `--help` is meaningful:
  ```
  uv run python scripts/setup_ralph_queue_ado.py --help
  ```
  Expected: help text mentions `--repo`, `--service-principal`, `--branch`, `--base-branch`, `--dry-run`, and the three env vars (`ADO_PAT`, `ADO_ORG_URL`, `ADO_PROJECT`).

- [ ] (deferred) Confirm the runbook's Phase 2 section is present:
  ```
  uv run python -c "from pathlib import Path; t = Path('docs/runbooks/ralph-queue-setup.md').read_text(encoding='utf-8'); [print(h, h in t) for h in ('# Phase 2 — Azure DevOps', '## Prerequisites (ADO)', '## Verification (ADO, all three options)', '## Troubleshooting (ADO)')]"
  ```
  Expected: each section header prints `True`.

- [ ] (deferred) Rehearse the orchestrator gate against an ADO test repo (export `ADO_PAT`/`ADO_ORG_URL`/`ADO_PROJECT`, run `scripts/setup_ralph_queue_ado.py --repo ralph-test-bed --service-principal ...`, then `git fetch && git branch -r | grep ralph-queue` against the cloned ADO repo).

Until Phase 2 is implemented, only Phase 1 needs to pass for the orchestrator's "after Plan 2" gate to be satisfied for the GitHub host. The orchestrator's host-selection logic ensures that a pod with `RALPH_GIT_HOST=github` never tries to use Phase 2 artifacts, and vice versa.
