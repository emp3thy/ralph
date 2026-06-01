"""Tests for the ``ralph-recover`` skill's entry script.

``ralph-recover`` is the operator-driven recovery path for stuck
claims under Scope 1 multi-ralph. It moves a PBI out of
``.ralph/current/`` back to either ``.ralph/inbox/`` or
``.ralph/blocked/``, deletes the orphan ``CLAIM.json``, appends an
audit entry to ``HISTORY.md``, and (when destination is ``inbox``)
resets the ``attempts:`` counter to ``0``. Every recover lands as one
commit pinned to ``chore(queue): recover <id> from <previous-owner>``.

Tests build a bare git repo as the queue remote, seed PBIs onto its
``ralph-queue`` branch, and verify each behaviour without any network
or real Git host.
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

# Identity of the foreign ralph that owns the claim being recovered.
# ralph-recover does not consult the operator's identity for any guard
# — the prior owner from CLAIM.json is what lands in the commit
# subject + HISTORY entry — so a single "foreign" constant suffices.
FOREIGN_INSTANCE_ID = "ralph-a"


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
    """Build a bare queue remote + an empty workspace dir.

    The bare remote is seeded with an empty ``ralph-queue`` so
    ``ensure_queue_clone`` can clone it cleanly. Returns
    ``(workspace_root, queue_repo_url)`` for the test to pass into
    ``ralph-recover`` via ``--workspace`` / ``--queue-repo``.
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
    attempts: int = 0,
    claim_instance_id: str | None = FOREIGN_INSTANCE_ID,
) -> None:
    """Clone the bare remote, add a PBI under ``.ralph/<state>/<id>``, push back.

    When ``state_folder == "current"`` and ``claim_instance_id`` is
    non-None (default ``FOREIGN_INSTANCE_ID``), also seeds a
    ``CLAIM.json`` so ralph-recover finds a claim to consume. Tests
    that need the missing-CLAIM scenario pass
    ``claim_instance_id=None``.
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
        f"attempts: {attempts}\n"
        'created_at: "2026-05-24T10:00:00+00:00"\n'
        'updated_at: "2026-05-24T10:00:00+00:00"\n'
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
    to_state: str,
    extra: list[str] | None = None,
    instance_id: str | None = "test-ralph",
) -> list[str]:
    argv = [
        "--pbi-id",
        pbi_id,
        "--to",
        to_state,
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


def _write_halt_sentinel(clone: Path) -> None:
    """Drop an UN-acknowledged halt sentinel into the clone's state dir.

    Mirrors the on-disk format ``write_halt_sentinel`` produces: the
    placeholder ``acknowledged_by:`` / ``acknowledged_at:`` lines have
    empty values, which ``check_halt_sentinel`` reads as
    ``HaltStatus.HALTED``. The file is untracked so a subsequent
    ``git pull --ff-only`` from ``acquire_queue_clone`` preserves it.
    """
    sentinel = clone / ".ralph" / "state" / "halted"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(
        "meta_bug_id: META-cycle-20260601T000000Z\n"
        "halted_at: 2026-06-01T00:00:00+00:00\n"
        "acknowledged_by:\n"
        "acknowledged_at:\n",
        encoding="utf-8",
    )


# ----------------------------------------------------------------------
# Happy-path tests
# ----------------------------------------------------------------------


def test_recover_to_inbox_resets_attempts_and_deletes_claim(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Happy path: --to inbox with foreign CLAIM. PBI moved to inbox/,
    CLAIM.json deleted at dest, attempts reset to 0, HISTORY appended,
    and a single commit lands on origin/ralph-queue."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-INBOX", attempts=2)

    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue-test-ralph")], check=True)
    _configure_identity(workspace / "queue-test-ralph")
    tip_before = _git(workspace / "queue-test-ralph", "ls-remote", "origin", "ralph-queue").strip()

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-INBOX",
            workspace=workspace,
            queue_repo=queue_repo,
            to_state="inbox",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-INBOX"
    assert payload["from_state"] == "current"
    assert payload["to_state"] == "inbox"
    assert payload["attempts_reset"] is True
    assert payload["recovered_from_instance"] == FOREIGN_INSTANCE_ID
    assert payload["pushed"] is True
    assert payload["commit_sha"]
    assert payload["dry_run_skipped"] is False

    # Verify against the bare remote: PBI is in inbox/, gone from
    # current/, CLAIM.json absent at dest, attempts reset to 0,
    # HISTORY.md grew, and the push landed ONE commit.
    verify = _verify_clone(tmp_path, queue_repo)
    assert not (verify / ".ralph" / "current" / "WI-INBOX").exists()
    inbox_dir = verify / ".ralph" / "inbox" / "WI-INBOX"
    assert inbox_dir.is_dir()
    assert not (inbox_dir / "CLAIM.json").exists()
    frontmatter = _read_frontmatter(inbox_dir / "PBI.md")
    assert frontmatter["status"] == "inbox"
    assert frontmatter["attempts"] == 0
    history = (inbox_dir / "HISTORY.md").read_text(encoding="utf-8")
    assert "actor: ralph-recover" in history
    assert "action: recover" in history
    assert f"from {FOREIGN_INSTANCE_ID}" in history

    tip_after = _git(workspace / "queue-test-ralph", "ls-remote", "origin", "ralph-queue").strip()
    assert tip_after != tip_before, "the recover push must advance origin"


def test_recover_to_blocked_preserves_attempts(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Happy path: --to blocked with foreign CLAIM. PBI moved to blocked/,
    CLAIM.json deleted at dest, attempts PRESERVED (not reset), HISTORY
    appended, single commit pushed. Plan invariant: an orphaned claim
    going to triage keeps its attempt history."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-BLOCKED", attempts=2)

    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue-test-ralph")], check=True)
    _configure_identity(workspace / "queue-test-ralph")

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-BLOCKED",
            workspace=workspace,
            queue_repo=queue_repo,
            to_state="blocked",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["to_state"] == "blocked"
    assert payload["attempts_reset"] is False
    assert payload["recovered_from_instance"] == FOREIGN_INSTANCE_ID

    verify = _verify_clone(tmp_path, queue_repo)
    assert not (verify / ".ralph" / "current" / "WI-BLOCKED").exists()
    blocked_dir = verify / ".ralph" / "blocked" / "WI-BLOCKED"
    assert blocked_dir.is_dir()
    assert not (blocked_dir / "CLAIM.json").exists()
    frontmatter = _read_frontmatter(blocked_dir / "PBI.md")
    assert frontmatter["status"] == "blocked"
    assert frontmatter["attempts"] == 2, "blocked recover must NOT reset attempts"
    history = (blocked_dir / "HISTORY.md").read_text(encoding="utf-8")
    assert "actor: ralph-recover" in history


# ----------------------------------------------------------------------
# Refusal tests
# ----------------------------------------------------------------------


def test_recover_refuses_when_pbi_not_in_current(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A PBI that is not under ``.ralph/current/`` cannot be recovered —
    ralph-recover is current-state-only. Exits 2 with a clear error."""
    workspace, queue_repo = queue_env
    # Seed in inbox/ instead of current/.
    _seed_pbi(queue_repo, tmp_path, "inbox", "WI-NOT-CURRENT")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue-test-ralph")], check=True)
    _configure_identity(workspace / "queue-test-ralph")

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-NOT-CURRENT",
            workspace=workspace,
            queue_repo=queue_repo,
            to_state="inbox",
        )
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "not found" in stderr
    assert "current" in stderr


def test_recover_refuses_when_destination_has_existing_pbi(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse when ``.ralph/<to>/<id>/`` already exists in the queue.
    Recover must NOT overwrite a directory at the destination — the
    operator has to clear it first."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-DUP")
    # Seed a colliding directory in the destination state folder.
    _seed_pbi(queue_repo, tmp_path, "inbox", "WI-DUP", claim_instance_id=None)
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue-test-ralph")], check=True)
    _configure_identity(workspace / "queue-test-ralph")
    tip_before = _git(workspace / "queue-test-ralph", "ls-remote", "origin", "ralph-queue").strip()

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-DUP",
            workspace=workspace,
            queue_repo=queue_repo,
            to_state="inbox",
        )
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "already exists" in stderr

    tip_after = _git(workspace / "queue-test-ralph", "ls-remote", "origin", "ralph-queue").strip()
    assert tip_before == tip_after, "destination-collision refusal must not push"


def test_recover_refuses_when_halt_sentinel_active(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ralph-recover refuses to run while the halt sentinel is active.
    Plan: exit 4 with ``ralph-recover: halt sentinel active``."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-HALTED")

    # Pre-clone + drop an unacknowledged halt sentinel into the local
    # clone. acquire_queue_clone's git fetch + pull --ff-only preserves
    # untracked files so the sentinel survives the refresh.
    clone = workspace / "queue-test-ralph"
    subprocess.run(["git", "clone", queue_repo, str(clone)], check=True)
    _configure_identity(clone)
    _write_halt_sentinel(clone)
    tip_before = _git(clone, "ls-remote", "origin", "ralph-queue").strip()

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-HALTED",
            workspace=workspace,
            queue_repo=queue_repo,
            to_state="inbox",
        )
    )
    assert exit_code == recover_module.EXIT_HALT_GUARD == 4
    stderr = capsys.readouterr().err
    assert "ralph-recover: halt sentinel active" in stderr

    tip_after = _git(clone, "ls-remote", "origin", "ralph-queue").strip()
    assert tip_before == tip_after, "halt-guard refusal must not push"
    # The from_dir must still be in current/ — no half-applied move.
    assert (clone / ".ralph" / "current" / "WI-HALTED").is_dir()


# ----------------------------------------------------------------------
# Audit + commit-shape tests
# ----------------------------------------------------------------------


def test_recover_stderr_audits_claim_contents_before_move(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The skill prints the CLAIM.json payload to stderr BEFORE the
    destructive move so the operator has a forensic copy of what was
    overwritten. Asserts the audit line is present in stderr and the
    payload contains the foreign instance id."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-AUDIT")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue-test-ralph")], check=True)
    _configure_identity(workspace / "queue-test-ralph")

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-AUDIT",
            workspace=workspace,
            queue_repo=queue_repo,
            to_state="inbox",
        )
    )
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "ralph-recover: claim contents before move:" in captured.err
    # The dumped payload is the literal CLAIM.json text — it should
    # carry the foreign instance_id key and the seed hostname.
    assert f'"instance_id": "{FOREIGN_INSTANCE_ID}"' in captured.err
    assert '"hostname": "seed-host"' in captured.err


def test_recover_commit_subject_pinned(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The recover commit subject is pinned to
    ``chore(queue): recover <id> from <previous-owner>``. Per
    [[d62c9e9e]] the test asserts the exact subject on the pushed tip
    (not just that the tip advanced)."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-SUBJ")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue-test-ralph")], check=True)
    _configure_identity(workspace / "queue-test-ralph")

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-SUBJ",
            workspace=workspace,
            queue_repo=queue_repo,
            to_state="blocked",
        )
    )
    assert exit_code == 0, capsys.readouterr().err

    verify = _verify_clone(tmp_path, queue_repo)
    latest = _git(verify, "log", "-1", "--pretty=%s").strip()
    assert latest == f"chore(queue): recover WI-SUBJ from {FOREIGN_INSTANCE_ID}"

    # One commit only — the previous commit must be the test's seed
    # commit, not a stack of intermediate writes.
    prev = _git(verify, "log", "-1", "--skip=1", "--pretty=%s").strip()
    assert prev == "chore(test): seed WI-SUBJ in current"


def test_recover_destination_carries_no_claim_json(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    recover_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Per the multi-ralph invariant, CLAIM.json may live ONLY under
    current/. The post-recover destination must therefore not carry a
    CLAIM.json — neither on the local clone nor on origin. Explicit
    coverage so a future regression that forgets to stage the deletion
    is caught."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-NOCLAIM-DEST")
    clone = workspace / "queue-test-ralph"
    subprocess.run(["git", "clone", queue_repo, str(clone)], check=True)
    _configure_identity(clone)

    exit_code = recover_module.main(
        _argv(
            pbi_id="WI-NOCLAIM-DEST",
            workspace=workspace,
            queue_repo=queue_repo,
            to_state="inbox",
        )
    )
    assert exit_code == 0, capsys.readouterr().err

    # No CLAIM.json on the local clone's destination.
    assert not (clone / ".ralph" / "inbox" / "WI-NOCLAIM-DEST" / "CLAIM.json").exists()
    # And none on the pushed remote either.
    verify = _verify_clone(tmp_path, queue_repo)
    assert not (verify / ".ralph" / "inbox" / "WI-NOCLAIM-DEST" / "CLAIM.json").exists()
    # The CLAIM.json must also be absent from the recover commit's
    # tree — guards against a stale committed copy slipping through.
    tree = _git(verify, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    assert ".ralph/inbox/WI-NOCLAIM-DEST/CLAIM.json" not in tree
