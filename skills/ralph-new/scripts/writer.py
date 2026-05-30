"""Frontmatter renderer + directory-write orchestrator for ralph-new.

Frontmatter shape matches the canonical schema parsed by
``ralph_executor.queue.filesystem.parse_pbi_directory``:

    id, type, status=inbox, severity, attempts=0, created_at,
    updated_at, depends_on (list), target_repo, parent_id (optional)

``write_pbi_dir`` is type-dispatched: writes BUG.md+REPRODUCE.md+HISTORY.md
for ``bug`` PBIs, or PBI.md+PLAN.md+HISTORY.md for ``feature`` PBIs.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

_TEMPLATES_PATH = Path(__file__).with_name("templates.py")


def _load_templates() -> ModuleType:
    """Path-load the sibling ``templates`` module.

    The skill is invoked as a standalone script (``python
    skills/ralph-new/scripts/new.py``), so ``from . import templates`` is
    unavailable — there is no parent package context. Reuse a cached
    instance across calls.
    """
    cached = sys.modules.get("ralph_new_templates")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("ralph_new_templates", _TEMPLATES_PATH)
    assert spec is not None and spec.loader is not None, (
        f"cannot locate templates at {_TEMPLATES_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["ralph_new_templates"] = module
    spec.loader.exec_module(module)
    return module


def now_iso(now: datetime | None = None) -> str:
    """Return UTC time as ISO-8601 with offset (``+00:00``)."""
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.isoformat()


def render_frontmatter(
    *,
    pbi_id: str,
    pbi_type: Literal["bug", "feature"],
    severity: str,
    target_repo: str,
    depends_on: Sequence[str],
    parent_id: str | None,
    now: datetime | None = None,
) -> str:
    """Render the full YAML frontmatter block (including opening + closing ``---``).

    Trailing blank line ensures the body that follows starts on its own line.
    """
    ts = now_iso(now)
    lines = [
        "---",
        f"id: {pbi_id}",
        f"type: {pbi_type}",
        "status: inbox",
        f"severity: {severity}",
        "attempts: 0",
        f"created_at: {ts}",
        f"updated_at: {ts}",
    ]
    if depends_on:
        lines.append("depends_on:")
        for dep in depends_on:
            lines.append(f"  - {dep}")
    else:
        lines.append("depends_on: []")
    lines.append(f"target_repo: {target_repo}")
    if parent_id:
        lines.append(f"parent_id: {parent_id}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_pbi_dir(
    *,
    pbi_dir: Path,
    pbi_type: Literal["bug", "feature"],
    bug_inputs: dict[str, Any] | None,
    feature_inputs: dict[str, Any] | None,
) -> None:
    """Create the PBI directory + per-type entry files + HISTORY.md.

    Raises ``FileExistsError`` if ``pbi_dir`` already exists, to prevent
    accidentally overwriting a real PBI.
    """
    if pbi_dir.exists():
        raise FileExistsError(f"PBI directory already exists: {pbi_dir}")
    templates = _load_templates()
    pbi_dir.mkdir(parents=True, exist_ok=False)

    if pbi_type == "bug":
        assert bug_inputs is not None, "bug_inputs required for type=bug"
        bug_text = templates.render_bug_md(
            frontmatter=bug_inputs["frontmatter"],
            title=bug_inputs["title"],
            symptom=bug_inputs["symptom"],
            root_cause=bug_inputs["root_cause"],
            impact=bug_inputs["impact"],
            acceptance_criteria=bug_inputs["acceptance_criteria"],
        )
        repro = bug_inputs["reproduce"]
        repro_text = templates.render_reproduce_md(
            title=bug_inputs["title"],
            environment=repro["environment"],
            steps=repro["steps"],
            expected=repro["expected"],
            actual=repro["actual"],
        )
        (pbi_dir / "BUG.md").write_text(bug_text, encoding="utf-8")
        (pbi_dir / "REPRODUCE.md").write_text(repro_text, encoding="utf-8")
    else:
        assert feature_inputs is not None, "feature_inputs required for type=feature"
        pbi_text = templates.render_pbi_md(
            frontmatter=feature_inputs["frontmatter"],
            title=feature_inputs["title"],
            description=feature_inputs["description"],
            acceptance_criteria=feature_inputs["acceptance_criteria"],
            spec_path=feature_inputs["spec_path"],
        )
        plan_text = templates.render_plan_md(
            title=feature_inputs["title"],
            plan_path=feature_inputs["plan_path"],
            plan_content=feature_inputs.get("plan_content", ""),
        )
        (pbi_dir / "PBI.md").write_text(pbi_text, encoding="utf-8")
        (pbi_dir / "PLAN.md").write_text(plan_text, encoding="utf-8")

    (pbi_dir / "HISTORY.md").write_text(templates.HISTORY_TEMPLATE, encoding="utf-8")
