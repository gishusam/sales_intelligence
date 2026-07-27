from scripts.restore_verified_snapshot import plan_lead_restoration


def lead(
    lead_id: int,
    name: str,
    *,
    area: str = "Nairobi",
    lead_type: str = "agency",
    phone: str | None = None,
    email: str | None = None,
    website: str | None = None,
    source_url: str | None = None,
) -> dict:
    return {
        "id": lead_id,
        "name": name,
        "name_normalized": None,
        "area": area,
        "lead_type": lead_type,
        "phone": phone,
        "email": email,
        "website": website,
        "source_url": source_url,
    }


def test_plan_matches_renamed_lead_when_phone_and_names_agree():
    snapshot = [lead(10, "Lavendar Properties", phone="+254 711 111 111")]
    current = [lead(20, "Lavender Properties Limited", phone="0711111111")]

    plan = plan_lead_restoration(snapshot, current)

    assert plan.id_map == {10: 20}
    assert plan.to_insert == []


def test_plan_does_not_match_unrelated_leads_on_phone_alone():
    snapshot = [lead(10, "Freedom Heights Residences", phone="0720 111 117")]
    current = [lead(20, "Laser Property Services Ltd", phone="+254 720 111 117")]

    plan = plan_lead_restoration(snapshot, current)

    assert plan.id_map == {}
    assert [row["id"] for row in plan.to_insert] == [10]


def test_plan_matches_exact_identity_before_ambiguous_name():
    snapshot = [
        lead(
            10,
            "Theowner Property Limited",
            area="Kilimani",
            phone="0712345678",
        )
    ]
    current = [
        lead(20, "Theowner Property Limited", area="Westlands", phone="0700000000"),
        lead(21, "Theowner Property Limited", area="Kilimani", phone="+254 712 345 678"),
    ]

    plan = plan_lead_restoration(snapshot, current)

    assert plan.id_map == {10: 21}
    assert plan.to_insert == []


def test_plan_uses_oldest_existing_row_when_exact_identity_is_duplicated():
    snapshot = [lead(10, "Bueno Property Management", phone="+254 722 457 100")]
    current = [
        lead(20, "Bueno Property Management", phone="+254 722 457 100"),
        lead(21, "Bueno Property Management", phone="0722457100"),
    ]

    plan = plan_lead_restoration(snapshot, current)

    assert plan.id_map == {10: 20}
    assert plan.to_insert == []
