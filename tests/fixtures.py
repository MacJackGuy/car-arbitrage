"""
Realistic test fixtures for the car arbitrage profit engine.

All scenarios are based on real SG market dynamics:

  Track 1 (near-COE flip):
    Old cars (>10 yr) have no PARF, so COE is the only SG rebate.
    The key is finding cars priced cheaply enough that UK value > landed cost.

  Full-duty opportunity:
    HMRC customs value = SG purchase price (invoice), NOT the net cost after rebates.
    This means high SG purchase prices (even with large rebates) suffer heavy duty.
    Full-duty S$50k threshold is best met by OLD cheap SG cars with premium UK prices.

  TOR opportunity (no duty):
    Young cars with large PARF drive large net-cost discounts. These show big TOR
    profit even if full-duty is below threshold. Always show both scenarios.

FX assumption: 1 GBP = 1.72 SGD throughout tests.
"""

FX = 1.72   # 1 GBP = 1.72 SGD

# ── F430 Scuderia: near-COE old car → full-duty opportunity ──────────────────
# F430 Scuderia registered 2008, ~18 years old → no PARF, no ARF refund.
# SG near-COE price: S$35,000. COE original: S$60k, 3 months left → refund S$1,500.
# Net SG: S$33,500.  Base import: ~S$17,973.
# Customs value for duty: S$35,000 / 1.72 = £20,349
#   Duty: £1,323 → S$2,275.  VAT: £4,334 → S$7,455.
# Total full-duty cost: S$33,500 + S$17,973 + S$9,730 = S$61,203
# UK F430 Scuderia: £78,000 = S$134,160
# Full-duty profit: S$134,160 − S$61,203 = S$72,957  ✓ exceeds S$50k
SCUDERIA_FULL_DUTY_OPPORTUNITY = dict(
    sg_price_sgd            = 35_000,
    uk_sale_price_gbp       = 78_000,
    fx_rate_gbp_sgd         = FX,
    coe_months_remaining    = 3,
    coe_original_value_sgd  = 60_000,
    arf_paid_sgd            = 0,
    year_registered         = 2008,     # 2026 − 2008 = 18 years → no PARF
)

# ── F430 (base model): near-COE, low price → TOR positive but below full-duty threshold ──
# Used to verify near-COE math without expecting full-duty threshold.
F430_NEAR_COE = dict(
    sg_price_sgd            = 25_000,
    uk_sale_price_gbp       = 30_000,
    fx_rate_gbp_sgd         = FX,
    coe_months_remaining    = 3,
    coe_original_value_sgd  = 60_000,
    arf_paid_sgd            = 0,        # >10 years old, no PARF
    year_registered         = 2006,     # 2026 − 2006 = 20 years old → no PARF
)

# ── Porsche 992 GT3: young car with large PARF → strong TOR opportunity ───────
# NOTE: coe_months_remaining=6 + year_registered=2022 are deliberately
# inconsistent (a 2022 car would have ~72 months left in 2026). The profit
# engine accepts any inputs; this fixture tests the arithmetic in isolation.
#
# Net SG: S$250k − S$5k (COE refund) − S$135k (PARF 75%) = S$110k
# UK 992 GT3: £125k = S$215k
# TOR profit: S$215k − S$128k = S$87k  ✓  (exceeds S$50k TOR threshold)
# Full-duty profit: customs duty on S$250k invoice is large → S$17.5k only
# → This is a TOR opportunity, not a full-duty opportunity.
GT3_NEAR_COE_OPPORTUNITY = dict(
    sg_price_sgd            = 250_000,
    uk_sale_price_gbp       = 125_000,
    fx_rate_gbp_sgd         = FX,
    coe_months_remaining    = 6,
    coe_original_value_sgd  = 100_000,
    arf_paid_sgd            = 180_000,
    year_registered         = 2022,
)

# ── Overpriced car: should NOT meet any threshold ────────────────────────────
# 991 GT3, SG asking S$380k. UK value only £85k. Both scenarios are negative.
GT3_OVERPRICED = dict(
    sg_price_sgd            = 380_000,
    uk_sale_price_gbp       = 85_000,
    fx_rate_gbp_sgd         = FX,
    coe_months_remaining    = 48,
    coe_original_value_sgd  = 100_000,
    arf_paid_sgd            = 160_000,
    year_registered         = 2021,
)

# ── Edge: no COE / PARF data at all ──────────────────────────────────────────
UNKNOWN_COE = dict(
    sg_price_sgd        = 150_000,
    uk_sale_price_gbp   = 60_000,
    fx_rate_gbp_sgd     = FX,
)
