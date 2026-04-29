-- ============================================================
-- Car Arbitrage Tool — SQLite Schema
-- All monetary amounts in their native currency unless the
-- column name specifies (e.g. price_sgd, profit_gbp).
-- ============================================================

-- ─── FX rates (daily cache) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fx_rates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT UNIQUE NOT NULL,           -- ISO-8601 YYYY-MM-DD
    sgd_to_gbp  REAL NOT NULL,
    gbp_to_sgd  REAL NOT NULL,
    source      TEXT DEFAULT 'exchangerate-api.com',
    fetched_at  TEXT DEFAULT (datetime('now'))
);

-- ─── Listings (SG and UK) ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS listings (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    source                  TEXT NOT NULL,      -- sgcarmart | carousell | carro | autotrader | ...
    market                  TEXT NOT NULL       -- SG | UK
                            CHECK(market IN ('SG', 'UK')),
    source_url              TEXT UNIQUE NOT NULL,
    source_listing_id       TEXT,

    -- Vehicle identity
    make                    TEXT NOT NULL,
    model                   TEXT NOT NULL,
    variant                 TEXT,
    year_manufactured       INTEGER,
    year_registered         INTEGER,
    mileage_km              INTEGER,           -- SG listings (km); UK = miles * 1.60934
    mileage_miles           INTEGER,           -- UK listings (miles); NULL for SG

    -- Price (both currencies where known)
    price_sgd               REAL,
    price_gbp               REAL,
    -- Auction price range (Collecting Cars — estimate low/high + live bid)
    price_low_gbp           REAL,
    price_high_gbp          REAL,
    current_bid_gbp         REAL,

    -- SG registration data (drives COE refund + PARF calculation)
    coe_expiry_date         TEXT,               -- ISO-8601 date
    coe_months_remaining    INTEGER,
    coe_original_value_sgd  REAL,               -- what was paid at registration; NULL = unknown
    arf_paid_sgd            REAL,               -- Additional Registration Fee; NULL = unknown
    omv_sgd                 REAL,               -- Open Market Value; NULL = unknown

    -- Physical / spec
    colour                  TEXT,
    interior_colour         TEXT,
    num_owners              INTEGER,
    is_direct_owner         INTEGER DEFAULT 0,  -- 0/1 boolean
    has_service_history     INTEGER,            -- 0/1/NULL (NULL = unknown)
    ppi_mentioned           INTEGER DEFAULT 0,  -- Pre-Purchase Inspection mentioned
    has_aftermarket_mods    INTEGER,            -- 0/1/NULL
    seat_type               TEXT,               -- daytona | sport_buckets | standard | unknown
    has_ccm_brakes          INTEGER,            -- 0/1/NULL

    -- Seller
    seller_name             TEXT,
    seller_type             TEXT,               -- private | dealer
    description_text        TEXT,
    image_urls              TEXT DEFAULT '[]',  -- JSON array of URLs

    -- Staleness tracking
    listing_date            TEXT,               -- ISO-8601, as listed by seller
    first_seen_at           TEXT DEFAULT (datetime('now')),
    last_seen_at            TEXT DEFAULT (datetime('now')),
    days_on_market          INTEGER,
    is_active               INTEGER DEFAULT 1,

    created_at              TEXT DEFAULT (datetime('now')),
    updated_at              TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_listings_market_make  ON listings(market, make);
CREATE INDEX IF NOT EXISTS idx_listings_source       ON listings(source);
CREATE INDEX IF NOT EXISTS idx_listings_active       ON listings(is_active);
CREATE INDEX IF NOT EXISTS idx_listings_make_model   ON listings(make, model);
CREATE INDEX IF NOT EXISTS idx_listings_coe          ON listings(coe_months_remaining);

-- ─── Price history ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price_sgd   REAL,
    price_gbp   REAL,
    recorded_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id);

-- ─── Profit calculations ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profit_calculations (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    sg_listing_id               INTEGER NOT NULL REFERENCES listings(id),
    uk_listing_id               INTEGER REFERENCES listings(id),    -- matched comparator if any

    -- Inputs recorded for auditability
    fx_rate_gbp_sgd             REAL NOT NULL,
    calculation_date            TEXT NOT NULL DEFAULT (date('now')),
    hold_period_months          INTEGER DEFAULT 8,
    assumptions_json            TEXT DEFAULT '{}',  -- JSON: flags for any defaulted values

    -- SG side
    purchase_price_sgd          REAL NOT NULL,
    coe_refund_sgd              REAL NOT NULL DEFAULT 0,
    parf_refund_sgd             REAL NOT NULL DEFAULT 0,
    net_sg_cost_sgd             REAL NOT NULL,      -- purchase_price − coe_refund − parf_refund

    -- UK import costs (SGD equivalent, shared across all three scenarios)
    shipping_sgd                REAL NOT NULL DEFAULT 10000,
    uk_registration_sgd         REAL NOT NULL DEFAULT 1400,
    insurance_sgd               REAL NOT NULL DEFAULT 6000,
    road_tax_prorata_sgd        REAL NOT NULL DEFAULT 0,
    depreciation_sgd            REAL NOT NULL DEFAULT 0,     -- UK sale price × rate × hold_months/12
    pre_sale_service_sgd        REAL NOT NULL DEFAULT 800,
    base_import_cost_sgd        REAL NOT NULL,               -- sum of six lines above

    -- UK sale price inputs
    uk_sale_price_gbp           REAL NOT NULL,      -- "mid" market comparable
    uk_sale_price_sgd           REAL NOT NULL,

    -- Scenario 1: TOR (Transfer of Residence — no duty, no VAT)
    total_cost_tor_sgd          REAL NOT NULL,
    profit_tor_sgd              REAL NOT NULL,
    profit_tor_gbp              REAL NOT NULL,
    profit_tor_conservative_sgd REAL NOT NULL,
    profit_tor_mid_sgd          REAL NOT NULL,
    profit_tor_optimistic_sgd   REAL NOT NULL,
    meets_threshold_tor         INTEGER NOT NULL DEFAULT 0,

    -- Scenario 2: Full duty — HMRC on full SG invoice price (worst case)
    customs_value_gbp                   REAL,
    import_duty_sgd                     REAL NOT NULL DEFAULT 0,
    vat_sgd                             REAL NOT NULL DEFAULT 0,
    total_cost_full_duty_sgd            REAL NOT NULL,
    profit_full_duty_sgd                REAL NOT NULL,
    profit_full_duty_gbp                REAL NOT NULL,
    profit_full_duty_conservative_sgd   REAL NOT NULL,
    profit_full_duty_mid_sgd            REAL NOT NULL,
    profit_full_duty_optimistic_sgd     REAL NOT NULL,
    meets_threshold_full_duty           INTEGER NOT NULL DEFAULT 0,

    -- Scenario 3: Effective duty — duty on net cost after PARF + COE rebates
    customs_value_effective_gbp                 REAL,
    import_duty_effective_sgd                   REAL NOT NULL DEFAULT 0,
    vat_effective_sgd                           REAL NOT NULL DEFAULT 0,
    total_cost_effective_duty_sgd               REAL,
    profit_effective_duty_sgd                   REAL,
    profit_effective_duty_gbp                   REAL,
    profit_effective_duty_conservative_sgd      REAL,
    profit_effective_duty_mid_sgd               REAL,
    profit_effective_duty_optimistic_sgd        REAL,
    meets_threshold_effective_duty              INTEGER NOT NULL DEFAULT 0,

    -- Rating
    opportunity_rating          TEXT,   -- green | amber_tor | amber_marginal | red

    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_profit_sg_listing      ON profit_calculations(sg_listing_id);
CREATE INDEX IF NOT EXISTS idx_profit_threshold_fd    ON profit_calculations(meets_threshold_full_duty);
CREATE INDEX IF NOT EXISTS idx_profit_date            ON profit_calculations(calculation_date);

-- ─── AI analyses ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_analyses (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id              INTEGER NOT NULL REFERENCES listings(id),

    opportunity_score       INTEGER CHECK(opportunity_score BETWEEN 0 AND 100),
    track_assignment        INTEGER CHECK(track_assignment BETWEEN 1 AND 4),

    -- Photo + description outputs
    colour_assessment       TEXT,
    condition_assessment    TEXT,
    spec_details            TEXT DEFAULT '{}',      -- JSON: seat_type, brakes, variant clues, etc.
    flags_raised            TEXT DEFAULT '[]',      -- JSON array of flag strings
    missing_details         TEXT DEFAULT '[]',      -- JSON array of items not visible/mentioned

    -- UK supply cross-reference
    uk_colour_supply_count  INTEGER,
    uk_colour_supply_note   TEXT,

    -- Summary
    analysis_summary        TEXT,
    raw_response            TEXT,                   -- full Claude response
    model_used              TEXT,
    tokens_used             INTEGER,

    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ai_listing ON ai_analyses(listing_id);
CREATE INDEX IF NOT EXISTS idx_ai_score   ON ai_analyses(opportunity_score);
CREATE INDEX IF NOT EXISTS idx_ai_track   ON ai_analyses(track_assignment);

-- ─── UK supply (colour / spec rarity data) ───────────────────────────────────
-- Tracks how many UK listings exist for a given make/model/colour combination.
-- Zero UK matches = rare flag (high weight in scoring).
CREATE TABLE IF NOT EXISTS uk_supply (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    make            TEXT NOT NULL,
    model           TEXT NOT NULL,
    variant         TEXT,
    colour          TEXT,
    year_min        INTEGER,
    year_max        INTEGER,
    supply_count    INTEGER DEFAULT 0,
    -- Price reference points (GBP)
    price_min_gbp   REAL,
    price_max_gbp   REAL,
    price_avg_gbp   REAL,
    last_updated    TEXT DEFAULT (datetime('now')),
    UNIQUE(make, model, variant, colour)
);

-- ─── Watchlist ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlist (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id          INTEGER NOT NULL REFERENCES listings(id),
    added_at            TEXT DEFAULT (datetime('now')),
    target_price_sgd    REAL,
    notes               TEXT,
    status              TEXT DEFAULT 'watching'     -- watching | purchased | passed
                        CHECK(status IN ('watching', 'purchased', 'passed'))
);

-- ─── Alerts ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  INTEGER REFERENCES listings(id),
    alert_type  TEXT NOT NULL,  -- opportunity | price_drop | new_listing | vpn_failure
    message     TEXT,
    email_sent  INTEGER DEFAULT 0,
    sent_at     TEXT DEFAULT (datetime('now'))
);

-- ─── Scrape run log ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scrape_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT NOT NULL,
    run_type            TEXT NOT NULL               -- full | incremental
                        CHECK(run_type IN ('full', 'incremental')),
    status              TEXT DEFAULT 'running'
                        CHECK(status IN ('running', 'completed', 'failed')),
    started_at          TEXT DEFAULT (datetime('now')),
    completed_at        TEXT,
    listings_found      INTEGER DEFAULT 0,
    new_listings        INTEGER DEFAULT 0,
    updated_listings    INTEGER DEFAULT 0,
    errors_json         TEXT DEFAULT '[]',          -- JSON array of error strings
    vpn_verified        INTEGER DEFAULT 0,
    ip_address          TEXT
);

-- ─── Multi-currency FX rates (UK scrapers: USD/EUR/etc. → GBP) ───────────────
CREATE TABLE IF NOT EXISTS currency_rates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,                    -- ISO-8601 YYYY-MM-DD
    from_currency TEXT NOT NULL,                    -- e.g. USD, EUR, SGD
    to_currency   TEXT NOT NULL DEFAULT 'GBP',
    rate          REAL NOT NULL,
    fetched_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(date, from_currency, to_currency)
);
