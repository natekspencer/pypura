"""Utilities."""

from __future__ import annotations

import logging
from base64 import b64decode
from typing import Final

from .const import FRAGRANCES

_LOGGER = logging.getLogger(__name__)

ENCODING: Final = "utf-8"
ISSUE_URL: Final = "https://github.com/natekspencer/pypura/issues/1"

FRAGRANCE_DICT = {f["sku"]: f for f in FRAGRANCES}


def decode(value: str) -> str:
    """Decode a value."""
    return b64decode(value).decode(ENCODING)


def fragrance_name(code: str) -> str:
    """Return fragrance name."""
    if not (name := FRAGRANCE_DICT.get(code, {}).get("name")):
        _LOGGER.warning(
            "Unknown fragrance code '%s', please report this at %s", code, ISSUE_URL
        )
        name = f"Fragrance code: {code}"
        FRAGRANCE_DICT[code] = {"name": name}
    return name
