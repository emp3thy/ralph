"""Tests for the ``ralph-promote`` skill's entry script.

``ralph-promote`` moves a PBI directory between state folders in the
queue clone (e.g. ``inbox/`` → ``current/``), updates the entry file's
``status`` + ``updated_at`` frontmatter, commits, and pushes ``main``
to the queue remote. It is the operator's manual override for the
executor's automatic state transitions.

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
SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-promote" / "scripts" / "promote.py"


@pytest.fixture(scope="module")
def promote_module() -> ModuleType:
    assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("ralph_promote_script", SCRIPT_PATH)
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
    _git(repo, "config", "user.email", "ralph-promote@example.com")
    _git(repo, "config", "user.name", "ralph-promote")


@pytest.fixture
def queue_env(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """Build a bare queue remote + an empty workspace dir.

    The bare remote is seeded with an empty ``main`` so
    ``ensure_queue_clone`` can clone it cleanly. Returns
    ``(workspace_root, queue_repo_url)`` for the test to pass into
    ``ralph-promote`` via ``--workspace`` / ``--queue-repo``.
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
    pbi_type: str = "feature",
) -> None:
    """Clone the bare remote, add a PBI under ``.ralph/<state>/<id>``, push back."""
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
    from_state: str,
    to_state: str,
    extra: list[str] | None = None,
) -> list[str]:
    argv = [
        "--pbi-id",
        pbi_id,
        "--from",
        from_state,
        "--to",
        to_state,
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


def test_promote_moves_pbi_between_states(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "inbox", "WI-100")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = promote_module.main(
        _argv(
            pbi_id="WI-100",
            workspace=workspace,
            queue_repo=queue_repo,
            from_state="inbox",
            to_state="current",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-100"
    assert payload["from_state"] == "inbox"
    assert payload["to_state"] == "current"
    assert payload["pushed"] is True
    assert payload["commit_sha"]
    assert payload["already_promoted"] is False

    verify = _verify_clone(tmp_path, queue_repo)
    assert not (verify / ".ralph" / "inbox" / "WI-100").exists()
    moved = verify / ".ralph" / "current" / "WI-100" / "PBI.md"
    assert moved.is_file()
    fm = _read_frontmatter(moved)
    assert fm["status"] == "current"
    assert "updated_at" in fm

    subject = _git(verify, "log", "-1", "--pretty=%s").strip()
    assert subject == "chore(queue): promote WI-100 (inbox -> current)"


def test_promote_bug_pbi_uses_bug_md(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "blocked", "BUG-1", entry_file="BUG.md", pbi_type="bug")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = promote_module.main(
        _argv(
            pbi_id="BUG-1",
            workspace=workspace,
            queue_repo=queue_repo,
            from_state="blocked",
            to_state="inbox",
        )
    )
    assert exit_code == 0, capsys.readouterr().err

    verify = _verify_clone(tmp_path, queue_repo)
    moved = verify / ".ralph" / "inbox" / "BUG-1" / "BUG.md"
    assert moved.is_file()
    fm = _read_frontmatter(moved)
    assert fm["status"] == "inbox"
    assert fm["type"] == "bug"


def test_promote_errors_on_missing_pbi_at_from_state(
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = promote_module.main(
        _argv(
            pbi_id="WI-NOPE",
            workspace=workspace,
            queue_repo=queue_repo,
            from_state="inbox",
            to_state="current",
        )
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "not found" in stderr or "no such" in stderr or "missing" in stderr


def test_promote_refuses_same_from_and_to(
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env

    exit_code = promote_module.main(
        _argv(
            pbi_id="WI-1",
            workspace=workspace,
            queue_repo=queue_repo,
            from_state="inbox",
            to_state="inbox",
        )
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "differ" in stderr or "same" in stderr


def test_promote_refuses_unknown_state(
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env

    with pytest.raises(SystemExit) as exc:
        promote_module.main(
            _argv(
                pbi_id="WI-1",
                workspace=workspace,
                queue_repo=queue_repo,
                from_state="inbox",
                to_state="bogus",
            )
        )
    assert exc.value.code == 2


def test_promote_no_push_keeps_remote_unchanged(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "inbox", "WI-200")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")
    before = _git(workspace / "queue", "ls-remote", "origin", "ralph-queue").strip()

    exit_code = promote_module.main(
        _argv(
            pbi_id="WI-200",
            workspace=workspace,
            queue_repo=queue_repo,
            from_state="inbox",
            to_state="current",
            extra=["--no-push"],
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pushed"] is False
    assert payload["commit_sha"]
    after = _git(workspace / "queue", "ls-remote", "origin", "ralph-queue").strip()
    assert before == after


def test_promote_dry_run_writes_nothing(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "inbox", "WI-300")
    before = subprocess.run(
        ["git", "ls-remote", queue_repo, "ralph-queue"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()

    exit_code = promote_module.main(
        _argv(
            pbi_id="WI-300",
            workspace=workspace,
            queue_repo=queue_repo,
            from_state="inbox",
            to_state="current",
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


def test_promote_idempotent_when_already_at_destination(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If HEAD already shows the PBI at the destination state, the
    operation is a no-op — already_promoted=True, no new commit."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "current", "WI-400")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = promote_module.main(
        _argv(
            pbi_id="WI-400",
            workspace=workspace,
            queue_repo=queue_repo,
            from_state="inbox",
            to_state="current",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["already_promoted"] is True
    assert payload["commit_sha"] == ""
    assert payload["pushed"] is False


def test_promote_appends_history_entry(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "inbox", "WI-500")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    exit_code = promote_module.main(
        _argv(
            pbi_id="WI-500",
            workspace=workspace,
            queue_repo=queue_repo,
            from_state="inbox",
            to_state="current",
        )
    )
    assert exit_code == 0, capsys.readouterr().err

    verify = _verify_clone(tmp_path, queue_repo)
    history = (verify / ".ralph" / "current" / "WI-500" / "HISTORY.md").read_text(encoding="utf-8")
    assert "ralph-promote" in history
    assert "inbox -> current" in history


def test_promote_errors_when_queue_repo_unset(
    tmp_path: Path,
    promote_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without --queue-repo and without TOML queue_repo, exit 2 with a clear error."""
    workspace = tmp_path / "ws"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    exit_code = promote_module.main(
        [
            "--pbi-id",
            "WI-1",
            "--from",
            "inbox",
            "--to",
            "current",
            "--workspace",
            str(workspace),
        ]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "queue_repo" in err.lower()


def test_promote_queue_repo_resolved_from_toml(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When --queue-repo is omitted, the TOML value is used."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "inbox", "WI-600")

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

    exit_code = promote_module.main(
        [
            "--pbi-id",
            "WI-600",
            "--from",
            "inbox",
            "--to",
            "current",
            "--workspace",
            str(workspace),
            "--no-push",
        ]
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["pbi_id"] == "WI-600"
    assert payload["commit_sha"] != ""


def test_promote_refuses_when_destination_dir_exists(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If the destination state already has a directory for this PBI
    (in the working tree but not yet in HEAD, e.g. a stale half-done
    move), refuse — the operator must clean up by hand. This also
    guards against silent data loss if git mv would error."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "inbox", "WI-700")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")
    # Pre-create the destination dir locally to simulate the conflict.
    stale = workspace / "queue" / ".ralph" / "current" / "WI-700"
    stale.mkdir(parents=True)
    (stale / "PBI.md").write_text("stale", encoding="utf-8")

    exit_code = promote_module.main(
        _argv(
            pbi_id="WI-700",
            workspace=workspace,
            queue_repo=queue_repo,
            from_state="inbox",
            to_state="current",
            extra=["--no-push"],
        )
    )
    assert exit_code == 2
    stderr = capsys.readouterr().err.lower()
    assert "already exists" in stderr or "exists" in stderr


def test_promote_pushes_ralph_queue_by_default(
    tmp_path: Path,
    queue_env: tuple[Path, str],
    promote_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ralph-promote pushes to ralph-queue when no --queue-branch override."""
    workspace, queue_repo = queue_env
    _seed_pbi(queue_repo, tmp_path, "inbox", "WI-7100")
    subprocess.run(["git", "clone", queue_repo, str(workspace / "queue")], check=True)
    _configure_identity(workspace / "queue")

    pushed: list[tuple[Path, str]] = []

    def fake_push(repo: Path, branch: str) -> None:
        pushed.append((repo, branch))

    monkeypatch.setattr(promote_module, "push", fake_push)

    exit_code = promote_module.main(
        _argv(
            pbi_id="WI-7100",
            workspace=workspace,
            queue_repo=queue_repo,
            from_state="inbox",
            to_state="current",
        )
    )
    assert exit_code == 0, capsys.readouterr().err
    assert [branch for _repo, branch in pushed] == ["ralph-queue"]
