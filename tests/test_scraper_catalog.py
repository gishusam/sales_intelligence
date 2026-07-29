import os

import pytest
from fastapi import HTTPException


os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.com")


def test_scraper_options_expose_one_verified_phase_one_catalog():
    from app.routers import scraper

    get_options = getattr(scraper, "get_scraper_options", None)
    assert callable(get_options)

    options = get_options()
    locations = options["locations"]
    ids = [location["id"] for location in locations]

    assert len(locations) == 25
    assert len(ids) == len(set(ids))
    assert options["recent_run_days"] == 7
    assert options["history_window"] == 50

    by_id = {location["id"]: location for location in locations}
    assert by_id["runda"]["county"] == "Nairobi"
    assert by_id["ruaka"]["qualified_term"] == (
        "Ruaka, Kiambu County, Kenya"
    )
    assert by_id["syokimau"]["qualified_term"] == (
        "Syokimau, Machakos County, Kenya"
    )


def test_scraper_options_describe_real_search_sets():
    from app.routers import scraper

    options = scraper.get_scraper_options()
    sources = {source["id"]: source for source in options["sources"]}

    assert len(sources["apartments"]["search_terms"]) == 7
    assert sources["apartments"]["requires_area"] is True
    assert sources["apartments"]["max_areas"] == 1
    assert sources["agencies"]["search_terms"] == [
        "property management companies",
        "property managers",
    ]
    assert sources["developers"]["requires_area"] is False
    assert sources["developers"]["max_areas"] == 0


def test_location_scrapers_require_one_known_area_id():
    from app.routers import scraper

    validate = getattr(scraper, "validate_run_request", None)
    assert callable(validate)

    location = validate("apartments", ["kilimani"])
    assert location["name"] == "Kilimani"

    with pytest.raises(HTTPException, match="exactly one area"):
        validate("apartments", [])
    with pytest.raises(HTTPException, match="exactly one area"):
        validate("agencies", ["kilimani", "westlands"])
    with pytest.raises(HTTPException, match="Unknown area"):
        validate("apartments", ["not-a-real-area"])


def test_developer_scraper_rejects_geography():
    from app.routers import scraper

    assert scraper.validate_run_request("developers", []) is None
    with pytest.raises(HTTPException, match="does not accept areas"):
        scraper.validate_run_request("developers", ["kilimani"])
