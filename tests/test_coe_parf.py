"""Tests for engine/coe_parf.py — COE refund and PARF rebate logic."""

import pytest
from engine.coe_parf import coe_refund, parf_rebate, parf_rate, estimate_arf


# ─── COE refund ──────────────────────────────────────────────────────────────

class TestCOERefund:
    def test_pro_rata_3_months(self):
        # 3 / 120 × S$60,000 = S$1,500
        assert coe_refund(3, 60_000) == pytest.approx(1_500)

    def test_pro_rata_12_months(self):
        # 12 / 120 × S$90,000 = S$9,000
        assert coe_refund(12, 90_000) == pytest.approx(9_000)

    def test_full_coe_remaining(self):
        # Brand-new COE → full value returned
        assert coe_refund(120, 80_000) == pytest.approx(80_000)

    def test_zero_months_returns_zero(self):
        assert coe_refund(0, 80_000) == 0.0

    def test_negative_months_returns_zero(self):
        assert coe_refund(-5, 80_000) == 0.0

    def test_zero_value_returns_zero(self):
        assert coe_refund(24, 0) == 0.0

    def test_clamps_above_120(self):
        # > 120 months is impossible but should not blow up
        assert coe_refund(150, 60_000) == pytest.approx(60_000)

    def test_6_months_100k_coe(self):
        # 6 / 120 × S$100,000 = S$5,000
        assert coe_refund(6, 100_000) == pytest.approx(5_000)


# ─── PARF rebate ─────────────────────────────────────────────────────────────

class TestPARFRebate:
    def test_age_0_to_5_is_75_pct(self):
        for yr_reg in range(2020, 2026):  # ages 0–5 in 2025
            result = parf_rebate(100_000, year_registered=yr_reg, deregister_year=2025)
            assert result == pytest.approx(75_000), f"Failed for yr_reg={yr_reg}"

    def test_age_6(self):
        result = parf_rebate(100_000, year_registered=2019, deregister_year=2025)
        assert result == pytest.approx(70_000)

    def test_age_7(self):
        result = parf_rebate(100_000, year_registered=2018, deregister_year=2025)
        assert result == pytest.approx(65_000)

    def test_age_8(self):
        result = parf_rebate(100_000, year_registered=2017, deregister_year=2025)
        assert result == pytest.approx(60_000)

    def test_age_9(self):
        result = parf_rebate(100_000, year_registered=2016, deregister_year=2025)
        assert result == pytest.approx(55_000)

    def test_age_10(self):
        result = parf_rebate(100_000, year_registered=2015, deregister_year=2025)
        assert result == pytest.approx(50_000)

    def test_age_11_no_parf(self):
        result = parf_rebate(100_000, year_registered=2014, deregister_year=2025)
        assert result == 0.0

    def test_age_20_no_parf(self):
        result = parf_rebate(200_000, year_registered=2005, deregister_year=2025)
        assert result == 0.0

    def test_zero_arf_returns_zero(self):
        assert parf_rebate(0, year_registered=2020, deregister_year=2025) == 0.0


class TestPARFRate:
    def test_young_car(self):
        assert parf_rate(year_registered=2023, deregister_year=2026) == pytest.approx(0.75)

    def test_old_car(self):
        assert parf_rate(year_registered=2010, deregister_year=2026) == pytest.approx(0.0)


# ─── ARF estimation ───────────────────────────────────────────────────────────

class TestEstimateARF:
    def test_below_20k_omv(self):
        # 100% of S$15,000
        assert estimate_arf(15_000) == pytest.approx(15_000)

    def test_exactly_20k_omv(self):
        # 100% × S$20,000
        assert estimate_arf(20_000) == pytest.approx(20_000)

    def test_50k_omv(self):
        # 100% × 20k + 140% × 30k = 20,000 + 42,000 = 62,000
        assert estimate_arf(50_000) == pytest.approx(62_000)

    def test_100k_omv(self):
        # 100% × 20k + 140% × 30k + 190% × 50k = 20k + 42k + 95k = 157,000
        assert estimate_arf(100_000) == pytest.approx(157_000)

    def test_150k_omv(self):
        # 20k + 42k + 95k + 250% × 50k = 157k + 125k = 282,000
        assert estimate_arf(150_000) == pytest.approx(282_000)

    def test_zero_omv(self):
        assert estimate_arf(0) == 0.0
