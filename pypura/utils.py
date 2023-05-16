"""Utilities."""
from __future__ import annotations

from base64 import b64decode

ENCODING = "utf-8"


def decode(value: str) -> str:
    """Decode a value."""
    return b64decode(value).decode(ENCODING)
