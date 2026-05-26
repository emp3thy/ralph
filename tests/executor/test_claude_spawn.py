"""Tests for ``ralph_executor.claude_spawn``."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import (
    _query_pr_checks,
    _wait_for_pr_checks,
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
    the claude subprocess exits, then gates pr_created on
    _wait_for_pr_checks reporting CI green. Stub both helpers — no real
    gh CLI call needed."""
    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    write_claude_script(
        fake_claude_binary,
        "print('done')\n",
    )

    def _fake_gh_lookup(_repo_path: Path, branch: str) -> str | None:
        assert branch == f"ralph/{pbi.id}"
        return "https://github.com/example/repo/pull/9999"

    def _fake_wait_for_pr_checks(
        _repo_path: Path,
        pr_number: int,
        *,
        max_polls: int,
        interval_seconds: float,
    ) -> tuple[str, list[str]]:
        assert pr_number == 9999
        # cfg_for_repo carries the defaults — confirm the cfg knobs reach
        # the verifier so the operator can actually shorten the budget.
        assert max_polls == cfg_for_repo.pr_check_poll_max_attempts
        assert interval_seconds == cfg_for_repo.pr_check_poll_interval_seconds
        return ("pass", [])

    monkeypatch.setattr("ralph_executor.claude_spawn._query_open_pr_via_gh", _fake_gh_lookup)
    monkeypatch.setattr("ralph_executor.claude_spawn._wait_for_pr_checks", _fake_wait_for_pr_checks)
    outcome = spawn_claude_p(cfg_for_repo, pbi)
    assert outcome.kind == "pr_created"
    assert outcome.pr_url == "https://github.com/example/repo/pull/9999"


def _fake_run_factory(
    returncode: int, stdout: str, stderr: str = ""
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a ``subprocess.run`` stand-in returning a fixed CompletedProcess.

    Patched into ``ralph_executor.claude_spawn.subprocess.run`` via
    string-form ``monkeypatch.setattr`` so ``_query_pr_checks`` exercises
    its parsing without hitting the real gh CLI.
    """

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["gh", "pr", "checks"],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    return _fake_run


def test_query_pr_checks_returns_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = '[{"bucket":"pass","name":"build"},{"bucket":"pass","name":"lint"}]'
    monkeypatch.setattr("ralph_executor.claude_spawn.subprocess.run", _fake_run_factory(0, payload))
    state, names = _query_pr_checks(tmp_path, 42)
    assert state == "pass"
    assert names == []


def test_query_pr_checks_returns_fail_with_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gh exits 1 when a required check has failed, but the JSON array on
    stdout is still authoritative — _query_pr_checks must parse it."""
    payload = (
        '[{"bucket":"pass","name":"build"},'
        '{"bucket":"fail","name":"unit-tests"},'
        '{"bucket":"fail","name":"lint"}]'
    )
    monkeypatch.setattr("ralph_executor.claude_spawn.subprocess.run", _fake_run_factory(1, payload))
    state, names = _query_pr_checks(tmp_path, 42)
    assert state == "fail"
    assert names == ["unit-tests", "lint"]


def test_query_pr_checks_returns_pending_on_exit_8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gh exits 8 with empty stdout when a required check is still
    pending — _query_pr_checks must classify as ``pending`` so the
    polling loop keeps waiting rather than erroring out."""
    monkeypatch.setattr("ralph_executor.claude_spawn.subprocess.run", _fake_run_factory(8, ""))
    state, names = _query_pr_checks(tmp_path, 42)
    assert state == "pending"
    assert names == []


def test_query_pr_checks_returns_error_on_gh_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``gh`` not installed (FileNotFoundError) is the canonical
    ``error`` path — _query_pr_checks must not raise."""

    def _explode(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh not on PATH")

    monkeypatch.setattr("ralph_executor.claude_spawn.subprocess.run", _explode)
    state, names = _query_pr_checks(tmp_path, 42)
    assert state == "error"
    assert names and "gh not on PATH" in names[0]


def test_wait_for_pr_checks_polls_until_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two pending polls then pass — loop must keep going past pending and
    return as soon as a terminal state arrives."""
    sequence: list[tuple[str, list[str]]] = [
        ("pending", []),
        ("pending", []),
        ("pass", []),
    ]
    calls = {"n": 0}

    def _fake_query(_repo: Path, _num: int, **_kwargs: object) -> tuple[str, list[str]]:
        result = sequence[calls["n"]]
        calls["n"] += 1
        return result

    sleeps: list[float] = []
    monkeypatch.setattr("ralph_executor.claude_spawn._query_pr_checks", _fake_query)
    monkeypatch.setattr("ralph_executor.claude_spawn.time.sleep", lambda s: sleeps.append(s))
    state, names = _wait_for_pr_checks(tmp_path, 42, max_polls=6, interval_seconds=0.5)
    assert state == "pass"
    assert names == []
    assert calls["n"] == 3
    # Two sleeps between three polls; final pass does not trigger a sleep.
    assert sleeps == [0.5, 0.5]


def test_wait_for_pr_checks_budget_exhausted_returns_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All polls report pending — caller treats the returned ``pending``
    as ``partial`` so the NEXT iteration re-polls. The loop must not
    sleep after the final attempt."""
    calls = {"n": 0}

    def _always_pending(*_args: object, **_kwargs: object) -> tuple[str, list[str]]:
        calls["n"] += 1
        return ("pending", [])

    sleeps: list[float] = []
    monkeypatch.setattr("ralph_executor.claude_spawn._query_pr_checks", _always_pending)
    monkeypatch.setattr("ralph_executor.claude_spawn.time.sleep", lambda s: sleeps.append(s))
    state, names = _wait_for_pr_checks(tmp_path, 42, max_polls=3, interval_seconds=0.25)
    assert state == "pending"
    assert names == []
    assert calls["n"] == 3
    # Two sleeps between three polls; no sleep after the last one.
    assert sleeps == [0.25, 0.25]


def test_classify_outcome_pass_returns_pr_created(tmp_path: Path) -> None:
    """Default ``pr_check_state="pass"`` keeps pre-verifier callers
    working; the explicit form behaves identically."""
    pbi_dir = tmp_path / "WI-pass"
    pbi_dir.mkdir()
    outcome = classify_outcome(
        pbi_dir=pbi_dir,
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
        pr_url="https://github.com/example/repo/pull/1",
        pr_check_state="pass",
    )
    assert outcome.kind == "pr_created"
    assert outcome.pr_url == "https://github.com/example/repo/pull/1"


def test_classify_outcome_fail_returns_partial_with_stderr(tmp_path: Path) -> None:
    """A PR exists but a required check failed → ``partial`` with the
    failing check names appended to stderr so the next iteration's
    Claude session can read them."""
    pbi_dir = tmp_path / "WI-fail"
    pbi_dir.mkdir()
    outcome = classify_outcome(
        pbi_dir=pbi_dir,
        stdout="",
        stderr="existing stderr line",
        exit_code=0,
        duration_seconds=1.0,
        pr_url="https://github.com/example/repo/pull/2",
        pr_check_state="fail",
        pr_check_failed_names=["unit-tests", "lint"],
    )
    assert outcome.kind == "partial"
    assert outcome.pr_url == "https://github.com/example/repo/pull/2"
    assert "existing stderr line" in outcome.stderr
    assert "unit-tests" in outcome.stderr
    assert "lint" in outcome.stderr
    assert "Fix the failures next iteration" in outcome.stderr


def test_classify_outcome_pending_returns_partial(tmp_path: Path) -> None:
    """Pending and error states must NOT emit a synthetic stderr line —
    the next iteration's polling decides; we don't want Claude to chase
    a transient pending as if it were a fail."""
    pbi_dir = tmp_path / "WI-pending"
    pbi_dir.mkdir()
    outcome = classify_outcome(
        pbi_dir=pbi_dir,
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
        pr_url="https://github.com/example/repo/pull/3",
        pr_check_state="pending",
    )
    assert outcome.kind == "partial"
    assert outcome.pr_url == "https://github.com/example/repo/pull/3"
    assert outcome.stderr == ""


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
