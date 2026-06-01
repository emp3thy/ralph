"""End-to-end-ish smoke test threading three supervisor skills together.

Builds a bare git repo as the queue remote (single ``main`` branch, no
``ralph-queue`` branch), seeds a PBI directly onto ``main`` via a
helper clone, then drives ``ralph-triage`` / ``ralph-promote`` /
``ralph-cancel`` against the new queue-clone model. The point of the
test is to confirm the three skills agree on the on-disk schema
produced by ``scripts.queue_writer`` — a single PBI flowing through
multiple skills must arrive at a consistent end state.
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

# Operator identity used when seeding multi-ralph CLAIM.json files and
# invoking the cancel / promote ownership guards. Mirrors the convention
# in tests/skills/test_ralph_cancel.py and tests/skills/test_ralph_promote.py.
OWN_INSTANCE_ID = "test-ralph"


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


def _configure_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "supervisor-smoke@example.com")
    _git(repo, "config", "user.name", "supervisor-smoke")


@pytest.fixture
def queue_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, str]]:
    """Bare queue remote with an empty seeded ``main`` + tmp workspace dir.

    Also sets GIT_AUTHOR_* / GIT_COMMITTER_* env vars so commits made
    inside the queue clone created by the skill (under ``<workspace>/queue``)
    succeed on a CI runner that has no global git identity. The clone
    is created by the script under test, not the fixture, so we cannot
    ``git config`` it in advance — the env vars are the only seam that
    covers both paths.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "supervisor-smoke")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "supervisor-smoke@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "supervisor-smoke")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "supervisor-smoke@example.com")

    bare = tmp_path / "queue.git"
    seed = tmp_path / "queue-seed"
    workspace = tmp_path / "ws"
    subprocess.run(["git", "init", "--bare", "-b", "ralph-queue", str(bare)], check=True)
    subprocess.run(["git", "init", "-b", "ralph-queue", str(seed)], check=True)
    _configure_identity(seed)
    _git(seed, "commit", "--allow-empty", "-m", "chore: initial ralph-queue")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-u", "origin", "ralph-queue")
    yield workspace, str(bare)


def _seed_pbi(
    bare_url: str,
    tmp_path: Path,
    state_folder: str,
    pbi_id: str,
    *,
    attempts: int = 0,
    entry_file: str = "PBI.md",
    pbi_type: str = "feature",
    severity: str = "normal",
) -> None:
    """Clone the bare remote, add a PBI under ``.ralph/<state>/<id>``, push."""
    work = tmp_path / f"seed-{pbi_id}-{state_folder}"
    subprocess.run(["git", "clone", bare_url, str(work)], check=True, capture_output=True)
    _configure_identity(work)
    pbi_dir = work / ".ralph" / state_folder / pbi_id
    pbi_dir.mkdir(parents=True)
    (pbi_dir / entry_file).write_text(
        "---\n"
        f"id: {pbi_id}\n"
        f"type: {pbi_type}\n"
        f"status: {state_folder}\n"
        f"severity: {severity}\n"
        f"attempts: {attempts}\n"
        'created_at: "2026-05-18T08:00:00+00:00"\n'
        'updated_at: "2026-05-22T09:30:00+00:00"\n'
        "---\n"
        "\n"
        "# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    if state_folder == "current":
        # Multi-ralph Scope 1 invariant: every current/<id>/ has a CLAIM.json.
        # Seeded as own-claim so cancel / promote ownership guards proceed.
        from ralph_executor.queue.claim import Claim, write_claim

        write_claim(
            pbi_dir / "CLAIM.json",
            Claim(
                instance_id=OWN_INSTANCE_ID,
                claimed_at="2026-05-22T09:30:00+00:00",
                hostname="seed-host",
            ),
        )
    _git(work, "add", f".ralph/{state_folder}/{pbi_id}")
    _git(work, "commit", "-m", f"chore(test): seed {pbi_id} in {state_folder}")
    _git(work, "push", "origin", "ralph-queue")


def _verify_clone(tmp_path: Path, bare_url: str, suffix: str) -> Path:
    verify = tmp_path / f"verify-{suffix}"
    subprocess.run(
        ["git", "clone", bare_url, str(verify)],
        check=True,
        capture_output=True,
    )
    return verify


def _read_frontmatter(entry_file: Path) -> dict[str, object]:
    text = entry_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "---"
    end = next(i for i, line in enumerate(lines[1:], start=1) if line == "---")
    result = yaml.safe_load("\n".join(lines[1:end]))
    assert isinstance(result, dict)
    return result


def test_triage_then_promote_round_trip(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A blocked PBI triaged back to inbox then promoted to current ends
    up in current/ with attempts=0 (from triage) and status=current
    (from promote), and the HISTORY.md carries entries from both skills."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "blocked", "WI-9000", attempts=3)

    assert (
        triage_module.main(
            [
                "--pbi-id",
                "WI-9000",
                "--to",
                "inbox",
                "--note",
                "reset after smoke test",
                "--workspace",
                str(workspace),
                "--queue-repo",
                queue_repo,
            ]
        )
        == 0
    ), capsys.readouterr().err
    capsys.readouterr()

    assert (
        promote_module.main(
            [
                "--pbi-id",
                "WI-9000",
                "--from",
                "inbox",
                "--to",
                "current",
                "--workspace",
                str(workspace),
                "--queue-repo",
                queue_repo,
            ]
        )
        == 0
    ), capsys.readouterr().err
    capsys.readouterr()

    verify = _verify_clone(tmp_path, queue_repo, "round-trip")
    assert not (verify / ".ralph" / "blocked" / "WI-9000").exists()
    assert not (verify / ".ralph" / "inbox" / "WI-9000").exists()
    new_pbi = verify / ".ralph" / "current" / "WI-9000"
    assert new_pbi.is_dir()

    fm = _read_frontmatter(new_pbi / "PBI.md")
    assert fm["status"] == "current"
    assert fm["attempts"] == 0

    history = (new_pbi / "HISTORY.md").read_text(encoding="utf-8")
    assert "ralph-triage" in history
    assert "ralph-promote" in history
    assert "reset after smoke test" in history
    assert "inbox -> current" in history


def test_cancel_and_promote_are_independent(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cancelling one PBI and promoting another in the same queue clone
    both succeed and produce the expected on-disk state — the skills
    do not stomp on each other."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-9100")
    _seed_pbi(queue_repo, tmp_path, "inbox", "WI-9101")

    assert (
        cancel_module.main(
            [
                "--pbi-id",
                "WI-9100",
                "--workspace",
                str(workspace),
                "--queue-repo",
                queue_repo,
                "--instance-id",
                OWN_INSTANCE_ID,
            ]
        )
        == 0
    ), capsys.readouterr().err
    capsys.readouterr()

    assert (
        promote_module.main(
            [
                "--pbi-id",
                "WI-9101",
                "--from",
                "inbox",
                "--to",
                "current",
                "--workspace",
                str(workspace),
                "--queue-repo",
                queue_repo,
            ]
        )
        == 0
    ), capsys.readouterr().err
    capsys.readouterr()

    verify = _verify_clone(tmp_path, queue_repo, "independent")
    cancel_file = verify / ".ralph" / "current" / "WI-9100" / "CANCEL"
    assert cancel_file.is_file()
    assert cancel_file.read_bytes() == b""

    promoted = verify / ".ralph" / "current" / "WI-9101"
    assert promoted.is_dir()
    fm = _read_frontmatter(promoted / "PBI.md")
    assert fm["status"] == "current"
    assert not (verify / ".ralph" / "inbox" / "WI-9101").exists()
