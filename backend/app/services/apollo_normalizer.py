def normalize_organization(raw: dict) -> dict:
    prospect = {
        "apollo_organization_id": raw.get("id"),
        "name": raw.get("name"),
        "domain": raw.get("primary_domain"),
        "website_url": raw.get("website_url"),
        "linkedin_url": raw.get("linkedin_url"),
        "employee_count": raw.get("estimated_num_employees"),
        "city": raw.get("city"),
        "country": raw.get("country"),
        "industry": raw.get("industry"),
    }

    if "keywords" in raw:
        prospect["keywords"] = raw.get("keywords") or []

    return prospect


def normalize_person(raw: dict) -> dict:
    return {
        "apollo_person_id": raw.get("id"),
        "apollo_organization_id": raw.get("organization_id"),
        "first_name": raw.get("first_name"),
        "last_name": raw.get("last_name"),
        "name": raw.get("name"),
        "title": raw.get("title"),
        "seniority": raw.get("seniority"),
        "linkedin_url": raw.get("linkedin_url"),
    }
