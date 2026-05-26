"""Tests for ``ralph_executor.host_select``.

Each task in the host-selection sequence adds its own tests to this
module -- start with ``select_host`` and ``verify_auth_env``, then
``stage_skills``/``verify_staged``, then ``prepare_host_environment``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_executor.host_select import (
    HostSelectionError,
    prepare_host_environment,
    select_host,
    stage_skills,
    verify_auth_env,
    verify_staged,
)

# --- select_host ------------------------------------------------------


def test_select_host_override_beats_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``override`` is supplied (non-blank), it wins over $RALPH_GIT_HOST.
    Mirrors the load_config layering where TOML/env feed cfg.git_host
    and cli passes that as the override."""
    monkeypatch.setenv("RALPH_GIT_HOST", "ado")
    assert select_host(override="github") == "github"


def test_select_host_override_blank_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_GIT_HOST", "github")
    assert select_host(override="") == "github"
    assert select_host(override=None) == "github"


def test_select_host_no_override_no_env_errors_mentioning_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Error message must point at BOTH env and TOML — operator could
    fix it via either knob and shouldn't have to guess."""
    monkeypatch.delenv("RALPH_GIT_HOST", raising=False)
    with pytest.raises(HostSelectionError) as exc_info:
        select_host()
    msg = str(exc_info.value)
    assert "RALPH_GIT_HOST" in msg
    assert "git_host" in msg
    assert "config.toml" in msg


def test_select_host_returns_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_GIT_HOST", "github")
    assert select_host() == "github"


def test_select_host_returns_ado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_GIT_HOST", "ado")
    assert select_host() == "ado"


def test_select_host_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_GIT_HOST", "GitHub")
    assert select_host() == "github"


def test_select_host_missing_env_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RALPH_GIT_HOST", raising=False)
    with pytest.raises(HostSelectionError) as excinfo:
        select_host()
    msg = str(excinfo.value)
    assert "RALPH_GIT_HOST" in msg
    assert "github" in msg
    assert "ado" in msg


def test_select_host_unknown_value_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_GIT_HOST", "gitlab")
    with pytest.raises(HostSelectionError) as excinfo:
        select_host()
    msg = str(excinfo.value)
    assert "RALPH_GIT_HOST" in msg
    assert "gitlab" in msg
    assert "github" in msg
    assert "ado" in msg


def test_select_host_empty_env_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_GIT_HOST", "   ")
    with pytest.raises(HostSelectionError, match="RALPH_GIT_HOST"):
        select_host()


# --- verify_auth_env --------------------------------------------------


def test_verify_auth_env_github_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_dummy")
    monkeypatch.setenv("GH_OWNER", "emp3thy")
    verify_auth_env("github")  # must not raise


def test_verify_auth_env_ado_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADO_PAT", "pat_dummy")
    monkeypatch.setenv("ADO_ORG_URL", "https://dev.azure.com/contoso")
    monkeypatch.setenv("ADO_PROJECT", "Contoso")
    verify_auth_env("ado")


def test_verify_auth_env_github_missing_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GH_OWNER", "emp3thy")
    with pytest.raises(HostSelectionError) as excinfo:
        verify_auth_env("github")
    msg = str(excinfo.value)
    assert "GH_TOKEN" in msg
    assert "github" in msg


def test_verify_auth_env_github_missing_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_dummy")
    monkeypatch.delenv("GH_OWNER", raising=False)
    with pytest.raises(HostSelectionError, match="GH_OWNER"):
        verify_auth_env("github")


def test_verify_auth_env_ado_missing_pat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADO_PAT", raising=False)
    monkeypatch.setenv("ADO_ORG_URL", "https://dev.azure.com/contoso")
    monkeypatch.setenv("ADO_PROJECT", "Contoso")
    with pytest.raises(HostSelectionError, match="ADO_PAT"):
        verify_auth_env("ado")


def test_verify_auth_env_ado_missing_org_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADO_PAT", "pat_dummy")
    monkeypatch.delenv("ADO_ORG_URL", raising=False)
    monkeypatch.setenv("ADO_PROJECT", "Contoso")
    with pytest.raises(HostSelectionError, match="ADO_ORG_URL"):
        verify_auth_env("ado")


def test_verify_auth_env_ado_missing_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADO_PAT", "pat_dummy")
    monkeypatch.setenv("ADO_ORG_URL", "https://dev.azure.com/contoso")
    monkeypatch.delenv("ADO_PROJECT", raising=False)
    with pytest.raises(HostSelectionError, match="ADO_PROJECT"):
        verify_auth_env("ado")


def test_verify_auth_env_unknown_host_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(HostSelectionError, match="unknown host"):
        verify_auth_env("gitlab")


def test_verify_auth_env_error_message_names_all_missing_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When multiple required vars are missing, the message names all of them."""
    monkeypatch.delenv("ADO_PAT", raising=False)
    monkeypatch.delenv("ADO_ORG_URL", raising=False)
    monkeypatch.delenv("ADO_PROJECT", raising=False)
    with pytest.raises(HostSelectionError) as excinfo:
        verify_auth_env("ado")
    msg = str(excinfo.value)
    assert "ADO_PAT" in msg
    assert "ADO_ORG_URL" in msg
    assert "ADO_PROJECT" in msg


# --- stage_skills / verify_staged ------------------------------------


def _make_fake_skill_source(skills_root: Path, host: str) -> None:
    """Build a synthetic ``skills/`` tree with the per-host skill dirs.

    Layout mirrors the real Phase 1 / Phase 2 structure: each skill
    directory has a ``scripts/`` subdir with one script, a SKILL.md,
    and a marker file so tests can prove the copy preserved contents.
    """
    pr_src = skills_root / f"pr-{host}"
    wi_src = skills_root / f"workitem-fetch-{host}"
    (pr_src / "scripts").mkdir(parents=True)
    (pr_src / "SKILL.md").write_text(f"# pr-{host} skill\n", encoding="utf-8")
    (pr_src / "scripts" / "create_pr.py").write_text(f"# create-pr for {host}\n", encoding="utf-8")
    (pr_src / "marker.txt").write_text(host, encoding="utf-8")

    (wi_src / "scripts").mkdir(parents=True)
    (wi_src / "SKILL.md").write_text(f"# workitem-fetch-{host} skill\n", encoding="utf-8")
    (wi_src / "scripts" / "fetch.py").write_text(f"# fetch for {host}\n", encoding="utf-8")
    (wi_src / "marker.txt").write_text(host, encoding="utf-8")


def test_stage_skills_copies_github_pair_to_canonical_names(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    claude_skills_dir = tmp_path / "claude_skills"
    claude_skills_dir.mkdir()
    _make_fake_skill_source(skills_root, "github")

    stage_skills("github", skills_root, claude_skills_dir)

    assert (claude_skills_dir / "pr" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# pr-github skill\n"
    assert (claude_skills_dir / "pr" / "scripts" / "create_pr.py").is_file()
    assert (claude_skills_dir / "pr" / "marker.txt").read_text(encoding="utf-8") == "github"
    assert (claude_skills_dir / "workitem-fetch" / "SKILL.md").is_file()
    assert (claude_skills_dir / "workitem-fetch" / "scripts" / "fetch.py").is_file()


def test_stage_skills_copies_ado_pair_to_canonical_names(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    claude_skills_dir = tmp_path / "claude_skills"
    claude_skills_dir.mkdir()
    _make_fake_skill_source(skills_root, "ado")

    stage_skills("ado", skills_root, claude_skills_dir)

    assert (claude_skills_dir / "pr" / "marker.txt").read_text(encoding="utf-8") == "ado"
    assert (claude_skills_dir / "workitem-fetch" / "marker.txt").read_text(
        encoding="utf-8"
    ) == "ado"


def test_stage_skills_is_idempotent(tmp_path: Path) -> None:
    """Re-running stage_skills must not raise (dirs_exist_ok semantics)."""
    skills_root = tmp_path / "skills"
    claude_skills_dir = tmp_path / "claude_skills"
    claude_skills_dir.mkdir()
    _make_fake_skill_source(skills_root, "github")

    stage_skills("github", skills_root, claude_skills_dir)
    stage_skills("github", skills_root, claude_skills_dir)  # second run

    assert (claude_skills_dir / "pr" / "SKILL.md").is_file()


def test_stage_skills_creates_claude_skills_dir_if_absent(
    tmp_path: Path,
) -> None:
    """When ~/.claude/skills doesn't yet exist, staging must create it."""
    skills_root = tmp_path / "skills"
    claude_skills_dir = tmp_path / "claude_skills_new"  # NOT created
    _make_fake_skill_source(skills_root, "github")

    stage_skills("github", skills_root, claude_skills_dir)

    assert claude_skills_dir.is_dir()
    assert (claude_skills_dir / "pr" / "SKILL.md").is_file()


def test_stage_skills_missing_pr_source_raises_clear_error(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    claude_skills_dir = tmp_path / "claude_skills"
    claude_skills_dir.mkdir()
    # Only the workitem-fetch source exists; pr-github is missing.
    (skills_root / "workitem-fetch-github" / "scripts").mkdir(parents=True)
    (skills_root / "workitem-fetch-github" / "scripts" / "fetch.py").write_text(
        "# fetch\n", encoding="utf-8"
    )

    with pytest.raises(HostSelectionError) as excinfo:
        stage_skills("github", skills_root, claude_skills_dir)
    msg = str(excinfo.value)
    assert "pr-github" in msg
    assert str(skills_root) in msg


def test_stage_skills_missing_workitem_fetch_source_is_tolerated(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """workitem-fetch-<host>/ is OPTIONAL (Plan 3 deferred). Missing dir
    must emit a warning and skip -- NOT raise. The executor only needs
    pr/; only supervisor skills consume workitem-fetch/.
    """
    import logging

    skills_root = tmp_path / "skills"
    claude_skills_dir = tmp_path / "claude_skills"
    claude_skills_dir.mkdir()
    (skills_root / "pr-ado" / "scripts").mkdir(parents=True)
    (skills_root / "pr-ado" / "scripts" / "create_pr.py").write_text(
        "# create-pr\n", encoding="utf-8"
    )

    with caplog.at_level(logging.WARNING, logger="ralph_executor.host_select"):
        stage_skills("ado", skills_root, claude_skills_dir)
    assert any("workitem-fetch-ado" in rec.message for rec in caplog.records)
    assert (claude_skills_dir / "pr" / "scripts" / "create_pr.py").is_file()


def test_stage_skills_unknown_host_raises(tmp_path: Path) -> None:
    with pytest.raises(HostSelectionError, match="unknown host"):
        stage_skills("gitlab", tmp_path / "skills", tmp_path / "claude")


def test_verify_staged_happy_path(tmp_path: Path) -> None:
    claude_skills_dir = tmp_path / "claude_skills"
    (claude_skills_dir / "pr" / "scripts").mkdir(parents=True)
    (claude_skills_dir / "pr" / "scripts" / "create_pr.py").write_text("# stub\n", encoding="utf-8")
    (claude_skills_dir / "workitem-fetch" / "scripts").mkdir(parents=True)
    (claude_skills_dir / "workitem-fetch" / "scripts" / "fetch.py").write_text(
        "# stub\n", encoding="utf-8"
    )
    verify_staged(claude_skills_dir)  # must not raise


def test_verify_staged_missing_pr_script_raises(tmp_path: Path) -> None:
    claude_skills_dir = tmp_path / "claude_skills"
    (claude_skills_dir / "workitem-fetch" / "scripts").mkdir(parents=True)
    (claude_skills_dir / "workitem-fetch" / "scripts" / "fetch.py").write_text("", encoding="utf-8")
    with pytest.raises(HostSelectionError) as excinfo:
        verify_staged(claude_skills_dir)
    msg = str(excinfo.value)
    assert "pr/scripts/create_pr.py" in msg


def test_verify_staged_missing_workitem_fetch_script_is_tolerated(
    tmp_path: Path,
) -> None:
    """workitem-fetch/ is optional (Plan 3 deferred). Verify must NOT
    require workitem-fetch/scripts/fetch.py -- only pr/scripts/create_pr.py.
    """
    claude_skills_dir = tmp_path / "claude_skills"
    (claude_skills_dir / "pr" / "scripts").mkdir(parents=True)
    (claude_skills_dir / "pr" / "scripts" / "create_pr.py").write_text("", encoding="utf-8")
    verify_staged(claude_skills_dir)  # must not raise


# --- prepare_host_environment ----------------------------------------


def test_prepare_host_environment_github_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root = tmp_path / "skills"
    claude_skills_dir = tmp_path / "claude_skills"
    _make_fake_skill_source(skills_root, "github")

    monkeypatch.setenv("RALPH_GIT_HOST", "github")
    monkeypatch.setenv("GH_TOKEN", "ghp_test")
    monkeypatch.setenv("GH_OWNER", "emp3thy")

    result = prepare_host_environment(
        skills_root=skills_root,
        claude_skills_dir=claude_skills_dir,
    )
    assert result == "github"
    assert (claude_skills_dir / "pr" / "scripts" / "create_pr.py").is_file()
    assert (claude_skills_dir / "workitem-fetch" / "scripts" / "fetch.py").is_file()


def test_prepare_host_environment_ado_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root = tmp_path / "skills"
    claude_skills_dir = tmp_path / "claude_skills"
    _make_fake_skill_source(skills_root, "ado")

    monkeypatch.setenv("RALPH_GIT_HOST", "ado")
    monkeypatch.setenv("ADO_PAT", "pat_test")
    monkeypatch.setenv("ADO_ORG_URL", "https://dev.azure.com/contoso")
    monkeypatch.setenv("ADO_PROJECT", "Contoso")

    result = prepare_host_environment(
        skills_root=skills_root,
        claude_skills_dir=claude_skills_dir,
    )
    assert result == "ado"


def test_prepare_host_environment_missing_host_raises_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env-check failure surfaces BEFORE any other check."""
    monkeypatch.delenv("RALPH_GIT_HOST", raising=False)
    with pytest.raises(HostSelectionError, match="RALPH_GIT_HOST"):
        prepare_host_environment(
            skills_root=tmp_path / "skills",
            claude_skills_dir=tmp_path / "claude",
        )


def test_prepare_host_environment_missing_auth_raises_before_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auth-check failure surfaces BEFORE staging is attempted."""
    skills_root = tmp_path / "skills"
    claude_skills_dir = tmp_path / "claude_skills"
    _make_fake_skill_source(skills_root, "github")

    monkeypatch.setenv("RALPH_GIT_HOST", "github")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GH_OWNER", "emp3thy")

    with pytest.raises(HostSelectionError, match="GH_TOKEN"):
        prepare_host_environment(
            skills_root=skills_root,
            claude_skills_dir=claude_skills_dir,
        )
    # Staging must not have run.
    assert not (claude_skills_dir / "pr").exists()


def test_prepare_host_environment_missing_skills_raises_after_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staging failure surfaces AFTER auth passed."""
    skills_root = tmp_path / "skills"  # empty -- no per-host dirs
    claude_skills_dir = tmp_path / "claude_skills"
    skills_root.mkdir()

    monkeypatch.setenv("RALPH_GIT_HOST", "github")
    monkeypatch.setenv("GH_TOKEN", "ghp_test")
    monkeypatch.setenv("GH_OWNER", "emp3thy")

    with pytest.raises(HostSelectionError, match="pr-github"):
        prepare_host_environment(
            skills_root=skills_root,
            claude_skills_dir=claude_skills_dir,
        )


def test_prepare_host_environment_uses_env_default_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When called with no kwargs, paths come from RALPH_SKILLS_ROOT
    / RALPH_CLAUDE_SKILLS_DIR env vars.
    """
    skills_root = tmp_path / "env_skills"
    claude_skills_dir = tmp_path / "env_claude_skills"
    _make_fake_skill_source(skills_root, "github")

    monkeypatch.setenv("RALPH_GIT_HOST", "github")
    monkeypatch.setenv("GH_TOKEN", "ghp_test")
    monkeypatch.setenv("GH_OWNER", "emp3thy")
    monkeypatch.setenv("RALPH_SKILLS_ROOT", str(skills_root))
    monkeypatch.setenv("RALPH_CLAUDE_SKILLS_DIR", str(claude_skills_dir))

    result = prepare_host_environment()
    assert result == "github"
    assert (claude_skills_dir / "pr" / "SKILL.md").is_file()


def test_prepare_host_environment_kwargs_override_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit kwargs take precedence over RALPH_SKILLS_ROOT /
    RALPH_CLAUDE_SKILLS_DIR.
    """
    env_root = tmp_path / "env_skills"
    env_claude = tmp_path / "env_claude"
    arg_root = tmp_path / "arg_skills"
    arg_claude = tmp_path / "arg_claude"
    # Only the kwarg-pointed source has the skill dirs; the env-pointed
    # one is empty. If kwargs win, prepare succeeds; if env wins, it
    # fails with "pr-github not found".
    _make_fake_skill_source(arg_root, "github")
    env_root.mkdir()
    env_claude.mkdir()

    monkeypatch.setenv("RALPH_GIT_HOST", "github")
    monkeypatch.setenv("GH_TOKEN", "ghp_test")
    monkeypatch.setenv("GH_OWNER", "emp3thy")
    monkeypatch.setenv("RALPH_SKILLS_ROOT", str(env_root))
    monkeypatch.setenv("RALPH_CLAUDE_SKILLS_DIR", str(env_claude))

    result = prepare_host_environment(
        skills_root=arg_root,
        claude_skills_dir=arg_claude,
    )
    assert result == "github"
    assert (arg_claude / "pr" / "SKILL.md").is_file()
    assert not (env_claude / "pr").exists()
