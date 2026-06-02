"""Dedup lookup across inbox/current/pending-pr + done within 30 days (T7)."""

from datetime import UTC, datetime
from pathlib import Path

from ralph_executor.autobug.dedup import DedupResult, lookup


def _write_pbi(
    queue_root: Path,
    state: str,
    pbi_id: str,
    signature: str,
    closed_at: str | None = None,
) -> None:
    pbi_dir = queue_root / ".ralph" / state / pbi_id
    pbi_dir.mkdir(parents=True, exist_ok=True)
    extra = f"closed_at: {closed_at}\n" if closed_at else ""
    (pbi_dir / "BUG.md").write_text(
        f"---\nid: {pbi_id}\ntype: bug\nseverity: critical\nstatus: {state}\n"
        f"attempts: 0\ncreated_at: 2026-05-31T00:00:00+00:00\n"
        f"updated_at: 2026-05-31T00:00:00+00:00\n"
        f"target_repo: https://github.com/test/repo\n"
        f"signature: {signature}\n{extra}---\n# bug body\n",
        encoding="utf-8",
    )


def test_lookup_returns_new_when_no_match(tmp_path: Path) -> None:
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    r = lookup("a" * 64, tmp_path, now)
    assert r.kind == "new"
    assert r.existing_pbi_id is None


def test_lookup_returns_bump_when_inbox_match(tmp_path: Path) -> None:
    sig = "b" * 64
    _write_pbi(tmp_path, "inbox", "autobug-bbbbbb-001", sig)
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    r = lookup(sig, tmp_path, now)
    assert r.kind == "bump_existing"
    assert r.existing_pbi_id == "autobug-bbbbbb-001"


def test_lookup_returns_bump_when_current_match(tmp_path: Path) -> None:
    sig = "c" * 64
    _write_pbi(tmp_path, "current", "autobug-cccccc-001", sig)
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    r = lookup(sig, tmp_path, now)
    assert r.kind == "bump_existing"


def test_lookup_returns_reopen_when_done_within_30d(tmp_path: Path) -> None:
    sig = "d" * 64
    _write_pbi(
        tmp_path,
        "done",
        "autobug-dddddd-001",
        sig,
        closed_at="2026-05-25T00:00:00+00:00",
    )
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    r = lookup(sig, tmp_path, now)
    assert r.kind == "reopen_regression"
    assert r.existing_pbi_id == "autobug-dddddd-001"


def test_lookup_returns_new_when_done_older_than_30d(tmp_path: Path) -> None:
    sig = "e" * 64
    _write_pbi(
        tmp_path,
        "done",
        "autobug-eeeeee-001",
        sig,
        closed_at="2026-03-01T00:00:00+00:00",
    )
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    r = lookup(sig, tmp_path, now)
    assert r.kind == "new"


def test_dedup_result_reexport() -> None:
    r = DedupResult(kind="new")
    assert r.kind == "new"


def test_lookup_window_days_override_widens_done_window(tmp_path: Path) -> None:
    """Done PBI 60 days old falls outside the default 30d window but inside a 90d override."""
    sig = "f" * 64
    _write_pbi(
        tmp_path,
        "done",
        "autobug-ffffff-001",
        sig,
        closed_at="2026-04-01T00:00:00+00:00",
    )
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    assert lookup(sig, tmp_path, now).kind == "new"
    assert lookup(sig, tmp_path, now, window_days=90).kind == "reopen_regression"


def test_lookup_window_days_override_narrows_done_window(tmp_path: Path) -> None:
    """Done PBI 6 days old falls inside the default 30d window but outside a 3d override."""
    sig = "9" * 64
    _write_pbi(
        tmp_path,
        "done",
        "autobug-999999-001",
        sig,
        closed_at="2026-05-25T00:00:00+00:00",
    )
    now = datetime(2026, 5, 31, 14, tzinfo=UTC)
    assert lookup(sig, tmp_path, now).kind == "reopen_regression"
    assert lookup(sig, tmp_path, now, window_days=3).kind == "new"
