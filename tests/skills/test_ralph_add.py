"""Tests for the host-agnostic ``ralph-add`` orchestrator.

The orchestrator delegates the work-item fetch step to a sibling
``workitem-fetch/`` skill via ``subprocess.run``. These tests stub the
fetcher with a tiny Python script written into ``tmp_path`` and point
``RALPH_WORKITEM_FETCH_SCRIPT`` at it, so the orchestrator's behaviour
can be verified end-to-end without invoking GitHub or ADO.

Queue side: each test builds a bare git repo as the "queue remote" and
points ``--queue-repo`` at it. ``ralph-add`` clones it into ``--workspace``,
mutates ``.ralph/inbox/<id>/`` on ``main``, and pushes back.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
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

TARGET_REPO_URL = "https://github.com/emp3thy/svc-auth"


@pytest.fixture(scope="module")
def add_module() -> ModuleType:
    """Load ``skills/ralph-add/scripts/add.py`` as an importable module."""
    assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("ralph_add_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


@pytest.fixture
def queue_env(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """Build a bare queue remote + an empty workspace dir.

    The bare remote is seeded with an empty ``main`` so
    ``ensure_queue_clone`` can clone it cleanly. Returns
    ``(workspace_root, queue_repo_url)`` for the test to pass into
    ``ralph-add`` via ``--workspace`` / ``--queue-repo``.
    """
    bare = tmp_path / "queue.git"
    seed = tmp_path / "queue-seed"
    workspace = tmp_path / "ws"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-b", "main", str(seed)], check=True)
    _git(seed, "config", "user.email", "seed@example.com")
    _git(seed, "config", "user.name", "Seed")
    _git(seed, "commit", "--allow-empty", "-m", "chore: initial main")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-u", "origin", "main")
    yield workspace, str(bare)


def _verify_clone(tmp_path: Path, bare_url: str) -> Path:
    """Clone the bare queue remote into a fresh verify path to inspect pushes."""
    verify = tmp_path / f"verify-{Path(bare_url).name}"
    subprocess.run(
        ["git", "clone", bare_url, str(verify)],
        check=True,
        capture_output=True,
    )
    return verify


def _write_mock_fetcher(
    tmp_path: Path,
    *,
    documents_by_id: dict[int, dict[str, object]],
) -> Path:
    """Write a mock fetcher script that emits canned JSON for given ids."""
    script = tmp_path / "mock_fetch.py"
    map_path = tmp_path / "mock_docs.json"
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
    target_repo: str = TARGET_REPO_URL,
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
        "target_repo": target_repo,
    }


def _common_argv(
    *,
    work_item: str | int,
    workspace: Path,
    queue_repo: str,
    target_repo: str = TARGET_REPO_URL,
    extra: list[str] | None = None,
) -> list[str]:
    argv = [
        "--work-item",
        str(work_item),
        "--target-repo",
        target_repo,
        "--workspace",
        str(workspace),
        "--queue-repo",
        queue_repo,
    ]
    if extra:
        argv.extend(extra)
    return argv


def _configure_clone_identity(workspace: Path) -> None:
    """Set committer identity on the clone so commits succeed in CI envs
    where no global git user.email is configured."""
    clone = workspace / "queue"
    _git(clone, "config", "user.email", "ralph-add@example.com")
    _git(clone, "config", "user.name", "ralph-add")


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_feature_work_item_writes_feature_pbi(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import base64

    workspace, queue_repo = queue_env
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-png-bytes").decode("ascii")
    pdf = base64.b64encode(b"%PDF-1.4 fake-pdf-bytes").decode("ascii")
    doc = _doc(
        number=WORK_ITEM_ID,
        pbi_type="feature",
        title="Add /healthz endpoint to service-auth",
        body="Platform requires every service to expose a liveness probe at GET /healthz.",
        acceptance='- GET /healthz returns 200.\n- Body is JSON {"status":"ok"}.',
        attachments=[
            {"name": "design.png", "url": "https://x/design.png", "content_base64": png},
            {"name": "context.pdf", "url": "https://x/context.pdf", "content_base64": pdf},
        ],
    )
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

    # Pre-clone so we can install committer identity before ralph-add runs.
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)

    exit_code = add_module.main(
        _common_argv(work_item=f"WI-{WORK_ITEM_ID}", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == f"WI-{WORK_ITEM_ID}"
    assert payload["pbi_type"] == "feature"
    assert payload["target_repo"] == TARGET_REPO_URL
    assert payload["attachments_downloaded"] == 2
    assert payload["pushed"] is True
    assert payload["dry_run"] is False
    assert payload["source_host"] == "github"

    verify = _verify_clone(tmp_path, queue_repo)
    pbi_dir = verify / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}"
    assert pbi_dir.is_dir()
    pbi_md = (pbi_dir / "PBI.md").read_text(encoding="utf-8")
    assert pbi_md.startswith("---\n")
    assert "type: feature" in pbi_md
    assert f"target_repo: {TARGET_REPO_URL}" in pbi_md
    assert "/healthz" in pbi_md
    assert "GET /healthz returns 200" in pbi_md
    assert "design.png" in pbi_md
    assert (pbi_dir / "PLAN.md").is_file()
    assert (pbi_dir / "HISTORY.md").is_file()
    assert (pbi_dir / "attachments" / "design.png").read_bytes().startswith(b"\x89PNG")
    assert (pbi_dir / "attachments" / "context.pdf").read_bytes().startswith(b"%PDF")


def test_attachment_with_path_traversal_name_is_stripped_to_basename(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression for BugBot finding on PR #25: a hostile / buggy
    fetcher emitting ``\"name\": \"../../sensitive\"`` must NOT escape the
    attachments/ directory. add.py strips directory components via
    Path(name).name before writing."""
    import base64

    workspace, queue_repo = queue_env
    payload_bytes = base64.b64encode(b"escaped").decode("ascii")
    doc = _doc(
        number=WORK_ITEM_ID,
        pbi_type="feature",
        title="x",
        body="x",
        acceptance="- x.",
        attachments=[
            {
                "name": "../../escape-attempt.bin",
                "url": "https://x/escape.bin",
                "content_base64": payload_bytes,
            },
        ],
    )
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)

    exit_code = add_module.main(
        _common_argv(work_item=f"WI-{WORK_ITEM_ID}", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 0, capsys.readouterr().err
    capsys.readouterr()  # drain

    verify = _verify_clone(tmp_path, queue_repo)
    pbi_dir = verify / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}"
    assert (pbi_dir / "attachments" / "escape-attempt.bin").read_bytes() == b"escaped"
    inbox_dir = verify / ".ralph" / "inbox"
    siblings = {p.name for p in inbox_dir.iterdir() if p.is_file()}
    assert "escape-attempt.bin" not in siblings


def test_work_item_id_with_path_traversal_is_rejected(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression for second BugBot finding on PR #25: a hostile / buggy
    fetcher emitting ``{\"id\": \"../../evil\"}`` must NOT escape the
    .ralph/inbox/ directory."""
    workspace, queue_repo = queue_env
    doc: dict[str, object] = {
        "id": "../../escape-attempt",
        "type": "feature",
        "severity": "normal",
        "title": "x",
        "body_markdown": "x",
        "acceptance_markdown": "- x.",
        "repro_steps_markdown": "",
        "attachments": [],
        "child_ids": [],
        "parent_id": None,
        "source_url": "https://example.invalid/issues/x",
        "source_host": "github",
        "raw": {},
        "target_repo": TARGET_REPO_URL,
    }
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)

    exit_code = add_module.main(
        _common_argv(work_item=f"WI-{WORK_ITEM_ID}", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 0, capsys.readouterr().err  # sanitizer reduces to plain basename
    capsys.readouterr()

    verify = _verify_clone(tmp_path, queue_repo)
    assert (verify / ".ralph" / "inbox" / "escape-attempt").is_dir()
    repo_root_names = {p.name for p in verify.iterdir()}
    assert "escape-attempt" not in repo_root_names
    assert "evil" not in repo_root_names


def test_work_item_id_with_newline_is_rejected_to_prevent_yaml_injection(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression for fourth BugBot finding on PR #25: a newline-laced id
    must be cleanly rejected (exit 2), not silently injected as an extra
    YAML key flipping ``status: inbox`` to ``status: complete``."""
    workspace, queue_repo = queue_env
    doc: dict[str, object] = {
        "id": "WI-1234\nstatus: complete",
        "type": "feature",
        "severity": "normal",
        "title": "x",
        "body_markdown": "x",
        "acceptance_markdown": "- x.",
        "repro_steps_markdown": "",
        "attachments": [],
        "child_ids": [],
        "parent_id": None,
        "source_url": "https://example.invalid/issues/x",
        "source_host": "github",
        "raw": {},
        "target_repo": TARGET_REPO_URL,
    }
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)

    exit_code = add_module.main(
        _common_argv(work_item=f"WI-{WORK_ITEM_ID}", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "safe id alphabet" in err or "id=" in err
    verify = _verify_clone(tmp_path, queue_repo)
    inbox = verify / ".ralph" / "inbox"
    if inbox.exists():
        present = {p.name for p in inbox.iterdir()}
        for entry in present:
            assert "status:" not in entry, f"newline-injected name landed: {entry!r}"


def test_dry_run_with_expand_children_reports_zero_attachments(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression for BugBot LOW on PR #25: with --expand-children +
    --dry-run, the JSON report's attachments_downloaded must be 0."""
    import base64

    workspace, queue_repo = queue_env
    payload_bytes = base64.b64encode(b"would-be-written").decode("ascii")
    child_id = WORK_ITEM_ID + 1
    parent_doc = _doc(
        number=WORK_ITEM_ID,
        pbi_type="feature",
        title="parent epic",
        body="parent",
        acceptance="- parent.",
        child_ids=[f"WI-{child_id}"],
    )
    child_doc = _doc(
        number=child_id,
        pbi_type="feature",
        title="child",
        body="child",
        acceptance="- child.",
        attachments=[
            {"name": "child-att.bin", "url": "x", "content_base64": payload_bytes},
        ],
    )
    fetcher = _write_mock_fetcher(
        tmp_path,
        documents_by_id={WORK_ITEM_ID: parent_doc, child_id: child_doc},
    )
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

    exit_code = add_module.main(
        _common_argv(
            work_item=f"WI-{WORK_ITEM_ID}",
            workspace=workspace,
            queue_repo=queue_repo,
            extra=["--expand-children", "--dry-run"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["attachments_downloaded"] == 0


def test_bug_work_item_writes_bug_pbi(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
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
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)

    exit_code = add_module.main(
        _common_argv(work_item=str(WORK_ITEM_ID), workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_type"] == "bug"
    assert payload["pbi_id"] == f"WI-{WORK_ITEM_ID}"

    verify = _verify_clone(tmp_path, queue_repo)
    pbi_dir = verify / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}"
    bug_md = (pbi_dir / "BUG.md").read_text(encoding="utf-8")
    assert "type: bug" in bug_md
    assert "severity: critical" in bug_md
    reproduce = (pbi_dir / "REPRODUCE.md").read_text(encoding="utf-8")
    assert "kubectl get pods" in reproduce
    assert "CrashLoopBackOff" in reproduce


def test_expand_children_writes_one_pbi_per_child(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
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
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)

    exit_code = add_module.main(
        _common_argv(
            work_item=str(PARENT_ID),
            workspace=workspace,
            queue_repo=queue_repo,
            extra=["--expand-children"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["children_expanded"] == 2

    verify = _verify_clone(tmp_path, queue_repo)
    inbox = verify / ".ralph" / "inbox"
    child_a_pbi = inbox / f"WI-{CHILD_A_ID}" / "PBI.md"
    child_b_pbi = inbox / f"WI-{CHILD_B_ID}" / "PBI.md"
    assert child_a_pbi.is_file()
    assert child_b_pbi.is_file()
    assert f"parent_id: WI-{PARENT_ID}" in child_a_pbi.read_text(encoding="utf-8")
    assert f"parent_id: WI-{PARENT_ID}" in child_b_pbi.read_text(encoding="utf-8")
    assert not (inbox / f"WI-{PARENT_ID}").exists()


def test_via_mcp_raises_not_implemented(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
    exit_code = add_module.main(
        _common_argv(
            work_item=str(WORK_ITEM_ID),
            workspace=workspace,
            queue_repo=queue_repo,
            extra=["--via-mcp"],
        )
    )
    assert exit_code == 2
    assert "NotImplemented" in capsys.readouterr().err


def test_dry_run_makes_no_writes_or_pushes(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    doc = _doc(number=WORK_ITEM_ID)
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

    exit_code = add_module.main(
        _common_argv(
            work_item=f"WI-{WORK_ITEM_ID}",
            workspace=workspace,
            queue_repo=queue_repo,
            extra=["--dry-run"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["pushed"] is False
    assert payload["commit_sha"] == ""

    # Dry-run must NOT have cloned the queue or written anything under workspace.
    assert not (workspace / "queue").exists()
    # Bare remote is empty of any inbox changes.
    verify = _verify_clone(tmp_path, queue_repo)
    assert not (verify / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}").exists()


def test_severity_override(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    doc = _doc(number=WORK_ITEM_ID, severity="normal")
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)

    exit_code = add_module.main(
        _common_argv(
            work_item=str(WORK_ITEM_ID),
            workspace=workspace,
            queue_repo=queue_repo,
            extra=["--severity", "critical"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err

    verify = _verify_clone(tmp_path, queue_repo)
    pbi_md = (verify / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}" / "PBI.md").read_text(
        encoding="utf-8"
    )
    assert "severity: critical" in pbi_md


def test_no_push_commits_but_does_not_push(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    doc = _doc(number=WORK_ITEM_ID)
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)

    remote_sha_before = subprocess.run(
        ["git", "ls-remote", queue_repo, "main"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()

    exit_code = add_module.main(
        _common_argv(
            work_item=str(WORK_ITEM_ID),
            workspace=workspace,
            queue_repo=queue_repo,
            extra=["--no-push"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pushed"] is False
    assert payload["commit_sha"] != ""

    remote_sha_after = subprocess.run(
        ["git", "ls-remote", queue_repo, "main"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    assert remote_sha_before == remote_sha_after


def test_fetcher_failure_propagates(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If the fetcher exits non-zero, ``ralph-add`` surfaces the error."""
    workspace, queue_repo = queue_env
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)
    exit_code = add_module.main(
        _common_argv(work_item=str(WORK_ITEM_ID), workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "workitem-fetch" in err or "fetcher" in err


def test_fetcher_invalid_json_rejected(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fetcher emitting malformed JSON must trigger a clean failure."""
    workspace, queue_repo = queue_env
    script = tmp_path / "broken_fetcher.py"
    script.write_text(
        "import sys\nprint('this is not json')\nsys.exit(0)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(script))
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)
    exit_code = add_module.main(
        _common_argv(work_item=str(WORK_ITEM_ID), workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "json" in err.lower() or "invalid" in err.lower()


def test_fetcher_invalid_schema_rejected(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fetcher emitting JSON missing required keys must trigger a clean failure."""
    workspace, queue_repo = queue_env
    bad_doc: dict[str, object] = {"id": "WI-9", "title": "missing the rest"}
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: bad_doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)
    exit_code = add_module.main(
        _common_argv(work_item=str(WORK_ITEM_ID), workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 2
    err = capsys.readouterr().err.lower()
    assert "schema" in err or "missing" in err or "invalid" in err


def test_generated_pbi_passes_plan1_validator(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PBI directories produced by ``ralph-add`` must satisfy the canonical
    PBI schema enforced by ``scripts.validate_samples``."""
    import base64

    from scripts.validate_samples import validate_sample

    workspace, queue_repo = queue_env
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")
    doc = _doc(
        number=WORK_ITEM_ID,
        title="Add /healthz endpoint to service-auth",
        body="Platform requires every service to expose a liveness probe at GET /healthz.",
        acceptance="- GET /healthz returns 200.",
        attachments=[{"name": "design.png", "url": "https://x/design.png", "content_base64": png}],
    )
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)

    exit_code = add_module.main(
        _common_argv(work_item=f"WI-{WORK_ITEM_ID}", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 0, capsys.readouterr().err

    pbi_dir = workspace / "queue" / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}"

    # Mirror into a feature-prefixed sibling for the validator's check.
    mirror = tmp_path / "validator-mirror" / f"feature-WI-{WORK_ITEM_ID}"
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
    assert errors == [], "ralph-add-produced PBI fails Plan 1 validator:\n  - " + "\n  - ".join(
        errors
    )


# ----------------------------------------------------------------------
# target_repo / config-resolution tests
# ----------------------------------------------------------------------


def test_target_repo_required(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Argparse fails (SystemExit) when --target-repo is omitted."""
    workspace, queue_repo = queue_env
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
    with pytest.raises(SystemExit):
        add_module.main(
            [
                "--work-item",
                f"WI-{WORK_ITEM_ID}",
                "--workspace",
                str(workspace),
                "--queue-repo",
                queue_repo,
            ]
        )


def test_ralph_add_writes_target_repo_to_pbi(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The --target-repo flag value lands verbatim in PBI frontmatter."""
    workspace, queue_repo = queue_env
    doc = _doc(number=WORK_ITEM_ID, title="target-repo test")
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)

    exit_code = add_module.main(
        _common_argv(
            work_item=f"WI-{WORK_ITEM_ID}",
            workspace=workspace,
            queue_repo=queue_repo,
            target_repo="https://github.com/emp3thy/svc-billing",
            extra=["--no-push"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err

    pbi_md = (workspace / "queue" / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}" / "PBI.md").read_text(
        encoding="utf-8"
    )
    assert "target_repo: https://github.com/emp3thy/svc-billing" in pbi_md


def test_ralph_add_rejects_invalid_target_repo_flag(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """http:// (not https://) --target-repo exits 2 with HTTPS-mentioning stderr."""
    workspace, queue_repo = queue_env
    doc = _doc(number=WORK_ITEM_ID)
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

    exit_code = add_module.main(
        _common_argv(
            work_item=f"WI-{WORK_ITEM_ID}",
            workspace=workspace,
            queue_repo=queue_repo,
            target_repo="http://github.com/x/y",
            extra=["--no-push"],
        )
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "HTTPS" in err


def test_ralph_add_queue_repo_resolved_from_toml(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When --queue-repo is omitted, the TOML value is used."""
    workspace, queue_repo = queue_env
    # Point ``~`` at a tmp HOME so the TOML write doesn't smear real config.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    (fake_home / ".ralph").mkdir()
    # TOML basic strings interpret backslash as an escape (``\U`` is a
    # parser error). Use a file:// URL whose path is forward-slash-only
    # so the value survives round-tripping on Windows.
    queue_url = "file:///" + Path(queue_repo).as_posix().lstrip("/")
    (fake_home / ".ralph" / "config.toml").write_text(
        f'queue_repo = "{queue_url}"\n',
        encoding="utf-8",
    )

    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_clone_identity(workspace)

    doc = _doc(number=WORK_ITEM_ID, title="toml-resolved queue")
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

    exit_code = add_module.main(
        [
            "--work-item",
            f"WI-{WORK_ITEM_ID}",
            "--target-repo",
            TARGET_REPO_URL,
            "--workspace",
            str(workspace),
            "--no-push",
        ]
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == f"WI-{WORK_ITEM_ID}"


def test_ralph_add_errors_when_queue_repo_unset(
    tmp_path: Path,
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without --queue-repo and without TOML queue_repo, exit 2 with a clear error."""
    workspace = tmp_path / "ws"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

    exit_code = add_module.main(
        [
            "--work-item",
            f"WI-{WORK_ITEM_ID}",
            "--target-repo",
            TARGET_REPO_URL,
            "--workspace",
            str(workspace),
        ]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "queue_repo" in err.lower()
