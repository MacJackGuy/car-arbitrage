"""
Multi-currency → GBP conversion for UK scrapers (primarily JamesEdition,
which lists cars priced in USD, EUR, CHF, SGD, and other currencies).

Rates are fetched once per day from exchangerate-api.com and cached in the
`currency_rates` table.  If the API key is not configured, a fallback table
of approximate rates is used so the scraper still runs.

Usage:
    from scrapers.currency import price_to_gbp, detect_currency

    currency, amount = detect_currency("$125,000")   # → ('USD', 125000.0)
    gbp = price_to_gbp(amount, currency)             # → ~99000.0
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

import config
from database.db import get_conn

log = logging.getLogger(__name__)

# ── Approximate fallback rates (1 <currency> = X GBP) ─────────────────────────
# Used when EXCHANGERATE_API_KEY is not set.  Updated periodically by hand.
_FALLBACK_RATES: dict[str, float] = {
    "USD": 0.79,
    "EUR": 0.86,
    "CHF": 0.88,
    "SGD": 0.58,
    "AUD": 0.51,
    "HKD": 0.10,
    "JPY": 0.0052,
    "CAD": 0.58,
    "NOK": 0.074,
    "SEK": 0.074,
    "DKK": 0.115,
    "NZD": 0.47,
    "ZAR": 0.043,
    "AED": 0.215,
}


# ── Currency detection ─────────────────────────────────────────────────────────

def detect_currency(price_text: str) -> tuple[str, Optional[float]]:
    """
    Parse a price string, returning (currency_code, numeric_amount).

    Examples:
        "£45,000"       → ("GBP", 45000.0)
        "$125,000"      → ("USD", 125000.0)
        "€85,000"       → ("EUR", 85000.0)
        "S$350,000"     → ("SGD", 350000.0)
        "CHF 250,000"   → ("CHF", 250000.0)
        "USD 125,000"   → ("USD", 125000.0)
        "45,000"        → ("GBP", 45000.0)   # default when no symbol
    """
    text = price_text.strip()

    # Multi-char symbol prefixes first (order matters)
    _PREFIX_MAP = [
        ("S$",   "SGD"),
        ("A$",   "AUD"),
        ("HK$",  "HKD"),
        ("NZ$",  "NZD"),
        ("CA$",  "CAD"),
        ("CHF",  "CHF"),
    ]
    for sym, code in _PREFIX_MAP:
        if text.upper().startswith(sym.upper()):
            amount = _parse_numeric(text[len(sym):])
            return code, amount

    # Single-char currency symbols
    _SYMBOL_MAP = {
        "£": "GBP",
        "$": "USD",
        "€": "EUR",
        "¥": "JPY",
        "₣": "CHF",
    }
    for sym, code in _SYMBOL_MAP.items():
        if text.startswith(sym):
            amount = _parse_numeric(text[1:])
            return code, amount

    # Trailing or leading ISO code: "USD 125,000" or "125,000 USD"
    m = re.search(
        r'\b(USD|EUR|GBP|SGD|CHF|AUD|CAD|HKD|JPY|NOK|SEK|DKK|NZD|ZAR|AED)\b',
        text,
        re.IGNORECASE,
    )
    if m:
        code   = m.group(1).upper()
        numeric = text[: m.start()] + text[m.end():]
        amount  = _parse_numeric(numeric)
        return code, amount

    # No recognisable currency — assume GBP (UK context default)
    return "GBP", _parse_numeric(text)


def _parse_numeric(raw: str) -> Optional[float]:
    """Strip all non-numeric chars (except '.') and convert to float."""
    cleaned = re.sub(r"[^\d.]", "", raw.replace(",", ""))
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


# ── Conversion ────────────────────────────────────────────────────────────────

def price_to_gbp(
    amount: Optional[float],
    currency: str,
    db_path: str = config.DB_PATH,
) -> Optional[float]:
    """
    Convert amount in `currency` to GBP.
    Returns None if amount is None or conversion rate unavailable.
    """
    if amount is None:
        return None
    currency = currency.upper()
    if currency == "GBP":
        return round(amount, 2)
    rate = get_rate_to_gbp(currency, db_path)
    if rate is None:
        log.warning("[currency] No rate found for %s → GBP, skipping conversion", currency)
        return None
    return round(amount * rate, 2)


def get_rate_to_gbp(
    currency: str,
    db_path: str = config.DB_PATH,
) -> Optional[float]:
    """
    Return exchange rate: 1 <currency> = X GBP.
    Checks the daily DB cache first; fetches from API on miss.
    """
    currency = currency.upper()
    if currency == "GBP":
        return 1.0

    from datetime import date
    today = date.today().isoformat()

    # Cache lookup
    try:
        with get_conn(db_path) as conn:
            row = conn.execute(
                """SELECT rate FROM currency_rates
                   WHERE date = ? AND from_currency = ? AND to_currency = 'GBP'""",
                (today, currency),
            ).fetchone()
            if row:
                return float(row["rate"])
    except Exception:
        pass

    # Live fetch
    rate = _fetch_live_rate(currency)
    if rate is None:
        rate = _FALLBACK_RATES.get(currency)
        if rate:
            log.info("[currency] Using fallback rate for %s: %.4f", currency, rate)
        else:
            log.warning("[currency] No rate available for %s", currency)
            return None

    # Cache the result
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO currency_rates
                   (date, from_currency, to_currency, rate)
                   VALUES (?, ?, 'GBP', ?)""",
                (today, currency, rate),
            )
    except Exception:
        pass

    return rate


def _fetch_live_rate(currency: str) -> Optional[float]:
    """Fetch 1 <currency> → GBP from exchangerate-api.com."""
    if not config.EXCHANGERATE_API_KEY:
        return None
    try:
        url = (
            f"https://v6.exchangerate-api.com/v6/{config.EXCHANGERATE_API_KEY}"
            f"/pair/{currency}/GBP"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("result") == "success":
            return float(data["conversion_rate"])
    except Exception as exc:
        log.warning("[currency] API fetch failed for %s: %s", currency, exc)
    return None
