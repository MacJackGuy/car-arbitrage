"""
AutoTrader UK scraper (autotrader.co.uk).

Site characteristics
─────────────────────
• React SPA behind Cloudflare — requires Playwright with stealth.
• Listing cards: `article[data-testid="search-listing-card"]`
• URL params: make=, model=, postcode=SW1A1AA, radius=1500, page=N,
              sort=latest-listed-desc
• Each card exposes: title, price, mileage (miles), year, seller type,
  dealer/private badge, number of photos badge.
• Listing detail URL: /car-details/{make}-{model}-{year}/{advert-id}
  advert-id is also embedded in each card's href.
• Pagination: &page=N, ~12–15 results per page.

Cloudflare note
───────────────
AutoTrader uses Cloudflare Bot Management.  This scraper implements best-
effort stealth (navigator.webdriver override, realistic UA, human-paced
interactions).  If Cloudflare triggers, the page will show a challenge;
`safe_goto` will return False and the run logs the failure.  A residential
proxy would improve reliability significantly.

Delta strategy
──────────────
sort=latest-listed-desc ensures newest first.  Stop on first known ID.
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
    parse_price_gbp,
    parse_year,
)
from scrapers.uk_targets import AUTOTRADER_MAKE, AUTOTRADER_MODEL, search_pairs

log = logging.getLogger(__name__)

_BASE      = "https://www.autotrader.co.uk"
_SEARCH    = f"{_BASE}/car-search"
_POSTCODE  = "EH11AD"
_RADIUS    = 1500        # miles radius (national coverage)


class AutoTraderScraper(BaseScraper):
    SOURCE = "autotrader"
    MARKET = "UK"
    LOCALE = "en-GB"
    TIMEZONE_ID = "Europe/London"

    async def run(self, known_ids: Optional[set[str]] = None) -> tuple[int, int]:
        if known_ids is None and self.run_type == "incremental":
            known_ids = self.get_known_ids()

        # Deduplicate searches: multiple canonical models can map to the same AT slug
        # (e.g. "911 GT3", "997.2 Turbo S" → "911").  Run each (make, at_slug) once.
        seen: set[tuple[str, Optional[str]]] = set()
        for canonical_make, model in search_pairs():
            site_make = AUTOTRADER_MAKE.get(canonical_make, canonical_make)
            at_model  = AUTOTRADER_MODEL.get(model, model) if model else None
            key = (site_make, at_model)
            if key in seen:
                continue
            seen.add(key)
            log.info("[autotrader] Searching %s / %s", canonical_make, at_model or "all")
            await self._scrape(canonical_make, site_make, at_model, known_ids)
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
                if not await self.safe_goto(page, url, wait="domcontentloaded", timeout_ms=45_000):
                    break

                # Check for Cloudflare challenge
                if await self._is_cf_challenge(page):
                    log.warning("[autotrader] Cloudflare challenge detected — skipping")
                    break

                await self._dismiss_cookies(page)
                # Wait for React to render cards
                try:
                    await page.wait_for_selector('li[id^="id-"]', timeout=15_000)
                except Exception:
                    pass

                cards = await self._extract_cards(page)
                if not cards:
                    log.info("[autotrader] No cards on page %d — stopping", page_n)
                    break

                stop = False
                for card in cards:
                    lid = card.get("listing_id", "")
                    if known_ids and lid in known_ids:
                        log.info("[autotrader] Hit known id %s — delta caught up", lid)
                        stop = True
                        break

                    listing = self._card_to_listing(card, canonical_make)
                    if listing:
                        self.save_listing(listing)

                if stop:
                    break

                # Check if there's a next page
                has_next = await page.query_selector(
                    '[data-testid="pagination-next"], a[aria-label="Next"], a[aria-label="next"]'
                )
                if not has_next or len(cards) < 5:
                    break

                page_n += 1
                await self.delay(min_s=8, max_s=25)

            await page.close()

    # ── Card extraction ───────────────────────────────────────────────────────

    async def _extract_cards(self, page) -> list[dict]:
        """Extract structured data from AutoTrader listing cards."""
        try:
            return await page.evaluate("""
            () => {
                const results = [];
                document.querySelectorAll('li[id^="id-"]').forEach(card => {
                    const lid = card.id.replace('id-', '');

                    const link = card.querySelector('a[href*="/car-details/"]');
                    const url = link ? link.href.split('?')[0] : '';

                    const titleEl = card.querySelector("[data-testid='search-listing-title']");
                    const title = titleEl ? titleEl.innerText.split('\\n')[0].trim() : '';

                    const subtitleEl = card.querySelector("[data-testid='search-listing-subtitle']");
                    const subtitle = subtitleEl ? subtitleEl.innerText.trim() : '';

                    // Price embedded in title text after newline, or in dedicated el
                    const allText = card.innerText;
                    const priceMatch = allText.match(/£[\\d,]+/);
                    const price = priceMatch ? priceMatch[0] : '';

                    const mileageEl = card.querySelector("[data-testid='mileage']");
                    const miles = mileageEl ? mileageEl.innerText.trim() : '';

                    const yearEl = card.querySelector("[data-testid='registered_year']");
                    const yearText = yearEl ? yearEl.innerText.trim() : '';
                    const yearMatch = yearText.match(/\\b(19|20)\\d{2}\\b/);
                    const year = yearMatch ? yearMatch[0] : '';

                    const isPrivate = !!card.querySelector("[data-testid='private-seller']");

                    results.push({ listing_id: lid, url, title, subtitle, price, miles,
                                   year, seller_badge: isPrivate ? 'private' : 'trade' });
                });
                return results.filter(r => r.listing_id);
            }
            """)
        except Exception as exc:
            self.log_error(f"AutoTrader card extraction: {exc}")
            return []

    def _card_to_listing(self, card: dict, canonical_make: str) -> Optional[dict]:
        try:
            lid   = card.get("listing_id", "")
            url   = card.get("url") or f"{_BASE}/car-details/{lid}"
            title = clean(card.get("title") or "")

            price_gbp = parse_price_gbp(card.get("price") or "")
            year      = parse_year(card.get("year") or "")
            miles     = parse_mileage_miles(card.get("miles") or "")

            badge = (card.get("seller_badge") or "").lower()
            seller_type = "private" if "private" in badge else "dealer"

            # Model: strip year and make from title
            model_raw = re.sub(re.escape(canonical_make), "", title or "",
                               flags=re.IGNORECASE).strip()
            model = re.sub(r'^\s*\d{4}\s*', '', model_raw).strip()[:80]

            # Colour from subtitle
            subtitle = card.get("subtitle") or ""
            colour = subtitle.split(",")[0].strip() if subtitle else None

            return {
                "source":           self.SOURCE,
                "market":           self.MARKET,
                "source_url":       url,
                "source_listing_id": lid,
                "make":             canonical_make,
                "model":            model,
                "year_manufactured": year,
                "price_gbp":        price_gbp,
                "mileage_miles":    miles,
                "mileage_km":       miles_to_km(miles),
                "colour":           colour,
                "seller_type":      seller_type,
                "is_direct_owner":  int(seller_type == "private"),
                "image_urls":       json.dumps([]),
            }
        except Exception as exc:
            self.log_error(f"AutoTrader card_to_listing: {exc}")
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _search_url(make: str, model: Optional[str], page_n: int) -> str:
        url = (f"{_SEARCH}?make={make.replace(' ', '%20')}"
               f"&postcode={_POSTCODE}&radius={_RADIUS}"
               f"&sort=latest-listed-desc&condition=used")
        if model:
            url += f"&model={model.replace(' ', '%20')}"
        if page_n > 1:
            url += f"&page={page_n}"
        return url

    @staticmethod
    async def _is_cf_challenge(page) -> bool:
        try:
            title = await page.title()
            return "just a moment" in title.lower() or "challenge" in title.lower()
        except Exception:
            return False

    @staticmethod
    async def _dismiss_cookies(page) -> None:
        try:
            btn = await page.query_selector(
                "button:has-text('Accept all'), #onetrust-accept-btn-handler"
            )
            if btn and await btn.is_visible():
                await btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass
