from datetime import UTC, datetime, timedelta
from pathlib import Path

from ralph_executor.autobug.fuses import (
    RateLimitConfig,
    rate_check,
    recursion_check,
    rollup,
)


def test_recursion_check_allows_when_unset() -> None:
    assert recursion_check({}) is True


def test_recursion_check_allows_when_zero() -> None:
    assert recursion_check({"RALPH_AUTOBUG_DEPTH": "0"}) is True


def test_recursion_check_denies_when_one() -> None:
    assert recursion_check({"RALPH_AUTOBUG_DEPTH": "1"}) is False


def test_recursion_check_denies_when_higher() -> None:
    assert recursion_check({"RALPH_AUTOBUG_DEPTH": "2"}) is False


def test_recursion_check_tolerates_non_integer() -> None:
    assert recursion_check({"RALPH_AUTOBUG_DEPTH": "garbage"}) is True


def test_rate_check_passes_under_cap(tmp_path: Path) -> None:
    cfg = RateLimitConfig(max_writes=5, window=timedelta(minutes=10))
    now = datetime(2026, 5, 31, 14, 23, tzinfo=UTC)
    state = tmp_path / "state"
    state.mkdir()
    assert rate_check(state, cfg, now) is True


def test_rate_check_denies_over_cap(tmp_path: Path) -> None:
    cfg = RateLimitConfig(max_writes=2, window=timedelta(minutes=10))
    now = datetime(2026, 5, 31, 14, 23, tzinfo=UTC)
    state = tmp_path / "state"
    state.mkdir()
    log_path = state / "autobug-emissions.log"
    log_path.write_text(
        "2026-05-31T14:22:00+00:00\n2026-05-31T14:22:30+00:00\n",
        encoding="utf-8",
    )
    assert rate_check(state, cfg, now) is False


def test_rate_check_ignores_entries_outside_window(tmp_path: Path) -> None:
    cfg = RateLimitConfig(max_writes=1, window=timedelta(minutes=10))
    now = datetime(2026, 5, 31, 14, 23, tzinfo=UTC)
    state = tmp_path / "state"
    state.mkdir()
    log_path = state / "autobug-emissions.log"
    log_path.write_text("2026-05-31T13:00:00+00:00\n", encoding="utf-8")
    assert rate_check(state, cfg, now) is True


def test_rollup_appends_suppressed_log(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    now = datetime(2026, 5, 31, 14, 23, tzinfo=UTC)
    rollup(state, "deadbeefcafe", "rate-limited new", now=now)
    contents = (state / "autobug-suppressed.log").read_text(encoding="utf-8")
    assert "deadbeefcafe" in contents
    assert "rate-limited new" in contents
