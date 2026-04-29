"""
SG scraper runner — orchestrates all three SG scrapers with VPN guard,
sequential execution, and unified reporting.

Usage
──────
CLI:
    python -m scrapers.runner              # incremental (default)
    python -m scrapers.runner --full       # full rescrape

Programmatic:
    import asyncio
    from scrapers.runner import run_sg_scrapers
    asyncio.run(run_sg_scrapers(mode="full"))
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from typing import Optional

import config
from scrapers.sgcarmart import SgCarMartScraper
from scrapers.carousell import CarousellScraper
from scrapers.carro import CarroScraper
from utils.ivpn import disconnect, switch_to_singapore, switch_to_uk

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# SG VPN scrapers run first (sgCarMart + Carro), then VPN is dropped and
# Carousell runs on the home IP (it blocks all VPN exit nodes).
_SG_VPN_SCRAPERS = [SgCarMartScraper, CarroScraper]
_NO_VPN_SCRAPERS = [CarousellScraper]


async def run_sg_scrapers(
    mode: str = "incremental",
    db_path: str = config.DB_PATH,
    sources: Optional[list[str]] = None,
) -> dict[str, tuple[int, int]]:
    """
    Run all (or selected) SG scrapers.

    Args:
        mode:    "full" for complete rescrape, "incremental" for new listings only.
        db_path: Path to the SQLite database.
        sources: Optional list of source names to run (e.g. ["sgcarmart"]).
                 Defaults to all three.

    Returns:
        Dict mapping source name → (new_count, updated_count).
    """
    results: dict[str, tuple[int, int]] = {}
    started = datetime.now()
    log.info("=== SG scrape run starting [mode=%s] ===", mode)

    # Switch to Singapore exit node — SG sites (sgCarMart, Carro) return 403
    # Cloudflare blocks for UK IPs. Switch back to UK when done.
    log.info("[runner] Switching IVPN to Singapore exit node...")
    try:
        switch_to_singapore(wait_s=15)
        log.info("[runner] IVPN now on Singapore exit node")
    except Exception as exc:
        log.error("[runner] Failed to switch to Singapore server: %s — aborting", exc)
        return results

    try:
        # ── Phase 1: SG VPN scrapers (sgCarMart, Carro) ───────────────────────
        abort = False
        for ScraperClass in _SG_VPN_SCRAPERS:
            if sources and ScraperClass.SOURCE not in sources:
                continue
            log.info("--- Starting %s ---", ScraperClass.SOURCE)
            try:
                async with ScraperClass(db_path=db_path, run_type=mode) as scraper:
                    new, updated = await scraper.run()
                results[ScraperClass.SOURCE] = (new, updated)
                log.info("--- %s done: new=%d updated=%d ---", ScraperClass.SOURCE, new, updated)
            except RuntimeError as exc:
                log.error("FATAL [%s]: %s — aborting run", ScraperClass.SOURCE, exc)
                abort = True
                break
            except Exception as exc:
                log.error("[%s] Scraper failed: %s", ScraperClass.SOURCE, exc, exc_info=True)
                results[ScraperClass.SOURCE] = (0, 0)

        # ── Phase 2: no-VPN scrapers (Carousell — blocks all VPN IPs) ─────────
        if not abort:
            no_vpn_needed = [s for s in _NO_VPN_SCRAPERS
                             if not sources or s.SOURCE in sources]
            if no_vpn_needed:
                log.info("[runner] Disconnecting VPN for home-IP scrapers...")
                try:
                    disconnect(wait_s=8)
                    log.info("[runner] VPN disconnected — running on home IP")
                except Exception as exc:
                    log.error("[runner] Failed to disconnect VPN: %s — skipping no-VPN scrapers", exc)
                    no_vpn_needed = []

                for ScraperClass in no_vpn_needed:
                    log.info("--- Starting %s ---", ScraperClass.SOURCE)
                    try:
                        async with ScraperClass(db_path=db_path, run_type=mode) as scraper:
                            new, updated = await scraper.run()
                        results[ScraperClass.SOURCE] = (new, updated)
                        log.info("--- %s done: new=%d updated=%d ---",
                                 ScraperClass.SOURCE, new, updated)
                    except RuntimeError as exc:
                        log.error("FATAL [%s]: %s — aborting run", ScraperClass.SOURCE, exc)
                        break
                    except Exception as exc:
                        log.error("[%s] Scraper failed: %s", ScraperClass.SOURCE, exc, exc_info=True)
                        results[ScraperClass.SOURCE] = (0, 0)

                # Reconnect to SG so the finally block can switch to UK cleanly
                log.info("[runner] Reconnecting IVPN to Singapore before UK switch...")
                try:
                    switch_to_singapore(wait_s=12)
                except Exception as exc:
                    log.warning("[runner] Failed to reconnect to SG: %s", exc)

    finally:
        log.info("[runner] Switching IVPN back to UK exit node...")
        try:
            switch_to_uk(wait_s=12)
            log.info("[runner] IVPN back on UK exit node")
        except Exception as exc:
            log.warning("[runner] Failed to switch back to UK: %s", exc)

    elapsed = (datetime.now() - started).total_seconds()
    total_new = sum(r[0] for r in results.values())
    total_upd = sum(r[1] for r in results.values())
    log.info(
        "=== Run complete in %.0fs: %d new, %d updated across %d sources ===",
        elapsed, total_new, total_upd, len(results),
    )
    return results


# ── CLI entry point ───────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run SG car listing scrapers")
    p.add_argument("--full", action="store_true",
                   help="Full rescrape (default: incremental / delta only)")
    p.add_argument("--source", choices=["sgcarmart", "carousell", "carro"],
                   action="append", dest="sources",
                   help="Scrape only this source (can be repeated)")
    p.add_argument("--db", default=config.DB_PATH,
                   help=f"SQLite database path (default: {config.DB_PATH})")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    mode = "full" if args.full else "incremental"
    results = asyncio.run(
        run_sg_scrapers(mode=mode, db_path=args.db, sources=args.sources)
    )

    # Print summary table
    print(f"\n{'Source':<16} {'New':>6} {'Updated':>8}")
    print("-" * 32)
    for source, (new, upd) in results.items():
        print(f"{source:<16} {new:>6} {upd:>8}")
    print("-" * 32)
    total_new = sum(r[0] for r in results.values())
    total_upd = sum(r[1] for r in results.values())
    print(f"{'TOTAL':<16} {total_new:>6} {total_upd:>8}")
    sys.exit(0)
