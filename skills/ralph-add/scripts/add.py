"""``ralph-add`` skill entry point — host-agnostic orchestrator.

Delegates the work-item fetch to a sibling ``workitem-fetch/`` skill
(either ``workitem-fetch-github`` in Phase 1 or ``workitem-fetch-ado`` in
Phase 2). Knows nothing about GitHub or Azure DevOps APIs. Reads the
fetcher's normalised JSON output, packages it into a PBI directory, and
commits + pushes to ``ralph-queue``.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_QUEUE_BRANCH = "ralph-queue"
ATTACHMENT_SUBDIR = "attachments"
ALLOWED_SEVERITIES = ("critical", "high", "normal", "low")

_REPO_ROOT = Path(__file__).resolve().parents[3]

_SCHEMA_PATH = _REPO_ROOT / "skills" / "workitem-fetch-github" / "scripts" / "schema.py"


def _load_schema_validator() -> Callable[[object], list[str]]:
    spec = importlib.util.spec_from_file_location("wi_schema", _SCHEMA_PATH)
    if spec is None or spec.loader is None:

        def _fallback_validate(doc: object) -> list[str]:
            if not isinstance(doc, dict):
                return ["document must be a JSON object"]
            required = (
                "id",
                "type",
                "severity",
                "title",
                "body_markdown",
                "acceptance_markdown",
                "repro_steps_markdown",
                "attachments",
                "child_ids",
                "parent_id",
                "source_url",
                "source_host",
                "raw",
            )
            return [f"missing required key: {k}" for k in required if k not in doc]

        return _fallback_validate
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn: Callable[[object], list[str]] = module.validate
    return fn


validate = _load_schema_validator()


@dataclass
class AddResult:
    work_item_id: str
    pbi_id: str
    pbi_type: str
    pbi_path: str
    repo_path: str
    branch: str
    attachments_downloaded: int
    children_expanded: int
    commit_sha: str
    pushed: bool
    dry_run: bool
    source_host: str


class _FatalError(RuntimeError):
    """Raised internally to signal a clean exit with code 2."""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ralph-add",
        description=(
            "Host-agnostic orchestrator. Fetches a work item via the staged "
            "workitem-fetch/ skill and submits it as a PBI to the ralph-queue "
            "branch of a target service repo."
        ),
    )
    parser.add_argument("--work-item", required=True, help="Work item id.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--repo", help="Path to an existing checkout of the target repo.")
    group.add_argument(
        "--repo-url",
        help="Git URL of the target repo. Reserved — not implemented in v1.",
    )
    parser.add_argument(
        "--severity",
        choices=ALLOWED_SEVERITIES,
        help="Override severity returned by the fetcher.",
    )
    parser.add_argument(
        "--expand-children",
        action="store_true",
        help="Write one PBI per child instead of one for the parent.",
    )
    parser.add_argument(
        "--via-mcp",
        action="store_true",
        help="Reserved for v2. Raises NotImplementedError in v1.",
    )
    parser.add_argument(
        "--branch",
        default=os.environ.get("RALPH_QUEUE_BRANCH", DEFAULT_QUEUE_BRANCH),
        help=f"Queue branch name (default: {DEFAULT_QUEUE_BRANCH}).",
    )
    parser.add_argument("--no-push", action="store_true", help="Commit but do not push.")
    parser.add_argument("--dry-run", action="store_true", help="Compute without mutating.")
    return parser.parse_args(argv)


def _resolve_fetcher() -> Path:
    override = os.environ.get("RALPH_WORKITEM_FETCH_SCRIPT", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_file():
            raise _FatalError(f"RALPH_WORKITEM_FETCH_SCRIPT points at non-existent file: {path}")
        return path

    home_staged = Path.home() / ".claude" / "skills" / "workitem-fetch" / "scripts" / "fetch.py"
    if home_staged.is_file():
        return home_staged

    host = os.environ.get("RALPH_GIT_HOST", "").strip().lower()
    if host == "github":
        candidate = _REPO_ROOT / "skills" / "workitem-fetch-github" / "scripts" / "fetch.py"
        if candidate.is_file():
            return candidate
    if host == "ado":
        candidate = _REPO_ROOT / "skills" / "workitem-fetch-ado" / "scripts" / "fetch.py"
        if candidate.is_file():
            return candidate

    raise _FatalError(
        "could not locate workitem-fetch fetcher; set RALPH_GIT_HOST or stage "
        "the skill first (see SKILL.md → 'How it finds the fetcher')"
    )


def _invoke_fetcher(fetcher: Path, work_item: str) -> dict[str, Any]:
    """Run the fetcher and parse its stdout as a normalised JSON document."""
    print(f"invoking fetcher {fetcher}...", file=sys.stderr)
    result = subprocess.run(
        [sys.executable, str(fetcher), "--work-item", work_item],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or "(no stderr)"
        raise _FatalError(f"workitem-fetch fetcher exited {result.returncode}: {stderr}")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise _FatalError(f"workitem-fetch fetcher produced invalid JSON: {exc}") from exc
    errors = validate(parsed)
    if errors:
        raise _FatalError(
            "workitem-fetch fetcher produced a schema-invalid document:\n  - "
            + "\n  - ".join(errors)
        )
    if not isinstance(parsed, dict):
        raise _FatalError("fetcher output was not a JSON object")
    return parsed


def _normalise_work_item_arg(raw: str) -> str:
    stripped = raw.strip().lstrip("#")
    if stripped.lower().startswith("wi-"):
        return f"WI-{stripped[3:]}"
    return stripped


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _render_frontmatter(
    pbi_id: str,
    pbi_type: str,
    severity: str,
    parent_id: str | None,
) -> str:
    now = _now_iso()
    lines = [
        "---",
        f"id: {pbi_id}",
        f"type: {pbi_type}",
        "status: inbox",
        f"severity: {severity}",
        "attempts: 0",
        f"created_at: {now}",
        f"updated_at: {now}",
    ]
    if parent_id:
        lines.append(f"parent_id: {parent_id}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _render_feature_body(doc: dict[str, Any]) -> str:
    title = str(doc["title"]).strip()
    description = str(doc["body_markdown"]).strip()
    acceptance = str(doc["acceptance_markdown"]).strip()
    attachments = doc["attachments"]
    source_url = str(doc["source_url"])
    source_host = str(doc["source_host"])

    parts: list[str] = [f"# {title}", ""]
    if description:
        parts.extend(["## Context", "", description, ""])
    if acceptance:
        parts.extend(["## Acceptance criteria", "", acceptance, ""])
    if attachments:
        parts.extend(["## Attachments", ""])
        for att in attachments:
            parts.append(f"- `{ATTACHMENT_SUBDIR}/{att['name']}` (from {att['url']})")
        parts.append("")
    parts.extend(
        [
            "## Source",
            "",
            f"- {source_host}: <{source_url}>",
            f"- Submitted by `ralph-add` at {_now_iso()}.",
            "",
        ]
    )
    return "\n".join(parts).rstrip() + "\n"


def _render_bug_body(doc: dict[str, Any]) -> str:
    title = str(doc["title"]).strip()
    description = str(doc["body_markdown"]).strip()
    attachments = doc["attachments"]
    source_url = str(doc["source_url"])
    source_host = str(doc["source_host"])

    parts: list[str] = [f"# {title}", ""]
    if description:
        parts.extend(["## Failure context", "", description, ""])
    if attachments:
        parts.extend(["## Attachments", ""])
        for att in attachments:
            parts.append(f"- `{ATTACHMENT_SUBDIR}/{att['name']}` (from {att['url']})")
        parts.append("")
    parts.extend(
        [
            "## Source",
            "",
            f"- {source_host}: <{source_url}>",
            f"- Submitted by `ralph-add` at {_now_iso()}.",
            "",
        ]
    )
    return "\n".join(parts).rstrip() + "\n"


def _render_reproduce(doc: dict[str, Any]) -> str:
    steps = str(doc["repro_steps_markdown"]).strip()
    pbi_id = str(doc["id"])
    header = f"# Reproduce — {pbi_id}\n\n"
    if not steps:
        return (
            header + "_No reproducer was supplied with the work item. "
            "Add reproduction steps before Ralph can investigate._\n"
        )
    return header + steps + "\n"


def _render_plan(doc: dict[str, Any]) -> str:
    title = str(doc["title"]).strip()
    pbi_id = str(doc["id"])
    return (
        f"# Implementation plan — {pbi_id} — {title}\n\n"
        "- [ ] 1. Read PBI.md end-to-end and confirm the acceptance "
        "criteria are unambiguous; if not, write STUCK.md.\n"
        "- [ ] 2. Sketch the smallest change set that satisfies the "
        "acceptance criteria.\n"
        "- [ ] 3. Add a failing test that pins the new behaviour.\n"
        "- [ ] 4. Implement the change. Keep the diff minimal.\n"
        "- [ ] 5. Run the full test suite; resolve regressions.\n"
        "- [ ] 6. Update any user-facing docs touched by the change.\n"
    )


def _write_pbi_directory(
    base_dir: Path,
    doc: dict[str, Any],
    severity: str,
    parent_id: str | None,
) -> Path:
    pbi_id = str(doc["id"])
    pbi_type = str(doc["type"])
    pbi_dir = base_dir / ".ralph" / "inbox" / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = _render_frontmatter(
        pbi_id=pbi_id,
        pbi_type=pbi_type,
        severity=severity,
        parent_id=parent_id,
    )
    if pbi_type == "bug":
        (pbi_dir / "BUG.md").write_text(frontmatter + _render_bug_body(doc), encoding="utf-8")
        (pbi_dir / "REPRODUCE.md").write_text(_render_reproduce(doc), encoding="utf-8")
    else:
        (pbi_dir / "PBI.md").write_text(frontmatter + _render_feature_body(doc), encoding="utf-8")
        (pbi_dir / "PLAN.md").write_text(_render_plan(doc), encoding="utf-8")
    (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")

    attachments = doc["attachments"]
    if attachments:
        attachments_dir = pbi_dir / ATTACHMENT_SUBDIR
        attachments_dir.mkdir(exist_ok=True)
        for att in attachments:
            # Strip directory components from the fetcher-supplied name to
            # prevent path traversal: a hostile / buggy fetcher emitting
            # `"../../sensitive"` would otherwise escape attachments_dir
            # via Path's join semantics. add.py is host-agnostic and must
            # not trust that all fetchers (current GitHub, future ADO,
            # third-party) sanitize names.
            name = Path(str(att["name"])).name
            if not name:
                raise _FatalError(
                    f"attachment name {att['name']!r} resolves to empty after "
                    f"directory-component stripping"
                )
            try:
                payload = base64.b64decode(att["content_base64"], validate=True)
            except (ValueError, TypeError) as exc:
                raise _FatalError(f"attachment {name!r} has invalid base64 payload: {exc}") from exc
            (attachments_dir / name).write_bytes(payload)
    return pbi_dir


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _ensure_git_repo(repo: Path) -> None:
    if not (repo / ".git").exists():
        raise _FatalError(f"{repo} is not a git repository (no .git/ directory)")


def _checkout_queue_branch(repo: Path, branch: str) -> None:
    local_branches = _run_git(repo, "branch", "--list", branch)
    if local_branches.strip():
        _run_git(repo, "checkout", branch)
    else:
        remote_branches = _run_git(repo, "branch", "-r", "--list", f"origin/{branch}")
        if remote_branches.strip():
            _run_git(repo, "checkout", "-b", branch, f"origin/{branch}")
        else:
            raise _FatalError(
                f"branch {branch!r} not found locally or as origin/{branch}; "
                "run docs/runbooks/ralph-queue-setup.md first"
            )


def _commit_pbi(repo: Path, pbi_dir: Path, message: str) -> str:
    _run_git(repo, "add", str(pbi_dir.relative_to(repo)))
    _run_git(repo, "commit", "-m", message)
    return _run_git(repo, "rev-parse", "HEAD")


def _push(repo: Path, branch: str) -> None:
    _run_git(repo, "push", "origin", branch)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        if args.via_mcp:
            raise _FatalError("NotImplemented: --via-mcp is reserved for v2; remove the flag.")
        if args.repo_url:
            raise _FatalError(
                "--repo-url is documented but not yet implemented in v1; "
                "clone the repo manually and pass --repo <path>"
            )

        repo = Path(args.repo).resolve()
        if not repo.is_dir():
            raise _FatalError(f"--repo path does not exist: {repo}")
        _ensure_git_repo(repo)

        fetcher = _resolve_fetcher()
        work_item_arg = _normalise_work_item_arg(args.work_item)

        if not args.dry_run:
            _checkout_queue_branch(repo, args.branch)

        root_doc = _invoke_fetcher(fetcher, work_item_arg)
        severity_override = args.severity

        attachments_total = 0
        children_expanded = 0
        last_commit_sha = ""
        first_pbi_for_report: tuple[str, str, str] | None = None
        pushed = False

        if args.expand_children:
            child_ids = list(root_doc.get("child_ids") or [])
            if not child_ids:
                raise _FatalError(
                    f"work item {root_doc['id']} has no child links; nothing to expand"
                )
            for child_id in child_ids:
                child_doc = _invoke_fetcher(fetcher, child_id)
                child_doc["parent_id"] = str(root_doc["id"])
                child_severity = severity_override or str(child_doc["severity"])
                if not args.dry_run:
                    pbi_dir = _write_pbi_directory(
                        base_dir=repo,
                        doc=child_doc,
                        severity=child_severity,
                        parent_id=str(root_doc["id"]),
                    )
                else:
                    pbi_dir = repo / ".ralph" / "inbox" / str(child_doc["id"])
                # Mirror the non-expand path (line 468 below): no files
                # are written in dry-run, so the reported count must be 0.
                # Counting them here would make the dry-run JSON output
                # inconsistent with what actually happened on disk.
                if not args.dry_run:
                    attachments_total += len(child_doc.get("attachments") or [])
                children_expanded += 1
                if first_pbi_for_report is None:
                    first_pbi_for_report = (
                        str(child_doc["id"]),
                        str(child_doc["type"]),
                        str(pbi_dir.relative_to(repo)).replace("\\", "/"),
                    )
                if not args.dry_run:
                    message = (
                        f"feat(ralph-queue): add {child_doc['id']} (child of {root_doc['id']})"
                    )
                    last_commit_sha = _commit_pbi(repo, pbi_dir, message)
        else:
            severity = severity_override or str(root_doc["severity"])
            if not args.dry_run:
                pbi_dir = _write_pbi_directory(
                    base_dir=repo,
                    doc=root_doc,
                    severity=severity,
                    parent_id=None,
                )
            else:
                pbi_dir = repo / ".ralph" / "inbox" / str(root_doc["id"])
            attachments_total = len(root_doc.get("attachments") or []) if not args.dry_run else 0
            first_pbi_for_report = (
                str(root_doc["id"]),
                str(root_doc["type"]),
                str(pbi_dir.relative_to(repo)).replace("\\", "/"),
            )
            if not args.dry_run:
                last_commit_sha = _commit_pbi(
                    repo,
                    pbi_dir,
                    f"feat(ralph-queue): add {root_doc['id']}",
                )

        if not args.dry_run and not args.no_push:
            print(f"pushing {args.branch} to origin...", file=sys.stderr)
            _push(repo, args.branch)
            pushed = True

        assert first_pbi_for_report is not None
        report_pbi_id, report_pbi_type, report_pbi_path = first_pbi_for_report
        result = AddResult(
            work_item_id=str(root_doc["id"]).removeprefix("WI-"),
            pbi_id=report_pbi_id,
            pbi_type=report_pbi_type,
            pbi_path=report_pbi_path,
            repo_path=str(repo),
            branch=args.branch,
            attachments_downloaded=attachments_total,
            children_expanded=children_expanded,
            commit_sha=last_commit_sha,
            pushed=pushed,
            dry_run=args.dry_run,
            source_host=str(root_doc["source_host"]),
        )
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0
    except _FatalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        print(
            f"error: git command failed ({exc.returncode}): {' '.join(exc.cmd)}\n{stderr}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
