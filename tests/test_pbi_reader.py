"""Unit tests for ``scripts.pbi_reader``.

Construct PBI directories directly in ``tmp_path``; no git involvement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.pbi_reader import (
    PBIRow,
    PBIRowError,
    enumerate_state,
    read_pbi,
)

VALID_FEATURE_FRONTMATTER = """---
id: WI-1234
type: feature
status: inbox
severity: normal
attempts: 0
created_at: 2026-05-24T09:15:00+00:00
updated_at: 2026-05-24T09:15:00+00:00
---

# Add /healthz endpoint

Body content here.
"""

VALID_BUG_FRONTMATTER = """---
id: BUG-1
type: bug
status: inbox
severity: critical
attempts: 0
created_at: 2026-05-24T09:15:00+00:00
updated_at: 2026-05-24T09:15:00+00:00
---

# Pod crashloops on ROSA

Body content here.
"""

VALID_FEEDBACK_FRONTMATTER = """---
id: PR-feedback-WI-1234-r2
type: pr-feedback
status: inbox
severity: high
attempts: 0
created_at: 2026-05-24T09:15:00+00:00
updated_at: 2026-05-24T09:15:00+00:00
---

# PR feedback round 2

Body.
"""


def _make_pbi(base: Path, name: str, entry_file: str, content: str) -> Path:
    pbi_dir = base / name
    pbi_dir.mkdir(parents=True)
    (pbi_dir / entry_file).write_text(content, encoding="utf-8")
    return pbi_dir


def test_read_pbi_feature(tmp_path: Path) -> None:
    pbi_dir = _make_pbi(tmp_path, "WI-1234", "PBI.md", VALID_FEATURE_FRONTMATTER)
    result = read_pbi(pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox")
    assert isinstance(result, PBIRow)
    assert result.pbi_id == "WI-1234"
    assert result.pbi_type == "feature"
    assert result.severity == "normal"
    assert result.title == "Add /healthz endpoint"
    assert result.state == "inbox"


def test_read_pbi_bug(tmp_path: Path) -> None:
    pbi_dir = _make_pbi(tmp_path, "BUG-1", "BUG.md", VALID_BUG_FRONTMATTER)
    result = read_pbi(pbi_dir, repo_path=tmp_path, repo_name="svc", state="current")
    assert isinstance(result, PBIRow)
    assert result.pbi_type == "bug"
    assert result.severity == "critical"
    assert result.title == "Pod crashloops on ROSA"


def test_read_pbi_feedback(tmp_path: Path) -> None:
    pbi_dir = _make_pbi(
        tmp_path,
        "PR-feedback-WI-1234-r2",
        "FEEDBACK.md",
        VALID_FEEDBACK_FRONTMATTER,
    )
    result = read_pbi(pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox")
    assert isinstance(result, PBIRow)
    assert result.pbi_type == "pr-feedback"


def test_read_pbi_missing_entry_file(tmp_path: Path) -> None:
    pbi_dir = tmp_path / "WI-9999"
    pbi_dir.mkdir()
    result = read_pbi(pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox")
    assert isinstance(result, PBIRowError)
    assert "no entry file" in result.message


def test_read_pbi_invalid_yaml(tmp_path: Path) -> None:
    bad = """---
id: WI-1
type: feature
status: inbox
severity: normal
attempts: 0
created_at: 2026-05-24T09:15:00+00:00
updated_at: 2026-05-24T09:15:00+00:00
  not-properly-indented: [unclosed
---

# Title
"""
    pbi_dir = _make_pbi(tmp_path, "WI-1", "PBI.md", bad)
    result = read_pbi(pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox")
    assert isinstance(result, PBIRowError)
    assert "YAML" in result.message or "yaml" in result.message


def test_read_pbi_missing_required_field(tmp_path: Path) -> None:
    missing = """---
id: WI-1
type: feature
status: inbox
severity: normal
# attempts intentionally omitted
created_at: 2026-05-24T09:15:00+00:00
updated_at: 2026-05-24T09:15:00+00:00
---

# Title
"""
    pbi_dir = _make_pbi(tmp_path, "WI-1", "PBI.md", missing)
    result = read_pbi(pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox")
    assert isinstance(result, PBIRowError)
    assert "attempts" in result.message


def test_read_pbi_bad_type_value(tmp_path: Path) -> None:
    bad_type = """---
id: WI-1
type: feechore
status: inbox
severity: normal
attempts: 0
created_at: 2026-05-24T09:15:00+00:00
updated_at: 2026-05-24T09:15:00+00:00
---

# Title
"""
    pbi_dir = _make_pbi(tmp_path, "WI-1", "PBI.md", bad_type)
    result = read_pbi(pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox")
    assert isinstance(result, PBIRowError)
    assert "type=" in result.message


def test_enumerate_state_missing_folder_is_empty(tmp_path: Path) -> None:
    assert enumerate_state(tmp_path, "done") == []


def test_enumerate_state_walks_pbis(tmp_path: Path) -> None:
    inbox = tmp_path / ".ralph" / "inbox"
    inbox.mkdir(parents=True)
    _make_pbi(inbox, "WI-1", "PBI.md", VALID_FEATURE_FRONTMATTER)
    _make_pbi(inbox, "BUG-1", "BUG.md", VALID_BUG_FRONTMATTER)
    rows = enumerate_state(tmp_path, "inbox", repo_name="svc")
    assert len(rows) == 2
    ids = sorted(row.pbi_id if isinstance(row, PBIRow) else "?" for row in rows)
    assert ids == ["BUG-1", "WI-1234"]
    assert all(isinstance(row, PBIRow) for row in rows)


def test_enumerate_state_unknown_state_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        enumerate_state(tmp_path, "limbo")


def test_attempts_true_is_rejected_as_bool(tmp_path: Path) -> None:
    """Regression for BugBot LOW on PR #26: bool is a subclass of int in
    Python, so ``isinstance(True, int)`` is True. Without an explicit
    bool exclusion, a frontmatter ``attempts: true`` would survive
    validation and propagate as Python ``True``, then serialize to JSON
    as ``\"attempts\": true`` (a boolean) instead of the integer
    callers expect."""
    body = """---
id: WI-7
type: feature
status: inbox
severity: normal
attempts: true
created_at: 2026-05-24T09:15:00+00:00
updated_at: 2026-05-24T09:15:00+00:00
---

# x
"""
    pbi_dir = _make_pbi(tmp_path, "WI-7", "PBI.md", body)
    result = read_pbi(pbi_dir, repo_path=tmp_path, repo_name="svc", state="inbox")
    assert isinstance(result, PBIRow), f"expected PBIRow, got {result}"
    assert result.attempts == 0
    # AND specifically not the Python True that bool-int conflation would produce.
    assert result.attempts is not True
    assert isinstance(result.attempts, int)
    assert not isinstance(result.attempts, bool)
