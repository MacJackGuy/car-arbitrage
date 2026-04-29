"""
Diagnostic script: inspect eBay UK DOM to find why card extraction fails for BMW.
Run with VPN active:  python scripts/debug_ebay.py
"""
from __future__ import annotations
import asyncio
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright
from scrapers.user_agents import random_ua

_SEARCH_URL = (
    "https://www.ebay.co.uk/sch/Cars-/9801/i.html"
    "?_nkw=BMW&LH_PrefLoc=1&_sop=10&LH_ItemCondition=3000&_ipg=60"
)


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(
            user_agent=random_ua(),
            locale="en-GB",
            timezone_id="Europe/London",
        )
        page = await ctx.new_page()
        print(f"Loading: {_SEARCH_URL}")
        await page.goto(_SEARCH_URL, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # ── Selector tests ────────────────────────────────────────────────────
        selectors = {
            "li[data-listingid]":                   'li[data-listingid]',
            ".s-item:not(.s-item--placeholder)":    '.s-item:not(.s-item--placeholder)',
            "[data-view='mi:1686']":                "[data-view='mi:1686']",
            ".srp-results li":                      ".srp-results li",
            "li.s-item":                            "li.s-item",
            ".s-main-content li":                   ".s-main-content li",
            "ul.srp-results > li":                  "ul.srp-results > li",
        }
        print("\n=== Selector counts ===")
        for label, sel in selectors.items():
            n = await page.evaluate(f"document.querySelectorAll({json.dumps(sel)}).length")
            print(f"  {n:>4}  {label}")

        # ── Sample first item from best matching selector ──────────────────────
        print("\n=== First .s-item outerHTML (truncated) ===")
        html = await page.evaluate("""
            () => {
                const el = document.querySelector('.s-item:not(.s-item--placeholder)');
                return el ? el.outerHTML.slice(0, 2000) : 'NOT FOUND';
            }
        """)
        print(html[:2000])

        # ── Try current extraction JS and show results ────────────────────────
        print("\n=== Current extraction JS — first 5 results ===")
        results = await page.evaluate(r"""
        () => {
            const results = [];
            const items = document.querySelectorAll('li[data-listingid], .s-item:not(.s-item--placeholder)');
            items.forEach(li => {
                const lid = li.getAttribute('data-listingid') || '';
                const a   = li.querySelector('a.s-item__link, a[href*="/itm/"]');
                const url = a ? a.href : '';
                const titleEl = li.querySelector('.s-item__title, h3.s-item__title');
                const title   = titleEl ? titleEl.innerText.trim() : '';
                const idFromUrl = url.match(/\/itm\/(\d+)/);
                results.push({
                    lid, url: url.slice(0, 80), title: title.slice(0, 60),
                    id_from_url: idFromUrl ? idFromUrl[1] : 'NO MATCH'
                });
            });
            return results.slice(0, 5);
        }
        """)
        for r in results:
            print(r)

        # ── Test updated regex for slug-format URLs ───────────────────────────
        print("\n=== Updated regex test (handles slug-format URLs) ===")
        results2 = await page.evaluate(r"""
        () => {
            const results = [];
            const items = document.querySelectorAll('.s-item:not(.s-item--placeholder)');
            items.forEach(li => {
                const a   = li.querySelector('a[href*="/itm/"]');
                const url = a ? a.href : '';
                // New regex: handles /itm/SLUG/ID and /itm/ID formats
                const idFromUrl = url.match(/\/itm\/(?:[^\/\?]+\/)?(\d+)/);
                const lid = li.getAttribute('data-listingid') || (idFromUrl ? idFromUrl[1] : '');
                results.push({ lid, url: url.slice(0, 100) });
            });
            return results.filter(r => r.lid).slice(0, 5);
        }
        """)
        for r in results2:
            print(r)

        # ── Check what URL format eBay is using ───────────────────────────────
        print("\n=== First 3 /itm/ links found ===")
        links = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href*="/itm/"]'))
                .map(a => a.href).slice(0, 3)
        """)
        for link in links:
            print(link)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
