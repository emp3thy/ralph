"""Tests for the baked .claude/settings.json."""

import json
from pathlib import Path
from typing import Any

import pytest

SETTINGS_PATH = Path(__file__).resolve().parents[2] / ".claude" / "settings.json"


@pytest.fixture(scope="module")
def settings() -> dict[str, Any]:
    assert SETTINGS_PATH.exists(), f"missing {SETTINGS_PATH}"
    with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def test_dangerously_skip_permissions_is_true(settings: dict[str, Any]) -> None:
    assert settings.get("dangerouslySkipPermissions") is True


def test_model_is_pinned(settings: dict[str, Any]) -> None:
    model = settings.get("model")
    assert isinstance(model, str) and model.startswith("claude-")


def test_permissions_allow_lists_every_required_tool(settings: dict[str, Any]) -> None:
    perms = settings.get("permissions", {})
    assert isinstance(perms, dict)
    allow = perms.get("allow", [])
    assert isinstance(allow, list)
    required = {
        "Bash",
        "Edit",
        "Write",
        "Read",
        "Grep",
        "Glob",
        "Task",
        "TodoWrite",
        "Skill(pr)",
        "Skill(ralph-new)",
        "Skill(ralph-status)",
        "Skill(ralph-cancel)",
        "Skill(ralph-promote)",
        "Skill(ralph-triage)",
        "Skill(ralph-doctor)",
    }
    missing = required - set(allow)
    assert not missing, f"missing permissions.allow entries: {missing}"


def test_pr_uses_canonical_name(settings: dict[str, Any]) -> None:
    """The image bakes the host-specific pr skill into the canonical
    path (skills/pr/), so settings.json MUST use the canonical name
    not the host-suffixed one. workitem-fetch is retired by ralph-new
    so its allow entry must not appear."""
    perms = settings.get("permissions", {})
    allow = set(perms.get("allow", []))
    assert "Skill(pr)" in allow
    assert "Skill(pr-github)" not in allow
    assert "Skill(pr-ado)" not in allow
    assert "Skill(workitem-fetch)" not in allow
    assert "Skill(ralph-add)" not in allow


def test_deny_list_blocks_obvious_footguns(settings: dict[str, Any]) -> None:
    perms = settings.get("permissions", {})
    deny = perms.get("deny", [])
    assert "Bash(rm -rf /*)" in deny
    assert "Bash(sudo *)" in deny


def test_deny_list_blocks_curl_wget_both_schemes(settings: dict[str, Any]) -> None:
    """Regression: BugBot PR #39 flagged that deny patterns only
    blocked http://*; curl/wget over https:// was unrestricted —
    secret exfiltration over HTTPS would slip past the only runtime
    guardrail under dangerouslySkipPermissions: true."""
    deny = settings.get("permissions", {}).get("deny", [])
    assert "Bash(curl http://*)" in deny
    assert "Bash(curl https://*)" in deny
    assert "Bash(wget http://*)" in deny
    assert "Bash(wget https://*)" in deny


def test_v2_memory_permissions_present_but_commented(settings: dict[str, Any]) -> None:
    reserved = settings.get("_v2_memory_mcp_permissions_commented_out")
    assert isinstance(reserved, list)
    assert any("memory_retrieve" in entry for entry in reserved)
