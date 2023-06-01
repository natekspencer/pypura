"""Constants."""
from __future__ import annotations

import json
import os
from typing import Final

USER_POOL_ID: Final = "dXMtZWFzdC0xX0xhQjcxOGhZdg=="
CLIENT_ID: Final = "NGlla3ViYXQwamI1aWxqZmJhYWxzaXFmOWo="

__FRAGRANCES_FILE = os.path.join(os.path.dirname(__file__), "fragrances.json")
with open(__FRAGRANCES_FILE, "r", encoding="utf8") as file:
    FRAGRANCES: Final[list[dict[str, str]]] = json.load(file)
