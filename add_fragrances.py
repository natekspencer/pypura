"""Script to add fragrances from Pura to a json file."""

from __future__ import annotations

import json
import os
from base64 import b64decode

import requests

ENCODING = "utf-8"
API_KEY = b64decode("a2xldnUtMTY2MDMyOTY5NDMyMTE1NTM4").decode(ENCODING)
FRAGRANCES_FILE = os.path.join(os.path.dirname(__file__), "./pypura/fragrances.json")


def add_fragrances() -> None:
    """Add fragrances."""
    with open(FRAGRANCES_FILE, "r", encoding="utf8") as file:
        fragrances: list[dict[str, str]] = json.load(file)

    resp = requests.post(
        "https://eucs30v2.ksearchnet.com/cs/v2/search",
        json={
            "context": {"apiKeys": [API_KEY]},
            "recordQueries": [
                {
                    "id": "search",
                    "typeOfRequest": "SEARCH",
                    "settings": {
                        "query": {"term": ""},
                        "id": "search",
                        "limit": 4000,
                        "typeOfRecords": ["KLEVU_PRODUCT"],
                        "fields": ["brand", "type", "name", "sku"],
                    },
                }
            ],
        },
        timeout=10,
    )

    if resp.status_code != 200:
        print("Could not get new fragrances.")
        return

    data = resp.json()
    found_fragrances = filter(
        lambda r: r.get("type", "") in ("Fragrance", "Car Fragrance")
        and not r.get("sku", "").startswith("900-"),
        data["queryResults"][0]["records"],
    )
    new_fragrances = False
    for fragrance in found_fragrances:
        if fragrance not in fragrances:
            new_fragrances = True
            fragrances.append(fragrance)
    if not new_fragrances:
        print("No new fragrances to add.")
        return
    fragrances = [i for n, i in enumerate(fragrances) if i not in fragrances[:n]]
    fragrances = sorted(
        fragrances,
        key=lambda d: (d.get("type"), d.get("name"), d.get("brand"), d.get("sku")),
    )
    with open(FRAGRANCES_FILE, "w", encoding=ENCODING) as file:
        json.dump(fragrances, file, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    add_fragrances()
