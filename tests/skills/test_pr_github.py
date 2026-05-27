"""Tests for the ``pr-github`` skill's entry scripts.

Each operation has its own script file. The skill directory name
(``pr-github``) contains a hyphen, so we load every script via
``importlib.util.spec_from_file_location`` and cache the loaded module
through a per-operation fixture.

All GitHub REST and GraphQL endpoints are mocked with ``responses``; no
network calls happen during the test run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import responses

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "skills" / "pr-github" / "scripts"

GH_API = "https://api.github.com"
GRAPHQL_URL = f"{GH_API}/graphql"
OWNER = "example-org"
TOKEN = "fake-token"
REPO = "service-auth"
PR_ID = 4242
THREAD_NODE_ID = "PRRT_kwDOABCD12345"


# ----------------------------------------------------------------------
# Module loader helpers
# ----------------------------------------------------------------------


def _load_module(name: str, path: Path) -> ModuleType:
    assert path.is_file(), f"missing entry script at {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve string annotations
    # (from __future__ import annotations) via sys.modules[cls.__module__].
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def common_module() -> ModuleType:
    return _load_module("pr_github_common", SCRIPTS_DIR / "_common.py")


@pytest.fixture(scope="module")
def create_pr_module() -> ModuleType:
    return _load_module("pr_github_create_pr", SCRIPTS_DIR / "create_pr.py")


@pytest.fixture(scope="module")
def read_threads_module() -> ModuleType:
    return _load_module("pr_github_read_threads", SCRIPTS_DIR / "read_threads.py")


@pytest.fixture(scope="module")
def reply_module() -> ModuleType:
    return _load_module("pr_github_reply", SCRIPTS_DIR / "reply.py")


@pytest.fixture(scope="module")
def set_status_module() -> ModuleType:
    return _load_module("pr_github_set_status", SCRIPTS_DIR / "set_status.py")


@pytest.fixture(scope="module")
def show_module() -> ModuleType:
    return _load_module("pr_github_show", SCRIPTS_DIR / "show.py")


# ----------------------------------------------------------------------
# Env fixture
# ----------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.setenv("GH_OWNER", OWNER)


# ----------------------------------------------------------------------
# URL builders
# ----------------------------------------------------------------------


def _pulls_url(repo: str = REPO) -> str:
    return f"{GH_API}/repos/{OWNER}/{repo}/pulls"


def _pr_url(pr_id: int = PR_ID, repo: str = REPO) -> str:
    return f"{_pulls_url(repo)}/{pr_id}"


def _reviews_url(pr_id: int = PR_ID, repo: str = REPO) -> str:
    return f"{_pr_url(pr_id, repo)}/reviews"


def _check_runs_url(sha: str, repo: str = REPO) -> str:
    return f"{GH_API}/repos/{OWNER}/{repo}/commits/{sha}/check-runs"


# ----------------------------------------------------------------------
# GraphQL routing helper — dispatches by operationName in the request body
# ----------------------------------------------------------------------


def _make_graphql_router(
    responses_by_op: dict[str, dict[str, Any]],
    status_by_op: dict[str, int] | None = None,
) -> Any:
    status_by_op = status_by_op or {}

    def _callback(request: Any) -> tuple[int, dict[str, str], str]:
        body = json.loads(request.body)
        query = body.get("query", "")
        op_name = body.get("operationName") or ""
        # Fall back to detecting from the query text if operationName is
        # absent — the scripts include `operationName` to make this easy.
        if not op_name:
            for candidate in responses_by_op:
                if f"{candidate}(" in query or f" {candidate}" in query:
                    op_name = candidate
                    break
        payload = responses_by_op.get(
            op_name, {"errors": [{"message": f"no mock for {op_name!r}"}]}
        )
        status = status_by_op.get(op_name, 200)
        return status, {"Content-Type": "application/json"}, json.dumps(payload)

    return _callback


# ----------------------------------------------------------------------
# _common.py tests
# ----------------------------------------------------------------------


def test_common_load_client_requires_token(
    common_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GH_OWNER", OWNER)
    with pytest.raises(common_module.FatalError) as excinfo:
        common_module.load_client()
    assert "GH_TOKEN" in str(excinfo.value)


def test_common_load_client_requires_owner(
    common_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.delenv("GH_OWNER", raising=False)
    with pytest.raises(common_module.FatalError) as excinfo:
        common_module.load_client()
    assert "GH_OWNER" in str(excinfo.value)


def test_common_load_client_returns_client(common_module: ModuleType, env: None) -> None:
    client = common_module.load_client()
    assert client.owner == OWNER
    assert client.token == TOKEN


def test_common_parse_pr_id_accepts_bare_int(
    common_module: ModuleType,
) -> None:
    assert common_module.parse_pr_id("4242") == 4242


def test_common_parse_pr_id_rejects_non_positive(
    common_module: ModuleType,
) -> None:
    with pytest.raises(common_module.FatalError, match="positive integer"):
        common_module.parse_pr_id("0")
    with pytest.raises(common_module.FatalError, match="positive integer"):
        common_module.parse_pr_id("-1")


def test_common_parse_pr_id_rejects_garbage(
    common_module: ModuleType,
) -> None:
    with pytest.raises(common_module.FatalError, match="positive integer"):
        common_module.parse_pr_id("abc")
    with pytest.raises(common_module.FatalError, match="positive integer"):
        common_module.parse_pr_id("")


def test_common_parse_thread_id_rejects_empty(
    common_module: ModuleType,
) -> None:
    with pytest.raises(common_module.FatalError, match="non-empty"):
        common_module.parse_thread_id("")
    with pytest.raises(common_module.FatalError, match="non-empty"):
        common_module.parse_thread_id("   ")


def test_common_parse_thread_id_accepts_opaque_string(
    common_module: ModuleType,
) -> None:
    assert common_module.parse_thread_id("PRRT_kwDOABCD12345") == "PRRT_kwDOABCD12345"


def test_common_parse_branch_passes_bare_name_through(common_module: ModuleType) -> None:
    assert common_module.parse_branch("ralph/WI-1234") == "ralph/WI-1234"


def test_common_parse_branch_strips_refs_heads_prefix(common_module: ModuleType) -> None:
    """refs/heads/<name> is the fully-qualified ref form; create-pr's --source-
    branch / --target-branch flags accept either form. The helper must
    normalise to the bare branch name."""
    assert common_module.parse_branch("refs/heads/main") == "main"
    assert common_module.parse_branch("refs/heads/ralph/WI-1234") == "ralph/WI-1234"


def test_common_parse_branch_rejects_empty(common_module: ModuleType) -> None:
    with pytest.raises(common_module.FatalError):
        common_module.parse_branch("")
    with pytest.raises(common_module.FatalError):
        common_module.parse_branch("   ")


@responses.activate
def test_common_graphql_error_handles_non_dict_items(env: None) -> None:
    """GraphQL errors array can contain non-dict items (plain strings).
    Earlier filter dropped them, producing 'GraphQL errors: ' with no
    detail. Caught by BugBot on PR #3.
    """
    common = _load_module("common_test_err", SCRIPTS_DIR / "_common.py")
    responses.add(
        responses.POST,
        GRAPHQL_URL,
        json={"errors": ["raw string error", {"message": "structured error"}]},
        status=200,
    )
    client = common.load_client()
    with pytest.raises(common.HttpError) as excinfo:
        client.graphql(query="query{viewer{login}}", variables={}, operation_name="Test")
    msg = str(excinfo.value)
    assert "raw string error" in msg
    assert "structured error" in msg


# ----------------------------------------------------------------------
# create-pr tests
# ----------------------------------------------------------------------


def _register_create_pr_success(
    pr_id: int = PR_ID,
    source_branch: str = "ralph/WI-1234",
    target_branch: str = "main",
    title: str = "WI-1234: add /healthz",
    is_draft: bool = False,
) -> None:
    responses.add(
        responses.POST,
        _pulls_url(),
        json={
            "number": pr_id,
            "title": title,
            "head": {"ref": source_branch},
            "base": {"ref": target_branch},
            "draft": is_draft,
            "html_url": f"https://github.com/{OWNER}/{REPO}/pull/{pr_id}",
            "url": f"{GH_API}/repos/{OWNER}/{REPO}/pulls/{pr_id}",
        },
        status=201,
    )


@responses.activate
def test_create_pr_happy_path(
    env: None,
    create_pr_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_create_pr_success()
    exit_code = create_pr_module.main(
        [
            "--repo",
            REPO,
            "--source-branch",
            "ralph/WI-1234",
            "--target-branch",
            "main",
            "--title",
            "WI-1234: add /healthz",
            "--body",
            "Closes #1234.",
        ]
    )
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["pr_id"] == PR_ID
    assert payload["repo"] == REPO
    assert payload["source_branch"] == "ralph/WI-1234"
    assert payload["target_branch"] == "main"
    assert payload["is_draft"] is False
    assert payload["dry_run"] is False
    assert payload["url"].endswith(f"/pull/{PR_ID}")

    raw_body = responses.calls[0].request.body
    assert isinstance(raw_body, (str, bytes))
    posted = json.loads(raw_body)
    assert posted["head"] == "ralph/WI-1234"
    assert posted["base"] == "main"
    assert posted["title"] == "WI-1234: add /healthz"
    assert posted["body"] == "Closes #1234."
    assert posted["draft"] is False


@responses.activate
def test_create_pr_with_draft(
    env: None,
    create_pr_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _register_create_pr_success(is_draft=True)
    exit_code = create_pr_module.main(
        [
            "--repo",
            REPO,
            "--source-branch",
            "ralph/WI-1234",
            "--title",
            "WI-1234: add /healthz",
            "--draft",
        ]
    )
    assert exit_code == 0
    raw_body = responses.calls[0].request.body
    assert isinstance(raw_body, (str, bytes))
    posted = json.loads(raw_body)
    assert posted["draft"] is True


@responses.activate
def test_create_pr_dry_run_makes_no_post(
    env: None,
    create_pr_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = create_pr_module.main(
        [
            "--repo",
            REPO,
            "--source-branch",
            "ralph/WI-1234",
            "--title",
            "dry",
            "--dry-run",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["pr_id"] is None
    assert len(responses.calls) == 0


def test_create_pr_missing_env_var_exits_two(
    monkeypatch: pytest.MonkeyPatch,
    create_pr_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GH_OWNER", OWNER)
    exit_code = create_pr_module.main(
        [
            "--repo",
            REPO,
            "--source-branch",
            "ralph/WI-1234",
            "--title",
            "x",
        ]
    )
    assert exit_code == 2
    assert "GH_TOKEN" in capsys.readouterr().err


@responses.activate
def test_create_pr_http_error_exits_three(
    env: None,
    create_pr_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add(
        responses.POST,
        _pulls_url(),
        json={"message": "Not Found", "documentation_url": "https://docs.github.com/rest"},
        status=404,
    )
    exit_code = create_pr_module.main(
        [
            "--repo",
            REPO,
            "--source-branch",
            "ralph/WI-1234",
            "--title",
            "x",
        ]
    )
    assert exit_code == 3
    assert "Not Found" in capsys.readouterr().err


# ----------------------------------------------------------------------
# read-threads tests
# ----------------------------------------------------------------------


def _sample_review_threads_payload() -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "id": THREAD_NODE_ID,
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "src/app.py",
                                "line": 12,
                                "startLine": 12,
                                "comments": {
                                    "nodes": [
                                        {
                                            "id": "PRRC_kw1",
                                            "databaseId": 1,
                                            "body": "Why is this off by one?",
                                            "createdAt": "2026-05-22T09:30:00Z",
                                            "author": {"login": "rachel"},
                                        }
                                    ]
                                },
                            },
                            {
                                "id": "PRRT_kwDOABCD99999",
                                "isResolved": True,
                                "isOutdated": False,
                                "path": None,
                                "line": None,
                                "startLine": None,
                                "comments": {
                                    "nodes": [
                                        {
                                            "id": "PRRC_kw2",
                                            "databaseId": 2,
                                            "body": "Resolved in abcdef0.",
                                            "createdAt": "2026-05-22T10:00:00Z",
                                            "author": {"login": "ralph-bot"},
                                        }
                                    ]
                                },
                            },
                        ]
                    }
                }
            }
        }
    }


@responses.activate
def test_read_threads_happy_path(
    env: None,
    read_threads_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add_callback(
        responses.POST,
        GRAPHQL_URL,
        callback=_make_graphql_router({"ReadThreads": _sample_review_threads_payload()}),
        content_type="application/json",
    )
    exit_code = read_threads_module.main(["--repo", REPO, "--pr-id", str(PR_ID)])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pr_id"] == PR_ID
    assert payload["repo"] == REPO
    assert len(payload["threads"]) == 2

    active = payload["threads"][0]
    assert active["id"] == THREAD_NODE_ID
    assert active["status"] == "active"
    assert active["is_deleted"] is False
    assert active["context"]["file_path"] == "src/app.py"
    assert active["context"]["right_start_line"] == 12
    assert active["comments"][0]["content"] == "Why is this off by one?"
    assert active["comments"][0]["author"] == "rachel"
    assert active["comments"][0]["id"] == 1

    resolved = payload["threads"][1]
    assert resolved["status"] == "closed"
    assert resolved["context"] is None


@responses.activate
def test_read_threads_graphql_error_exits_three(
    env: None,
    read_threads_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add_callback(
        responses.POST,
        GRAPHQL_URL,
        callback=_make_graphql_router(
            {"ReadThreads": {"errors": [{"message": "Could not resolve to a Repository."}]}}
        ),
        content_type="application/json",
    )
    exit_code = read_threads_module.main(["--repo", REPO, "--pr-id", str(PR_ID)])
    assert exit_code == 3
    assert "Could not resolve" in capsys.readouterr().err


@responses.activate
def test_read_threads_http_404_exits_three(
    env: None,
    read_threads_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add(
        responses.POST,
        GRAPHQL_URL,
        json={"message": "Not Found"},
        status=404,
    )
    exit_code = read_threads_module.main(["--repo", REPO, "--pr-id", str(PR_ID)])
    assert exit_code == 3


def test_read_threads_malformed_pr_id_exits_two(
    env: None,
    read_threads_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = read_threads_module.main(["--repo", REPO, "--pr-id", "not-a-number"])
    assert exit_code == 2
    assert "positive integer" in capsys.readouterr().err


@responses.activate
def test_read_threads_nonexistent_pr_exits_two(
    env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """GraphQL returns {data: {repository: {pullRequest: null}}} when the PR
    doesn't exist. The script must surface this as an error, not silently
    return an empty thread list with exit 0. Caught by BugBot on PR #3.
    """
    responses.add(
        responses.POST,
        GRAPHQL_URL,
        json={"data": {"repository": {"pullRequest": None}}},
        status=200,
    )
    mod = _load_module("read_threads_test", SCRIPTS_DIR / "read_threads.py")
    exit_code = mod.main(["--repo", REPO, "--pr-id", str(PR_ID)])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "not found" in err.lower() or f"#{PR_ID}" in err


# ----------------------------------------------------------------------
# reply tests
# ----------------------------------------------------------------------


def _reply_success_payload(database_id: int = 99) -> dict[str, Any]:
    return {
        "data": {
            "addPullRequestReviewThreadReply": {
                "comment": {
                    "id": "PRRC_kw_reply",
                    "databaseId": database_id,
                    "body": "Applied in abcdef0.",
                    "createdAt": "2026-05-22T11:00:00Z",
                    "author": {"login": "ralph-bot"},
                }
            }
        }
    }


@responses.activate
def test_reply_happy_path(
    env: None,
    reply_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add_callback(
        responses.POST,
        GRAPHQL_URL,
        callback=_make_graphql_router({"Reply": _reply_success_payload()}),
        content_type="application/json",
    )
    exit_code = reply_module.main(
        [
            "--repo",
            REPO,
            "--pr-id",
            str(PR_ID),
            "--thread-id",
            THREAD_NODE_ID,
            "--text",
            "Applied in abcdef0.",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["comment_id"] == 99
    assert payload["thread_id"] == THREAD_NODE_ID
    assert payload["pr_id"] == PR_ID
    assert payload["repo"] == REPO
    assert payload["posted_at"] == "2026-05-22T11:00:00Z"

    raw_body = responses.calls[0].request.body
    assert isinstance(raw_body, (str, bytes))
    posted = json.loads(raw_body)
    assert posted["variables"]["threadId"] == THREAD_NODE_ID
    assert posted["variables"]["body"] == "Applied in abcdef0."


def test_reply_empty_text_exits_two(
    env: None,
    reply_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = reply_module.main(
        [
            "--repo",
            REPO,
            "--pr-id",
            str(PR_ID),
            "--thread-id",
            THREAD_NODE_ID,
            "--text",
            "",
        ]
    )
    assert exit_code == 2
    assert "non-empty" in capsys.readouterr().err.lower()


def test_reply_empty_thread_id_exits_two(
    env: None,
    reply_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = reply_module.main(
        [
            "--repo",
            REPO,
            "--pr-id",
            str(PR_ID),
            "--thread-id",
            "   ",
            "--text",
            "x",
        ]
    )
    assert exit_code == 2
    assert "non-empty" in capsys.readouterr().err.lower()


@responses.activate
def test_reply_graphql_error_exits_three(
    env: None,
    reply_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add_callback(
        responses.POST,
        GRAPHQL_URL,
        callback=_make_graphql_router(
            {"Reply": {"errors": [{"message": "Could not resolve thread."}]}}
        ),
        content_type="application/json",
    )
    exit_code = reply_module.main(
        [
            "--repo",
            REPO,
            "--pr-id",
            str(PR_ID),
            "--thread-id",
            THREAD_NODE_ID,
            "--text",
            "x",
        ]
    )
    assert exit_code == 3
    assert "Could not resolve thread" in capsys.readouterr().err


# ----------------------------------------------------------------------
# set-status tests
# ----------------------------------------------------------------------


def _resolve_payload(resolved: bool = True) -> dict[str, Any]:
    return {
        "data": {"resolveReviewThread": {"thread": {"id": THREAD_NODE_ID, "isResolved": resolved}}}
    }


def _unresolve_payload(resolved: bool = False) -> dict[str, Any]:
    return {
        "data": {
            "unresolveReviewThread": {"thread": {"id": THREAD_NODE_ID, "isResolved": resolved}}
        }
    }


@pytest.mark.parametrize("status", ["fixed", "closed"])
@responses.activate
def test_set_status_resolves_for_fixed_and_closed(
    env: None,
    set_status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    responses.add_callback(
        responses.POST,
        GRAPHQL_URL,
        callback=_make_graphql_router({"Resolve": _resolve_payload()}),
        content_type="application/json",
    )
    exit_code = set_status_module.main(
        [
            "--repo",
            REPO,
            "--pr-id",
            str(PR_ID),
            "--thread-id",
            THREAD_NODE_ID,
            "--status",
            status,
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == status
    assert payload["thread_id"] == THREAD_NODE_ID
    assert payload["pr_id"] == PR_ID
    assert payload["repo"] == REPO
    # previous_status: GitHub returns the new isResolved; the script
    # infers previous_status from the requested change. Since we requested
    # a resolve, the previous state was unresolved → "active".
    assert payload["previous_status"] == "active"

    raw_body = responses.calls[0].request.body
    assert isinstance(raw_body, (str, bytes))
    posted = json.loads(raw_body)
    assert "resolveReviewThread" in posted["query"]
    assert posted["variables"]["threadId"] == THREAD_NODE_ID


@pytest.mark.parametrize("status", ["active", "pending"])
@responses.activate
def test_set_status_unresolves_for_active_and_pending(
    env: None,
    set_status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    responses.add_callback(
        responses.POST,
        GRAPHQL_URL,
        callback=_make_graphql_router({"Unresolve": _unresolve_payload()}),
        content_type="application/json",
    )
    exit_code = set_status_module.main(
        [
            "--repo",
            REPO,
            "--pr-id",
            str(PR_ID),
            "--thread-id",
            THREAD_NODE_ID,
            "--status",
            status,
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == status
    assert payload["previous_status"] == "closed"

    raw_body = responses.calls[0].request.body
    assert isinstance(raw_body, (str, bytes))
    posted = json.loads(raw_body)
    assert "unresolveReviewThread" in posted["query"]


@pytest.mark.parametrize("status", ["wontFix", "byDesign"])
def test_set_status_rejects_human_only_statuses(
    env: None,
    set_status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    exit_code = set_status_module.main(
        [
            "--repo",
            REPO,
            "--pr-id",
            str(PR_ID),
            "--thread-id",
            THREAD_NODE_ID,
            "--status",
            status,
        ]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "human-only" in err.lower()
    assert status in err


def test_set_status_rejects_unknown_status(
    env: None,
    set_status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        set_status_module.main(
            [
                "--repo",
                REPO,
                "--pr-id",
                str(PR_ID),
                "--thread-id",
                THREAD_NODE_ID,
                "--status",
                "made-up",
            ]
        )
    assert excinfo.value.code == 2


@responses.activate
def test_set_status_graphql_error_exits_three(
    env: None,
    set_status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add_callback(
        responses.POST,
        GRAPHQL_URL,
        callback=_make_graphql_router({"Resolve": {"errors": [{"message": "Thread not found."}]}}),
        content_type="application/json",
    )
    exit_code = set_status_module.main(
        [
            "--repo",
            REPO,
            "--pr-id",
            str(PR_ID),
            "--thread-id",
            THREAD_NODE_ID,
            "--status",
            "fixed",
        ]
    )
    assert exit_code == 3
    assert "Thread not found" in capsys.readouterr().err


# ----------------------------------------------------------------------
# show tests
# ----------------------------------------------------------------------


def _sample_pr_payload(
    state: str = "open",
    merged: bool = False,
    head_sha: str = "abc123",
) -> dict[str, Any]:
    return {
        "number": PR_ID,
        "title": "WI-1234: add /healthz",
        "state": state,
        "merged": merged,
        "mergeable": True,
        "mergeable_state": "clean",
        "draft": False,
        "head": {"ref": "ralph/WI-1234", "sha": head_sha},
        "base": {"ref": "main"},
        "user": {"login": "ralph-bot"},
        "created_at": "2026-05-22T08:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "html_url": f"https://github.com/{OWNER}/{REPO}/pull/{PR_ID}",
        "url": f"{GH_API}/repos/{OWNER}/{REPO}/pulls/{PR_ID}",
        "requested_reviewers": [{"login": "rachel", "id": 11}],
    }


def _sample_reviews_payload() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "user": {"login": "rachel"},
            "state": "APPROVED",
            "submitted_at": "2026-05-22T09:00:00Z",
        },
        {
            "id": 2,
            "user": {"login": "acr-bot"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-05-22T09:30:00Z",
        },
    ]


def _sample_check_runs_payload() -> dict[str, Any]:
    return {
        "total_count": 2,
        "check_runs": [
            {
                "name": "service-auth-ci",
                "status": "completed",
                "conclusion": "success",
                "output": {"summary": "Build 412 passed."},
                "details_url": "https://github.com/example-org/service-auth/actions/runs/412",
            },
            {
                "name": "auto-code-review",
                "status": "in_progress",
                "conclusion": None,
                "output": {"summary": "Auto code review running."},
                "details_url": None,
            },
        ],
    }


@responses.activate
def test_show_happy_path(
    env: None,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add(responses.GET, _pr_url(), json=_sample_pr_payload(), status=200)
    responses.add(
        responses.GET,
        _reviews_url(),
        json=_sample_reviews_payload(),
        status=200,
    )
    responses.add(
        responses.GET,
        _check_runs_url("abc123"),
        json=_sample_check_runs_payload(),
        status=200,
    )

    exit_code = show_module.main(["--repo", REPO, "--pr-id", str(PR_ID)])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pr_id"] == PR_ID
    assert payload["title"] == "WI-1234: add /healthz"
    assert payload["status"] == "active"  # open → active
    assert payload["merge_status"] == "mergeable"
    assert payload["mergeable_state"] == "clean"
    assert payload["source_branch"] == "ralph/WI-1234"
    assert payload["target_branch"] == "main"
    assert payload["is_draft"] is False
    assert payload["created_by"]["unique_name"] == "ralph-bot"
    assert payload["closed_date"] is None
    assert payload["url"].endswith(f"/pull/{PR_ID}")

    reviewers = {r["unique_name"]: r for r in payload["reviewers"]}
    assert reviewers["rachel"]["vote_label"] == "approved"
    assert reviewers["acr-bot"]["vote_label"] == "changes-requested"

    statuses = {s["name"]: s for s in payload["statuses"]}
    assert statuses["service-auth-ci"]["state"] == "succeeded"
    assert statuses["auto-code-review"]["state"] == "pending"


@responses.activate
def test_show_merged_pr(
    env: None,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pr = _sample_pr_payload(state="closed", merged=True)
    pr["closed_at"] = "2026-05-22T13:00:00Z"
    pr["merged_at"] = "2026-05-22T13:00:00Z"
    responses.add(responses.GET, _pr_url(), json=pr, status=200)
    responses.add(responses.GET, _reviews_url(), json=[], status=200)
    responses.add(
        responses.GET,
        _check_runs_url("abc123"),
        json={"total_count": 0, "check_runs": []},
        status=200,
    )
    exit_code = show_module.main(["--repo", REPO, "--pr-id", str(PR_ID)])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    assert payload["closed_date"] == "2026-05-22T13:00:00Z"
    assert payload["statuses"] == []


@responses.activate
def test_show_abandoned_pr(
    env: None,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pr = _sample_pr_payload(state="closed", merged=False)
    pr["closed_at"] = "2026-05-22T13:00:00Z"
    responses.add(responses.GET, _pr_url(), json=pr, status=200)
    responses.add(responses.GET, _reviews_url(), json=[], status=200)
    responses.add(
        responses.GET,
        _check_runs_url("abc123"),
        json={"total_count": 0, "check_runs": []},
        status=200,
    )
    exit_code = show_module.main(["--repo", REPO, "--pr-id", str(PR_ID)])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "abandoned"


@responses.activate
def test_show_pr_not_found_exits_three(
    env: None,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add(
        responses.GET,
        _pr_url(),
        json={"message": "Not Found"},
        status=404,
    )
    exit_code = show_module.main(["--repo", REPO, "--pr-id", str(PR_ID)])
    assert exit_code == 3
    assert "Not Found" in capsys.readouterr().err


def test_show_malformed_pr_id_exits_two(
    env: None,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = show_module.main(["--repo", REPO, "--pr-id", "abc"])
    assert exit_code == 2
    assert "positive integer" in capsys.readouterr().err


@responses.activate
@pytest.mark.parametrize(
    "mergeable_state,expected_status",
    [
        ("clean", "mergeable"),
        ("unstable", "mergeable"),
        ("has_hooks", "mergeable"),
        ("dirty", "conflicting"),
        ("blocked", "unknown"),
        ("behind", "unknown"),
        ("unknown", "unknown"),
        ("some_future_state", "unknown"),
    ],
)
def test_show_merge_status_mapping(
    env: None,
    capsys: pytest.CaptureFixture[str],
    mergeable_state: str,
    expected_status: str,
) -> None:
    """When GitHub's mergeable is null, mergeable_state is consulted. Map
    every GitHub vocabulary onto the documented {mergeable, conflicting,
    unknown} set. Caught by BugBot on PR #3.
    """
    pr_url = _pr_url()
    reviews_url = f"{pr_url}/reviews?per_page=100"
    checks_url = _check_runs_url("abc123") + "?per_page=100"
    responses.add(
        responses.GET,
        pr_url,
        json={
            "number": PR_ID,
            "title": "x",
            "state": "open",
            "draft": False,
            "merged": False,
            "mergeable": None,
            "mergeable_state": mergeable_state,
            "head": {"ref": "feat", "sha": "abc123"},
            "base": {"ref": "main"},
            "user": {"login": "u"},
            "created_at": "2026-05-25T00:00:00Z",
            "closed_at": None,
            "html_url": pr_url,
        },
        status=200,
    )
    responses.add(responses.GET, reviews_url, json=[], status=200)
    responses.add(responses.GET, checks_url, json={"check_runs": []}, status=200)

    mod = _load_module(f"show_test_merge_{mergeable_state}", SCRIPTS_DIR / "show.py")
    exit_code = mod.main(["--repo", REPO, "--pr-id", str(PR_ID)])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["merge_status"] == expected_status
    # Raw mergeable_state is also passed through verbatim — downstream
    # callers (sweep's auto-merge predicate) gate on the host vocab.
    assert payload["mergeable_state"] == mergeable_state


@responses.activate
def test_show_mergeable_state_null_passthrough(
    env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When GitHub returns ``mergeable_state: null`` (async check not yet
    resolved), the raw field is emitted as JSON ``null``."""
    pr_url = _pr_url()
    reviews_url = f"{pr_url}/reviews?per_page=100"
    checks_url = _check_runs_url("abc123") + "?per_page=100"
    responses.add(
        responses.GET,
        pr_url,
        json={
            "number": PR_ID,
            "title": "x",
            "state": "open",
            "draft": False,
            "merged": False,
            "mergeable": None,
            "mergeable_state": None,
            "head": {"ref": "feat", "sha": "abc123"},
            "base": {"ref": "main"},
            "user": {"login": "u"},
            "created_at": "2026-05-25T00:00:00Z",
            "closed_at": None,
            "html_url": pr_url,
        },
        status=200,
    )
    responses.add(responses.GET, reviews_url, json=[], status=200)
    responses.add(responses.GET, checks_url, json={"check_runs": []}, status=200)

    mod = _load_module("show_test_mergeable_null", SCRIPTS_DIR / "show.py")
    exit_code = mod.main(["--repo", REPO, "--pr-id", str(PR_ID)])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mergeable_state"] is None


# ---------------------------------------------------------------------------
# lookup_by_branch
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lookup_by_branch_module() -> ModuleType:
    return _load_module("pr_github_lookup_by_branch", SCRIPTS_DIR / "lookup_by_branch.py")


def _lookup_pulls_url(repo: str = REPO) -> str:
    return f"{GH_API}/repos/{OWNER}/{repo}/pulls"


def _lookup_branch_url(branch: str, repo: str = REPO) -> str:
    return f"{GH_API}/repos/{OWNER}/{repo}/branches/{branch}"


@responses.activate
def test_lookup_by_branch_returns_open_pr(
    lookup_by_branch_module: ModuleType,
    env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add(
        responses.GET,
        _lookup_pulls_url(),
        json=[
            {
                "number": 42,
                "state": "open",
                "merged_at": None,
                "html_url": "https://github.com/example-org/service-auth/pull/42",
            }
        ],
        status=200,
    )
    rc = lookup_by_branch_module.main(["--branch", "ralph/PBI-XYZ", "--repo", REPO])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pr"] == {
        "state": "open",
        "url": "https://github.com/example-org/service-auth/pull/42",
        "pr_id": 42,
        "merged_at": None,
    }
    assert payload["branch_exists"] is None


@responses.activate
def test_lookup_by_branch_returns_merged_pr(
    lookup_by_branch_module: ModuleType,
    env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add(
        responses.GET,
        _lookup_pulls_url(),
        json=[
            {
                "number": 25,
                "state": "closed",
                "merged_at": "2026-05-26T18:00:00Z",
                "html_url": "https://github.com/example-org/service-auth/pull/25",
            }
        ],
        status=200,
    )
    rc = lookup_by_branch_module.main(["--branch", "ralph/STAGE-B-PLAN-03", "--repo", REPO])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pr"]["state"] == "merged"
    assert payload["pr"]["merged_at"] == "2026-05-26T18:00:00Z"


@responses.activate
def test_lookup_by_branch_returns_closed_unmerged_pr(
    lookup_by_branch_module: ModuleType,
    env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add(
        responses.GET,
        _lookup_pulls_url(),
        json=[
            {
                "number": 7,
                "state": "closed",
                "merged_at": None,
                "html_url": "https://github.com/example-org/service-auth/pull/7",
            }
        ],
        status=200,
    )
    rc = lookup_by_branch_module.main(["--branch", "ralph/x", "--repo", REPO])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pr"]["state"] == "closed"


@responses.activate
def test_lookup_by_branch_returns_null_when_no_pr(
    lookup_by_branch_module: ModuleType,
    env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add(
        responses.GET,
        _lookup_pulls_url(),
        json=[],
        status=200,
    )
    rc = lookup_by_branch_module.main(["--branch", "ralph/never-existed", "--repo", REPO])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pr"] is None
    assert payload["branch_exists"] is None


@responses.activate
def test_lookup_by_branch_checks_branch_exists_when_flag_set(
    lookup_by_branch_module: ModuleType,
    env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add(
        responses.GET,
        _lookup_pulls_url(),
        json=[],
        status=200,
    )
    responses.add(
        responses.GET,
        _lookup_branch_url("ralph/PBI-Z"),
        status=200,
        json={"name": "ralph/PBI-Z"},
    )
    rc = lookup_by_branch_module.main(
        ["--branch", "ralph/PBI-Z", "--repo", REPO, "--include-branch-check"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pr"] is None
    assert payload["branch_exists"] is True


@responses.activate
def test_lookup_by_branch_reports_branch_missing(
    lookup_by_branch_module: ModuleType,
    env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add(
        responses.GET,
        _lookup_pulls_url(),
        json=[],
        status=200,
    )
    responses.add(
        responses.GET,
        _lookup_branch_url("ralph/PBI-DEAD"),
        status=404,
        json={"message": "Branch not found"},
    )
    rc = lookup_by_branch_module.main(
        ["--branch", "ralph/PBI-DEAD", "--repo", REPO, "--include-branch-check"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pr"] is None
    assert payload["branch_exists"] is False


def test_lookup_by_branch_missing_gh_token_exits_2(
    lookup_by_branch_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GH_OWNER", OWNER)
    rc = lookup_by_branch_module.main(["--branch", "ralph/x", "--repo", REPO])
    assert rc == 2
    assert "GH_TOKEN" in capsys.readouterr().err


def test_lookup_by_branch_missing_branch_arg_exits_2(
    lookup_by_branch_module: ModuleType,
    env: None,
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        lookup_by_branch_module.main(["--repo", REPO])
    assert excinfo.value.code == 2


@responses.activate
def test_lookup_by_branch_github_500_exits_3(
    lookup_by_branch_module: ModuleType,
    env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses.add(
        responses.GET,
        _lookup_pulls_url(),
        status=500,
        json={"message": "Internal server error"},
    )
    rc = lookup_by_branch_module.main(["--branch", "ralph/x", "--repo", REPO])
    assert rc == 3
    assert "github error" in capsys.readouterr().err


@responses.activate
def test_lookup_by_branch_unexpected_state_exits_3(
    lookup_by_branch_module: ModuleType,
    env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Regression: BugBot PR #31 found that _map_pr_state previously raised
    # FatalError on an unexpected state, but main() only caught HttpError,
    # so the script exited 1 with a traceback. reconcile.py then logged a
    # spurious ReconcileError instead of treating it as KEEP_API_ERROR.
    responses.add(
        responses.GET,
        _lookup_pulls_url(),
        json=[
            {
                "number": 99,
                "state": "weird",
                "merged_at": None,
                "html_url": "https://github.com/example-org/service-auth/pull/99",
            }
        ],
        status=200,
    )
    rc = lookup_by_branch_module.main(["--branch", "ralph/x", "--repo", REPO])
    assert rc == 3
    err = capsys.readouterr().err
    assert "github error" in err
    assert "unexpected GitHub PR state" in err
