"""
Playwright smoke test on a real JS-rendered careers page.

Target: Razorpay  (https://razorpay.com/jobs/jobs-all/)
Steps:
  1. Scrape listing page — extract all job listings
  2. Print count, first 3 titles, first detail URL
  3. Fetch the full JD from that detail URL via fetch_jd_javascript()
  4. Print first 300 chars of the JD text

Does NOT save anything to Google Sheets.

Usage:
    python3 test_playwright_real.py
"""

import asyncio
import sys
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from loguru import logger

from agents.scraping_agent import _clean_text, _fetch_jd_js_async

logger.remove()
logger.add(sys.stdout, level="INFO", format="{message}")

BASE_URL      = "https://razorpay.com"
LISTING_URL   = f"{BASE_URL}/jobs/jobs-all/"
WAIT_SELECTOR = "a[href*='/jobs/jobs-all/detail/']"
USER_AGENT    = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


async def scrape_listing() -> list[dict]:
    """Render the Razorpay jobs page and return a list of job dicts."""
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(user_agent=USER_AGENT, locale="en-US")
            page = await context.new_page()

            logger.info(f"Navigating to {LISTING_URL} ...")
            await page.goto(LISTING_URL, timeout=30_000, wait_until="domcontentloaded")

            logger.info(f"Waiting for job listings (selector: {WAIT_SELECTOR!r}) ...")
            try:
                await page.wait_for_selector(WAIT_SELECTOR, timeout=30_000)
                logger.info("Job listings appeared.")
            except PWTimeout:
                logger.warning("Selector timed out — scraping whatever has loaded.")

            html = await page.content()
        finally:
            await browser.close()

    soup = BeautifulSoup(html, "html.parser")
    raw_links = [
        a for a in soup.find_all("a", href=True)
        if "/jobs/jobs-all/detail/" in a.get("href", "")
    ]

    jobs = []
    for a in raw_links:
        title_el = a.select_one("[class*='jobTitle']")
        dept_el  = a.select_one("[class*='jobDept']")
        loc_el   = a.select_one("[class*='jobLocation'], [class*='location']")

        title    = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)[:80]
        dept     = dept_el.get_text(strip=True)  if dept_el  else ""
        location = loc_el.get_text(strip=True)   if loc_el   else ""
        href     = urljoin(BASE_URL, a["href"])

        jobs.append({"title": title, "dept": dept, "location": location, "url": href})

    return jobs


async def main():
    SEP = "=" * 60

    print(f"\n{SEP}")
    print("  Razorpay — Playwright smoke test")
    print(SEP)

    # ── Step 1: Listing page ─────────────────────────────────────────
    jobs = await scrape_listing()

    print(f"\nJob listings found : {len(jobs)}")

    if not jobs:
        print("\nNo jobs extracted — the page structure may have changed.")
        print("Check WAIT_SELECTOR and re-run.")
        return

    print("\nFirst 3 job titles:")
    for i, job in enumerate(jobs[:3]):
        dept_str = f"  [{job['dept']}]" if job["dept"] else ""
        print(f"  {i+1}. {job['title']}{dept_str}")

    first = jobs[0]
    print(f"\nFirst job detail URL:")
    print(f"  {first['url']}")

    # ── Step 2: Full JD fetch ────────────────────────────────────────
    print(f"\n{SEP}")
    print("  Fetching full JD via fetch_jd_javascript() ...")
    print(SEP)
    print(f"  URL: {first['url']}\n")

    jd_text = await _fetch_jd_js_async(first["url"])

    print(f"JD length : {len(jd_text)} chars")
    print(f"\nFirst 300 characters of JD:")
    print(f"  {jd_text[:300]}")

    print(f"\n{SEP}")
    if len(jd_text) >= 200:
        print("  Result: JD fetch PASSED (>= 200 chars)")
    else:
        print(f"  Result: JD too short ({len(jd_text)} chars) — would be marked jd_missing")
    print(SEP)


if __name__ == "__main__":
    asyncio.run(main())
