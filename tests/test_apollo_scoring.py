from app.services.apollo_scoring import score_prospect


def test_property_management_company_gets_max_business_model_fit():
    prospect = {
        "apollo_organization_id": "org-123",
        "name": "Acme Property Management",
        "industry": "property management",
        "employee_count": None,
        "city": None,
        "country": None,
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["company_fit"] == 20
    assert result["quality_score"] == 20
    assert "Property management company" in result["score_reasons"]


def test_property_developer_scores_18_for_business_model_fit():
    prospect = {
        "apollo_organization_id": "org-456",
        "name": "Acme Developments",
        "industry": "property development",
        "employee_count": None,
        "city": None,
        "country": None,
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["company_fit"] == 18
    assert result["quality_score"] == 18
    assert "Property developer" in result["score_reasons"]


def test_general_real_estate_agency_scores_lower_business_model_fit():
    prospect = {
        "apollo_organization_id": "org-789",
        "name": "Acme Realty",
        "industry": "real estate",
        "employee_count": None,
        "city": None,
        "country": None,
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["company_fit"] == 10
    assert result["quality_score"] == 10
    assert "General real estate agency" in result["score_reasons"]


def test_facilities_management_scores_18_for_business_model_fit():
    prospect = {
        "apollo_organization_id": "org-facilities",
        "name": "Acme Facilities Management",
        "industry": "facilities management",
        "employee_count": None,
        "city": None,
        "country": None,
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["company_fit"] == 18
    assert result["quality_score"] == 18
    assert "Facilities or estate management company" in result["score_reasons"]


def test_construction_only_company_scores_low_business_model_fit():
    prospect = {
        "apollo_organization_id": "org-construction",
        "name": "Acme Construction",
        "industry": "construction",
        "employee_count": None,
        "city": None,
        "country": None,
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["company_fit"] == 6
    assert result["quality_score"] == 6
    assert "Construction-only company" in result["score_reasons"]


def test_operational_need_signals_add_three_points_each():
    prospect = {
        "apollo_organization_id": "org-needs",
        "name": "Acme Realty",
        "industry": "real estate",
        "keywords": [
            "tenant management",
            "rent collection",
        ],
        "employee_count": None,
        "city": None,
        "country": None,
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["company_fit"] == 16
    assert result["quality_score"] == 16
    assert "Operational need signals: 2" in result["score_reasons"]


def test_operational_need_signals_are_capped_at_fifteen_points():
    prospect = {
        "apollo_organization_id": "org-many-signals",
        "name": "Acme Realty",
        "industry": "real estate",
        "keywords": [
            "property management",
            "tenant management",
            "rent collection",
            "rental management",
            "letting",
            "estate management",
        ],
        "employee_count": None,
        "city": None,
        "country": None,
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["company_fit"] == 25
    assert result["quality_score"] == 25


def test_ideal_company_size_adds_five_points():
    prospect = {
        "apollo_organization_id": "org-scale",
        "name": "Acme Realty",
        "industry": "real estate",
        "keywords": [],
        "employee_count": 48,
        "city": None,
        "country": None,
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["company_fit"] == 15
    assert result["quality_score"] == 15
    assert "Ideal company scale: 10-200 employees" in result["score_reasons"]


def test_company_scale_uses_expected_size_bands():
    cases = [
        (3, 11),    # base 10 + 1
        (7, 13),    # base 10 + 3
        (250, 14),  # base 10 + 4
        (900, 13),  # base 10 + 3
    ]

    for employee_count, expected_score in cases:
        prospect = {
            "apollo_organization_id": "org-scale-band",
            "name": "Acme Realty",
            "industry": "real estate",
            "keywords": [],
            "employee_count": employee_count,
            "city": None,
            "country": None,
            "domain": None,
            "linkedin_url": None,
            "decision_makers": [],
        }

        result = score_prospect(prospect)

        assert result["score_breakdown"]["company_fit"] == expected_score
        assert result["quality_score"] == expected_score


def test_managing_director_with_c_suite_seniority_gets_max_decision_maker_fit():
    prospect = {
        "apollo_organization_id": "org-md",
        "name": "Acme Realty",
        "industry": "real estate",
        "keywords": [],
        "employee_count": None,
        "city": None,
        "country": None,
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [
            {
                "apollo_person_id": "person-md",
                "name": "Jane Doe",
                "title": "Managing Director",
                "seniority": "c_suite",
                "linkedin_url": None,
            }
        ],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["decision_maker_fit"] == 35
    assert result["quality_score"] == 47
    assert "Strong decision maker: Managing Director" in result["score_reasons"]


def test_decision_maker_fit_uses_best_role_and_seniority():
    prospect = {
        "apollo_organization_id": "org-best-person",
        "name": "Acme Realty",
        "industry": "real estate",
        "keywords": [],
        "employee_count": None,
        "city": None,
        "country": None,
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [
            {
                "apollo_person_id": "person-finance",
                "name": "John Doe",
                "title": "Finance Manager",
                "seniority": "manager",
                "linkedin_url": None,
            },
            {
                "apollo_person_id": "person-ops",
                "name": "Jane Doe",
                "title": "Head of Operations",
                "seniority": "head",
                "linkedin_url": None,
            },
        ],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["decision_maker_fit"] == 31
    assert result["quality_score"] == 43
    assert "Strong decision maker: Head of Operations" in result["score_reasons"]


def test_kenya_priority_market_gets_full_market_fit():
    prospect = {
        "apollo_organization_id": "org-nairobi",
        "name": "Acme Realty",
        "industry": "real estate",
        "keywords": [],
        "employee_count": None,
        "city": "Nairobi",
        "country": "Kenya",
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["market_fit"] == 15
    assert result["quality_score"] == 25
    assert "Kenya market" in result["score_reasons"]
    assert "Priority market: Nairobi" in result["score_reasons"]


def test_complete_prospect_data_gets_full_data_confidence():
    prospect = {
        "apollo_organization_id": "org-data",
        "name": "Acme Realty",
        "industry": "real estate",
        "keywords": [],
        "employee_count": None,
        "city": None,
        "country": None,
        "domain": "acme.co.ke",
        "linkedin_url": "https://linkedin.com/company/acme",
        "decision_makers": [
            {
                "apollo_person_id": "person-data",
                "name": "Jane Doe",
                "title": None,
                "seniority": None,
                "linkedin_url": "https://linkedin.com/in/janedoe",
            }
        ],
    }

    result = score_prospect(prospect)

    assert result["score_breakdown"]["data_confidence"] == 10
    assert result["quality_score"] == 20
    assert "Company domain available" in result["score_reasons"]
    assert "Company LinkedIn available" in result["score_reasons"]
    assert "Decision-maker LinkedIn available" in result["score_reasons"]
    assert "Apollo organization and person IDs available" in result["score_reasons"]


def test_high_quality_prospect_gets_high_quality_band():
    prospect = {
        "apollo_organization_id": "org-high",
        "name": "Acme Property Management",
        "industry": "property management",
        "keywords": [
            "tenant management",
            "rent collection",
            "rental management",
            "letting",
            "property portfolio",
        ],
        "employee_count": 48,
        "city": "Nairobi",
        "country": "Kenya",
        "domain": "acme.co.ke",
        "linkedin_url": "https://linkedin.com/company/acme",
        "decision_makers": [
            {
                "apollo_person_id": "person-high",
                "name": "Jane Doe",
                "title": "Managing Director",
                "seniority": "c_suite",
                "linkedin_url": "https://linkedin.com/in/janedoe",
            }
        ],
    }

    result = score_prospect(prospect)

    assert result["quality_score"] == 100
    assert result["quality_band"] == "high"


def test_quality_score_uses_good_possible_and_weak_bands():
    good = {
        "apollo_organization_id": None,
        "name": "Good Prospect",
        "industry": "property management",
        "keywords": [],
        "employee_count": 48,
        "city": "Nairobi",
        "country": "Kenya",
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [
            {
                "apollo_person_id": None,
                "name": "Jane Doe",
                "title": "Managing Director",
                "seniority": "c_suite",
                "linkedin_url": None,
            }
        ],
    }

    possible = {
        "apollo_organization_id": None,
        "name": "Possible Prospect",
        "industry": "real estate",
        "keywords": [],
        "employee_count": 48,
        "city": "Nairobi",
        "country": "Kenya",
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [
            {
                "apollo_person_id": None,
                "name": "John Doe",
                "title": "Operations Manager",
                "seniority": "manager",
                "linkedin_url": None,
            }
        ],
    }

    weak = {
        "apollo_organization_id": None,
        "name": "Weak Prospect",
        "industry": "real estate",
        "keywords": [],
        "employee_count": None,
        "city": None,
        "country": None,
        "domain": None,
        "linkedin_url": None,
        "decision_makers": [],
    }

    assert score_prospect(good)["quality_band"] == "good"
    assert score_prospect(possible)["quality_band"] == "possible"
    assert score_prospect(weak)["quality_band"] == "weak"
