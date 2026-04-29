"""
Carro scraper (carro.co/sg/en/buy/used).

Site characteristics (2025 → 2026)
────────────────────────────────────
• Carro migrated from a custom REST API to Algolia for listing search.
• Algolia index: carro_showroom_sort_newest  (sorted newest-first by default)
• App ID: MB7388LVJ4  |  Search-only API key embedded in the JS bundle.
• Hit structure: { id, created_at, inventory: {...}, listing: {...} }
• Listing URL: https://carro.co/sg/en/buy/<slug>
• All listings on Carro are dealer (Carro is a dealer/certified platform).
• Results are queried via POST to the Algolia REST API — no Playwright needed
  for data extraction; the BaseScraper browser is launched but not used.

Delta strategy
──────────────
Algolia index sorts newest-first.  We paginate page-by-page (50 hits/page)
and stop when a known listing ID appears.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Optional

import requests as req_lib

import config
from database.db import get_conn, init_db
from scrapers.base import BaseScraper
from scrapers.parsers import (
    clean,
    coe_months_remaining,
    parse_listing_date,
    parse_year,
)

log = logging.getLogger(__name__)

_BASE = "https://carro.co"
_BUY_BASE = f"{_BASE}/sg/en/buy"

# Algolia search-only config (embedded in Carro's JS bundle; safe to hardcode)
_ALGOLIA_APP_ID  = "MB7388LVJ4"
_ALGOLIA_API_KEY = "70ae7310e06fd7a68d9c217a6df412ab"
_ALGOLIA_INDEX   = "carro_showroom_sort_newest"
_ALGOLIA_URL     = (
    f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net"
    f"/1/indexes/{_ALGOLIA_INDEX}/query"
)
_ALGOLIA_HEADERS = {
    "x-algolia-application-id": _ALGOLIA_APP_ID,
    "x-algolia-api-key":        _ALGOLIA_API_KEY,
    "content-type":             "application/json",
}
# Fields we need — reduces response size
_ALGOLIA_ATTRS = [
    "id", "created_at",
    "inventory.make", "inventory.model", "inventory.year_of_manufacture",
    "inventory.original_registration_date", "inventory.coe_expiry_date",
    "inventory.engine_capacity", "inventory.number_of_owners",
    "inventory.color", "inventory.transmission",
    "listing.slug", "listing.title", "listing.asking_price",
    "listing.mileage", "listing.photos", "listing.is_sold",
    "listing.months_left",
]
_HITS_PER_PAGE = 50

# Canonical make → Algolia make name (as stored in inventory.make)
# Algolia filters are case-insensitive so canonical names work fine.
_CARRO_MAKES: dict[str, str] = {
    "Ferrari":       "Ferrari",
    "Lamborghini":   "Lamborghini",
    "McLaren":       "McLaren",
    "Porsche":       "Porsche",
    "Aston Martin":  "Aston Martin",
    "Bentley":       "Bentley",
    "BMW":           "BMW",
    "Audi":          "Audi",
    "Maserati":      "Maserati",
    "Mercedes-Benz": "Mercedes-Benz",
    "Rolls-Royce":   "Rolls-Royce",
}


class CarroScraper(BaseScraper):
    SOURCE = "carro"
    MARKET = "SG"
    VPN_COUNTRY = "SG"   # Cloudflare blocks non-SG IPs

    # ── Public interface ──────────────────────────────────────────────────────

    async def run(self, known_ids: Optional[set[str]] = None) -> tuple[int, int]:
        """Scrape all target makes from Carro via Algolia search API."""
        if known_ids is None and self.run_type == "incremental":
            known_ids = self.get_known_ids()

        session = req_lib.Session()
        session.headers.update(_ALGOLIA_HEADERS)

        for canonical_make in config.TARGET_VEHICLES:
            algolia_make = _CARRO_MAKES.get(canonical_make)
            if not algolia_make:
                log.warning("[carro] No Algolia make mapping for %s — skipping", canonical_make)
                continue

            log.info("[carro] Scraping make: %s", canonical_make)
            await self._scrape_make(session, canonical_make, algolia_make, known_ids)
            await asyncio.sleep(random.uniform(3, 8))

        return self._new, self._updated

    # ── Per-make pagination ───────────────────────────────────────────────────

    async def _scrape_make(
        self,
        session: req_lib.Session,
        canonical_make: str,
        algolia_make: str,
        known_ids: Optional[set[str]],
    ) -> None:
        page = 0
        seen_this_make: set[str] = set()

        while True:
            body = {
                "query":              "",
                "page":               page,
                "hitsPerPage":        _HITS_PER_PAGE,
                "filters":            f'inventory.make:"{algolia_make}" AND listing.is_sold:false',
                "attributesToRetrieve": _ALGOLIA_ATTRS,
            }

            loop = asyncio.get_event_loop()
            try:
                resp = await loop.run_in_executor(
                    None,
                    lambda b=body: session.post(_ALGOLIA_URL, json=b, timeout=20),
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                self.log_error(f"Algolia request page={page} make={algolia_make}: {exc}")
                break

            hits     = data.get("hits", [])
            nb_pages = data.get("nbPages", 1)
            log.info("[carro] %s page=%d/%d hits=%d", canonical_make, page, nb_pages - 1, len(hits))

            if not hits:
                break

            hit_known = False
            for hit in hits:
                lid = str(hit.get("id", ""))
                if not lid or lid in seen_this_make:
                    continue
                seen_this_make.add(lid)

                if known_ids and lid in known_ids:
                    log.info("[carro] Hit known id %s — delta caught up for %s", lid, canonical_make)
                    hit_known = True
                    break

                listing = self._normalise_hit(hit, canonical_make)
                if listing:
                    self.save_listing(listing)
                    log.info("[carro] Saved %s — %s %s S$%s",
                             lid, canonical_make,
                             listing.get("model", ""),
                             listing.get("price_sgd", ""))

            if hit_known or page >= nb_pages - 1:
                break

            page += 1
            await asyncio.sleep(random.uniform(1.5, 4.0))

    # ── Hit normalisation ─────────────────────────────────────────────────────

    def _normalise_hit(self, hit: dict, canonical_make: str) -> Optional[dict]:
        """Map an Algolia hit to our listing schema."""
        try:
            lid = str(hit.get("id", ""))
            if not lid:
                return None

            inv = hit.get("inventory", {})
            lst = hit.get("listing", {})

            # URL
            slug = lst.get("slug") or lid
            url = f"{_BUY_BASE}/{slug}"

            # Price — asking_price is integer SGD cents? No — spot check: 215000 = S$215k ✓
            price_raw = lst.get("asking_price")
            price_sgd = int(price_raw) if price_raw else None

            # Model — strip make prefix if present
            full_model = clean(inv.get("model") or lst.get("title") or "")
            model = re.sub(re.escape(canonical_make), "", full_model, flags=re.IGNORECASE).strip()[:80]
            if not model:
                model = full_model[:80]

            # Year
            year_manuf = inv.get("year_of_manufacture")
            if year_manuf:
                try:
                    year_manuf = int(year_manuf)
                except (ValueError, TypeError):
                    year_manuf = None
            reg_raw = inv.get("original_registration_date") or ""
            year_reg = parse_year(reg_raw[:4]) if reg_raw else None

            # Mileage (stored as integer km)
            mileage_raw = lst.get("mileage")
            mileage_km = int(mileage_raw) if mileage_raw else None

            # COE — already ISO date string (YYYY-MM-DD)
            coe_expiry = inv.get("coe_expiry_date") or None
            coe_months = coe_months_remaining(coe_expiry) if coe_expiry else None

            # Owners, colour
            owners_raw = inv.get("number_of_owners")
            num_owners = int(owners_raw) if owners_raw is not None else None
            colour     = clean(inv.get("color") or "")

            # Images
            photos = lst.get("photos") or []
            image_urls = [p for p in photos[:30] if isinstance(p, str) and p]

            # Listing date
            created_at   = hit.get("created_at") or ""
            listing_date = parse_listing_date(created_at) if created_at else None

            return {
                "source":               self.SOURCE,
                "market":               self.MARKET,
                "source_url":           url,
                "source_listing_id":    lid,
                "make":                 canonical_make,
                "model":                model,
                "year_manufactured":    year_manuf,
                "year_registered":      year_reg,
                "price_sgd":            price_sgd,
                "mileage_km":           mileage_km,
                "coe_expiry_date":      coe_expiry,
                "coe_months_remaining": coe_months,
                "colour":               colour,
                "num_owners":           num_owners,
                "seller_type":          "dealer",   # Carro is dealer-only
                "is_direct_owner":      0,
                "description_text":     None,
                "image_urls":           json.dumps(image_urls),
                "listing_date":         listing_date,
            }

        except Exception as exc:
            self.log_error(f"Carro normalise hit {hit.get('id')}: {exc}")
            return None
