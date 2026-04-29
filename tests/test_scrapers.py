"""
Tests for Phase 2/3 scraper infrastructure:
  - UK parser utilities (mileage, GBP price, pence conversion)
  - Currency detection and conversion
  - JamesEdition auction price parsing + multi-currency conversion
  - Scraper class wiring (imports, SOURCE/MARKET, search URL construction)
"""
import pytest
from scrapers.parsers import (
    parse_mileage_miles,
    miles_to_km,
    parse_price_gbp,
    parse_price_pence,
    is_target_model,
    infer_seller_type,
    parse_coe_expiry_iso,
    coe_months_remaining,
)
from scrapers.currency import detect_currency, price_to_gbp, _FALLBACK_RATES
from scrapers.uk_targets import search_pairs, CARANDCLASSIC_MAKE, JAMESEDITION_MAKE
from scrapers.jamesedition import _parse_estimate_range, _json_ld_to_item
import config


# ─── UK mileage ───────────────────────────────────────────────────────────────

class TestMileageParsing:
    def test_miles_with_text(self):
        assert parse_mileage_miles("25,432 miles") == 25432

    def test_miles_no_text(self):
        assert parse_mileage_miles("25432") == 25432

    def test_miles_with_comma(self):
        assert parse_mileage_miles("1,432 miles") == 1432

    def test_miles_empty(self):
        assert parse_mileage_miles("") is None

    def test_miles_zero(self):
        assert parse_mileage_miles("0") is None

    def test_miles_to_km_conversion(self):
        # 1 mile = 1.60934 km → 10000 miles ≈ 16093 km
        assert miles_to_km(10_000) == pytest.approx(16093, abs=2)

    def test_miles_to_km_none(self):
        assert miles_to_km(None) is None

    def test_miles_to_km_zero(self):
        assert miles_to_km(0) == 0


# ─── GBP price parsing ────────────────────────────────────────────────────────

class TestGBPPriceParsing:
    def test_pound_sign(self):
        assert parse_price_gbp("£45,000") == pytest.approx(45000)

    def test_no_symbol(self):
        assert parse_price_gbp("45,000") == pytest.approx(45000)

    def test_pence_conversion(self):
        assert parse_price_pence(4_500_000) == pytest.approx(45000)

    def test_pence_zero(self):
        assert parse_price_pence(0) is None

    def test_pence_string(self):
        assert parse_price_pence("4500000") == pytest.approx(45000)

    def test_pence_midpoint_large(self):
        # £125k car → 12500000 pence
        assert parse_price_pence(12_500_000) == pytest.approx(125000)


# ─── Currency detection ───────────────────────────────────────────────────────

class TestCurrencyDetection:
    def test_gbp_symbol(self):
        cur, amt = detect_currency("£45,000")
        assert cur == "GBP"
        assert amt == pytest.approx(45000)

    def test_usd_symbol(self):
        cur, amt = detect_currency("$125,000")
        assert cur == "USD"
        assert amt == pytest.approx(125000)

    def test_eur_symbol(self):
        cur, amt = detect_currency("€85,000")
        assert cur == "EUR"
        assert amt == pytest.approx(85000)

    def test_sgd_prefix(self):
        cur, amt = detect_currency("S$350,000")
        assert cur == "SGD"
        assert amt == pytest.approx(350000)

    def test_chf_prefix(self):
        cur, amt = detect_currency("CHF 250,000")
        assert cur == "CHF"
        assert amt == pytest.approx(250000)

    def test_usd_iso_code(self):
        cur, amt = detect_currency("USD 125,000")
        assert cur == "USD"
        assert amt == pytest.approx(125000)

    def test_no_symbol_defaults_gbp(self):
        cur, amt = detect_currency("45,000")
        assert cur == "GBP"
        assert amt == pytest.approx(45000)

    def test_gbp_passthrough(self):
        # price_to_gbp with GBP → no rate fetch, returns same amount
        result = price_to_gbp(45000.0, "GBP")
        assert result == pytest.approx(45000)

    def test_usd_conversion_uses_fallback(self):
        # No API key in test env → fallback rate (~0.79) applied
        rate = _FALLBACK_RATES.get("USD", 0.79)
        result = price_to_gbp(100_000.0, "USD")
        assert result is not None
        assert result == pytest.approx(100_000 * rate, rel=0.01)

    def test_none_amount_returns_none(self):
        assert price_to_gbp(None, "USD") is None



# ─── JamesEdition auction price parsing ──────────────────────────────────────

class TestJamesEditionAuctionParsing:
    """Test _parse_estimate_range and _json_ld_to_item auction field extraction."""

    def test_gbp_range_text(self):
        low, high = _parse_estimate_range("£45,000 – £65,000", "GBP")
        assert low  == pytest.approx(45_000)
        assert high == pytest.approx(65_000)

    def test_k_suffix_range(self):
        low, high = _parse_estimate_range("£45k – £65k", "GBP")
        assert low  == pytest.approx(45_000)
        assert high == pytest.approx(65_000)

    def test_usd_range_converted(self):
        rate = _FALLBACK_RATES.get("USD", 0.79)
        low, high = _parse_estimate_range("$100,000 – $150,000", "USD")
        assert low  == pytest.approx(100_000 * rate, rel=0.01)
        assert high == pytest.approx(150_000 * rate, rel=0.01)

    def test_iso_prefix_range(self):
        rate = _FALLBACK_RATES.get("USD", 0.79)
        low, high = _parse_estimate_range("USD 80,000 to USD 120,000", "USD")
        assert low  == pytest.approx(80_000  * rate, rel=0.01)
        assert high == pytest.approx(120_000 * rate, rel=0.01)

    def test_single_price(self):
        low, high = _parse_estimate_range("£55,000", "GBP")
        assert low  == pytest.approx(55_000)
        assert high is None

    def test_empty_text(self):
        low, high = _parse_estimate_range("", "GBP")
        assert low is None and high is None

    def test_estimate_prefix_stripped(self):
        low, high = _parse_estimate_range("Estimate: £45,000 – £65,000", "GBP")
        assert low  == pytest.approx(45_000)
        assert high == pytest.approx(65_000)

    def test_json_ld_auction_fields_extracted(self):
        """_json_ld_to_item maps priceSpecification min/max to estimate low/high."""
        ld = {
            "@type": "Car",
            "name": "Ferrari F430",
            "offers": {
                "price": 75000,
                "priceCurrency": "GBP",
                "priceSpecification": {
                    "minPrice": 65000,
                    "maxPrice": 85000,
                },
                "currentBid": 62000,
            },
        }
        item = _json_ld_to_item(ld, "https://www.jamesedition.com/cars/ferrari/f430/", "abc123")
        assert item is not None
        assert item["estimate"]["low"]  == 65000
        assert item["estimate"]["high"] == 85000
        assert item["current_bid"]      == 62000
        assert item["currency"]         == "GBP"

    def test_json_ld_no_auction_fields(self):
        """_json_ld_to_item works fine when no auction data is present."""
        ld = {
            "@type": "Car",
            "name": "Porsche 911 GT3",
            "offers": {"price": 120000, "priceCurrency": "GBP"},
        }
        item = _json_ld_to_item(ld, "https://www.jamesedition.com/cars/porsche/911-gt3/", "xyz")
        assert item is not None
        # estimate low/high both None when not present
        assert item["estimate"]["low"]  is None
        assert item["estimate"]["high"] is None
        assert item["current_bid"]      is None


# ─── Search targets ───────────────────────────────────────────────────────────

class TestSearchTargets:
    def test_all_makes_covered(self):
        makes_in_pairs = {make for make, _ in search_pairs()}
        for make in config.TARGET_VEHICLES:
            assert make in makes_in_pairs, f"Missing in search_pairs: {make}"

    def test_models_expanded(self):
        # Makes with specific models should have one pair per model
        ferrari_pairs = [(m, mdl) for m, mdl in search_pairs() if m == "Ferrari"]
        n_models = len(config.TARGET_VEHICLES["Ferrari"])
        assert len(ferrari_pairs) == n_models

    def test_mclaren_single_pair(self):
        # McLaren has empty model list → one (McLaren, None) pair
        mc_pairs = [(m, mdl) for m, mdl in search_pairs() if m == "McLaren"]
        assert len(mc_pairs) == 1
        assert mc_pairs[0][1] is None

    def test_carandclassic_make_lowercase(self):
        assert CARANDCLASSIC_MAKE["Ferrari"]     == "ferrari"
        assert CARANDCLASSIC_MAKE["Aston Martin"] == "aston-martin"
        assert CARANDCLASSIC_MAKE["BMW"]          == "bmw"

    def test_jamesedition_make_slugs(self):
        assert JAMESEDITION_MAKE["Aston Martin"] == "aston-martin"
        assert JAMESEDITION_MAKE["McLaren"]       == "mclaren"


# ─── Scraper class wiring ─────────────────────────────────────────────────────

class TestScraperWiring:
    def test_all_uk_scrapers_import(self):
        from scrapers.autotrader    import AutoTraderScraper
        from scrapers.carandclassic import CarAndClassicScraper
        from scrapers.pistonheads   import PistonHeadsScraper
        from scrapers.ebay_motors   import EbayMotorsScraper
        from scrapers.jamesedition  import JamesEditionScraper  # disabled but file kept

        for cls in (AutoTraderScraper, CarAndClassicScraper, PistonHeadsScraper,
                    EbayMotorsScraper, JamesEditionScraper):
            assert cls.MARKET == "UK"
            assert cls.SOURCE != ""

    def test_sg_scrapers_market(self):
        from scrapers.sgcarmart import SgCarMartScraper
        from scrapers.carousell import CarousellScraper
        from scrapers.carro     import CarroScraper
        for cls in (SgCarMartScraper, CarousellScraper, CarroScraper):
            assert cls.MARKET == "SG"

    def test_carandclassic_search_url(self):
        from scrapers.carandclassic import CarAndClassicScraper
        url = CarAndClassicScraper._search_url("ferrari", "f430", 1)
        assert "ferrari" in url and "f430" in url

    def test_carandclassic_search_url_page2(self):
        from scrapers.carandclassic import CarAndClassicScraper
        url = CarAndClassicScraper._search_url("porsche", None, 3)
        assert "page=3" in url

    def test_pistonheads_source(self):
        from scrapers.pistonheads import PistonHeadsScraper
        assert PistonHeadsScraper.SOURCE == "pistonheads"
        assert PistonHeadsScraper.MARKET == "UK"

    def test_ebay_search_url(self):
        from scrapers.ebay_motors import EbayMotorsScraper
        url = EbayMotorsScraper._search_url("Ferrari", "F430", 2)
        assert "_pgn=2" in url and "Ferrari" in url

    def test_autotrader_search_url(self):
        from scrapers.autotrader import AutoTraderScraper
        url = AutoTraderScraper._search_url("Ferrari", "F430", 1)
        assert "make=Ferrari" in url and "F430" in url and "EH11AD" in url

