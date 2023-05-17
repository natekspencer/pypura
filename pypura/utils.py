"""Utilities."""
from __future__ import annotations

import logging
from base64 import b64decode

from .const import FRAGRANCES

_LOGGER = logging.getLogger(__name__)

ENCODING = "utf-8"
ISSUE_URL = "https://github.com/natekspencer/pypura/issues/1"


def decode(value: str) -> str:
    """Decode a value."""
    return b64decode(value).decode(ENCODING)


def fragrance_name(code: str) -> str:
    """Return fragrance name."""
    if not (name := FRAGRANCES.get(code)):
        _LOGGER.warning(
            "Unknown fragrance code '%s', please report this at %s", code, ISSUE_URL
        )
        name = f"Fragrance code: {code}"
    return name
