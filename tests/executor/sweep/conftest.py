"""Shared fixtures for sweep runner tests."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def queue_root(tmp_path: Path) -> Path:
    """Construct an empty ``.ralph/`` layout under tmp_path."""
    root = tmp_path / "repo" / ".ralph"
    for sub in ("inbox", "current", "pending-pr", "done", "blocked"):
        (root / sub).mkdir(parents=True)
    return root


@pytest.fixture
def make_pending_pbi(queue_root: Path):  # type: ignore[no-untyped-def]
    """Factory: write a pending-pr PBI with the given metadata."""

    def _make(
        pbi_id: str,
        *,
        pr_id: int,
        attempts: int = 1,
        severity: str = "normal",
        pbi_type: str = "feature",
        parent_id: str | None = None,
    ) -> Path:
        pbi_dir = queue_root / "pending-pr" / pbi_id
        pbi_dir.mkdir()
        frontmatter = [
            "---",
            f"id: {pbi_id}",
            f"type: {pbi_type}",
            "status: pending-pr",
            f"severity: {severity}",
            f"attempts: {attempts}",
            "created_at: 2026-05-20T08:00:00+00:00",
            "updated_at: 2026-05-23T08:00:00+00:00",
        ]
        if parent_id:
            frontmatter.append(f"parent_id: {parent_id}")
        frontmatter += ["---", "", f"# {pbi_id}", "", "(test PBI body)\n"]
        entry = "BUG.md" if pbi_type == "bug" else "PBI.md"
        (pbi_dir / entry).write_text("\n".join(frontmatter), encoding="utf-8")
        (pbi_dir / "HISTORY.md").write_text("", encoding="utf-8")
        (pbi_dir / "PR-LINK.md").write_text(
            f"# PR link\n\n- **PR ID:** !{pr_id}\n",
            encoding="utf-8",
        )
        return pbi_dir

    return _make


@pytest.fixture
def fake_ado_pr_skill(tmp_path: Path) -> Path:
    """Build a fake PR-skill ``scripts/`` directory driven by env vars.

    Tests set ``SHIM_SHOW_<pr_id>_JSON`` and ``SHIM_THREADS_<pr_id>_JSON`` to
    register canned responses per PR.
    """
    skill_dir = tmp_path / "pr-skill-shim" / "scripts"
    skill_dir.mkdir(parents=True)

    (skill_dir / "show.py").write_text(
        textwrap.dedent(
            """\
            import json, os, sys
            pr_id = sys.argv[sys.argv.index("--pr-id") + 1]
            payload = json.loads(os.environ[f"SHIM_SHOW_{pr_id}_JSON"])
            print(json.dumps(payload))
            """
        )
    )
    (skill_dir / "read_threads.py").write_text(
        textwrap.dedent(
            """\
            import json, os, sys
            pr_id = sys.argv[sys.argv.index("--pr-id") + 1]
            payload = json.loads(os.environ[f"SHIM_THREADS_{pr_id}_JSON"])
            print(json.dumps(payload))
            """
        )
    )
    (skill_dir / "merge_pr.py").write_text(
        textwrap.dedent(
            """\
            import json, os, sys
            pr_id = sys.argv[sys.argv.index("--pr-id") + 1]
            rc = int(os.environ.get(f"SHIM_MERGE_PR_{pr_id}_EXIT", "0"))
            payload = {"pr_id": int(pr_id), "merged": rc == 0, "sha": "deadbeef"}
            print(json.dumps(payload))
            sys.exit(rc)
            """
        )
    )
    return skill_dir


def register_pr(
    monkeypatch: pytest.MonkeyPatch,
    pr_id: int,
    *,
    status: str,
    ci_status: str = "succeeded",
    last_activity_at: str = "2026-05-23T08:00:00+00:00",
    threads: list[dict[str, Any]] | None = None,
    source_branch: str = "ralph/WI-1234",
    mergeable_state: str = "unknown",
) -> None:
    """Register canned ``show`` and ``read_threads`` JSON for a PR id."""
    show: dict[str, Any] = {
        "pr_id": pr_id,
        "title": f"PR #{pr_id}",
        "status": status,
        "ci_status": ci_status,
        "source_branch": source_branch,
        "target_branch": "main",
        "reviewers": ["reviewer@example.com"],
        "url": f"https://example/_git/svc/pullrequest/{pr_id}",
        "last_activity_at": last_activity_at,
        "mergeable_state": mergeable_state,
    }
    monkeypatch.setenv(f"SHIM_SHOW_{pr_id}_JSON", json.dumps(show))
    monkeypatch.setenv(f"SHIM_THREADS_{pr_id}_JSON", json.dumps(threads or []))


def register_merge_pr_exit(monkeypatch: pytest.MonkeyPatch, pr_id: int, exit_code: int) -> None:
    """Set the shim ``merge_pr.py`` exit code for ``pr_id``."""
    monkeypatch.setenv(f"SHIM_MERGE_PR_{pr_id}_EXIT", str(exit_code))
