"""
Car & Classic scraper (carandclassic.com/search).

Site characteristics
─────────────────────
• React SPA — server renders a `<script>` tag containing Inertia page JSON
  with component "search/index/Page". The old /cat/3/ URL and data-page
  attribute approach broke when C&C migrated to a new React frontend.
• Search URL: /search?make={slug}&model={slug}&page={n}
• Price is in PENCE (divide by 100 to get GBP).
• Mileage unit in attributes.mileage.unit — "mi" or "km".
• Seller type inferred from listing URL prefix:
    /car/C{id}  → dealer
    /l/C{id}    → private
• Pagination: searchResults.pagination.last_page
• totalImageCount field still present in search result JSON.

Delta strategy
──────────────
Newest-first sort not supported on new URL; results come back by relevance.
Stop pagination when a known listing ID appears.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

import config
from scrapers.base import BaseScraper
from scrapers.parsers import (
    clean,
    miles_to_km,
    parse_mileage_miles,
    parse_price_pence,
    parse_year,
)
from scrapers.uk_targets import CARANDCLASSIC_MAKE

log = logging.getLogger(__name__)

_BASE = "https://www.carandclassic.com"
_SEARCH = f"{_BASE}/search"


class CarAndClassicScraper(BaseScraper):
    SOURCE = "carandclassic"
    MARKET = "UK"

    async def run(self, known_ids: Optional[set[str]] = None) -> tuple[int, int]:
        if known_ids is None and self.run_type == "incremental":
            known_ids = self.get_known_ids()

        for canonical_make, site_make in CARANDCLASSIC_MAKE.items():
            log.info("[c&c] Searching %s (all models)", canonical_make)
            await self._scrape(canonical_make, site_make, None, known_ids)
            await self.delay()

        return self._new, self._updated

    # ── Pagination ────────────────────────────────────────────────────────────

    async def _scrape(
        self,
        canonical_make: str,
        site_make: str,
        model: Optional[str],
        known_ids: Optional[set[str]],
    ) -> None:
        page_n = 1

        async with await self.new_context() as ctx:
            page = await ctx.new_page()

            while True:
                url = self._search_url(site_make, model, page_n)
                if not await self.safe_goto(page, url, wait="domcontentloaded"):
                    break

                await self._dismiss_cookies(page)

                data = await self._extract_page_json(page)
                if data is None:
                    log.warning("[c&c] No Inertia JSON at %s", url)
                    break

                search_results = data.get("props", {}).get("searchResults") or {}
                items = search_results.get("data", [])
                if not items:
                    log.info("[c&c] No listings on page %d — stopping", page_n)
                    break

                stop = False
                for item in items:
                    lid = str(item.get("id") or item.get("slug") or "")
                    if not lid:
                        continue
                    if known_ids and lid in known_ids:
                        log.info("[c&c] Hit known id %s — delta caught up", lid)
                        stop = True
                        break

                    listing = self._normalise(item, canonical_make)
                    if listing:
                        self.save_listing(listing)

                if stop:
                    break

                pagination = search_results.get("pagination") or {}
                last_page = pagination.get("last_page", 1)
                if page_n >= last_page:
                    break

                page_n += 1
                await self.delay(min_s=5, max_s=15)

            await page.close()

    # ── Data extraction ───────────────────────────────────────────────────────

    async def _extract_page_json(self, page) -> Optional[dict]:
        """Extract Inertia page JSON from an inline <script> tag."""
        try:
            return await page.evaluate("""
                () => {
                    for (const el of document.querySelectorAll('script:not([src])')) {
                        const t = el.textContent.trim();
                        if (!t.startsWith('{')) continue;
                        try {
                            const d = JSON.parse(t);
                            if (d.component && d.props) return d;
                        } catch(e) {}
                    }
                    return null;
                }
            """)
        except Exception:
            return None

    def _normalise(self, item: dict, canonical_make: str) -> Optional[dict]:
        """Map a Car&Classic listing JSON object to our schema."""
        try:
            lid = str(item.get("id") or "")
            slug = item.get("slug") or lid

            # URL prefix tells us seller type
            url_path = item.get("url") or f"/car/C{slug}"
            seller_type = "private" if url_path.startswith("/l/") else "dealer"
            full_url = f"{_BASE}{url_path}" if url_path.startswith("/") else url_path

            # Price: pence → GBP
            price_raw = item.get("price") or {}
            price_pence = price_raw.get("value") if isinstance(price_raw, dict) else price_raw
            price_gbp = parse_price_pence(price_pence)

            # Title / model
            title = clean(item.get("title") or item.get("name") or "")
            model_raw = clean(item.get("model") or "")
            if not model_raw:
                model_raw = re.sub(re.escape(canonical_make), "", title or "",
                                   flags=re.IGNORECASE).strip()
            model = model_raw[:80]

            # Year
            year = parse_year(str(item.get("year") or ""))

            # Mileage — unit may be "mi" or "km"
            attrs = item.get("attributes") or {}
            mileage_field = attrs.get("mileage") or {}
            if isinstance(mileage_field, dict):
                raw_val = mileage_field.get("value")
                unit = mileage_field.get("unit", "mi")
            else:
                raw_val = mileage_field
                unit = "mi"

            if raw_val is not None and unit == "km":
                km_val = int(raw_val)
                miles = int(km_val / 1.60934) if km_val else None
            else:
                miles = parse_mileage_miles(str(raw_val) if raw_val is not None else "")

            # Seller name
            seller = item.get("seller") or {}
            seller_name = clean(seller.get("name") or seller.get("username") or "")

            return {
                "source":            self.SOURCE,
                "market":            self.MARKET,
                "source_url":        full_url,
                "source_listing_id": lid or slug,
                "make":              canonical_make,
                "model":             model,
                "year_manufactured": year,
                "price_gbp":         price_gbp,
                "mileage_miles":     miles,
                "mileage_km":        miles_to_km(miles),
                "seller_type":       seller_type,
                "is_direct_owner":   int(seller_type == "private"),
                "seller_name":       seller_name,
                "image_urls":        json.dumps([]),
                "description_text":  clean(item.get("description") or ""),
                "listing_date":      None,
            }
        except Exception as exc:
            self.log_error(f"C&C normalise: {exc}")
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _search_url(make: str, model: Optional[str], page_n: int) -> str:
        params = (
            f"make={make}"
            f"&country[0]=GB"
            f"&listing_type_ex=advert"
            f"&vehicle_type=cars"
            f"&steering_position=rhd"
        )
        if model:
            params += f"&model={model.lower().replace(' ', '-')}"
        if page_n > 1:
            params += f"&page={page_n}"
        return f"{_SEARCH}?{params}"

    @staticmethod
    async def _dismiss_cookies(page) -> None:
        try:
            btn = await page.query_selector(
                "button:has-text('Accept'), button:has-text('Accept all'), #accept-cookies"
            )
            if btn and await btn.is_visible():
                await btn.click()
                await asyncio.sleep(0.8)
        except Exception:
            pass
