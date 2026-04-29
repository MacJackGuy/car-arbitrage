# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

SG→UK luxury/collector car arbitrage tool. Identifies Singapore cars that can be profitably bought, deregistered, and exported to the UK. Uses Python + SQLite + Flask + Playwright + Anthropic API.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # required for scrapers

# Tests
pytest tests/                     # all tests
pytest tests/test_profit.py -v    # single file
pytest tests/ -k "test_parf"      # filter by name

# Init database
python3 -c "from database.db import init_db; init_db()"

# Scrapers (VPN must be active — IVPN verified via ipinfo.io before each run)
python -m scrapers.runner                        # daily delta (new listings only)
python -m scrapers.runner --full                 # full initial scrape
python -m scrapers.runner --source sgcarmart     # single source
python -m scrapers.runner --full --source carro  # full + single source
```

## Architecture

```
engine/          Core business logic (no I/O dependencies)
  coe_parf.py    COE pro-rata refund + PARF rebate tiers + ARF estimation
  fx.py          Live GBP/SGD rate from exchangerate-api.com, cached daily in DB
  profit.py      calculate_profit() → ProfitResult with 3 scenarios + opportunity_rating

database/
  schema.sql     Source of truth — 8 tables (listings, profit_calculations, fx_rates,
                 ai_analyses, uk_supply, watchlist, alerts, scrape_runs)
  db.py          get_conn(), init_db(), upsert_listing(), save_profit_calculation()

models/listing.py    Listing + UKSupply dataclasses with to_db_dict() / from_row()
utils/vpn.py         require_vpn() — blocks execution if IVPN not detected via ipinfo.io
config.py            All constants, target vehicles, cost defaults, API keys from env

scrapers/        Phase 2 — SG listing scrapers
  base.py        BaseScraper async context manager: VPN check, Playwright lifecycle,
                 random delays (8–30s), rotating UAs, scrape_run DB logging
  parsers.py     Pure parsing utils: price, mileage, COE date, year, seller type, etc.
  user_agents.py Pool of 30 realistic desktop UA strings
  sgcarmart.py   SgCarMartScraper — server-side PHP; #merchantlisting container;
                 MDL param; BRSR pagination; delta via listing ID match
  carousell.py   CarousellScraper — React SPA; XHR interception + DOM fallback;
                 a[href*="/p/"] link harvesting; infinite scroll
  carro.py       CarroScraper — Next.js; API interception + __NEXT_DATA__ fallback;
                 DOM card harvesting; all listings are dealer type
  runner.py      run_sg_scrapers() orchestrator; CLI entry point
```

### Scraper delta mode
All scrapers accept `known_ids: set[str]` (source_listing_ids from DB). They sort results newest-first and stop pagination when a known ID appears. Pass `None` for a full rescrape.

### Selector resilience
sgCarMart: multiple fallback selectors for spec tables (`table_cardetail`, `car_spec_cont`, global table scan). Carousell/Carro: XHR interception is primary (immune to CSS class churn); DOM scraping is fallback.

## Profit model

**SG side:**  
`net_sg_cost = purchase_price − coe_refund − parf_refund`

- COE refund = `(months_remaining / 120) × original_coe_value`
- PARF rebate = tiered % of ARF paid: 75% (≤5yr) → 50% (10yr) → 0% (>10yr)

**UK base costs (shared across all three scenarios):**  
`base_import = shipping + UK_registration + insurance + road_tax_prorata + depreciation + pre_sale_service`
- Depreciation = `uk_sale_price_sgd × 5%/yr × hold_months/12` (time decay during hold)
- Pre-sale service = S$800 default

**Three scenarios computed side-by-side:**

| # | Name | Customs basis | When applicable |
|---|------|---------------|----------------|
| 1 | TOR | No duty/VAT | Owner moving back to UK (≥12 months abroad) |
| 2 | Full duty | SG invoice price / FX | Standard commercial import (worst case) |
| 3 | Effective duty | Net cost after rebates / FX | Practical middle ground |

**Key insight:** HMRC sees the SG invoice price, not your net cost after collecting COE/PARF rebates. A 992 GT3 at S$250k with S$140k in rebates still pays duty on £145k customs value → S$70k duty+VAT → makes full-duty threshold very hard to meet for high-priced SG cars. Old cheap cars with premium UK prices (F430 Scuderia at S$35k → £78k UK) clear it easily.

**Opportunity ratings** (`result.opportunity_rating`):

| Rating | Condition | Meaning |
|--------|-----------|---------|
| `green` | TOR profit ≥ S$50k | Strong — viable under any scenario |
| `amber_marginal` | Full-duty profit ≥ S$20k | Workable without TOR |
| `amber_tor` | TOR profit S$20–50k, full-duty < S$20k | Needs TOR confirmed first |
| `red` | TOR profit < 0 or < S$20k with poor duty | Pass |

**`min_threshold_met`** = `full_duty.meets_threshold` (most conservative — profit ≥ S$50k after worst-case duty)

## Scoring tracks

| # | Name | Key models |
|---|------|-----------|
| 1 | Near-COE private seller flip | Any target make near COE expiry |
| 2 | Spec/colour rarity arbitrage | Rare colour with 0 UK matches |
| 3 | 15-year collector appreciation | Ferrari, McLaren, Porsche GT variants |
| 4 | 4/5-seater GT | Urus, DBX707, Bentayga Speed, RS6, RSQ8, Purosangue, 612 Scaglietti, California |

## Build phases

| Phase | Status | Scope |
|-------|--------|-------|
| 1 | **Done** | Database schema + profit engine |
| 2 | **Done** | SG scrapers (sgCarMart, Carousell, Carro) |
| 3 | Pending | UK scrapers (AutoTrader, PistonHeads, Car&Classic, eBay, Collecting Cars, ClassicCarsForSale, JamesEdition) |
| 4 | Pending | AI analysis via Anthropic API (photos + description) |
| 5 | Pending | Flask dashboard (SGD+GBP side by side, ranked opportunities) |
| 6 | Pending | Scheduler + Mac automation + email alerts |

## Environment variables

See `.env.example`. Required for live use:
- `EXCHANGERATE_API_KEY` — exchangerate-api.com (free tier: 1500 req/month)
- `ANTHROPIC_API_KEY` — Phase 4 AI analysis
- `ALERT_EMAIL` / `SMTP_*` — Phase 6 email alerts

## VPN requirement

`utils/vpn.require_vpn()` must be called before every scrape run. Detects IVPN by checking the `org` field in the ipinfo.io response. Raises `RuntimeError` if not active — do not bypass this check.

## Session behaviour rules

- Do not ask for approval on individual file edits — make the change directly
- Read TASKS.md at session start if it exists
- Commit working code before moving to the next task
- Do not change the stack or add new dependencies without explaining why first
- Current priority: Phase 3 (UK scrapers)