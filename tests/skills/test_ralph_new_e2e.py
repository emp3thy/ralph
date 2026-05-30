"""End-to-end tests for the ralph-new CLI.

Uses the project-wide ``queue_env`` fixture from ``tests/conftest.py`` — a
bare queue remote on the ``ralph-queue`` branch + a tmp workspace.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "skills" / "ralph-new" / "scripts" / "new.py"

TARGET = "https://github.com/owner/svc"


@pytest.fixture(scope="module")
def new_mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ralph_new", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _verify_clone(tmp_path: Path, bare_url: str) -> Path:
    verify = tmp_path / f"verify-{Path(bare_url).name}"
    subprocess.run(["git", "clone", "-b", "ralph-queue", bare_url, str(verify)], check=True)
    return verify


def _configure_clone_identity(clone: Path) -> None:
    subprocess.run(["git", "-C", str(clone), "config", "user.email", "t@e"], check=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.name", "t"], check=True)


def _common_argv(workspace: Path, queue_repo: str) -> list[str]:
    return [
        "--workspace",
        str(workspace),
        "--queue-repo",
        queue_repo,
        "--queue-branch",
        "ralph-queue",
        "--non-interactive",
    ]


def _seed_existing_pbi(tmp_path: Path, queue_repo: str, state: str, pbi_id: str) -> None:
    """Push an empty PBI dir under .ralph/<state>/<pbi_id>/ to the bare queue."""
    seed = tmp_path / f"seed-existing-{state}-{pbi_id}"
    subprocess.run(["git", "clone", "-b", "ralph-queue", queue_repo, str(seed)], check=True)
    _configure_clone_identity(seed)
    pbi = seed / ".ralph" / state / pbi_id
    pbi.mkdir(parents=True, exist_ok=True)
    (pbi / "HISTORY.md").write_text("<!-- seed -->\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "-m", f"seed: existing {pbi_id}"],
        check=True,
    )
    subprocess.run(["git", "-C", str(seed), "push", "origin", "ralph-queue"], check=True)


def _write_inline(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _set_git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@e")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@e")


# --- full runs --------------------------------------------------------


def test_full_run_bug_writes_three_files_and_pushes(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _set_git_identity(monkeypatch)

    argv = _common_argv(workspace, queue_repo) + [
        "--title",
        "Fix login timeout on Safari",
        "--type",
        "bug",
        "--severity",
        "high",
        "--target-repo",
        TARGET,
        "--body-file",
        str(_write_inline(tmp_path, "bug-body.md", "## Symptom\nhangs\n")),
        "--reproduce-file",
        str(_write_inline(tmp_path, "repro.md", "## Steps\n1. ...\n")),
    ]
    rc = new_mod.main(argv)
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "FIX-LOGIN-TIMEOUT-ON-SAFARI"
    assert payload["type"] == "bug"

    verify = _verify_clone(tmp_path, queue_repo)
    pbi_dir = verify / ".ralph" / "inbox" / "FIX-LOGIN-TIMEOUT-ON-SAFARI"
    assert pbi_dir.is_dir()
    assert sorted(p.name for p in pbi_dir.iterdir()) == [
        "BUG.md",
        "HISTORY.md",
        "REPRODUCE.md",
    ]

    log = subprocess.run(
        ["git", "-C", str(verify), "log", "-1", "--format=%s"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()
    assert log == "chore(queue): add FIX-LOGIN-TIMEOUT-ON-SAFARI"


def test_full_run_feature_writes_three_files_with_plan_pointer(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, queue_repo = queue_env
    _set_git_identity(monkeypatch)
    plan_file = _write_inline(tmp_path, "csv-plan.md", "# CSV plan body\n")

    argv = _common_argv(workspace, queue_repo) + [
        "--title",
        "Export CSV report",
        "--type",
        "feature",
        "--target-repo",
        TARGET,
        "--body-file",
        str(_write_inline(tmp_path, "feat.md", "We need CSV export.")),
        "--spec-path",
        "docs/superpowers/specs/2026-05-30-csv-design.md",
        "--plan-path",
        str(plan_file),
    ]
    rc = new_mod.main(argv)
    assert rc == 0

    verify = _verify_clone(tmp_path, queue_repo)
    pbi_dir = verify / ".ralph" / "inbox" / "EXPORT-CSV-REPORT"
    assert sorted(p.name for p in pbi_dir.iterdir()) == [
        "HISTORY.md",
        "PBI.md",
        "PLAN.md",
    ]
    plan_text = (pbi_dir / "PLAN.md").read_text(encoding="utf-8")
    assert "csv-plan.md" in plan_text
    assert "CSV plan body" in plan_text


def test_full_run_feature_blank_plan_writes_todo_stub(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, queue_repo = queue_env
    _set_git_identity(monkeypatch)

    argv = _common_argv(workspace, queue_repo) + [
        "--title",
        "Stub plan feature",
        "--type",
        "feature",
        "--target-repo",
        TARGET,
        "--body-file",
        str(_write_inline(tmp_path, "feat.md", "x")),
    ]
    rc = new_mod.main(argv)
    assert rc == 0
    verify = _verify_clone(tmp_path, queue_repo)
    text = (verify / ".ralph" / "inbox" / "STUB-PLAN-FEATURE" / "PLAN.md").read_text(
        encoding="utf-8"
    )
    assert "TODO" in text


# --- depends_on -------------------------------------------------------


def test_depends_on_existence_check_passes(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, queue_repo = queue_env
    _seed_existing_pbi(tmp_path, queue_repo, "done", "PRECURSOR")
    _set_git_identity(monkeypatch)

    rc = new_mod.main(
        _common_argv(workspace, queue_repo)
        + [
            "--title",
            "Dep ok",
            "--type",
            "bug",
            "--target-repo",
            TARGET,
            "--depends-on",
            "PRECURSOR",
            "--body-file",
            str(_write_inline(tmp_path, "b.md", "x")),
            "--reproduce-file",
            str(_write_inline(tmp_path, "r.md", "x")),
        ]
    )
    assert rc == 0


def test_depends_on_existence_check_fails_exit_2(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _set_git_identity(monkeypatch)

    rc = new_mod.main(
        _common_argv(workspace, queue_repo)
        + [
            "--title",
            "Dep missing",
            "--type",
            "bug",
            "--target-repo",
            TARGET,
            "--depends-on",
            "DOES-NOT-EXIST",
            "--body-file",
            str(_write_inline(tmp_path, "b.md", "x")),
            "--reproduce-file",
            str(_write_inline(tmp_path, "r.md", "x")),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "DOES-NOT-EXIST" in err
    assert "not found" in err.lower()


@pytest.mark.parametrize("state", ["inbox", "current", "pending-pr", "blocked", "done", "archive"])
def test_depends_on_existence_check_searches_all_states(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    workspace, queue_repo = queue_env
    _seed_existing_pbi(tmp_path, queue_repo, state, f"PRE-{state.upper()}")
    _set_git_identity(monkeypatch)
    rc = new_mod.main(
        _common_argv(workspace, queue_repo)
        + [
            "--title",
            f"X for {state}",
            "--type",
            "bug",
            "--target-repo",
            TARGET,
            "--depends-on",
            f"PRE-{state.upper()}",
            "--body-file",
            str(_write_inline(tmp_path, f"b-{state}.md", "x")),
            "--reproduce-file",
            str(_write_inline(tmp_path, f"r-{state}.md", "x")),
        ]
    )
    assert rc == 0


# --- collision --------------------------------------------------------


def test_slug_collision_appends_suffix_end_to_end(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_existing_pbi(tmp_path, queue_repo, "inbox", "COLLIDE-ME")
    _set_git_identity(monkeypatch)

    rc = new_mod.main(
        _common_argv(workspace, queue_repo)
        + [
            "--title",
            "Collide me",
            "--type",
            "bug",
            "--target-repo",
            TARGET,
            "--body-file",
            str(_write_inline(tmp_path, "b.md", "x")),
            "--reproduce-file",
            str(_write_inline(tmp_path, "r.md", "x")),
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "COLLIDE-ME-2"


# --- dry-run, no-push, errors -----------------------------------------


def test_dry_run_no_clone_no_commit(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    rc = new_mod.main(
        _common_argv(workspace, queue_repo)
        + [
            "--title",
            "Dry run feature",
            "--type",
            "feature",
            "--target-repo",
            TARGET,
            "--body-file",
            str(_write_inline(tmp_path, "f.md", "x")),
            "--dry-run",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert not (workspace / "queue").exists()


def test_non_interactive_missing_title_exits_2(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    rc = new_mod.main(
        [
            "--workspace",
            str(workspace),
            "--queue-repo",
            queue_repo,
            "--non-interactive",
            "--type",
            "bug",
            "--target-repo",
            TARGET,
        ]
    )
    assert rc == 2
    assert "title" in capsys.readouterr().err.lower()


def test_target_repo_invalid_url_exits_2(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    rc = new_mod.main(
        _common_argv(workspace, queue_repo)
        + [
            "--title",
            "Bad url",
            "--type",
            "bug",
            "--target-repo",
            "not-a-url",
            "--body-file",
            str(_write_inline(tmp_path, "b.md", "x")),
            "--reproduce-file",
            str(_write_inline(tmp_path, "r.md", "x")),
        ]
    )
    assert rc == 2
    assert "target_repo" in capsys.readouterr().err.lower()


def test_no_push_commits_locally_but_does_not_push(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, queue_repo = queue_env
    _set_git_identity(monkeypatch)

    rc = new_mod.main(
        _common_argv(workspace, queue_repo)
        + [
            "--title",
            "Local only",
            "--type",
            "bug",
            "--target-repo",
            TARGET,
            "--body-file",
            str(_write_inline(tmp_path, "b.md", "x")),
            "--reproduce-file",
            str(_write_inline(tmp_path, "r.md", "x")),
            "--no-push",
        ]
    )
    assert rc == 0

    verify = _verify_clone(tmp_path, queue_repo)
    assert not (verify / ".ralph" / "inbox" / "LOCAL-ONLY").exists()
    local = workspace / "queue"
    assert (local / ".ralph" / "inbox" / "LOCAL-ONLY").exists()


def test_parent_id_threaded_into_frontmatter(
    new_mod: ModuleType,
    queue_env: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, queue_repo = queue_env
    _set_git_identity(monkeypatch)
    rc = new_mod.main(
        _common_argv(workspace, queue_repo)
        + [
            "--title",
            "Child PBI",
            "--type",
            "bug",
            "--target-repo",
            TARGET,
            "--parent-id",
            "EPIC-PARENT",
            "--body-file",
            str(_write_inline(tmp_path, "b.md", "x")),
            "--reproduce-file",
            str(_write_inline(tmp_path, "r.md", "x")),
        ]
    )
    assert rc == 0
    verify = _verify_clone(tmp_path, queue_repo)
    text = (verify / ".ralph" / "inbox" / "CHILD-PBI" / "BUG.md").read_text(encoding="utf-8")
    assert "parent_id: EPIC-PARENT" in text
