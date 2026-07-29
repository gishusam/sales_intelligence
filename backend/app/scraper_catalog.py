"""Canonical location and source options for scraper runs."""

import json
from functools import lru_cache
from pathlib import Path


CATALOG_PATH = Path(__file__).with_name("scraper_locations.json")


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    with CATALOG_PATH.open(encoding="utf-8") as catalog_file:
        return json.load(catalog_file)


def public_options() -> dict:
    catalog = load_catalog()
    return {
        "recent_run_days": catalog["recent_run_days"],
        "history_window": catalog["history_window"],
        "sources": catalog["sources"],
        "locations": catalog["locations"],
    }


def get_location(area_id: str) -> dict | None:
    normalized = area_id.strip().lower()
    return next(
        (
            location
            for location in load_catalog()["locations"]
            if location["id"] == normalized
        ),
        None,
    )
