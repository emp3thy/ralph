"""Tests for the ``ralph-status`` skill's entry script.

All git operations are local (bare repo + worktree clones in
``tmp_path``). No network, no real ADO, no real remote.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-status" / "scripts" / "status.py"

WI_FEATURE = "WI-1234"
WI_BUG = "BUG-rosa-irsa"
WI_DONE = "WI-2000"
WI_BLOCKED = "WI-3000"
WI_CURRENT = "WI-1235"

FEATURE_PBI_MD = textwrap.dedent("""\
    ---
    id: WI-1234
    type: feature
    status: inbox
    severity: normal
    attempts: 0
    created_at: 2026-05-24T09:15:00+00:00
    updated_at: 2026-05-24T09:15:00+00:00
    ---

    # Add /healthz endpoint to service-auth

    Body.
    """)

BUG_PBI_MD = textwrap.dedent("""\
    ---
    id: BUG-rosa-irsa
    type: bug
    status: inbox
    severity: critical
    attempts: 0
    created_at: 2026-05-24T08:00:00+00:00
    updated_at: 2026-05-24T08:00:00+00:00
    ---

    # Pod crashloops on ROSA via IRSA

    Body.
    """)

CURRENT_PBI_MD = textwrap.dedent("""\
    ---
    id: WI-1235
    type: feature
    status: current
    severity: high
    attempts: 1
    created_at: 2026-05-23T12:00:00+00:00
    updated_at: 2026-05-24T08:00:00+00:00
    ---

    # Onboarding flow refactor

    Body.
    """)

DONE_PBI_MD = textwrap.dedent("""\
    ---
    id: WI-2000
    type: feature
    status: done
    severity: low
    attempts: 1
    created_at: 2026-05-20T09:15:00+00:00
    updated_at: 2026-05-22T09:15:00+00:00
    ---

    # Old completed work

    Body.
    """)

BLOCKED_PBI_MD = textwrap.dedent("""\
    ---
    id: WI-3000
    type: feature
    status: blocked
    severity: normal
    attempts: 3
    created_at: 2026-05-22T09:15:00+00:00
    updated_at: 2026-05-23T09:15:00+00:00
    ---

    # Stuck PBI

    Body.
    """)

MALFORMED_PBI_MD = "this file has no frontmatter at all\n"


@pytest.fixture(scope="module")
def show_module() -> ModuleType:
    """Load ``skills/ralph-status/scripts/status.py`` as an importable module."""
    assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("ralph_status_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec_module so dataclasses can resolve forward-ref
    # annotations (PEP 563) by looking up the module in sys.modules.
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
    )
    return result.stdout


def _write_pbi(work: Path, state: str, pbi_id: str, entry_file: str, content: str) -> None:
    pbi_dir = work / ".ralph" / state / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=True)
    (pbi_dir / entry_file).write_text(content, encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")


def _init_repo_with_pbis(tmp_path: Path, name: str) -> Path:
    """Return a path to a working clone whose ``ralph-queue`` branch holds PBIs.

    The bare repo is the "remote" — the skill creates a worktree against
    the working clone passed as ``--repo``.
    """
    bare = tmp_path / f"{name}.git"
    work = tmp_path / f"{name}-work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", str(work)], check=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test User")
    _git(work, "commit", "--allow-empty", "-m", "chore: initial main")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")
    _git(work, "checkout", "-b", "ralph-queue")

    _write_pbi(work, "inbox", WI_FEATURE, "PBI.md", FEATURE_PBI_MD)
    _write_pbi(work, "inbox", WI_BUG, "BUG.md", BUG_PBI_MD)
    _write_pbi(work, "current", WI_CURRENT, "PBI.md", CURRENT_PBI_MD)
    _write_pbi(work, "done", WI_DONE, "PBI.md", DONE_PBI_MD)
    _write_pbi(work, "blocked", WI_BLOCKED, "PBI.md", BLOCKED_PBI_MD)

    _git(work, "add", ".ralph")
    _git(work, "commit", "-m", "feat(queue): seed PBIs for tests")
    _git(work, "push", "-u", "origin", "ralph-queue")
    _git(work, "checkout", "main")
    return work


@pytest.fixture
def git_repo_with_pbis(tmp_path: Path) -> Path:
    return _init_repo_with_pbis(tmp_path, "service-auth")


@pytest.fixture
def empty_repo(tmp_path: Path) -> Path:
    """Bare + working repo with a ralph-queue branch but no PBIs."""
    bare = tmp_path / "empty.git"
    work = tmp_path / "empty-work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", str(work)], check=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test User")
    _git(work, "commit", "--allow-empty", "-m", "chore: initial main")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")
    _git(work, "checkout", "-b", "ralph-queue")
    _git(work, "commit", "--allow-empty", "-m", "chore(queue): bootstrap")
    _git(work, "push", "-u", "origin", "ralph-queue")
    _git(work, "checkout", "main")
    return work


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_single_repo_renders_all_states(
    git_repo_with_pbis: Path,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = show_module.main(["--repo", str(git_repo_with_pbis)])
    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "REPO" in stdout
    assert "STATE" in stdout
    assert "ID" in stdout
    for pbi_id in (WI_FEATURE, WI_BUG, WI_CURRENT, WI_DONE, WI_BLOCKED):
        assert pbi_id in stdout, f"missing {pbi_id} in:\n{stdout}"
    assert "inbox" in stdout
    assert "current" in stdout
    assert "done" in stdout
    assert "blocked" in stdout


def test_state_filter_narrows_output(
    git_repo_with_pbis: Path,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = show_module.main(["--repo", str(git_repo_with_pbis), "--state", "current"])
    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert WI_CURRENT in stdout
    for pbi_id in (WI_FEATURE, WI_BUG, WI_DONE, WI_BLOCKED):
        assert pbi_id not in stdout, f"unexpected {pbi_id} in filtered output:\n{stdout}"


def test_json_output_is_valid(
    git_repo_with_pbis: Path,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = show_module.main(["--repo", str(git_repo_with_pbis), "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "rows" in payload
    assert "errors" in payload
    assert "repos" in payload
    assert isinstance(payload["rows"], list)
    assert len(payload["rows"]) == 5
    ids = sorted(row["id"] for row in payload["rows"])
    assert WI_FEATURE in ids
    assert WI_BUG in ids
    sample = payload["rows"][0]
    for key in (
        "repo",
        "repo_name",
        "state",
        "id",
        "type",
        "severity",
        "title",
        "attempts",
        "pbi_dir",
        "error",
    ):
        assert key in sample, f"row missing key {key!r}: {sample}"


def test_empty_queue_renders_header_only(
    empty_repo: Path,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = show_module.main(["--repo", str(empty_repo)])
    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "REPO" in stdout
    assert "WI-" not in stdout
    assert "BUG-" not in stdout


def test_repos_file_aggregates_across_repos(
    tmp_path: Path,
    git_repo_with_pbis: Path,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    second = _init_repo_with_pbis(tmp_path, "service-billing")
    cfg = tmp_path / "repos.txt"
    cfg.write_text(
        f"# A comment\n{git_repo_with_pbis}\n{second}\n",
        encoding="utf-8",
    )
    exit_code = show_module.main(["--repos-file", str(cfg), "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["repos"]) == 2
    repo_names = {repo["name"] for repo in payload["repos"]}
    assert repo_names == {"service-auth-work", "service-billing-work"}
    assert len(payload["rows"]) == 10


def test_malformed_pbi_becomes_question_row(
    tmp_path: Path,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bare = tmp_path / "svc.git"
    work = tmp_path / "svc-work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", str(work)], check=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test User")
    _git(work, "commit", "--allow-empty", "-m", "chore: initial main")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")
    _git(work, "checkout", "-b", "ralph-queue")
    _write_pbi(work, "inbox", WI_FEATURE, "PBI.md", FEATURE_PBI_MD)
    _write_pbi(work, "inbox", "WI-broken", "PBI.md", MALFORMED_PBI_MD)
    _git(work, "add", ".ralph")
    _git(work, "commit", "-m", "feat(queue): seed mixed PBIs")
    _git(work, "push", "-u", "origin", "ralph-queue")
    _git(work, "checkout", "main")

    exit_code = show_module.main(["--repo", str(work), "--json"])
    assert exit_code == 0, "malformed PBIs must not abort the whole command"
    payload = json.loads(capsys.readouterr().out)
    ids = {row["id"] for row in payload["rows"]}
    assert WI_FEATURE in ids
    assert "WI-broken" in ids
    broken = next(row for row in payload["rows"] if row["id"] == "WI-broken")
    assert broken["error"] is not None
    assert broken["type"] == "?" or broken["type"] is None


def test_repo_path_must_exist(
    tmp_path: Path,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "does-not-exist"
    exit_code = show_module.main(["--repo", str(missing)])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "does not exist" in err or "not a git" in err


def test_branch_missing_exits_nonzero(
    tmp_path: Path,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bare = tmp_path / "no-queue.git"
    work = tmp_path / "no-queue-work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", str(work)], check=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test User")
    _git(work, "commit", "--allow-empty", "-m", "chore: initial main")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")

    exit_code = show_module.main(["--repo", str(work)])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "ralph-queue" in err


def test_no_cleanup_keeps_worktree(
    git_repo_with_pbis: Path,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = show_module.main(["--repo", str(git_repo_with_pbis), "--json", "--no-cleanup"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["repos"]) == 1
    worktree_path = Path(payload["repos"][0]["worktree_path"])
    assert worktree_path.is_dir(), "--no-cleanup must leave the worktree on disk"
    assert (worktree_path / ".ralph").is_dir()


def test_state_filter_rejects_unknown_state(
    git_repo_with_pbis: Path,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        show_module.main(["--repo", str(git_repo_with_pbis), "--state", "limbo"])
    assert excinfo.value.code == 2


def test_status_recognises_plan1_samples(
    tmp_path: Path,
    show_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The reader must parse the canonical Plan 1 sample PBIs cleanly.

    We copy ``samples/feature-WI-1234/`` etc. into ``.ralph/inbox/<id>/``
    on a fresh ralph-queue branch and run the skill against the repo.
    All three samples must show up as successful PBIRow entries (not
    PBIRowError) in the JSON output.
    """
    samples_dir = REPO_ROOT / "samples"

    bare = tmp_path / "svc.git"
    work = tmp_path / "svc-work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", str(work)], check=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test User")
    _git(work, "commit", "--allow-empty", "-m", "chore: initial main")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")
    _git(work, "checkout", "-b", "ralph-queue")

    sample_to_queue_id = {
        "feature-WI-1234": "WI-1234",
        "bug-deploy-rosa-irsa-2026-05-23": "BUG-rosa-irsa-2026-05-23",
        "pr-feedback-WI-1234-r2": "PR-feedback-WI-1234-r2",
    }
    for sample_name, queue_id in sample_to_queue_id.items():
        src = samples_dir / sample_name
        assert src.is_dir(), f"missing sample: {src}"
        dest = work / ".ralph" / "inbox" / queue_id
        dest.mkdir(parents=True)
        for child in src.iterdir():
            if child.is_file():
                (dest / child.name).write_bytes(child.read_bytes())

    _git(work, "add", ".ralph")
    _git(work, "commit", "-m", "feat(queue): seed from Plan 1 samples")
    _git(work, "push", "-u", "origin", "ralph-queue")
    _git(work, "checkout", "main")

    exit_code = show_module.main(["--repo", str(work), "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)

    assert len(payload["rows"]) == 3
    assert all(row["error"] is None for row in payload["rows"]), (
        "Plan 1 samples must parse cleanly:\n" + json.dumps(payload["rows"], indent=2)
    )
    types = sorted(row["type"] for row in payload["rows"])
    assert types == ["bug", "feature", "pr-feedback"]
