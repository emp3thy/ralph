"""Tests for the ``ralph-cancel`` skill's entry script.

``ralph-cancel`` is the only mutation we allow on ``.ralph/current/``:
it drops an empty ``CANCEL`` sentinel file inside the directory of the
PBI Ralph is actively working on, then commits + pushes ``main`` to the
queue clone's origin. Ralph notices the sentinel on its next iteration
and abandons the PBI.

The script lives at ``skills/ralph-cancel/scripts/cancel.py``. Tests
build a bare git repo as the "queue remote", seed PBIs onto its ``main``
branch, and verify each behaviour without any network or real Git host.
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
SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-cancel" / "scripts" / "cancel.py"

# Every existing test that lands a CANCEL sentinel runs as this operator
# identity; seeded CLAIM.json files declare the same instance_id so the
# Task 13 ownership guard lets the cancel through.
OWN_INSTANCE_ID = "test-ralph"


@pytest.fixture(scope="module")
def cancel_module() -> ModuleType:
    assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("ralph_cancel_script", SCRIPT_PATH)
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
    _git(repo, "config", "user.email", "ralph-cancel@example.com")
    _git(repo, "config", "user.name", "ralph-cancel")


@pytest.fixture
def queue_env(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """Build a bare queue remote + an empty workspace dir.

    The bare remote is seeded with an empty ``main`` so
    ``ensure_queue_clone`` can clone it cleanly. Returns
    ``(workspace_root, queue_repo_url)`` for the test to pass into
    ``ralph-cancel`` via ``--workspace`` / ``--queue-repo``.
    """
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
    entry_file: str = "PBI.md",
    claim_instance_id: str | None = OWN_INSTANCE_ID,
) -> None:
    """Clone the bare remote, add a PBI under ``.ralph/<state>/<id>``, push back.

    When ``state_folder == "current"`` and ``claim_instance_id`` is non-None
    (default), also seeds a ``CLAIM.json`` so the Task 13 ownership guard
    in ralph-cancel finds a valid claim. Tests that need the
    missing-CLAIM or foreign-CLAIM scenarios override
    ``claim_instance_id``.
    """
    work = tmp_path / f"seed-{pbi_id}-{state_folder}"
    subprocess.run(["git", "clone", bare_url, str(work)], check=True, capture_output=True)
    _configure_identity(work)
    pbi_dir = work / ".ralph" / state_folder / pbi_id
    pbi_dir.mkdir(parents=True)
    (pbi_dir / entry_file).write_text(
        "---\n"
        f"id: {pbi_id}\n"
        "type: feature\n"
        f"status: {state_folder}\n"
        "severity: normal\n"
        "attempts: 0\n"
        "created_at: 2026-05-24T10:00:00+00:00\n"
        "updated_at: 2026-05-24T10:00:00+00:00\n"
        "---\n"
        "\n"
        "# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    if state_folder == "current" and claim_instance_id is not None:
        from ralph_executor.queue.claim import Claim, write_claim

        write_claim(
            pbi_dir / "CLAIM.json",
            Claim(
                instance_id=claim_instance_id,
                claimed_at="2026-05-24T10:00:00+00:00",
                hostname="seed-host",
            ),
        )
    _git(work, "add", f".ralph/{state_folder}/{pbi_id}")
    _git(work, "commit", "-m", f"chore(test): seed {pbi_id} in {state_folder}")
    _git(work, "push", "origin", "ralph-queue")


def _verify_clone(tmp_path: Path, bare_url: str) -> Path:
    verify = tmp_path / f"verify-{Path(bare_url).name}"
    subprocess.run(
        ["git", "clone", bare_url, str(verify)],
        check=True,
        capture_output=True,
    )
    return verify


def _argv(
    *,
    pbi_id: str,
    workspace: Path,
    queue_repo: str,
    extra: list[str] | None = None,
    instance_id: str | None = OWN_INSTANCE_ID,
) -> list[str]:
    argv = [
        "--pbi-id",
        pbi_id,
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


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_cancel_drops_sentinel_in_current(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-1234")

    # Pre-clone so committer identity is configured before cancel commits.
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-1234", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-1234"
    assert payload["sentinel_path"].endswith("WI-1234/CANCEL")
    assert payload["pushed"] is True
    assert payload["commit_sha"]

    verify = _verify_clone(tmp_path, queue_repo)
    cancel_file = verify / ".ralph" / "current" / "WI-1234" / "CANCEL"
    assert cancel_file.is_file()
    assert cancel_file.read_bytes() == b""

    latest = _git(verify, "log", "-1", "--pretty=%s").strip()
    assert latest == "chore(queue): cancel WI-1234"


def test_cancel_refuses_pbi_outside_current(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "inbox", "WI-2000")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-2000", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "current" in stderr


def test_cancel_errors_on_missing_pbi(
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-DOES-NOT-EXIST", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "not found" in stderr or "no such" in stderr or "missing" in stderr


def test_cancel_no_push_keeps_remote_at_previous_sha(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-3000")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")
    before = _git(workspace / "queue", "ls-remote", "origin", "ralph-queue").strip()

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-3000", workspace=workspace, queue_repo=queue_repo, extra=["--no-push"])
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pushed"] is False
    assert payload["commit_sha"]
    after = _git(workspace / "queue", "ls-remote", "origin", "ralph-queue").strip()
    assert before == after


def test_cancel_dry_run_writes_nothing(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-4000")
    before = subprocess.run(
        ["git", "ls-remote", queue_repo, "ralph-queue"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-4000", workspace=workspace, queue_repo=queue_repo, extra=["--dry-run"])
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["pushed"] is False
    assert payload["commit_sha"] == ""

    # Dry-run must NOT clone the queue or push.
    assert not (workspace / "queue").exists()
    after = subprocess.run(
        ["git", "ls-remote", queue_repo, "ralph-queue"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    assert before == after


def test_cancel_idempotent_when_sentinel_already_present(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-5000")
    # Push a pre-existing CANCEL sentinel into ralph-queue.
    seed = tmp_path / "seed-cancel"
    subprocess.run(["git", "clone", queue_repo, str(seed)], check=True, capture_output=True)
    _configure_identity(seed)
    (seed / ".ralph" / "current" / "WI-5000" / "CANCEL").write_text("", encoding="utf-8")
    _git(seed, "add", ".ralph/current/WI-5000/CANCEL")
    _git(seed, "commit", "-m", "chore(test): pre-existing cancel")
    _git(seed, "push", "origin", "ralph-queue")

    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-5000", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["already_cancelled"] is True
    assert payload["commit_sha"] == ""
    assert payload["pushed"] is False


def test_cancel_runs_full_path_when_sentinel_on_disk_but_not_committed(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: BugBot PR #35 flagged that the idempotency check used
    ``Path.exists()`` which fires for a sentinel that was staged but
    never committed (pre-commit hook reject, missing user.email, etc.).
    The script then exited 0 with ``already_cancelled=True`` even though
    HEAD was unchanged — ralph would never see the cancel. Fix routes
    the check through ``git cat-file -e HEAD:<path>``; this test seeds
    the bad-state (sentinel on disk + staged but NOT committed in the
    queue clone) and asserts the next invocation re-runs the full commit
    + push path."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-5050")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")
    clone = workspace / "queue"
    # Stage but do NOT commit — simulates a previously-failed cancel
    # whose ``git add`` succeeded but whose ``git commit`` hook rejected.
    (clone / ".ralph" / "current" / "WI-5050" / "CANCEL").write_text("", encoding="utf-8")
    _git(clone, "add", ".ralph/current/WI-5050/CANCEL")
    head_before = _git(clone, "rev-parse", "HEAD").strip()

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-5050", workspace=workspace, queue_repo=queue_repo, extra=["--no-push"])
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["already_cancelled"] is False, (
        "must re-run the full path when the sentinel is not in HEAD, "
        "even if it exists on the filesystem"
    )
    assert payload["commit_sha"] != ""
    head_after = _git(clone, "rev-parse", "HEAD").strip()
    assert head_after != head_before, "a new commit must land on main"


def test_cancel_pushes_ralph_queue_by_default(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ralph-cancel pushes to ralph-queue when no --queue-branch override."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-7000")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    pushed: list[tuple[Path, str]] = []

    def fake_push(repo: Path, branch: str) -> None:
        pushed.append((repo, branch))

    monkeypatch.setattr(cancel_module, "push", fake_push)

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-7000", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 0, capsys.readouterr().err
    assert [branch for _repo, branch in pushed] == ["ralph-queue"]


def test_cancel_errors_when_queue_repo_unset(
    tmp_path: Path,
    cancel_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without --queue-repo and without TOML queue_repo, exit 2 with a clear error."""
    workspace = tmp_path / "ws"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    exit_code = cancel_module.main(
        [
            "--pbi-id",
            "WI-1",
            "--workspace",
            str(workspace),
        ]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "queue_repo" in err.lower()


def test_cancel_queue_repo_resolved_from_toml(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When --queue-repo is omitted, the TOML value is used."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-6000")

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    (fake_home / ".ralph").mkdir()
    # file:// URL with forward slashes survives TOML basic-string escaping on Windows.
    queue_url = "file:///" + Path(queue_repo).as_posix().lstrip("/")
    (fake_home / ".ralph" / "config.toml").write_text(
        f'queue_repo = "{queue_url}"\n',
        encoding="utf-8",
    )

    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = cancel_module.main(
        [
            "--pbi-id",
            "WI-6000",
            "--workspace",
            str(workspace),
            "--no-push",
            "--instance-id",
            OWN_INSTANCE_ID,
        ]
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-6000"
    assert payload["commit_sha"] != ""


# ----------------------------------------------------------------------
# Task 13 — CLAIM.json ownership guard
# ----------------------------------------------------------------------


def test_cancel_refuses_foreign_claim_with_exit_3(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Per Task 13: a cancel against a PBI claimed by another instance is
    refused with exit 3 and the operator is steered to ralph-recover.
    The remote MUST be untouched — no CANCEL sentinel landed, no commit."""
    workspace, queue_repo = queue_env
    _seed_pbi(
        queue_repo,
        tmp_path,
        "current",
        "WI-FOREIGN",
        claim_instance_id="other-ralph",
    )
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")
    tip_before = _git(workspace / "queue", "ls-remote", "origin", "ralph-queue").strip()

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-FOREIGN", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == cancel_module.EXIT_CLAIM_GUARD == 3
    stderr = capsys.readouterr().err
    assert "ralph-cancel: cannot cancel PBI claimed by 'other-ralph'" in stderr
    assert "use ralph-recover" in stderr

    tip_after = _git(workspace / "queue", "ls-remote", "origin", "ralph-queue").strip()
    assert tip_before == tip_after, "foreign-claim refusal must not push anything"


def test_cancel_refuses_missing_claim_with_exit_3(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Per Task 13: every ``current/<id>/`` MUST carry a CLAIM.json under
    Scope 1. A missing CLAIM.json is an inconsistent queue and ralph-cancel
    refuses with exit 3 + the documented message."""
    workspace, queue_repo = queue_env
    # claim_instance_id=None suppresses CLAIM.json seeding.
    _seed_pbi(
        queue_repo,
        tmp_path,
        "current",
        "WI-NOCLAIM",
        claim_instance_id=None,
    )
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-NOCLAIM", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == cancel_module.EXIT_CLAIM_GUARD == 3
    stderr = capsys.readouterr().err
    assert "ralph-cancel: PBI in current/ but no CLAIM.json" in stderr


def test_cancel_proceeds_when_claim_owned_by_operator(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Sanity test for Task 13's positive path: when CLAIM.json's
    ``instance_id`` matches the operator's resolved identity, the cancel
    proceeds through the existing happy-path. Sentinel lands on origin."""
    workspace, queue_repo = queue_env
    _seed_pbi(
        queue_repo,
        tmp_path,
        "current",
        "WI-OWN",
        claim_instance_id=OWN_INSTANCE_ID,
    )
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-OWN", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pushed"] is True
    assert payload["commit_sha"]

    verify = _verify_clone(tmp_path, queue_repo)
    assert (verify / ".ralph" / "current" / "WI-OWN" / "CANCEL").is_file()


def test_cancel_refuses_malformed_claim_with_exit_3(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A CLAIM.json that fails to parse (corrupt JSON / missing keys) is
    rejected with the same exit-3 guard. Resilience requirement: ralph-cancel
    must NOT crash with an unhandled ClaimError on a busted queue."""
    workspace, queue_repo = queue_env
    # Seed a CLAIM.json by going through the normal seed path, then
    # overwrite the file with invalid JSON via a follow-up commit.
    _seed_pbi(queue_repo, tmp_path, "current", "WI-BAD")
    overwrite = tmp_path / "seed-bad-claim"
    subprocess.run(
        ["git", "clone", queue_repo, str(overwrite)],
        check=True,
        capture_output=True,
    )
    _configure_identity(overwrite)
    (overwrite / ".ralph" / "current" / "WI-BAD" / "CLAIM.json").write_text(
        "not json at all", encoding="utf-8"
    )
    _git(overwrite, "add", ".ralph/current/WI-BAD/CLAIM.json")
    _git(overwrite, "commit", "-m", "chore(test): break CLAIM.json")
    _git(overwrite, "push", "origin", "ralph-queue")

    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-BAD", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == cancel_module.EXIT_CLAIM_GUARD == 3
    stderr = capsys.readouterr().err
    assert "malformed CLAIM.json" in stderr


def test_cancel_resolves_instance_id_from_env_when_flag_omitted(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operator identity resolution honours the documented precedence
    chain. With no --instance-id flag, RALPH_INSTANCE_ID env wins over
    the (real-host) hostname fallback so the seeded claim matches."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-ENV", claim_instance_id="env-ralph")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")
    monkeypatch.setenv("RALPH_INSTANCE_ID", "env-ralph")
    # Isolate the user-config layer so a real ~/.ralph/config.toml does
    # not inject an instance_id that pre-empts the env var.
    fake_home = tmp_path / "home-env"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    exit_code = cancel_module.main(
        _argv(
            pbi_id="WI-ENV",
            workspace=workspace,
            queue_repo=queue_repo,
            instance_id=None,
        )
    )
    assert exit_code == 0, capsys.readouterr().err
