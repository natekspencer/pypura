"""Tests utils."""

from pypura.utils import decode, get_device_name, get_model_name


def test_decode() -> None:
    """Test decode function."""
    assert decode("ZGVjb2RlZA==") == "decoded"


def test_get_device_name() -> None:
    """Test get device name."""
    assert get_device_name({"displayName": {"name": "Test"}}) == "Test Diffuser"


def test_get_model_name() -> None:
    """Test get model name."""
    assert get_model_name({"deviceVer": "v48"}) == "Pura Home v4"
    assert get_model_name({"deviceVer": "wall_5"}) == "Pura Home v5"
    assert get_model_name({"model": 1}) == "Pura Home"
    assert get_model_name({"model": 2}) == "Pura Car"
    assert get_model_name({"model": 3}) == "Pura Home Plus"
    assert get_model_name({"model": 4}) == "Pura Home Mini"
    assert get_model_name({"model": -1}) == "Pura"
