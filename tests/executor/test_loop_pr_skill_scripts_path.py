"""Tests for _pr_skill_scripts_path after the repo_path refactor."""

from pathlib import Path

from ralph_executor.loop import _pr_skill_scripts_path
from tests.executor.conftest import _build_minimal_cfg


def test_resolves_to_executor_source_tree(tmp_path: Path) -> None:
    """Path must derive from ralph_executor source tree, not any operator path."""
    cfg = _build_minimal_cfg(tmp_path, git_host="github")
    result = _pr_skill_scripts_path(cfg)
    assert result.is_absolute()
    assert result.parts[-3:] == ("skills", "pr-github", "scripts")
    assert tmp_path not in result.parents
