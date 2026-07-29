"""Resolve canonical scraper locations inside local and Cloud Run workers."""

import json
import os
import re
from functools import lru_cache
from pathlib import Path


def _catalog_path() -> Path:
    configured = os.getenv("SCRAPER_LOCATIONS_PATH")
    candidates = [
        Path(configured) if configured else None,
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "scraper_locations.json",
        Path("/app/scraper_locations.json"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise FileNotFoundError("Canonical scraper location catalog was not found")


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    with _catalog_path().open(encoding="utf-8") as catalog_file:
        return json.load(catalog_file)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def resolve_location(value: str) -> dict:
    normalized = value.strip().lower()
    for location in load_catalog()["locations"]:
        if location["id"] == normalized or location["name"].lower() == normalized:
            return dict(location)
    clean_name = value.strip()
    return {
        "id": _slug(clean_name),
        "name": clean_name,
        "county": "",
        "qualified_term": f"{clean_name}, Kenya",
        "tier": "unvetted",
    }


def source_search_terms(source_id: str) -> list[str]:
    for source in load_catalog()["sources"]:
        if source["id"] == source_id:
            return list(source["search_terms"])
    raise ValueError(f"Unknown scraper source: {source_id}")
