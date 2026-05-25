"""Tests for ``ralph_executor.claude_spawn``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import (
    classify_outcome,
    spawn_claude_p,
)
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import FilesystemQueueSource
from ralph_executor.queue.movements import move_inbox_to_current
from ralph_executor.types import PBI
from tests.executor.conftest import write_claude_script, write_sample_pbi


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    ).stdout


def _setup_current_pbi(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> PBI:
    _git(fake_repo, "checkout", "ralph-queue")
    write_sample_pbi(fake_repo, pbi_id="WI-1234")
    _git(fake_repo, "add", ".ralph/inbox/WI-1234")
    _git(fake_repo, "commit", "-m", "inbox: WI-1234")
    _git(fake_repo, "push", "origin", "ralph-queue")
    source = FilesystemQueueSource(cfg_for_repo)
    pbi = source.inbox_pbis()[0]
    return move_inbox_to_current(cfg_for_repo, pbi)


def test_spawn_invokes_claude_with_pbi_context(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
) -> None:
    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    write_claude_script(
        fake_claude_binary,
        "import sys\nprint('argv=' + ' '.join(sys.argv[1:]))\nsys.exit(0)\n",
    )
    outcome = spawn_claude_p(cfg_for_repo, pbi)
    assert outcome.exit_code == 0
    assert "argv=" in outcome.stdout
    # The spawned process should at least see ``-p`` somewhere in argv.
    assert "-p" in outcome.stdout


def test_classify_pr_created_when_pr_url_provided(tmp_path: Path) -> None:
    """pr_url is the source of truth — set by spawn_claude_p via the
    real gh CLI call. classify_outcome itself just maps the answer."""
    pbi_dir = tmp_path / "WI-1"
    pbi_dir.mkdir()
    outcome = classify_outcome(
        pbi_dir=pbi_dir,
        stdout="Did the work.\n",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
        pr_url="https://github.com/example/repo/pull/4711",
    )
    assert outcome.kind == "pr_created"
    assert outcome.pr_url == "https://github.com/example/repo/pull/4711"


def test_classify_pr_created_via_pr_lookup_callback(tmp_path: Path) -> None:
    """pr_lookup is the deferred form — called only when pr_url not given.
    Lets sweep/tests inject a different resolution strategy."""
    pbi_dir = tmp_path / "WI-1"
    pbi_dir.mkdir()
    outcome = classify_outcome(
        pbi_dir=pbi_dir,
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
        pr_lookup=lambda: "https://github.com/example/repo/pull/777",
    )
    assert outcome.kind == "pr_created"
    assert outcome.pr_url == "https://github.com/example/repo/pull/777"


def test_classify_partial_when_stdout_marker_but_no_real_pr(tmp_path: Path) -> None:
    """Source of truth is now the git host (gh CLI), not Claude's stdout.
    A run that exits zero with a marker-looking line but no actual open
    PR is classified `partial`."""
    pbi_dir = tmp_path / "WI-1"
    pbi_dir.mkdir()
    outcome = classify_outcome(
        pbi_dir=pbi_dir,
        stdout="PR created: https://made-up/url\n",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
        pr_url=None,
    )
    assert outcome.kind == "partial"
    assert outcome.pr_url is None


def test_classify_stuck_when_stuck_md_present(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-2"
    pbi_dir.mkdir()
    (pbi_dir / "STUCK.md").write_text("# stuck\n", encoding="utf-8")
    outcome = classify_outcome(
        pbi_dir=pbi_dir,
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=0.5,
    )
    assert outcome.kind == "stuck"
    assert outcome.pr_url is None


def test_classify_partial_when_nothing_special(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-3"
    pbi_dir.mkdir()
    outcome = classify_outcome(
        pbi_dir=pbi_dir,
        stdout="Did some work but not done yet.\n",
        stderr="",
        exit_code=0,
        duration_seconds=2.0,
    )
    assert outcome.kind == "partial"


def test_classify_error_when_exit_code_nonzero(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-4"
    pbi_dir.mkdir()
    outcome = classify_outcome(
        pbi_dir=pbi_dir,
        stdout="",
        stderr="boom\n",
        exit_code=137,
        duration_seconds=0.1,
    )
    assert outcome.kind == "error"


def test_stuck_takes_precedence_over_pr_created(tmp_path: Path) -> None:
    # If Ralph wrote STUCK.md before exiting, that's the truth even if
    # the stdout contains a stale PR-created line from an earlier step.
    pbi_dir = tmp_path / "WI-5"
    pbi_dir.mkdir()
    (pbi_dir / "STUCK.md").write_text("# stuck mid-run\n", encoding="utf-8")
    outcome = classify_outcome(
        pbi_dir=pbi_dir,
        stdout="PR created: https://example/pullrequest/1\n",
        stderr="",
        exit_code=0,
        duration_seconds=0.3,
    )
    assert outcome.kind == "stuck"


def test_spawn_records_stdout_stderr_and_exit_code(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
) -> None:
    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    write_claude_script(
        fake_claude_binary,
        "import sys\n"
        "sys.stdout.write('hello-stdout\\n')\n"
        "sys.stderr.write('hello-stderr\\n')\n"
        "sys.exit(2)\n",
    )
    outcome = spawn_claude_p(cfg_for_repo, pbi)
    assert outcome.exit_code == 2
    assert "hello-stdout" in outcome.stdout
    assert "hello-stderr" in outcome.stderr
    assert outcome.duration_seconds >= 0


def test_spawn_simulates_pr_creation(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spawn_claude_p resolves PR state via _query_open_pr_via_gh AFTER
    the claude subprocess exits. Stub the helper to return a fake PR
    URL — no real gh CLI call needed."""
    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    write_claude_script(
        fake_claude_binary,
        "print('done')\n",
    )

    def _fake_gh_lookup(repo_path: Path, branch: str) -> str | None:
        assert branch == f"ralph/{pbi.id}"
        return "https://github.com/example/repo/pull/9999"

    monkeypatch.setattr("ralph_executor.claude_spawn._query_open_pr_via_gh", _fake_gh_lookup)
    outcome = spawn_claude_p(cfg_for_repo, pbi)
    assert outcome.kind == "pr_created"
    assert outcome.pr_url == "https://github.com/example/repo/pull/9999"


def test_spawn_simulates_stuck(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
) -> None:
    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    write_claude_script(
        fake_claude_binary,
        "from pathlib import Path\n"
        "import sys, os\n"
        "# The PBI dir is passed via the RALPH_PBI_DIR env var by the spawner.\n"
        "pbi_dir = Path(os.environ['RALPH_PBI_DIR'])\n"
        "(pbi_dir / 'STUCK.md').write_text('# stuck\\n')\n"
        "sys.exit(0)\n",
    )
    outcome = spawn_claude_p(cfg_for_repo, pbi)
    assert outcome.kind == "stuck"
