"""Tests for _pr_skill_scripts_path after the repo_path refactor."""

from pathlib import Path

import pytest

from ralph_executor.loop import _pr_skill_scripts_path
from tests.executor.conftest import _build_minimal_cfg


@pytest.mark.skip(reason="depends on ExecutorConfig.repo_path deletion (Task 5/8)")
def test_resolves_to_executor_source_tree(tmp_path: Path) -> None:
    """Path must derive from ralph_executor source tree, not any operator path.

    Once ``ExecutorConfig.repo_path`` is deleted in Task 8, ``_build_minimal_cfg``
    constructs a valid cfg without ``repo_path=...`` and this test unskips.
    """
    cfg = _build_minimal_cfg(tmp_path, git_host="github")
    result = _pr_skill_scripts_path(cfg)
    assert result.is_absolute()
    assert result.parts[-3:] == ("skills", "pr-github", "scripts")
    assert tmp_path not in result.parents
