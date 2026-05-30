"""Tests for the ``ralph-doctor`` skill.

Loads ``skills/ralph-doctor/scripts/check.py`` via importlib because the
parent directory name contains a hyphen. All network calls are mocked
with ``responses``; the filesystem-only checks read synthetic
settings.json files built in ``tmp_path``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import responses

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "skills" / "ralph-doctor" / "scripts" / "check.py"
CHECKS_DIR = REPO_ROOT / "skills" / "ralph-doctor" / "scripts" / "checks"

ANTHROPIC_TOKEN_URL = "https://api.anthropic.com/v1/messages/count_tokens"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_OWNER = "example-org"
GITHUB_TEST_REPO_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/test-permissions"
ADO_ORG = "https://dev.azure.com/example-org"
ADO_PROJECT = "example-project"
ADO_REPO = "service-auth"
NON_EXISTENT_PR_ID = 999999999
ADO_PROBE_URL = (
    f"{ADO_ORG}/{ADO_PROJECT}/_apis/git/repositories/{ADO_REPO}/pullrequests/{NON_EXISTENT_PR_ID}"
)


# ----------------------------------------------------------------------
# Module loaders
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def runner_module() -> ModuleType:
    assert RUNNER_PATH.is_file(), f"missing entry script at {RUNNER_PATH}"
    spec = importlib.util.spec_from_file_location("ralph_doctor_runner", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_check_module(name: str) -> ModuleType:
    path = CHECKS_DIR / f"{name}.py"
    assert path.is_file(), f"missing check module at {path}"
    spec = importlib.util.spec_from_file_location(f"ralph_doctor_check_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------------
# Synthetic settings
# ----------------------------------------------------------------------


_GOOD_ALLOW: list[str] = [
    "Bash(*)",
    "Edit(*)",
    "Write(*)",
    "Read(*)",
    "Grep(*)",
    "Glob(*)",
    "Skill(pr)",
    "Skill(ralph-doctor)",
]

GOOD_SETTINGS: dict[str, Any] = {
    "permissions": {"allow": list(_GOOD_ALLOW)},
    "hooks": {
        "PostToolUse": [
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python /opt/ralph/log_observation.py",
                        "async": True,
                    }
                ],
            }
        ],
        "SessionStart": [
            {
                "matcher": "startup",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python /opt/ralph/announce.py",
                    }
                ],
            }
        ],
    },
    "mcpServers": {
        "ado": {
            "command": "uvx",
            "args": ["ado-mcp"],
            "env": {"ADO_PAT": "${ADO_PAT}"},
        }
    },
}

BAD_PERMISSIONS_SETTINGS: dict[str, Any] = {
    "permissions": {"allow": ["Bash(*)", "Read(*)"]},
    "hooks": {},
    "mcpServers": {},
}

BAD_HOOK_SETTINGS: dict[str, Any] = {
    "permissions": {"allow": list(_GOOD_ALLOW)},
    "hooks": {
        "PreToolUse": [
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": ("python -c \"claude.AskUserQuestion('confirm?')\""),
                    }
                ],
            }
        ]
    },
    "mcpServers": {},
}

ASYNC_HOOK_WITH_INPUT_SETTINGS: dict[str, Any] = {
    "permissions": {"allow": list(_GOOD_ALLOW)},
    "hooks": {
        "PostToolUse": [
            {
                "matcher": ".*",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python -c 'input(\"continue?\")'",
                        "async": True,
                    }
                ],
            }
        ]
    },
    "mcpServers": {},
}

BAD_MCP_OAUTH_SETTINGS: dict[str, Any] = {
    "permissions": {"allow": list(_GOOD_ALLOW)},
    "hooks": {},
    "mcpServers": {
        "evil": {
            "command": "uvx",
            "args": ["--auth", "oauth"],
            "env": {},
        }
    },
}


# ----------------------------------------------------------------------
# Helpers + fixtures
# ----------------------------------------------------------------------


def _write_settings(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _frontmatter(name: str, description: str = "stub") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


def _build_skills_dir(
    tmp_path: Path,
    *,
    include_ask_user_question: bool = False,
    host: str = "github",
    stage_mismatch: bool = False,
    missing_skill: str | None = None,
) -> Path:
    """Build a synthetic ``~/.claude/skills/`` tree under ``tmp_path``.

    See the plan (Task 3) for the parameter contract.
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    other = "ado" if host == "github" else "github"
    pr_name = f"pr-{other}" if stage_mismatch else f"pr-{host}"

    if missing_skill != "pr":
        pr_dir = skills_dir / "pr"
        pr_dir.mkdir(parents=True, exist_ok=True)
        (pr_dir / "SKILL.md").write_text(_frontmatter(pr_name), encoding="utf-8")

    if include_ask_user_question:
        ask_dir = skills_dir / "asks-questions"
        (ask_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (ask_dir / "SKILL.md").write_text(_frontmatter("asks-questions"), encoding="utf-8")
        (ask_dir / "scripts" / "main.py").write_text(
            "from claude import AskUserQuestion\nAskUserQuestion('hello?')\n",
            encoding="utf-8",
        )

    return skills_dir


class _Ctx:
    def __init__(
        self,
        settings_path: Path,
        skills_dir: Path,
        git_host: str,
        strict: bool = False,
    ) -> None:
        self.settings_path = settings_path
        self.skills_dir = skills_dir
        self.strict = strict
        self.git_host = git_host
        self.extra: dict[str, str] = {}


def _make_context(settings: Path, skills_dir: Path, *, git_host: str = "github") -> _Ctx:
    """Return a duck-typed CheckContext avoiding the hyphenated-package import."""
    return _Ctx(settings_path=settings, skills_dir=skills_dir, git_host=git_host)


@pytest.fixture
def good_env_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RALPH_GIT_HOST", "github")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.delenv("RALPH_USE_BEDROCK", raising=False)
    monkeypatch.setenv("GH_TOKEN", "ghp_fake")
    monkeypatch.setenv("GH_OWNER", GITHUB_OWNER)
    monkeypatch.delenv("ADO_PAT", raising=False)
    monkeypatch.delenv("ADO_ORG_URL", raising=False)
    monkeypatch.delenv("ADO_PROJECT", raising=False)
    monkeypatch.delenv("ADO_REPOSITORY", raising=False)


@pytest.fixture
def good_env_ado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RALPH_GIT_HOST", "ado")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.delenv("RALPH_USE_BEDROCK", raising=False)
    monkeypatch.setenv("ADO_PAT", "fake-pat")
    monkeypatch.setenv("ADO_ORG_URL", ADO_ORG)
    monkeypatch.setenv("ADO_PROJECT", ADO_PROJECT)
    monkeypatch.setenv("ADO_REPOSITORY", ADO_REPO)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GH_OWNER", raising=False)


def _run(
    runner_module: ModuleType,
    *,
    settings_path: Path,
    skills_dir: Path,
    extra_argv: list[str] | None = None,
) -> int:
    argv = [
        "--settings",
        str(settings_path),
        "--skills-dir",
        str(skills_dir),
    ]
    if extra_argv:
        argv.extend(extra_argv)
    return int(runner_module.main(argv))


def _payload_from_capsys(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, dict)
    return data


# ----------------------------------------------------------------------
# TestRunnerExitCodes
# ----------------------------------------------------------------------


class TestRunnerExitCodes:
    def test_all_pass_returns_zero_github(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        good_env_github: None,
    ) -> None:
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        with responses.RequestsMock() as rsps:
            rsps.add(responses.POST, ANTHROPIC_TOKEN_URL, json={"input_tokens": 1}, status=200)
            rsps.add(responses.GET, GITHUB_USER_URL, json={"login": "user"}, status=200)
            rsps.add(responses.GET, GITHUB_TEST_REPO_URL, json={"message": "Not Found"}, status=404)
            code = _run(runner_module, settings_path=settings, skills_dir=skills_dir)
        payload = _payload_from_capsys(capsys)
        assert code == 0
        assert payload["ok"] is True
        assert payload["summary"]["passes"] == 7
        assert payload["summary"]["skips"] == 1
        names = {entry["name"] for entry in payload["checks"]}
        assert names == {
            "permissions",
            "hooks",
            "skills",
            "mcp",
            "auth",
            "host_staging",
            "github_auth",
            "ado_auth",
        }

    def test_missing_permission_returns_one(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        good_env_github: None,
    ) -> None:
        settings = tmp_path / "settings.json"
        _write_settings(settings, BAD_PERMISSIONS_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        code = _run(
            runner_module,
            settings_path=settings,
            skills_dir=skills_dir,
            extra_argv=["--skip", "auth,github_auth,host_staging"],
        )
        payload = _payload_from_capsys(capsys)
        assert code == 1
        permissions_entry = next(e for e in payload["checks"] if e["name"] == "permissions")
        assert permissions_entry["status"] == "fail"

    def test_skip_runs_subset(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        good_env_github: None,
    ) -> None:
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        code = _run(
            runner_module,
            settings_path=settings,
            skills_dir=skills_dir,
            extra_argv=["--skip", "auth,github_auth,host_staging"],
        )
        payload = _payload_from_capsys(capsys)
        assert code == 0
        skipped_names = {e["name"] for e in payload["checks"] if e["status"] == "skipped"}
        assert {"auth", "github_auth", "host_staging"}.issubset(skipped_names)

    def test_only_runs_named_subset(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        good_env_github: None,
    ) -> None:
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        code = _run(
            runner_module,
            settings_path=settings,
            skills_dir=skills_dir,
            extra_argv=["--only", "permissions"],
        )
        payload = _payload_from_capsys(capsys)
        assert code == 0
        permissions_entry = next(e for e in payload["checks"] if e["name"] == "permissions")
        assert permissions_entry["status"] == "pass"
        other_statuses = {e["status"] for e in payload["checks"] if e["name"] != "permissions"}
        assert other_statuses == {"skipped"}

    def test_skip_and_only_are_mutually_exclusive(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        good_env_github: None,
    ) -> None:
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        code = _run(
            runner_module,
            settings_path=settings,
            skills_dir=skills_dir,
            extra_argv=["--skip", "auth", "--only", "permissions"],
        )
        captured = capsys.readouterr()
        assert code == 2
        assert "mutually exclusive" in captured.err

    def test_missing_settings_path_returns_two(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        good_env_github: None,
    ) -> None:
        settings = tmp_path / "does-not-exist.json"
        skills_dir = _build_skills_dir(tmp_path, host="github")
        code = _run(runner_module, settings_path=settings, skills_dir=skills_dir)
        captured = capsys.readouterr()
        assert code == 2
        assert "settings" in captured.err.lower()

    def test_malformed_settings_returns_two(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        good_env_github: None,
    ) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text("{not json", encoding="utf-8")
        skills_dir = _build_skills_dir(tmp_path, host="github")
        code = _run(runner_module, settings_path=settings, skills_dir=skills_dir)
        captured = capsys.readouterr()
        assert code == 2
        assert "settings" in captured.err.lower()

    def test_strict_promotes_warn_to_error(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        good_env_github: None,
    ) -> None:
        settings = tmp_path / "settings.json"
        _write_settings(settings, ASYNC_HOOK_WITH_INPUT_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        code = _run(
            runner_module,
            settings_path=settings,
            skills_dir=skills_dir,
            extra_argv=[
                "--strict",
                "--skip",
                "auth,github_auth,host_staging",
            ],
        )
        _payload_from_capsys(capsys)
        assert code == 1


# ----------------------------------------------------------------------
# TestHostDispatch
# ----------------------------------------------------------------------


class TestHostDispatch:
    def test_github_host_skips_ado_auth(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        good_env_github: None,
    ) -> None:
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        with responses.RequestsMock() as rsps:
            rsps.add(responses.POST, ANTHROPIC_TOKEN_URL, json={}, status=200)
            rsps.add(responses.GET, GITHUB_USER_URL, json={"login": "u"}, status=200)
            rsps.add(responses.GET, GITHUB_TEST_REPO_URL, json={}, status=404)
            code = _run(runner_module, settings_path=settings, skills_dir=skills_dir)
        payload = _payload_from_capsys(capsys)
        assert code == 0
        ado_entry = next(e for e in payload["checks"] if e["name"] == "ado_auth")
        assert ado_entry["status"] == "skipped"
        assert "RALPH_GIT_HOST" in ado_entry["message"]

    def test_ado_host_skips_github_auth(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        good_env_ado: None,
    ) -> None:
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="ado")
        with responses.RequestsMock() as rsps:
            rsps.add(responses.POST, ANTHROPIC_TOKEN_URL, json={}, status=200)
            rsps.add(responses.GET, ADO_PROBE_URL, json={}, status=404)
            code = _run(runner_module, settings_path=settings, skills_dir=skills_dir)
        payload = _payload_from_capsys(capsys)
        assert code == 0
        gh_entry = next(e for e in payload["checks"] if e["name"] == "github_auth")
        assert gh_entry["status"] == "skipped"

    def test_unset_host_returns_two(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("RALPH_GIT_HOST", raising=False)
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        code = _run(runner_module, settings_path=settings, skills_dir=skills_dir)
        captured = capsys.readouterr()
        assert code == 2
        assert "RALPH_GIT_HOST" in captured.err

    def test_unknown_host_returns_two(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RALPH_GIT_HOST", "gitea")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        code = _run(runner_module, settings_path=settings, skills_dir=skills_dir)
        captured = capsys.readouterr()
        assert code == 2
        assert "gitea" in captured.err or "unknown" in captured.err.lower()

    def test_host_staging_runs_for_both_hosts(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Github run
        monkeypatch.setenv("RALPH_GIT_HOST", "github")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("GH_TOKEN", "x")
        monkeypatch.setenv("GH_OWNER", GITHUB_OWNER)
        monkeypatch.delenv("RALPH_USE_BEDROCK", raising=False)
        settings_gh = tmp_path / "settings_gh.json"
        _write_settings(settings_gh, GOOD_SETTINGS)
        skills_gh = _build_skills_dir(tmp_path / "gh", host="github")
        with responses.RequestsMock() as rsps:
            rsps.add(responses.POST, ANTHROPIC_TOKEN_URL, json={}, status=200)
            rsps.add(responses.GET, GITHUB_USER_URL, json={}, status=200)
            rsps.add(responses.GET, GITHUB_TEST_REPO_URL, json={}, status=404)
            _run(runner_module, settings_path=settings_gh, skills_dir=skills_gh)
        payload_gh = _payload_from_capsys(capsys)
        gh_staging = next(e for e in payload_gh["checks"] if e["name"] == "host_staging")
        assert gh_staging["status"] == "pass"

        # ADO run
        monkeypatch.setenv("RALPH_GIT_HOST", "ado")
        monkeypatch.setenv("ADO_PAT", "x")
        monkeypatch.setenv("ADO_ORG_URL", ADO_ORG)
        monkeypatch.setenv("ADO_PROJECT", ADO_PROJECT)
        monkeypatch.setenv("ADO_REPOSITORY", ADO_REPO)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GH_OWNER", raising=False)
        settings_ado = tmp_path / "settings_ado.json"
        _write_settings(settings_ado, GOOD_SETTINGS)
        skills_ado = _build_skills_dir(tmp_path / "ado", host="ado")
        with responses.RequestsMock() as rsps:
            rsps.add(responses.POST, ANTHROPIC_TOKEN_URL, json={}, status=200)
            rsps.add(responses.GET, ADO_PROBE_URL, json={}, status=404)
            _run(runner_module, settings_path=settings_ado, skills_dir=skills_ado)
        payload_ado = _payload_from_capsys(capsys)
        ado_staging = next(e for e in payload_ado["checks"] if e["name"] == "host_staging")
        assert ado_staging["status"] == "pass"

    def test_host_dispatch_message_points_at_orchestrator(
        self,
        runner_module: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("RALPH_GIT_HOST", raising=False)
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        code = _run(runner_module, settings_path=settings, skills_dir=skills_dir)
        captured = capsys.readouterr()
        assert code == 2
        assert "RALPH_GIT_HOST" in captured.err
        assert "orchestrator" in captured.err.lower()


# ----------------------------------------------------------------------
# TestPermissionsCheck
# ----------------------------------------------------------------------


class TestPermissionsCheck:
    def test_all_required_tools_present_passes(self, tmp_path: Path) -> None:
        mod = _load_check_module("permissions")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "pass"
        assert result.severity == "error"

    def test_missing_tool_fails(self, tmp_path: Path) -> None:
        mod = _load_check_module("permissions")
        settings = tmp_path / "settings.json"
        _write_settings(settings, BAD_PERMISSIONS_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "fail"
        missing = result.details.get("missing")
        assert isinstance(missing, list)
        for tool in ("Edit", "Write", "Grep", "Glob", "Skill"):
            assert tool in missing

    def test_global_wildcard_covers_everything(self, tmp_path: Path) -> None:
        mod = _load_check_module("permissions")
        settings = tmp_path / "settings.json"
        _write_settings(
            settings,
            {"permissions": {"allow": ["*"]}, "hooks": {}, "mcpServers": {}},
        )
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "pass"

    def test_missing_permissions_section_fails(self, tmp_path: Path) -> None:
        mod = _load_check_module("permissions")
        settings = tmp_path / "settings.json"
        _write_settings(settings, {"hooks": {}, "mcpServers": {}})
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "fail"

    def test_skill_subkey_recognised(self, tmp_path: Path) -> None:
        mod = _load_check_module("permissions")
        settings = tmp_path / "settings.json"
        _write_settings(
            settings,
            {
                "permissions": {
                    "allow": [
                        "Bash(*)",
                        "Edit(*)",
                        "Write(*)",
                        "Read(*)",
                        "Grep(*)",
                        "Glob(*)",
                        "Skill(pr)",
                    ]
                },
                "hooks": {},
                "mcpServers": {},
            },
        )
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "pass"


# ----------------------------------------------------------------------
# TestHooksCheck
# ----------------------------------------------------------------------


class TestHooksCheck:
    def test_clean_hooks_pass(self, tmp_path: Path) -> None:
        mod = _load_check_module("hooks")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "pass"

    def test_ask_user_question_in_hook_fails(self, tmp_path: Path) -> None:
        mod = _load_check_module("hooks")
        settings = tmp_path / "settings.json"
        _write_settings(settings, BAD_HOOK_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "fail"
        assert "AskUserQuestion" in result.message

    def test_read_p_in_hook_fails(self, tmp_path: Path) -> None:
        mod = _load_check_module("hooks")
        settings = tmp_path / "settings.json"
        data: dict[str, Any] = {
            "permissions": {"allow": list(_GOOD_ALLOW)},
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": ".*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "bash -c 'read -p \"go?\" reply'",
                            }
                        ],
                    }
                ]
            },
            "mcpServers": {},
        }
        _write_settings(settings, data)
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "fail"

    def test_async_hook_with_input_is_warn(self, tmp_path: Path) -> None:
        mod = _load_check_module("hooks")
        settings = tmp_path / "settings.json"
        _write_settings(settings, ASYNC_HOOK_WITH_INPUT_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "fail"
        assert result.severity == "warn"


# ----------------------------------------------------------------------
# TestSkillsCheck
# ----------------------------------------------------------------------


class TestSkillsCheck:
    def test_clean_skills_dir_passes(self, tmp_path: Path) -> None:
        mod = _load_check_module("skills")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        ctx = _make_context(settings, skills_dir)
        result = mod.check(ctx)
        assert result.status == "pass"

    def test_ask_user_question_in_skill_fails(self, tmp_path: Path) -> None:
        mod = _load_check_module("skills")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github", include_ask_user_question=True)
        ctx = _make_context(settings, skills_dir)
        result = mod.check(ctx)
        assert result.status == "fail"
        assert "asks-questions" in result.message

    def test_missing_skills_dir_is_pass_with_note(self, tmp_path: Path) -> None:
        mod = _load_check_module("skills")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path / "no-such-skills-dir")
        result = mod.check(ctx)
        assert result.status == "pass"
        assert "no skills directory" in result.message.lower()

    def test_offending_skill_name_handles_nested_scripts_subdir(self, tmp_path: Path) -> None:
        """``_scan_skill`` uses ``scripts_dir.rglob('*.py')`` so an offending
        file can sit several levels below ``scripts/``. The helper must
        report the SKILL directory, not the intermediate subdirectory it
        happens to live under (e.g. ``scripts/utils/helper.py`` belongs to
        ``my-skill``, not to ``utils``)."""
        mod = _load_check_module("skills")
        nested = tmp_path / "my-skill" / "scripts" / "utils" / "helper.py"
        assert mod._offending_skill_name(str(nested)) == "my-skill"
        # Sanity: the direct-child case still works.
        direct = tmp_path / "my-skill" / "scripts" / "main.py"
        assert mod._offending_skill_name(str(direct)) == "my-skill"

    def test_unclosed_ignore_marker_does_not_suppress_subsequent_content(self) -> None:
        """An unclosed ``<!-- ralph-doctor: ignore -->`` must FAIL OPEN: the
        scanner has to keep seeing content after it. Silently dropping the
        rest of the file would let a missing close tag mask any subsequent
        ``AskUserQuestion`` from this safety gate."""
        mod = _load_check_module("skills")
        body = (
            "# Skill\n"
            "<!-- ralph-doctor: ignore -->\n"
            "this text would be inside the ignore region.\n"
            "but the close marker is missing.\n"
            "AskUserQuestion('this must still be visible to the scanner')\n"
        )
        stripped = mod._strip_ignored_markdown_regions(body)
        assert "AskUserQuestion" in stripped, (
            "unclosed ignore marker must not silently suppress later content"
        )

    def test_ralph_doctor_skill_does_not_self_flag(self, tmp_path: Path) -> None:
        """The skills check scans every subdirectory of ``skills_dir``, and
        ralph-doctor's own files mention the interactive-prompt needle (in
        ``NEEDLE``, ``INTERACTIVE_INDICATORS``, docstrings, the SKILL.md
        documentation table). If ralph-doctor cannot pass its own check
        when installed alongside other skills at
        ``~/.claude/skills/ralph-doctor/``, the pod is blocked even with a
        correct environment — this is the false-positive self-detection
        BugBot flagged."""
        import shutil

        mod = _load_check_module("skills")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        # Copy the actual ralph-doctor skill alongside the synthetic
        # pr dir.
        shutil.copytree(REPO_ROOT / "skills" / "ralph-doctor", skills_dir / "ralph-doctor")
        ctx = _make_context(settings, skills_dir)
        result = mod.check(ctx)
        assert result.status == "pass", f"ralph-doctor must not self-flag; message={result.message}"

    def test_unclosed_ignore_marker_propagates_via_check(self, tmp_path: Path) -> None:
        """End-to-end: a SKILL.md with an unclosed ignore marker and an
        ``AskUserQuestion`` afterwards must be flagged by the skills check."""
        mod = _load_check_module("skills")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        bad = skills_dir / "leaky" / "SKILL.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text(
            _frontmatter("leaky")
            + "<!-- ralph-doctor: ignore -->\n"
            + "missing close tag\n"
            + "AskUserQuestion('still visible')\n",
            encoding="utf-8",
        )
        ctx = _make_context(settings, skills_dir)
        result = mod.check(ctx)
        assert result.status == "fail"
        assert "leaky" in result.message


# ----------------------------------------------------------------------
# TestMcpCheck
# ----------------------------------------------------------------------


class TestMcpCheck:
    def test_non_oauth_mcp_passes(self, tmp_path: Path) -> None:
        mod = _load_check_module("mcp")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "pass"

    def test_oauth_mcp_fails(self, tmp_path: Path) -> None:
        mod = _load_check_module("mcp")
        settings = tmp_path / "settings.json"
        _write_settings(settings, BAD_MCP_OAUTH_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "fail"

    def test_empty_mcp_is_pass(self, tmp_path: Path) -> None:
        mod = _load_check_module("mcp")
        settings = tmp_path / "settings.json"
        _write_settings(
            settings,
            {
                "permissions": {"allow": list(_GOOD_ALLOW)},
                "hooks": {},
                "mcpServers": {},
            },
        )
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "pass"


# ----------------------------------------------------------------------
# TestAuthCheck
# ----------------------------------------------------------------------


class TestAuthCheck:
    def test_anthropic_200_passes(
        self,
        tmp_path: Path,
        good_env_github: None,
    ) -> None:
        mod = _load_check_module("auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                ANTHROPIC_TOKEN_URL,
                json={"input_tokens": 1},
                status=200,
            )
            result = mod.check(ctx)
        assert result.status == "pass"

    def test_anthropic_401_fails(
        self,
        tmp_path: Path,
        good_env_github: None,
    ) -> None:
        mod = _load_check_module("auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                ANTHROPIC_TOKEN_URL,
                json={"error": {"type": "unauthorized"}},
                status=401,
            )
            result = mod.check(ctx)
        assert result.status == "fail"
        assert "401" in result.message

    def test_missing_api_key_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("RALPH_USE_BEDROCK", raising=False)
        mod = _load_check_module("auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "fail"
        assert "ANTHROPIC_API_KEY" in result.message


# ----------------------------------------------------------------------
# TestHostStagingCheck
# ----------------------------------------------------------------------


class TestHostStagingCheck:
    def test_github_staged_correctly_passes(self, tmp_path: Path) -> None:
        mod = _load_check_module("host_staging")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        ctx = _make_context(settings, skills_dir, git_host="github")
        result = mod.check(ctx)
        assert result.status == "pass"
        assert "pr-github" in result.message

    def test_ado_staged_correctly_passes(self, tmp_path: Path) -> None:
        mod = _load_check_module("host_staging")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="ado")
        ctx = _make_context(settings, skills_dir, git_host="ado")
        result = mod.check(ctx)
        assert result.status == "pass"

    def test_mismatch_fails(self, tmp_path: Path) -> None:
        mod = _load_check_module("host_staging")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github", stage_mismatch=True)
        ctx = _make_context(settings, skills_dir, git_host="github")
        result = mod.check(ctx)
        assert result.status == "fail"
        assert "pr-ado" in result.message
        assert "pr-github" in result.message
        assert "RALPH_GIT_HOST" in result.message
        assert "host_select" in result.message

    def test_missing_pr_skill_fails(self, tmp_path: Path) -> None:
        mod = _load_check_module("host_staging")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github", missing_skill="pr")
        ctx = _make_context(settings, skills_dir, git_host="github")
        result = mod.check(ctx)
        assert result.status == "fail"
        assert "pr/SKILL.md" in result.message or "pr\\SKILL.md" in result.message
        assert "not found" in result.message.lower()

    def test_malformed_frontmatter_fails(self, tmp_path: Path) -> None:
        mod = _load_check_module("host_staging")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        # Overwrite pr/SKILL.md with a file lacking frontmatter fences.
        (skills_dir / "pr" / "SKILL.md").write_text("no frontmatter here\n", encoding="utf-8")
        ctx = _make_context(settings, skills_dir, git_host="github")
        result = mod.check(ctx)
        assert result.status == "fail"
        assert "frontmatter" in result.message.lower()
        assert "name" in result.message.lower()

    def test_unknown_git_host_in_context_fails(self, tmp_path: Path) -> None:
        mod = _load_check_module("host_staging")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        skills_dir = _build_skills_dir(tmp_path, host="github")
        ctx = _make_context(settings, skills_dir, git_host="gitea")
        result = mod.check(ctx)
        assert result.status == "fail"
        assert "gitea" in result.message


# ----------------------------------------------------------------------
# TestGithubAuthCheck
# ----------------------------------------------------------------------


class TestGithubAuthCheck:
    def test_user_200_and_repo_404_passes(
        self,
        tmp_path: Path,
        good_env_github: None,
    ) -> None:
        mod = _load_check_module("github_auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, GITHUB_USER_URL, json={"login": "u"}, status=200)
            rsps.add(responses.GET, GITHUB_TEST_REPO_URL, json={}, status=404)
            result = mod.check(ctx)
        assert result.status == "pass"

    def test_user_401_fails(
        self,
        tmp_path: Path,
        good_env_github: None,
    ) -> None:
        mod = _load_check_module("github_auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                GITHUB_USER_URL,
                json={"message": "Bad credentials"},
                status=401,
            )
            result = mod.check(ctx)
        assert result.status == "fail"
        assert "401" in result.message
        assert "GH_TOKEN" in result.message

    def test_user_200_repo_403_fails_with_scope_hint(
        self,
        tmp_path: Path,
        good_env_github: None,
    ) -> None:
        mod = _load_check_module("github_auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, GITHUB_USER_URL, json={"login": "u"}, status=200)
            rsps.add(responses.GET, GITHUB_TEST_REPO_URL, json={"message": "Forbidden"}, status=403)
            result = mod.check(ctx)
        assert result.status == "fail"
        assert "403" in result.message
        assert "scope" in result.message.lower()

    def test_user_network_error_fails(
        self,
        tmp_path: Path,
        good_env_github: None,
    ) -> None:
        import requests

        mod = _load_check_module("github_auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                GITHUB_USER_URL,
                body=requests.ConnectionError("boom"),
            )
            result = mod.check(ctx)
        assert result.status == "fail"
        assert "raised" in result.message

    def test_missing_gh_token_fails(
        self,
        tmp_path: Path,
        good_env_github: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        mod = _load_check_module("github_auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "fail"
        assert "GH_TOKEN" in result.message

    def test_missing_gh_owner_fails(
        self,
        tmp_path: Path,
        good_env_github: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GH_OWNER", raising=False)
        mod = _load_check_module("github_auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path)
        result = mod.check(ctx)
        assert result.status == "fail"
        assert "GH_OWNER" in result.message


# ----------------------------------------------------------------------
# TestAdoAuthCheck
# ----------------------------------------------------------------------


class TestAdoAuthCheck:
    def test_404_passes(
        self,
        tmp_path: Path,
        good_env_ado: None,
    ) -> None:
        mod = _load_check_module("ado_auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path, git_host="ado")
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, ADO_PROBE_URL, json={}, status=404)
            result = mod.check(ctx)
        assert result.status == "pass"

    def test_401_fails(
        self,
        tmp_path: Path,
        good_env_ado: None,
    ) -> None:
        mod = _load_check_module("ado_auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path, git_host="ado")
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, ADO_PROBE_URL, json={}, status=401)
            result = mod.check(ctx)
        assert result.status == "fail"
        assert "401" in result.message

    def test_200_means_routing_is_wrong_and_fails(
        self,
        tmp_path: Path,
        good_env_ado: None,
    ) -> None:
        mod = _load_check_module("ado_auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path, git_host="ado")
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                ADO_PROBE_URL,
                json={"pullRequestId": NON_EXISTENT_PR_ID},
                status=200,
            )
            result = mod.check(ctx)
        assert result.status == "fail"
        assert "200" in result.message

    def test_missing_env_var_fails(
        self,
        tmp_path: Path,
        good_env_ado: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ADO_PAT", raising=False)
        mod = _load_check_module("ado_auth")
        settings = tmp_path / "settings.json"
        _write_settings(settings, GOOD_SETTINGS)
        ctx = _make_context(settings, tmp_path, git_host="ado")
        result = mod.check(ctx)
        assert result.status == "fail"
        assert "ADO_PAT" in result.message
