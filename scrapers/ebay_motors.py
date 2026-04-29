"""
eBay Motors UK scraper (ebay.co.uk/sch/Cars-/9801/).

Site characteristics
─────────────────────
• Server-side rendered (Marko.js) — structured HTML available without JS.
• Listing cards: `li[data-listingid="NNNN"]` or `li[data-view="mi:1686"]`.
• Pagination: `&_pgn=N` (URL param).
• Mileage in card: span containing "miles" text.
• Price: `.s-item__price` span.
• Seller type: "Classified Ad" → private; "Buy It Now" / store → dealer.
  Must check badge text on SRP card; visit detail page for confirmation.
• Listing ID: `data-listingid` attribute value.
• Image count: requires visiting detail page; store 0 on SRP pass and
  optionally update during detail scrape (for high-score listings).

Delta strategy
──────────────
Sort `_sop=10` = "Newly listed" first.  Stop pagination on first known ID.

eBay search URL:
    https://www.ebay.co.uk/sch/Cars-/9801/i.html
    ?_nkw=Ferrari&LH_PrefLoc=1&_sop=10&LH_ItemCondition=3000&_pgn=N
    LH_ItemCondition=3000 = Used
    LH_PrefLoc=1 = UK only
    Searches by make only (not make+model) to avoid missing results.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

import config
from scrapers.base import BaseScraper
from utils.ivpn import rotate_server_async
from scrapers.parsers import (
    clean,
    miles_to_km,
    parse_mileage_miles,
    parse_price_gbp,
    parse_year,
)
from scrapers.uk_targets import PISTONHEADS_MAKE

log = logging.getLogger(__name__)

_BASE      = "https://www.ebay.co.uk"
_SEARCH    = f"{_BASE}/sch/Cars-/9801/i.html"
_PAGE_SIZE = 60   # eBay default max per page
_MAX_PAGES = 5    # cap per make — keeps each IP session short to avoid eBay rate-limits


class EbayMotorsScraper(BaseScraper):
    SOURCE = "ebay_motors"
    MARKET = "UK"

    async def run(self, known_ids: Optional[set[str]] = None) -> tuple[int, int]:
        if known_ids is None and self.run_type == "incremental":
            known_ids = self.get_known_ids()

        for canonical_make in PISTONHEADS_MAKE:
            # Rotate VPN server before every make to get a fresh IP.
            # eBay rate-limits by IP after ~300 listing requests; rotating
            # between London and Manchester resets the quota each time.
            await rotate_server_async(wait_s=10)
            await self.restart_browser()
            log.info("[ebay] Searching %s (all models)", canonical_make)
            await self._scrape(canonical_make, None, known_ids)
            await self.delay()

        return self._new, self._updated

    # ── Pagination ────────────────────────────────────────────────────────────

    async def _scrape(
        self,
        canonical_make: str,
        _model: Optional[str],  # unused — search by make only
        known_ids: Optional[set[str]],
    ) -> None:
        page_n = 1

        async with await self.new_context() as ctx:
            page = await ctx.new_page()

            while True:
                url = self._search_url(canonical_make, None, page_n)
                if not await self.safe_goto(page, url, wait="domcontentloaded"):
                    break

                await asyncio.sleep(2)  # let Marko hydrate

                cards = await self._extract_cards(page)
                if not cards:
                    log.info("[ebay] No cards on page %d — stopping", page_n)
                    break

                stop = False
                for card in cards:
                    lid = card.get("listing_id", "")
                    if known_ids and lid in known_ids:
                        log.info("[ebay] Hit known id %s — delta caught up", lid)
                        stop = True
                        break

                    listing = self._card_to_listing(card, canonical_make)
                    if listing:
                        self.save_listing(listing)

                if stop or len(cards) < 10:
                    break

                if page_n >= _MAX_PAGES:
                    log.info("[ebay] Reached page limit (%d) for %s", _MAX_PAGES, canonical_make)
                    break

                # Check for "next page" button presence
                has_next = await page.query_selector(".pagination__next, a[aria-label='Next page']")
                if not has_next:
                    break

                page_n += 1
                await self.delay(min_s=6, max_s=20)

            await page.close()

    # ── Card extraction ───────────────────────────────────────────────────────

    async def _extract_cards(self, page) -> list[dict]:
        """Extract raw listing data from all visible eBay listing cards.

        eBay UK migrated from .s-item__* to .s-card__* classes (Apr 2025).
        Title lives in .s-card__title; price in .s-card__price; mileage is in
        a plain span whose text starts with "Miles:".  Filter to ebay.co.uk
        URLs to exclude cross-border US placeholder cards.
        """
        try:
            return await page.evaluate(r"""
            () => {
                const results = [];
                const items = document.querySelectorAll('li[data-listingid]');
                items.forEach(li => {
                    const lid = li.getAttribute('data-listingid') || '';
                    const a   = li.querySelector('a.s-card__link, a[href*="/itm/"]');
                    const url = a ? a.href : '';

                    // Only keep UK listings
                    if (!url.includes('ebay.co.uk')) return;

                    // Prefer the primary styled-text span (the actual title);
                    // fall back to full .s-card__title and strip noise.
                    const titleSpan = li.querySelector('.s-card__title .su-styled-text.primary, .s-card__title .su-styled-text');
                    const titleEl   = titleSpan || li.querySelector('.s-card__title');
                    const title     = (titleEl ? titleEl.innerText.trim() : '')
                        .replace(/\bnew listing\b/gi, '')
                        .replace(/opens in a new window or tab/gi, '')
                        .trim();
                    const priceEl = li.querySelector('.s-card__price');
                    const price   = priceEl ? priceEl.innerText.trim() : '';

                    // Mileage: span whose text begins with "Miles:"
                    let miles = '';
                    li.querySelectorAll('span').forEach(sp => {
                        if (!miles && /^\s*Miles:/i.test(sp.innerText)) miles = sp.innerText.trim();
                    });

                    // Year from title
                    const yearM = title.match(/\b(19|20)\d{2}\b/);
                    const year  = yearM ? yearM[0] : '';

                    // Seller type: scan for "Classified Ad" or "Buy It Now"
                    let badge = '';
                    li.querySelectorAll('span').forEach(sp => {
                        if (!badge && /classified ad|buy it now/i.test(sp.innerText)) {
                            badge = sp.innerText.trim();
                        }
                    });

                    const imgs = li.querySelectorAll('img[src*="i.ebayimg"], img[data-defer-load*="i.ebayimg"]');
                    const idFromUrl = url.match(/\/itm\/(\d+)/);
                    results.push({
                        listing_id: lid || (idFromUrl ? idFromUrl[1] : ''),
                        url:        url,
                        title:      title,
                        price_raw:  price,
                        miles_raw:  miles,
                        year_raw:   year,
                        badge:      badge,
                        n_images:   imgs.length,
                    });
                });
                return results.filter(r => r.listing_id);
            }
            """)
        except Exception as exc:
            self.log_error(f"eBay card extraction: {exc}")
            return []

    def _card_to_listing(self, card: dict, canonical_make: str) -> Optional[dict]:
        """Convert a raw card dict to our listing schema."""
        try:
            lid  = card.get("listing_id", "")
            url  = card.get("url") or f"{_BASE}/itm/{lid}"
            if url and "ebay.co.uk" not in url:
                url = _BASE + url if url.startswith("/") else url

            title     = clean(card.get("title") or "")
            price_gbp = parse_price_gbp(card.get("price_raw") or "")
            year      = parse_year(card.get("year_raw") or "")
            miles_raw = card.get("miles_raw") or ""
            miles     = parse_mileage_miles(miles_raw)

            # Seller type from badge text
            badge = (card.get("badge") or "").lower()
            if "classified" in badge or "private" in badge:
                seller_type = "private"
            else:
                seller_type = "dealer"

            # Model: strip make from title
            model_raw = re.sub(re.escape(canonical_make), "", title or "",
                               flags=re.IGNORECASE).strip()
            # Strip year from front of model string
            model = re.sub(r'^\s*\d{4}\s*', '', model_raw).strip()[:80]

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
                "seller_type":      seller_type,
                "is_direct_owner":  int(seller_type == "private"),
                "image_urls":       json.dumps([]),
            }
        except Exception as exc:
            self.log_error(f"eBay card_to_listing {card.get('listing_id')}: {exc}")
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _search_url(make: str, model: Optional[str], page_n: int) -> str:
        q = f"{make}+{model}".replace(" ", "+") if model else make.replace(" ", "+")
        url = (f"{_SEARCH}?_nkw={q}&LH_PrefLoc=1&_sop=10"
               f"&LH_ItemCondition=3000&_ipg=60")
        if page_n > 1:
            url += f"&_pgn={page_n}"
        return url
