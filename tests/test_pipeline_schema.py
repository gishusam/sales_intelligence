import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_listing_promotion_only_targets_current_lead_columns():
    source = (REPO_ROOT / "pipeline.py").read_text(encoding="utf-8")
    match = re.search(
        r"INSERT INTO leads \(\s*(.*?)\)\s*SELECT DISTINCT ON .*?"
        r"FROM listing_staging",
        source,
        re.DOTALL,
    )
    assert match is not None
    insert_columns = match.group(1)

    for removed_column in (
        "owner_url", "raw_location", "raw_price", "unit_count",
        "google_rating", "google_reviews",
    ):
        assert removed_column not in insert_columns

    assert "COALESCE(owner_name, name), area, phone" in source


def test_staging_schema_contains_fields_written_by_google_scrapers():
    schema = (REPO_ROOT / "migrations" / "init.sql").read_text(encoding="utf-8")

    google_table = schema.split(
        "CREATE TABLE IF NOT EXISTS google_places_leads", 1
    )[1].split(");", 1)[0]
    developer_table = schema.split(
        "CREATE TABLE IF NOT EXISTS developer_staging", 1
    )[1].split(");", 1)[0]

    assert "rating" in google_table
    assert "review_count" in google_table
    assert "rating" in developer_table
    assert "review_count" in developer_table


def test_leads_schema_contains_notes_used_by_api_routes():
    schema = (REPO_ROOT / "migrations" / "init.sql").read_text(encoding="utf-8")
    model = (REPO_ROOT / "backend" / "app" / "models" / "lead.py").read_text(
        encoding="utf-8"
    )

    leads_table = schema.split(
        "CREATE TABLE IF NOT EXISTS leads", 1
    )[1].split(");", 1)[0]

    assert re.search(r"\bnotes\s+TEXT\b", leads_table)
    assert re.search(r"\bnotes\s*=\s*Column\(Text\)", model)
    assert "ALTER TABLE leads ADD COLUMN IF NOT EXISTS notes TEXT;" in schema
