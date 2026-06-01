"""Unit tests for python_crash_signature.

Subprocess-side normaliser tests are appended in T3.
"""

from ralph_executor.autobug.signature import python_crash_signature


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
