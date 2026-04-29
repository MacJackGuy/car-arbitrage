"""
PistonHeads diagnostic — dumps __NEXT_DATA__ structure and Apollo state
from a single make page so we can see exactly what the scraper receives.

Usage (no VPN required):
    cd /path/to/car-arbitrage
    source .venv/bin/activate
    python scripts/ph_diag.py [make-slug]   # default: aston-martin
"""
from __future__ import annotations

import asyncio
import json
import sys

from playwright.async_api import async_playwright


MAKE_SLUG = sys.argv[1] if len(sys.argv) > 1 else "aston-martin"
URL = f"https://www.pistonheads.com/buy/{MAKE_SLUG}?postcode=EH11AD&sort=date-desc"


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="en-GB",
            timezone_id="Europe/London",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        print(f"Navigating to {URL} …")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)

        # ── 1. Raw __NEXT_DATA__ top-level keys ──────────────────────────────
        nd_keys = await page.evaluate("""
            () => {
                try {
                    const nd = JSON.parse(document.getElementById('__NEXT_DATA__').textContent);
                    return Object.keys(nd);
                } catch(e) { return ['ERROR: ' + e.message]; }
            }
        """)
        print(f"\n[1] __NEXT_DATA__ top-level keys: {nd_keys}")

        # ── 2. pageProps keys ────────────────────────────────────────────────
        pp_keys = await page.evaluate("""
            () => {
                try {
                    const nd = JSON.parse(document.getElementById('__NEXT_DATA__').textContent);
                    return Object.keys(nd?.props?.pageProps || {});
                } catch(e) { return ['ERROR: ' + e.message]; }
            }
        """)
        print(f"[2] pageProps keys: {pp_keys}")

        # ── 3. Apollo state present? ─────────────────────────────────────────
        apollo_keys_sample = await page.evaluate("""
            () => {
                try {
                    const nd = JSON.parse(document.getElementById('__NEXT_DATA__').textContent);
                    const apollo = nd?.props?.pageProps?.__APOLLO_STATE__;
                    if (!apollo) return null;
                    return Object.keys(apollo).slice(0, 20);
                } catch(e) { return ['ERROR: ' + e.message]; }
            }
        """)
        print(f"[3] Apollo state keys (first 20): {apollo_keys_sample}")

        # ── 4. Count Advert: keys in Apollo state ────────────────────────────
        advert_count = await page.evaluate("""
            () => {
                try {
                    const nd = JSON.parse(document.getElementById('__NEXT_DATA__').textContent);
                    const apollo = nd?.props?.pageProps?.__APOLLO_STATE__;
                    if (!apollo) return -1;
                    return Object.keys(apollo).filter(k => k.startsWith('Advert:')).length;
                } catch(e) { return -2; }
            }
        """)
        print(f"[4] 'Advert:' keys in Apollo state: {advert_count}")

        # ── 5. If no Apollo, look for alternate data locations ────────────────
        if not apollo_keys_sample:
            alt = await page.evaluate("""
                () => {
                    try {
                        const nd = JSON.parse(document.getElementById('__NEXT_DATA__').textContent);
                        // Walk pageProps looking for arrays that might be listings
                        const pp = nd?.props?.pageProps || {};
                        const result = {};
                        for (const [k, v] of Object.entries(pp)) {
                            if (Array.isArray(v)) result[k] = `Array[${v.length}]`;
                            else if (v && typeof v === 'object') result[k] = `Object{${Object.keys(v).slice(0,5).join(',')}}`;
                            else result[k] = typeof v;
                        }
                        return result;
                    } catch(e) { return {error: e.message}; }
                }
            """)
            print(f"[5] pageProps structure (no Apollo found): {json.dumps(alt, indent=2)}")

        # ── 6. Sample one Advert object if any exist ─────────────────────────
        if advert_count and advert_count > 0:
            sample_advert = await page.evaluate("""
                () => {
                    try {
                        const nd = JSON.parse(document.getElementById('__NEXT_DATA__').textContent);
                        const apollo = nd?.props?.pageProps?.__APOLLO_STATE__;
                        const key = Object.keys(apollo).find(k => k.startsWith('Advert:'));
                        return apollo[key];
                    } catch(e) { return {error: e.message}; }
                }
            """)
            print(f"\n[6] Sample Advert object keys: {list(sample_advert.keys()) if isinstance(sample_advert, dict) else sample_advert}")
            if isinstance(sample_advert, dict):
                for k, v in sample_advert.items():
                    print(f"    {k}: {repr(v)[:120]}")

        # ── 7. Check page title / H1 (confirms correct page loaded) ──────────
        title = await page.title()
        h1 = await page.evaluate("() => document.querySelector('h1')?.innerText || ''")
        print(f"\n[7] Page title: {title!r}")
        print(f"    H1: {h1!r}")

        # ── 8. Raw NEXT_DATA size ─────────────────────────────────────────────
        nd_size = await page.evaluate("""
            () => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent.length : 0;
            }
        """)
        print(f"[8] __NEXT_DATA__ raw size: {nd_size:,} chars")

        # ── 9. Check for alternate listing containers ─────────────────────────
        listing_count_dom = await page.evaluate("""
            () => document.querySelectorAll('[data-testid*="listing"], [class*="listing"], article').length
        """)
        print(f"[9] DOM listing-like elements: {listing_count_dom}")

        await browser.close()
        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
