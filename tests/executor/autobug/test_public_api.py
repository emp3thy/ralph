def test_public_api_exports_two_detect_functions() -> None:
    from ralph_executor import autobug

    assert hasattr(autobug, "detect_python_crash")
    assert hasattr(autobug, "detect_subprocess_crash")
    assert callable(autobug.detect_python_crash)
    assert callable(autobug.detect_subprocess_crash)


def test_public_api_exports_context_type() -> None:
    from ralph_executor import autobug

    assert hasattr(autobug, "Context")
