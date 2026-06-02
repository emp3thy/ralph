"""End-to-end: a non-zero claude subprocess exit emits a bug PBI."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ralph_executor import claude_spawn
from ralph_executor.config import ExecutorConfig
from ralph_executor.queue.filesystem import parse_pbi_directory
from tests.executor.conftest import _git, write_claude_script, write_sample_pbi


def test_subprocess_nonzero_exit_emits_autobug_pbi(
    fake_repo: Path,
    cfg_for_repo: ExecutorConfig,
    fake_claude_binary: Path,
) -> None:
    write_claude_script(
        fake_claude_binary,
        "import sys\nsys.stderr.write('Killed\\n')\nsys.exit(137)\n",
    )
    cfg = replace(cfg_for_repo, bot_author_email="bot@e.com")

    pbi_dir = write_sample_pbi(fake_repo, pbi_id="WI-9999", where="current")
    _git(fake_repo, "add", str(pbi_dir.relative_to(fake_repo)))
    _git(fake_repo, "commit", "-m", "test: stage WI-9999 in current")
    _git(fake_repo, "push", "origin", "main")
    pbi = parse_pbi_directory(pbi_dir, status="current")

    claude_spawn.spawn_claude_p(cfg, pbi, cwd=fake_repo, pbi_dir=pbi_dir)

    inbox = fake_repo / ".ralph" / "inbox"
    autobug_dirs = [d for d in inbox.iterdir() if d.is_dir() and d.name.startswith("autobug-")]
    assert autobug_dirs, "expected an autobug PBI on non-zero subprocess exit"
    bug_md = (autobug_dirs[0] / "BUG.md").read_text(encoding="utf-8")
    assert "subprocess" in bug_md.lower() or "subprocess_crash" in bug_md
    reproduce_md = (autobug_dirs[0] / "REPRODUCE.md").read_text(encoding="utf-8")
    assert "## stderr" in reproduce_md, (
        "REPRODUCE.md must include the stderr section for subprocess crashes "
        f"(got: {reproduce_md!r})"
    )
    assert "Killed" in reproduce_md, (
        f"REPRODUCE.md stderr section must contain the real stderr tail; got: {reproduce_md!r}"
    )
    assert "Exit code: 137" in reproduce_md, (
        f"REPRODUCE.md must include the exit code; got: {reproduce_md!r}"
    )
