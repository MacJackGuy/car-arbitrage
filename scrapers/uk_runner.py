"""
UK scraper runner — orchestrates active UK scrapers with VPN guard,
sequential execution, and unified reporting.

Execution order is chosen by data quality and reliability:
  1. Car & Classic — Inertia JSON, no Cloudflare, server-rendered; cleanest data first
  2. PistonHeads   — __NEXT_DATA__ dump, no Cloudflare, richest structured data
  3. AutoTrader    — React SPA + Cloudflare (may need residential proxy)
  4. eBay Motors   — Marko SSR DOM; runs last to get freshest VPN IP after rotation

Disabled scrapers:
  - JamesEdition   — Cloudflare ASN ban on IVPN ASN 9009; needs residential proxy
  - Collecting Cars — deleted (domain/Cloudflare blocked, no usable data)
  - Classic Cars UK — deleted (SSL cert mismatch, domain dead)

Usage
──────
CLI:
    python -m scrapers.uk_runner              # incremental (default)
    python -m scrapers.uk_runner --full       # full rescrape
    python -m scrapers.uk_runner --source pistonheads
    python -m scrapers.uk_runner --source autotrader --source carandclassic

Programmatic:
    import asyncio
    from scrapers.uk_runner import run_uk_scrapers
    asyncio.run(run_uk_scrapers(mode="full"))
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from typing import Optional

import config
from utils.ivpn import log_active_server, rotate_server_async
from scrapers.pistonheads   import PistonHeadsScraper
from scrapers.carandclassic import CarAndClassicScraper
from scrapers.ebay_motors   import EbayMotorsScraper
from scrapers.autotrader    import AutoTraderScraper
# JamesEdition disabled — Cloudflare ASN ban on IVPN ASN 9009; needs residential proxy
# from scrapers.jamesedition import JamesEditionScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Execution order: C&C first (no Cloudflare, cleanest data), eBay last (gets freshest VPN IP)
_SCRAPERS = [
    CarAndClassicScraper,
    PistonHeadsScraper,
    AutoTraderScraper,
    EbayMotorsScraper,
    # JamesEditionScraper — disabled, needs residential proxy (IVPN ASN 9009 blocked)
]


async def run_uk_scrapers(
    mode: str = "incremental",
    db_path: str = config.DB_PATH,
    sources: Optional[list[str]] = None,
) -> dict[str, tuple[int, int]]:
    """
    Run all (or selected) UK scrapers.

    Args:
        mode:    "full" for complete rescrape, "incremental" for new listings only.
        db_path: Path to the SQLite database.
        sources: Optional list of source names (e.g. ["pistonheads", "autotrader"]).

    Returns:
        Dict mapping source → (new_count, updated_count).
    """
    results: dict[str, tuple[int, int]] = {}
    started = datetime.now()
    log.info("=== UK scrape run starting [mode=%s] ===", mode)
    log_active_server()

    for ScraperClass in _SCRAPERS:
        if sources and ScraperClass.SOURCE not in sources:
            continue

        log.info("--- Starting %s ---", ScraperClass.SOURCE)
        try:
            # Rotate VPN exit node before eBay to get a fresh IP
            if ScraperClass.SOURCE == "ebay_motors":
                await rotate_server_async(wait_s=8)

            async with ScraperClass(db_path=db_path, run_type=mode) as scraper:
                new, updated = await scraper.run()
            results[ScraperClass.SOURCE] = (new, updated)
            log.info(
                "--- %s done: new=%d updated=%d ---",
                ScraperClass.SOURCE, new, updated,
            )

        except RuntimeError as exc:
            # VPN not detected or other hard stop — abort entire run
            log.error("FATAL [%s]: %s — aborting run", ScraperClass.SOURCE, exc)
            break

        except Exception as exc:
            log.error("[%s] Scraper failed: %s", ScraperClass.SOURCE, exc, exc_info=True)
            results[ScraperClass.SOURCE] = (0, 0)

    elapsed = (datetime.now() - started).total_seconds()
    total_new = sum(r[0] for r in results.values())
    total_upd = sum(r[1] for r in results.values())
    log.info(
        "=== UK run complete in %.0fs: %d new, %d updated across %d sources ===",
        elapsed, total_new, total_upd, len(results),
    )
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run UK car listing scrapers")
    p.add_argument("--full", action="store_true",
                   help="Full rescrape (default: incremental / delta only)")
    p.add_argument(
        "--source",
        choices=[s.SOURCE for s in _SCRAPERS],
        action="append",
        dest="sources",
        help="Scrape only this source (repeatable)",
    )
    p.add_argument("--db", default=config.DB_PATH,
                   help=f"SQLite database path (default: {config.DB_PATH})")
    return p.parse_args()


if __name__ == "__main__":
    args   = _parse_args()
    mode   = "full" if args.full else "incremental"
    results = asyncio.run(
        run_uk_scrapers(mode=mode, db_path=args.db, sources=args.sources)
    )

    print(f"\n{'Source':<18} {'New':>6} {'Updated':>8}")
    print("-" * 34)
    for source, (new, upd) in results.items():
        print(f"{source:<18} {new:>6} {upd:>8}")
    print("-" * 34)
    total_new = sum(r[0] for r in results.values())
    total_upd = sum(r[1] for r in results.values())
    print(f"{'TOTAL':<18} {total_new:>6} {total_upd:>8}")
    sys.exit(0)
