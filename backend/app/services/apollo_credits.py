from dataclasses import dataclass
from numbers import Real


LEAD_CREDITS_PER_CONTACT = 1
DIRECT_DIAL_CREDITS_PER_CONTACT = 8
UNIFIED_CREDITS_PER_CONTACT = 9


class CreditBalanceUnavailable(ValueError):
    """Raised when Apollo's live credit balance cannot be trusted."""


@dataclass(frozen=True)
class ApolloCreditBudget:
    mode: str
    lead_credits_left: int
    direct_dial_credits_left: int | None
    reset_date: str | None
    raw: dict

    def can_enrich_contact(self, reserved_attempts: int = 0) -> bool:
        attempts = reserved_attempts + 1

        if self.mode == "unified":
            return (
                self.lead_credits_left
                >= attempts * UNIFIED_CREDITS_PER_CONTACT
            )

        return (
            self.lead_credits_left
            >= attempts * LEAD_CREDITS_PER_CONTACT
            and (self.direct_dial_credits_left or 0)
            >= attempts * DIRECT_DIAL_CREDITS_PER_CONTACT
        )

    def as_dict(self) -> dict:
        return {
            "verified": True,
            "mode": self.mode,
            "lead_credits_left": self.lead_credits_left,
            "direct_dial_credits_left": self.direct_dial_credits_left,
            "reset_date": self.reset_date,
        }


def _remaining(pool: object, name: str) -> int:
    if not isinstance(pool, dict):
        raise CreditBalanceUnavailable(
            f"Apollo {name} balance is missing"
        )

    value = pool.get("left_over")
    if (
        not isinstance(value, Real)
        or isinstance(value, bool)
        or value < 0
    ):
        raise CreditBalanceUnavailable(
            f"Apollo {name} balance is invalid"
        )

    return int(value)


def normalize_credit_budget(payload: dict) -> ApolloCreditBudget:
    if not isinstance(payload, dict):
        raise CreditBalanceUnavailable(
            "Apollo credit response is invalid"
        )

    stats = payload.get("credit_usage_stats")
    if not isinstance(stats, dict):
        raise CreditBalanceUnavailable(
            "Apollo credit usage stats are missing"
        )

    lead_pool = stats.get("lead_credit")
    lead_left = _remaining(lead_pool, "lead_credit")
    direct_pool = stats.get("direct_dial_credit")

    # Unified plans report phone usage through lead_credit and either omit
    # direct_dial_credit or return a zero-limit pool.
    direct_limit = (
        direct_pool.get("limit")
        if isinstance(direct_pool, dict)
        else None
    )
    unified = direct_pool is None or direct_limit == 0
    direct_left = (
        None
        if unified
        else _remaining(direct_pool, "direct_dial_credit")
    )

    cycle = payload.get("current_credit_cycle") or {}
    reset_date = (
        cycle.get("end_date")
        or cycle.get("end")
        or cycle.get("ends_at")
    )

    return ApolloCreditBudget(
        mode="unified" if unified else "separate",
        lead_credits_left=lead_left,
        direct_dial_credits_left=direct_left,
        reset_date=reset_date,
        raw=payload,
    )
