from app.services.apollo_normalizer import normalize_organization


def test_normalize_organization_maps_apollo_fields():
    raw = {
        "id": "org-123",
        "name": "Acme Property Management",
        "primary_domain": "acme.co.ke",
        "website_url": "https://acme.co.ke",
        "linkedin_url": "https://linkedin.com/company/acme",
        "estimated_num_employees": 48,
        "city": "Nairobi",
        "country": "Kenya",
        "industry": "real estate",
    }

    prospect = normalize_organization(raw)

    assert prospect == {
        "apollo_organization_id": "org-123",
        "name": "Acme Property Management",
        "domain": "acme.co.ke",
        "website_url": "https://acme.co.ke",
        "linkedin_url": "https://linkedin.com/company/acme",
        "employee_count": 48,
        "city": "Nairobi",
        "country": "Kenya",
        "industry": "real estate",
    }


from app.services.apollo_normalizer import normalize_person


def test_normalize_person_maps_apollo_fields():
    raw = {
        "id": "person-123",
        "first_name": "Jane",
        "last_name": "Doe",
        "name": "Jane Doe",
        "title": "Managing Director",
        "seniority": "c_suite",
        "linkedin_url": "https://linkedin.com/in/janedoe",
        "organization_id": "org-123",
    }

    person = normalize_person(raw)

    assert person == {
        "apollo_person_id": "person-123",
        "apollo_organization_id": "org-123",
        "first_name": "Jane",
        "last_name": "Doe",
        "name": "Jane Doe",
        "title": "Managing Director",
        "seniority": "c_suite",
        "linkedin_url": "https://linkedin.com/in/janedoe",
    }


def test_normalize_organization_preserves_operational_keywords():
    raw = {
        "id": "org-keywords",
        "name": "Acme Property Management",
        "keywords": [
            "tenant management",
            "rent collection",
        ],
    }

    prospect = normalize_organization(raw)

    assert prospect["keywords"] == [
        "tenant management",
        "rent collection",
    ]


def test_normalize_person_builds_display_name_from_obfuscated_last_name():
    raw = {
        "id": "68527052713b92000135dac5",
        "first_name": "Kenneth",
        "last_name_obfuscated": "Mb***e",
        "title": "Managing Director",
        "organization": {
            "name": "Centum Real Estate",
        },
    }

    person = normalize_person(raw)

    assert person["name"] == "Kenneth Mb***e"
