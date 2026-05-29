"""Tests for the ``ralph-triage`` skill's entry script.

``ralph-triage`` walks ``.ralph/blocked/`` in the queue clone. For each
PBI the human decides whether to:

- return it to ``.ralph/inbox/`` (with ``attempts`` reset to 0 and a
  triage note appended to HISTORY.md), or
- close it out by moving it to ``.ralph/archive/`` (the archive folder
  is created on demand) with a final HISTORY.md entry.

Tests build a bare git repo as the queue remote, seed PBIs onto its
``main`` branch, and verify each behaviour without any network or real
Git host.
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
SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-triage" / "scripts" / "triage.py"


@pytest.fixture(scope="module")
def triage_module() -> ModuleType:
    assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("ralph_triage_script", SCRIPT_PATH)
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
    _git(repo, "config", "user.email", "ralph-triage@example.com")
    _git(repo, "config", "user.name", "ralph-triage")


@pytest.fixture
def queue_env(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """Build a bare queue remote + an empty workspace dir.

    The bare remote is seeded with an empty ``main`` so
    ``ensure_queue_clone`` can clone it cleanly. Returns
    ``(workspace_root, queue_repo_url)`` for the test to pass into
    ``ralph-triage`` via ``--workspace`` / ``--queue-repo``.
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


def _seed_blocked_pbi(
    bare_url: str,
    tmp_path: Path,
    pbi_id: str,
    *,
    entry_file: str = "PBI.md",
    pbi_type: str = "feature",
    attempts: int = 3,
    with_stuck: bool = True,
) -> None:
    """Clone the bare remote, add a blocked PBI, push back."""
    work = tmp_path / f"seed-{pbi_id}"
    subprocess.run(["git", "clone", bare_url, str(work)], check=True, capture_output=True)
    _configure_identity(work)
    pbi_dir = work / ".ralph" / "blocked" / pbi_id
    pbi_dir.mkdir(parents=True)
    (pbi_dir / entry_file).write_text(
        "---\n"
        f"id: {pbi_id}\n"
        f"type: {pbi_type}\n"
        "status: blocked\n"
        "severity: normal\n"
        f"attempts: {attempts}\n"
        'created_at: "2026-05-18T08:00:00+00:00"\n'
        'updated_at: "2026-05-22T09:30:00+00:00"\n'
        "---\n"
        "\n"
        "# body text\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text(
        "- timestamp: 2026-05-22T09:30:00+00:00\n"
        "- actor: ralph-executor\n"
        "- action: blocked\n"
        "- detail: attempt 3 exhausted\n",
        encoding="utf-8",
    )
    if with_stuck:
        (pbi_dir / "STUCK.md").write_text(
            "# Stuck\n\n"
            "what I tried: rebuilt the index three times.\n"
            "what would unblock me: a working repro of the failure.\n",
            encoding="utf-8",
        )
    _git(work, "add", f".ralph/blocked/{pbi_id}")
    _git(work, "commit", "-m", f"chore(test): seed blocked {pbi_id}")
    _git(work, "push", "origin", "ralph-queue")


def _seed_pbi_at_state(
    bare_url: str,
    tmp_path: Path,
    state_folder: str,
    pbi_id: str,
) -> None:
    """Seed a PBI at an arbitrary state folder on the bare remote."""
    work = tmp_path / f"seed-{pbi_id}-{state_folder}"
    subprocess.run(["git", "clone", bare_url, str(work)], check=True, capture_output=True)
    _configure_identity(work)
    pbi_dir = work / ".ralph" / state_folder / pbi_id
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "PBI.md").write_text(
        "---\n"
        f"id: {pbi_id}\n"
        "type: feature\n"
        f"status: {state_folder}\n"
        "severity: normal\n"
        "attempts: 0\n"
        'created_at: "2026-05-24T10:00:00+00:00"\n'
        'updated_at: "2026-05-24T10:00:00+00:00"\n'
        "---\n"
        "\n"
        "# body\n",
        encoding="utf-8",
    )
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
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
    destination: str,
    note: str,
    extra: list[str] | None = None,
) -> list[str]:
    argv = [
        "--pbi-id",
        pbi_id,
        "--to",
        destination,
        "--note",
        note,
        "--workspace",
        str(workspace),
        "--queue-repo",
        queue_repo,
    ]
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


def test_triage_to_inbox_resets_attempts_and_moves(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_blocked_pbi(queue_repo, tmp_path, "WI-700", attempts=3)
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = triage_module.main(
        _argv(
            pbi_id="WI-700",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
            note="tried a fresh REPRODUCE.md from QA; reset and retry",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-700"
    assert payload["destination"] == "inbox"
    assert payload["previous_state_folder"] == "blocked"
    assert payload["attempts_reset_to_zero"] is True
    assert payload["pushed"] is True
    assert payload["commit_sha"]
    assert payload["already_triaged"] is False

    verify = _verify_clone(tmp_path, queue_repo)
    assert not (verify / ".ralph" / "blocked" / "WI-700").exists()
    moved = verify / ".ralph" / "inbox" / "WI-700" / "PBI.md"
    assert moved.is_file()
    fm = _read_frontmatter(moved)
    assert fm["attempts"] == 0
    assert fm["status"] == "inbox"
    assert fm["updated_at"] != "2026-05-22T09:30:00+00:00"

    history = (verify / ".ralph" / "inbox" / "WI-700" / "HISTORY.md").read_text(encoding="utf-8")
    assert "ralph-triage" in history
    assert "return-to-inbox" in history
    assert "tried a fresh REPRODUCE.md from QA" in history

    subject = _git(verify, "log", "-1", "--pretty=%s").strip()
    assert subject == "chore(queue): triage WI-700 (blocked -> inbox)"


def test_triage_to_archive_creates_archive_folder(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_blocked_pbi(queue_repo, tmp_path, "WI-800", attempts=4)
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = triage_module.main(
        _argv(
            pbi_id="WI-800",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="archive",
            note="ambiguous scope; closing out, BA will re-submit if needed",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["destination"] == "archive"
    assert payload["archive_created"] is True
    assert payload["attempts_reset_to_zero"] is False

    verify = _verify_clone(tmp_path, queue_repo)
    assert (verify / ".ralph" / "archive").is_dir()
    assert not (verify / ".ralph" / "blocked" / "WI-800").exists()
    moved = verify / ".ralph" / "archive" / "WI-800" / "PBI.md"
    assert moved.is_file()
    fm = _read_frontmatter(moved)
    assert fm["status"] == "archive"
    assert fm["attempts"] == 4

    history = (verify / ".ralph" / "archive" / "WI-800" / "HISTORY.md").read_text(encoding="utf-8")
    assert "ralph-triage" in history
    assert "archive" in history
    assert "ambiguous scope" in history


def test_triage_bug_pbi_uses_bug_md(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_blocked_pbi(
        queue_repo,
        tmp_path,
        "BUG-1",
        entry_file="BUG.md",
        pbi_type="bug",
    )
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = triage_module.main(
        _argv(
            pbi_id="BUG-1",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
            note="repro now available",
        )
    )
    assert exit_code == 0, capsys.readouterr().err

    verify = _verify_clone(tmp_path, queue_repo)
    moved = verify / ".ralph" / "inbox" / "BUG-1" / "BUG.md"
    assert moved.is_file()
    fm = _read_frontmatter(moved)
    assert fm["status"] == "inbox"
    assert fm["attempts"] == 0
    assert fm["type"] == "bug"


def test_triage_rejects_unknown_destination(
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
) -> None:
    workspace, queue_repo = queue_env

    with pytest.raises(SystemExit) as exc:
        triage_module.main(
            _argv(
                pbi_id="WI-900",
                workspace=workspace,
                queue_repo=queue_repo,
                destination="trash",
                note="no",
            )
        )
    assert exc.value.code == 2


def test_triage_errors_on_missing_pbi(
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = triage_module.main(
        _argv(
            pbi_id="WI-NOPE",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
            note="missing",
        )
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "not found" in stderr or "blocked" in stderr


def test_triage_rejects_pbi_outside_blocked(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi_at_state(queue_repo, tmp_path, "inbox", "WI-1000")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = triage_module.main(
        _argv(
            pbi_id="WI-1000",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="archive",
            note="won't work",
        )
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "blocked" in stderr


def test_triage_requires_note(
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
) -> None:
    workspace, queue_repo = queue_env

    with pytest.raises(SystemExit) as exc:
        triage_module.main(
            [
                "--pbi-id",
                "WI-1100",
                "--to",
                "inbox",
                "--workspace",
                str(workspace),
                "--queue-repo",
                queue_repo,
            ]
        )
    assert exc.value.code == 2


def test_triage_dry_run_writes_nothing(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_blocked_pbi(queue_repo, tmp_path, "WI-1200")
    before = subprocess.run(
        ["git", "ls-remote", queue_repo, "ralph-queue"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()

    exit_code = triage_module.main(
        _argv(
            pbi_id="WI-1200",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
            note="would-be note",
            extra=["--dry-run"],
        )
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


def test_triage_dry_run_archive_reports_conservative_archive_created(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run never clones, so it cannot tell whether .ralph/archive is
    already in HEAD. The previous implementation set archive_created to
    `destination == "archive"` unconditionally, which lied when the
    folder already existed. The conservative report is False."""
    workspace, queue_repo = queue_env
    _seed_blocked_pbi(queue_repo, tmp_path, "WI-1250")

    exit_code = triage_module.main(
        _argv(
            pbi_id="WI-1250",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="archive",
            note="closing out (dry-run)",
            extra=["--dry-run"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["destination"] == "archive"
    assert payload["archive_created"] is False
    assert not (workspace / "queue").exists()


def test_triage_no_push_keeps_remote_unchanged(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_blocked_pbi(queue_repo, tmp_path, "WI-1300")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")
    before = _git(workspace / "queue", "ls-remote", "origin", "ralph-queue").strip()

    exit_code = triage_module.main(
        _argv(
            pbi_id="WI-1300",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="archive",
            note="closing out",
            extra=["--no-push"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pushed"] is False
    assert payload["commit_sha"]
    after = _git(workspace / "queue", "ls-remote", "origin", "ralph-queue").strip()
    assert before == after


def test_triage_idempotent_when_already_at_destination(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If HEAD already shows the PBI at the destination state, the
    operation is a no-op — already_triaged=True, no new commit."""
    workspace, queue_repo = queue_env
    _seed_pbi_at_state(queue_repo, tmp_path, "inbox", "WI-1400")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = triage_module.main(
        _argv(
            pbi_id="WI-1400",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
            note="already there",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["already_triaged"] is True
    assert payload["commit_sha"] == ""
    assert payload["pushed"] is False


def test_triage_refuses_when_destination_dir_exists(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Destination dir already in the working tree (but not HEAD) is a
    half-done move from a prior aborted run. Refuse so a human inspects
    rather than silently overwriting."""
    workspace, queue_repo = queue_env
    _seed_blocked_pbi(queue_repo, tmp_path, "WI-1500")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")
    stale = workspace / "queue" / ".ralph" / "inbox" / "WI-1500"
    stale.mkdir(parents=True)
    (stale / "PBI.md").write_text("stale", encoding="utf-8")

    exit_code = triage_module.main(
        _argv(
            pbi_id="WI-1500",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
            note="should refuse",
            extra=["--no-push"],
        )
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "already exists" in stderr or "exists" in stderr


def test_triage_errors_when_queue_repo_unset(
    tmp_path: Path,
    triage_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without --queue-repo and without TOML queue_repo, exit 2 with a clear error."""
    workspace = tmp_path / "ws"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    exit_code = triage_module.main(
        [
            "--pbi-id",
            "WI-1",
            "--to",
            "inbox",
            "--note",
            "x",
            "--workspace",
            str(workspace),
        ]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "queue_repo" in err.lower()


def test_triage_queue_repo_resolved_from_toml(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When --queue-repo is omitted, the TOML value is used."""
    workspace, queue_repo = queue_env
    _seed_blocked_pbi(queue_repo, tmp_path, "WI-1600")

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

    exit_code = triage_module.main(
        [
            "--pbi-id",
            "WI-1600",
            "--to",
            "inbox",
            "--note",
            "from TOML",
            "--workspace",
            str(workspace),
            "--no-push",
        ]
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-1600"
    assert payload["commit_sha"] != ""


def test_triage_pushes_ralph_queue_by_default(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    triage_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ralph-triage pushes to ralph-queue when no --queue-branch override."""
    workspace, queue_repo = queue_env
    _seed_blocked_pbi(queue_repo, tmp_path, "WI-7200")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    pushed: list[tuple[Path, str]] = []

    def fake_push(repo: Path, branch: str) -> None:
        pushed.append((repo, branch))

    monkeypatch.setattr(triage_module, "push", fake_push)

    exit_code = triage_module.main(
        _argv(
            pbi_id="WI-7200",
            workspace=workspace,
            queue_repo=queue_repo,
            destination="inbox",
            note="default-branch test",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    assert [branch for _repo, branch in pushed] == ["ralph-queue"]
