"""
BaseScraper — async context manager providing Playwright browser lifecycle,
VPN enforcement, randomised delays, rotating user agents, and DB run logging.

All three SG scrapers inherit from this class.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Response,
)

import config
from database.db import get_conn, init_db, upsert_listing
from scrapers.user_agents import random_ua

log = logging.getLogger(__name__)


# ── Stealth init script injected into every new context ──────────────────────
_STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    window.chrome = { runtime: {} };
"""


class BaseScraper(ABC):
    """
    Async context manager.  Subclasses implement `run(known_ids)`.

    Usage:
        async with SgCarMartScraper(db_path, run_type="delta") as scraper:
            new, updated = await scraper.run(known_ids={"12345", "67890"})
    """

    SOURCE: str = ""          # override in subclass
    MARKET: str = "SG"
    LOCALE: str = "en-SG"
    TIMEZONE_ID: str = "Asia/Singapore"
    # VPN exit-country enforcement.  UK scrapers require "GB"; SG scrapers
    # set "SG".  Set REQUIRES_VPN = False to skip the check entirely (e.g.
    # Carousell, which blocks VPN IPs and must run on the home IP).
    VPN_COUNTRY: str | None = "GB"
    REQUIRES_VPN: bool = True

    def __init__(
        self,
        db_path: str = config.DB_PATH,
        run_type: str = "full",   # "full" | "incremental"
    ):
        self.db_path = db_path
        self.run_type = run_type

        # runtime state
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._run_id: Optional[int] = None
        self._found = 0
        self._new = 0
        self._updated = 0
        self._errors: list[str] = []

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "BaseScraper":
        # 1. VPN guard
        from utils.vpn import require_vpn
        if self.REQUIRES_VPN:
            ip = require_vpn(expected_country=self.VPN_COUNTRY)
            log.info("[%s] VPN confirmed (%s)", self.SOURCE, ip)
        else:
            import requests
            try:
                ip = requests.get("https://ipinfo.io/json", timeout=10).json().get("ip", "unknown")
            except Exception:
                ip = "unknown"
            log.info("[%s] Running without VPN (home IP: %s)", self.SOURCE, ip)

        # 2. Ensure DB schema exists
        init_db(self.db_path)

        # 3. Launch browser
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-web-security",
            ],
        )
        log.info("[%s] Browser started", self.SOURCE)

        # 4. Log scrape run start
        with get_conn(self.db_path) as conn:
            conn.execute(
                """INSERT INTO scrape_runs
                   (source, run_type, vpn_verified, ip_address, started_at, status)
                   VALUES (?, ?, 1, ?, datetime('now'), 'running')""",
                (self.SOURCE, self.run_type, ip),
            )
            self._run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        return self

    async def restart_browser(self) -> None:
        """Close and relaunch the browser to get a fresh process and fingerprint."""
        if self._browser:
            await self._browser.close()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-web-security",
            ],
        )
        log.debug("[%s] Browser restarted", self.SOURCE)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

        status = "completed" if exc_type is None else "failed"
        if exc_val:
            self._errors.append(f"{exc_type.__name__}: {exc_val}")

        with get_conn(self.db_path) as conn:
            conn.execute(
                """UPDATE scrape_runs SET
                   status = ?, completed_at = datetime('now'),
                   listings_found = ?, new_listings = ?,
                   updated_listings = ?, errors_json = ?
                   WHERE id = ?""",
                (
                    status,
                    self._found,
                    self._new,
                    self._updated,
                    json.dumps(self._errors),
                    self._run_id,
                ),
            )
        log.info(
            "[%s] Run finished: status=%s found=%d new=%d updated=%d errors=%d",
            self.SOURCE, status, self._found, self._new, self._updated, len(self._errors),
        )
        return False  # do not suppress exceptions

    # ── Browser helpers ───────────────────────────────────────────────────────

    async def new_context(self) -> BrowserContext:
        """Create a fresh browser context with a random user agent and stealth settings."""
        lang = self.LOCALE.split("-")[0]
        ctx = await self._browser.new_context(
            user_agent=random_ua(),
            viewport={"width": random.choice([1920, 1440, 1366]), "height": random.choice([1080, 900, 768])},
            locale=self.LOCALE,
            timezone_id=self.TIMEZONE_ID,
            extra_http_headers={
                "Accept-Language": f"{self.LOCALE},{lang};q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            },
        )
        await ctx.add_init_script(_STEALTH_SCRIPT)
        return ctx

    async def delay(self, min_s: float = 8.0, max_s: float = 30.0) -> None:
        """Random human-paced delay between requests."""
        secs = random.uniform(min_s, max_s)
        log.debug("[%s] Sleeping %.1fs", self.SOURCE, secs)
        await asyncio.sleep(secs)

    async def short_delay(self) -> None:
        """Shorter delay for within-page actions (clicks, scrolls)."""
        await asyncio.sleep(random.uniform(1.0, 3.5))

    async def safe_goto(
        self,
        page: Page,
        url: str,
        wait: str = "networkidle",
        retries: int = 3,
        timeout_ms: int = 30_000,
    ) -> bool:
        """Navigate to URL with retries. Returns True on success.

        Uses asyncio.wait_for as a hard outer timeout so ERR_NETWORK_CHANGED
        hangs (where Playwright's internal timeout silently stops firing) are
        always killed within timeout_ms + 5s.
        """
        hard_timeout_s = (timeout_ms / 1000) + 5
        for attempt in range(retries):
            try:
                await asyncio.wait_for(
                    page.goto(url, wait_until=wait, timeout=timeout_ms),
                    timeout=hard_timeout_s,
                )
                return True
            except Exception as exc:
                if attempt < retries - 1:
                    wait_s = random.uniform(3, 8) * (attempt + 1)
                    log.warning("[%s] goto failed (attempt %d/%d): %s — retrying in %.0fs",
                                self.SOURCE, attempt + 1, retries, exc, wait_s)
                    await asyncio.sleep(wait_s)
                else:
                    msg = f"Failed to load {url} after {retries} attempts: {exc}"
                    log.error("[%s] %s", self.SOURCE, msg)
                    self._errors.append(msg)
                    return False
        return False

    async def scroll_to_bottom(self, page: Page, pauses: int = 3) -> None:
        """Scroll to bottom of page to trigger lazy-load content."""
        for _ in range(pauses):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(random.uniform(1.5, 3.0))

    # ── DB helpers ────────────────────────────────────────────────────────────

    def save_listing(self, listing: dict) -> tuple[int, bool]:
        """
        Upsert a listing dict to the DB.
        Returns (listing_id, is_new).
        """
        with get_conn(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM listings WHERE source_url = ?",
                (listing.get("source_url", ""),),
            ).fetchone()
            is_new = existing is None
            listing_id = upsert_listing(conn, listing)

        self._found += 1
        if is_new:
            self._new += 1
        else:
            self._updated += 1

        return listing_id, is_new

    def get_known_ids(self) -> set[str]:
        """Return all known source_listing_ids for this source from the DB."""
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT source_listing_id FROM listings WHERE source = ? AND source_listing_id IS NOT NULL",
                (self.SOURCE,),
            ).fetchall()
        return {row[0] for row in rows}

    def log_error(self, msg: str) -> None:
        log.error("[%s] %s", self.SOURCE, msg)
        self._errors.append(msg)

    # ── Subclass interface ────────────────────────────────────────────────────

    @abstractmethod
    async def run(self, known_ids: Optional[set[str]] = None) -> tuple[int, int]:
        """
        Execute the scrape.
        known_ids: set of source_listing_ids already in DB (for delta mode).
        Returns (new_count, updated_count).
        """
