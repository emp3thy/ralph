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
    claim_instance_id: str | None = None,
    claim_payload: str | None = None,
) -> None:
    """Clone the bare remote, add a PBI under ``.ralph/<state>/<id>``, push back.

    When ``claim_instance_id`` is supplied, a ``CLAIM.json`` is committed
    alongside the entry file naming that instance as the owner. When
    ``claim_payload`` is supplied, the raw string is written verbatim
    (used to seed malformed CLAIM.json fixtures).
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
    if claim_payload is not None:
        (pbi_dir / "CLAIM.json").write_text(claim_payload, encoding="utf-8")
    elif claim_instance_id is not None:
        claim = {
            "instance_id": claim_instance_id,
            "claimed_at": "2026-05-31T12:00:00+00:00",
            "hostname": "test-host",
        }
        (pbi_dir / "CLAIM.json").write_text(json.dumps(claim, indent=2), encoding="utf-8")
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


TEST_INSTANCE_ID = "test"


def _argv(
    *,
    pbi_id: str,
    workspace: Path,
    queue_repo: str,
    instance_id: str | None = TEST_INSTANCE_ID,
    extra: list[str] | None = None,
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


def _clone_path(workspace: Path, instance_id: str = TEST_INSTANCE_ID) -> Path:
    return workspace / f"queue-{instance_id}"


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
    subprocess.run(["git", "clone", queue_repo, str(_clone_path(workspace))], check=True)
    _configure_identity(_clone_path(workspace))

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
    subprocess.run(["git", "clone", queue_repo, str(_clone_path(workspace))], check=True)
    _configure_identity(_clone_path(workspace))

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
    subprocess.run(["git", "clone", queue_repo, str(_clone_path(workspace))], check=True)
    _configure_identity(_clone_path(workspace))

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
    subprocess.run(["git", "clone", queue_repo, str(_clone_path(workspace))], check=True)
    _configure_identity(_clone_path(workspace))
    before = _git(_clone_path(workspace), "ls-remote", "origin", "ralph-queue").strip()

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-3000", workspace=workspace, queue_repo=queue_repo, extra=["--no-push"])
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pushed"] is False
    assert payload["commit_sha"]
    after = _git(_clone_path(workspace), "ls-remote", "origin", "ralph-queue").strip()
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
    assert not _clone_path(workspace).exists()
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

    subprocess.run(["git", "clone", queue_repo, str(_clone_path(workspace))], check=True)
    _configure_identity(_clone_path(workspace))

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
    subprocess.run(["git", "clone", queue_repo, str(_clone_path(workspace))], check=True)
    _configure_identity(_clone_path(workspace))
    clone = _clone_path(workspace)
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
    subprocess.run(["git", "clone", queue_repo, str(_clone_path(workspace))], check=True)
    _configure_identity(_clone_path(workspace))

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

    subprocess.run(["git", "clone", queue_repo, str(_clone_path(workspace))], check=True)
    _configure_identity(_clone_path(workspace))

    exit_code = cancel_module.main(
        [
            "--pbi-id",
            "WI-6000",
            "--workspace",
            str(workspace),
            "--instance-id",
            TEST_INSTANCE_ID,
            "--no-push",
        ]
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-6000"
    assert payload["commit_sha"] != ""


# ----------------------------------------------------------------------
# T16: foreign-CLAIM refusal + --instance-id flag
# ----------------------------------------------------------------------


def test_cancel_refuses_foreign_claim(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Current PBI with a CLAIM owned by ralph-b is not cancellable by ralph-a."""
    workspace, queue_repo = queue_env
    _seed_pbi(
        queue_repo,
        tmp_path,
        "current",
        "WI-FOREIGN",
        claim_instance_id="ralph-b",
    )
    subprocess.run(
        ["git", "clone", queue_repo, str(_clone_path(workspace, "ralph-a"))],
        check=True,
    )
    _configure_identity(_clone_path(workspace, "ralph-a"))

    exit_code = cancel_module.main(
        _argv(
            pbi_id="WI-FOREIGN",
            workspace=workspace,
            queue_repo=queue_repo,
            instance_id="ralph-a",
        )
    )
    assert exit_code == 2
    err = capsys.readouterr().err.lower()
    assert "claimed by" in err
    assert "ralph-b" in err
    assert "ralph-recover" in err


def test_cancel_accepts_own_claim(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Current PBI claimed by this instance proceeds to cancel."""
    workspace, queue_repo = queue_env
    _seed_pbi(
        queue_repo,
        tmp_path,
        "current",
        "WI-OWN",
        claim_instance_id=TEST_INSTANCE_ID,
    )
    subprocess.run(
        ["git", "clone", queue_repo, str(_clone_path(workspace))],
        check=True,
    )
    _configure_identity(_clone_path(workspace))

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-OWN", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["already_cancelled"] is False
    assert payload["commit_sha"]


def test_cancel_errors_on_malformed_claim_json(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed CLAIM.json surfaces as a QueueWriterError exit, not a silent pass."""
    workspace, queue_repo = queue_env
    _seed_pbi(
        queue_repo,
        tmp_path,
        "current",
        "WI-BAD",
        claim_payload="not json at all",
    )
    subprocess.run(
        ["git", "clone", queue_repo, str(_clone_path(workspace))],
        check=True,
    )
    _configure_identity(_clone_path(workspace))

    exit_code = cancel_module.main(
        _argv(pbi_id="WI-BAD", workspace=workspace, queue_repo=queue_repo)
    )
    assert exit_code == 2
    err = capsys.readouterr().err.lower()
    assert "malformed" in err and "claim" in err


def test_cancel_instance_id_flag_propagates_to_acquire(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--instance-id ralph-z reaches acquire_queue_clone as a kwarg."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-PROP")
    subprocess.run(
        ["git", "clone", queue_repo, str(_clone_path(workspace, "ralph-z"))],
        check=True,
    )
    _configure_identity(_clone_path(workspace, "ralph-z"))

    seen: list[str | None] = []
    real_acquire = cancel_module.acquire_queue_clone

    def fake_acquire(
        workspace_root: Path,
        repo: str,
        branch: str,
        *,
        instance_id: str | None = None,
        timeout: float = 120.0,
    ) -> Path:
        seen.append(instance_id)
        return real_acquire(
            workspace_root,
            repo,
            branch,
            instance_id=instance_id,
            timeout=timeout,
        )

    monkeypatch.setattr(cancel_module, "acquire_queue_clone", fake_acquire)
    exit_code = cancel_module.main(
        _argv(
            pbi_id="WI-PROP",
            workspace=workspace,
            queue_repo=queue_repo,
            instance_id="ralph-z",
            extra=["--no-push"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    assert seen == ["ralph-z"]


def test_cancel_rejects_invalid_instance_id(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--instance-id '!!!' sanitises to empty and is rejected with exit 2."""
    workspace, queue_repo = queue_env
    exit_code = cancel_module.main(
        _argv(
            pbi_id="WI-1",
            workspace=workspace,
            queue_repo=queue_repo,
            instance_id="!!!",
        )
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "instance_id" in err.lower()


def test_cancel_dry_run_uses_namespaced_clone_path(
    queue_env: tuple[Path, str],
    cancel_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run payload reports the per-instance namespaced clone path."""
    workspace, queue_repo = queue_env
    exit_code = cancel_module.main(
        _argv(
            pbi_id="WI-DRY",
            workspace=workspace,
            queue_repo=queue_repo,
            instance_id="ralph-z",
            extra=["--dry-run"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["queue_clone"].endswith("queue-ralph-z")
