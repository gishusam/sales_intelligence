import pytest
from pydantic import ValidationError

from app.schemas.apollo import ProspectSearchRequest


def test_prospect_search_request_uses_nyumba_zetu_fields():
    request = ProspectSearchRequest(
        locations=["Kenya"],
        business_types=[
            "property management",
            "real estate",
        ],
        employee_min=5,
        employee_max=200,
    )

    assert request.locations == ["Kenya"]
    assert request.business_types == [
        "property management",
        "real estate",
    ]
    assert request.employee_min == 5
    assert request.employee_max == 200
    assert request.page == 1
    assert request.per_page == 25


def test_prospect_search_request_rejects_inverted_employee_range():
    with pytest.raises(ValidationError):
        ProspectSearchRequest(
            locations=["Kenya"],
            business_types=["property management"],
            employee_min=200,
            employee_max=5,
        )


def test_prospect_search_request_accepts_decision_maker_filters():
    request = ProspectSearchRequest(
        locations=["Kenya"],
        business_types=["property management"],
        employee_min=5,
        employee_max=200,
        decision_maker_titles=[
            "Founder",
            "Managing Director",
            "Operations Manager",
        ],
        decision_maker_seniorities=[
            "owner",
            "founder",
            "c_suite",
            "director",
            "manager",
        ],
    )

    assert request.decision_maker_titles == [
        "Founder",
        "Managing Director",
        "Operations Manager",
    ]
    assert request.decision_maker_seniorities == [
        "owner",
        "founder",
        "c_suite",
        "director",
        "manager",
    ]


def test_prospect_search_request_rejects_invalid_pagination():
    with pytest.raises(ValidationError):
        ProspectSearchRequest(
            locations=["Kenya"],
            business_types=["property management"],
            employee_min=5,
            employee_max=200,
            page=0,
        )

    with pytest.raises(ValidationError):
        ProspectSearchRequest(
            locations=["Kenya"],
            business_types=["property management"],
            employee_min=5,
            employee_max=200,
            per_page=101,
        )
