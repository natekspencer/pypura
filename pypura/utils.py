"""Utilities."""

from __future__ import annotations

import logging
from base64 import b64decode
from typing import Final

_LOGGER = logging.getLogger(__name__)

ENCODING: Final = "utf-8"
ISSUE_URL: Final = "https://github.com/natekspencer/pypura/issues/1"


def decode(value: str) -> str:
    """Decode a value."""
    return b64decode(value).decode(ENCODING)
