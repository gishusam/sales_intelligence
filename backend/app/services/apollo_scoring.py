def score_prospect(prospect: dict) -> dict:
    industry = (prospect.get("industry") or "").lower()

    company_fit = 0
    reasons = []

    if "property management" in industry:
        company_fit = 20
        reasons.append("Property management company")
    elif "property development" in industry:
        company_fit = 18
        reasons.append("Property developer")
    elif (
        "facilities management" in industry
        or "estate management" in industry
    ):
        company_fit = 18
        reasons.append("Facilities or estate management company")
    elif "real estate" in industry:
        company_fit = 10
        reasons.append("General real estate agency")
    elif "construction" in industry:
        company_fit = 6
        reasons.append("Construction-only company")

    operational_signals = {
        "property management",
        "tenant management",
        "rent collection",
        "rental management",
        "letting",
        "estate management",
        "maintenance management",
        "property portfolio",
        "residential units",
        "commercial properties",
    }

    keywords = {
        str(keyword).strip().lower()
        for keyword in prospect.get("keywords", [])
    }

    matched_signals = operational_signals & keywords

    if matched_signals:
        operational_points = min(
            len(matched_signals) * 3,
            15,
        )
        company_fit += operational_points
        reasons.append(
            f"Operational need signals: {len(matched_signals)}"
        )

    employee_count = prospect.get("employee_count")

    if employee_count is not None:
        if 1 <= employee_count <= 4:
            company_fit += 1
            reasons.append(
                "Very small company scale: 1-4 employees"
            )
        elif 5 <= employee_count <= 9:
            company_fit += 3
            reasons.append(
                "Small company scale: 5-9 employees"
            )
        elif 10 <= employee_count <= 200:
            company_fit += 5
            reasons.append(
                "Ideal company scale: 10-200 employees"
            )
        elif 201 <= employee_count <= 500:
            company_fit += 4
            reasons.append(
                "Large company scale: 201-500 employees"
            )
        elif employee_count >= 501:
            company_fit += 3
            reasons.append(
                "Enterprise company scale: 501+ employees"
            )

    decision_maker_fit = 0
    best_decision_maker_title = None

    title_scores = {
        "founder": 25,
        "owner": 25,
        "ceo": 25,
        "chief executive officer": 25,
        "managing director": 25,
        "coo": 23,
        "chief operating officer": 23,
        "head of operations": 23,
        "head of property management": 23,
        "operations manager": 20,
        "property manager": 20,
        "finance director": 18,
        "head of finance": 18,
        "finance manager": 15,
    }

    seniority_scores = {
        "owner": 10,
        "founder": 10,
        "c_suite": 10,
        "head": 8,
        "director": 6,
        "manager": 4,
    }

    for person in prospect.get("decision_makers", []):
        raw_title = (person.get("title") or "").strip()
        title = raw_title.lower()
        seniority = (person.get("seniority") or "").strip().lower()

        title_points = title_scores.get(title)

        if title_points is None:
            if "director" in title:
                title_points = 12
            elif "manager" in title:
                title_points = 8
            else:
                title_points = 0

        seniority_points = seniority_scores.get(seniority, 0)

        person_score = min(
            title_points + seniority_points,
            35,
        )

        if person_score > decision_maker_fit:
            decision_maker_fit = person_score
            best_decision_maker_title = raw_title

    if best_decision_maker_title:
        reasons.append(
            f"Strong decision maker: {best_decision_maker_title}"
        )

    market_fit = 0

    country = (prospect.get("country") or "").strip().lower()
    city = (prospect.get("city") or "").strip().lower()

    priority_markets = {
        "nairobi",
        "kiambu",
        "mombasa",
        "nakuru",
        "kisumu",
    }

    if country == "kenya":
        market_fit += 10
        reasons.append("Kenya market")

        if city in priority_markets:
            market_fit += 5
            reasons.append(
                f"Priority market: {prospect.get('city')}"
            )

    data_confidence = 0

    if prospect.get("domain"):
        data_confidence += 3
        reasons.append("Company domain available")

    if prospect.get("linkedin_url"):
        data_confidence += 2
        reasons.append("Company LinkedIn available")

    decision_makers = prospect.get("decision_makers", [])

    if any(
        person.get("linkedin_url")
        for person in decision_makers
    ):
        data_confidence += 3
        reasons.append("Decision-maker LinkedIn available")

    if (
        prospect.get("apollo_organization_id")
        and any(
            person.get("apollo_person_id")
            for person in decision_makers
        )
    ):
        data_confidence += 2
        reasons.append(
            "Apollo organization and person IDs available"
        )

    quality_score = (
        company_fit
        + decision_maker_fit
        + market_fit
        + data_confidence
    )

    if quality_score >= 80:
        quality_band = "high"
    elif quality_score >= 65:
        quality_band = "good"
    elif quality_score >= 50:
        quality_band = "possible"
    else:
        quality_band = "weak"

    return {
        "quality_score": quality_score,
        "quality_band": quality_band,
        "score_breakdown": {
            "company_fit": company_fit,
            "decision_maker_fit": decision_maker_fit,
            "market_fit": market_fit,
            "data_confidence": data_confidence,
        },
        "score_reasons": reasons,
    }
