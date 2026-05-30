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
MAIN_PROTECTION_URL = f"{REPO_URL}/branches/main/protection"
QUEUE_PROTECTION_URL = f"{REPO_URL}/branches/ralph-queue/protection"


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
        MAIN_PROTECTION_URL,
        json={"url": MAIN_PROTECTION_URL, "enabled": True},
        status=200,
    )
    responses.add(
        responses.PUT,
        QUEUE_PROTECTION_URL,
        json={"url": QUEUE_PROTECTION_URL, "enabled": True},
        status=200,
    )


def _register_seed_endpoints() -> None:
    """Register the content GETs (absent) + PUTs for the seed step.

    Task 13 adds README.md on main and `.ralph/<folder>/.gitkeep` skeleton
    files on ralph-queue. Each PUT is preceded by a GET to check existence
    (idempotency); we register the GETs as 404 and the PUTs as 201.
    """
    # README.md on main
    responses.add(
        responses.GET,
        f"{REPO_URL}/contents/README.md",
        json={"message": "Not Found"},
        status=404,
    )
    responses.add(
        responses.PUT,
        f"{REPO_URL}/contents/README.md",
        json={"commit": {"sha": "r" * 40}},
        status=201,
    )
    # Skeleton .gitkeep files on ralph-queue
    for folder in ("inbox", "current", "pending-pr", "blocked", "archive", "done"):
        responses.add(
            responses.GET,
            f"{REPO_URL}/contents/.ralph/{folder}/.gitkeep",
            json={"message": "Not Found"},
            status=404,
        )
        responses.add(
            responses.PUT,
            f"{REPO_URL}/contents/.ralph/{folder}/.gitkeep",
            json={"commit": {"sha": "k" * 40}},
            status=201,
        )
    # .ralph/config.toml stub on ralph-queue
    responses.add(
        responses.GET,
        f"{REPO_URL}/contents/.ralph/config.toml",
        json={"message": "Not Found"},
        status=404,
    )
    responses.add(
        responses.PUT,
        f"{REPO_URL}/contents/.ralph/config.toml",
        json={"commit": {"sha": "c" * 40}},
        status=201,
    )


@responses.activate
def test_happy_path_returns_zero_and_prints_json(
    env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _register_repo_lookup()
    _register_main_tip()
    _register_queue_branch_absent()
    _register_queue_branch_create()
    _register_seed_endpoints()
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
    _register_seed_endpoints()
    _register_protection_put()

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["branch_created"] is False
    assert payload["branch_existed"] is True
    assert payload["protection_applied"] is True


@responses.activate
def test_repo_lookup_403_exits_nonzero(env: None, capsys: pytest.CaptureFixture[str]) -> None:
    """A non-404 GhError on the repo lookup (e.g. 403 Forbidden — bad token
    scope) must surface as a clean exit 2. 404 is a separate case handled by
    ``_ensure_repo_exists`` (the repo is created)."""
    responses.add(
        responses.GET,
        REPO_URL,
        json={"message": "Forbidden"},
        status=403,
    )

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "Forbidden" in stderr


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
def test_dry_run_makes_no_mutations(env: None, capsys: pytest.CaptureFixture[str]) -> None:
    _register_repo_lookup()
    _register_main_tip()
    _register_queue_branch_absent()
    # Seed step still issues existence GETs in dry-run (skips PUTs).
    responses.add(
        responses.GET,
        f"{REPO_URL}/contents/README.md",
        json={"message": "Not Found"},
        status=404,
    )
    for folder in ("inbox", "current", "pending-pr", "blocked", "archive", "done"):
        responses.add(
            responses.GET,
            f"{REPO_URL}/contents/.ralph/{folder}/.gitkeep",
            json={"message": "Not Found"},
            status=404,
        )
    responses.add(
        responses.GET,
        f"{REPO_URL}/contents/.ralph/config.toml",
        json={"message": "Not Found"},
        status=404,
    )

    exit_code = setup_ralph_queue_github.main(["--repo", REPO, "--dry-run"])
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["branch_created"] is False
    assert payload["protection_applied"] is False
    # Happy-path dry-run on an existing repo: walked all steps, just skipped
    # the PUTs. dry_run_skipped should NOT be True here — that's reserved
    # for the early-exit paths (repo absent, base branch tipless).
    assert payload["dry_run_skipped"] is False
    assert all(call.request.method == "GET" for call in responses.calls)


@responses.activate
def test_protection_payload_shape(env: None, capsys: pytest.CaptureFixture[str]) -> None:
    """The PUTs to /branches/{main,ralph-queue}/protection send the documented shape."""
    _register_repo_lookup()
    _register_main_tip()
    _register_queue_branch_absent()
    _register_queue_branch_create()
    _register_seed_endpoints()
    _register_protection_put()

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 0

    # Find protection PUTs (the seed step also issues PUTs to /contents/...).
    put_calls = [
        c
        for c in responses.calls
        if c.request.method == "PUT" and c.request.url.endswith("/protection")
    ]
    # Two protection PUTs: one on main, one on ralph-queue.
    assert len(put_calls) == 2

    # ralph-queue payload: no PR requirement, no force-push, no deletion.
    queue_calls = [
        c for c in put_calls if c.request.url.endswith("/branches/ralph-queue/protection")
    ]
    assert len(queue_calls) == 1
    queue_body = json.loads(queue_calls[0].request.body)
    assert queue_body["enforce_admins"] is True
    assert queue_body["allow_force_pushes"] is False
    assert queue_body["allow_deletions"] is False
    assert queue_body["required_pull_request_reviews"] is None
    # Required-status-checks and restrictions may be null OR an object;
    # both are documented as accepted by the GitHub API. The script
    # MUST include each top-level key (even if value is null) per the docs.
    assert "required_status_checks" in queue_body
    assert "required_pull_request_reviews" in queue_body
    assert "restrictions" in queue_body

    # main payload: PR required (1 approval), no force-push, no deletion.
    main_calls = [c for c in put_calls if c.request.url.endswith("/branches/main/protection")]
    assert len(main_calls) == 1
    main_body = json.loads(main_calls[0].request.body)
    assert main_body["enforce_admins"] is True
    assert main_body["allow_force_pushes"] is False
    assert main_body["allow_deletions"] is False
    assert main_body["required_pull_request_reviews"]["required_approving_review_count"] == 1


@responses.activate
def test_protection_403_upgrade_to_pro_continues_clean(
    env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """GitHub Free + private repo: protection PUT returns 403 with
    ``Upgrade to GitHub Pro`` — script must warn, set protection_applied=false,
    and exit 0. Repo + branch + seed steps already succeeded; rolling those
    back would be worse than leaving the repo unprotected."""
    _register_repo_lookup()
    _register_main_tip()
    _register_queue_branch_absent()
    _register_queue_branch_create()
    _register_seed_endpoints()
    responses.add(
        responses.PUT,
        MAIN_PROTECTION_URL,
        json={
            "message": (
                "Upgrade to GitHub Pro or make this repository public to enable this feature."
            ),
            "documentation_url": "https://docs.github.com/...",
            "status": "403",
        },
        status=403,
    )

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["protection_applied"] is False
    assert "Upgrade to GitHub Pro" in captured.err or "branch protection" in captured.err.lower()


@responses.activate
def test_protection_403_other_message_still_fails(
    env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Any 403 that is NOT the Pro-upgrade message stays fatal. PAT-scope and
    ACL errors must still surface so the operator hits the existing
    troubleshooting paths."""
    _register_repo_lookup()
    _register_main_tip()
    _register_queue_branch_absent()
    _register_queue_branch_create()
    _register_seed_endpoints()
    responses.add(
        responses.PUT,
        MAIN_PROTECTION_URL,
        json={"message": "Resource not accessible by integration"},
        status=403,
    )

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 2


@responses.activate
def test_network_error_exits_nonzero(env: None, capsys: pytest.CaptureFixture[str]) -> None:
    """Network-level errors (ConnectionError, Timeout) must produce a clean
    exit code 2, not an unhandled traceback."""
    from requests.exceptions import ConnectionError as ReqConnectionError

    responses.add(
        responses.GET,
        REPO_URL,
        body=ReqConnectionError("simulated network failure"),
    )

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "network error" in stderr.lower() or "simulated network failure" in stderr


@responses.activate
def test_concurrent_create_422_still_applies_protection(
    env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Race regression: when a concurrent run creates ralph-queue between our
    GET-ref check and our POST-ref attempt, GitHub returns 422 'Reference
    already exists'. The script must treat this as branch-existed-already and
    still apply branch protection — otherwise the repo is left unprotected.
    Caught by BugBot on PR #2.
    """
    _register_repo_lookup()
    _register_main_tip()
    _register_queue_branch_absent()
    # POST returns 422 — branch was concurrently created.
    responses.add(
        responses.POST,
        REFS_CREATE_URL,
        json={"message": "Reference already exists"},
        status=422,
    )
    _register_seed_endpoints()
    _register_protection_put()

    exit_code = setup_ralph_queue_github.main(["--repo", REPO])
    assert exit_code == 0

    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert payload["branch_created"] is False
    assert payload["branch_existed"] is True
    assert payload["protection_applied"] is True
    # Confirm the protection PUTs actually fired (would be skipped if the 422
    # had escaped to the outer GhError handler). The seed step also issues
    # PUTs to /contents/..., so filter to the protection URLs specifically.
    # Dual-branch protection: PUT on both main and ralph-queue.
    put_calls = [
        c
        for c in responses.calls
        if c.request.method == "PUT" and c.request.url.endswith("/protection")
    ]
    assert len(put_calls) == 2
    urls = {c.request.url for c in put_calls}
    assert any(u.endswith("/branches/main/protection") for u in urls)
    assert any(u.endswith("/branches/ralph-queue/protection") for u in urls)


def test_creates_repo_when_absent_for_real(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the repo 404s and --dry-run is NOT set, the script POSTs /user/repos."""
    from scripts import setup_ralph_queue_github as setup

    calls: list[tuple[str, str]] = []
    # After the create POST, downstream GETs (main tip, queue tip) need to
    # succeed so main runs to completion. Track repo-existence per call.
    repo_seen = {"created": False}

    class FakeClient:
        def get(self, path: str, **_: Any) -> Any:
            if path == "/repos/test/queue":
                if repo_seen["created"]:
                    return {"id": 1, "full_name": "test/queue"}
                raise setup.GhError(404, "not found", path)
            if path == "/repos/test/queue/git/ref/heads/main":
                return {"ref": "refs/heads/main", "object": {"sha": MAIN_SHA}}
            if path == "/repos/test/queue/git/ref/heads/ralph-queue":
                raise setup.GhError(404, "not found", path)
            return {}

        def post(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            calls.append(("POST", path))
            if path in ("/user/repos", "/orgs/myorg/repos"):
                repo_seen["created"] = True
            return {}

        def put(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            calls.append(("PUT", path))
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue"])
    assert rc == 0
    assert ("POST", "/user/repos") in calls


def test_dry_run_does_not_create_absent_repo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dry-run prints 'would create' but does NOT POST."""
    from scripts import setup_ralph_queue_github as setup

    calls: list[tuple[str, str]] = []

    class FakeClient:
        def get(self, path: str, **_: Any) -> Any:
            if path == "/repos/test/queue":
                raise setup.GhError(404, "not found", path)
            return {}

        def post(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            calls.append(("POST", path))
            return {}

        def put(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            calls.append(("PUT", path))
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue", "--dry-run"])
    assert rc == 0
    assert ("POST", "/user/repos") not in calls
    assert not any(method == "POST" for method, _ in calls)
    captured = capsys.readouterr()
    err = captured.err
    assert "DRY-RUN would create" in err
    # The all-False branch_* fields are ambiguous on their own (they look
    # like an idempotent no-op on a fully-provisioned repo). dry_run_skipped
    # disambiguates: True here means "stopped early because repo absent".
    payload = json.loads(captured.out)
    assert payload["dry_run_skipped"] is True
    assert payload["branch_created"] is False
    assert payload["branch_existed"] is False


def test_org_flag_uses_orgs_endpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When --org is set, repo creation goes to /orgs/{org}/repos, not /user/repos."""
    from scripts import setup_ralph_queue_github as setup

    calls: list[tuple[str, str]] = []
    repo_seen = {"created": False}

    class FakeClient:
        def get(self, path: str, **_: Any) -> Any:
            if path == "/repos/test/queue":
                if repo_seen["created"]:
                    return {"id": 1, "full_name": "test/queue"}
                raise setup.GhError(404, "not found", path)
            if path == "/repos/test/queue/git/ref/heads/main":
                return {"ref": "refs/heads/main", "object": {"sha": MAIN_SHA}}
            if path == "/repos/test/queue/git/ref/heads/ralph-queue":
                raise setup.GhError(404, "not found", path)
            return {}

        def post(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            calls.append(("POST", path))
            if path in ("/user/repos", "/orgs/myorg/repos"):
                repo_seen["created"] = True
            return {}

        def put(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            calls.append(("PUT", path))
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue", "--org", "myorg"])
    assert rc == 0
    assert ("POST", "/orgs/myorg/repos") in calls
    assert ("POST", "/user/repos") not in calls


def test_seeds_readme_and_skeleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed step: README on main, .ralph/<state>/.gitkeep on ralph-queue."""
    from scripts import setup_ralph_queue_github as setup

    puts: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def get(self, path: str, **_: Any) -> Any:
            # Pretend main + ralph-queue refs exist (so creation skipped)
            # but README + .ralph/ skeleton content do NOT exist.
            if "/contents/README.md" in path or "/contents/.ralph" in path:
                raise setup.GhError(404, "not found", path)
            return {"object": {"sha": "abc123"}}

        def post(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            return {}

        def put(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            puts.append((path, json_body or {}))
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue", "--no-protection"])
    assert rc == 0
    # README PUT on main
    assert any(path.endswith("/contents/README.md") for path, _ in puts)
    # Skeleton PUTs on ralph-queue, one per state folder
    for folder in ("inbox", "current", "pending-pr", "blocked", "archive", "done"):
        assert any(path.endswith(f"/contents/.ralph/{folder}/.gitkeep") for path, _ in puts)


def test_protection_applied_to_main_and_ralph_queue(monkeypatch):
    """Protection step applies rules to both main and ralph-queue."""
    from scripts import setup_ralph_queue_github as setup

    protected: list[str] = []

    class FakeClient:
        def get(self, path):
            # All GETs succeed (repo, refs, contents) — protection-only happy path.
            return {"object": {"sha": "abc"}}

        def post(self, path, json_body=None):
            return {}

        def put(self, path, json_body=None):
            if "/protection" in path:
                protected.append(path)
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue"])
    assert rc == 0
    assert any(p.endswith("/branches/main/protection") for p in protected)
    assert any(p.endswith("/branches/ralph-queue/protection") for p in protected)


def test_seeds_ralph_config_toml_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setup script seeds .ralph/config.toml on the queue branch."""
    from scripts import setup_ralph_queue_github as setup

    puts: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def get(self, path: str, **_: Any) -> Any:
            if "/contents/README.md" in path or "/contents/.ralph" in path:
                raise setup.GhError(404, "not found", path)
            return {"object": {"sha": "abc123"}}

        def post(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            return {}

        def put(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            puts.append((path, json_body or {}))
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue", "--no-protection"])
    assert rc == 0
    assert any(path.endswith("/contents/.ralph/config.toml") for path, _ in puts)


def test_fresh_repo_gets_custom_readme(monkeypatch):
    """When the repo is newly created, the custom README content actually
    lands on main. Regression for BugBot finding: ``auto_init=True`` made
    GitHub commit a default README, which then short-circuited
    ``_seed_main_readme`` via the ``_content_exists`` guard.
    """
    from scripts import setup_ralph_queue_github as setup

    puts: list[tuple[str, dict[str, Any]]] = []

    repo_created = {"done": False}

    class FakeClient:
        def get(self, path: str, **_: Any) -> Any:
            # Repo absent on first GET, present after creation
            if path == "/repos/test/queue" and not repo_created["done"]:
                raise setup.GhError(404, "not found", path)
            if "/contents/README.md" in path:
                raise setup.GhError(404, "not found", path)
            if "/contents/.ralph" in path:
                raise setup.GhError(404, "not found", path)
            return {"object": {"sha": "abc"}}

        def post(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            if path == "/user/repos":
                repo_created["done"] = True
            return {}

        def put(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            puts.append((path, json_body or {}))
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue", "--no-protection"])
    assert rc == 0
    readme_puts = [p for p, _ in puts if p.endswith("/contents/README.md")]
    assert len(readme_puts) == 1, f"expected exactly one README PUT, got: {readme_puts}"
    # Verify the body content matches the spec
    import base64

    readme_payload = next(body for path, body in puts if path.endswith("/contents/README.md"))
    content_b64 = readme_payload["content"]
    decoded = base64.b64decode(content_b64).decode("utf-8")
    assert "Queue repo for ralph-executor" in decoded


def test_fresh_repo_seeds_main_before_reading_tip(monkeypatch):
    """Regression for BugBot round 3: with auto_init=False, main does not exist
    after repo creation. The script must PUT README.md before _read_branch_tip,
    so main is created in time for its tip to be read.

    Simulates GitHub realistically: ref lookups for main return 404 until the
    README PUT lands; ref lookups for ralph-queue return 404 until that branch
    is created.
    """
    from scripts import setup_ralph_queue_github as setup

    state = {
        "repo_created": False,
        "main_seeded": False,
        "ralph_queue_created": False,
    }
    puts: list[tuple[str, dict[str, Any]]] = []
    call_order: list[str] = []

    class FakeClient:
        def get(self, path: str, **_: Any) -> Any:
            call_order.append(f"GET {path}")
            if path == "/repos/test/queue" and not state["repo_created"]:
                raise setup.GhError(404, "not found", path)
            # ref lookups: main 404 until seeded, ralph-queue 404 until created
            if "/git/ref/heads/main" in path:
                if not state["main_seeded"]:
                    raise setup.GhError(404, "not found", path)
                return {"object": {"sha": "main-sha"}}
            if "/git/ref/heads/ralph-queue" in path:
                if not state["ralph_queue_created"]:
                    raise setup.GhError(404, "not found", path)
                return {"object": {"sha": "rq-sha"}}
            # contents: everything absent (fresh repo)
            if "/contents/" in path:
                raise setup.GhError(404, "not found", path)
            return {"object": {"sha": "abc"}}

        def post(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            call_order.append(f"POST {path}")
            if path == "/user/repos":
                state["repo_created"] = True
            if path.endswith("/git/refs"):
                state["ralph_queue_created"] = True
            return {}

        def put(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            call_order.append(f"PUT {path}")
            if path.endswith("/contents/README.md"):
                state["main_seeded"] = True
            puts.append((path, json_body or {}))
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue", "--no-protection"])
    assert rc == 0, f"expected rc=0 on fresh repo provisioning; got {rc}. Call order: {call_order}"
    # README PUT must happen BEFORE any GET that reads main's tip in
    # _read_branch_tip — otherwise the script would have aborted earlier.
    readme_put_idx = next(
        i for i, c in enumerate(call_order) if c.startswith("PUT ") and "/contents/README.md" in c
    )
    main_tip_get_idx = next(
        i for i, c in enumerate(call_order) if c.startswith("GET ") and "/git/ref/heads/main" in c
    )
    assert readme_put_idx < main_tip_get_idx, (
        f"README PUT (idx {readme_put_idx}) must precede main tip GET (idx {main_tip_get_idx}). "
        f"Call order: {call_order}"
    )


def test_base_branch_and_branch_flags_thread_through(monkeypatch):
    """When --base-branch master --branch wq is passed, the README PUT must
    target branch=master, the protection PUT must hit /branches/master/protection,
    and the README content must reference the configured queue branch ("wq")
    rather than the hardcoded "ralph-queue".

    Regression for BugBot findings PRRT_kwDOSnF2Ks6F2-J8 (MEDIUM: hardcoded main)
    and PRRT_kwDOSnF2Ks6F2-J9 (LOW: hardcoded ralph-queue in README content).
    """
    from scripts import setup_ralph_queue_github as setup

    puts: list[tuple[str, dict[str, Any]]] = []
    protected: list[str] = []

    class FakeClient:
        def get(self, path: str, **_: Any) -> Any:
            # Pretend repo + refs exist; README + .ralph absent so PUTs fire.
            if "/contents/README.md" in path or "/contents/.ralph" in path:
                raise setup.GhError(404, "not found", path)
            return {"object": {"sha": "abc"}}

        def post(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            return {}

        def put(self, path: str, *, json_body: Any = None, **_: Any) -> Any:
            puts.append((path, json_body or {}))
            if "/protection" in path:
                protected.append(path)
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue", "--base-branch", "master", "--branch", "wq"])
    assert rc == 0

    # README PUT goes to branch=master in the JSON body.
    readme_put = next(body for path, body in puts if path.endswith("/contents/README.md"))
    assert readme_put["branch"] == "master", (
        f"README PUT should target branch=master, got {readme_put['branch']!r}"
    )

    # README content mentions the configured queue branch "wq", not "ralph-queue".
    import base64

    decoded = base64.b64decode(readme_put["content"]).decode("utf-8")
    assert "`wq` branch" in decoded, f"README should mention 'wq branch', got: {decoded!r}"
    assert "ralph-queue" not in decoded, (
        f"README should not contain hardcoded 'ralph-queue', got: {decoded!r}"
    )

    # Protection PUT goes to /branches/master/protection (not /branches/main/).
    assert any(p.endswith("/branches/master/protection") for p in protected), (
        f"expected protection PUT on /branches/master/protection; got {protected}"
    )
    assert not any(p.endswith("/branches/main/protection") for p in protected), (
        f"protection must not target main when --base-branch=master; got {protected}"
    )
    # The queue branch protection also follows --branch.
    assert any(p.endswith("/branches/wq/protection") for p in protected), (
        f"expected protection PUT on /branches/wq/protection; got {protected}"
    )


def test_full_run_idempotent_on_second_invocation(monkeypatch):
    """Re-running on a fully-provisioned repo is a no-op (except protection re-applies)."""
    from scripts import setup_ralph_queue_github as setup

    posts: list[str] = []
    puts: list[str] = []

    class FakeClient:
        def get(self, path):
            # Everything exists already (repo, refs, README, .ralph/ skeleton, config.toml)
            return {"object": {"sha": "abc"}}

        def post(self, path, json_body=None):
            posts.append(path)
            return {}

        def put(self, path, json_body=None):
            puts.append(path)
            return {}

    monkeypatch.setattr(setup, "GhClient", lambda token: FakeClient())
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_OWNER", "test")

    rc = setup.main(["--repo", "queue", "--no-protection"])
    assert rc == 0
    # No POSTs (no branch creation, no repo creation)
    assert posts == []
    # No content PUTs (README + skeleton + config.toml already exist)
    content_puts = [p for p in puts if "/contents/" in p]
    assert content_puts == [], f"Expected zero content PUTs on re-run, got: {content_puts}"
    # Protection PUTs are skipped because --no-protection
    protection_puts = [p for p in puts if "/protection" in p]
    assert protection_puts == []
