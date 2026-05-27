"""Tests for ralph_executor.url_utils."""

from __future__ import annotations

import pytest

from ralph_executor.url_utils import TargetRepoInfo, parse_target_repo


def test_parse_github_https_url() -> None:
    info = parse_target_repo("https://github.com/emp3thy/ralph")
    assert isinstance(info, TargetRepoInfo)
    assert info.host == "github.com"
    assert info.owner == "emp3thy"
    assert info.name == "ralph"
    assert info.slug == "emp3thy-ralph"
    assert info.clone_url == "https://github.com/emp3thy/ralph.git"


def test_parse_strips_git_suffix() -> None:
    info = parse_target_repo("https://github.com/emp3thy/ralph.git")
    assert info.name == "ralph"
    assert info.clone_url == "https://github.com/emp3thy/ralph.git"


def test_parse_rejects_http_scheme() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        parse_target_repo("http://github.com/x/y")


def test_parse_rejects_missing_host() -> None:
    with pytest.raises(ValueError, match="host"):
        parse_target_repo("https:///x/y")


def test_parse_rejects_path_with_less_than_two_segments() -> None:
    with pytest.raises(ValueError, match="path"):
        parse_target_repo("https://github.com/onlyone")


def test_parse_rejects_no_path() -> None:
    with pytest.raises(ValueError, match="path"):
        parse_target_repo("https://github.com")


def test_parse_ado_url_extracts_first_two_path_segments() -> None:
    """Documented limitation: ADO URLs parse as github-shaped.
    Acceptable because host_select rejects non-github hosts upstream."""
    info = parse_target_repo("https://dev.azure.com/myorg/myproj/_git/myrepo")
    assert info.host == "dev.azure.com"
    assert info.owner == "myorg"
    assert info.name == "myproj"
