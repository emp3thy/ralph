"""Tests for ``ralph_executor.subprocess_utils``.

The wrappers exist to immunise the executor against
``UnicodeDecodeError`` when a child emits bytes that are not valid in
the host locale's default codec (cp1252 on stock Windows). Tests
exercise the failure mode that triggered the original bug — a 0x90
byte in stdout — plus a few caller-override sanity checks.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ralph_executor.subprocess_utils import popen_text, run_text


def test_run_text_decodes_non_cp1252_byte_with_replacement() -> None:
    """A child that writes 0x90 (invalid cp1252) must not raise; the
    byte should be decoded to U+FFFD instead. This is the regression
    that prompted the audit — see BUG.md."""
    result = run_text(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'\\x90\\n')",
        ],
        capture_output=True,
        check=True,
    )
    assert result.stdout == "�\n"


def test_run_text_round_trips_utf8_emoji() -> None:
    """A child that emits a UTF-8 multi-byte sequence (e.g. emoji /
    non-ASCII) must round-trip unchanged through the wrapper."""
    result = run_text(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write('héllo \\U0001F600\\n'.encode('utf-8'))",
        ],
        capture_output=True,
        check=True,
    )
    assert result.stdout == "héllo \U0001f600\n"


def test_popen_text_streams_lines_with_replacement(tmp_path: Path) -> None:
    """A streaming child whose stdout contains an invalid cp1252 byte
    must not crash the parent's reader. This mirrors the production
    failure: ``_tee_stream`` iterates ``for line in proc.stdout``, and a
    bad byte midway through the stream would raise inside the loop."""
    script = tmp_path / "bad_byte.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'ok-1\\n')\n"
        "sys.stdout.buffer.write(b'\\x90\\n')\n"
        "sys.stdout.buffer.write(b'ok-3\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    proc = popen_text(
        [sys.executable, "-u", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    assert proc.stdout is not None
    lines: list[str] = []

    def drain() -> None:
        for raw_line in proc.stdout:  # type: ignore[union-attr]
            lines.append(raw_line)

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    proc.wait()
    t.join(timeout=5)
    assert lines == ["ok-1\n", "�\n", "ok-3\n"]


def test_popen_text_caller_can_override_encoding() -> None:
    """Overriding ``encoding`` must propagate. We pick ``ascii`` +
    ``errors="strict"`` so any non-ASCII byte surfaces as a decode
    error inside the read loop."""
    proc = popen_text(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'\\x90\\n')",
        ],
        stdout=subprocess.PIPE,
        encoding="ascii",
        errors="strict",
    )
    assert proc.stdout is not None
    with pytest.raises(UnicodeDecodeError):
        proc.stdout.read()
    proc.wait()


def test_run_text_passes_through_check_and_returncode() -> None:
    """Sanity: the wrapper does not swallow non-zero exits when
    ``check=True``."""
    with pytest.raises(subprocess.CalledProcessError):
        run_text(
            [sys.executable, "-c", "import sys; sys.exit(7)"],
            capture_output=True,
            check=True,
        )

    result = run_text(
        [sys.executable, "-c", "import sys; sys.exit(7)"],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 7
