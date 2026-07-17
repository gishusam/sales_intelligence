from pathlib import Path

from scripts.migrate_railway_leads import identity_key, plan_matches
from scripts.migrate_railway_remaining_data import audit_key, row_key, run_key


def lead(lead_id, name, area, lead_type="agency", phone=None):
    return {
        "id": lead_id,
        "name": name,
        "area": area,
        "lead_type": lead_type,
        "phone": phone,
    }


def test_identity_key_matches_local_and_international_phone_formats():
    local = lead(1, "NW Realite Ltd", "Kilimani", phone="0709 180 180")
    international = lead(2, " nw realite ltd ", "kilimani", phone="+254709180180")

    assert identity_key(local) == identity_key(international)


def test_plan_matches_preserves_distinct_railway_rows():
    source = [
        lead(1, "Same apartments", "Thika", "apartment", None),
        lead(2, "Same apartments", "Thika", "apartment", "0711000000"),
    ]
    target = [lead(20, "Same Apartments", "thika", "apartment", None)]

    assert plan_matches(source, target) == {1: 20}


def test_legacy_agent_contains_no_embedded_database_or_password_credentials():
    text = (Path(__file__).parents[1] / "scraper_agent.py").read_text(encoding="utf-8")

    assert "postgresql://" + "postgres:" not in text
    assert 'RAILWAY_DB_URL = os.getenv("RAILWAY_DATABASE_URL", "")' in text
    assert 'AGENT_PASSWORD = "' not in text


def test_remaining_migration_keys_are_stable_across_case_and_array_identity():
    assert row_key({"developer_name": "  Acme Homes "}, ("developer_name",)) == ("acme homes",)
    assert run_key({"scraper_type": "Agencies", "areas": ["kilimani"], "started_at": "2026-07-17"}) == (
        "agencies", ("kilimani",), "2026-07-17"
    )


def test_audit_key_ignores_source_primary_key():
    row = {"id": 7, "name": "Acme", "area": "Kilimani", "phone": None, "website": None,
           "category": None, "outcome": "imported", "reason": None, "created_at": "now"}
    assert audit_key(row) == audit_key({**row, "id": 99})
