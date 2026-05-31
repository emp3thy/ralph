"""Tests for ``ralph_executor.setup_cmds``."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.setup_cmds import cmd_init
from ralph_executor.user_config import (
    read_queue_repo,
    read_workspace_root,
    write_queue_branch,
    write_queue_repo,
    write_workspace_root,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Path.home() at a temp dir so user_config.* writes there."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _default_closed_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default ``input()`` raises EOFError so cmd_init tests that don't
    explicitly drive the queue_repo prompt fall through with a warning
    rather than blocking on real stdin.

    Tests that want to exercise the prompt override this by calling
    ``monkeypatch.setattr(builtins, "input", ...)`` again — the second
    monkeypatch.setattr wins, per pytest's stacked-undo semantics.
    """
    import builtins

    def _closed(prompt: str = "") -> str:  # noqa: ARG001
        raise EOFError("default closed stdin")

    monkeypatch.setattr(builtins, "input", _closed)


@pytest.fixture
def _smoke_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `_smoke_clone_queue_repo` return True without touching the network."""
    monkeypatch.setattr(
        "ralph_executor.setup_cmds._smoke_clone_queue_repo",
        lambda _url, *, timeout=10.0: True,
    )


# ---------------------------------------------------------------------------
# cmd_init
# ---------------------------------------------------------------------------


def test_init_assume_yes_writes_workspace_root_default(fake_home: Path) -> None:
    """--yes path: no prompt, picks the OS default for workspace_root."""
    exit_code = cmd_init(assume_yes=True)
    assert exit_code == 0
    ws = read_workspace_root()
    assert ws is not None
    assert ws.exists() and ws.is_dir()


def test_init_is_idempotent_when_workspace_root_already_set(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """If workspace_root is already set, init reports the value and
    exits 0 without overwriting."""
    first = (fake_home / "first").resolve()
    write_workspace_root(first)
    capsys.readouterr()
    exit_code = cmd_init(assume_yes=True)
    assert exit_code == 0
    assert read_workspace_root() == first
    out = capsys.readouterr().out
    assert "workspace_root already set" in out


def test_init_handles_closed_stdin_when_not_assume_yes(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ralph-executor init` with no --yes, run with a closed/piped
    stdin (CI, non-tty), must accept the default for workspace_root
    rather than crash with an unhandled EOFError."""
    import builtins

    def closed_stdin_input(prompt: str = "") -> str:  # noqa: ARG001
        raise EOFError("simulated closed stdin")

    monkeypatch.setattr(builtins, "input", closed_stdin_input)
    exit_code = cmd_init(assume_yes=False)
    assert exit_code == 0
    ws = read_workspace_root()
    assert ws is not None  # default was used


def test_init_handles_oserror_from_disk_write(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Disk-full / permission-denied on workspace_root mkdir or
    user-config write must produce a clean error + exit 2, not a raw
    traceback. Drive the prompt to return a workspace_root path whose
    mkdir is monkeypatched to fail."""
    import builtins

    target = fake_home / "oops"

    def answer(prompt: str = "") -> str:  # noqa: ARG001
        return str(target)

    monkeypatch.setattr(builtins, "input", answer)

    real_mkdir = Path.mkdir

    def faulty_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if str(self).endswith("oops"):
            raise OSError("simulated disk full")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", faulty_mkdir)
    exit_code = cmd_init(assume_yes=False)
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot write user config" in err
    assert "simulated disk full" in err


def test_init_warns_when_gh_missing(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "ralph_executor.setup_cmds._check_tool",
        lambda name: None,  # noqa: ARG005
    )
    cmd_init(assume_yes=True)
    out = capsys.readouterr().out
    assert "gh CLI not found" in out
    assert "claude CLI not found" in out


# ---------------------------------------------------------------------------
# cmd_init — queue_repo prompt (Task 8)
# ---------------------------------------------------------------------------


def test_init_prompts_for_queue_repo_and_writes_user_config(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    _smoke_ok: None,
) -> None:
    """`init` prompts for queue_repo, validates the URL, and persists it
    to ``~/.ralph/config.toml`` alongside workspace_root."""
    import builtins

    answers = iter(
        [
            "",  # 1. workspace_root prompt → accept default
            "https://github.com/example/ralph-queue",
            "",  # queue_branch — accept default
        ]
    )
    monkeypatch.setattr(builtins, "input", lambda *_a, **_k: next(answers))

    exit_code = cmd_init(assume_yes=False)
    assert exit_code == 0
    assert read_queue_repo() == "https://github.com/example/ralph-queue"
    # workspace_root persisted via the default prompt path.
    ws = read_workspace_root()
    assert ws is not None and ws.exists()


def test_init_reprompts_on_invalid_queue_repo(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    _smoke_ok: None,
) -> None:
    """parse_target_repo rejection → reprompt; final valid URL is written."""
    import builtins

    answers = iter(
        [
            "",  # workspace_root → default
            "",  # empty → reprompt
            "ftp://nope.example.com/q",  # bad scheme → reprompt
            "https://github.com/example/queue",  # accepted
            "",  # queue_branch — accept default
        ]
    )
    monkeypatch.setattr(builtins, "input", lambda *_a, **_k: next(answers))

    exit_code = cmd_init(assume_yes=False)
    assert exit_code == 0
    assert read_queue_repo() == "https://github.com/example/queue"


def test_init_smoke_clone_failure_warns_but_writes(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`git ls-remote` failure prints a warning but the value still lands —
    operator may be on a flaky network or behind credentials that are
    valid for clone but reject ls-remote."""
    import builtins

    answers = iter(
        [
            "",  # workspace_root → default
            "https://github.com/example/queue",
            "",  # queue_branch — accept default
        ]
    )
    monkeypatch.setattr(builtins, "input", lambda *_a, **_k: next(answers))
    monkeypatch.setattr(
        "ralph_executor.setup_cmds._smoke_clone_queue_repo",
        lambda _url, *, timeout=10.0: False,
    )

    exit_code = cmd_init(assume_yes=False)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "smoke" in out.lower()
    assert read_queue_repo() == "https://github.com/example/queue"


def test_init_assume_yes_skips_queue_repo_prompt_with_warning(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--yes` must never block on stdin — emit a warning and leave
    queue_repo unset for the operator to add manually."""
    exit_code = cmd_init(assume_yes=True)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "queue_repo" in out
    assert "WARNING" in out
    assert read_queue_repo() is None


def test_init_skips_queue_repo_prompt_if_already_set(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A second `init` with queue_repo already in user_config must NOT
    reprompt — the function is idempotent for that knob."""
    # Pre-seed all three knobs so no prompt is reachable; `boom` enforces
    # the no-prompt invariant for every remaining knob.
    write_workspace_root(fake_home / "ws")
    write_queue_repo("https://github.com/already/set")
    write_queue_branch("custom-branch")

    import builtins

    def boom(*_a: object, **_k: object) -> str:
        raise AssertionError("init must not prompt when knobs already set")

    monkeypatch.setattr(builtins, "input", boom)

    exit_code = cmd_init(assume_yes=False)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "queue_repo already set" in out
    assert read_queue_repo() == "https://github.com/already/set"


def test_init_handles_closed_stdin_on_queue_repo_prompt(
    fake_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """EOFError during the prompt → warning + clean exit, no write.

    Uses the autouse default (`_default_closed_stdin`) — input() raises
    EOFError on the very first call, simulating a piped / closed stdin.
    workspace_root EOFs to its default; queue_repo EOFs and warns.
    """
    exit_code = cmd_init(assume_yes=False)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "queue_repo" in out
    assert "WARNING" in out
    assert read_queue_repo() is None
