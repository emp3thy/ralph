"""Tests for ``ralph_executor.claude_spawn``."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from ralph_executor.claude_spawn import (
    _query_pr_checks,
    _summarise_stream_json_line,
    _tee_stream,
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
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def _setup_current_pbi(cfg_for_repo: ExecutorConfig, fake_repo: Path) -> PBI:
    write_sample_pbi(fake_repo, pbi_id="WI-1234")
    _git(fake_repo, "add", ".ralph/inbox/WI-1234")
    _git(fake_repo, "commit", "-m", "inbox: WI-1234")
    _git(fake_repo, "push", "origin", "main")
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


def test_spawn_uses_pbi_work_worktree_as_cwd_when_set(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
    tmp_path: Path,
) -> None:
    """spawn_claude_p falls back to ``pbi.work_worktree`` for cwd when
    the explicit ``cwd`` kwarg is omitted (multi-target mode: claim
    populates ``pbi.work_worktree`` to the per-PBI worktree inside
    the target clone)."""
    from dataclasses import replace

    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    work_wt = tmp_path / "fake-clone" / ".ralph-work" / "WI-1234"
    work_wt.mkdir(parents=True)
    pbi = replace(pbi, work_worktree=work_wt)

    cwd_dump = tmp_path / "cwd.txt"
    write_claude_script(
        fake_claude_binary,
        f"import os, pathlib\npathlib.Path({str(cwd_dump)!r}).write_text(os.getcwd())\n",
    )
    spawn_claude_p(cfg_for_repo, pbi)
    assert Path(cwd_dump.read_text()).resolve() == work_wt.resolve()


def test_spawn_sets_gh_owner_env_from_target_info(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
    tmp_path: Path,
) -> None:
    """env['GH_OWNER'] = pbi.target_info.owner so subprocess gh / pr-github
    calls write to the target repo's owner, not whatever the operator
    configured globally."""
    import json
    from dataclasses import replace

    from ralph_executor.url_utils import TargetRepoInfo

    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    pbi = replace(
        pbi,
        target_info=TargetRepoInfo(host="github.com", owner="orgA", name="repoA"),
    )

    env_dump = tmp_path / "env.json"
    write_claude_script(
        fake_claude_binary,
        "import json, os, pathlib\n"
        f"pathlib.Path({str(env_dump)!r}).write_text(json.dumps(dict(os.environ)))\n",
    )
    spawn_claude_p(cfg_for_repo, pbi)
    env = json.loads(env_dump.read_text())
    assert env["GH_OWNER"] == "orgA"


def test_spawn_omits_gh_owner_when_target_info_absent(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy single-target PBIs (target_info=None) inherit GH_OWNER from
    the operator's parent env rather than getting an override. The
    fallback path matters because not all PBIs go through _claim_pbi's
    parser (e.g. tests constructing PBIs directly)."""
    import json

    monkeypatch.delenv("GH_OWNER", raising=False)
    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    assert pbi.target_info is None

    env_dump = tmp_path / "env.json"
    write_claude_script(
        fake_claude_binary,
        "import json, os, pathlib\n"
        f"pathlib.Path({str(env_dump)!r}).write_text(json.dumps(dict(os.environ)))\n",
    )
    spawn_claude_p(cfg_for_repo, pbi)
    env = json.loads(env_dump.read_text())
    assert "GH_OWNER" not in env


def test_spawn_sets_better_memory_project_env_from_target_info(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
    tmp_path: Path,
) -> None:
    """env['BETTER_MEMORY_PROJECT'] = pbi.target_info.name so subagent
    observations land in the target_repo's project scope rather than the
    worktree-derived cwd project."""
    import json
    from dataclasses import replace

    from ralph_executor.url_utils import TargetRepoInfo

    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    pbi = replace(
        pbi,
        target_info=TargetRepoInfo(host="github.com", owner="orgA", name="growatt-monitor"),
    )

    env_dump = tmp_path / "env.json"
    write_claude_script(
        fake_claude_binary,
        "import json, os, pathlib\n"
        f"pathlib.Path({str(env_dump)!r}).write_text(json.dumps(dict(os.environ)))\n",
    )
    spawn_claude_p(cfg_for_repo, pbi)
    env = json.loads(env_dump.read_text())
    assert env["BETTER_MEMORY_PROJECT"] == "growatt-monitor"


def test_spawn_omits_better_memory_project_when_target_info_absent(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without pbi.target_info (legacy single-target mode), the env var
    must NOT be set — the subprocess falls back to cwd-derived project
    resolution, which is correct behaviour for that mode."""
    import json

    monkeypatch.delenv("BETTER_MEMORY_PROJECT", raising=False)
    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    assert pbi.target_info is None

    env_dump = tmp_path / "env.json"
    write_claude_script(
        fake_claude_binary,
        "import json, os, pathlib\n"
        f"pathlib.Path({str(env_dump)!r}).write_text(json.dumps(dict(os.environ)))\n",
    )
    spawn_claude_p(cfg_for_repo, pbi)
    env = json.loads(env_dump.read_text())
    assert "BETTER_MEMORY_PROJECT" not in env


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
    monkeypatch.setattr("ralph_executor.claude_spawn.run_text", _fake_run_factory(0, payload))
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
    monkeypatch.setattr("ralph_executor.claude_spawn.run_text", _fake_run_factory(1, payload))
    state, names = _query_pr_checks(tmp_path, 42)
    assert state == "fail"
    assert names == ["unit-tests", "lint"]


def test_query_pr_checks_returns_pending_on_exit_8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gh exits 8 with empty stdout when a required check is still
    pending — _query_pr_checks must classify as ``pending`` so the
    polling loop keeps waiting rather than erroring out."""
    monkeypatch.setattr("ralph_executor.claude_spawn.run_text", _fake_run_factory(8, ""))
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

    monkeypatch.setattr("ralph_executor.claude_spawn.run_text", _explode)
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


def test_spawn_passes_stream_json_flags_to_claude(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
) -> None:
    """The spawner must pass ``--output-format stream-json --verbose`` so
    Claude flushes one JSON envelope per event on Windows. Without these
    flags, Node block-buffers stdout when stdout is a pipe and the
    operator sees nothing until Claude exits
    (BUG-claude-stdout-streaming-windows)."""
    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    # Print argv one element per line so the assertions below can check
    # both presence AND ordering. Substring-matching the joined string
    # would let `-p --output-format stream-json --verbose <prompt>` pass
    # while still being broken (commander/yargs would treat `-p`'s value
    # as `--output-format`).
    write_claude_script(
        fake_claude_binary,
        "import sys\nprint('\\n'.join(sys.argv[1:]))\nsys.exit(0)\n",
    )
    outcome = spawn_claude_p(cfg_for_repo, pbi)
    argv_lines = outcome.stdout.splitlines()
    assert "--output-format" in argv_lines
    assert "stream-json" in argv_lines
    assert "--verbose" in argv_lines
    assert "-p" in argv_lines
    # The token immediately after `-p` must be the prompt string, not
    # one of the stream-json flags — otherwise Claude consumes the flag
    # as the prompt value (BugBot finding on PR #24).
    p_index = argv_lines.index("-p")
    prompt_token = argv_lines[p_index + 1]
    assert prompt_token.startswith("Read ./prompt/PROMPT.md"), (
        f"argv element after -p must be the prompt; got {prompt_token!r}"
    )


def test_spawn_passes_permission_mode_flag_to_claude(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
) -> None:
    """Spawner must pass ``--permission-mode <cfg.claude_permission_mode>``
    BEFORE ``-p`` so the spawned claude subprocess does not inherit the
    host's ``~/.claude/settings.json`` ``defaultMode``. Putting the flag
    after ``-p`` would make claude consume ``--permission-mode`` as the
    prompt value (commander/yargs)."""
    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    write_claude_script(
        fake_claude_binary,
        "import sys\nprint('\\n'.join(sys.argv[1:]))\nsys.exit(0)\n",
    )
    outcome = spawn_claude_p(cfg_for_repo, pbi)
    argv_lines = outcome.stdout.splitlines()
    assert "--permission-mode" in argv_lines
    flag_index = argv_lines.index("--permission-mode")
    assert argv_lines[flag_index + 1] == "bypassPermissions", (
        f"--permission-mode value should be bypassPermissions; got {argv_lines[flag_index + 1]!r}"
    )
    p_index = argv_lines.index("-p")
    assert flag_index < p_index, (
        "--permission-mode must precede -p so claude doesn't consume the flag as the prompt value"
    )


def test_spawn_passes_overridden_permission_mode(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
) -> None:
    """A non-default ``claude_permission_mode`` (set by TOML/env) flows
    through ``_build_argv`` unchanged. Mirrors the operator escape hatch
    for stricter modes like ``acceptEdits`` or ``plan``."""
    import dataclasses

    custom_cfg = dataclasses.replace(cfg_for_repo, claude_permission_mode="acceptEdits")
    pbi = _setup_current_pbi(custom_cfg, fake_repo)
    write_claude_script(
        fake_claude_binary,
        "import sys\nprint('\\n'.join(sys.argv[1:]))\nsys.exit(0)\n",
    )
    outcome = spawn_claude_p(custom_cfg, pbi)
    argv_lines = outcome.stdout.splitlines()
    flag_index = argv_lines.index("--permission-mode")
    assert argv_lines[flag_index + 1] == "acceptEdits"


def test_summarise_stream_json_line_extracts_assistant_text() -> None:
    """Assistant events get summarised to their first text block so the
    operator sees what Claude is actually saying, not the JSON envelope."""
    raw = '{"type":"assistant","message":{"content":[{"type":"text","text":"hello world"}]}}\n'
    assert _summarise_stream_json_line(raw) == "assistant: hello world"


def test_summarise_stream_json_line_handles_tool_use() -> None:
    """tool_use blocks surface the tool name — tool calls are the most
    interesting events to watch live."""
    raw = (
        '{"type":"assistant","message":{"content":'
        '[{"type":"tool_use","name":"Bash","input":{"command":"ls"}}]}}\n'
    )
    assert _summarise_stream_json_line(raw) == "assistant: tool_use Bash"


def test_summarise_stream_json_line_handles_system_event() -> None:
    raw = '{"type":"system","subtype":"init","cwd":"/tmp"}\n'
    assert _summarise_stream_json_line(raw) == "system/init"


def test_summarise_stream_json_line_handles_result() -> None:
    raw = '{"type":"result","subtype":"success","is_error":false,"duration_ms":2517}\n'
    assert _summarise_stream_json_line(raw) == "result: success (2517 ms)"


def test_summarise_stream_json_line_falls_back_for_non_json() -> None:
    """Stderr lines and pre-init warnings are not JSON — fall back to
    the raw stripped line so operators still see them."""
    raw = "Warning: no stdin data received in 3s\n"
    assert _summarise_stream_json_line(raw) == "Warning: no stdin data received in 3s"


class _TimedBuf(list[str]):
    """Subclass of list that records monotonic timestamps on append.

    Used by the streaming regression test to assert lines arrived
    incrementally during the child's lifetime, not all at once at exit.
    """

    def __init__(self) -> None:
        super().__init__()
        self.timestamps: list[float] = []

    def append(self, item: str) -> None:
        self.timestamps.append(time.monotonic())
        super().append(item)


def test_tee_stream_delivers_lines_incrementally(tmp_path: Path) -> None:
    """Regression: a child that prints ``tick-N`` with 200ms gaps and
    flushes per line must produce a non-empty buffer BEFORE the child
    exits, with appends spread across time rather than batched at the
    end. This guards against Windows-pipe block-buffering on the parent
    side. (The child-side fix — passing ``--output-format stream-json``
    to Claude — is verified by
    ``test_spawn_passes_stream_json_flags_to_claude``.)"""
    script = tmp_path / "ticker.py"
    script.write_text(
        "import sys, time\n"
        "for i in range(3):\n"
        "    sys.stdout.write(f'tick-{i}\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.2)\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, "-u", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None and proc.stderr is not None
    buf = _TimedBuf()
    stderr_buf: list[str] = []
    stdout_err: list[BaseException] = []
    stderr_err: list[BaseException] = []
    t_out = threading.Thread(
        target=_tee_stream,
        args=(proc.stdout, "[tick]", buf, stdout_err),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_tee_stream,
        args=(proc.stderr, "[tick!]", stderr_buf, stderr_err),
        daemon=True,
    )
    t_out.start()
    t_err.start()
    proc.wait()
    t_out.join()
    t_err.join()
    assert not stdout_err and not stderr_err
    assert [line.rstrip() for line in buf] == ["tick-0", "tick-1", "tick-2"]
    # Three lines with 200ms gaps → the gap between the first append and
    # the last must be at least 300ms. A buffered child would deliver all
    # three within a few ms of each other at exit.
    span = buf.timestamps[-1] - buf.timestamps[0]
    assert span >= 0.3, f"lines arrived in {span:.3f}s; expected >= 0.3s with 200ms child gaps"


def test_tee_stream_survives_invalid_utf8_byte(tmp_path: Path) -> None:
    """Regression for BUG-SUBPROCESS-WINDOWS-ENCODING-AUDIT: a child that
    emits a byte invalid in the host locale (0x90 on stock Windows
    cp1252) must NOT crash the tee thread. The byte must land in the
    buffer as the U+FFFD replacement character so the classifier sees a
    well-formed string rather than half a stream.

    Before the fix, ``_tee_stream`` raised ``UnicodeDecodeError`` inside
    its ``for line in stream`` loop, the exception was captured into
    ``err_slot``, and the parent re-raised it — crashing the executor's
    iteration loop with no surviving stdout.
    """
    from ralph_executor.subprocess_utils import popen_text

    script = tmp_path / "bad_byte.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'ok-1\\n')\n"
        "sys.stdout.buffer.write(b'\\x90\\n')\n"
        "sys.stdout.buffer.write(b'ok-3\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    proc = popen_text(
        [sys.executable, "-u", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    assert proc.stdout is not None and proc.stderr is not None
    buf: list[str] = []
    stderr_buf: list[str] = []
    stdout_err: list[BaseException] = []
    stderr_err: list[BaseException] = []
    t_out = threading.Thread(
        target=_tee_stream,
        args=(proc.stdout, "[bad]", buf, stdout_err),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_tee_stream,
        args=(proc.stderr, "[bad!]", stderr_buf, stderr_err),
        daemon=True,
    )
    t_out.start()
    t_err.start()
    proc.wait()
    t_out.join(timeout=5)
    t_err.join(timeout=5)

    assert not stdout_err, f"tee thread crashed: {stdout_err!r}"
    assert not stderr_err
    # Replacement char must be present where the 0x90 byte was.
    assert buf == ["ok-1\n", "�\n", "ok-3\n"]


def test_spawn_claude_p_respects_cwd_and_pbi_dir_overrides(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
    tmp_path: Path,
) -> None:
    """In worktree mode the loop passes explicit ``cwd`` and ``pbi_dir``
    overrides so Claude runs in the per-PBI work worktree but reads
    ``.ralph/current/<id>/`` from the queue worktree. The spawner must
    honour both — it sets ``RALPH_PBI_DIR`` from ``pbi_dir`` and the
    subprocess cwd from ``cwd``."""
    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    # Fake the two worktree-mode paths: a separate ``code_wt`` for cwd and
    # a separate ``queue_pbi_dir`` for RALPH_PBI_DIR. The fake claude
    # script echoes both back via stdout so the test can assert on them
    # without involving real git worktrees.
    code_wt = tmp_path / "code-wt"
    code_wt.mkdir()
    queue_pbi_dir = tmp_path / "queue-wt-pbi"
    queue_pbi_dir.mkdir()
    write_claude_script(
        fake_claude_binary,
        "import os, sys\n"
        "print('CWD=' + os.getcwd())\n"
        "print('RALPH_PBI_DIR=' + os.environ.get('RALPH_PBI_DIR', ''))\n"
        "sys.exit(0)\n",
    )

    outcome = spawn_claude_p(
        cfg_for_repo,
        pbi,
        cwd=code_wt,
        pbi_dir=queue_pbi_dir,
    )

    assert outcome.exit_code == 0
    assert str(code_wt) in outcome.stdout
    assert str(queue_pbi_dir) in outcome.stdout


def test_spawn_claude_p_rejects_missing_pbi_dir(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
    tmp_path: Path,
) -> None:
    """A misconfigured worktree (``RALPH_PBI_DIR`` pointing at nothing)
    must fail fast with ``FileNotFoundError`` before the spawn — better
    than crashing the spawned Claude with a confusing read failure."""
    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    missing = tmp_path / "does-not-exist"

    with pytest.raises(FileNotFoundError):
        spawn_claude_p(cfg_for_repo, pbi, pbi_dir=missing)


def test_spawn_claude_p_sets_bash_max_timeout_ms_from_cfg(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
) -> None:
    """``spawn_claude_p`` must export ``cfg.bash_max_timeout_ms`` to the
    Claude subprocess env so the bash-tool ceiling matches what TOML/env
    resolved. The stand-in claude echoes the env var so the test can
    assert on the value without mocking ``subprocess.Popen``."""
    from dataclasses import replace

    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    write_claude_script(
        fake_claude_binary,
        "import os, sys\n"
        "print('BASH_MAX_TIMEOUT_MS=' + os.environ.get('BASH_MAX_TIMEOUT_MS', ''))\n"
        "sys.exit(0)\n",
    )
    cfg = replace(cfg_for_repo, bash_max_timeout_ms=420_000)

    outcome = spawn_claude_p(cfg, pbi)

    assert outcome.exit_code == 0
    assert "BASH_MAX_TIMEOUT_MS=420000" in outcome.stdout


def test_spawn_claude_p_kills_child_on_session_timeout(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``proc.wait`` exceeds ``cfg.claude_session_timeout_seconds``,
    the spawner kills the child, joins the tee threads, and returns an
    ``error`` outcome with a synthetic stderr explanation. This is the
    unit-level coverage; the integration-level coverage drives a real
    long-running fake claude script."""
    from dataclasses import replace

    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)

    kill_calls: list[None] = []
    wait_calls: list[float | None] = []

    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = _EmptyStream()
            self.stderr = _EmptyStream()
            self.pid = 999999  # unused — taskkill/killpg are stubbed
            self._killed = False

        def wait(self, timeout: float | None = None) -> int:
            wait_calls.append(timeout)
            if not self._killed:
                raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout or 0.0)
            return -1

        def kill(self) -> None:
            kill_calls.append(None)
            self._killed = True
            self.stdout.close()
            self.stderr.close()

    fake_proc = _FakeProc()

    def _fake_popen(*args: object, **kwargs: object) -> _FakeProc:
        return fake_proc

    tree_kill_calls: list[object] = []

    def _fake_kill_tree(proc: subprocess.Popen[str]) -> None:
        tree_kill_calls.append(proc)
        proc.kill()

    monkeypatch.setattr("ralph_executor.claude_spawn.popen_text", _fake_popen)
    monkeypatch.setattr(
        "ralph_executor.claude_spawn._kill_process_tree",
        _fake_kill_tree,
    )
    # Bypass the post-spawn ``gh pr list`` call; classify_outcome short-
    # circuits on exit_code != 0 anyway, but we don't want the test
    # depending on whether ``gh`` is installed.
    monkeypatch.setattr(
        "ralph_executor.claude_spawn._query_open_pr_via_gh",
        lambda repo_path, branch: None,
    )

    cfg = replace(cfg_for_repo, claude_session_timeout_seconds=1)
    outcome = spawn_claude_p(cfg, pbi)

    assert tree_kill_calls == [fake_proc]
    assert kill_calls == [None]
    assert wait_calls and wait_calls[0] == 1
    assert outcome.kind == "error"
    assert outcome.exit_code == -1
    assert "1s and was killed" in outcome.stderr


class _EmptyStream:
    """Minimal IO stand-in for ``subprocess.PIPE`` streams in unit tests.

    The tee threads iterate the stream until EOF; ``__iter__`` yields
    nothing so they exit immediately. ``close`` is provided so the fake
    process can release the iterator if needed.
    """

    def __init__(self) -> None:
        self._closed = False

    def __iter__(self) -> _EmptyStream:
        return self

    def __next__(self) -> str:
        raise StopIteration

    def close(self) -> None:
        self._closed = True


def test_spawn_claude_p_session_timeout_integration(
    cfg_for_repo: ExecutorConfig,
    fake_repo: Path,
    fake_claude_binary: Path,
) -> None:
    """A real long-running fake claude script must be killed within a
    couple of seconds of the configured budget. Drives the full Popen +
    tee + wait path that the unit test mocks out."""
    from dataclasses import replace

    pbi = _setup_current_pbi(cfg_for_repo, fake_repo)
    write_claude_script(
        fake_claude_binary,
        "import sys, time\n"
        "sys.stdout.write('starting\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n"
        "sys.exit(0)\n",
    )
    cfg = replace(cfg_for_repo, claude_session_timeout_seconds=2)

    start = time.monotonic()
    outcome = spawn_claude_p(cfg, pbi)
    elapsed = time.monotonic() - start

    assert outcome.kind == "error"
    assert outcome.exit_code != 0
    assert "2s and was killed" in outcome.stderr
    # Generous upper bound: the wall budget is 2 s, plus a small tail for
    # process teardown + the post-spawn gh-pr-list call.
    assert elapsed < 30, f"spawn took {elapsed:.1f}s, expected near 2s"
