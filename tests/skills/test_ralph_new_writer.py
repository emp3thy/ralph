"""Tests for the frontmatter renderer + write_pbi_dir orchestrator."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRITER_PATH = REPO_ROOT / "skills" / "ralph-new" / "scripts" / "writer.py"
TEMPLATES_PATH = REPO_ROOT / "skills" / "ralph-new" / "scripts" / "templates.py"


@pytest.fixture(scope="module")
def writer() -> ModuleType:
    tspec = importlib.util.spec_from_file_location("ralph_new_templates", TEMPLATES_PATH)
    assert tspec is not None and tspec.loader is not None
    tmod = importlib.util.module_from_spec(tspec)
    sys.modules["ralph_new_templates"] = tmod
    tspec.loader.exec_module(tmod)

    wspec = importlib.util.spec_from_file_location("ralph_new_writer", WRITER_PATH)
    assert wspec is not None and wspec.loader is not None
    wmod = importlib.util.module_from_spec(wspec)
    sys.modules["ralph_new_writer"] = wmod
    wspec.loader.exec_module(wmod)
    return wmod


FIXED_TS = datetime(2026, 5, 30, 14, 0, 0, tzinfo=UTC)
ISO = "2026-05-30T14:00:00+00:00"


# ---- frontmatter -----------------------------------------------------


def test_render_frontmatter_feature_full(writer: ModuleType) -> None:
    fm = writer.render_frontmatter(
        pbi_id="FEAT-X",
        pbi_type="feature",
        severity="normal",
        target_repo="https://github.com/owner/repo",
        depends_on=("PREREQ-A", "PREREQ-B"),
        parent_id="EPIC-1",
        now=FIXED_TS,
    )
    assert fm.startswith("---\n")
    assert fm.endswith("---\n\n")
    assert "id: FEAT-X" in fm
    assert "type: feature" in fm
    assert "status: inbox" in fm
    assert "severity: normal" in fm
    assert "attempts: 0" in fm
    assert f"created_at: {ISO}" in fm
    assert f"updated_at: {ISO}" in fm
    assert "depends_on:\n  - PREREQ-A\n  - PREREQ-B" in fm
    assert "target_repo: https://github.com/owner/repo" in fm
    assert "parent_id: EPIC-1" in fm


def test_render_frontmatter_bug_no_parent_id(writer: ModuleType) -> None:
    fm = writer.render_frontmatter(
        pbi_id="BUG-Y",
        pbi_type="bug",
        severity="high",
        target_repo="https://github.com/owner/repo",
        depends_on=(),
        parent_id=None,
        now=FIXED_TS,
    )
    assert "parent_id:" not in fm
    assert "depends_on: []" in fm
    assert "type: bug" in fm


def test_render_frontmatter_depends_on_empty_serialises_inline(writer: ModuleType) -> None:
    fm = writer.render_frontmatter(
        pbi_id="X1",
        pbi_type="feature",
        severity="low",
        target_repo="https://github.com/o/r",
        depends_on=(),
        parent_id=None,
        now=FIXED_TS,
    )
    assert "depends_on: []" in fm


def test_history_template_constant_present_with_marker() -> None:
    tspec = importlib.util.spec_from_file_location("rt", TEMPLATES_PATH)
    assert tspec is not None and tspec.loader is not None
    tmod = importlib.util.module_from_spec(tspec)
    tspec.loader.exec_module(tmod)
    assert "Executor appends attempt records here" in tmod.HISTORY_TEMPLATE


# ---- write_pbi_dir ---------------------------------------------------


def test_write_pbi_dir_bug_creates_three_files(writer: ModuleType, tmp_path: Path) -> None:
    pbi_dir = tmp_path / "BUG-X"
    writer.write_pbi_dir(
        pbi_dir=pbi_dir,
        pbi_type="bug",
        bug_inputs={
            "frontmatter": "---\nid: BUG-X\n---\n\n",
            "title": "Login broken",
            "symptom": "form hangs",
            "root_cause": "tls handshake race",
            "impact": "12% of users",
            "acceptance_criteria": "- AC1",
            "reproduce": {
                "environment": "Safari 17",
                "steps": "1. ...",
                "expected": "login ok",
                "actual": "timeout",
            },
        },
        feature_inputs=None,
    )
    assert sorted(p.name for p in pbi_dir.iterdir()) == [
        "BUG.md",
        "HISTORY.md",
        "REPRODUCE.md",
    ]
    assert "Login broken" in (pbi_dir / "BUG.md").read_text(encoding="utf-8")
    assert "Environment" in (pbi_dir / "REPRODUCE.md").read_text(encoding="utf-8")
    assert "Executor appends" in (pbi_dir / "HISTORY.md").read_text(encoding="utf-8")


def test_write_pbi_dir_feature_creates_three_files(writer: ModuleType, tmp_path: Path) -> None:
    pbi_dir = tmp_path / "FEAT-X"
    writer.write_pbi_dir(
        pbi_dir=pbi_dir,
        pbi_type="feature",
        bug_inputs=None,
        feature_inputs={
            "frontmatter": "---\nid: FEAT-X\n---\n\n",
            "title": "New thing",
            "description": "Build a new thing.",
            "acceptance_criteria": "- AC1\n- AC2",
            "spec_path": "docs/superpowers/specs/foo.md",
            "plan_path": "docs/superpowers/plans/foo.md",
            "plan_content": "# inline plan\n\n- [ ] do x\n",
        },
    )
    assert sorted(p.name for p in pbi_dir.iterdir()) == ["HISTORY.md", "PBI.md", "PLAN.md"]
    pbi_text = (pbi_dir / "PBI.md").read_text(encoding="utf-8")
    assert "New thing" in pbi_text
    assert "Acceptance criteria" in pbi_text
    assert "Spec: `docs/superpowers/specs/foo.md`" in pbi_text
    plan_text = (pbi_dir / "PLAN.md").read_text(encoding="utf-8")
    assert "do x" in plan_text
    assert "docs/superpowers/plans/foo.md" in plan_text


def test_write_pbi_dir_feature_blank_plan_writes_stub(writer: ModuleType, tmp_path: Path) -> None:
    pbi_dir = tmp_path / "FEAT-Y"
    writer.write_pbi_dir(
        pbi_dir=pbi_dir,
        pbi_type="feature",
        bug_inputs=None,
        feature_inputs={
            "frontmatter": "---\nid: FEAT-Y\n---\n\n",
            "title": "Another thing",
            "description": "",
            "acceptance_criteria": "",
            "spec_path": "",
            "plan_path": "",
            "plan_content": "",
        },
    )
    plan_text = (pbi_dir / "PLAN.md").read_text(encoding="utf-8")
    assert "TODO" in plan_text


def test_write_pbi_dir_refuses_existing_dir(writer: ModuleType, tmp_path: Path) -> None:
    pbi_dir = tmp_path / "BUG-Z"
    pbi_dir.mkdir()
    with pytest.raises(FileExistsError):
        writer.write_pbi_dir(
            pbi_dir=pbi_dir,
            pbi_type="bug",
            bug_inputs={
                "frontmatter": "---\n---\n\n",
                "title": "x",
                "symptom": "",
                "root_cause": "",
                "impact": "",
                "acceptance_criteria": "",
                "reproduce": {
                    "environment": "",
                    "steps": "",
                    "expected": "",
                    "actual": "",
                },
            },
            feature_inputs=None,
        )
