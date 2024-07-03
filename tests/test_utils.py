"""Tests constants."""

from pytest import LogCaptureFixture

from pypura.utils import decode, fragrance_name


def test_decode() -> None:
    """Test decode function."""
    assert decode("ZGVjb2RlZA==") == "decoded"


def test_fragrance_name(caplog: LogCaptureFixture) -> None:
    """Test fragrance name lookup."""
    assert fragrance_name("NDM") == "Volcano"
    assert fragrance_name("NDMH") == "Volcano"

    assert fragrance_name("NOTFOUND") == "Fragrance code: NOTFOUND"
    assert "Unknown fragrance code 'NOTFOUND'" in caplog.text
