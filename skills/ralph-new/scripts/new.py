"""ralph-new — author a PBI directly into the ralph-queue.

Replaces the GitHub-issue / ralph-add submission flow. See
``docs/superpowers/specs/2026-05-30-ralph-new-design.md`` for the full
design.

Exit codes:
    0   — PBI written + (optionally) pushed
    2   — validation error, collision overflow, queue / auth error,
          --non-interactive missing required field
    130 — operator Ctrl+C (caller's KeyboardInterrupt propagates)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, cast

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT))

from ralph_executor.queue_clone import (  # noqa: E402
    QueueCloneError,
    ensure_queue_clone,
)
from ralph_executor.user_config import (  # noqa: E402
    read_queue_branch,
    read_queue_repo,
    read_workspace_root,
)


def _load_sibling(name: str) -> ModuleType:
    """Path-load a sibling script module (validate / slugify / writer).

    The skill is invoked as a standalone script (no parent package), so
    ``from . import validate`` is unavailable. Cached in ``sys.modules``
    so the writer's own ``_load_templates`` lookup re-uses the cached
    instance.
    """
    cached = sys.modules.get(f"ralph_new_{name}")
    if cached is not None:
        return cached
    path = _HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ralph_new_{name}", path)
    assert spec is not None and spec.loader is not None, f"cannot locate {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"ralph_new_{name}"] = module
    spec.loader.exec_module(module)
    return module


validate = _load_sibling("validate")
slugify = _load_sibling("slugify")
writer = _load_sibling("writer")

DEFAULT_SEVERITY = "normal"
DEFAULT_QUEUE_BRANCH = "ralph-queue"
STATES: tuple[str, ...] = (
    "inbox",
    "current",
    "pending-pr",
    "blocked",
    "done",
    "archive",
)
COLLISION_STATES: tuple[str, ...] = ("inbox", "current", "pending-pr")


# --- argument parsing -------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ralph-new",
        description="Author a new PBI directly into the ralph-queue.",
    )
    p.add_argument("--title")
    p.add_argument("--type", choices=("bug", "feature"))
    p.add_argument("--severity", choices=("critical", "high", "normal", "low"))
    p.add_argument("--target-repo")
    p.add_argument("--depends-on", action="append", default=[])
    p.add_argument("--parent-id")
    p.add_argument("--id")
    p.add_argument("--spec-path", default="")
    p.add_argument("--plan-path", default="")
    p.add_argument("--body-file", type=Path)
    p.add_argument("--reproduce-file", type=Path)
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--no-push", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check-depends-on", action="store_true")
    p.add_argument("--workspace", type=Path)
    p.add_argument("--queue-repo")
    p.add_argument("--queue-branch")
    return p


# --- prompts ----------------------------------------------------------


def _prompt(label: str, *, default: str | None = None) -> str:
    suffix = f" (default: {default})" if default else ""
    raw = input(f"{label}{suffix}: ").strip()
    return raw or (default or "")


def _multiline_prompt(label: str) -> str:
    print(f"{label} (blank line to finish):")
    lines: list[str] = []
    while True:
        line = input("  ")
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


# --- helpers ----------------------------------------------------------


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _list_existing_slugs_in_collision_states(queue_clone: Path) -> set[str]:
    """Slugs already taken across inbox/current/pending-pr."""
    out: set[str] = set()
    for state in COLLISION_STATES:
        state_dir = queue_clone / ".ralph" / state
        if not state_dir.is_dir():
            continue
        out.update(p.name for p in state_dir.iterdir() if p.is_dir())
    return out


def _depends_on_exists(queue_clone: Path, dep_id: str) -> bool:
    return any((queue_clone / ".ralph" / state / dep_id).is_dir() for state in STATES)


def _read_body_file(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            _fail(f"--body-file / --reproduce-file: cannot read {path!r}: {exc}")
        ) from exc


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def _resolve_required(
    value: str | None,
    *,
    name: str,
    non_interactive: bool,
    prompt_label: str | None = None,
) -> str | None:
    del name  # informational; caller emits the error message
    if value is not None and value != "":
        return value
    if non_interactive:
        return None
    label = prompt_label or "value"
    return _prompt(label).strip() or None


# --- per-type input builders ------------------------------------------


def _build_reproduce_inputs(args: argparse.Namespace) -> dict[str, str]:
    # --reproduce-file: embed file verbatim as REPRODUCE.md body (see SKILL.md
    # "read REPRODUCE.md body from file"). Presence of the `raw_body` KEY
    # (not its truthiness) signals writer to skip the structured-sections
    # template — an empty/whitespace-only file still means "use raw mode",
    # not "fall back to _Not provided_".
    if args.reproduce_file is not None:
        body = _read_body_file(args.reproduce_file).strip()
        return {"raw_body": body, "environment": "", "steps": "", "expected": "", "actual": ""}
    if args.non_interactive:
        raise SystemExit(_fail("bug PBIs require --reproduce-file under --non-interactive"))
    return {
        "environment": _multiline_prompt("Environment"),
        "steps": _multiline_prompt("Steps"),
        "expected": _multiline_prompt("Expected"),
        "actual": _multiline_prompt("Actual"),
    }


def _build_bug_inputs(args: argparse.Namespace, frontmatter: str, title: str) -> dict[str, Any]:
    if args.body_file is not None:
        body = _read_body_file(args.body_file).strip()
        return {
            "frontmatter": frontmatter,
            "title": title,
            "symptom": body,
            "root_cause": "",
            "impact": "",
            "acceptance_criteria": "",
            "reproduce": _build_reproduce_inputs(args),
        }
    if args.non_interactive:
        raise SystemExit(_fail("bug PBIs require --body-file under --non-interactive"))
    return {
        "frontmatter": frontmatter,
        "title": title,
        "symptom": _prompt("Symptom (one line)"),
        "root_cause": _multiline_prompt("Root cause (suspected)"),
        "impact": _multiline_prompt("Impact"),
        "acceptance_criteria": _multiline_prompt("Acceptance criteria"),
        "reproduce": _build_reproduce_inputs(args),
    }


def _read_plan_file(plan_path: str) -> str:
    try:
        return Path(plan_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(_fail(f"--plan-path {plan_path!r}: cannot read file: {exc}")) from exc


def _build_feature_inputs(args: argparse.Namespace, frontmatter: str, title: str) -> dict[str, Any]:
    plan_path = (args.plan_path or "").strip()
    plan_content = _read_plan_file(plan_path) if plan_path else ""
    if args.body_file is not None:
        return {
            "frontmatter": frontmatter,
            "title": title,
            "description": _read_body_file(args.body_file).strip(),
            "acceptance_criteria": "",
            "spec_path": args.spec_path or "",
            "plan_path": plan_path,
            "plan_content": plan_content,
        }
    if args.non_interactive:
        raise SystemExit(_fail("feature PBIs require --body-file under --non-interactive"))
    description = _multiline_prompt("Description")
    acceptance_criteria = _multiline_prompt("Acceptance criteria")
    spec_path = _prompt("Spec doc path", default=args.spec_path or "").strip()
    if not plan_path:
        plan_path = _prompt("Plan doc path (blank to write TODO stub)", default="").strip()
        if plan_path:
            plan_content = _read_plan_file(plan_path)
    return {
        "frontmatter": frontmatter,
        "title": title,
        "description": description,
        "acceptance_criteria": acceptance_criteria,
        "spec_path": spec_path,
        "plan_path": plan_path,
        "plan_content": plan_content,
    }


# --- orchestration ----------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    title = _resolve_required(
        args.title,
        name="title",
        non_interactive=args.non_interactive,
        prompt_label="Title",
    )
    if not title:
        return _fail("--title is required (or supply interactively without --non-interactive)")

    pbi_type = _resolve_required(
        args.type,
        name="type",
        non_interactive=args.non_interactive,
        prompt_label="Type [bug/feature]",
    )
    if not pbi_type:
        return _fail("--type is required")
    err = validate.validate_type(pbi_type)
    if err:
        return _fail(err)

    if args.severity:
        severity = args.severity
    elif args.non_interactive:
        severity = DEFAULT_SEVERITY
    else:
        severity = (
            _prompt("Severity [critical/high/normal/low]", default=DEFAULT_SEVERITY)
            or DEFAULT_SEVERITY
        )
    err = validate.validate_severity(severity)
    if err:
        return _fail(err)

    target_repo = _resolve_required(
        args.target_repo,
        name="target-repo",
        non_interactive=args.non_interactive,
        prompt_label="Target repo URL",
    )
    if not target_repo:
        return _fail("--target-repo is required")
    err = validate.validate_target_repo(target_repo)
    if err:
        return _fail(err)

    depends_on: list[str] = list(args.depends_on)
    if not depends_on and not args.non_interactive:
        raw = _prompt("Depends on (comma-separated PBI ids, blank for none)")
        if raw:
            depends_on = [s.strip() for s in raw.split(",") if s.strip()]
    err = validate.validate_depends_on_syntax(depends_on)
    if err:
        return _fail(err)

    parent_id: str | None = args.parent_id
    if parent_id:
        err = validate.validate_pbi_id(parent_id)
        if err:
            return _fail(f"--parent-id: {err}")

    # --- slug -----------------------------------------------------
    try:
        base_slug = args.id if args.id else slugify.slug_from_title(title)
    except ValueError as exc:
        return _fail(str(exc))
    err = validate.validate_pbi_id(base_slug)
    if err:
        return _fail(err)

    # --- gather body inputs --------------------------------------
    bug_inputs: dict[str, Any] | None
    feature_inputs: dict[str, Any] | None
    if pbi_type == "bug":
        bug_inputs = _build_bug_inputs(args, frontmatter="", title=title)
        feature_inputs = None
    else:
        bug_inputs = None
        feature_inputs = _build_feature_inputs(args, frontmatter="", title=title)

    # --- dry-run early exit (no clone, no write) -----------------
    if args.dry_run and not args.check_depends_on:
        if depends_on:
            print(
                "dry-run: skipping depends_on existence check (requires queue "
                "clone); pass --check-depends-on to force the clone",
                file=sys.stderr,
            )
        envelope: dict[str, Any] = {
            "dry_run": True,
            "pbi_id": base_slug,
            "type": pbi_type,
            "title": title,
            "severity": severity,
            "target_repo": target_repo,
            "depends_on": depends_on,
            "parent_id": parent_id,
        }
        print(json.dumps(envelope, indent=2))
        return 0

    # --- acquire queue clone -------------------------------------
    workspace = args.workspace or read_workspace_root() or (Path.home() / "ralph-workspaces")
    queue_repo = args.queue_repo or read_queue_repo()
    queue_branch = args.queue_branch or read_queue_branch() or DEFAULT_QUEUE_BRANCH
    if not queue_repo:
        return _fail(
            "queue_repo is not configured (set in ~/.ralph/config.toml or pass --queue-repo)"
        )
    try:
        queue_clone = ensure_queue_clone(workspace, queue_repo, queue_branch)
    except QueueCloneError as exc:
        return _fail(f"queue clone failed: {exc}")

    # --- resolve slug + collision --------------------------------
    existing = _list_existing_slugs_in_collision_states(queue_clone)
    try:
        pbi_id = slugify.resolve_collision(base_slug, existing=existing)
    except ValueError as exc:
        return _fail(str(exc))

    # --- depends_on existence check ------------------------------
    for dep in depends_on:
        if not _depends_on_exists(queue_clone, dep):
            return _fail(
                f"depends_on id {dep!r} not found in queue (searched "
                f"inbox / current / pending-pr / blocked / done / archive)"
            )

    # --- render frontmatter --------------------------------------
    pbi_type_lit = cast(Literal["bug", "feature"], pbi_type)
    frontmatter = writer.render_frontmatter(
        pbi_id=pbi_id,
        pbi_type=pbi_type_lit,
        severity=severity,
        target_repo=target_repo,
        depends_on=tuple(depends_on),
        parent_id=parent_id,
    )
    if bug_inputs is not None:
        bug_inputs["frontmatter"] = frontmatter
    if feature_inputs is not None:
        feature_inputs["frontmatter"] = frontmatter

    # --- dry-run with clone (depends_on already validated above) -
    if args.dry_run:
        envelope = {
            "dry_run": True,
            "pbi_id": pbi_id,
            "type": pbi_type,
            "title": title,
            "severity": severity,
            "target_repo": target_repo,
            "depends_on": depends_on,
            "parent_id": parent_id,
        }
        print(json.dumps(envelope, indent=2))
        return 0

    # --- write the PBI directory ---------------------------------
    pbi_dir = queue_clone / ".ralph" / "inbox" / pbi_id
    try:
        writer.write_pbi_dir(
            pbi_dir=pbi_dir,
            pbi_type=pbi_type_lit,
            bug_inputs=bug_inputs,
            feature_inputs=feature_inputs,
        )
    except FileExistsError as exc:
        return _fail(str(exc))

    # --- git add / commit / push ---------------------------------
    # Scope `git add` to the PBI dir we just wrote — `-A` would silently
    # pick up any pre-existing uncommitted state in the queue clone
    # (e.g. orphan files from a previous run that crashed after write
    # but before commit) and bundle them into this commit.
    try:
        _git(queue_clone, "add", "--", str(pbi_dir))
        _git(queue_clone, "commit", "-m", f"chore(queue): add {pbi_id}")
        if not args.no_push:
            _git(queue_clone, "push", "origin", queue_branch)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        return _fail(
            f"git command failed (exit {exc.returncode}): {' '.join(exc.cmd)}\n{stderr}"
            if stderr
            else f"git command failed (exit {exc.returncode}): {' '.join(exc.cmd)}"
        )

    envelope = {
        "dry_run": False,
        "pbi_id": pbi_id,
        "type": pbi_type,
        "title": title,
        "severity": severity,
        "target_repo": target_repo,
        "depends_on": depends_on,
        "parent_id": parent_id,
        "pushed": not args.no_push,
        "pbi_dir": str(pbi_dir),
    }
    print(json.dumps(envelope, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
