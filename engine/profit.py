"""
Core profit calculation engine for SG→UK car arbitrage.

Three scenarios computed side-by-side
──────────────────────────────────────
Scenario 1 — TOR (Transfer of Residence)
    Importer has lived abroad ≥ 12 months and is returning to the UK.
    One vehicle may be imported duty/VAT-free.
    Import duty = 0, VAT = 0.

Scenario 2 — Full duty  (worst case)
    HMRC levies duty on the full original SG purchase price converted to GBP.
    This is conservative: the invoice price includes SG taxes (COE/ARF) that
    inflate the customs value vs the car's intrinsic market value.
    Duty = 6.5% of invoice GBP value
    VAT  = 20% of (invoice GBP + duty)

Scenario 3 — Effective duty  (practical middle ground)
    Duty calculated on net cash cost after PARF and COE rebates, i.e. what
    you actually paid out of pocket for the vehicle.  Some importers argue
    this is the defensible customs valuation since the SG government returned
    the COE/ARF component.
    Duty = 6.5% of (net_sg_cost / fx)
    VAT  = 20% of (net customs value + duty)

Cost formula (shared base)
──────────────────────────
    net_sg_cost = purchase_price − COE_refund − PARF_rebate
    base_cost   = net_sg_cost
                + shipping (S$10k default)
                + UK DVLA/NOVA/MOT (S$1,400 default)
                + insurance (S$6k default)
                + road_tax pro-rata (hold period)
                + depreciation during hold (5%/yr of UK sale price)
                + pre-sale service estimate (S$800 default)

    profit = UK_sale_price_SGD − total_landed_cost

Opportunity ratings
───────────────────
    Green          TOR profit ≥ S$50k — viable even without TOR eligibility
    Amber (TOR)    TOR profit S$20–50k, full-duty profit < S$20k — confirm TOR first
    Amber (marginal) Full-duty profit ≥ S$20k — workable without TOR
    Red            TOR profit < 0 or < S$20k with poor full-duty — pass

Usage
──────
    from engine.profit import calculate_profit, CostInputs

    result = calculate_profit(
        sg_price_sgd=250_000,
        uk_sale_price_gbp=125_000,
        fx_rate_gbp_sgd=1.72,
        coe_months_remaining=6,
        coe_original_value_sgd=100_000,
        arf_paid_sgd=180_000,
        year_registered=2022,
    )
    print(result.summary())
    db_row = result.to_db_dict(sg_listing_id=42)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import config
from engine.coe_parf import coe_refund, parf_rebate


# ── Input overrides ───────────────────────────────────────────────────────────

@dataclass
class CostInputs:
    """
    Cost parameters with project defaults.  Override any value to model
    non-standard scenarios (e.g. cheaper shipping, higher insurance tier).
    """
    shipping_sgd:               float = config.SHIPPING_SGD
    uk_registration_sgd:        float = config.UK_REGISTRATION_SGD
    insurance_sgd:              float = config.INSURANCE_SGD_DEFAULT
    uk_road_tax_annual_sgd:     float = config.UK_ROAD_TAX_ANNUAL_SGD
    pre_sale_service_sgd:       float = config.PRE_SALE_SERVICE_SGD
    hold_months:                int   = config.DEFAULT_HOLD_MONTHS
    depreciation_rate_annual:   float = config.DEPRECIATION_RATE_ANNUAL
    import_duty_rate:           float = config.IMPORT_DUTY_RATE
    vat_rate:                   float = config.VAT_RATE


# ── Per-scenario result ───────────────────────────────────────────────────────

@dataclass
class Scenario:
    """Profit figures for one duty scenario."""
    total_cost_sgd:             float
    profit_sgd:                 float       # alias for profit_mid_sgd
    profit_gbp:                 float
    profit_conservative_sgd:    float
    profit_mid_sgd:             float
    profit_optimistic_sgd:      float
    meets_threshold:            bool        # profit_mid ≥ MIN_PROFIT_SGD (S$50k)
    # Duty components (both 0.0 for TOR)
    customs_value_gbp:          float = 0.0
    import_duty_sgd:            float = 0.0
    vat_sgd:                    float = 0.0


# ── Full result ───────────────────────────────────────────────────────────────

@dataclass
class ProfitResult:
    """Complete three-scenario arbitrage analysis for one SG listing."""

    # ── Inputs ────────────────────────────────────────────────────────────────
    sg_price_sgd:               float
    uk_sale_price_gbp:          float
    fx_rate_gbp_sgd:            float

    # ── SG recovery ───────────────────────────────────────────────────────────
    coe_refund_sgd:             float
    parf_refund_sgd:            float
    net_sg_cost_sgd:            float       # sg_price − coe_refund − parf_refund

    # ── UK import costs (shared across all three scenarios) ───────────────────
    shipping_sgd:               float
    uk_registration_sgd:        float
    insurance_sgd:              float
    road_tax_prorata_sgd:       float
    depreciation_sgd:           float       # UK sale price × rate × hold_months/12
    pre_sale_service_sgd:       float
    base_import_cost_sgd:       float       # sum of the six lines above

    # ── UK sale price (SGD equivalents) ───────────────────────────────────────
    uk_sale_price_sgd:          float       # = uk_sale_mid_sgd
    uk_sale_conservative_sgd:   float
    uk_sale_mid_sgd:            float
    uk_sale_optimistic_sgd:     float

    # ── Three scenarios ───────────────────────────────────────────────────────
    tor:            Scenario    # Scenario 1 — no duty
    full_duty:      Scenario    # Scenario 2 — duty on full SG invoice price
    effective_duty: Scenario    # Scenario 3 — duty on net cost after rebates

    # ── Meta ──────────────────────────────────────────────────────────────────
    hold_months:            int
    min_threshold_met:      bool    # True when full_duty.meets_threshold (most conservative)
    assumptions:            dict = field(default_factory=dict)

    # ── Opportunity rating ────────────────────────────────────────────────────

    @property
    def opportunity_rating(self) -> str:
        """
        Rating based on mid-price profit figures.

        green          — TOR profit ≥ S$50k (strong deal)
        amber_marginal — full-duty profit ≥ S$20k (viable without TOR)
        amber_tor      — TOR profit S$20-50k but full-duty profit < S$20k
        red            — TOR profit < 0, or < S$20k with poor full-duty
        """
        tor = self.tor.profit_mid_sgd
        fd  = self.full_duty.profit_mid_sgd

        if tor < 0:
            return "red"
        elif tor >= 50_000:
            return "green"
        elif fd >= 20_000:
            # full-duty viable → amber_marginal (TOR also ≥ fd ≥ 20k)
            return "amber_marginal"
        elif tor >= 20_000:
            # TOR-only opportunity; full-duty profit < S$20k
            return "amber_tor"
        else:
            return "red"

    # ── Convenience ───────────────────────────────────────────────────────────

    def summary(self) -> str:
        """One-line human-readable summary showing all three scenarios."""
        rating_labels = {
            "green":          "GREEN ✓",
            "amber_tor":      "AMBER (TOR-only)",
            "amber_marginal": "AMBER (marginal)",
            "red":            "RED — pass",
        }
        return (
            f"TOR: S${self.tor.profit_mid_sgd:>10,.0f}  |  "
            f"Eff. duty: S${self.effective_duty.profit_mid_sgd:>+10,.0f}  |  "
            f"Full duty: S${self.full_duty.profit_mid_sgd:>+10,.0f}  |  "
            f"{rating_labels[self.opportunity_rating]}"
        )

    def to_db_dict(self, sg_listing_id: int, uk_listing_id: int | None = None) -> dict:
        """Return a flat dict matching the profit_calculations schema columns."""
        t  = self.tor
        fd = self.full_duty
        ed = self.effective_duty
        return {
            "sg_listing_id":                        sg_listing_id,
            "uk_listing_id":                        uk_listing_id,
            "fx_rate_gbp_sgd":                      self.fx_rate_gbp_sgd,
            "hold_period_months":                   self.hold_months,
            "assumptions_json":                     json.dumps(self.assumptions),
            # SG side
            "purchase_price_sgd":                   self.sg_price_sgd,
            "coe_refund_sgd":                       self.coe_refund_sgd,
            "parf_refund_sgd":                      self.parf_refund_sgd,
            "net_sg_cost_sgd":                      self.net_sg_cost_sgd,
            # UK import costs
            "shipping_sgd":                         self.shipping_sgd,
            "uk_registration_sgd":                  self.uk_registration_sgd,
            "insurance_sgd":                        self.insurance_sgd,
            "road_tax_prorata_sgd":                 self.road_tax_prorata_sgd,
            "depreciation_sgd":                     self.depreciation_sgd,
            "pre_sale_service_sgd":                 self.pre_sale_service_sgd,
            "base_import_cost_sgd":                 self.base_import_cost_sgd,
            # UK sale prices
            "uk_sale_price_gbp":                    self.uk_sale_price_gbp,
            "uk_sale_price_sgd":                    self.uk_sale_price_sgd,
            # Scenario 1: TOR
            "total_cost_tor_sgd":                   t.total_cost_sgd,
            "profit_tor_sgd":                       t.profit_sgd,
            "profit_tor_gbp":                       t.profit_gbp,
            "profit_tor_conservative_sgd":          t.profit_conservative_sgd,
            "profit_tor_mid_sgd":                   t.profit_mid_sgd,
            "profit_tor_optimistic_sgd":            t.profit_optimistic_sgd,
            "meets_threshold_tor":                  int(t.meets_threshold),
            # Scenario 2: Full duty
            "customs_value_gbp":                    fd.customs_value_gbp,
            "import_duty_sgd":                      fd.import_duty_sgd,
            "vat_sgd":                              fd.vat_sgd,
            "total_cost_full_duty_sgd":             fd.total_cost_sgd,
            "profit_full_duty_sgd":                 fd.profit_sgd,
            "profit_full_duty_gbp":                 fd.profit_gbp,
            "profit_full_duty_conservative_sgd":    fd.profit_conservative_sgd,
            "profit_full_duty_mid_sgd":             fd.profit_mid_sgd,
            "profit_full_duty_optimistic_sgd":      fd.profit_optimistic_sgd,
            "meets_threshold_full_duty":            int(fd.meets_threshold),
            # Scenario 3: Effective duty
            "customs_value_effective_gbp":              ed.customs_value_gbp,
            "import_duty_effective_sgd":                ed.import_duty_sgd,
            "vat_effective_sgd":                        ed.vat_sgd,
            "total_cost_effective_duty_sgd":            ed.total_cost_sgd,
            "profit_effective_duty_sgd":                ed.profit_sgd,
            "profit_effective_duty_gbp":                ed.profit_gbp,
            "profit_effective_duty_conservative_sgd":   ed.profit_conservative_sgd,
            "profit_effective_duty_mid_sgd":            ed.profit_mid_sgd,
            "profit_effective_duty_optimistic_sgd":     ed.profit_optimistic_sgd,
            "meets_threshold_effective_duty":           int(ed.meets_threshold),
            # Rating
            "opportunity_rating":                   self.opportunity_rating,
        }


# ── Main entry point ──────────────────────────────────────────────────────────

def calculate_profit(
    sg_price_sgd: float,
    uk_sale_price_gbp: float,
    fx_rate_gbp_sgd: float,
    *,
    coe_months_remaining: int = 0,
    coe_original_value_sgd: float = 0.0,
    arf_paid_sgd: float = 0.0,
    year_registered: int | None = None,
    costs: CostInputs | None = None,
) -> ProfitResult:
    """
    Calculate arbitrage profit for a SG vehicle to be exported to the UK.

    Args:
        sg_price_sgd:           SG asking price (SGD).
        uk_sale_price_gbp:      Expected UK sale price (GBP) — use comparable listings.
                                Becomes the "mid" scenario; conservative/optimistic derived.
        fx_rate_gbp_sgd:        Current GBP→SGD rate (e.g. 1.72 means £1 = S$1.72).
        coe_months_remaining:   Months left on COE. 0 = expired / unknown → refund = 0.
        coe_original_value_sgd: COE paid at registration (SGD). 0 = unknown → refund = 0.
        arf_paid_sgd:           ARF paid at registration (SGD). 0 = unknown → PARF = 0.
        year_registered:        Year of first SG registration (for PARF age tier).
        costs:                  Override default cost assumptions.

    Returns:
        ProfitResult with full breakdown and all three duty scenarios.
    """
    if costs is None:
        costs = CostInputs()

    assumptions: dict[str, str] = {}

    # ── SG recovery ──────────────────────────────────────────────────────────
    coe_ref = coe_refund(coe_months_remaining, coe_original_value_sgd)
    if coe_original_value_sgd <= 0 and coe_months_remaining > 0:
        assumptions["coe_refund"] = "zero (original COE value unknown)"

    parf_ref = 0.0
    if year_registered is not None:
        parf_ref = parf_rebate(arf_paid_sgd, year_registered)
        if arf_paid_sgd <= 0:
            assumptions["parf_rebate"] = "zero (ARF value unknown)"
    else:
        assumptions["parf_rebate"] = "zero (registration year unknown)"

    net_sg = sg_price_sgd - coe_ref - parf_ref

    # ── UK sale price band (SGD) ──────────────────────────────────────────────
    uk_mid_sgd          = round(uk_sale_price_gbp * fx_rate_gbp_sgd, 2)
    uk_conservative_sgd = round(uk_mid_sgd * config.UK_SALE_MULTIPLIERS["conservative"], 2)
    uk_optimistic_sgd   = round(uk_mid_sgd * config.UK_SALE_MULTIPLIERS["optimistic"],   2)

    # ── UK import base costs (shared across all three scenarios) ─────────────
    road_tax_pro     = round(costs.uk_road_tax_annual_sgd * costs.hold_months / 12, 2)
    depreciation_sgd = round(uk_mid_sgd * costs.depreciation_rate_annual * costs.hold_months / 12, 2)
    base_import      = round(
        costs.shipping_sgd
        + costs.uk_registration_sgd
        + costs.insurance_sgd
        + road_tax_pro
        + depreciation_sgd
        + costs.pre_sale_service_sgd,
        2,
    )
    base_total = round(net_sg + base_import, 2)

    # ── Scenario 1: TOR (no duty) ─────────────────────────────────────────────
    tor = _build_scenario(
        total_cost       = base_total,
        uk_conservative  = uk_conservative_sgd,
        uk_mid           = uk_mid_sgd,
        uk_optimistic    = uk_optimistic_sgd,
        fx               = fx_rate_gbp_sgd,
        customs_gbp      = 0.0,
        duty_sgd         = 0.0,
        vat_sgd          = 0.0,
    )

    # ── Scenario 2: Full duty (HMRC on full SG invoice price) ────────────────
    inv_customs_gbp = round(sg_price_sgd / fx_rate_gbp_sgd, 2)
    inv_duty_gbp    = round(inv_customs_gbp * costs.import_duty_rate, 2)
    inv_vat_gbp     = round((inv_customs_gbp + inv_duty_gbp) * costs.vat_rate, 2)
    inv_duty_sgd    = round(inv_duty_gbp * fx_rate_gbp_sgd, 2)
    inv_vat_sgd     = round(inv_vat_gbp  * fx_rate_gbp_sgd, 2)
    fd_total        = round(base_total + inv_duty_sgd + inv_vat_sgd, 2)

    full_duty = _build_scenario(
        total_cost       = fd_total,
        uk_conservative  = uk_conservative_sgd,
        uk_mid           = uk_mid_sgd,
        uk_optimistic    = uk_optimistic_sgd,
        fx               = fx_rate_gbp_sgd,
        customs_gbp      = inv_customs_gbp,
        duty_sgd         = inv_duty_sgd,
        vat_sgd          = inv_vat_sgd,
    )

    # ── Scenario 3: Effective duty (duty on net cost after rebates) ───────────
    eff_customs_gbp = round(net_sg / fx_rate_gbp_sgd, 2)
    eff_duty_gbp    = round(eff_customs_gbp * costs.import_duty_rate, 2)
    eff_vat_gbp     = round((eff_customs_gbp + eff_duty_gbp) * costs.vat_rate, 2)
    eff_duty_sgd    = round(eff_duty_gbp * fx_rate_gbp_sgd, 2)
    eff_vat_sgd     = round(eff_vat_gbp  * fx_rate_gbp_sgd, 2)
    eff_total       = round(base_total + eff_duty_sgd + eff_vat_sgd, 2)

    effective_duty = _build_scenario(
        total_cost       = eff_total,
        uk_conservative  = uk_conservative_sgd,
        uk_mid           = uk_mid_sgd,
        uk_optimistic    = uk_optimistic_sgd,
        fx               = fx_rate_gbp_sgd,
        customs_gbp      = eff_customs_gbp,
        duty_sgd         = eff_duty_sgd,
        vat_sgd          = eff_vat_sgd,
    )

    return ProfitResult(
        sg_price_sgd            = sg_price_sgd,
        uk_sale_price_gbp       = uk_sale_price_gbp,
        fx_rate_gbp_sgd         = fx_rate_gbp_sgd,
        coe_refund_sgd          = coe_ref,
        parf_refund_sgd         = parf_ref,
        net_sg_cost_sgd         = round(net_sg, 2),
        shipping_sgd            = costs.shipping_sgd,
        uk_registration_sgd     = costs.uk_registration_sgd,
        insurance_sgd           = costs.insurance_sgd,
        road_tax_prorata_sgd    = road_tax_pro,
        depreciation_sgd        = depreciation_sgd,
        pre_sale_service_sgd    = costs.pre_sale_service_sgd,
        base_import_cost_sgd    = base_import,
        uk_sale_price_sgd       = uk_mid_sgd,
        uk_sale_conservative_sgd= uk_conservative_sgd,
        uk_sale_mid_sgd         = uk_mid_sgd,
        uk_sale_optimistic_sgd  = uk_optimistic_sgd,
        tor                     = tor,
        full_duty               = full_duty,
        effective_duty          = effective_duty,
        hold_months             = costs.hold_months,
        min_threshold_met       = full_duty.meets_threshold,
        assumptions             = assumptions,
    )


# ── Internal helper ───────────────────────────────────────────────────────────

def _build_scenario(
    total_cost:     float,
    uk_conservative: float,
    uk_mid:         float,
    uk_optimistic:  float,
    fx:             float,
    customs_gbp:    float,
    duty_sgd:       float,
    vat_sgd:        float,
) -> Scenario:
    profit_mid = round(uk_mid - total_cost, 2)
    return Scenario(
        total_cost_sgd          = total_cost,
        profit_sgd              = profit_mid,
        profit_gbp              = round(profit_mid / fx, 2),
        profit_conservative_sgd = round(uk_conservative - total_cost, 2),
        profit_mid_sgd          = profit_mid,
        profit_optimistic_sgd   = round(uk_optimistic - total_cost, 2),
        meets_threshold         = profit_mid >= config.MIN_PROFIT_SGD,
        customs_value_gbp       = customs_gbp,
        import_duty_sgd         = duty_sgd,
        vat_sgd                 = vat_sgd,
    )
