"""
Tests for engine/profit.py — three-scenario profit calculation engine.

Scenario 1 — TOR:            no duty, no VAT
Scenario 2 — Full duty:      6.5% duty + 20% VAT on full SG invoice price
Scenario 3 — Effective duty: 6.5% duty + 20% VAT on net cost after COE/PARF

All monetary values in SGD unless variable name specifies otherwise.
FX fixture: 1 GBP = 1.72 SGD.
"""

import pytest
from engine.profit import calculate_profit, CostInputs
from tests.fixtures import (
    F430_NEAR_COE,
    GT3_NEAR_COE_OPPORTUNITY,
    GT3_OVERPRICED,
    SCUDERIA_FULL_DUTY_OPPORTUNITY,
    UNKNOWN_COE,
    FX,
)

ALL_FIXTURES = [F430_NEAR_COE, GT3_NEAR_COE_OPPORTUNITY, GT3_OVERPRICED,
                SCUDERIA_FULL_DUTY_OPPORTUNITY, UNKNOWN_COE]


# ─── SG recovery (COE + PARF) ────────────────────────────────────────────────

class TestSGRecovery:
    def test_f430_coe_refund_correct(self):
        r = calculate_profit(**F430_NEAR_COE)
        assert r.coe_refund_sgd == pytest.approx(1_500)

    def test_f430_no_parf_old_car(self):
        r = calculate_profit(**F430_NEAR_COE)
        assert r.parf_refund_sgd == 0.0

    def test_gt3_parf_75_pct_at_4_years(self):
        # 2022 reg, deregistered 2026 = 4 years → 75% × S$180k = S$135k
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.parf_refund_sgd == pytest.approx(135_000)

    def test_gt3_coe_refund(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.coe_refund_sgd == pytest.approx(5_000)

    def test_gt3_net_sg_cost(self):
        # S$250k − S$5k − S$135k = S$110k
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.net_sg_cost_sgd == pytest.approx(110_000)


# ─── UK import costs ─────────────────────────────────────────────────────────

class TestImportCosts:
    def test_base_import_cost_includes_all_components(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        expected = (
            r.shipping_sgd
            + r.uk_registration_sgd
            + r.insurance_sgd
            + r.road_tax_prorata_sgd
            + r.depreciation_sgd
            + r.pre_sale_service_sgd
        )
        assert r.base_import_cost_sgd == pytest.approx(expected)

    def test_depreciation_based_on_uk_sale_price(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        # 5% / year × 8 months on S$215k
        expected = round(215_000 * 0.05 * 8 / 12, 2)
        assert r.depreciation_sgd == pytest.approx(expected, abs=1)

    def test_pre_sale_service_default(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.pre_sale_service_sgd == 800

    def test_custom_hold_period_changes_road_tax_and_depreciation(self):
        r12 = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY, costs=CostInputs(hold_months=12))
        r4  = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY, costs=CostInputs(hold_months=4))
        assert r12.road_tax_prorata_sgd > r4.road_tax_prorata_sgd
        assert r12.depreciation_sgd    > r4.depreciation_sgd

    def test_custom_shipping_reflected(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY, costs=CostInputs(shipping_sgd=15_000))
        assert r.shipping_sgd == 15_000
        assert r.base_import_cost_sgd > 10_000 + 1_400 + 6_000

    def test_custom_depreciation_rate(self):
        r0 = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY, costs=CostInputs(depreciation_rate_annual=0.0))
        r10 = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY, costs=CostInputs(depreciation_rate_annual=0.10))
        assert r0.depreciation_sgd == 0.0
        assert r10.depreciation_sgd > r0.depreciation_sgd

    def test_custom_pre_sale_service(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY, costs=CostInputs(pre_sale_service_sgd=2_000))
        assert r.pre_sale_service_sgd == 2_000


# ─── UK sale price conversions ────────────────────────────────────────────────

class TestUKSalePrice:
    def test_mid_price_conversion(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.uk_sale_price_sgd == pytest.approx(215_000)

    def test_conservative_below_mid(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.uk_sale_conservative_sgd < r.uk_sale_mid_sgd

    def test_optimistic_above_mid(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.uk_sale_optimistic_sgd > r.uk_sale_mid_sgd

    def test_conservative_is_90_pct_of_mid(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.uk_sale_conservative_sgd == pytest.approx(r.uk_sale_mid_sgd * 0.90)

    def test_optimistic_is_103_pct_of_mid(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.uk_sale_optimistic_sgd == pytest.approx(r.uk_sale_mid_sgd * 1.03)


# ─── Scenario 1: TOR ─────────────────────────────────────────────────────────

class TestTORScenario:
    def test_tor_has_zero_duty(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.tor.import_duty_sgd == 0.0
        assert r.tor.vat_sgd         == 0.0
        assert r.tor.customs_value_gbp == 0.0

    def test_tor_total_cost_equals_net_sg_plus_import(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.tor.total_cost_sgd == pytest.approx(r.net_sg_cost_sgd + r.base_import_cost_sgd)

    def test_tor_profit_equals_sale_minus_cost(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.tor.profit_mid_sgd == pytest.approx(r.uk_sale_price_sgd - r.tor.total_cost_sgd)

    def test_tor_profit_gbp_conversion(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.tor.profit_gbp == pytest.approx(r.tor.profit_sgd / FX)

    def test_tor_meets_threshold_gt3(self):
        # TOR profit S$79k ≥ S$50k threshold
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.tor.meets_threshold is True


# ─── Scenario 2: Full duty ────────────────────────────────────────────────────

class TestFullDutyScenario:
    def test_full_duty_has_positive_duty_and_vat(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.full_duty.import_duty_sgd > 0
        assert r.full_duty.vat_sgd         > 0

    def test_full_duty_customs_is_invoice_price(self):
        # Customs value = full SG purchase price / FX rate
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        expected_gbp = round(GT3_NEAR_COE_OPPORTUNITY["sg_price_sgd"] / FX, 2)
        assert r.full_duty.customs_value_gbp == pytest.approx(expected_gbp, rel=1e-4)

    def test_duty_rate_is_6_5_pct_of_customs_value(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        expected_duty_sgd = r.full_duty.customs_value_gbp * 0.065 * FX
        assert r.full_duty.import_duty_sgd == pytest.approx(expected_duty_sgd, rel=1e-3)

    def test_vat_is_20_pct_of_customs_plus_duty(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        c_gbp = r.full_duty.customs_value_gbp
        d_gbp = c_gbp * 0.065
        expected_vat_sgd = (c_gbp + d_gbp) * 0.20 * FX
        assert r.full_duty.vat_sgd == pytest.approx(expected_vat_sgd, rel=1e-3)

    def test_full_duty_always_less_profitable_than_tor(self):
        for f in ALL_FIXTURES:
            r = calculate_profit(**f)
            assert r.tor.profit_mid_sgd >= r.full_duty.profit_mid_sgd

    def test_conservative_below_optimistic(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.full_duty.profit_conservative_sgd < r.full_duty.profit_optimistic_sgd


# ─── Scenario 3: Effective duty ───────────────────────────────────────────────

class TestEffectiveDutyScenario:
    def test_effective_duty_customs_is_net_sg_cost(self):
        # Customs value = net cost after rebates / FX rate
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        expected_gbp = round(r.net_sg_cost_sgd / FX, 2)
        assert r.effective_duty.customs_value_gbp == pytest.approx(expected_gbp, rel=1e-4)

    def test_effective_duty_has_positive_duty_and_vat(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.effective_duty.import_duty_sgd > 0
        assert r.effective_duty.vat_sgd         > 0

    def test_effective_duty_rate_is_6_5_pct_of_net_customs(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        expected_duty_sgd = r.effective_duty.customs_value_gbp * 0.065 * FX
        assert r.effective_duty.import_duty_sgd == pytest.approx(expected_duty_sgd, rel=1e-3)

    def test_effective_duty_between_tor_and_full_duty(self):
        # TOR ≥ effective_duty ≥ full_duty (since net_cost ≤ invoice_price)
        for f in ALL_FIXTURES:
            r = calculate_profit(**f)
            assert r.tor.profit_mid_sgd          >= r.effective_duty.profit_mid_sgd
            assert r.effective_duty.profit_mid_sgd >= r.full_duty.profit_mid_sgd

    def test_effective_equals_full_when_no_rebates(self):
        # When COE=0 and PARF=0, net_cost = sg_price → both duty scenarios identical
        r = calculate_profit(**UNKNOWN_COE)
        assert r.effective_duty.import_duty_sgd == pytest.approx(r.full_duty.import_duty_sgd, rel=1e-4)
        assert r.effective_duty.vat_sgd         == pytest.approx(r.full_duty.vat_sgd,         rel=1e-4)

    def test_effective_cheaper_than_full_when_rebates_exist(self):
        # GT3 has large PARF + COE rebates → effective duty < full duty
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.effective_duty.import_duty_sgd < r.full_duty.import_duty_sgd
        assert r.effective_duty.vat_sgd         < r.full_duty.vat_sgd

    def test_effective_duty_profit_gbp_conversion(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.effective_duty.profit_gbp == pytest.approx(r.effective_duty.profit_sgd / FX)

    def test_effective_duty_meets_threshold_gt3(self):
        # GT3 effective duty profit S$48.5k < S$50k → False
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.effective_duty.meets_threshold is False

    def test_effective_duty_meets_threshold_scuderia(self):
        # Scuderia: tiny rebates → effective ≈ full duty, both meet threshold
        r = calculate_profit(**SCUDERIA_FULL_DUTY_OPPORTUNITY)
        assert r.effective_duty.meets_threshold is True


# ─── Opportunity detection ────────────────────────────────────────────────────

class TestOpportunityThreshold:
    def test_gt3_meets_tor_threshold_not_full_duty(self):
        """
        GT3: PARF rebates reduce net cost but HMRC duty is on the full S$250k invoice.
        TOR profit ~S$79k → GREEN. Full duty profit ~S$9.5k → below threshold.
        """
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.tor.meets_threshold is True,       "GT3 TOR should meet S$50k threshold"
        assert r.full_duty.meets_threshold is False, "GT3 full-duty should NOT meet threshold"
        assert r.min_threshold_met is False

    def test_scuderia_meets_full_duty_threshold(self):
        """
        Old Scuderia: low SG price (S$35k) keeps customs value tiny.
        Car commands £78k in UK. Full-duty profit ~S$68k → meets threshold.
        """
        r = calculate_profit(**SCUDERIA_FULL_DUTY_OPPORTUNITY)
        assert r.min_threshold_met is True
        assert r.full_duty.meets_threshold is True
        assert r.full_duty.profit_mid_sgd >= 50_000

    def test_overpriced_car_misses_all_thresholds(self):
        r = calculate_profit(**GT3_OVERPRICED)
        assert r.tor.meets_threshold           is False
        assert r.full_duty.meets_threshold     is False
        assert r.effective_duty.meets_threshold is False
        assert r.min_threshold_met is False

    def test_threshold_flag_matches_profit(self):
        for f in [GT3_NEAR_COE_OPPORTUNITY, SCUDERIA_FULL_DUTY_OPPORTUNITY]:
            r = calculate_profit(**f)
            for scenario in (r.tor, r.full_duty, r.effective_duty):
                meets = scenario.profit_mid_sgd >= 50_000
                assert scenario.meets_threshold == meets

    def test_min_threshold_met_mirrors_full_duty(self):
        for f in ALL_FIXTURES:
            r = calculate_profit(**f)
            assert r.min_threshold_met == r.full_duty.meets_threshold


# ─── Opportunity rating ───────────────────────────────────────────────────────

class TestOpportunityRating:
    def test_gt3_is_green(self):
        # TOR profit S$79k ≥ S$50k
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert r.opportunity_rating == "green"

    def test_scuderia_is_green(self):
        # TOR profit S$77k ≥ S$50k
        r = calculate_profit(**SCUDERIA_FULL_DUTY_OPPORTUNITY)
        assert r.opportunity_rating == "green"

    def test_overpriced_is_red(self):
        # TOR profit negative
        r = calculate_profit(**GT3_OVERPRICED)
        assert r.opportunity_rating == "red"

    def test_amber_tor_scenario(self):
        # TOR profit 20k-50k, full-duty profit < 20k → amber_tor
        # SG=60k (no rebates), UK=£65k=S$111.8k, no depreciation/pre-sale/road-tax
        # base import = 10k+1.4k+6k = S$17.4k
        # TOR cost = S$77.4k, TOR profit = S$34.4k  ✓ (20-50k)
        # Full duty: customs=60k/1.72=£34.9k, duty≈S$3.9k, vat≈S$12.8k
        # fd cost ≈ S$94.1k, fd profit ≈ S$17.7k  ✓ (<20k → amber_tor)
        r = calculate_profit(
            sg_price_sgd=60_000, uk_sale_price_gbp=65_000, fx_rate_gbp_sgd=FX,
            costs=CostInputs(
                depreciation_rate_annual=0.0,
                pre_sale_service_sgd=0,
                uk_road_tax_annual_sgd=0,
            ),
        )
        assert r.tor.profit_mid_sgd >= 20_000
        assert r.tor.profit_mid_sgd < 50_000
        assert r.full_duty.profit_mid_sgd < 20_000
        assert r.opportunity_rating == "amber_tor"

    def test_amber_marginal_scenario(self):
        # Full-duty profit ≥ 20k but TOR < 50k → amber_marginal
        # Old cheap car, no rebates. Low customs value → low duty.
        # SG=20k (old car, no rebates), UK=£40k=S$68.8k
        # No depreciation/pre-sale to keep math simple
        # base cost: net_sg=20k + import(10+1.4+6+0+0+0)=17.4k = 37.4k
        # TOR profit: 68.8k - 37.4k = 31.4k (amber_tor range)
        # Full duty: customs=20k/1.72=£11.6k, duty=£754=S$1297, vat=£2471=S$4250
        # fd cost = 37.4k+5.5k=42.9k, fd profit=68.8k-42.9k=25.9k (≥20k → amber_marginal) ✓
        r = calculate_profit(
            sg_price_sgd=20_000, uk_sale_price_gbp=40_000, fx_rate_gbp_sgd=FX,
            costs=CostInputs(depreciation_rate_annual=0.0, pre_sale_service_sgd=0,
                             uk_road_tax_annual_sgd=0),
        )
        assert r.tor.profit_mid_sgd >= 20_000
        assert r.tor.profit_mid_sgd < 50_000
        assert r.full_duty.profit_mid_sgd >= 20_000
        assert r.opportunity_rating == "amber_marginal"

    def test_red_when_tor_below_zero(self):
        r = calculate_profit(**GT3_OVERPRICED)
        assert r.tor.profit_mid_sgd < 0
        assert r.opportunity_rating == "red"

    def test_red_when_tor_positive_but_small(self):
        # TOR profit 0-20k, full-duty small → red
        r = calculate_profit(**F430_NEAR_COE)
        assert 0 < r.tor.profit_mid_sgd < 20_000
        assert r.opportunity_rating == "red"


# ─── Unknown / missing data ────────────────────────────────────────────────────

class TestUnknownInputs:
    def test_no_coe_data_gives_zero_refund(self):
        r = calculate_profit(**UNKNOWN_COE)
        assert r.coe_refund_sgd == 0.0

    def test_no_parf_data_gives_zero_rebate(self):
        r = calculate_profit(**UNKNOWN_COE)
        assert r.parf_refund_sgd == 0.0

    def test_net_sg_equals_price_when_no_rebates(self):
        r = calculate_profit(**UNKNOWN_COE)
        assert r.net_sg_cost_sgd == pytest.approx(UNKNOWN_COE["sg_price_sgd"])

    def test_assumptions_flags_unknown_values(self):
        r = calculate_profit(**UNKNOWN_COE)
        assert "parf_rebate" in r.assumptions

    def test_still_calculates_all_three_scenarios_without_sg_data(self):
        r = calculate_profit(**UNKNOWN_COE)
        assert isinstance(r.tor.profit_mid_sgd,           float)
        assert isinstance(r.full_duty.profit_mid_sgd,     float)
        assert isinstance(r.effective_duty.profit_mid_sgd, float)


# ─── DB serialisation ─────────────────────────────────────────────────────────

class TestDBDict:
    def test_to_db_dict_has_required_columns(self):
        r   = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        row = r.to_db_dict(sg_listing_id=1)
        required = [
            # inputs
            "sg_listing_id", "fx_rate_gbp_sgd", "purchase_price_sgd",
            "coe_refund_sgd", "parf_refund_sgd", "net_sg_cost_sgd",
            # import costs
            "shipping_sgd", "uk_registration_sgd", "insurance_sgd",
            "road_tax_prorata_sgd", "depreciation_sgd", "pre_sale_service_sgd",
            "base_import_cost_sgd",
            # UK sale
            "uk_sale_price_gbp", "uk_sale_price_sgd",
            # TOR
            "total_cost_tor_sgd", "profit_tor_mid_sgd", "meets_threshold_tor",
            # Full duty
            "customs_value_gbp", "import_duty_sgd", "vat_sgd",
            "total_cost_full_duty_sgd", "profit_full_duty_mid_sgd",
            "meets_threshold_full_duty",
            # Effective duty
            "customs_value_effective_gbp", "import_duty_effective_sgd", "vat_effective_sgd",
            "total_cost_effective_duty_sgd", "profit_effective_duty_mid_sgd",
            "meets_threshold_effective_duty",
            # Rating
            "opportunity_rating",
        ]
        for col in required:
            assert col in row, f"Missing column: {col}"

    def test_threshold_flags_are_0_or_1(self):
        r   = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        row = r.to_db_dict(sg_listing_id=1)
        for col in ("meets_threshold_tor", "meets_threshold_full_duty",
                    "meets_threshold_effective_duty"):
            assert row[col] in (0, 1), f"{col} should be 0 or 1"

    def test_opportunity_rating_in_db_dict(self):
        r   = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        row = r.to_db_dict(sg_listing_id=1)
        assert row["opportunity_rating"] in ("green", "amber_tor", "amber_marginal", "red")

    def test_uk_listing_id_is_none_when_not_provided(self):
        r   = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        row = r.to_db_dict(sg_listing_id=1)
        assert row["uk_listing_id"] is None


# ─── summary() output ────────────────────────────────────────────────────────

class TestSummary:
    def test_summary_contains_all_three_scenarios(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        s = r.summary()
        assert "TOR"       in s
        assert "Eff. duty" in s
        assert "Full duty" in s

    def test_summary_green_for_strong_deal(self):
        r = calculate_profit(**SCUDERIA_FULL_DUTY_OPPORTUNITY)
        assert "GREEN" in r.summary()

    def test_summary_red_for_bad_deal(self):
        r = calculate_profit(**GT3_OVERPRICED)
        assert "RED" in r.summary()

    def test_summary_gt3_is_green(self):
        r = calculate_profit(**GT3_NEAR_COE_OPPORTUNITY)
        assert "GREEN" in r.summary()
