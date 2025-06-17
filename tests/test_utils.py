"""Tests utils."""

from pypura.utils import decode


def test_decode() -> None:
    """Test decode function."""
    assert decode("ZGVjb2RlZA==") == "decoded"
