"""
Shared parsing utilities for raw scraped text → typed Python values.
All functions are pure (no I/O) and return None on parse failure.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional


# ── Price ─────────────────────────────────────────────────────────────────────

def parse_price_sgd(raw: str) -> Optional[float]:
    """'S$ 250,000' | '$250,000' | '250000' → 250000.0"""
    if not raw:
        return None
    cleaned = re.sub(r'[^\d.]', '', raw.replace(',', ''))
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


# ── Mileage ───────────────────────────────────────────────────────────────────

def parse_mileage_km(raw: str) -> Optional[int]:
    """'50,000 km' | '50000km' | '50,000' → 50000"""
    if not raw:
        return None
    cleaned = re.sub(r'[^\d]', '', raw.split('k')[0].replace(',', ''))
    try:
        val = int(cleaned)
        return val if 0 < val < 2_000_000 else None
    except ValueError:
        return None


# ── Year ──────────────────────────────────────────────────────────────────────

def parse_year(raw: str) -> Optional[int]:
    """'2015' | 'Mar 2015' | '2015-03' → 2015"""
    if not raw:
        return None
    match = re.search(r'\b(19\d{2}|20\d{2})\b', raw)
    if match:
        return int(match.group(1))
    return None


def parse_year_from_date(raw: str) -> Optional[int]:
    """'03/2015' | '2015-03-01' → 2015"""
    if not raw:
        return None
    # MM/YYYY
    m = re.search(r'\d{1,2}/(\d{4})', raw)
    if m:
        return int(m.group(1))
    return parse_year(raw)


# ── COE ───────────────────────────────────────────────────────────────────────

def parse_coe_expiry_iso(raw: str) -> Optional[str]:
    """
    Parse COE expiry from various formats → ISO date string 'YYYY-MM-01'.
    Inputs: '03/2026', 'Mar 2026', '2026-03', '2026/03'
    """
    if not raw:
        return None

    # MM/YYYY (most common on sgCarMart)
    m = re.search(r'\b(\d{1,2})[/\-](\d{4})\b', raw)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 2000 <= year <= 2040:
            return f"{year}-{month:02d}-01"

    # Month name YYYY: "Mar 2026"
    months = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
              "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
    m = re.search(r'([A-Za-z]{3})\s+(\d{4})', raw)
    if m:
        mname = m.group(1).lower()
        if mname in months:
            year = int(m.group(2))
            return f"{year}-{months[mname]:02d}-01"

    return None


def coe_months_remaining(expiry_iso: Optional[str]) -> Optional[int]:
    """Given 'YYYY-MM-01' ISO date, return whole months remaining from today."""
    if not expiry_iso:
        return None
    try:
        exp = date.fromisoformat(expiry_iso)
        today = date.today()
        months = (exp.year - today.year) * 12 + (exp.month - today.month)
        return max(0, months)
    except ValueError:
        return None


def parse_coe_expiry_from_reg_date(raw: str) -> Optional[str]:
    """
    Given the 'Reg Date' field from the new sgCarMart site (e.g. '19-Sep-2022'),
    calculate the COE expiry as reg_date + 10 years → 'YYYY-MM-01'.
    Input may also contain trailing '\n(Xyrs …)' which is ignored.
    """
    if not raw:
        return None
    # Take only the first line
    first_line = raw.split('\n')[0].strip()
    months = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
              "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
    # DD-Mon-YYYY or DD Mon YYYY
    m = re.search(r'(\d{1,2})[-\s]([A-Za-z]{3})[-\s](\d{4})', first_line)
    if m:
        try:
            day, mon, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
            if mon in months and 1990 <= year <= 2030:
                exp_year = year + 10
                exp_month = months[mon]
                return f"{exp_year}-{exp_month:02d}-01"
        except (ValueError, KeyError):
            pass
    return None


def parse_coe_category(raw: str) -> Optional[str]:
    """Extract 'A', 'B', 'C', 'D', or 'E' COE category."""
    if not raw:
        return None
    m = re.search(r'Cat(?:egory)?\s*([A-E])', raw, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r'\bCat\s*([A-E])\b', raw, re.IGNORECASE)
    return m.group(1).upper() if m else None


# ── Engine / road tax / currency ──────────────────────────────────────────────

def parse_engine_cc(raw: str) -> Optional[int]:
    """'1,984 cc' | '1984cc' → 1984"""
    if not raw:
        return None
    cleaned = re.sub(r'[^\d]', '', raw.split('c')[0].replace(',', ''))
    try:
        val = int(cleaned)
        return val if 100 < val < 10_000 else None
    except ValueError:
        return None


def parse_owners(raw: str) -> Optional[int]:
    """'1 Owner' | '2' | 'Two' → integer"""
    if not raw:
        return None
    m = re.search(r'\b(\d)\b', raw)
    if m:
        return int(m.group(1))
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    for w, n in words.items():
        if w in raw.lower():
            return n
    return None


def parse_depreciation_annual(raw: str) -> Optional[float]:
    """'S$ 15,000 / yr' → 15000.0"""
    return parse_price_sgd(raw.split('/')[0] if raw else "")


# ── Seller type ───────────────────────────────────────────────────────────────

def infer_seller_type(raw: str) -> str:
    """
    Infer 'private' or 'dealer' from badge text / page markers.
    Defaults to 'dealer' when ambiguous (conservative for scoring).
    """
    if not raw:
        return "dealer"
    low = raw.lower()
    if any(kw in low for kw in ("direct owner", "private seller", "individual")):
        return "private"
    return "dealer"


def is_direct_owner(page_text: str) -> bool:
    """True if the listing is marked as a direct owner sale."""
    return infer_seller_type(page_text) == "private"


# ── Make / model normalisation ────────────────────────────────────────────────

def normalise_make(raw: str) -> str:
    """Normalise raw make string to canonical form from config."""
    _MAP = {
        "FERRARI": "Ferrari",
        "LAMBORGHINI": "Lamborghini",
        "MCLAREN": "McLaren",
        "PORSCHE": "Porsche",
        "ASTON MARTIN": "Aston Martin",
        "BENTLEY": "Bentley",
        "BMW": "BMW",
        "AUDI": "Audi",
        "MASERATI": "Maserati",
    }
    return _MAP.get(raw.strip().upper(), raw.strip().title())


def is_target_model(make: str, model_text: str) -> bool:
    """
    Return True if model_text matches any of our target models for this make.
    Matching is keyword-based: all words in the target must appear in model_text.
    Empty target list = accept all models for that make.
    """
    import config
    targets = config.TARGET_VEHICLES.get(make, [])
    if not targets:
        return True
    upper = model_text.upper()
    for target in targets:
        words = target.upper().split()
        if all(w in upper for w in words):
            return True
    return False


# ── Text cleaning ─────────────────────────────────────────────────────────────

def clean(raw: Optional[str]) -> Optional[str]:
    """Strip whitespace and collapse internal runs."""
    if raw is None:
        return None
    return re.sub(r'\s+', ' ', raw).strip() or None


# ── UK mileage ────────────────────────────────────────────────────────────────

MILES_TO_KM = 1.60934


def parse_mileage_miles(raw: str) -> Optional[int]:
    """'25,432 miles' | '25432' | '25,432' → 25432"""
    if not raw:
        return None
    cleaned = re.sub(r'[^\d]', '', raw.split('m')[0].replace(',', ''))
    try:
        val = int(cleaned)
        return val if 0 < val < 2_000_000 else None
    except ValueError:
        return None


def miles_to_km(miles: Optional[int]) -> Optional[int]:
    """Convert statute miles to kilometres (rounded to nearest integer)."""
    if miles is None:
        return None
    return int(round(miles * MILES_TO_KM))


def parse_price_gbp(raw: str) -> Optional[float]:
    """'£45,000' | '45000' | '45,000 GBP' → 45000.0"""
    return parse_price_sgd(raw)  # same stripping logic


def parse_price_pence(raw) -> Optional[float]:
    """Pence integer (Car&Classic API) → GBP float. 4500000 → 45000.0"""
    try:
        pence = int(raw)
        return pence / 100.0 if pence > 0 else None
    except (TypeError, ValueError):
        return None


def parse_listing_date(raw: str) -> Optional[str]:
    """
    Parse various 'listed X ago' or absolute date strings → ISO date.
    Returns today's date for relative strings we can't resolve precisely.
    """
    if not raw:
        return None
    raw = raw.strip().lower()
    today = date.today()

    if "today" in raw or "just now" in raw or "hour" in raw or "minute" in raw:
        return today.isoformat()
    if "yesterday" in raw:
        from datetime import timedelta
        return (today - timedelta(days=1)).isoformat()

    m = re.search(r'(\d+)\s+day', raw)
    if m:
        from datetime import timedelta
        return (today - timedelta(days=int(m.group(1)))).isoformat()

    m = re.search(r'(\d+)\s+week', raw)
    if m:
        from datetime import timedelta
        return (today - timedelta(weeks=int(m.group(1)))).isoformat()

    # Try absolute date formats
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue

    return None
