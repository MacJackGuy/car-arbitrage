"""
Singapore COE and PARF rebate calculations.

COE (Certificate of Entitlement)
─────────────────────────────────
Every vehicle in Singapore requires a 10-year COE.  On deregistration the
owner receives a pro-rated refund based on the Prevailing Quota Premium (PQP)
at the time of deregistration.  Since PQP is not known in advance, this module
uses the original COE value paid as a conservative approximation.

    Refund ≈ (months_remaining / 120) × original_coe_value_sgd

PARF (Preferential Additional Registration Fee) rebate
───────────────────────────────────────────────────────
Applies only to vehicles deregistered within 10 years of first registration.
The rebate is a percentage of the ARF (Additional Registration Fee) paid,
tiered by age at deregistration.

Age at deregistration  PARF rate
─────────────────────  ─────────
≤ 5 years              75 %
  6 years              70 %
  7 years              65 %
  8 years              60 %
  9 years              55 %
 10 years              50 %
> 10 years               0 %  (no PARF eligibility)
"""

from datetime import date

# PARF rebate rate keyed by age in whole years at deregistration
_PARF_RATES: dict[int, float] = {
    0: 0.75, 1: 0.75, 2: 0.75, 3: 0.75, 4: 0.75, 5: 0.75,
    6: 0.70,
    7: 0.65,
    8: 0.60,
    9: 0.55,
    10: 0.50,
}

COE_TOTAL_MONTHS = 120  # standard 10-year COE


def coe_refund(months_remaining: int, original_coe_value_sgd: float) -> float:
    """
    Pro-rated COE refund on deregistration.

    Returns 0 if either argument is ≤ 0 (unknown / expired COE).
    Clamps months_remaining to [0, 120].
    """
    if months_remaining <= 0 or original_coe_value_sgd <= 0:
        return 0.0
    months = min(months_remaining, COE_TOTAL_MONTHS)
    return round((months / COE_TOTAL_MONTHS) * original_coe_value_sgd, 2)


def parf_rebate(
    arf_paid_sgd: float,
    year_registered: int,
    deregister_year: int | None = None,
) -> float:
    """
    PARF rebate on deregistration.

    Args:
        arf_paid_sgd:     Total ARF paid at original registration.
        year_registered:  Year of first SG registration.
        deregister_year:  Year of deregistration (defaults to current year).

    Returns:
        PARF rebate in SGD, or 0.0 if the vehicle is > 10 years old or
        arf_paid_sgd is 0/unknown.
    """
    if arf_paid_sgd <= 0:
        return 0.0

    if deregister_year is None:
        deregister_year = date.today().year

    age = deregister_year - year_registered
    rate = _PARF_RATES.get(age, 0.0)   # 0 for any age > 10
    return round(arf_paid_sgd * rate, 2)


def parf_rate(year_registered: int, deregister_year: int | None = None) -> float:
    """Return the PARF rate (0.0–0.75) for a vehicle's age, without the ARF amount."""
    if deregister_year is None:
        deregister_year = date.today().year
    age = deregister_year - year_registered
    return _PARF_RATES.get(age, 0.0)


def estimate_arf(omv_sgd: float) -> float:
    """
    Estimate ARF paid from OMV using LTA's tiered ARF rates.

    Tier         OMV band           ARF rate
    ────────     ─────────────────  ────────
    First        up to S$20,000      100 %
    Next         S$20,001–$50,000    140 %
    Next         S$50,001–$100,000   190 %
    Above        > S$100,000         250 %

    Useful when ARF is not listed but OMV is known.
    """
    if omv_sgd <= 0:
        return 0.0

    arf = 0.0
    bands = [
        (20_000,  1.00),
        (30_000,  1.40),   # $20k–$50k
        (50_000,  1.90),   # $50k–$100k
        (float("inf"), 2.50),
    ]
    remaining = omv_sgd
    for band_size, rate in bands:
        chunk = min(remaining, band_size)
        arf += chunk * rate
        remaining -= chunk
        if remaining <= 0:
            break

    return round(arf, 2)
