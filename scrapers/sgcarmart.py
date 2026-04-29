"""
sgCarMart scraper (sgcarmart.com) — Next.js rewrite (2025).

Site characteristics (post-2025 redesign)
──────────────────────────────────────────
• Next.js SPA under /used-cars/ — Playwright + JS execution required.
• Search:  /used-cars/listing?q=<query>&page=<N>   (20 listings/page)
• Detail:  /used-cars/info/<make-model-slug-ID>/
• Listing links:  a[href*="/used-cars/info/"]  — ID is trailing number in slug.
• Spec extraction: label-proximity (find leaf element whose text === label,
  then read its grandparent's next sibling value element).
• Results sorted newest-first by default.
• Cloudflare blocks UK IPs → runner switches to SG IVPN exit node before use.

Delta strategy
──────────────
Pass known_ids (source_listing_ids from DB) to run(). Pagination stops per
make/model search as soon as a known listing ID appears in results.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
from typing import Optional

import config
from scrapers.base import BaseScraper
from scrapers.parsers import (
    clean,
    coe_months_remaining,
    infer_seller_type,
    normalise_make,
    parse_coe_expiry_from_reg_date,
    parse_coe_expiry_iso,
    parse_engine_cc,
    parse_listing_date,
    parse_mileage_km,
    parse_owners,
    parse_price_sgd,
    parse_year,
    parse_year_from_date,
    parse_depreciation_annual,
)

log = logging.getLogger(__name__)

# ── Search targets ────────────────────────────────────────────────────────────
# Maps canonical make → list of (make_q, model_q) where model_q=None means
# search by make alone (e.g. Ferrari, Lamborghini).
_SEARCH_TARGETS: dict[str, list[tuple[str, Optional[str]]]] = {
    "Ferrari":      [("Ferrari",        None)],
    "Lamborghini":  [("Lamborghini",    None)],
    "McLaren":      [("McLaren",        None)],
    "Porsche":      [("Porsche",        None)],
    "Aston Martin": [("Aston Martin",   None)],
    "Bentley":      [("Bentley",        None)],
    "BMW":          [("BMW", m) for m in (
                        "M2", "M3", "M4", "M5", "M6", "M8",
                        "X5M", "X6M", "Z3", "Z4", "Z8", "i8",
                    )],
    "Audi":         [("Audi", m) for m in (
                        "R8",
                        "RS3", "RS4", "RS5", "RS6", "RS7",
                        "RSQ3", "RSQ5", "RSQ8",
                        "S3", "S4", "S5", "S6", "S7", "S8",
                        "SQ5", "SQ7", "SQ8",
                    )],
    "Maserati":     [("Maserati", "Levante")],
    "Mercedes-Benz": [("Mercedes-Benz", m) for m in (
                        "AMG GT", "C63", "E63", "G63", "G500",
                        "S63", "S65", "SLS", "GLE63", "GLS63",
                    )],
    "Rolls-Royce":  [("Rolls-Royce",    None)],
}

# JavaScript that extracts specs by matching known label text in leaf elements
_SPEC_LABELS = [
    "Manufactured", "Reg Date", "Mileage", "Engine Capacity",
    "COE", "OMV", "ARF", "Depreciation", "Transmission",
    "No. of Owners", "Colour", "Power",
]
_SPEC_EXTRACT_JS = r"""
(labels) => {
    const specs = {};
    for (const label of labels) {
        for (const el of document.querySelectorAll("*")) {
            if (el.children.length === 0 && el.innerText && el.innerText.trim() === label) {
                let value = null;
                const parent = el.parentElement;
                if (parent && parent.parentElement) {
                    const siblings = Array.from(parent.parentElement.children);
                    const idx = siblings.indexOf(parent);
                    if (idx >= 0 && idx + 1 < siblings.length) {
                        value = siblings[idx + 1].innerText.trim();
                    }
                }
                if (!value) {
                    const next = el.nextElementSibling;
                    if (next) value = next.innerText.trim();
                }
                if (value) specs[label] = value;
                break;
            }
        }
    }
    return specs;
}
"""


class SgCarMartScraper(BaseScraper):
    SOURCE = "sgcarmart"
    MARKET = "SG"
    VPN_COUNTRY = "SG"   # must be on SG exit node (Cloudflare blocks UK IPs)
    BASE_URL = "https://www.sgcarmart.com"
    SEARCH_URL = f"{BASE_URL}/used-cars/listing"
    PAGE_SIZE = 20

    # ── Public interface ──────────────────────────────────────────────────────

    async def run(self, known_ids: Optional[set[str]] = None) -> tuple[int, int]:
        if known_ids is None and self.run_type == "incremental":
            known_ids = self.get_known_ids()

        for canonical_make, searches in _SEARCH_TARGETS.items():
            for make_q, mdl_q in searches:
                log.info("[sgcarmart] Searching make=%s mdl=%s", make_q, mdl_q)
                await self._scrape_search(canonical_make, make_q, mdl_q, known_ids)
                await self.delay()

        return self._new, self._updated

    # ── Search pagination ─────────────────────────────────────────────────────

    async def _scrape_search(
        self,
        canonical_make: str,
        make_q: str,
        mdl_q: Optional[str],
        known_ids: Optional[set[str]],
    ) -> None:
        page_num = 1
        seen_this_run: set[str] = set()
        async with await self.new_context() as ctx:
            browser_page = await ctx.new_page()
            while True:
                url = self._build_search_url(make_q, mdl_q, page_num)
                if not await self.safe_goto(browser_page, url, wait="domcontentloaded"):
                    break
                await asyncio.sleep(2)

                id_url_pairs = await self._extract_ids_from_search(browser_page)
                if not id_url_pairs:
                    log.info("[sgcarmart] No listings on page %d — stopping", page_num)
                    break

                hit_known = False
                new_on_page = 0
                for lid, listing_url in id_url_pairs:
                    if lid in seen_this_run:
                        continue
                    seen_this_run.add(lid)
                    new_on_page += 1
                    if known_ids and lid in known_ids:
                        log.info("[sgcarmart] Hit known id %s — delta caught up", lid)
                        hit_known = True
                        break
                    await self._scrape_and_save_detail(ctx, lid, listing_url, canonical_make)
                    await self.delay()

                if hit_known or new_on_page == 0 or len(id_url_pairs) < self.PAGE_SIZE:
                    break

                page_num += 1
                await self.delay()

    # ── Detail scraping ───────────────────────────────────────────────────────

    async def _scrape_and_save_detail(
        self,
        ctx,
        listing_id: str,
        url: str,
        canonical_make: str,
    ) -> None:
        page = await ctx.new_page()
        try:
            if not await self.safe_goto(page, url, wait="domcontentloaded"):
                return
            await asyncio.sleep(2)

            raw = await self._extract_detail(page, listing_id, canonical_make, url)
            if raw is None:
                return

            self.save_listing(raw)
            log.info("[sgcarmart] Saved %s — %s %s %s",
                     listing_id, canonical_make, raw.get("model", ""), raw.get("price_sgd", ""))

        except Exception as exc:
            self.log_error(f"Detail page {listing_id}: {exc}")
        finally:
            await page.close()

    async def _extract_detail(
        self,
        page,
        listing_id: str,
        canonical_make: str,
        url: str,
    ) -> Optional[dict]:
        try:
            # ── Title ─────────────────────────────────────────────────────────
            title = await self._text(page, "h1")
            title = clean(title) or ""

            # ── Price ─────────────────────────────────────────────────────────
            price_raw = await self._text(page,
                "[class*='price__'], [class*='_price'], [class*='asking_price']")
            if not price_raw:
                content = await page.inner_text("body")
                m = re.search(r'\$([\d,]+)', content)
                price_raw = m.group(0) if m else ""
            price_sgd = parse_price_sgd(price_raw)

            # ── Specs (label-proximity extraction) ────────────────────────────
            specs = await page.evaluate(_SPEC_EXTRACT_JS, _SPEC_LABELS)

            year_manuf   = parse_year(specs.get("Manufactured", ""))
            reg_raw      = specs.get("Reg Date", "")
            year_reg     = parse_year(reg_raw.split('\n')[0]) if reg_raw else None
            mileage_km   = parse_mileage_km(specs.get("Mileage", ""))
            engine_cc    = parse_engine_cc(specs.get("Engine Capacity", ""))
            num_owners   = parse_owners(specs.get("No. of Owners", ""))
            colour       = clean(specs.get("Colour", ""))
            dep_raw      = specs.get("Depreciation", "")
            transmission_raw = specs.get("Transmission", "")
            transmission = ("manual" if "manual" in transmission_raw.lower()
                            else "auto" if transmission_raw else None)

            # Financial
            omv_sgd  = parse_price_sgd(specs.get("OMV", ""))
            arf_raw  = specs.get("ARF", "")
            arf_sgd  = parse_price_sgd(arf_raw)

            # COE expiry: derive from registration date + 10 years
            coe_expiry = parse_coe_expiry_from_reg_date(reg_raw)
            if not coe_expiry:
                coe_expiry = parse_coe_expiry_iso(specs.get("COE", ""))
            coe_months = coe_months_remaining(coe_expiry)

            # ── Model from title ───────────────────────────────────────────────
            model_text = re.sub(re.escape(canonical_make), "", title, flags=re.IGNORECASE).strip()
            model = clean(model_text[:60]) or title[:60]

            # ── Seller ─────────────────────────────────────────────────────────
            page_text = await page.inner_text("body")
            seller_type_str = infer_seller_type(page_text)
            direct_owner = seller_type_str == "private"

            seller_name = clean(
                await self._text(page, "[class*='dealer_name'], [class*='seller_name'], [class*='agent_name']")
            )

            # ── Description ────────────────────────────────────────────────────
            desc_raw = await self._text(page, "[class*='description'], [class*='remarks']")
            description = clean(desc_raw)

            # ── Images ─────────────────────────────────────────────────────────
            images = await self._extract_images(page)

            # ── Listing date ───────────────────────────────────────────────────
            date_raw = await self._text(page, "[class*='date'], [class*='posted'], time")
            listing_date = parse_listing_date(date_raw) if date_raw else None

            return {
                "source":               self.SOURCE,
                "market":               self.MARKET,
                "source_url":           url,
                "source_listing_id":    listing_id,
                "make":                 canonical_make,
                "model":                model,
                "year_manufactured":    year_manuf,
                "year_registered":      year_reg,
                "price_sgd":            price_sgd,
                "mileage_km":           mileage_km,
                "coe_expiry_date":      coe_expiry,
                "coe_months_remaining": coe_months,
                "omv_sgd":              omv_sgd,
                "arf_paid_sgd":         arf_sgd,
                "colour":               colour,
                "num_owners":           num_owners,
                "is_direct_owner":      int(direct_owner),
                "seller_name":          seller_name,
                "seller_type":          seller_type_str,
                "description_text":     description,
                "image_urls":           json.dumps(images),
                "listing_date":         listing_date,
            }

        except Exception as exc:
            self.log_error(f"Parsing detail {listing_id}: {exc}")
            return None

    # ── DOM helpers ───────────────────────────────────────────────────────────

    def _build_search_url(self, make_q: str, mdl_q: Optional[str], page: int) -> str:
        q = f"{make_q} {mdl_q}".strip() if mdl_q else make_q
        params = f"q={urllib.parse.quote(q)}"
        if page > 1:
            params += f"&page={page}"
        return f"{self.SEARCH_URL}?{params}"

    async def _extract_ids_from_search(self, page) -> list[tuple[str, str]]:
        """Return list of (listing_id, full_url) from /used-cars/info/ links."""
        try:
            links = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('a[href*="/used-cars/info/"]'))
                    .map(a => a.href.split('?')[0])
                    .filter((v, i, a) => a.indexOf(v) === i)
            """)
        except Exception as exc:
            self.log_error(f"Extracting IDs from search page: {exc}")
            return []

        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for link in links:
            m = re.search(r'-(\d+)/?$', link)
            if m:
                lid = m.group(1)
                if lid not in seen:
                    seen.add(lid)
                    clean_url = link.rstrip('/') + '/'
                    result.append((lid, clean_url))
        return result

    async def _extract_images(self, page) -> list[str]:
        try:
            srcs = await page.eval_on_selector_all(
                'img[src*="i-sgcm.com"], img[alt*="photo"], [class*="gallery"] img, [class*="photo"] img',
                "els => els.map(e => e.src).filter(s => s && !s.includes('logo') && !s.includes('icon') && !s.includes('svg'))",
            )
            result = []
            for src in srcs:
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = self.BASE_URL + src
                if src and src not in result:
                    result.append(src)
            return result[:30]
        except Exception:
            return []

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
