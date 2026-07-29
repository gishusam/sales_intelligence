import sys
from pathlib import Path


SCRAPER_ROOT = Path(__file__).resolve().parents[1] / "scraper"
sys.path.insert(0, str(SCRAPER_ROOT))

from spiders import apartments
from spiders import googlemaps


def test_apartment_queries_use_the_catalog_county_qualifier():
    build_queries = getattr(apartments, "build_queries", None)
    assert callable(build_queries)

    queries = build_queries("thika")

    assert len(queries) == 7
    assert queries[0] == "apartments Thika, Kiambu County, Kenya"
    assert all("Thika, Kiambu County, Kenya" in query for query in queries)
    assert all("Thika Nairobi" not in query for query in queries)


def test_agency_queries_use_the_catalog_county_qualifier():
    build_queries = getattr(googlemaps, "build_queries", None)
    assert callable(build_queries)

    assert build_queries("syokimau") == [
        "property management companies Syokimau, Machakos County, Kenya",
        "property managers Syokimau, Machakos County, Kenya",
    ]


def test_catalog_resolution_preserves_display_name_for_storage_and_scoring():
    resolve = getattr(apartments, "resolve_location", None)
    assert callable(resolve)

    assert resolve("upper-hill") == {
        "id": "upper-hill",
        "name": "Upper Hill",
        "county": "Nairobi",
        "qualified_term": "Upper Hill, Nairobi County, Kenya",
        "tier": "unvetted",
    }
