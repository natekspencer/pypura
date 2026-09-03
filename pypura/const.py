"""Constants."""

from __future__ import annotations

from typing import Final

USER_POOL_ID: Final = "dXMtZWFzdC0xX0xhQjcxOGhZdg=="
CLIENT_ID: Final = "NGlla3ViYXQwamI1aWxqZmJhYWxzaXFmOWo="

DEVICE_VERSION_MODEL_MAP: Final[dict[str, str]] = {
    "1": "Car",
    "27": "Car Pro",
    "mini_1": "Home Mini",
    "plus_1": "Home Plus",
    "v2": "Home v3",
    "v3l": "Home v3",
    "v48": "Home v4",
    "wall_5": "Home v5",
    # device type defaults
    "car": "Car",
    "mini": "Home Mini",
    "plus": "Home Plus",
    "wall": "Home",
}
MODEL_TYPE_MAP: Final[dict[int, str]] = {
    1: "wall",
    2: "car",
    3: "plus",
    4: "mini",
}
