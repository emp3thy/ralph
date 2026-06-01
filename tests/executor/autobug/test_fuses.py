from ralph_executor.autobug.fuses import recursion_check


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
