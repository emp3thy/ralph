"""Unit tests for python_crash_signature, subprocess_crash_signature and _normalise."""

from ralph_executor.autobug.signature import (
    _normalise,
    python_crash_signature,
    subprocess_crash_signature,
)


def _raise_at_known_lineno() -> None:
    raise RuntimeError("boom at known line")


def test_python_crash_signature_is_deterministic() -> None:
    try:
        _raise_at_known_lineno()
    except RuntimeError as exc:
        sig_a = python_crash_signature(exc)
    try:
        _raise_at_known_lineno()
    except RuntimeError as exc:
        sig_b = python_crash_signature(exc)
    assert sig_a == sig_b
    assert len(sig_a) == 64  # sha256 hex


def test_python_crash_signature_differs_by_exc_class() -> None:
    try:
        raise RuntimeError("a")
    except RuntimeError as exc:
        sig_rt = python_crash_signature(exc)
    try:
        raise ValueError("a")
    except ValueError as exc:
        sig_ve = python_crash_signature(exc)
    assert sig_rt != sig_ve


def test_python_crash_signature_single_user_frame_uses_sentinel() -> None:
    try:
        raise RuntimeError("single")
    except RuntimeError as exc:
        sig = python_crash_signature(exc)
    assert len(sig) == 64


def test_normalise_uuid_collapses() -> None:
    assert "<UUID>" in _normalise("request 6f0c8b27-3a8b-4f6f-8e5f-2cd2cc62c9a8 failed")


def test_normalise_timestamp_collapses() -> None:
    assert "<TS>" in _normalise("at 2026-05-31T14:23:01Z error")
    assert "<TS>" in _normalise("at 2026-05-31T14:23:01.123456+00:00 error")


def test_normalise_path_collapses_windows_and_posix() -> None:
    assert "<PATH>" in _normalise("crashed at C:\\Users\\foo\\bar.py")
    assert "<PATH>" in _normalise("crashed at /home/user/foo.py")


def test_normalise_pbi_id_collapses() -> None:
    assert "<PBI>" in _normalise("WI-247 failed")
    assert "<PBI>" in _normalise("autobug-a3f2c9-001 failed")


def test_normalise_sha_collapses_after_uuid_and_path() -> None:
    # SHA pattern must NOT eat the UUID (run after).
    out = _normalise("ref 51cc97a fixed 6f0c8b27-3a8b-4f6f-8e5f-2cd2cc62c9a8")
    assert "<SHA>" in out
    assert "<UUID>" in out


def test_subprocess_signature_collapses_per_iteration_noise() -> None:
    s1 = "Error at 2026-05-31T14:23:01Z for WI-247 in /tmp/foo.py"
    s2 = "Error at 2026-05-31T14:24:01Z for WI-248 in /tmp/foo.py"
    assert subprocess_crash_signature(1, s1) == subprocess_crash_signature(1, s2)


def test_subprocess_signature_differs_by_exit_code() -> None:
    assert subprocess_crash_signature(1, "msg") != subprocess_crash_signature(137, "msg")


def test_subprocess_signature_uses_last_nonempty_line() -> None:
    s = "irrelevant prefix\n\nKilled\n"
    sig = subprocess_crash_signature(137, s)
    sig_direct = subprocess_crash_signature(137, "Killed")
    assert sig == sig_direct
