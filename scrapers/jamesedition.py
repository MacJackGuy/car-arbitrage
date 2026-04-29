"""
JamesEdition scraper (jamesedition.com).

DISABLED — Cloudflare blocks IVPN ASN 9009 (all IVPN exit nodes share this ASN).
Needs a residential proxy to bypass. File kept for future reactivation.
To re-enable: uncomment JamesEditionScraper in uk_runner.py and scrapers/__init__.py.

Site characteristics
─────────────────────
• Luxury goods marketplace — Cloudflare JS challenge; Playwright with stealth.
• URL structure: /cars/<make>/ and /cars/<make>/<model>/
• Prices listed in the seller's local currency (USD most common for US
  consignors; GBP for UK; EUR for Europe; SGD, CHF etc. also seen).
  ALL prices are converted to GBP before storage using scrapers.currency.
• Next.js frontend — try __NEXT_DATA__ first, then XHR interception,
  then DOM card harvesting as fallback.
• Pagination: ?page=N on make/model pages.
• JSON-LD (`<script type="application/ld+json">`) often present on
  individual listing pages — rich structured data when available.

Cloudflare note
───────────────
Best-effort stealth.  A residential proxy meaningfully improves pass rate.
The scraper logs failures and continues with other makes.

Delta strategy
──────────────
Sort param appended where supported.  Stop on first known listing ID.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

import config
from scrapers.base import BaseScraper
from scrapers.currency import detect_currency, price_to_gbp
from scrapers.parsers import (
    clean,
    is_target_model,
    miles_to_km,
    parse_mileage_miles,
    parse_price_gbp,
    parse_year,
)
from scrapers.uk_targets import JAMESEDITION_MAKE, search_pairs

log = logging.getLogger(__name__)

_BASE = "https://www.jamesedition.com"

_API_PATTERNS = [
    "/api/listings",
    "/api/cars",
    "jamesedition.com/api",
    "/graphql",
    "/search",
]
_MAX_PAGES = 15


class JamesEditionScraper(BaseScraper):
    SOURCE = "jamesedition"
    MARKET = "UK"

    async def run(self, known_ids: Optional[set[str]] = None) -> tuple[int, int]:
        if known_ids is None and self.run_type == "incremental":
            known_ids = self.get_known_ids()

        seen_makes: set[str] = set()
        for canonical_make, model in search_pairs():
            # JamesEdition: scrape by make only (model filtering is client-side)
            if canonical_make in seen_makes:
                continue
            seen_makes.add(canonical_make)

            slug = JAMESEDITION_MAKE.get(canonical_make)
            if not slug:
                log.warning("[je] No slug for %s — skipping", canonical_make)
                continue

            log.info("[je] Scraping make: %s (%s)", canonical_make, slug)
            await self._scrape_make(canonical_make, slug, known_ids)
            await self.delay()

        return self._new, self._updated

    # ── Make scraper ──────────────────────────────────────────────────────────

    async def _scrape_make(
        self,
        canonical_make: str,
        slug: str,
        known_ids: Optional[set[str]],
    ) -> None:
        intercepted: list[dict] = []

        async with await self.new_context() as ctx:
            page = await ctx.new_page()

            async def on_response(resp):
                try:
                    if any(p in resp.url for p in _API_PATTERNS):
                        data = await resp.json()
                        intercepted.append(data)
                except Exception:
                    pass

            page.on("response", on_response)

            for page_n in range(1, _MAX_PAGES + 1):
                url = f"{_BASE}/cars/{slug}/" + (f"?page={page_n}" if page_n > 1 else "")
                if not await self.safe_goto(page, url, wait="domcontentloaded", timeout_ms=45_000):
                    break

                if await _is_cf_challenge(page):
                    log.warning("[je] Cloudflare challenge on %s page %d", canonical_make, page_n)
                    break

                await _dismiss_cookies(page)
                await asyncio.sleep(3)

                # ── Try __NEXT_DATA__ first ────────────────────────────────
                next_items = await self._extract_next_data_listings(page)
                if next_items:
                    stop = await self._process_items(next_items, canonical_make, known_ids)
                    if stop or len(next_items) < 12:
                        break
                    page_n += 1
                    await self.delay(min_s=6, max_s=18)
                    continue

                # ── Try intercepted XHR ────────────────────────────────────
                xhr_items: list[dict] = []
                for payload in intercepted:
                    xhr_items.extend(_iter_items(payload))
                intercepted.clear()

                if xhr_items:
                    stop = await self._process_items(xhr_items, canonical_make, known_ids)
                    if stop or len(xhr_items) < 12:
                        break
                    page_n += 1
                    await self.delay(min_s=6, max_s=18)
                    continue

                # ── DOM fallback ───────────────────────────────────────────
                listing_urls = await self._harvest_listing_urls(page)
                if not listing_urls:
                    log.info("[je] No listings found on %s page %d", canonical_make, page_n)
                    break

                stop = False
                for listing_url in listing_urls:
                    lid = _url_to_id(listing_url)
                    if known_ids and lid and lid in known_ids:
                        stop = True
                        break
                    detail = await self._scrape_listing_page(ctx, listing_url, canonical_make)
                    if detail and is_target_model(canonical_make, detail.get("model", "")):
                        self.save_listing(detail)
                    await self.delay(min_s=5, max_s=15)

                if stop:
                    break

                # Scroll to trigger lazy load
                prev_h = await page.evaluate("document.body.scrollHeight")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
                if await page.evaluate("document.body.scrollHeight") == prev_h:
                    break

            await page.close()

    async def _process_items(
        self,
        items: list[dict],
        canonical_make: str,
        known_ids: Optional[set[str]],
    ) -> bool:
        """Normalise and save a list of listing items. Returns True if delta stop triggered."""
        for item in items:
            lid = _extract_id(item)
            if known_ids and lid and lid in known_ids:
                log.info("[je] Hit known id %s — delta caught up", lid)
                return True
            listing = self._normalise(item, canonical_make)
            if listing and is_target_model(canonical_make, listing.get("model", "")):
                self.save_listing(listing)
        return False

    # ── __NEXT_DATA__ extraction ──────────────────────────────────────────────

    async def _extract_next_data_listings(self, page) -> list[dict]:
        """Pull listing array from Next.js hydration data if available."""
        try:
            data = await page.evaluate("""
                () => {
                    try {
                        const nd = JSON.parse(
                            document.getElementById('__NEXT_DATA__').textContent
                        );
                        const pp = nd?.props?.pageProps || {};
                        // Try common keys
                        for (const k of ['listings', 'cars', 'vehicles', 'items', 'data', 'results']) {
                            if (Array.isArray(pp[k])) return pp[k];
                            if (pp[k]?.data && Array.isArray(pp[k].data)) return pp[k].data;
                        }
                        return null;
                    } catch(e) { return null; }
                }
            """)
            return data or []
        except Exception:
            return []

    # ── Individual listing page ───────────────────────────────────────────────

    async def _scrape_listing_page(
        self, ctx, url: str, canonical_make: str
    ) -> Optional[dict]:
        """Scrape a single JamesEdition listing page."""
        lid = _url_to_id(url)
        page = await ctx.new_page()
        try:
            if not await self.safe_goto(page, url, wait="domcontentloaded", timeout_ms=40_000):
                return None
            await asyncio.sleep(2)

            # Try JSON-LD structured data first (richest)
            ld_data = await self._extract_json_ld(page)
            if ld_data:
                item = _json_ld_to_item(ld_data, url, lid)
                if item:
                    return self._normalise(item, canonical_make)

            # Try __NEXT_DATA__
            next_data = await page.evaluate("""
                () => {
                    try {
                        const nd = JSON.parse(document.getElementById('__NEXT_DATA__').textContent);
                        const pp = nd?.props?.pageProps || {};
                        for (const k of ['listing', 'car', 'vehicle', 'data', 'detail']) {
                            if (pp[k] && typeof pp[k] === 'object') return pp[k];
                        }
                        return null;
                    } catch(e) { return null; }
                }
            """)
            if next_data:
                return self._normalise(next_data, canonical_make)

            # DOM fallback
            return await self._dom_detail(page, url, lid, canonical_make)

        except Exception as exc:
            self.log_error(f"JE listing page {url}: {exc}")
            return None
        finally:
            await page.close()

    async def _extract_json_ld(self, page) -> Optional[dict]:
        """Extract application/ld+json data from the page."""
        try:
            scripts = await page.eval_on_selector_all(
                'script[type="application/ld+json"]',
                "els => els.map(e => e.textContent)"
            )
            for script in scripts:
                try:
                    data = json.loads(script)
                    if isinstance(data, list):
                        data = data[0]
                    # Vehicle schema
                    if isinstance(data, dict) and data.get("@type") in ("Car", "Vehicle", "Product"):
                        return data
                except Exception:
                    continue
        except Exception:
            pass
        return None

    async def _dom_detail(
        self, page, url: str, lid: str, canonical_make: str
    ) -> Optional[dict]:
        """Manual DOM extraction fallback for a JamesEdition listing page."""
        try:
            title  = clean(await self._text(page, "h1"))
            page_text = await page.inner_text("body")

            # Price — JamesEdition shows it prominently, possibly with currency symbol
            price_raw = await self._text(page,
                "[class*='price'], [data-testid='price'], h2[class*='amount']")
            if not price_raw:
                m = re.search(r'[\$£€][\d,]+', page_text or "")
                price_raw = m.group(0) if m else ""

            currency, amount = detect_currency(price_raw) if price_raw else ("GBP", None)
            price_gbp = price_to_gbp(amount, currency, self.db_path)

            year  = parse_year(re.search(r'\b(19|20)\d{2}\b', page_text or "").group() if re.search(r'\b(19|20)\d{2}\b', page_text or "") else "")
            miles_m = re.search(r'([\d,]+)\s*miles?', page_text or "", re.IGNORECASE)
            miles = parse_mileage_miles(miles_m.group(0) if miles_m else "")

            model = re.sub(re.escape(canonical_make), "", title or "",
                           flags=re.IGNORECASE).strip()[:80]

            images = await page.eval_on_selector_all(
                "img[src*='jamesedition'], img[class*='photo'], [class*='gallery'] img",
                "els => [...new Set(els.map(e => e.src).filter(Boolean))]"
            )

            # Auction estimate / bid from DOM
            estimate_text = await self._text(
                page,
                "[class*='estimate'], [class*='auction-price'], [data-testid='estimate'],"
                "[class*='bid'], [data-testid='current-bid']",
            ) or ""
            low_gbp, high_gbp = _parse_estimate_range(estimate_text, "GBP", self.db_path)
            bid_raw = await self._text(
                page, "[class*='current-bid'], [data-testid='current-bid'], [class*='live-bid']"
            ) or ""
            bid_gbp = parse_price_gbp(bid_raw) if bid_raw else None

            price_gbp = price_gbp or (
                round((low_gbp + high_gbp) / 2, 2) if low_gbp and high_gbp
                else low_gbp or bid_gbp
            )

            return {
                "source":           self.SOURCE,
                "market":           self.MARKET,
                "source_url":       url,
                "source_listing_id": lid,
                "make":             canonical_make,
                "model":            model,
                "year_manufactured": year,
                "price_gbp":        price_gbp,
                "price_low_gbp":    low_gbp,
                "price_high_gbp":   high_gbp,
                "current_bid_gbp":  bid_gbp,
                "mileage_miles":    miles,
                "mileage_km":       miles_to_km(miles),
                "seller_type":      "dealer",
                "is_direct_owner":  0,
                "image_urls":       json.dumps(images[:30]),
                "description_text": clean(await self._text(page,
                    "[class*='description'], [class*='detail-text']")),
            }
        except Exception as exc:
            self.log_error(f"JE DOM detail {url}: {exc}")
            return None

    # ── Normalisation ─────────────────────────────────────────────────────────

    def _normalise(self, item: dict, canonical_make: str) -> Optional[dict]:
        """
        Map a JamesEdition listing dict (from any data source) to our schema.
        Multi-currency prices are detected and converted to GBP.
        """
        try:
            lid  = _extract_id(item)
            slug = item.get("slug") or item.get("permalink") or lid or ""
            url  = item.get("url") or item.get("permalink") or ""
            if not url or not url.startswith("http"):
                url = f"{_BASE}/cars/{slug}" if slug else f"{_BASE}/cars/"

            # Title
            title = clean(
                item.get("title") or item.get("name") or item.get("headline") or
                f"{item.get('make','')} {item.get('model','')} {item.get('year','')}".strip()
            ) or ""

            # Model
            model_raw = clean(item.get("model") or item.get("model_name") or "")
            if not model_raw:
                model_raw = re.sub(re.escape(canonical_make), "", title,
                                   flags=re.IGNORECASE).strip()
            model = model_raw[:80]

            # ── Price with currency detection ─────────────────────────────
            price_gbp = self._resolve_price(item)

            # ── Auction estimate + current bid (multi-currency) ───────────
            # Determine currency once so auction sub-fields use the same rate
            raw_currency = str(item.get("currency") or item.get("price_currency") or "USD")
            low_gbp, high_gbp, bid_gbp = self._resolve_auction_prices(item, raw_currency)
            # If estimate midpoint is a better price proxy than a missing price
            if price_gbp is None and low_gbp and high_gbp:
                price_gbp = round((low_gbp + high_gbp) / 2, 2)
            elif price_gbp is None and bid_gbp:
                price_gbp = bid_gbp

            # Year
            year = parse_year(str(item.get("year") or item.get("manufactured_year") or ""))

            # Mileage
            miles_raw = item.get("mileage") or item.get("odometer") or item.get("miles") or ""
            miles = parse_mileage_miles(str(miles_raw))
            if miles is None and isinstance(miles_raw, (int, float)) and miles_raw > 0:
                miles = int(miles_raw)

            # Colour
            colour = clean(item.get("colour") or item.get("color") or
                          item.get("exterior_color") or "")

            # Images
            photos = item.get("images") or item.get("photos") or item.get("gallery") or []
            image_urls: list[str] = []
            for p in photos[:30]:
                src = (p.get("url") or p.get("src") or p.get("thumbnail")
                       if isinstance(p, dict) else str(p))
                if src:
                    image_urls.append(src)

            # Seller
            seller_raw = item.get("seller") or item.get("dealer") or {}
            seller_name = clean(str(seller_raw.get("name") or seller_raw.get("title") or "")
                                if isinstance(seller_raw, dict) else "")
            # JamesEdition is a mix of private and dealer; default dealer
            seller_type_raw = str(
                (seller_raw.get("type") or seller_raw.get("seller_type") or "dealer")
                if isinstance(seller_raw, dict) else "dealer"
            ).lower()
            seller_type = "private" if "private" in seller_type_raw else "dealer"

            desc = clean(item.get("description") or item.get("details") or "")
            date_raw = item.get("created_at") or item.get("listed_at") or ""

            return {
                "source":           self.SOURCE,
                "market":           self.MARKET,
                "source_url":       url,
                "source_listing_id": lid,
                "make":             canonical_make,
                "model":            model,
                "year_manufactured": year,
                "price_gbp":        price_gbp,
                "price_low_gbp":    low_gbp,
                "price_high_gbp":   high_gbp,
                "current_bid_gbp":  bid_gbp,
                "mileage_miles":    miles,
                "mileage_km":       miles_to_km(miles),
                "colour":           colour,
                "seller_type":      seller_type,
                "is_direct_owner":  int(seller_type == "private"),
                "seller_name":      seller_name,
                "description_text": desc,
                "image_urls":       json.dumps(image_urls),
            }
        except Exception as exc:
            self.log_error(f"JE normalise {item.get('id','?')}: {exc}")
            return None

    def _resolve_auction_prices(
        self, item: dict, currency: str
    ) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Extract (price_low_gbp, price_high_gbp, current_bid_gbp) from a JE item.
        All amounts converted to GBP using the item-level currency where needed.

        Handles these API shapes:
          { "estimate": {"low": 150000, "high": 200000}, "currency": "USD" }
          { "estimate": "USD 150,000 – USD 200,000" }
          { "estimate_low": 150000, "estimate_high": 200000 }
          { "current_bid": 130000 }
        """
        def _to_gbp(val) -> Optional[float]:
            if val is None:
                return None
            if isinstance(val, (int, float)):
                return price_to_gbp(float(val) if val > 0 else None, currency, self.db_path)
            # String: try to detect embedded currency symbol first
            detected_cur, amount = detect_currency(str(val))
            if detected_cur != "GBP" or any(c in str(val) for c in ("$", "€", "¥", "CHF")):
                return price_to_gbp(amount, detected_cur, self.db_path)
            return price_to_gbp(amount, currency, self.db_path)

        # Nested estimate object
        est = item.get("estimate") or {}
        if isinstance(est, dict):
            low_raw  = est.get("low")  or est.get("min")
            high_raw = est.get("high") or est.get("max")
        elif isinstance(est, str) and est:
            # String like "USD 150,000 – USD 200,000" or "£45k–£65k"
            low_raw = high_raw = est
        else:
            low_raw = high_raw = None

        # Flat-key overrides (take precedence when nested estimate missing)
        low_raw  = low_raw  or item.get("estimate_low")  or item.get("price_low")
        high_raw = high_raw or item.get("estimate_high") or item.get("price_high")
        bid_raw  = (item.get("current_bid") or item.get("currentBid")
                    or item.get("highest_bid") or item.get("live_bid"))

        # Parse string ranges (e.g. "£45k–£65k")
        if isinstance(low_raw, str):
            low_gbp, high_gbp = _parse_estimate_range(low_raw, currency, self.db_path)
        else:
            low_gbp  = _to_gbp(low_raw)
            high_gbp = _to_gbp(high_raw) if not isinstance(high_raw, str) else _to_gbp(high_raw)

        bid_gbp = _to_gbp(bid_raw)
        return low_gbp, high_gbp, bid_gbp

    def _resolve_price(self, item: dict) -> Optional[float]:
        """
        Detect currency and convert to GBP.
        JamesEdition may provide price as:
          { "price": 125000, "currency": "USD" }
          { "price": "USD 125,000" }
          { "price": "$125,000" }
          { "price": 125000 }   (assume USD if no currency key)
        """
        raw_price = item.get("price") or item.get("asking_price") or ""
        currency  = str(item.get("currency") or item.get("price_currency") or "")

        if isinstance(raw_price, (int, float)):
            amount = float(raw_price) if raw_price > 0 else None
            if not currency:
                # JamesEdition defaults to USD when currency not specified
                currency = "USD"
        else:
            detected_cur, amount = detect_currency(str(raw_price))
            if not currency:
                currency = detected_cur

        return price_to_gbp(amount, currency or "USD", self.db_path)

    # ── DOM helpers ───────────────────────────────────────────────────────────

    async def _harvest_listing_urls(self, page) -> list[str]:
        """Collect listing URLs from the make page DOM."""
        try:
            hrefs = await page.eval_on_selector_all(
                'a[href*="/cars/"]',
                "els => [...new Set(els.map(e => e.href).filter("
                "h => /\\/cars\\/[^/]+\\/[^/]+\\//.test(h)))]"
            )
            return [h for h in hrefs if "jamesedition.com" in h]
        except Exception:
            return []

    async def _text(self, page, selector: str) -> Optional[str]:
        try:
            for sel in selector.split(","):
                el = await page.query_selector(sel.strip())
                if el:
                    t = await el.inner_text()
                    if t and t.strip():
                        return t.strip()
        except Exception:
            pass
        return None


# ── Module helpers ────────────────────────────────────────────────────────────

def _iter_items(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "listings", "cars", "vehicles", "results", "items"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                for kk in ("data", "items", "results"):
                    vv = v.get(kk)
                    if isinstance(vv, list):
                        return vv
    return []


def _extract_id(item: dict) -> Optional[str]:
    for k in ("id", "listing_id", "listingId", "uuid", "slug"):
        v = item.get(k)
        if v:
            return str(v)
    return None


def _url_to_id(url: str) -> Optional[str]:
    """Extract listing ID or slug from a JamesEdition URL."""
    # /cars/ferrari/f430-2006-123456/ → last non-empty segment
    parts = [p for p in url.rstrip("/").split("/") if p]
    return parts[-1] if parts else None


def _json_ld_to_item(ld: dict, url: str, lid: Optional[str]) -> Optional[dict]:
    """Convert JSON-LD Car/Vehicle schema to our normalisation input dict."""
    try:
        offers = ld.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0]

        # JSON-LD AuctionEvent or priceSpecification may carry estimate range
        price_spec = offers.get("priceSpecification") or {}
        if isinstance(price_spec, list):
            price_spec = price_spec[0] if price_spec else {}

        return {
            "id":           lid,
            "url":          url,
            "title":        ld.get("name") or "",
            "model":        ld.get("model") or "",
            "year":         ld.get("vehicleModelDate") or ld.get("modelDate"),
            "price":        offers.get("price"),
            "currency":     offers.get("priceCurrency") or "GBP",
            "mileage":      (ld.get("mileageFromOdometer") or {}).get("value"),
            "colour":       ld.get("color"),
            "description":  ld.get("description") or "",
            "images":       [{"url": u} for u in (ld.get("image") or [])
                             if isinstance(u, str)],
            # Auction estimate from priceSpecification or offer extensions
            "estimate": {
                "low":  price_spec.get("minPrice") or offers.get("lowPrice"),
                "high": price_spec.get("maxPrice") or offers.get("highPrice"),
            },
            "current_bid":  offers.get("currentBid") or offers.get("current_bid"),
        }
    except Exception:
        return None


def _parse_estimate_range(
    text: str, currency: str, db_path: Optional[str] = None
) -> tuple[Optional[float], Optional[float]]:
    """
    Parse an estimate range string into (low_gbp, high_gbp).
    Handles formats like:
      "£45,000 – £65,000", "$150k – $200k", "USD 150,000 to USD 200,000"
    Single values return (value, None).
    """
    if not text:
        return None, None

    import re as _re

    def _val(s: str, cur: str) -> Optional[float]:
        s = s.strip().replace(",", "")
        # k/K suffix
        if s.lower().endswith("k"):
            try:
                raw = float(s[:-1]) * 1000
            except ValueError:
                return None
        else:
            try:
                raw = float(s) if s else None
            except ValueError:
                return None
        if raw is None or raw <= 0:
            return None
        return price_to_gbp(raw, cur, db_path)

    # Strip common prefixes: "Estimate:", "Guide price:", etc.
    text = _re.sub(r'^[A-Za-z\s]+:\s*', '', text.strip())

    # Try to find two numbers separated by a range separator
    m = _re.search(
        r'([A-Z]{3}\s*)?'          # optional ISO code before first number
        r'[£$€]?\s*([\d,]+k?)'     # first number
        r'\s*[-–—to]+\s*'          # separator
        r'([A-Z]{3}\s*)?'          # optional ISO code before second number
        r'[£$€]?\s*([\d,]+k?)',    # second number
        text, _re.IGNORECASE,
    )
    if m:
        cur1 = (m.group(1) or "").strip().upper() or currency
        cur2 = (m.group(3) or "").strip().upper() or cur1
        low  = _val(m.group(2), cur1)
        high = _val(m.group(4), cur2)
        return low, high

    # Single value
    m2 = _re.search(r'([A-Z]{3}\s*)?[£$€]?\s*([\d,]+k?)', text, _re.IGNORECASE)
    if m2:
        cur = (m2.group(1) or "").strip().upper() or currency
        return _val(m2.group(2), cur), None

    return None, None


async def _is_cf_challenge(page) -> bool:
    try:
        t = await page.title()
        return any(k in t.lower() for k in ("just a moment", "challenge", "checking"))
    except Exception:
        return False


async def _dismiss_cookies(page) -> None:
    try:
        btn = await page.query_selector(
            "button:has-text('Accept'), button:has-text('Accept all'), #cookie-consent-accept"
        )
        if btn and await btn.is_visible():
            await btn.click()
            await asyncio.sleep(0.8)
    except Exception:
        pass
