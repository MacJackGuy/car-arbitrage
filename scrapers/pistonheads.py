"""
PistonHeads scraper (pistonheads.com).

Site characteristics
─────────────────────
• Next.js with getStaticProps — pages are statically generated.  __NEXT_DATA__
  contains exactly 16 "featured" adverts per page snapshot.
• Pagination via ?page=N: works for model sub-pages where PH has pre-rendered
  multiple static snapshots; returns duplicate IDs when only one snapshot exists.
• Apollo state keyed as `Advert:{id}` and `Seller:{id}`.
• Seller type: `Seller:{id}.sellerType` → "Trade" | "Private".
• Mileage: `specificationData.mileage` (integer, miles).
• Price: integer GBP.

Approach: model sub-pages
─────────────────────────
1. For each make, load the make index (e.g. /buy/aston-martin) and extract
   all linked model sub-pages (e.g. /buy/aston-martin/db9-coupe).
2. For each model sub-page, paginate via ?page=N, stopping when advert IDs
   repeat (pagination exhausted or not pre-rendered) or a known ID is hit.

Delta strategy
──────────────
?page=N with sort=date-desc gives newest first.  Stop pagination when the
advert ID set on page N matches page N-1 (static cache boundary) or when a
known listing ID appears.
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
    parse_price_gbp,
    parse_year,
)
from scrapers.uk_targets import PISTONHEADS_MAKE, PISTONHEADS_MAKE_ID

log = logging.getLogger(__name__)

_BASE           = "https://www.pistonheads.com"
_BROWSE         = f"{_BASE}/buy"
_PAGE_SIZE      = 16
_MAX_PAGES_MODEL = 20   # cap per model sub-page (20 × 16 = 320 max per model)


class PistonHeadsScraper(BaseScraper):
    SOURCE = "pistonheads"
    MARKET = "UK"
    LOCALE = "en-GB"
    TIMEZONE_ID = "Europe/London"

    async def run(self, known_ids: Optional[set[str]] = None) -> tuple[int, int]:
        if known_ids is None and self.run_type == "incremental":
            known_ids = self.get_known_ids()

        for canonical_make, slug in PISTONHEADS_MAKE.items():
            log.info("[ph] %s — discovering model sub-pages", canonical_make)
            await self.restart_browser()

            async with await self.new_context() as ctx:
                page = await ctx.new_page()

                model_urls = await self._discover_models(page, slug)
                log.info("[ph] %s — %d model pages found", canonical_make, len(model_urls))

                for model_url in model_urls:
                    await self._scrape_model_page(page, model_url, canonical_make, known_ids)
                    await self.delay(min_s=4, max_s=12)

                # Sweep the make-level search to catch models not in the featured 8.
                make_id = PISTONHEADS_MAKE_ID.get(canonical_make)
                if make_id:
                    await self._scrape_make_search(page, canonical_make, make_id, known_ids)

                await page.close()

            await self.delay(min_s=20, max_s=60)

        return self._new, self._updated

    # ── Model discovery ───────────────────────────────────────────────────────

    async def _discover_models(self, page, slug: str) -> list[str]:
        """
        Load the make index page and return all model sub-page URLs.
        Model links have the form /buy/{slug}/{model} (exactly 4 path segments).
        Scrolls to the bottom before querying links because the model nav items
        below the fold are lazy-rendered and may not appear immediately.
        """
        url = f"{_BROWSE}/{slug}"
        if not await self.safe_goto(page, url, wait="domcontentloaded"):
            log.warning("[ph] Could not load make index %s", url)
            return []

        await asyncio.sleep(2)
        await self._dismiss_cookies(page)

        # Scroll to reveal all lazy-rendered model links
        for _ in range(4):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.8)

        prefix = f"/buy/{slug}/"
        try:
            links: list[str] = await page.evaluate(
                r"""(prefix) => {
                    return Array.from(document.querySelectorAll('a[href]'))
                        .map(a => a.getAttribute('href'))
                        .filter(h => h && h.startsWith(prefix))
                        .filter(h => {
                            const parts = h.replace(/\/+$/, '').split('/');
                            return parts.length === 4;
                        })
                        .filter((v, i, a) => a.indexOf(v) === i);
                }""",
                prefix,
            )
        except Exception as exc:
            self.log_error(f"PH discover_models {slug}: {exc}")
            return []

        full_urls = [f"{_BASE}{path}" for path in links]
        log.info("[ph] %s model pages: %s", slug, full_urls)
        return full_urls

    async def _scrape_make_search(
        self,
        page,
        canonical_make: str,
        make_id: int,
        known_ids: Optional[set[str]],
    ) -> None:
        """
        Scrape the make-level search page (/buy/search?make-id=N) to pick up
        listings for models that have no dedicated sub-page (e.g. F430, 488,
        812 for Ferrari).  PH only pre-renders one static snapshot (16 adverts)
        for this URL so pagination is skipped; deduplication via source_url
        handles any overlap with model sub-page results.
        """
        url = f"{_BASE}/buy/search?make-id={make_id}"
        log.info("[ph] %s — make-level sweep %s", canonical_make, url)

        if not await self.safe_goto(page, url, wait="domcontentloaded"):
            log.warning("[ph] Could not load make search %s", url)
            return

        await asyncio.sleep(1)

        apollo = await self._extract_apollo_state(page)
        if not apollo:
            log.warning("[ph] No Apollo state on make search %s", url)
            return

        adverts, sellers = self._parse_apollo(apollo)
        if not adverts:
            log.info("[ph] No adverts on make search %s", url)
            return

        for advert_key, advert in adverts.items():
            lid = str(advert.get("id") or advert_key)
            if known_ids and lid in known_ids:
                continue
            seller_ref = (advert.get("seller") or {}).get("__ref", "")
            seller_obj = sellers.get(seller_ref, {})
            listing = self._normalise(advert, seller_obj, canonical_make)
            if listing:
                self.save_listing(listing)

        log.info("[ph] %s make-level sweep done (%d adverts examined)", canonical_make, len(adverts))

    # ── Per-model scraping ────────────────────────────────────────────────────

    async def _scrape_model_page(
        self,
        page,
        model_url: str,
        canonical_make: str,
        known_ids: Optional[set[str]],
    ) -> None:
        """
        Paginate through a single model sub-page (e.g. /buy/aston-martin/db9-coupe).
        Stops when advert IDs repeat across pages (static cache boundary reached),
        a known delta ID appears, or _MAX_PAGES_MODEL is hit.
        """
        prev_ids: set[str] = set()
        page_n = 1

        while True:
            url = model_url if page_n == 1 else f"{model_url}?page={page_n}"
            if not await self.safe_goto(page, url, wait="domcontentloaded"):
                break

            apollo = await self._extract_apollo_state(page)
            if not apollo:
                log.warning("[ph] No Apollo state at %s", url)
                break

            adverts, sellers = self._parse_apollo(apollo)
            if not adverts:
                log.info("[ph] No adverts at %s — stopping", url)
                break

            curr_ids = {str(a.get("id") or k) for k, a in adverts.items()}

            # Stop if this page's IDs are the same as the previous page —
            # the static snapshot doesn't change past the pre-rendered boundary.
            if curr_ids == prev_ids:
                log.info("[ph] Page %d IDs match page %d — pagination exhausted for %s",
                         page_n, page_n - 1, model_url.split("/buy/")[1])
                break

            stop = False
            for advert_key, advert in adverts.items():
                lid = str(advert.get("id") or advert_key)
                if known_ids and lid in known_ids:
                    log.info("[ph] Hit known id %s — delta caught up", lid)
                    stop = True
                    break

                seller_ref = (advert.get("seller") or {}).get("__ref", "")
                seller_obj = sellers.get(seller_ref, {})
                listing = self._normalise(advert, seller_obj, canonical_make)
                if listing:
                    self.save_listing(listing)

            prev_ids = curr_ids

            if stop or len(adverts) < _PAGE_SIZE or page_n >= _MAX_PAGES_MODEL:
                if page_n >= _MAX_PAGES_MODEL:
                    log.info("[ph] Page cap (%d) hit for %s",
                             _MAX_PAGES_MODEL, model_url.split("/buy/")[1])
                break

            page_n += 1
            await self.delay(min_s=3, max_s=8)

    # ── Data extraction ───────────────────────────────────────────────────────

    async def _extract_apollo_state(self, page) -> Optional[dict]:
        """Pull __APOLLO_STATE__ from __NEXT_DATA__."""
        try:
            return await page.evaluate("""
                () => {
                    try {
                        const nd = JSON.parse(
                            document.getElementById('__NEXT_DATA__').textContent
                        );
                        return nd?.props?.pageProps?.__APOLLO_STATE__ || null;
                    } catch(e) { return null; }
                }
            """)
        except Exception:
            return None

    @staticmethod
    def _parse_apollo(apollo: dict) -> tuple[dict, dict]:
        """Split Apollo state into {Advert:id → data} and {Seller:id → data} dicts."""
        adverts: dict = {}
        sellers: dict = {}
        for key, val in apollo.items():
            if key.startswith("Advert:"):
                adverts[key] = val
            elif key.startswith("Seller:"):
                sellers[key] = val
        return adverts, sellers

    def _normalise(
        self,
        advert: dict,
        seller: dict,
        canonical_make: str,
    ) -> Optional[dict]:
        try:
            lid = str(advert.get("id") or "")
            if not lid:
                return None

            url = advert.get("url") or f"{_BASE}/classifieds/{lid}"

            headline  = clean(advert.get("headline") or "")
            make_name = clean(advert.get("makeAnalyticsName") or advert.get("make") or canonical_make)
            model_raw = clean(advert.get("modelAnalyticsName") or advert.get("model") or "")
            if not model_raw:
                model_raw = re.sub(re.escape(make_name or ""), "", headline or "",
                                   flags=re.IGNORECASE).strip()
            model = model_raw[:80]

            price_raw = advert.get("price")
            price_gbp = float(price_raw) if price_raw else None

            year = advert.get("year") or parse_year(headline or "")

            spec  = advert.get("specificationData") or {}
            miles = spec.get("mileage")
            if miles is not None:
                miles = int(miles)

            images  = advert.get("fullSizeImageUrls") or advert.get("imageUrls") or []

            seller_type_raw = seller.get("sellerType") or "Trade"
            seller_type     = "private" if seller_type_raw.lower() == "private" else "dealer"
            seller_name     = clean(seller.get("name") or seller.get("displayName") or "")

            description = clean(advert.get("description") or advert.get("sellerDescription") or "")
            colour      = clean(spec.get("colour") or spec.get("color") or "")
            trans_raw   = str(spec.get("transmission") or "").lower()
            transmission = "manual" if "manual" in trans_raw else ("auto" if trans_raw else None)

            return {
                "source":            self.SOURCE,
                "market":            self.MARKET,
                "source_url":        url,
                "source_listing_id": lid,
                "make":              canonical_make,
                "model":             model,
                "year_manufactured": year,
                "price_gbp":         price_gbp,
                "mileage_miles":     miles,
                "mileage_km":        miles_to_km(miles),
                "colour":            colour,
                "seller_type":       seller_type,
                "is_direct_owner":   int(seller_type == "private"),
                "seller_name":       seller_name,
                "description_text":  description,
                "image_urls":        json.dumps(images[:30]),
            }
        except Exception as exc:
            self.log_error(f"PH normalise advert {advert.get('id')}: {exc}")
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    async def _dismiss_cookies(page) -> None:
        try:
            btn = await page.query_selector(
                "button:has-text('Accept all'), button:has-text('Accept'), #onetrust-accept-btn-handler"
            )
            if btn and await btn.is_visible():
                await btn.click()
                await asyncio.sleep(0.8)
        except Exception:
            pass
