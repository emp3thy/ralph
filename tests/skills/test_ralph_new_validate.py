"""Pure-function validators for ralph-new fields."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "skills" / "ralph-new" / "scripts" / "validate.py"


@pytest.fixture(scope="module")
def validate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ralph_new_validate", MOD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---- target_repo -----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo",
        "https://dev.azure.com/org/project/_git/repo",
    ],
)
def test_target_repo_accepts_valid_https_url(validate, url):
    assert validate.validate_target_repo(url) is None


@pytest.mark.parametrize(
    "url, fragment",
    [
        ("http://github.com/owner/repo", "HTTPS"),
        ("ftp://github.com/owner/repo", "HTTPS"),
        ("https://github.com", "owner"),
        ("https://github.com/owner", "owner"),
        ("https://", "host"),
        ("https://github.com/owner/repo\n", "newline"),
        ("https://github.com/owner/repo\r", "newline"),
    ],
)
def test_target_repo_rejects_bad_url(validate, url, fragment):
    msg = validate.validate_target_repo(url)
    assert msg is not None
    assert fragment.lower() in msg.lower()


# ---- pbi_id ----------------------------------------------------------


@pytest.mark.parametrize(
    "pbi_id",
    [
        "FIX-LOGIN",
        "BUG-A-B-C-1-2-3",
        "FOO",
        "F" + "O" * 78 + "O",  # 80 chars exact
    ],
)
def test_pbi_id_accepts_valid(validate, pbi_id):
    assert validate.validate_pbi_id(pbi_id) is None


@pytest.mark.parametrize(
    "pbi_id, fragment",
    [
        ("", "empty"),
        ("a", "regex"),  # lowercase, also 1 char so regex fails first
        ("F" + "O" * 80, "length"),  # 81 chars too long
        ("-FOO", "regex"),  # leading hyphen
        ("FOO-", "regex"),  # trailing hyphen
        ("FOO BAR", "regex"),  # space
        ("FOO_BAR", "regex"),  # underscore
    ],
)
def test_pbi_id_rejects_invalid(validate, pbi_id, fragment):
    msg = validate.validate_pbi_id(pbi_id)
    assert msg is not None
    # length errors mention size; regex errors mention "regex" or "must match"
    assert fragment.lower() in msg.lower() or "regex" in msg.lower() or "match" in msg.lower()


# ---- severity --------------------------------------------------------


@pytest.mark.parametrize("s", ["critical", "high", "normal", "low"])
def test_severity_accepts_valid(validate, s):
    assert validate.validate_severity(s) is None


@pytest.mark.parametrize("s", ["", "URGENT", "med", "high-ish", "Critical"])
def test_severity_rejects_invalid(validate, s):
    assert validate.validate_severity(s) is not None


# ---- type ------------------------------------------------------------


@pytest.mark.parametrize("t", ["bug", "feature"])
def test_type_accepts_bug_and_feature(validate, t):
    assert validate.validate_type(t) is None


@pytest.mark.parametrize("t", ["", "pr-feedback", "Feature", "task", "story"])
def test_type_rejects_anything_else(validate, t):
    msg = validate.validate_type(t)
    assert msg is not None
    if t == "pr-feedback":
        assert "sweep" in msg.lower() or "pr-feedback" in msg.lower()


# ---- depends_on syntax -----------------------------------------------


def test_depends_on_syntax_accepts_list_of_valid_ids(validate):
    assert validate.validate_depends_on_syntax(["FOO", "BAR-1", "BAZ-QUX-2"]) is None


def test_depends_on_syntax_accepts_empty_list(validate):
    assert validate.validate_depends_on_syntax([]) is None


def test_depends_on_syntax_rejects_any_invalid_id(validate):
    msg = validate.validate_depends_on_syntax(["FOO", "bar", "BAZ"])
    assert msg is not None
    assert "bar" in msg
