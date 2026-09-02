"""Tests utils."""

from typing import Any

import pytest

from pypura.utils import decode, get_device_name, get_model_name


def test_decode() -> None:
    """Test decode function."""
    assert decode("ZGVjb2RlZA==") == "decoded"


def test_get_device_name() -> None:
    """Test get device name."""
    assert get_device_name({"displayName": {"name": "Test"}}) == "Test Diffuser"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"deviceVer": "v48"}, "Pura Home v4"),
        ({"deviceVer": "wall_5"}, "Pura Home v5"),
        ({"deviceVer": "unknown"}, "Pura"),
        ({"model": 2}, "Pura Car"),
        ({"model": 3}, "Pura Home Plus"),
        ({"model": 4}, "Pura Home Mini"),
        ({"model": -1}, "Pura"),
        ({}, "Pura"),
    ],
)
def test_get_model_name(data: dict[str, Any], expected: str) -> None:
    """Test get model name."""
    assert get_model_name(data) == expected
