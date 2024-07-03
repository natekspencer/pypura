"""Tests constants."""

from pypura.const import FRAGRANCES


def test_fragrance_const() -> None:
    """Test the fragrance const."""
    assert len(FRAGRANCES) >= 745
