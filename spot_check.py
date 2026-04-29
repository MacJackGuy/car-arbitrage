"""
Spot-check live listing pages against the DB for the four active UK sources.

Checks:
  1. Liveness — is the URL still up (no 404 / redirect-to-home)?
  2. Price match — does the live page price match DB price_gbp?
  3. C&C DBX707 gap — are there any DBX707 listings in DB? If not, live-search C&C.

Usage:
    python spot_check.py
    python spot_check.py --db data/arbitrage.db --rows 10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from typing import Optional

import requests

import config
from database.db import get_conn

# ── Config ────────────────────────────────────────────────────────────────────

ACTIVE_SOURCES = ["pistonheads", "carandclassic", "ebay_motors", "autotrader"]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_PRICE_TOLERANCE = 0.02   # 2% — ignore tiny rounding differences

# ── DB helpers ────────────────────────────────────────────────────────────────

def fetch_recent_listings(conn, source: str, n: int = 10) -> list[dict]:
    rows = conn.execute(
        """SELECT id, source_url, price_gbp, make, model, year_manufactured
           FROM listings
           WHERE source = ? AND market = 'UK'
           ORDER BY id DESC
           LIMIT ?""",
        (source, n),
    ).fetchall()
    return [dict(r) for r in rows]


def query_dbx707(conn) -> int:
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM listings
           WHERE source = 'carandclassic'
           AND (model LIKE '%DBX707%' OR model LIKE '%DBX 707%'
                OR model LIKE '%DBX%')""",
    ).fetchone()
    return row["cnt"]


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 15) -> requests.Response | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout,
                         allow_redirects=True)
        return r
    except requests.RequestException as exc:
        print(f"    [net error] {exc}")
        return None


# ── Liveness check ────────────────────────────────────────────────────────────

_DEAD_PATTERNS = [
    r"page\s+not\s+found",
    r"listing\s+(has\s+)?(ended|expired|been\s+removed|been\s+sold)",
    r"this\s+advert\s+(has\s+)?expired",
    r"sorry,\s+we\s+couldn",
    r"no\s+longer\s+available",
    r"vehicle\s+sold",
    r"advert\s+not\s+found",
]

_DEAD_REDIRECT_FRAGMENTS = [
    "/search", "/classifieds?", "/buy/", "/?q=", "/results",
]

def is_dead(resp: requests.Response, original_url: str) -> bool:
    if resp.status_code == 404:
        return True
    if resp.status_code >= 400:
        return True
    # Significant redirect away from the listing
    final = resp.url.rstrip("/").lower()
    orig  = original_url.rstrip("/").lower()
    if final != orig:
        for frag in _DEAD_REDIRECT_FRAGMENTS:
            if frag in final and frag not in orig:
                return True
        # Redirected to root domain (homepage)
        from urllib.parse import urlparse
        if urlparse(final).path in ("", "/"):
            return True
    # Dead-content patterns
    text = resp.text.lower()
    if any(re.search(p, text) for p in _DEAD_PATTERNS):
        return True
    return False


# ── Price extraction per source ───────────────────────────────────────────────

def _extract_price_pistonheads(text: str) -> Optional[float]:
    """Pull price from __NEXT_DATA__ Apollo state."""
    m = re.search(r'id="__NEXT_DATA__"[^>]*>(\{.+?\})</script>', text, re.DOTALL)
    if not m:
        return None
    try:
        nd = json.loads(m.group(1))
        apollo = (nd.get("props", {}).get("pageProps", {})
                    .get("__APOLLO_STATE__", {}))
        for key, val in apollo.items():
            if key.startswith("Advert:") and "price" in val:
                p = val["price"]
                return float(p) if p else None
    except Exception:
        pass
    # Fallback: first price pattern in JSON blob
    pm = re.search(r'"price"\s*:\s*(\d+)', text)
    return float(pm.group(1)) if pm else None


def _extract_price_carandclassic(text: str) -> Optional[float]:
    """Pull price from Inertia.js script tag."""
    for script in re.findall(r'<script[^>]*>(\{.+?\})</script>', text, re.DOTALL):
        if '"component"' in script and '"props"' in script:
            try:
                data = json.loads(script)
                props = data.get("props", {})
                # Try common paths
                for path in [
                    lambda p: p["listing"]["price"],
                    lambda p: p["car"]["price"],
                    lambda p: p["data"]["price"],
                ]:
                    try:
                        val = path(props)
                        if val:
                            return float(str(val).replace(",", "").replace("£", "").strip())
                    except (KeyError, TypeError):
                        pass
            except Exception:
                pass
    # Fallback: og:price or meta
    m = re.search(r'"price"\s*:\s*"?(\d[\d,\.]+)"?', text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _extract_price_ebay(text: str) -> Optional[float]:
    """Extract price from eBay listing page."""
    # itemprop
    m = re.search(r'itemprop="price"\s+content="([\d\.]+)"', text)
    if m:
        return float(m.group(1))
    # data-testid price primary
    m = re.search(r'data-testid="x-price-primary"[^>]*>.*?£\s*([\d,\.]+)', text, re.DOTALL)
    if m:
        return float(m.group(1).replace(",", ""))
    # Plain price span fallback
    m = re.search(r'class="[^"]*notranslate[^"]*"[^>]*>£\s*([\d,\.]+)', text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _extract_price_autotrader(text: str) -> Optional[float]:
    """AutoTrader is a React SPA — static HTML may have price in a JSON blob."""
    # Try window.__INITIAL_STATE__ or similar
    m = re.search(r'"price"\s*:\s*\{\s*"value"\s*:\s*"?(\d[\d,\.]+)"?', text)
    if m:
        return float(m.group(1).replace(",", ""))
    # £ price pattern in script tags
    m = re.search(r'(?:advertPrice|price)["\s:]+(?:£|GBP)?\s*"?([\d,]+)"?', text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


_PRICE_EXTRACTORS = {
    "pistonheads":   _extract_price_pistonheads,
    "carandclassic": _extract_price_carandclassic,
    "ebay_motors":   _extract_price_ebay,
    "autotrader":    _extract_price_autotrader,
}


# ── DBX707 live search on C&C ─────────────────────────────────────────────────

def live_search_cc_dbx707() -> dict:
    """Search Car & Classic for DBX707. Returns result dict."""
    url = "https://www.carandclassic.com/search?make=aston-martin&model=dbx707"
    resp = _get(url)
    if not resp:
        return {"searched": url, "result": "network_error", "count": None}
    # Parse Inertia JSON for result count
    count = None
    for script in re.findall(r'<script[^>]*>(\{.+?\})</script>', resp.text, re.DOTALL):
        if '"component"' in script and '"searchResults"' in script:
            try:
                data  = json.loads(script)
                sr    = (data.get("props", {}).get("searchResults", {})
                             or data.get("props", {}).get("data", {}).get("searchResults", {}))
                total = sr.get("total") or sr.get("pagination", {}).get("total")
                if total is not None:
                    count = int(total)
                    break
                items = sr.get("data", [])
                count = len(items)
                break
            except Exception:
                pass
    return {
        "searched": url,
        "http_status": resp.status_code,
        "result": "found" if count else "absent_or_parse_failed",
        "count": count,
    }


# ── Main spot-check logic ─────────────────────────────────────────────────────

def spot_check_source(source: str, rows: int, conn) -> list[dict]:
    listings = fetch_recent_listings(conn, source, rows)
    if not listings:
        print(f"  No listings found in DB for {source}")
        return []

    results = []
    for listing in listings:
        lid    = listing["id"]
        url    = listing["source_url"]
        db_gbp = listing["price_gbp"]
        label  = f"{listing.get('make','')} {listing.get('model','')} {listing.get('year_manufactured','') or ''}".strip()

        print(f"  [{lid}] {label[:40]:<40} ", end="", flush=True)

        resp = _get(url)
        time.sleep(0.5)  # polite crawl rate

        if resp is None:
            status = "network_error"
            live_price = None
            print("NETWORK ERROR")
        elif is_dead(resp, url):
            status = "dead"
            live_price = None
            print(f"DEAD ({resp.status_code})")
        else:
            # Alive — extract live price
            extractor  = _PRICE_EXTRACTORS.get(source)
            live_price = extractor(resp.text) if extractor else None

            if live_price is None:
                status = "live_price_unknown"
                print(f"live  price=n/a  db=£{db_gbp}")
            elif db_gbp is None:
                status = "live"
                print(f"live  price=£{live_price:.0f}  db=null")
            else:
                diff = abs(live_price - db_gbp) / max(db_gbp, 1)
                if diff > _PRICE_TOLERANCE:
                    status = "price_mismatch"
                    print(f"PRICE MISMATCH  live=£{live_price:.0f}  db=£{db_gbp:.0f}  Δ{diff*100:.1f}%")
                else:
                    status = "live"
                    print(f"live  £{live_price:.0f} ✓")

        results.append({
            "source":     source,
            "listing_id": lid,
            "url":        url,
            "label":      label,
            "status":     status,
            "db_price":   db_gbp,
            "live_price": live_price,
        })

    return results


# ── Entry point ───────────────────────────────────────────────────────────────

def main(db_path: str, rows: int) -> None:
    report: dict = {
        "generated_at": datetime.now().isoformat(),
        "db_path":      db_path,
        "rows_per_source": rows,
        "results":      [],
        "dbx707_check": None,
        "summary":      {},
    }

    with get_conn(db_path) as conn:

        # ── Per-source checks ───────────────────────────────────────────────
        for source in ACTIVE_SOURCES:
            print(f"\n{'='*60}")
            print(f"  {source.upper()}  (last {rows} listings)")
            print(f"{'='*60}")
            source_results = spot_check_source(source, rows, conn)
            report["results"].extend(source_results)

            counts: dict[str, int] = {}
            for r in source_results:
                counts[r["status"]] = counts.get(r["status"], 0) + 1
            report["summary"][source] = counts

        # ── C&C DBX707 gap check ────────────────────────────────────────────
        print(f"\n{'='*60}")
        print("  C&C DBX707 GAP CHECK")
        print(f"{'='*60}")
        dbx_count = query_dbx707(conn)
        print(f"  DB rows (carandclassic, model~DBX): {dbx_count}")

        if dbx_count == 0:
            print("  → None in DB. Live-searching Car & Classic for DBX707…")
            cc_result = live_search_cc_dbx707()
            print(f"  → Search URL:   {cc_result['searched']}")
            print(f"  → HTTP status:  {cc_result.get('http_status','?')}")
            print(f"  → Live count:   {cc_result['count']}")
            print(f"  → Verdict:      {'SCRAPING GAP' if (cc_result['count'] or 0) > 0 else 'Genuinely absent on C&C'}")
        else:
            cc_result = {"result": "in_db", "count": dbx_count}
            print(f"  → {dbx_count} DBX listings found in DB — no gap.")

        report["dbx707_check"] = {"db_count": dbx_count, **cc_result}

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    all_results = report["results"]
    total = len(all_results)
    by_status: dict[str, int] = {}
    for r in all_results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    print(f"  Total checked: {total}")
    for status, cnt in sorted(by_status.items()):
        icon = "✓" if status == "live" else ("⚠" if "mismatch" in status else "✗")
        print(f"  {icon} {status}: {cnt}")

    print("\n  By source:")
    for source, counts in report["summary"].items():
        parts = ", ".join(f"{s}={n}" for s, n in counts.items())
        print(f"    {source:<20} {parts}")

    # ── Save JSON ────────────────────────────────────────────────────────────
    out_path = "spot_check_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Full report saved → {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Spot-check live UK listing pages vs DB")
    p.add_argument("--db",   default=config.DB_PATH, help="SQLite DB path")
    p.add_argument("--rows", default=10, type=int,   help="Listings to check per source")
    args = p.parse_args()
    main(db_path=args.db, rows=args.rows)
