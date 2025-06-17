"""Tests constants."""

from pypura.const import CLIENT_ID, USER_POOL_ID


def test_constants() -> None:
    """Test constants."""
    assert USER_POOL_ID is not None
    assert CLIENT_ID is not None
