"""Tests for the ``ralph-recover`` skill's entry script.

``ralph-recover`` is the manual escape hatch for foreign CLAIM.json in
``.ralph/current/<pbi-id>/``. It forces the PBI back to ``inbox/`` or
``blocked/``, strips the foreign ``CLAIM.json``, rewrites frontmatter
(``status`` and ``attempts``), appends a HISTORY entry, commits, and
pushes. Every other operator skill refuses foreign claims and points
operators here.
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
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-recover" / "scripts" / "recover.py"


@pytest.fixture(scope="module")
def recover_module() -> ModuleType:
    assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("ralph_recover_script", SCRIPT_PATH)
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


def _configure_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "ralph-recover@example.com")
    _git(repo, "config", "user.name", "ralph-recover")


@pytest.fixture
def queue_env(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """Build a bare queue remote on ``ralph-queue`` + workspace dir."""
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


def _seed_current_pbi(
    bare_url: str,
    tmp_path: Path,
    pbi_id: str,
    *,
    entry_file: str = "PBI.md",
    pbi_type: str = "feature",
    attempts: int = 5,
    claim_instance_id: str | None = "ralph-b",
    claim_payload: str | None = None,
) -> None:
    """Clone bare, add ``.ralph/current/<pbi-id>/`` with CLAIM.json, push back."""
    work = tmp_path / f"seed-{pbi_id}-current"
    subprocess.run(["git", "clone", bare_url, str(work)], check=True, capture_output=True)
    _configure_identity(work)
    pbi_dir = work / ".ralph" / "current" / pbi_id
    pbi_dir.mkdir(parents=True)
    (pbi_dir / entry_file).write_text(
        "---\n"
        f"id: {pbi_id}\n"
        f"type: {pbi_type}\n"
        "status: current\n"
        "severity: normal\n"
        f"attempts: {attempts}\n"
        'created_at: "2026-05-24T10:00:00+00:00"\n'
        'updated_at: "2026-05-24T10:00:00+00:00"\n'
        "---\n"
        "\n"
        "# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    if claim_payload is not None:
        (pbi_dir / "CLAIM.json").write_text(claim_payload, encoding="utf-8")
    elif claim_instance_id is not None:
        claim = {
            "instance_id": claim_instance_id,
            "claimed_at": "2026-05-31T12:00:00+00:00",
            "hostname": "test-host",
        }
        (pbi_dir / "CLAIM.json").write_text(json.dumps(claim, indent=2), encoding="utf-8")
    _git(work, "add", f".ralph/current/{pbi_id}")
    _git(work, "commit", "-m", f"chore(test): seed {pbi_id} in current")
    _git(work, "push", "origin", "ralph-queue")


def _seed_halt_sentinel(bare_url: str, tmp_path: Path) -> None:
    work = tmp_path / "seed-halt"
    subprocess.run(["git", "clone", bare_url, str(work)], check=True, capture_output=True)
    _configure_identity(work)
    halt = work / ".ralph" / "state" / "halted"
    halt.parent.mkdir(parents=True, exist_ok=True)
    halt.write_text("halt\n", encoding="utf-8")
    _git(work, "add", ".ralph/state/halted")
    _git(work, "commit", "-m", "chore(test): seed halt sentinel")
    _git(work, "push", "origin", "ralph-queue")


def _verify_clone(tmp_path: Path, bare_url: str) -> Path:
    verify = tmp_path / f"verify-{Path(bare_url).name}"
    subprocess.run(
        ["git", "clone", bare_url, str(verify)],
        check=True,
        capture_output=True,
    )
    return verify


TEST_INSTANCE_ID = "ralph-a"


def _argv(
    *,
    pbi_id: str,
    workspace: Path,
    queue_repo: str,
    destination: str,
    instance_id: str | None = TEST_INSTANCE_ID,
    extra: list[str] | None = None,
) -> list[str]:
    argv = [
        "--pbi-id",
        pbi_id,
        "--to",
        destination,
        "--workspace",
        str(workspace),
        "--queue-repo",
        queue_repo,
    ]
    if instance_id is not None:
        argv.extend(["--instance-id", instance_id])
    if extra:
        argv.extend(extra)
    return argv


def _read_frontmatter(entry: Path) -> dict[str, object]:
    text = entry.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "---"
    end = next(i for i, line in enumerate(lines[1:], start=1) if line == "---")
    result = yaml.safe_load("\n".join(lines[1:end]))
    assert isinstance(result, dict)
    return result


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_recover_to_inbox_moves_pbi_resets_attempts_and_strips_claim(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_current_pbi(queue_repo, tmp_path, "WI-100", attempts=5, claim_instance_id="ralph-b")

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-100",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-100"
    assert payload["from_state"] == "current"
    assert payload["to_state"] == "inbox"
    assert payload["previous_owner"] == "ralph-b"
    assert payload["pushed"] is True
    assert payload["commit_sha"]

    verify = _verify_clone(tmp_path, queue_repo)
    assert not (verify / ".ralph" / "current" / "WI-100").exists()
    moved = verify / ".ralph" / "inbox" / "WI-100" / "PBI.md"
    assert moved.is_file()
    assert not (verify / ".ralph" / "inbox" / "WI-100" / "CLAIM.json").exists()
    fm = _read_frontmatter(moved)
    assert fm["status"] == "inbox"
    assert fm["attempts"] == 0
    assert "updated_at" in fm

    subject = _git(verify, "log", "-1", "--pretty=%s").strip()
    assert subject == "chore(queue): recover WI-100 from ralph-b"


def test_recover_to_blocked_moves_pbi_preserves_attempts(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_current_pbi(queue_repo, tmp_path, "WI-200", attempts=7, claim_instance_id="ralph-b")

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-200",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="blocked",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["to_state"] == "blocked"

    verify = _verify_clone(tmp_path, queue_repo)
    moved = verify / ".ralph" / "blocked" / "WI-200" / "PBI.md"
    assert moved.is_file()
    assert not (verify / ".ralph" / "blocked" / "WI-200" / "CLAIM.json").exists()
    fm = _read_frontmatter(moved)
    assert fm["status"] == "blocked"
    assert fm["attempts"] == 7  # preserved on blocked path


def test_recover_refuses_if_not_in_current(
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    # Seed nothing — the PBI does not exist.

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-NOPE",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
        )
    )
    assert exit_code == 2
    err = capsys.readouterr().err.lower()
    assert "not found" in err and "current" in err


def test_recover_refuses_when_halt_active(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_current_pbi(queue_repo, tmp_path, "WI-300", claim_instance_id="ralph-b")
    _seed_halt_sentinel(queue_repo, tmp_path)

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-300",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
        )
    )
    assert exit_code == 2
    err = capsys.readouterr().err.lower()
    assert "halt" in err


def test_recover_refuses_destination_collision(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_current_pbi(queue_repo, tmp_path, "WI-400", claim_instance_id="ralph-b")

    # Seed a colliding directory at the destination (.ralph/inbox/WI-400/).
    work = tmp_path / "seed-collide"
    subprocess.run(["git", "clone", queue_repo, str(work)], check=True, capture_output=True)
    _configure_identity(work)
    collide = work / ".ralph" / "inbox" / "WI-400"
    collide.mkdir(parents=True)
    (collide / "PBI.md").write_text(
        "---\nid: WI-400\ntype: feature\nstatus: inbox\nseverity: normal\nattempts: 0\n"
        'created_at: "2026-05-24T10:00:00+00:00"\n'
        'updated_at: "2026-05-24T10:00:00+00:00"\n---\n\n# duplicate\n',
        encoding="utf-8",
    )
    (collide / "HISTORY.md").write_text("", encoding="utf-8")
    _git(work, "add", ".ralph/inbox/WI-400")
    _git(work, "commit", "-m", "chore(test): seed colliding WI-400 in inbox")
    _git(work, "push", "origin", "ralph-queue")

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-400",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
        )
    )
    assert exit_code == 2
    err = capsys.readouterr().err.lower()
    assert "already exists" in err or "refusing to overwrite" in err


def test_recover_appends_history_note(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_current_pbi(queue_repo, tmp_path, "WI-500", claim_instance_id="ralph-b")

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-500",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
        )
    )
    assert exit_code == 0, capsys.readouterr().err

    verify = _verify_clone(tmp_path, queue_repo)
    history = (verify / ".ralph" / "inbox" / "WI-500" / "HISTORY.md").read_text(encoding="utf-8")
    assert "ralph-recover" in history
    assert "ralph-b" in history
    assert "recover" in history.lower()


def test_recover_no_claim_records_previous_owner_null(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A current/ PBI without CLAIM.json (legacy / mid-migration) recovers cleanly."""
    workspace, queue_repo = queue_env
    _seed_current_pbi(queue_repo, tmp_path, "WI-600", claim_instance_id=None)

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-600",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["previous_owner"] is None

    verify = _verify_clone(tmp_path, queue_repo)
    subject = _git(verify, "log", "-1", "--pretty=%s").strip()
    assert subject == "chore(queue): recover WI-600 from <no claim>"


def test_recover_errors_on_malformed_claim(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_current_pbi(queue_repo, tmp_path, "WI-700", claim_payload="not json at all")

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-700",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
        )
    )
    assert exit_code == 2
    err = capsys.readouterr().err.lower()
    assert "malformed" in err and "claim" in err


def test_recover_no_push_keeps_remote_unchanged(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_current_pbi(queue_repo, tmp_path, "WI-800", claim_instance_id="ralph-b")
    before = subprocess.run(
        ["git", "ls-remote", queue_repo, "ralph-queue"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-800",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
            extra=["--no-push"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pushed"] is False
    assert payload["commit_sha"]
    after = subprocess.run(
        ["git", "ls-remote", queue_repo, "ralph-queue"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    assert before == after


def test_recover_uses_namespaced_clone(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--instance-id`` flag routes the clone to ``queue-<instance-id>/``."""
    workspace, queue_repo = queue_env
    _seed_current_pbi(queue_repo, tmp_path, "WI-900", claim_instance_id="ralph-b")

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-900",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
            instance_id="ralph-zz",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["queue_clone"].endswith("queue-ralph-zz")
    assert (workspace / "queue-ralph-zz").is_dir()


def test_recover_rejects_invalid_instance_id(
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-1",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
            instance_id="!!!",
        )
    )
    assert exit_code == 2
    err = capsys.readouterr().err.lower()
    assert "instance_id" in err
