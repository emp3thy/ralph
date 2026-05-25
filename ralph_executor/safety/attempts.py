"""Per-PBI attempts counter (wraps the ``attempts:`` frontmatter field).

The counter is consulted by the executor's loop driver at the start of
each iteration on a given PBI: increment first, then either spawn
claude-p (if the counter did not raise) or move the PBI to ``blocked/``
via :func:`ralph_executor.safety.stuck.move_to_blocked` (if it did).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_ATTEMPTS = 3
_MAX_ATTEMPTS_ENV = "RALPH_MAX_ATTEMPTS"
_ATTEMPT_LINE = re.compile(r"^(attempts:\s*)(-?\d+)\s*$", flags=re.MULTILINE)
_FRONTMATTER_FENCE = "---"
_CANDIDATE_FILES = ("PBI.md", "BUG.md", "FEEDBACK.md")


class AttemptsExceeded(RuntimeError):
    """Raised when incrementing the counter would exceed the configured max."""

    def __init__(self, *, pbi_id: str, attempts: int, limit: int) -> None:
        super().__init__(f"attempts ({attempts}) reached limit ({limit}) for {pbi_id}")
        self.pbi_id = pbi_id
        self.attempts = attempts
        self.limit = limit


def max_attempts() -> int:
    raw = os.environ.get(_MAX_ATTEMPTS_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_ATTEMPTS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS
    if value <= 0:
        return DEFAULT_MAX_ATTEMPTS
    return value


def _frontmatter_file(pbi_dir: Path) -> Path:
    for candidate in _CANDIDATE_FILES:
        path = pbi_dir / candidate
        if path.is_file():
            return path
    raise FileNotFoundError(f"no PBI frontmatter file ({_CANDIDATE_FILES}) under {pbi_dir}")


def read_attempts(pbi_dir: Path) -> int:
    path = _frontmatter_file(pbi_dir)
    text = path.read_text(encoding="utf-8")
    front, _body = _split_frontmatter(text)
    match = _ATTEMPT_LINE.search(front)
    if not match:
        return 0
    return int(match.group(2))


def write_attempts(pbi_dir: Path, *, value: int) -> None:
    path = _frontmatter_file(pbi_dir)
    text = path.read_text(encoding="utf-8")
    front, body = _split_frontmatter(text)
    if _ATTEMPT_LINE.search(front):
        new_front = _ATTEMPT_LINE.sub(rf"\g<1>{value}", front)
    else:
        # Insert ``attempts:`` line just before the closing fence so the
        # frontmatter remains well-formed even on hand-authored PBIs that
        # forgot the field.
        new_front = front.rstrip() + f"\nattempts: {value}\n"
    path.write_text(_join_frontmatter(new_front, body), encoding="utf-8")


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith(_FRONTMATTER_FENCE):
        raise ValueError("frontmatter must open with '---' on line 1")
    try:
        end_idx = text.index(f"\n{_FRONTMATTER_FENCE}", len(_FRONTMATTER_FENCE))
    except ValueError as exc:
        raise ValueError("frontmatter has no closing '---' fence") from exc
    front = text[len(_FRONTMATTER_FENCE) : end_idx]
    body = text[end_idx + len(f"\n{_FRONTMATTER_FENCE}") :]
    return front.strip("\n"), body


def _join_frontmatter(front: str, body: str) -> str:
    return f"{_FRONTMATTER_FENCE}\n{front.strip()}\n{_FRONTMATTER_FENCE}{body}"


@dataclass
class AttemptCounter:
    """Mutable counter view over a PBI directory's ``attempts:`` field."""

    pbi_dir: Path

    def current(self) -> int:
        return read_attempts(self.pbi_dir)

    def increment(self) -> int:
        new_value = read_attempts(self.pbi_dir) + 1
        limit = max_attempts()
        if new_value > limit:
            raise AttemptsExceeded(
                pbi_id=self.pbi_dir.name,
                attempts=new_value,
                limit=limit,
            )
        write_attempts(self.pbi_dir, value=new_value)
        return new_value
