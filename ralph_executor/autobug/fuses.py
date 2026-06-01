"""Three independent fuses: recursion guard, rate limit, rollup."""

from __future__ import annotations

from collections.abc import Mapping


def recursion_check(env: Mapping[str, str]) -> bool:
    """True = allowed to emit; False = recursion guard tripped."""
    raw = env.get("RALPH_AUTOBUG_DEPTH", "0")
    try:
        depth = int(raw)
    except ValueError:
        depth = 0
    return depth < 1
