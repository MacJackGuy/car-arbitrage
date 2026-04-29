"""
Shared UK scraper search targets derived from config.TARGET_VEHICLES.

All UK scrapers import `search_pairs()` and the site-specific make/model
name maps defined here.  Keeps each scraper file free of duplication.
"""
from __future__ import annotations

from typing import Optional
import config


def search_pairs() -> list[tuple[str, Optional[str]]]:
    """
    Return a flat list of (canonical_make, model_or_None) for every target.
    When a make has no specific models listed, a single (make, None) pair is
    returned, meaning 'search all models for this make'.
    """
    pairs: list[tuple[str, Optional[str]]] = []
    for make, models in config.TARGET_VEHICLES.items():
        if not models:
            pairs.append((make, None))
        else:
            for model in models:
                pairs.append((make, model))
    return pairs


# ── Site-specific make name maps ──────────────────────────────────────────────
# Override where a site's search field differs from our canonical name.

# AutoTrader uses mixed-case make names (same as canonical, generally)
AUTOTRADER_MAKE: dict[str, str] = {
    "Aston Martin":  "Aston Martin",
    "Audi":          "Audi",
    "Bentley":       "Bentley",
    "BMW":           "BMW",
    "Ferrari":       "Ferrari",
    "Lamborghini":   "Lamborghini",
    "Maserati":      "Maserati",
    "McLaren":       "McLaren",
    "Mercedes-Benz": "Mercedes-Benz",
    "Porsche":       "Porsche",
    "Rolls-Royce":   "Rolls-Royce",
}

# AutoTrader model slugs — canonical model → AT model param.
# AT uses base model names; variant suffixes (GT3, E46, V10…) are not valid.
# Multiple canonical models can share one AT slug (e.g. all 911 variants → "911");
# the scraper deduplicates searches so each (make, slug) pair runs only once.
AUTOTRADER_MODEL: dict[str, str] = {
    # Porsche — variant suffixes stripped to base AT model slug
    "911 GTS":         "911",
    "911 Turbo":       "911",
    "Cayenne GTS":     "Cayenne",
    "Cayenne Turbo":   "Cayenne",
    "Panamera GTS":    "Panamera",
    "Panamera Turbo":  "Panamera",
    "Taycan Turbo":    "Taycan",
    "Macan GTS":       "Macan",
    # BMW — X5M/X6M use a space on AutoTrader ("X5 M")
    "X5M":             "X5 M",
    "X6M":             "X6 M",
    # Mercedes-Benz — AT uses body-class names, not AMG variant suffixes
    "C63":             "C Class",
    "E63":             "E Class",
    "G63":             "G Class",
    "G500":            "G Class",
    "S63":             "S Class",
    "S65":             "S Class",
    "SLS":             "SLS AMG",
    "GLE63":           "GLE Class",
    "GLS63":           "GLS Class",
    # Aston Martin
    "DBX707":          "DBX",
    # Bentley — AT uses "Continental" not "Continental GT"
    "Continental GT":  "Continental",
    "Bentayga Speed":  "Bentayga",
    # Maserati
    "Levante Trofeo":  "Levante",
}

# Car&Classic uses lowercase, hyphenated for multi-word makes
CARANDCLASSIC_MAKE: dict[str, str] = {
    "Aston Martin":  "aston-martin",
    "Audi":          "audi",
    "Bentley":       "bentley",
    "BMW":           "bmw",
    "Ferrari":       "ferrari",
    "Lamborghini":   "lamborghini",
    "Maserati":      "maserati",
    "McLaren":       "mclaren",
    "Mercedes-Benz": "mercedes-benz",
    "Porsche":       "porsche",
    "Rolls-Royce":   "rolls-royce",
}

# Carro / JamesEdition use title-case in URL path
JAMESEDITION_MAKE: dict[str, str] = {
    "Aston Martin":  "aston-martin",
    "Audi":          "audi",
    "Bentley":       "bentley",
    "BMW":           "bmw",
    "Ferrari":       "ferrari",
    "Lamborghini":   "lamborghini",
    "Maserati":      "maserati",
    "McLaren":       "mclaren",
    "Mercedes-Benz": "mercedes-benz",
    "Porsche":       "porsche",
    "Rolls-Royce":   "rolls-royce",
}

# PistonHeads make-level numeric IDs (used for /buy/search?make-id=N sweep).
# Verified 2025-04 by scraping the make index page "View all" link.
PISTONHEADS_MAKE_ID: dict[str, int] = {
    "Aston Martin":  14,
    "Audi":          15,
    "Bentley":       16,
    "BMW":           17,
    "Ferrari":       23,
    "Lamborghini":   28,
    "Maserati":      78,
    "McLaren":       119,
    "Mercedes-Benz": 35,
    "Porsche":       43,
    "Rolls-Royce":   46,
}

# PistonHeads uses /buy/{slug} — lowercase, hyphenated
PISTONHEADS_MAKE: dict[str, str] = {
    "Aston Martin":  "aston-martin",
    "Audi":          "audi",
    "Bentley":       "bentley",
    "BMW":           "bmw",
    "Ferrari":       "ferrari",
    "Lamborghini":   "lamborghini",
    "Maserati":      "maserati",
    "McLaren":       "mclaren",
    "Mercedes-Benz": "mercedes-benz",
    "Porsche":       "porsche",
    "Rolls-Royce":   "rolls-royce",
}

# classiccars.co.uk make IDs (numeric or slug — use slug)
CLASSICCARS_MAKE: dict[str, str] = {
    "Aston Martin":  "Aston+Martin",
    "Audi":          "Audi",
    "Bentley":       "Bentley",
    "BMW":           "BMW",
    "Ferrari":       "Ferrari",
    "Lamborghini":   "Lamborghini",
    "Maserati":      "Maserati",
    "McLaren":       "McLaren",
    "Mercedes-Benz": "Mercedes-Benz",
    "Porsche":       "Porsche",
    "Rolls-Royce":   "Rolls-Royce",
}
