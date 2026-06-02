"""Three independent fuses: recursion guard, rate limit, rollup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


def recursion_check(env: Mapping[str, str]) -> bool:
    """True = allowed to emit; False = recursion guard tripped."""
    raw = env.get("RALPH_AUTOBUG_DEPTH", "0")
    try:
        depth = int(raw)
    except ValueError:
        depth = 0
    return depth < 1


@dataclass(frozen=True)
class RateLimitConfig:
    max_writes: int = 5
    window: timedelta = timedelta(minutes=10)


def _read_timestamps_after(log_path: Path, cutoff: datetime) -> list[datetime]:
    if not log_path.is_file():
        return []
    out: list[datetime] = []
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            ts = datetime.fromisoformat(line)
        except ValueError:
            continue
        if ts >= cutoff:
            out.append(ts)
    return out


def rate_check(state_dir: Path, cfg: RateLimitConfig, now: datetime) -> bool:
    """True = under cap; False = over cap, caller should rollup."""
    log_path = state_dir / "autobug-emissions.log"
    recent = _read_timestamps_after(log_path, now - cfg.window)
    return len(recent) < cfg.max_writes


def append_emission(state_dir: Path, now: datetime) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "autobug-emissions.log"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{now.isoformat()}\n")


def rollup(state_dir: Path, signature: str, reason: str, *, now: datetime) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "autobug-suppressed.log"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{now.isoformat()}\t{signature}\t{reason}\n")
