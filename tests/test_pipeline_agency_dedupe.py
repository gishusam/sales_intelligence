from pathlib import Path


def agency_block():
    text = Path("pipeline.py").read_text()
    return (
        text.split("# 5b: Google Maps agency leads", 1)[1]
        .split("# 5c: Apartment staging leads", 1)[0]
    )


def test_agency_identity_is_not_area_scoped():
    block = agency_block()

    # Area must never determine whether an agency is a duplicate.
    assert "SELECT DISTINCT ON (g.business_name, g.area)" not in block
    assert "LOWER(l.area) = LOWER(g.area)" not in block

    # Identity must use stable business information instead.
    assert "normalized_phone" in block
    assert "l.source_url = c.maps_url" in block


def test_agency_candidates_collapse_same_name_and_phone_before_insert():
    block = agency_block()

    assert "agency_by_contact AS" in block
    assert """SELECT DISTINCT ON (
                normalized_name,
                normalized_phone
            )""" in block
