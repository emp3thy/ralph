#!/usr/bin/env python3
"""Walk events.db files, extract error-class stderr blobs into raw/ bucket.

Operator runs this against a live ralph queue clone, then hand-sorts the
extracted blobs in ``tests/executor/autobug/fixtures/stderr_corpus/raw/``
into bucket subdirectories based on root cause.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        required=True,
        type=Path,
        help="Path to .ralph/state/events.db",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output dir (fixtures/stderr_corpus/raw/)",
    )
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(args.db))
    try:
        rows = conn.execute(
            "SELECT rowid, payload FROM events WHERE kind = ?",
            ("autobug_emitted",),
        ).fetchall()
        written = 0
        for rowid, payload in rows:
            try:
                data = json.loads(payload or "{}")
            except json.JSONDecodeError:
                continue
            stderr = data.get("stderr")
            if not stderr:
                continue
            (args.out / f"event-{rowid:05d}.txt").write_text(stderr, encoding="utf-8")
            written += 1
    finally:
        conn.close()
    print(f"wrote {written} blobs to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
