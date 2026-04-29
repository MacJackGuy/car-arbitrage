"""
Central configuration for the SG→UK car arbitrage tool.
All monetary values in SGD unless the variable name specifies otherwise.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Target vehicles ───────────────────────────────────────────────────────────
# Empty list = all variants of that make are in scope
TARGET_VEHICLES: dict[str, list[str]] = {
    "Ferrari":      ["F430", "430 Scuderia", "California", "612 Scaglietti", "Purosangue"],
    "Lamborghini":  ["Huracan", "Gallardo", "Urus"],
    "McLaren":      [],
    "Porsche":      ["911 GTS", "911 Turbo", "Cayenne GTS", "Cayenne Turbo",
                     "Panamera GTS", "Panamera Turbo", "Taycan Turbo", "Macan GTS"],
    "Aston Martin": ["Vantage", "DB11", "DBX707"],
    "Bentley":      ["Continental GT", "Bentayga Speed"],
    "BMW":          ["M2", "M3", "M4", "M5", "M6", "M8",
                     "X5M", "X6M", "Z3", "Z4", "Z8", "i8"],
    "Audi":         ["R8", "RS3", "RS4", "RS5", "RS6", "RS7",
                     "RSQ3", "RSQ5", "RSQ8",
                     "S3", "S4", "S5", "S6", "S7", "S8",
                     "SQ5", "SQ7", "SQ8"],
    "Maserati":     ["Levante Trofeo"],
    "Mercedes-Benz": ["AMG GT", "C63", "E63", "G63", "G500",
                      "S63", "S65", "SLS", "GLE63", "GLS63"],
    "Rolls-Royce":  [],
}

# Track 4 eligible models (4/5-seater GT)
TRACK_4_MODELS = [
    "Urus", "DBX707", "Bentayga Speed", "RS6", "RSQ8",
    "Purosangue", "612 Scaglietti", "California",
]

SCORING_TRACKS = {
    1: "Near-COE private seller flip",
    2: "Spec/colour rarity arbitrage",
    3: "15-year collector appreciation",
    4: "4/5-seater GT",
}

# ── Scrape sources ────────────────────────────────────────────────────────────
SG_SOURCES = ["sgcarmart", "carousell", "carro"]
UK_SOURCES = [
    "autotrader", "carandclassic", "pistonheads", "ebay_motors",
    # "jamesedition" — disabled (Cloudflare ASN 9009 ban; needs residential proxy)
]

# ── Default costs (SGD) ───────────────────────────────────────────────────────
SHIPPING_SGD: float             = 10_000
UK_REGISTRATION_SGD: float      = 1_400      # DVLA/NOVA/MOT combined
INSURANCE_SGD_MIN: float        = 4_000
INSURANCE_SGD_MAX: float        = 8_000
INSURANCE_SGD_DEFAULT: float    = 6_000      # midpoint
UK_ROAD_TAX_ANNUAL_SGD: float   = 860        # ~£500/yr for high-emission cars

# ── UK import duty rates ──────────────────────────────────────────────────────
IMPORT_DUTY_RATE: float = 0.065   # 6.5% on customs (CIF) value
VAT_RATE: float         = 0.20    # 20% on (customs value + duty)

# ── Profit thresholds & scenarios ────────────────────────────────────────────
MIN_PROFIT_SGD: float           = 50_000     # minimum after full duty to flag as opportunity
DEFAULT_HOLD_MONTHS: int        = 8
DEPRECIATION_RATE_ANNUAL: float = 0.05   # 5%/yr — conservative for collector/luxury cars
PRE_SALE_SERVICE_SGD: float     = 800    # basic pre-sale service / inspection / valet

# UK sale price relative to the "mid" market comparable listing
UK_SALE_MULTIPLIERS: dict[str, float] = {
    "conservative": 0.90,   # 10% below comparable (slow market / quick exit)
    "mid":          0.97,   # 3% below (typical negotiating discount)
    "optimistic":   1.03,   # 3% above (rare spec / strong buyer interest)
}

# ── Scoring signal weights (sum to 100 for a perfect-score car) ───────────────
SCORING_WEIGHTS: dict[str, int] = {
    "direct_owner":         20,
    "uk_colour_gap":        15,
    "spec_rarity":          15,
    "coe_leverage":         10,   # large PARF+COE rebate vs asking price
    "low_owners":           10,   # 1–2 owners
    "condition_excellent":  10,
    "service_history":       8,
    "ppi_mentioned":         7,
    # penalties
    "six_plus_owners":     -15,
    "aftermarket_mods":    -10,
    "price_firm_no_td":     -5,   # "price firm, no test drives" = amber
    "manuf_reg_gap":        -5,   # manufactured > 12 months before registration
}

# ── External services ─────────────────────────────────────────────────────────
EXCHANGERATE_API_KEY: str = os.getenv("EXCHANGERATE_API_KEY", "")
ANTHROPIC_API_KEY: str    = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL: str             = "claude-opus-4-6"

ALERT_EMAIL: str  = os.getenv("ALERT_EMAIL", "")
SMTP_HOST: str    = os.getenv("SMTP_HOST", "")
SMTP_PORT: int    = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str    = os.getenv("SMTP_USER", "")
SMTP_PASS: str    = os.getenv("SMTP_PASS", "")

# ── Paths ─────────────────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "data/arbitrage.db")
