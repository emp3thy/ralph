"""Identity sanitisation, validation, hostname default."""

from __future__ import annotations

import pytest

from ralph_executor.identity import (
    INSTANCE_ID_REGEX,
    InstanceIdError,
    default_instance_id,
    sanitize_instance_id,
    validate_instance_id,
)


class TestSanitize:
    def test_lowercases(self) -> None:
        assert sanitize_instance_id("MyBox") == "mybox"

    def test_replaces_dots_with_dashes(self) -> None:
        assert sanitize_instance_id("box.example.com") == "box-example-com"

    def test_replaces_other_chars_with_dashes(self) -> None:
        assert sanitize_instance_id("box/space:colon") == "box-space-colon"

    def test_collapses_repeated_dashes(self) -> None:
        assert sanitize_instance_id("box...com") == "box-com"

    def test_strips_leading_trailing_dashes(self) -> None:
        assert sanitize_instance_id("---box---") == "box"

    def test_already_clean_passes_through(self) -> None:
        assert sanitize_instance_id("ralph-a") == "ralph-a"

    def test_empty_after_sanitise_returns_empty(self) -> None:
        assert sanitize_instance_id("---") == ""


class TestValidate:
    def test_accepts_simple_id(self) -> None:
        assert validate_instance_id("ralph-a") == "ralph-a"

    def test_accepts_digits_and_underscore(self) -> None:
        assert validate_instance_id("ralph_1") == "ralph_1"

    def test_rejects_empty(self) -> None:
        with pytest.raises(InstanceIdError, match="must be set"):
            validate_instance_id("")

    def test_rejects_leading_dash(self) -> None:
        with pytest.raises(InstanceIdError, match="does not match"):
            validate_instance_id("-ralph")

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(InstanceIdError, match="does not match"):
            validate_instance_id("Ralph")

    def test_rejects_too_long(self) -> None:
        with pytest.raises(InstanceIdError, match="does not match"):
            validate_instance_id("a" * 64)


class TestRegex:
    def test_pattern_value(self) -> None:
        assert INSTANCE_ID_REGEX.pattern == r"^[a-z0-9][a-z0-9_-]{0,62}$"


class TestDefaultFromHostname:
    def test_returns_sanitised_hostname(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("socket.gethostname", lambda: "MyBox.example.com")
        assert default_instance_id() == "mybox-example-com"

    def test_empty_hostname_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("socket.gethostname", lambda: "")
        assert default_instance_id() == ""
