"""End-to-end-ish smoke test threading three supervisor skills together.

The test seeds a PBI in ``.ralph/blocked/``, invokes ``ralph-promote``
to escalate its severity (which leaves it in blocked/), then invokes
``ralph-triage --to inbox`` to return it for retry. The resulting
``ralph-queue`` state is asserted in one place to confirm the skills
agree on the on-disk schema produced by the shared queue-writer.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(script_relpath: str, name: str) -> ModuleType:
    path = REPO_ROOT / script_relpath
    assert path.is_file(), f"missing script {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def promote_module() -> ModuleType:
    return _load(
        "skills/ralph-promote/scripts/promote.py",
        "ralph_promote_smoke",
    )


@pytest.fixture(scope="module")
def triage_module() -> ModuleType:
    return _load(
        "skills/ralph-triage/scripts/triage.py",
        "ralph_triage_smoke",
    )


@pytest.fixture(scope="module")
def cancel_module() -> ModuleType:
    return _load(
        "skills/ralph-cancel/scripts/cancel.py",
        "ralph_cancel_smoke",
    )


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
def git_repo(tmp_path: Path) -> Iterator[Path]:
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
    _git(work, "commit", "--allow-empty", "-m", "chore(queue): bootstrap")
    _git(work, "push", "-u", "origin", "ralph-queue")
    _git(work, "checkout", "main")
    yield work


def _seed_blocked(repo: Path, pbi_id: str) -> Path:
    _git(repo, "checkout", "ralph-queue")
    pbi_dir = repo / ".ralph" / "blocked" / pbi_id
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        f"id: {pbi_id}\n"
        "type: feature\n"
        "status: blocked\n"
        "severity: normal\n"
        "attempts: 3\n"
        'created_at: "2026-05-18T08:00:00+00:00"\n'
        'updated_at: "2026-05-22T09:30:00+00:00"\n'
        "---\n"
        "\n"
        "# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _git(repo, "add", f".ralph/blocked/{pbi_id}")
    _git(repo, "commit", "-m", "chore(test): seed")
    _git(repo, "push", "origin", "ralph-queue")
    _git(repo, "checkout", "main")
    return pbi_dir


def _read_frontmatter(entry_file: Path) -> dict[str, object]:
    text = entry_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "---"
    end = next(i for i, line in enumerate(lines[1:], start=1) if line == "---")
    result = yaml.safe_load("\n".join(lines[1:end]))
    assert isinstance(result, dict)
    return result


def test_promote_then_triage_round_trip(
    git_repo: Path,
    promote_module: ModuleType,
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_blocked(git_repo, "WI-9000")

    assert (
        promote_module.main(
            [
                "--pbi-id",
                "WI-9000",
                "--severity",
                "high",
                "--repo",
                str(git_repo),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        triage_module.main(
            [
                "--pbi-id",
                "WI-9000",
                "--to",
                "inbox",
                "--note",
                "reset after severity bump",
                "--repo",
                str(git_repo),
            ]
        )
        == 0
    )
    capsys.readouterr()

    _git(git_repo, "checkout", "ralph-queue")
    assert not (git_repo / ".ralph" / "blocked" / "WI-9000").exists()
    new_pbi = git_repo / ".ralph" / "inbox" / "WI-9000"
    assert new_pbi.is_dir()
    fm = _read_frontmatter(new_pbi / "PBI.md")
    assert fm["severity"] == "high"
    assert fm["status"] == "inbox"
    assert fm["attempts"] == 0

    history = (new_pbi / "HISTORY.md").read_text(encoding="utf-8")
    assert "ralph-promote" in history
    assert "ralph-triage" in history
    assert "normal -> high" in history
    assert "reset after severity bump" in history


def test_cancel_then_promote_is_independent(
    git_repo: Path,
    cancel_module: ModuleType,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _git(git_repo, "checkout", "ralph-queue")
    pbi_dir = git_repo / ".ralph" / "current" / "WI-9100"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        "id: WI-9100\n"
        "type: feature\n"
        "status: current\n"
        "severity: normal\n"
        "attempts: 1\n"
        'created_at: "2026-05-24T10:00:00+00:00"\n'
        'updated_at: "2026-05-24T10:00:00+00:00"\n'
        "---\n"
        "\n"
        "# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _git(git_repo, "add", ".ralph/current/WI-9100")
    _git(git_repo, "commit", "-m", "chore(test): seed current WI-9100")
    _git(git_repo, "push", "origin", "ralph-queue")
    _git(git_repo, "checkout", "main")

    assert (
        cancel_module.main(
            [
                "--pbi-id",
                "WI-9100",
                "--repo",
                str(git_repo),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        promote_module.main(
            [
                "--pbi-id",
                "WI-9100",
                "--severity",
                "critical",
                "--repo",
                str(git_repo),
            ]
        )
        == 0
    )
    capsys.readouterr()

    _git(git_repo, "checkout", "ralph-queue")
    cancel_file = git_repo / ".ralph" / "current" / "WI-9100" / "CANCEL"
    assert cancel_file.is_file()
    assert cancel_file.read_bytes() == b""
    fm = _read_frontmatter(git_repo / ".ralph" / "current" / "WI-9100" / "PBI.md")
    assert fm["severity"] == "critical"
