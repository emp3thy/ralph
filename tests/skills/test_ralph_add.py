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


@pytest.fixture
def git_repo(tmp_path: Path) -> Iterator[Path]:
    """Build a local bare repo + a working clone with a ralph-queue branch.

    Origin's fetch URL is rewritten to an HTTPS placeholder after the
    initial push so ralph-add's ``_derive_target_repo`` has a parseable
    HTTPS URL. The bare remote is retained as ``pushurl`` so subsequent
    pushes still work against the local-filesystem bare repo.
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
    # Keep pushurl pointing at the bare repo; switch fetch URL to HTTPS
    # so ralph-add can auto-derive target_repo from origin.
    _git(work, "remote", "set-url", "--push", "origin", str(bare))
    _git(work, "remote", "set-url", "origin", "https://github.com/emp3thy/ralph.git")
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
    target_repo: str = "https://github.com/emp3thy/ralph",
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
        acceptance='- GET /healthz returns 200.\n- Body is JSON {"status":"ok"}.',
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


def test_attachment_with_path_traversal_name_is_stripped_to_basename(
    tmp_path: Path,
    git_repo: Path,
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression for BugBot finding on PR #25: a hostile / buggy
    fetcher emitting `\"name\": \"../../sensitive\"` must NOT escape the
    attachments/ directory. add.py strips directory components via
    Path(name).name before writing."""
    import base64

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

    exit_code = add_module.main(["--work-item", f"WI-{WORK_ITEM_ID}", "--repo", str(git_repo)])
    assert exit_code == 0
    capsys.readouterr()  # drain

    _git(git_repo, "checkout", "ralph-queue")
    pbi_dir = git_repo / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}"
    # The file is written under attachments/<basename> — NOT escaped up.
    assert (pbi_dir / "attachments" / "escape-attempt.bin").read_bytes() == b"escaped"
    # Critical: the parent directories did NOT get a file written by
    # the traversal — the working tree above attachments/ is clean.
    inbox_dir = git_repo / ".ralph" / "inbox"
    # The PBI dir is one level under inbox; nothing else should appear
    # adjacent to it.
    siblings = {p.name for p in inbox_dir.iterdir() if p.is_file()}
    assert "escape-attempt.bin" not in siblings


def test_work_item_id_with_path_traversal_is_rejected(
    tmp_path: Path,
    git_repo: Path,
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression for second BugBot finding on PR #25: a hostile / buggy
    fetcher emitting ``{\"id\": \"../../evil\"}`` must NOT escape the
    .ralph/inbox/ directory. Same defence applied to attachment names
    earlier in this PR — extended to the work-item id."""
    # Fetcher document with a traversing id.
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
        "target_repo": "https://github.com/emp3thy/ralph",
    }
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

    exit_code = add_module.main(["--work-item", f"WI-{WORK_ITEM_ID}", "--repo", str(git_repo)])
    assert exit_code == 0  # sanitizer reduces to plain basename
    capsys.readouterr()

    _git(git_repo, "checkout", "ralph-queue")
    # Basename "escape-attempt" lands under .ralph/inbox/, NOT in repo root.
    assert (git_repo / ".ralph" / "inbox" / "escape-attempt").is_dir()
    # No file/dir named "evil" or "escape-attempt" appears outside inbox.
    repo_root_names = {p.name for p in git_repo.iterdir()}
    assert "escape-attempt" not in repo_root_names
    assert "evil" not in repo_root_names


def test_work_item_id_with_newline_is_rejected_to_prevent_yaml_injection(
    tmp_path: Path,
    git_repo: Path,
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression for fourth BugBot finding on PR #25: Path(...).name
    does NOT strip newlines on POSIX. A fetcher emitting
    ``{\"id\": \"WI-1234\\nstatus: complete\"}`` would inject an extra
    YAML key into the PBI's frontmatter, flipping
    ``status: inbox`` to ``status: complete`` and causing the
    executor to skip the PBI as if it were already done.

    The sanitizer now enforces a strict ``[A-Za-z0-9._-]+`` whitelist
    on top of ``Path(...).name`` so any control character / shell
    metacharacter / whitespace causes a clean rejection."""
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
        "target_repo": "https://github.com/emp3thy/ralph",
    }
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

    exit_code = add_module.main(["--work-item", f"WI-{WORK_ITEM_ID}", "--repo", str(git_repo)])
    # Clean rejection (exit 2), not silent acceptance with a polluted frontmatter.
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "safe id alphabet" in err or "id=" in err
    # Critical: NO PBI directory was written under either the intended
    # WI-1234 OR the polluted "WI-1234\nstatus: complete" name.
    _git(git_repo, "checkout", "ralph-queue")
    inbox = git_repo / ".ralph" / "inbox"
    if inbox.exists():
        present = {p.name for p in inbox.iterdir()}
        for entry in present:
            assert "status:" not in entry, f"newline-injected name landed: {entry!r}"


def test_dry_run_with_expand_children_reports_zero_attachments(
    tmp_path: Path,
    git_repo: Path,
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression for BugBot LOW on PR #25: with --expand-children +
    --dry-run, the JSON report's attachments_downloaded must be 0
    (mirroring the non-expand dry-run path). Counting attachments that
    were never written would make the dry-run output lie."""
    import base64

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
        [
            "--work-item",
            f"WI-{WORK_ITEM_ID}",
            "--repo",
            str(git_repo),
            "--expand-children",
            "--dry-run",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["attachments_downloaded"] == 0


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
    fetcher = _write_mock_fetcher(
        tmp_path, documents_by_id={WORK_ITEM_ID: _doc(number=WORK_ITEM_ID)}
    )
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
    pbi_md = (git_repo / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}" / "PBI.md").read_text(
        encoding="utf-8"
    )
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
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={})
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
    bad_doc: dict[str, object] = {"id": "WI-9", "title": "missing the rest"}
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


def test_generated_pbi_passes_plan1_validator(
    tmp_path: Path,
    git_repo: Path,
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PBI directories produced by ``ralph-add`` must satisfy the canonical
    PBI schema enforced by ``scripts.validate_samples``.

    The validator derives PBI type from the frontmatter ``type`` field
    (reconciliation #3) rather than the directory name, so the directory
    ``WI-<n>`` produced by ``ralph-add`` is acceptable as-is. We mirror it
    into a typed-name sibling here only to keep the assertion robust if a
    future tightening reintroduces a prefix expectation.
    """
    import base64

    from scripts.validate_samples import validate_sample

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
    assert errors == [], "ralph-add-produced PBI fails Plan 1 validator:\n  - " + "\n  - ".join(
        errors
    )


# ----------------------------------------------------------------------
# target_repo tests (RALPH-PBI-TARGET-REPO-FIELD — Task 6)
# ----------------------------------------------------------------------


def _init_repo_with_origin(repo: Path, origin: str) -> None:
    """Initialise an empty git repo with a single origin remote."""
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "remote", "add", "origin", origin)


def test_derive_target_repo_from_https_origin(
    tmp_path: Path,
    add_module: ModuleType,
) -> None:
    """HTTPS origin (with .git suffix) -> HTTPS URL without .git suffix."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_origin(repo, "https://github.com/emp3thy/ralph.git")

    assert add_module._derive_target_repo(repo) == "https://github.com/emp3thy/ralph"


def test_derive_target_repo_from_ssh_origin(
    tmp_path: Path,
    add_module: ModuleType,
) -> None:
    """SSH origin git@host:owner/name(.git)? -> https://host/owner/name."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_origin(repo, "git@github.com:emp3thy/ralph.git")

    assert add_module._derive_target_repo(repo) == "https://github.com/emp3thy/ralph"


def test_derive_target_repo_raises_on_unparseable_origin(
    tmp_path: Path,
    add_module: ModuleType,
) -> None:
    """Local-file-path origin cannot map to an HTTPS URL; raise pointing
    at the --target-repo escape hatch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_origin(repo, "/some/local/path")

    with pytest.raises(add_module._FatalError, match="--target-repo"):
        add_module._derive_target_repo(repo)


def test_ralph_add_writes_derived_target_repo_to_new_pbi(
    tmp_path: Path,
    git_repo: Path,
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No --target-repo flag: origin is auto-derived into PBI frontmatter."""
    # Repoint origin to an HTTPS URL so auto-derive yields a real value.
    # Run with --no-push since the bare remote is no longer reachable.
    _git(git_repo, "remote", "set-url", "origin", "https://github.com/emp3thy/ralph.git")
    doc = _doc(number=WORK_ITEM_ID, title="auto-derive test")
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

    exit_code = add_module.main(
        [
            "--work-item",
            f"WI-{WORK_ITEM_ID}",
            "--repo",
            str(git_repo),
            "--no-push",
        ]
    )
    assert exit_code == 0, capsys.readouterr().err
    capsys.readouterr()

    _git(git_repo, "checkout", "ralph-queue")
    pbi_md = (git_repo / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}" / "PBI.md").read_text(
        encoding="utf-8"
    )
    assert "target_repo: https://github.com/emp3thy/ralph" in pbi_md


def test_ralph_add_flag_overrides_origin(
    tmp_path: Path,
    git_repo: Path,
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--target-repo wins over auto-derive."""
    _git(git_repo, "remote", "set-url", "origin", "https://github.com/A/B.git")
    doc = _doc(number=WORK_ITEM_ID, title="flag override")
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

    exit_code = add_module.main(
        [
            "--work-item",
            f"WI-{WORK_ITEM_ID}",
            "--repo",
            str(git_repo),
            "--no-push",
            "--target-repo",
            "https://github.com/C/D",
        ]
    )
    assert exit_code == 0, capsys.readouterr().err
    capsys.readouterr()

    _git(git_repo, "checkout", "ralph-queue")
    pbi_md = (git_repo / ".ralph" / "inbox" / f"WI-{WORK_ITEM_ID}" / "PBI.md").read_text(
        encoding="utf-8"
    )
    assert "target_repo: https://github.com/C/D" in pbi_md
    assert "target_repo: https://github.com/A/B" not in pbi_md


def test_ralph_add_rejects_invalid_target_repo_flag(
    tmp_path: Path,
    git_repo: Path,
    add_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """http:// (not https://) --target-repo exits 2 with HTTPS-mentioning stderr."""
    doc = _doc(number=WORK_ITEM_ID)
    fetcher = _write_mock_fetcher(tmp_path, documents_by_id={WORK_ITEM_ID: doc})
    monkeypatch.setenv("RALPH_WORKITEM_FETCH_SCRIPT", str(fetcher))

    exit_code = add_module.main(
        [
            "--work-item",
            f"WI-{WORK_ITEM_ID}",
            "--repo",
            str(git_repo),
            "--no-push",
            "--target-repo",
            "http://github.com/x/y",
        ]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "HTTPS" in err
