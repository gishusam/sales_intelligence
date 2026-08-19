from app.routers.communications import _lead_filter_query


def test_lead_filter_query_preserves_campaign_filters():
    where, params = _lead_filter_query({
        "area": "Westlands",
        "lead_type": "Agency",
        "status": "new",
        "ai_score": "high",
    })

    assert "email IS NOT NULL" in where
    assert "email != ''" in where
    assert "lead_type = :lead_type" in where
    assert "area ILIKE :area" in where
    assert "status = :status" in where
    assert "ai_score = :ai_score" in where

    assert params == {
        "area": "%Westlands%",
        "lead_type": "Agency",
        "status": "new",
        "ai_score": "high",
    }


def test_lead_filter_query_allows_no_optional_filters():
    where, params = _lead_filter_query(None)

    assert where == "email IS NOT NULL AND email != ''"
    assert params == {}
