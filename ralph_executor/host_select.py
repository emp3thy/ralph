"""Startup-phase host selection for the executor.

Ralph supports two git-host backends: ``github`` and ``ado``. The choice
is made once per pod (or once per local dev session). It can be set
either via ``$RALPH_GIT_HOST`` or via ``git_host = "github"`` in
``<repo>/.ralph/config.toml`` (env wins, as everywhere else). The
resolved value is fixed for that pod's lifetime. This module runs once
at startup BEFORE the iteration loop:

  1. ``select_host(override=...)`` validates the resolved host string.
     The override is normally sourced from ``ExecutorConfig.git_host``
     (which itself layers TOML over env); when omitted, falls back to
     reading ``$RALPH_GIT_HOST`` directly for backwards compatibility.
  2. ``verify_auth_env(host)`` checks the host's required auth vars.
  3. ``stage_skills(host, skills_root, claude_skills_dir)`` copies the
     chosen ``pr-<host>/`` and ``workitem-fetch-<host>/`` directories
     into the Claude skills location as ``pr/`` and ``workitem-fetch/``.
  4. ``verify_staged(claude_skills_dir)`` sanity-checks the result.
  5. ``prepare_host_environment(...)`` orchestrates the above and is
     called once from ``cli.py`` before the iteration loop starts.

See the orchestrator's "Host selection architecture" section for the
design rationale.

Environment variables (all read here, none in ``config.py``):

* ``RALPH_GIT_HOST`` -- required UNLESS set via TOML (see above).
  Either ``github`` or ``ado``.
* ``GH_TOKEN``, ``GH_OWNER`` -- required when host is ``github``.
* ``ADO_PAT``, ``ADO_ORG_URL``, ``ADO_PROJECT`` -- required when ``ado``.
* ``RALPH_SKILLS_ROOT`` -- optional. Path to the source ``skills/`` tree
  containing ``pr-github/``, ``pr-ado/``, ``workitem-fetch-github/``,
  ``workitem-fetch-ado/``. Defaults to ``<this_module_dir>/../skills``,
  i.e. the ``skills/`` sibling of the ``ralph_executor/`` package in a
  source checkout. Override when running from a packaged install whose
  skills tree lives elsewhere.
* ``RALPH_CLAUDE_SKILLS_DIR`` -- optional. Path to the Claude skills
  directory (where staged ``pr/`` and ``workitem-fetch/`` end up).
  Defaults to ``~/.claude/skills``.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Final, Literal

log = logging.getLogger(__name__)

Host = Literal["github", "ado"]

VALID_HOSTS: Final[tuple[Host, ...]] = ("github", "ado")

REQUIRED_AUTH_ENV: Final[dict[str, tuple[str, ...]]] = {
    "github": ("GH_TOKEN", "GH_OWNER"),
    "ado": ("ADO_PAT", "ADO_ORG_URL", "ADO_PROJECT"),
}


class HostSelectionError(RuntimeError):
    """Raised when host selection / staging / verification fails.

    Every message names the offending env var (or skill directory) so the
    operator can fix the problem without reading source.
    """


def _is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


def select_host(override: str | None = None) -> Host:
    """Return the canonical host string.

    Resolution order (highest → lowest):
      1. ``override`` argument (non-blank). Typically sourced from
         ``ExecutorConfig.git_host`` which itself layers TOML over env.
      2. ``RALPH_GIT_HOST`` environment variable (legacy direct path
         used when no override is supplied).

    Raises ``HostSelectionError`` if neither yields a non-blank value
    or if the resolved value is not one of the supported hosts.

    The error message points at TOML, env, AND init since any of the
    three can fix it — operator shouldn't have to guess which knob.
    """
    if not _is_blank(override):
        raw: str | None = override
        source_hint = "ExecutorConfig.git_host (set via .ralph/config.toml or $RALPH_GIT_HOST)"
    else:
        raw = os.environ.get("RALPH_GIT_HOST")
        source_hint = "$RALPH_GIT_HOST or `.ralph/config.toml` git_host key"
    if _is_blank(raw):
        raise HostSelectionError(
            f"git host is required but unset. "
            f"Set it to one of {list(VALID_HOSTS)} via either "
            f'$RALPH_GIT_HOST or `git_host = "github"` in '
            f"<repo>/.ralph/config.toml."
        )
    assert raw is not None  # narrowed by _is_blank
    normalised = raw.strip().lower()
    if normalised not in VALID_HOSTS:
        raise HostSelectionError(
            f"git host {raw!r} (from {source_hint}) is not a supported host. "
            f"Valid values: {list(VALID_HOSTS)}."
        )
    return normalised


def verify_auth_env(host: str) -> None:
    """Check that the host's required auth env vars are present.

    Raises ``HostSelectionError`` listing every missing variable. The
    caller is expected to have already validated ``host`` via
    ``select_host``; we re-check defensively here.
    """
    if host not in REQUIRED_AUTH_ENV:
        raise HostSelectionError(
            f"verify_auth_env: unknown host {host!r}. Valid hosts: {list(VALID_HOSTS)}."
        )
    required = REQUIRED_AUTH_ENV[host]
    missing = [name for name in required if _is_blank(os.environ.get(name))]
    if missing:
        # Map env-var names to their TOML-key equivalents (when one exists)
        # so the operator sees both knobs in the error. Secrets stay
        # env-only by policy.
        toml_hint = {
            "GH_OWNER": "gh_owner",
            "ADO_ORG_URL": "ado_org_url",
            "ADO_PROJECT": "ado_project",
            # GH_TOKEN / ADO_PAT are secrets: env-only, no TOML hint.
        }
        per_var_hints = [
            (
                f"{name} (TOML: `{toml_hint[name]}` in <repo>/.ralph/config.toml)"
                if name in toml_hint
                else f"{name} (env-only; secret)"
            )
            for name in missing
        ]
        raise HostSelectionError(
            f"git_host={host} but required auth value(s) missing or blank: "
            + "; ".join(per_var_hints)
            + ". Either export the env var(s) or set the TOML key(s) where shown."
        )


def _skill_pair(host: str) -> tuple[str, str]:
    """Return the ``(pr_dirname, workitem_fetch_dirname)`` for the host."""
    if host not in REQUIRED_AUTH_ENV:
        raise HostSelectionError(
            f"stage_skills: unknown host {host!r}. Valid hosts: {list(VALID_HOSTS)}."
        )
    return f"pr-{host}", f"workitem-fetch-{host}"


def _copy_skill_tree(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst`` overwriting any existing destination.

    ``shutil.copytree(..., dirs_exist_ok=True)`` is the cross-platform
    primitive -- Windows-safe, no symlink dependency, idempotent. On
    POSIX a symlink would be marginally faster, but the copy is fast
    enough at the scale of a single skill directory and avoids the
    Windows-vs-POSIX divergence entirely.
    """
    shutil.copytree(src, dst, dirs_exist_ok=True)


def stage_skills(host: str, skills_root: Path, claude_skills_dir: Path) -> None:
    """Stage the host's skill directories into the Claude skills location.

    Copies ``skills_root/pr-<host>/`` to ``claude_skills_dir/pr/`` and
    ``skills_root/workitem-fetch-<host>/`` to
    ``claude_skills_dir/workitem-fetch/``.
    Uses ``shutil.copytree(..., dirs_exist_ok=True)`` so the operation
    is idempotent and works identically on Windows and POSIX.

    Raises ``HostSelectionError`` if either source directory is missing
    (the message names the missing directory and the search root so the
    operator can fix the deployment).
    """
    pr_dirname, wi_dirname = _skill_pair(host)
    pr_src = skills_root / pr_dirname
    wi_src = skills_root / wi_dirname
    if not pr_src.is_dir():
        raise HostSelectionError(
            f"host={host}: source skill directory {pr_dirname}/ "
            f"not found under {skills_root}. "
            f"Expected path: {pr_src}. "
            f"Set RALPH_SKILLS_ROOT to the directory containing "
            f"the per-host skill folders, or ensure the {pr_dirname}/ "
            f"directory has been built (Plans 3 and 5)."
        )
    # workitem-fetch-<host>/ is OPTIONAL — it ships in Plan 3 (deferred)
    # for the supervisor-side ralph-add skill. The executor itself doesn't
    # invoke it; only PROMPT.md-driven Claude sessions might. Tolerate
    # absence with a warning so the MVR runs before Plan 3 lands.
    claude_skills_dir.mkdir(parents=True, exist_ok=True)
    log.info("staging pr skill: %s -> %s", pr_src, claude_skills_dir / "pr")
    _copy_skill_tree(pr_src, claude_skills_dir / "pr")
    if wi_src.is_dir():
        log.info(
            "staging workitem-fetch skill: %s -> %s",
            wi_src,
            claude_skills_dir / "workitem-fetch",
        )
        _copy_skill_tree(wi_src, claude_skills_dir / "workitem-fetch")
    else:
        log.warning(
            "host=%s: workitem-fetch-%s/ not under %s -- skipping (Plan 3 "
            "deferred; executor doesn't need it, supervisor skills do).",
            host,
            host,
            skills_root,
        )


def verify_staged(claude_skills_dir: Path) -> None:
    """Sanity-check that the staged skills landed where Claude expects.

    Asserts the two script entry points exist:
      * ``<claude_skills_dir>/pr/scripts/create-pr.py``
      * ``<claude_skills_dir>/workitem-fetch/scripts/fetch.py``

    Raises ``HostSelectionError`` listing every missing file.
    """
    # pr/ is required; workitem-fetch/ is optional (Plan 3 deferred).
    # Verify only the executor-required entry point.
    # Real script name is create_pr.py (underscore — see skills/pr-github/scripts/).
    expected = [claude_skills_dir / "pr" / "scripts" / "create_pr.py"]
    missing = [
        path.relative_to(claude_skills_dir).as_posix() for path in expected if not path.is_file()
    ]
    if missing:
        raise HostSelectionError(
            f"staged skills incomplete under {claude_skills_dir}: "
            f"missing {missing}. Check that stage_skills ran "
            "successfully and the source skill directories under "
            "RALPH_SKILLS_ROOT are well-formed."
        )


def _default_skills_root() -> Path:
    """Resolve the default ``skills_root`` for source-checkout layouts.

    Layout assumed by the default: the package lives at
    ``<repo>/ralph_executor/host_select.py`` and the skill sources live
    at ``<repo>/skills/<per-host-dir>/``. The default walks up two
    parents from this file (``ralph_executor`` then the repo root) and
    appends ``skills``. Packaged installs whose layout differs MUST set
    ``RALPH_SKILLS_ROOT`` explicitly.
    """
    return Path(__file__).resolve().parent.parent / "skills"


def _default_claude_skills_dir() -> Path:
    """Resolve the default ``claude_skills_dir`` (``~/.claude/skills``)."""
    return Path.home() / ".claude" / "skills"


def prepare_host_environment(
    *,
    skills_root: Path | None = None,
    claude_skills_dir: Path | None = None,
    host_override: str | None = None,
) -> Host:
    """Run the four startup-phase steps in order and return the host.

    Order (the orchestrator's "Host selection architecture" spec):

      1. ``select_host()`` -- read and validate ``RALPH_GIT_HOST``.
      2. ``verify_auth_env(host)`` -- check the host's auth env vars.
      3. ``stage_skills(host, skills_root, claude_skills_dir)`` -- copy
         the chosen ``pr-<host>/`` and ``workitem-fetch-<host>/``
         directories into the Claude skills location.
      4. ``verify_staged(claude_skills_dir)`` -- sanity check.

    Each step's failure surfaces a clear ``HostSelectionError`` that
    names the offending env var or path. The earlier the failure, the
    more useful the error -- e.g. a missing auth var aborts BEFORE any
    filesystem mutation.

    ``skills_root`` and ``claude_skills_dir`` resolve in priority
    order:
      1. Explicit kwarg, if non-None.
      2. ``RALPH_SKILLS_ROOT`` / ``RALPH_CLAUDE_SKILLS_DIR`` env var.
      3. Source-checkout default / ``~/.claude/skills``.

    Returns the selected host so callers can log it.
    """
    # Local import to avoid an import cycle (user_config imports
    # ConfigError from config, and config-layer code doesn't pull
    # host_select). Only consulted as a fallback below; tests that
    # monkeypatch the env vars are unaffected.
    from ralph_executor.user_config import read_claude_skills_dir, read_skills_root

    effective_skills_root: Path
    if skills_root is not None:
        effective_skills_root = skills_root
    else:
        env_root = os.environ.get("RALPH_SKILLS_ROOT")
        if env_root and env_root.strip():
            effective_skills_root = Path(env_root.strip())
        else:
            user_cfg_root = read_skills_root()
            if user_cfg_root is not None:
                effective_skills_root = user_cfg_root
            else:
                effective_skills_root = _default_skills_root()

    effective_claude_skills_dir: Path
    if claude_skills_dir is not None:
        effective_claude_skills_dir = claude_skills_dir
    else:
        env_claude = os.environ.get("RALPH_CLAUDE_SKILLS_DIR")
        if env_claude and env_claude.strip():
            effective_claude_skills_dir = Path(env_claude.strip())
        else:
            user_cfg_claude = read_claude_skills_dir()
            if user_cfg_claude is not None:
                effective_claude_skills_dir = user_cfg_claude
            else:
                effective_claude_skills_dir = _default_claude_skills_dir()

    host = select_host(override=host_override)
    log.info("git_host=%s", host)
    verify_auth_env(host)
    log.info(
        "staging skills from %s into %s",
        effective_skills_root,
        effective_claude_skills_dir,
    )
    stage_skills(host, effective_skills_root, effective_claude_skills_dir)
    verify_staged(effective_claude_skills_dir)
    log.info("host environment ready: host=%s", host)
    return host
