"""
Carousell scraper (carousell.sg).

Site characteristics (2025 → 2026)
────────────────────────────────────
• React SPA — Playwright with JS execution required.
• CSS class names are hashed and change per deploy; avoid them.
• Listing links are stable:  /p/<slug>-<numeric-id>/
• Search URL (new as of 2025):
    /categories/cars-32/used-cars-singapore-1173/?search=<query>
  (old /categories/cars-on-carousell-60/ path returns 404)
• Results render fully in DOM — no infinite scroll needed for niche makes.
  All /p/ links are visible after initial page load + brief wait.
• XHR responses no longer carry listing JSON — DOM-only scraping.

Delta strategy
──────────────
Scrape /p/ listing links from search results, stop when a known listing ID
appears in the harvested set.  Each make is searched independently.
Run gentle delays between queries to avoid banning home IP.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import urllib.parse
from typing import Optional

import config
from scrapers.base import BaseScraper
from scrapers.parsers import (
    clean,
    coe_months_remaining,
    infer_seller_type,
    parse_coe_expiry_iso,
    parse_listing_date,
    parse_mileage_km,
    parse_owners,
    parse_price_sgd,
    parse_year,
)

log = logging.getLogger(__name__)

_BASE = "https://www.carousell.sg"
# Used-cars search category (updated from the deprecated cars-on-carousell-60 path)
_SEARCH_BASE = f"{_BASE}/categories/cars-32/used-cars-singapore-1173/"

# Stealth delays (seconds) — runs on home IP; be gentle to avoid IP ban.
_QUERY_DELAY   = (45.0, 120.0)  # between search queries
_LISTING_DELAY = (20.0, 60.0)   # between individual listing page fetches


class CarousellScraper(BaseScraper):
    SOURCE = "carousell"
    MARKET = "SG"
    VPN_COUNTRY = None    # no VPN — runs on home IP
    REQUIRES_VPN = False  # Carousell blocks all VPN exit nodes

    # ── Public interface ──────────────────────────────────────────────────────

    async def run(self, known_ids: Optional[set[str]] = None) -> tuple[int, int]:
        """Scrape all target make/model combinations from Carousell."""
        if known_ids is None and self.run_type == "incremental":
            known_ids = self.get_known_ids()

        for make, models in config.TARGET_VEHICLES.items():
            queries = self._build_queries(make, models)
            for query in queries:
                log.info("[carousell] Searching: %s", query)
                await self._scrape_query(query, make, known_ids)
                await self.delay(min_s=_QUERY_DELAY[0], max_s=_QUERY_DELAY[1])

        return self._new, self._updated

    # ── Search ────────────────────────────────────────────────────────────────

    def _build_queries(self, make: str, models: list[str]) -> list[str]:
        if not models:
            return [make]
        return [f"{make} {m}" for m in models]

    async def _scrape_query(
        self,
        query: str,
        canonical_make: str,
        known_ids: Optional[set[str]],
    ) -> None:
        url = f"{_SEARCH_BASE}?search={urllib.parse.quote(query)}"

        async with await self.new_context() as ctx:
            page = await ctx.new_page()

            if not await self.safe_goto(page, url, wait="domcontentloaded", timeout_ms=40_000):
                await page.close()
                return

            await asyncio.sleep(3)

            # Harvest listing URLs from DOM
            collected_urls: list[str] = []
            stop = False

            new_urls = await self._harvest_listing_urls(page)
            for link_url in new_urls:
                lid = self._extract_listing_id(link_url)
                if lid and link_url not in collected_urls:
                    if known_ids and lid in known_ids:
                        log.info("[carousell] Hit known id %s — stopping", lid)
                        stop = True
                        break
                    collected_urls.append(link_url)

            await page.close()

        if not collected_urls:
            log.info("[carousell] No listings found for query: %s", query)
            return

        log.info("[carousell] Found %d new listing URLs for: %s", len(collected_urls), query)

        # Scrape each listing page individually
        async with await self.new_context() as ctx:
            for listing_url in collected_urls:
                lid = self._extract_listing_id(listing_url)
                detail = await self._scrape_listing_page(ctx, listing_url, canonical_make)
                if detail:
                    self.save_listing(detail)
                    log.info("[carousell] Saved %s — %s", lid, listing_url)
                await self.delay(min_s=_LISTING_DELAY[0], max_s=_LISTING_DELAY[1])

    # ── Listing page scraping ─────────────────────────────────────────────────

    async def _scrape_listing_page(
        self,
        ctx,
        url: str,
        canonical_make: str,
    ) -> Optional[dict]:
        lid = self._extract_listing_id(url)
        page = await ctx.new_page()
        try:
            if not await self.safe_goto(page, url, wait="domcontentloaded", timeout_ms=35_000):
                return None

            await asyncio.sleep(2)

            # Title
            title = await self._text(page, "h1")
            title = clean(title) or ""

            # Price
            price_raw = await self._text(page, "[data-testid='listing-price'], p[class*='price'], h2[class*='price']")
            if not price_raw:
                content = await page.inner_text("body")
                m = re.search(r'S\$\s*([\d,]+)', content)
                price_raw = m.group(0) if m else ""
            price_sgd = parse_price_sgd(price_raw)

            # Description
            desc_raw = await self._text(page,
                "[data-testid='listing-description'], div[class*='description'], section[class*='desc']")
            description = clean(desc_raw)

            # Parse car specs from description text
            year = self._extract_from_desc(description, r'\b(20\d{2}|19\d{2})\b')
            mileage_km = parse_mileage_km(self._extract_from_desc(description, r'[\d,]+\s*km') or "")
            owners = parse_owners(self._extract_from_desc(description, r'\b(\d)\s*owner') or "")
            coe_raw = self._extract_from_desc(description, r'COE[^,\n]{0,40}') or ""
            coe_expiry = parse_coe_expiry_iso(coe_raw)
            coe_months = coe_months_remaining(coe_expiry)

            # Seller
            seller_name_raw = await self._text(page,
                "a[href*='/u/'], [data-testid='seller-name'], p[class*='seller']")
            seller_name = clean(seller_name_raw)
            page_text = await page.inner_text("body")
            seller_type = infer_seller_type(page_text)
            direct_owner = int(seller_type == "private")

            # Images
            images = await page.eval_on_selector_all(
                "img[src*='carousell'], img[alt*='photo'], [data-testid*='photo'] img",
                "els => els.map(e => e.src).filter(Boolean)",
            )
            images = list(dict.fromkeys(images))[:30]

            # Date listed
            date_raw = await self._text(page, "time, [datetime], [class*='date'], [class*='posted']")
            listed_date = parse_listing_date(date_raw) if date_raw else None

            # Model from title
            model = re.sub(re.escape(canonical_make), "", title, flags=re.IGNORECASE).strip()[:80]

            return {
                "source":               self.SOURCE,
                "market":               self.MARKET,
                "source_url":           url,
                "source_listing_id":    lid,
                "make":                 canonical_make,
                "model":                model,
                "year_manufactured":    parse_year(str(year)) if year else None,
                "price_sgd":            price_sgd,
                "mileage_km":           mileage_km,
                "coe_expiry_date":      coe_expiry,
                "coe_months_remaining": coe_months,
                "num_owners":           owners,
                "seller_name":          seller_name,
                "seller_type":          seller_type,
                "is_direct_owner":      direct_owner,
                "description_text":     description,
                "image_urls":           json.dumps(images),
                "listing_date":         listed_date,
            }

        except Exception as exc:
            self.log_error(f"Carousell listing page {url}: {exc}")
            return None
        finally:
            await page.close()

    # ── DOM helpers ───────────────────────────────────────────────────────────

    async def _harvest_listing_urls(self, page) -> list[str]:
        try:
            hrefs = await page.eval_on_selector_all(
                'a[href*="/p/"]',
                "els => [...new Set(els.map(e => e.href).filter(h => /\\/p\\/.+\\d/.test(h)))]",
            )
            return [h for h in hrefs if _BASE in h]
        except Exception:
            return []

    @staticmethod
    def _extract_listing_id(url: str) -> Optional[str]:
        m = re.search(r'/p/[^/]+-(\d+)/?', url)
        return m.group(1) if m else None

    @staticmethod
    def _extract_from_desc(description: Optional[str], pattern: str) -> Optional[str]:
        if not description:
            return None
        m = re.search(pattern, description, re.IGNORECASE)
        return m.group(0) if m else None

    async def _text(self, page, selector: str) -> Optional[str]:
        try:
            for sel in selector.split(","):
                el = await page.query_selector(sel.strip())
                if el:
                    text = await el.inner_text()
                    if text and text.strip():
                        return text.strip()
        except Exception:
            pass
        return None
