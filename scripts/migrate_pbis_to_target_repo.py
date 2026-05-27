"""One-shot migration: insert target_repo into every PBI/BUG/FEEDBACK
that lacks the field.

Hardcoded for the 2026-05-27 ralph self-host migration: every existing
work item is for the ralph repo itself. Throwaway script; safe to keep
in the tree for audit but never expected to run a second time except
defensively (idempotent skip-if-present).

Run from repo root:
    uv run python scripts/migrate_pbis_to_target_repo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

TARGET_REPO = "https://github.com/emp3thy/ralph"
REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_DIRS = ("inbox", "current", "pending-pr", "done", "blocked")
ENTRY_FILENAMES = ("PBI.md", "BUG.md", "FEEDBACK.md")


def _insert_field(path: Path) -> str:
    """Insert ``target_repo: <url>`` line into frontmatter if absent.

    Returns one of:
      - ``updated`` — field inserted, file written
      - ``skipped`` — field already present, no change
      - ``error: <reason>`` — could not parse frontmatter
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return "error: no frontmatter fence at start"
    lines = text.splitlines(keepends=True)
    close_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].rstrip("\r\n") == "---":
            close_idx = idx
            break
    if close_idx is None:
        return "error: no closing frontmatter fence"
    for line in lines[1:close_idx]:
        if line.lstrip().startswith("target_repo:"):
            return "skipped"
    eol = "\r\n" if lines[close_idx].endswith("\r\n") else "\n"
    new_line = f"target_repo: {TARGET_REPO}{eol}"
    lines.insert(close_idx, new_line)
    path.write_text("".join(lines), encoding="utf-8", newline="")
    return "updated"


def main() -> int:
    queue_root = REPO_ROOT / ".ralph"
    if not queue_root.is_dir():
        print(f"error: {queue_root} does not exist", file=sys.stderr)
        return 2

    counts = {"updated": 0, "skipped": 0, "error": 0}
    for state in STATE_DIRS:
        state_dir = queue_root / state
        if not state_dir.is_dir():
            continue
        for pbi_dir in sorted(state_dir.iterdir()):
            if not pbi_dir.is_dir():
                continue
            for filename in ENTRY_FILENAMES:
                entry = pbi_dir / filename
                if not entry.is_file():
                    continue
                result = _insert_field(entry)
                rel = entry.relative_to(REPO_ROOT)
                if result.startswith("error"):
                    counts["error"] += 1
                    print(f"  {rel}: {result}", file=sys.stderr)
                else:
                    counts[result] += 1
                print(f"{rel}: {result}")
    print(f"\n{counts['updated']} updated, {counts['skipped']} skipped, {counts['error']} errors.")
    return 0 if counts["error"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
