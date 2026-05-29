"""Tests for the ``ralph-status`` skill's entry script.

``ralph-status`` now reads the single queue clone at
``<workspace_root>/queue/`` (always on ``main``). Tests build a bare git
repo as the "queue remote", seed PBIs into it from a throw-away seed
clone with distinct ``target_repo`` frontmatter values, then run the
skill with ``--workspace`` / ``--queue-repo`` and verify the rendered
output. No network, no real Git host.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "skills" / "ralph-status" / "scripts" / "status.py"

# Distinct target_repo URLs so we can test grouping / filtering.
TARGET_AUTH = "https://github.com/emp3thy/svc-auth"
TARGET_BILLING = "https://github.com/emp3thy/svc-billing"

WI_FEATURE = "WI-1234"
WI_BUG = "BUG-rosa"
WI_CURRENT = "WI-1235"
WI_PENDING = "WI-980"
WI_BLOCKED = "WI-3000"


def _pbi_md(
    *,
    pbi_id: str,
    pbi_type: str = "feature",
    status: str = "inbox",
    severity: str = "normal",
    target_repo: str = TARGET_AUTH,
    attempts: int = 0,
    created_at: str = "2026-05-24T09:15:00+00:00",
    updated_at: str = "2026-05-24T09:15:00+00:00",
    title: str = "Seed PBI",
) -> str:
    return textwrap.dedent(f"""\
        ---
        id: {pbi_id}
        type: {pbi_type}
        status: {status}
        severity: {severity}
        attempts: {attempts}
        created_at: {created_at}
        updated_at: {updated_at}
        target_repo: {target_repo}
        ---

        # {title}

        Body.
        """)


MALFORMED_PBI_MD = "no frontmatter at all\n"


@pytest.fixture(scope="module")
def status_module() -> ModuleType:
    assert SCRIPT_PATH.is_file(), f"missing entry script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("ralph_status_script", SCRIPT_PATH)
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
    _git(repo, "config", "user.email", "ralph-status@example.com")
    _git(repo, "config", "user.name", "ralph-status")


@pytest.fixture
def queue_env(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """Build a bare queue remote (seeded with an empty ``main``) + an empty workspace.

    Returns ``(workspace_root, queue_repo_url)`` for the test to pass into
    ``ralph-status`` via ``--workspace`` / ``--queue-repo``.
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
    state: str,
    pbi_id: str,
    *,
    entry_file: str = "PBI.md",
    content: str | None = None,
) -> None:
    """Clone the bare remote, add a PBI under ``.ralph/<state>/<id>``, push back."""
    work = tmp_path / f"seed-{pbi_id}-{state}"
    subprocess.run(["git", "clone", bare_url, str(work)], check=True, capture_output=True)
    _configure_identity(work)
    pbi_dir = work / ".ralph" / state / pbi_id
    pbi_dir.mkdir(parents=True)
    text = content if content is not None else _pbi_md(pbi_id=pbi_id, status=state)
    (pbi_dir / entry_file).write_text(text, encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
    _git(work, "add", f".ralph/{state}/{pbi_id}")
    _git(work, "commit", "-m", f"chore(test): seed {pbi_id} in {state}")
    _git(work, "push", "origin", "ralph-queue")


@pytest.fixture
def seeded_queue(queue_env: tuple[Path, str], tmp_path: Path) -> tuple[Path, str]:
    """Queue with five PBIs spread across four states and two targets."""
    queue_repo = queue_env[1]
    _seed_pbi(
        queue_repo,
        tmp_path,
        "inbox",
        WI_FEATURE,
        content=_pbi_md(
            pbi_id=WI_FEATURE,
            status="inbox",
            target_repo=TARGET_AUTH,
            title="Add /healthz endpoint",
        ),
    )
    _seed_pbi(
        queue_repo,
        tmp_path,
        "inbox",
        WI_BUG,
        entry_file="BUG.md",
        content=_pbi_md(
            pbi_id=WI_BUG,
            pbi_type="bug",
            status="inbox",
            severity="critical",
            target_repo=TARGET_AUTH,
            created_at="2026-05-24T08:00:00+00:00",
            title="Pod crashloops on ROSA via IRSA",
        ),
    )
    _seed_pbi(
        queue_repo,
        tmp_path,
        "current",
        WI_CURRENT,
        content=_pbi_md(
            pbi_id=WI_CURRENT,
            status="current",
            severity="high",
            target_repo=TARGET_AUTH,
            attempts=1,
            created_at="2026-05-23T12:00:00+00:00",
            title="Onboarding refactor",
        ),
    )
    _seed_pbi(
        queue_repo,
        tmp_path,
        "pending-pr",
        WI_PENDING,
        content=_pbi_md(
            pbi_id=WI_PENDING,
            status="pending-pr",
            severity="high",
            target_repo=TARGET_BILLING,
            created_at="2026-05-20T09:15:00+00:00",
            title="Migrate invoices",
        ),
    )
    _seed_pbi(
        queue_repo,
        tmp_path,
        "blocked",
        WI_BLOCKED,
        content=_pbi_md(
            pbi_id=WI_BLOCKED,
            status="blocked",
            severity="normal",
            target_repo=TARGET_BILLING,
            attempts=3,
            created_at="2026-05-22T09:15:00+00:00",
            title="Stuck PBI",
        ),
    )
    return queue_env


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_default_lists_all_states_with_target_column(
    seeded_queue: tuple[Path, str],
    status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = seeded_queue
    exit_code = status_module.main(["--workspace", str(workspace), "--queue-repo", queue_repo])
    assert exit_code == 0, capsys.readouterr().err
    stdout = capsys.readouterr().out
    header_line = stdout.splitlines()[0]
    assert "TARGET" in header_line
    assert "REPO" not in header_line, "header should no longer carry the legacy REPO column"
    for column in ("STATE", "ID", "TYPE", "SEVERITY", "AGE", "TITLE"):
        assert column in header_line
    for pbi_id in (WI_FEATURE, WI_BUG, WI_CURRENT, WI_PENDING, WI_BLOCKED):
        assert pbi_id in stdout, f"missing {pbi_id} in:\n{stdout}"
    assert TARGET_AUTH in stdout
    assert TARGET_BILLING in stdout


def test_rows_grouped_contiguously_by_target_repo(
    seeded_queue: tuple[Path, str],
    status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """All rows for a single target_repo appear in a contiguous block."""
    workspace, queue_repo = seeded_queue
    exit_code = status_module.main(["--workspace", str(workspace), "--queue-repo", queue_repo])
    assert exit_code == 0
    stdout = capsys.readouterr().out
    lines = [line for line in stdout.splitlines() if line.strip()]
    auth_idx = [i for i, line in enumerate(lines) if TARGET_AUTH in line]
    billing_idx = [i for i, line in enumerate(lines) if TARGET_BILLING in line]
    assert auth_idx, "expected auth rows in output"
    assert billing_idx, "expected billing rows in output"
    contiguous = max(auth_idx) < min(billing_idx) or max(billing_idx) < min(auth_idx)
    assert contiguous, (
        f"target groups should be contiguous; auth_idx={auth_idx} billing_idx={billing_idx}"
    )


def test_state_filter_narrows_output(
    seeded_queue: tuple[Path, str],
    status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = seeded_queue
    exit_code = status_module.main(
        ["--workspace", str(workspace), "--queue-repo", queue_repo, "--state", "current"]
    )
    assert exit_code == 0, capsys.readouterr().err
    stdout = capsys.readouterr().out
    assert WI_CURRENT in stdout
    for pbi_id in (WI_FEATURE, WI_BUG, WI_PENDING, WI_BLOCKED):
        assert pbi_id not in stdout, f"unexpected {pbi_id} in filtered output:\n{stdout}"


def test_target_repo_filter_narrows_output(
    seeded_queue: tuple[Path, str],
    status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = seeded_queue
    exit_code = status_module.main(
        [
            "--workspace",
            str(workspace),
            "--queue-repo",
            queue_repo,
            "--target-repo",
            TARGET_BILLING,
        ]
    )
    assert exit_code == 0, capsys.readouterr().err
    stdout = capsys.readouterr().out
    for pbi_id in (WI_PENDING, WI_BLOCKED):
        assert pbi_id in stdout
    for pbi_id in (WI_FEATURE, WI_BUG, WI_CURRENT):
        assert pbi_id not in stdout


def test_json_envelope_shape(
    seeded_queue: tuple[Path, str],
    status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = seeded_queue
    exit_code = status_module.main(
        ["--workspace", str(workspace), "--queue-repo", queue_repo, "--json"]
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert set(payload.keys()) == {"rows", "errors"}, (
        f"envelope must be {{rows, errors}} with no 'repos' key, got {set(payload.keys())}"
    )
    assert isinstance(payload["rows"], list)
    assert isinstance(payload["errors"], list)
    assert len(payload["rows"]) == 5
    sample = payload["rows"][0]
    for key in (
        "target_repo",
        "state",
        "id",
        "type",
        "severity",
        "attempts",
        "created_at",
        "updated_at",
        "title",
        "pbi_dir",
        "error",
    ):
        assert key in sample, f"row missing key {key!r}: {sample}"
    assert "repo" not in sample, "legacy 'repo' field must not appear"
    assert "repo_name" not in sample, "legacy 'repo_name' field must not appear"
    targets = {row["target_repo"] for row in payload["rows"]}
    assert targets == {TARGET_AUTH, TARGET_BILLING}


def test_json_target_repo_filter(
    seeded_queue: tuple[Path, str],
    status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = seeded_queue
    exit_code = status_module.main(
        [
            "--workspace",
            str(workspace),
            "--queue-repo",
            queue_repo,
            "--json",
            "--target-repo",
            TARGET_AUTH,
        ]
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert all(row["target_repo"] == TARGET_AUTH for row in payload["rows"])
    ids = {row["id"] for row in payload["rows"]}
    assert ids == {WI_FEATURE, WI_BUG, WI_CURRENT}


def test_empty_queue_renders_header_only(
    queue_env: tuple[Path, str],
    status_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    exit_code = status_module.main(["--workspace", str(workspace), "--queue-repo", queue_repo])
    assert exit_code == 0, capsys.readouterr().err
    stdout = capsys.readouterr().out
    assert "TARGET" in stdout
    assert "WI-" not in stdout
    assert "BUG-" not in stdout


def test_malformed_pbi_becomes_error_row(
    queue_env: tuple[Path, str],
    status_module: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = queue_env
    _seed_pbi(
        queue_repo,
        tmp_path,
        "inbox",
        WI_FEATURE,
        content=_pbi_md(pbi_id=WI_FEATURE, status="inbox", title="Good PBI"),
    )
    _seed_pbi(
        queue_repo,
        tmp_path,
        "inbox",
        "WI-broken",
        content=MALFORMED_PBI_MD,
    )
    exit_code = status_module.main(
        ["--workspace", str(workspace), "--queue-repo", queue_repo, "--json"]
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    ids = {row["id"] for row in payload["rows"]}
    assert WI_FEATURE in ids
    assert "WI-broken" in ids
    broken = next(row for row in payload["rows"] if row["id"] == "WI-broken")
    assert broken["error"] is not None
    assert broken["target_repo"] is None
    assert broken["type"] is None


def test_target_repo_filter_preserves_error_rows(
    queue_env: tuple[Path, str],
    status_module: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed PBIs have no parseable target_repo, but the SKILL.md
    contract requires them to appear in `rows` so the caller sees parse
    failures. The --target-repo filter must pass them through.
    """
    workspace, queue_repo = queue_env
    _seed_pbi(
        queue_repo,
        tmp_path,
        "inbox",
        WI_FEATURE,
        content=_pbi_md(
            pbi_id=WI_FEATURE, status="inbox", target_repo=TARGET_AUTH, title="Auth PBI"
        ),
    )
    _seed_pbi(
        queue_repo,
        tmp_path,
        "inbox",
        WI_PENDING,
        content=_pbi_md(
            pbi_id=WI_PENDING, status="inbox", target_repo=TARGET_BILLING, title="Billing PBI"
        ),
    )
    _seed_pbi(
        queue_repo,
        tmp_path,
        "inbox",
        "WI-broken",
        content=MALFORMED_PBI_MD,
    )
    exit_code = status_module.main(
        [
            "--workspace",
            str(workspace),
            "--queue-repo",
            queue_repo,
            "--json",
            "--target-repo",
            TARGET_AUTH,
        ]
    )
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    ids = {row["id"] for row in payload["rows"]}
    # Filtered-out billing PBI is gone; matched auth PBI and the malformed
    # row (which cannot be filtered on target_repo) both survive.
    assert ids == {WI_FEATURE, "WI-broken"}


def test_unexpected_exception_in_render_returns_exit_2(
    queue_env: tuple[Path, str],
    status_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Per SKILL.md, top-level failures print to stderr and exit 2. A
    bug inside _render_table or any future change downstream must not
    bubble a raw traceback.
    """
    workspace, queue_repo = queue_env
    monkeypatch.setattr(
        status_module,
        "_render_table",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("synthetic render crash")),
    )
    exit_code = status_module.main(["--workspace", str(workspace), "--queue-repo", queue_repo])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "synthetic render crash" in err
    assert err.startswith("error: ")


def test_state_filter_rejects_unknown_state(
    seeded_queue: tuple[Path, str],
    status_module: ModuleType,
) -> None:
    workspace, queue_repo = seeded_queue
    with pytest.raises(SystemExit) as excinfo:
        status_module.main(
            [
                "--workspace",
                str(workspace),
                "--queue-repo",
                queue_repo,
                "--state",
                "limbo",
            ]
        )
    assert excinfo.value.code == 2


def test_errors_when_queue_repo_unset(
    tmp_path: Path,
    status_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "ws"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    exit_code = status_module.main(["--workspace", str(workspace)])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "queue_repo" in err.lower()


def test_queue_repo_resolved_from_toml(
    seeded_queue: tuple[Path, str],
    status_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace, queue_repo = seeded_queue
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    (fake_home / ".ralph").mkdir()
    queue_url = "file:///" + Path(queue_repo).as_posix().lstrip("/")
    (fake_home / ".ralph" / "config.toml").write_text(
        f'queue_repo = "{queue_url}"\n',
        encoding="utf-8",
    )

    exit_code = status_module.main(["--workspace", str(workspace), "--json"])
    assert exit_code == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["rows"]) == 5


def test_status_acquires_ralph_queue_by_default(
    seeded_queue: tuple[Path, str],
    status_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ralph-status calls acquire_queue_clone with queue_branch='ralph-queue' by default."""
    workspace, queue_repo = seeded_queue
    calls: list[tuple[Path, str, str]] = []

    real_acquire = status_module.acquire_queue_clone

    def fake_acquire(workspace_root: Path, repo: str, branch: str) -> Path:
        calls.append((workspace_root, repo, branch))
        return real_acquire(workspace_root, repo, branch)

    monkeypatch.setattr(status_module, "acquire_queue_clone", fake_acquire)

    exit_code = status_module.main(
        ["--workspace", str(workspace), "--queue-repo", queue_repo, "--json"]
    )
    assert exit_code == 0, capsys.readouterr().err
    assert [branch for _ws, _repo, branch in calls] == ["ralph-queue"]
