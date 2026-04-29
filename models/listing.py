"""
Dataclasses representing database entities.

These map 1-to-1 with schema.sql tables and serve as typed wrappers
around the raw dicts that come off sqlite3.Row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Listing:
    """A vehicle listing from any source (SG or UK)."""
    source: str         # sgcarmart | carousell | carro | autotrader | …
    market: str         # SG | UK
    source_url: str
    make: str
    model: str

    id:                     Optional[int]   = None
    source_listing_id:      Optional[str]   = None
    variant:                Optional[str]   = None
    year_manufactured:      Optional[int]   = None
    year_registered:        Optional[int]   = None
    mileage_km:             Optional[int]   = None

    price_sgd:              Optional[float] = None
    price_gbp:              Optional[float] = None

    # SG registration data
    coe_expiry_date:        Optional[str]   = None      # ISO-8601 date
    coe_months_remaining:   Optional[int]   = None
    coe_original_value_sgd: Optional[float] = None
    arf_paid_sgd:           Optional[float] = None
    omv_sgd:                Optional[float] = None

    # Physical / spec
    colour:                 Optional[str]   = None
    interior_colour:        Optional[str]   = None
    num_owners:             Optional[int]   = None
    is_direct_owner:        bool            = False
    has_service_history:    Optional[bool]  = None
    ppi_mentioned:          bool            = False
    has_aftermarket_mods:   Optional[bool]  = None
    seat_type:              Optional[str]   = None  # daytona | sport_buckets | standard | unknown
    has_ccm_brakes:         Optional[bool]  = None

    # Seller
    seller_name:            Optional[str]   = None
    seller_type:            Optional[str]   = None  # private | dealer
    description_text:       Optional[str]   = None
    image_urls:             list[str]       = field(default_factory=list)

    # Tracking
    listing_date:           Optional[str]   = None
    days_on_market:         Optional[int]   = None
    is_active:              bool            = True
    first_seen_at:          Optional[str]   = None
    last_seen_at:           Optional[str]   = None

    def to_db_dict(self) -> dict:
        """Return a flat dict suitable for upsert_listing()."""
        d = {
            "source":                   self.source,
            "market":                   self.market,
            "source_url":               self.source_url,
            "make":                     self.make,
            "model":                    self.model,
            "source_listing_id":        self.source_listing_id,
            "variant":                  self.variant,
            "year_manufactured":        self.year_manufactured,
            "year_registered":          self.year_registered,
            "mileage_km":               self.mileage_km,
            "price_sgd":                self.price_sgd,
            "price_gbp":                self.price_gbp,
            "coe_expiry_date":          self.coe_expiry_date,
            "coe_months_remaining":     self.coe_months_remaining,
            "coe_original_value_sgd":   self.coe_original_value_sgd,
            "arf_paid_sgd":             self.arf_paid_sgd,
            "omv_sgd":                  self.omv_sgd,
            "colour":                   self.colour,
            "interior_colour":          self.interior_colour,
            "num_owners":               self.num_owners,
            "is_direct_owner":          int(self.is_direct_owner),
            "has_service_history":      None if self.has_service_history is None
                                             else int(self.has_service_history),
            "ppi_mentioned":            int(self.ppi_mentioned),
            "has_aftermarket_mods":     None if self.has_aftermarket_mods is None
                                             else int(self.has_aftermarket_mods),
            "seat_type":                self.seat_type,
            "has_ccm_brakes":           None if self.has_ccm_brakes is None
                                             else int(self.has_ccm_brakes),
            "seller_name":              self.seller_name,
            "seller_type":              self.seller_type,
            "description_text":         self.description_text,
            "image_urls":               json.dumps(self.image_urls),
            "listing_date":             self.listing_date,
            "days_on_market":           self.days_on_market,
            "is_active":                int(self.is_active),
        }
        # Drop None values so SQLite DEFAULT expressions fire correctly
        return {k: v for k, v in d.items() if v is not None or k in
                {"price_sgd", "price_gbp", "coe_original_value_sgd",
                 "arf_paid_sgd", "omv_sgd", "has_service_history",
                 "has_aftermarket_mods", "has_ccm_brakes"}}

    @classmethod
    def from_row(cls, row: dict) -> "Listing":
        """Construct a Listing from a sqlite3.Row / dict."""
        r = dict(row)
        r["image_urls"] = json.loads(r.get("image_urls") or "[]")
        r["is_direct_owner"] = bool(r.get("is_direct_owner", 0))
        r["ppi_mentioned"] = bool(r.get("ppi_mentioned", 0))
        r["is_active"] = bool(r.get("is_active", 1))
        for flag in ("has_service_history", "has_aftermarket_mods", "has_ccm_brakes"):
            if flag in r and r[flag] is not None:
                r[flag] = bool(r[flag])
        return cls(**{k: v for k, v in r.items() if k in cls.__dataclass_fields__})


@dataclass
class UKSupply:
    """UK market supply data for a make/model/colour combination."""
    make:           str
    model:          str
    supply_count:   int     = 0
    id:             Optional[int]   = None
    variant:        Optional[str]   = None
    colour:         Optional[str]   = None
    year_min:       Optional[int]   = None
    year_max:       Optional[int]   = None
    price_min_gbp:  Optional[float] = None
    price_max_gbp:  Optional[float] = None
    price_avg_gbp:  Optional[float] = None
    last_updated:   Optional[str]   = None
