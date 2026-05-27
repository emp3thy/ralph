"""Tests for ``ralph_executor.sweep.pr_state.fetch``.

Instead of mocking ``requests``, the tests build a tiny shim script that
pretends to be the PR skill's ``show.py`` and ``read_threads.py``. The shim
reads its argv to figure out which operation is being requested, prints a
canned JSON payload, and exits 0. ``pr_state.fetch`` is invoked with
``skill_scripts_path`` pointing at the shim's parent directory.
"""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph_executor.sweep.pr_state import AdoSkillError, fetch
from ralph_executor.sweep.types import PrSnapshot

SHOW_PAYLOAD_MERGED: dict[str, object] = {
    "pr_id": 100,
    "title": "WI-1234 — Add /healthz endpoint",
    "status": "completed",
    "ci_status": "succeeded",
    "source_branch": "ralph/WI-1234",
    "target_branch": "main",
    "reviewers": ["reviewer@example.com"],
    "url": "https://dev.azure.com/example-org/_git/svc/pullrequest/100",
    "last_activity_at": "2026-05-23T10:00:00+00:00",
}

THREADS_PAYLOAD_EMPTY: list[dict[str, object]] = []

THREADS_PAYLOAD_WITH_HUMAN_COMMENT: list[dict[str, object]] = [
    {
        "thread_id": 11,
        "status": "active",
        "file_path": "src/app.py",
        "line": 42,
        "comments": [
            {
                "comment_id": 101,
                "author_email": "reviewer@example.com",
                "posted_at": "2026-05-24T09:00:00+00:00",
                "text": "Please rename this variable.",
                "file_path": "src/app.py",
                "line": 42,
            }
        ],
    }
]


@pytest.fixture
def shim_skill(tmp_path: Path) -> Path:
    """Build a fake ``skills/<pr-skill>/scripts/`` directory with show + read_threads."""
    skill_dir = tmp_path / "pr-skill-shim" / "scripts"
    skill_dir.mkdir(parents=True)

    show_path = skill_dir / "show.py"
    show_path.write_text(
        textwrap.dedent(
            """\
            import json, os, sys
            payload = json.loads(os.environ["SHIM_SHOW_JSON"])
            print(json.dumps(payload))
            """
        )
    )
    read_threads_path = skill_dir / "read_threads.py"
    read_threads_path.write_text(
        textwrap.dedent(
            """\
            import json, os, sys
            payload = json.loads(os.environ["SHIM_THREADS_JSON"])
            print(json.dumps(payload))
            """
        )
    )
    return skill_dir


def test_fetch_parses_merged_pr_with_no_threads(
    shim_skill: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHIM_SHOW_JSON", json.dumps(SHOW_PAYLOAD_MERGED))
    monkeypatch.setenv("SHIM_THREADS_JSON", json.dumps(THREADS_PAYLOAD_EMPTY))

    snapshot = fetch(pr_id=100, skill_scripts_path=shim_skill, repo_name="ralph")

    assert isinstance(snapshot, PrSnapshot)
    assert snapshot.pr_id == 100
    assert snapshot.pr_status == "completed"
    assert snapshot.ci_status == "succeeded"
    assert snapshot.reviewers == ("reviewer@example.com",)
    assert snapshot.threads == ()
    assert snapshot.last_activity_at == datetime(2026, 5, 23, 10, 0, 0, tzinfo=UTC)


def test_fetch_parses_threads_into_typed_snapshots(
    shim_skill: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "SHIM_SHOW_JSON",
        json.dumps({**SHOW_PAYLOAD_MERGED, "status": "active"}),
    )
    monkeypatch.setenv("SHIM_THREADS_JSON", json.dumps(THREADS_PAYLOAD_WITH_HUMAN_COMMENT))

    snapshot = fetch(pr_id=100, skill_scripts_path=shim_skill, repo_name="ralph")

    assert len(snapshot.threads) == 1
    thread = snapshot.threads[0]
    assert thread.thread_id == 11
    assert thread.status == "active"
    assert len(thread.comments) == 1
    assert thread.comments[0].author_email == "reviewer@example.com"
    assert thread.comments[0].text.startswith("Please rename")
    assert thread.comments[0].posted_at == datetime(2026, 5, 24, 9, 0, 0, tzinfo=UTC)


def test_fetch_last_activity_at_falls_back_to_comments_when_show_missing(
    shim_skill: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    show = {**SHOW_PAYLOAD_MERGED, "status": "active"}
    show.pop("last_activity_at", None)
    monkeypatch.setenv("SHIM_SHOW_JSON", json.dumps(show))
    monkeypatch.setenv("SHIM_THREADS_JSON", json.dumps(THREADS_PAYLOAD_WITH_HUMAN_COMMENT))

    snapshot = fetch(pr_id=100, skill_scripts_path=shim_skill, repo_name="ralph")
    assert snapshot.last_activity_at == datetime(2026, 5, 24, 9, 0, 0, tzinfo=UTC)


def test_fetch_raises_on_nonzero_exit(tmp_path: Path) -> None:
    skill_dir = tmp_path / "pr-skill-broken" / "scripts"
    skill_dir.mkdir(parents=True)
    (skill_dir / "show.py").write_text("import sys; sys.exit(7)")
    (skill_dir / "read_threads.py").write_text("print('[]')")

    with pytest.raises(AdoSkillError) as excinfo:
        fetch(pr_id=100, skill_scripts_path=skill_dir, repo_name="ralph")
    assert "exit code 7" in str(excinfo.value)


def test_fetch_raises_on_invalid_json(tmp_path: Path) -> None:
    skill_dir = tmp_path / "pr-skill-bad-json" / "scripts"
    skill_dir.mkdir(parents=True)
    (skill_dir / "show.py").write_text("print('not json at all')")
    (skill_dir / "read_threads.py").write_text("print('[]')")

    with pytest.raises(AdoSkillError, match="JSON"):
        fetch(pr_id=100, skill_scripts_path=skill_dir, repo_name="ralph")


def test_fetch_passes_repo_and_pr_id_flags(tmp_path: Path) -> None:
    """Regression: skill scripts require ``--repo`` + ``--pr-id`` flags.

    Positional invocation fails with argparse exit 2 (
    "the following arguments are required: --repo, --pr-id").
    Each script must receive both flags with the values passed to ``fetch``.
    """
    skill_dir = tmp_path / "pr-skill-capture" / "scripts"
    skill_dir.mkdir(parents=True)
    show_argv_log = tmp_path / "show_argv.json"
    threads_argv_log = tmp_path / "threads_argv.json"
    (skill_dir / "show.py").write_text(
        textwrap.dedent(
            f"""\
            import json, sys
            from pathlib import Path
            Path({str(show_argv_log)!r}).write_text(json.dumps(sys.argv[1:]))
            print(json.dumps({{"pr_id": 7, "status": "active"}}))
            """
        )
    )
    (skill_dir / "read_threads.py").write_text(
        textwrap.dedent(
            f"""\
            import json, sys
            from pathlib import Path
            Path({str(threads_argv_log)!r}).write_text(json.dumps(sys.argv[1:]))
            print("[]")
            """
        )
    )

    fetch(pr_id=7, skill_scripts_path=skill_dir, repo_name="ralph")

    show_argv = json.loads(show_argv_log.read_text())
    threads_argv = json.loads(threads_argv_log.read_text())
    assert show_argv == ["--repo", "ralph", "--pr-id", "7"]
    assert threads_argv == ["--repo", "ralph", "--pr-id", "7"]
