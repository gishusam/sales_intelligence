import pytest

from app.services.apollo_credits import (
    CreditBalanceUnavailable,
    normalize_credit_budget,
)


def test_separate_pools_reserve_email_and_phone_credits_per_attempt():
    budget = normalize_credit_budget(
        {
            "credit_usage_stats": {
                "lead_credit": {"limit": 100, "consumed": 98, "left_over": 2},
                "direct_dial_credit": {"limit": 80, "consumed": 64, "left_over": 16},
            },
            "current_credit_cycle": {
                "start_date": "2026-09-01",
                "end_date": "2026-10-01",
            },
        }
    )

    assert budget.mode == "separate"
    assert budget.lead_credits_left == 2
    assert budget.direct_dial_credits_left == 16
    assert budget.reset_date == "2026-10-01"
    assert budget.can_enrich_contact(reserved_attempts=1) is True
    assert budget.can_enrich_contact(reserved_attempts=2) is False


def test_unified_pool_reserves_worst_case_nine_credits_per_attempt():
    budget = normalize_credit_budget(
        {
            "credit_usage_stats": {
                "lead_credit": {"limit": 100, "consumed": 81, "left_over": 19},
                "direct_dial_credit": {"limit": 0, "consumed": 0, "left_over": 0},
            },
            "current_credit_cycle": {"end_date": "2026-10-01"},
        }
    )

    assert budget.mode == "unified"
    assert budget.can_enrich_contact(reserved_attempts=1) is True
    assert budget.can_enrich_contact(reserved_attempts=2) is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"credit_usage_stats": {}},
        {"credit_usage_stats": {"lead_credit": {"left_over": None}}},
        {"credit_usage_stats": {"lead_credit": {"left_over": -1}}},
        {"credit_usage_stats": {"lead_credit": {"left_over": "nine"}}},
    ],
)
def test_unverifiable_credit_response_is_rejected(payload):
    with pytest.raises(CreditBalanceUnavailable):
        normalize_credit_budget(payload)
