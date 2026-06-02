import asyncio
import random
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config.settings import load_companies
from utils.sheets_client import SheetsClient


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Company-specific CSS selectors Playwright waits for before extracting HTML.
# Falls back to networkidle if the selector isn't found within 30 s.
_WAIT_SELECTORS: dict[str, str] = {
    "Google": "[data-ph-at-id='jobs-list-item'], li[class*='lLd3Je']",
    "Uber": "[data-testid='job-card'], li[class*='job']",
    "Adobe": "[data-ph-at-id='jobs-list-item'], .jobs-list-item",
    "Amazon": "div.job-tile, table.job-listing",
    "Swiggy": ".career-section, [class*='job']",
    "Flipkart": "[class*='JobCard'], [class*='job-card']",
    "Zepto": ".job-list-item, [class*='career']",
    "PhonePe": ".job-item, [class*='opening']",
    "UrbanCompany": ".job-item, [class*='job']",
    "Meesho": "[class*='JobCard'], [class*='job']",
    "Razorpay": "a[href*='/jobs/jobs-all/detail/']",
    "Microsoft": "li.ms-List-cell, [data-automationid='ListCell']",
    "Atlassian": "article, [data-testid='job-card']",
}

_JOB_URL_RE = re.compile(
    r"/(job|jobs|opening|openings|position|positions|apply|role|roles|requisition)/",
    re.IGNORECASE,
)

_PRIMARY_KEYWORDS = [
    "product manager",
    "product management",
    "group product manager",
    "principal product manager",
    "associate product manager",
    "senior product manager",
    "lead product manager",
    "director of product",
    "director - product",
    "vp product",
    "head of product",
    "chief product officer",
    "gpm",
    "apm",
    "spm",
]

_EXCLUSION_KEYWORDS = [
    "software engineer",
    "software developer",
    "data engineer",
    "data scientist",
    "machine learning",
    "devops",
    "backend",
    "frontend",
    "full stack",
    "android",
    "ios developer",
    "qa engineer",
    "test engineer",
    "programme manager",
    "program manager",
    "project manager",
    "scrum master",
    "business analyst",
    "data analyst",
    "marketing manager",
    "sales manager",
    "account manager",
    "hr manager",
    "finance manager",
    "operations manager",
]


# ---------------------------------------------------------------------------
# HTML extractors
# ---------------------------------------------------------------------------

def _extract_lever(soup: BeautifulSoup, company: str) -> list[dict]:
    # Lever structure: <div.posting> > <a.posting-title href="..."> > <h5> + <div.posting-categories>
    jobs = []
    for posting in soup.select("div.posting"):
        link = posting.select_one("a.posting-title")
        if not link:
            continue
        title_el = link.select_one("h5")
        loc_el = posting.select_one(".sort-by-location")
        jobs.append({
            "company": company,
            "role": title_el.get_text(strip=True) if title_el else link.get_text(strip=True),
            "url": link.get("href", ""),
            "location": loc_el.get_text(strip=True) if loc_el else "",
            "posted_date": "",
        })
    return jobs


def _extract_greenhouse(soup: BeautifulSoup, company: str, base_url: str) -> list[dict]:
    # Greenhouse structure (board embeds): <tr.job-post> > <td> > <a href="..."> > <p>(title) + <p>(location)
    # Legacy structure: <div.opening> > <a>(title) + <span.location>
    jobs = []
    for opening in soup.select("div.opening, tr.job-post"):
        link = opening.select_one("a")
        if not link:
            continue
        href = link.get("href", "")
        if href and not href.startswith("http"):
            href = urljoin(base_url, href)

        paras = link.find_all("p")
        if len(paras) >= 2:
            # Board embed format: first <p> = title, second <p> = location
            role = paras[0].get_text(strip=True)
            location = paras[1].get_text(strip=True)
        else:
            # Legacy format: link text = title, sibling span = location
            role = link.get_text(strip=True)
            loc_el = opening.select_one(".location, .job-post-location")
            location = loc_el.get_text(strip=True) if loc_el else ""

        jobs.append({
            "company": company,
            "role": role,
            "url": href,
            "location": location,
            "posted_date": "",
        })
    return jobs


def _extract_generic(soup: BeautifulSoup, company: str, base_url: str) -> list[dict]:
    """Heuristic extractor: collects anchor tags whose URLs look like individual job postings."""
    seen: set[str] = set()
    jobs = []

    for a in soup.find_all("a", href=True):
        href: str = a["href"].strip()
        text: str = a.get_text(strip=True)

        if not text or not (5 <= len(text) <= 150):
            continue

        full_url = href if href.startswith("http") else urljoin(base_url, href)

        if not _JOB_URL_RE.search(full_url) or full_url == base_url:
            continue

        if full_url in seen:
            continue
        seen.add(full_url)

        # Try to find location text in the nearest container element
        parent = a.find_parent(["li", "div", "article", "tr"])
        location = ""
        if parent:
            loc_el = parent.find(class_=re.compile(r"location|city|place", re.I))
            if loc_el:
                location = loc_el.get_text(strip=True)
            else:
                loc_str = parent.find(
                    string=re.compile(r"Bengaluru|Bangalore|India|Remote|Hybrid", re.I)
                )
                if loc_str:
                    location = str(loc_str).strip()

        jobs.append({
            "company": company,
            "role": text,
            "url": full_url,
            "location": location,
            "posted_date": "",
        })

    return jobs


# ---------------------------------------------------------------------------
# JD fetching helpers (module-level so test scripts can import them directly)
# ---------------------------------------------------------------------------

# Containers tried in priority order; first with >= 200 chars of text wins.
_JD_SELECTORS = [
    (lambda s: s.find(class_=re.compile(r"description", re.I))),
    (lambda s: s.find(class_=re.compile(r"content", re.I))),
    (lambda s: s.find(class_=re.compile(r"job-detail", re.I))),
    (lambda s: s.find(class_=re.compile(r"posting", re.I))),
    (lambda s: s.find("article")),
    (lambda s: s.find("main")),
]

_MIN_JD_CHARS = 200


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_jd_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for finder in _JD_SELECTORS:
        el = finder(soup)
        if el:
            text = _clean_text(el.get_text(separator=" ", strip=True))
            if len(text) >= _MIN_JD_CHARS:
                return text
    # Fallback: concatenate all <p> tags
    paras = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    return _clean_text(" ".join(paras))


def _fetch_jd_static(job_url: str) -> str:
    resp = requests.get(job_url, headers=_BROWSER_HEADERS, timeout=30)
    resp.raise_for_status()
    return _extract_jd_from_html(resp.text)


async def _fetch_jd_js_async(job_url: str) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=_BROWSER_HEADERS["User-Agent"],
                locale="en-US",
            )
            page = await context.new_page()
            await page.goto(job_url, timeout=30_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3_000)
            text = await page.evaluate("() => document.body.innerText")
            return _clean_text(text)
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Date parsing helper
# ---------------------------------------------------------------------------

def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse a job posting date string into a timezone-aware datetime.

    Handles ISO dates, common locale formats, and relative strings like
    '2 days ago'. Returns None if the string cannot be understood.
    """
    if not date_str:
        return None

    s = date_str.strip()
    now = datetime.now(timezone.utc)
    low = s.lower()

    # Relative expressions
    if low in ("just now", "today", "moments ago"):
        return now
    m = re.match(r"(\d+)\s*(minute|hour|day|week)s?\s+ago", low)
    if m:
        n = int(m.group(1))
        delta = {
            "minute": timedelta(minutes=n),
            "hour": timedelta(hours=n),
            "day": timedelta(days=n),
            "week": timedelta(weeks=n),
        }[m.group(2)]
        return now - delta

    # Explicit formats (most-specific first)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%m/%d/%Y",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    return None


def _parse_html(company: dict, html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    url = company["careers_url"]
    name = company["name"]

    if "lever.co" in url:
        return _extract_lever(soup, name)
    if "greenhouse.io" in url:
        return _extract_greenhouse(soup, name, url)
    return _extract_generic(soup, name, url)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class ScrapingAgent:
    _MAX_JOBS_PER_COMPANY = 50
    _MAX_PAGES  = 5
    _MAX_SCROLLS = 10

    def __init__(self):
        from config.settings import load_companies as _lc_all
        import json as _json
        import pathlib as _pl

        self.companies = load_companies()  # enabled only
        self._company_map = {c["name"]: c for c in self.companies}
        self.sheets = SheetsClient()

        # Log Phase-2 disabled companies so operators know what's skipped
        _all_raw = _json.loads(
            (_pl.Path(__file__).resolve().parent.parent / "config" / "companies.json")
            .read_text()
        )["companies"]
        _disabled = [
            c for c in _all_raw
            if not c.get("enabled") and c.get("disabled_reason")
        ]
        for c in _disabled:
            logger.info(f"Skipping {c['name']}: {c['disabled_reason']}")

        self._phase2_names = [c["name"] for c in _disabled]
        logger.info(f"ScrapingAgent initialised with {len(self.companies)} active companies "
                    f"({len(_disabled)} disabled for Phase 2)")

    # ------------------------------------------------------------------ #
    # Scrapers
    # ------------------------------------------------------------------ #

    def scrape_static(self, company: dict) -> list[dict]:
        resp = requests.get(company["careers_url"], headers=_BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
        return _parse_html(company, resp.text)

    def scrape_javascript(self, company: dict, override_url: str = None) -> list[dict]:
        # URL pagination is handled at the sync level to avoid nested asyncio.run()
        if company.get("pagination_type") == "url" and override_url is None:
            return self._paginate_url(company)
        return asyncio.run(self._scrape_js_async(company, override_url))

    def _paginate_url(self, company: dict) -> list[dict]:
        base_url  = company["careers_url"]
        param     = company.get("pagination_url_param", "page")
        increment = company.get("pagination_increment", 1)
        max_jobs  = company.get("max_jobs", self._MAX_JOBS_PER_COMPANY)
        name      = company["name"]

        all_jobs: list[dict] = []
        seen_urls: set[str]  = set()
        page_num = 1

        while page_num <= self._MAX_PAGES:
            sep = "&" if "?" in base_url else "?"
            url = f"{base_url}{sep}{param}={page_num}"
            logger.info(f"  {name}: scraping page {page_num} — {url}")

            try:
                jobs = asyncio.run(self._scrape_js_async(company, url))
            except Exception as exc:
                logger.error(f"  {name}: page {page_num} failed — {exc}")
                break

            if not jobs:
                logger.info(f"  {name}: No jobs on page {page_num}, stopping")
                break

            new_jobs = [j for j in jobs if j.get("url") and j["url"] not in seen_urls]
            if not new_jobs:
                logger.info(f"  {name}: All jobs on page {page_num} already seen, stopping pagination")
                break

            all_jobs.extend(new_jobs)
            seen_urls.update(j["url"] for j in new_jobs)
            logger.info(f"  {name}: Page {page_num}: {len(new_jobs)} new jobs (total: {len(all_jobs)})")

            if len(all_jobs) >= max_jobs:
                logger.info(f"  {name}: Reached max_jobs limit ({max_jobs})")
                break

            page_num += increment
            time.sleep(2)

        return all_jobs

    async def _scrape_js_async(self, company: dict, override_url: str = None) -> list[dict]:
        from playwright.async_api import async_playwright
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        name            = company["name"]
        url             = override_url or company["careers_url"]
        wait_sel        = _WAIT_SELECTORS.get(name, "body")
        pagination_type = company.get("pagination_type", "none")
        wait_time       = company.get("wait_time", 0)
        filter_clicks   = company.get("filter_clicks", [])

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent=_BROWSER_HEADERS["User-Agent"],
                    locale="en-US",
                )
                page = await context.new_page()
                await page.goto(url, timeout=30_000, wait_until="domcontentloaded")

                try:
                    await page.wait_for_selector(wait_sel, timeout=30_000)
                except PlaywrightTimeout:
                    logger.warning(f"{name}: selector '{wait_sel}' timed out — falling back to networkidle")
                    try:
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightTimeout:
                        logger.warning(f"{name}: networkidle also timed out — extracting whatever loaded")

                if wait_time:
                    await page.wait_for_timeout(wait_time)

                # Meesho: lazy-load — scroll slowly before attempting extraction
                if name == "Meesho" or company.get("scroll_before_extract"):
                    steps    = company.get("scroll_steps", 3)
                    step_px  = company.get("scroll_step_px", 500)
                    for i in range(steps):
                        await page.evaluate(f"window.scrollTo(0, {(i + 1) * step_px})")
                        await page.wait_for_timeout(1500)
                    logger.info(f"  {name}: lazy-load scroll complete ({steps} steps)")

                # Google: scroll and retry if jobs not visible after initial load
                if name == "Google" and company.get("scroll_and_retry"):
                    title_sel = company.get("job_selectors", {}).get("title", "h3.QJPWVe")
                    if not await page.query_selector(title_sel):
                        logger.info(f"  Google: jobs not visible, scrolling and retrying...")
                        await page.evaluate("window.scrollBy(0, 500)")
                        await page.wait_for_timeout(3000)
                        if not await page.query_selector(title_sel):
                            logger.warning("  Google: no jobs found — possible bot detection")

                # Apply filter clicks before extraction
                if filter_clicks:
                    await self._apply_filter_clicks_async(page, company)
                    await page.wait_for_timeout(2000)

                # Choose extraction strategy
                if pagination_type == "scroll":
                    return await self._paginate_scroll_async(page, company)
                else:
                    # "none", "url" (single-page pass), or missing
                    return await self._extract_jobs_with_selectors_async(page, company)

            finally:
                await browser.close()

    async def _apply_filter_clicks_async(self, page, company: dict) -> bool:
        name          = company["name"]
        filter_clicks = company.get("filter_clicks", [])
        all_ok        = True

        for fc in filter_clicks:
            fc_type    = fc.get("type", "button")
            selector   = fc.get("selector", "")
            text       = fc.get("text", "")
            wait_after = fc.get("wait_after", 1000)

            try:
                if fc_type == "sidebar":
                    # Sidebar items may be hidden until scrolled into view;
                    # scroll the element into view then force-click.
                    await page.evaluate(f"""
                        const els = [...document.querySelectorAll('{selector.split(":has")[0]}')];
                        const target = els.find(e => e.textContent.trim() === '{text}');
                        if (target) target.scrollIntoView();
                    """)
                    await page.wait_for_timeout(1000)

                    # Build a Playwright text-selector if the selector already has :has-text
                    click_sel = selector if ":has-text" in selector else f"{selector}:has-text('{text}')"
                    try:
                        await page.click(click_sel, force=True, timeout=5000)
                        await page.wait_for_timeout(wait_after)
                        logger.info(f"  {name}: sidebar filter clicked via force click: {text!r}")
                    except Exception as fe:
                        logger.warning(f"  {name}: Force click failed for '{text}' — {fe}")
                        # Fallback: dispatch a synthetic MouseEvent directly via JS
                        try:
                            await page.evaluate("""
                                () => {
                                    const items = document.querySelectorAll('li.sidebar-item');
                                    const target = Array.from(items).find(
                                        el => el.textContent.trim() === 'Product Management');
                                    if (target) {
                                        target.dispatchEvent(new MouseEvent('click', {
                                            bubbles: true,
                                            cancelable: true,
                                            view: window
                                        }));
                                    }
                                }
                            """)
                            await page.wait_for_timeout(3000)
                            logger.info(f"  {name}: filter applied via JS injection")
                        except Exception as je:
                            logger.warning(
                                f"  {name}: JS injection also failed: {je} — "
                                f"keyword filter will handle PM narrowing"
                            )
                            all_ok = False
                    continue

                # Non-sidebar types: find by text match and click
                elements = await page.query_selector_all(selector)
                target   = None
                for el in elements:
                    el_text = (await el.inner_text()).strip()
                    if text.lower() in el_text.lower():
                        target = el
                        break

                if target is None:
                    logger.warning(f"  {name}: Filter click failed for '{text}' — no matching element found")
                    all_ok = False
                    continue

                await target.click()
                await page.wait_for_timeout(wait_after)

                label = {"dropdown": "dropdown", "option": "option"}.get(fc_type, "element")
                logger.info(f"  {name}: Clicked {label}: {text!r}")

            except Exception as exc:
                logger.warning(f"  {name}: Filter click failed for '{text}' — {exc}")
                all_ok = False

        return all_ok

    async def _extract_jobs_with_selectors_async(self, page, company: dict) -> list[dict]:
        selectors = company.get("job_selectors", {})
        title_sel = selectors.get("title")
        url_sel   = selectors.get("url")
        base_url  = company.get("base_url", "")
        name      = company["name"]

        if not title_sel:
            # No selectors configured: fall back to BeautifulSoup HTML parsing
            html = await page.content()
            return _parse_html(company, html)

        # Try multiple selector candidates until one returns results
        _fallback_title_sels = [title_sel, "div[class*='col-span']", "a[href*='job'] div"]
        active_sel = title_sel
        jobs: list[dict] = []
        try:
            title_elements = []
            for candidate in _fallback_title_sels:
                title_elements = await page.query_selector_all(candidate)
                if title_elements:
                    active_sel = candidate
                    if candidate != title_sel:
                        logger.info(f"  {name}: primary selector empty, using fallback {candidate!r}")
                    else:
                        logger.info(f"  {name}: using selector {candidate!r} — {len(title_elements)} elements found")
                    break

            if not title_elements:
                logger.warning(f"  {name}: all selectors returned 0 elements — falling back to HTML parse")
                html = await page.content()
                return _parse_html(company, html)
            for el in title_elements:
                title_text = (await el.inner_text()).strip()
                if not title_text:
                    continue

                href = ""
                tag  = await el.evaluate("el => el.tagName.toLowerCase()")
                if tag == "a":
                    href = await el.get_attribute("href") or ""
                elif url_sel:
                    url_el = await el.query_selector(url_sel)
                    if url_el:
                        href = await url_el.get_attribute("href") or ""

                if not href:
                    href = await el.evaluate(
                        "el => el.closest('a') ? el.closest('a').getAttribute('href') : ''"
                    ) or ""

                if href.startswith("/"):
                    href = base_url + href

                if href:
                    jobs.append({
                        "company":     name,
                        "role":        title_text,
                        "url":         href,
                        "location":    "",
                        "posted_date": "",
                    })

        except Exception as exc:
            logger.warning(f"  {name}: selector extraction failed ({exc}) — falling back to HTML parse")
            html = await page.content()
            return _parse_html(company, html)

        return jobs

    async def _paginate_scroll_async(self, page, company: dict) -> list[dict]:
        max_jobs  = company.get("max_jobs", self._MAX_JOBS_PER_COMPANY)
        name      = company["name"]
        all_jobs: list[dict] = []
        seen_urls: set[str]  = set()
        scroll_count = 0

        while scroll_count < self._MAX_SCROLLS:
            current_jobs = await self._extract_jobs_with_selectors_async(page, company)
            new_jobs     = [j for j in current_jobs if j.get("url") and j["url"] not in seen_urls]
            all_jobs.extend(new_jobs)
            seen_urls.update(j["url"] for j in new_jobs)

            if len(all_jobs) >= max_jobs:
                logger.info(f"  {name}: Reached max_jobs ({max_jobs}), stopping scroll")
                break

            if not new_jobs and scroll_count > 0:
                logger.info(f"  {name}: No new jobs after scroll, end of results")
                break

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2500)
            scroll_count += 1
            logger.info(f"  {name}: Scroll {scroll_count}: {len(all_jobs)} total jobs so far")

        return all_jobs

    # ------------------------------------------------------------------ #
    # Phase 2: Google + LinkedIn via Apify
    # Currently disabled - see companies.json
    # ------------------------------------------------------------------ #

    def _scrape_via_apify(self, company: dict) -> list[dict]:
        """Placeholder for Phase 2 Apify integration.

        Google and LinkedIn both require Apify actors to bypass bot detection
        and handle authentication respectively. This stub will be wired up in
        Phase 2 alongside the Apify API client setup.
        """
        name = company.get("name", "?")
        logger.warning(
            f"  {name}: Apify scraper not yet implemented (Phase 2). "
            f"Mark company enabled=true once Apify actor is configured."
        )
        return []

    # ------------------------------------------------------------------ #
    # Google careers JSON API scraper (avoids Playwright bot detection)
    # ------------------------------------------------------------------ #

    def _scrape_google_api(self, company: dict) -> list[dict]:
        _GOOGLE_HEADERS = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://careers.google.com/",
            "Accept":  "application/json",
        }
        all_jobs: list[dict] = []
        page = 1

        # Try multiple known endpoint variants — Google changes these periodically.
        _ENDPOINTS = [
            "https://careers.google.com/api/jobs/jobs-site/search/",
            "https://www.google.com/about/careers/applications/jobs/results/",
        ]

        while page <= self._MAX_PAGES:
            raw_url = _ENDPOINTS[0]
            url = f"{raw_url}?q=Product+Manager&location=Bangalore%2C+India&page={page}"
            try:
                resp = requests.get(url, headers=_GOOGLE_HEADERS, timeout=15)
                if resp.status_code == 404 and page == 1:
                    logger.warning(
                        f"  Google API: endpoint returned 404 — "
                        f"the careers API path may have changed. "
                        f"Inspect network requests on careers.google.com to find the current endpoint."
                    )
                    break
                if resp.status_code != 200:
                    logger.info(f"  Google API: HTTP {resp.status_code}, stopping")
                    break

                data = resp.json()
                jobs_data = (
                    data.get("jobs")
                    or data.get("results")
                    or data.get("data")
                    or []
                )

                if not jobs_data:
                    logger.info(f"  Google API: no more jobs at page {page}")
                    break

                # Log the raw keys of the first job for field-name verification
                if page == 1 and jobs_data:
                    logger.info(f"  Google API: first job keys = {list(jobs_data[0].keys())}")

                for job in jobs_data:
                    title = (
                        job.get("title")
                        or job.get("job_title")
                        or job.get("name")
                        or ""
                    )
                    job_id = job.get("job_id") or job.get("id") or ""
                    job_url = (
                        f"https://careers.google.com/jobs/results/{job_id}"
                        if job_id
                        else (job.get("url") or job.get("apply_url") or "")
                    )
                    location = (
                        job.get("location")
                        or (job.get("locations") or [""])[0]
                        or ""
                    )
                    if title:
                        all_jobs.append({
                            "company":     "Google",
                            "role":        title,
                            "url":         job_url,
                            "location":    location,
                            "posted_date": "",
                        })

                logger.info(f"  Google API page {page}: {len(jobs_data)} jobs found")
                page += 1
                time.sleep(1)

            except Exception as exc:
                logger.error(f"  Google API error: {exc}")
                break

        logger.info(f"  Google API total: {len(all_jobs)} jobs found")
        return all_jobs

    # ------------------------------------------------------------------ #
    # Filtering
    # ------------------------------------------------------------------ #

    _LOCATION_TERMS = (
        "bangalore", "bengaluru", "blr", "karnataka",
        "remote", "work from home", "wfh", "hybrid", "india",
    )

    def is_relevant_job(self, job_title: str, company: dict) -> bool:
        title_lower = job_title.lower()

        if any(kw in title_lower for kw in _EXCLUSION_KEYWORDS):
            logger.debug(f"Excluded: {job_title} (contains exclusion keyword)")
            return False

        if not any(kw in title_lower for kw in _PRIMARY_KEYWORDS):
            logger.debug(f"Excluded: {job_title} (no primary PM keyword found)")
            return False

        return True

    def is_relevant_location(self, location: str) -> bool:
        if not location or not location.strip():
            return True  # missing location: keep (logged by caller)
        loc_lower = location.lower()
        return any(term in loc_lower for term in self._LOCATION_TERMS)

    def filter_by_date(self, jobs: list[dict], backfill: bool = False) -> list[dict]:
        if backfill:
            return jobs

        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        kept = []
        for job in jobs:
            role = job.get("role", "?")
            posted = job.get("posted_date", "")
            dt = _parse_date(posted)
            if dt is None:
                logger.info(f"Could not parse date for {role}, keeping")
                kept.append(job)
            elif dt >= cutoff:
                kept.append(job)
            else:
                logger.debug(f"Filtered out (too old): {role}")
        return kept

    # ------------------------------------------------------------------ #
    # JD fetching
    # ------------------------------------------------------------------ #

    def fetch_jd_static(self, job_url: str) -> str:
        return _fetch_jd_static(job_url)

    def fetch_jd_javascript(self, job_url: str) -> str:
        return asyncio.run(_fetch_jd_js_async(job_url))

    def fetch_jd(self, job: dict) -> str:
        url = job.get("url", "")
        if not url:
            return ""
        company_name = job.get("company", "")
        ctype = self._company_map.get(company_name, {}).get("type", "static")
        try:
            jd = self.fetch_jd_javascript(url) if ctype == "javascript" else self.fetch_jd_static(url)
        except Exception as exc:
            logger.warning(f"fetch_jd failed for {url}: {exc}")
            jd = ""
        time.sleep(1)
        return jd

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #

    def scrape_all(self) -> list[dict]:
        all_jobs: list[dict] = []
        total_scraped    = 0
        role_skipped     = 0
        location_skipped = 0
        url_filtered_log = set()  # avoid repeating the bypass log per-job

        for company in self.companies:
            name         = company["name"]
            ctype        = company.get("type", "static")
            url_filtered = company.get("url_filtered", False)

            if url_filtered and name not in url_filtered_log:
                logger.info(f"Skipping location filter for {name} - URL already filtered")
                url_filtered_log.add(name)

            n_filters   = len(company.get("filter_clicks", []))
            pagination  = company.get("pagination_type", "none")
            max_jobs    = company.get("max_jobs", self._MAX_JOBS_PER_COMPANY)
            logger.info(
                f"Scraping {name}: type={ctype}, "
                f"filters={n_filters} clicks, "
                f"pagination={pagination}, "
                f"max={max_jobs} jobs"
            )
            try:
                if name == "Google" or company.get("scraper_type") == "api":
                    raw_jobs = self._scrape_google_api(company)
                elif ctype == "javascript":
                    raw_jobs = self.scrape_javascript(company)
                else:
                    raw_jobs = self.scrape_static(company)
                total_scraped += len(raw_jobs)

                kept = []
                for job in raw_jobs:
                    role     = job.get("role", "")
                    location = job.get("location", "")

                    # Role filter (cheapest check first)
                    if not self.is_relevant_job(role, company):
                        logger.debug(f"  Skipped: {role} (not a PM role)")
                        role_skipped += 1
                        continue

                    # Location filter — bypass when URL already scopes to Bangalore
                    if not url_filtered:
                        if not location.strip():
                            logger.warning(f"  No location for '{role}' at {name}, keeping")
                        elif not self.is_relevant_location(location):
                            logger.info(
                                f"  Skipped: {role} at {name} - "
                                f"location: {location} (not Bangalore/Remote)"
                            )
                            location_skipped += 1
                            continue

                    kept.append(job)

                logger.info(
                    f"  {name}: {len(raw_jobs)} found, {len(kept)} kept "
                    f"({len(raw_jobs) - len(kept)} filtered)"
                )
                all_jobs.extend(kept)
            except Exception as exc:
                logger.error(f"  {name}: scrape failed — {exc}")

            delay = random.uniform(2, 4)
            logger.debug(f"  Waiting {delay:.1f}s before next company ...")
            time.sleep(delay)

        phase2_str = ", ".join(self._phase2_names) if self._phase2_names else "none"
        logger.info(
            f"Scraping complete:\n"
            f"  Active companies:   {len(self.companies)}\n"
            f"  Disabled (Phase 2): {phase2_str}\n"
            f"  Jobs scraped:       {total_scraped}\n"
            f"  PM roles kept:      {total_scraped - role_skipped}\n"
            f"  Location filtered:  {location_skipped}\n"
            f"  Saved to sheet:     (see save_jobs)"
        )
        return all_jobs

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def is_duplicate(self, job_url: str) -> bool:
        return self.sheets.row_exists(job_url)

    def save_jobs(self, jobs: list[dict]) -> int:
        existing_urls = {row.get("url") for row in self.sheets.get_all_rows()}
        new_jobs = [j for j in jobs if j.get("url") and j["url"] not in existing_urls]

        for job in new_jobs:
            role = job.get("role", "")
            company = job.get("company", "")
            logger.info(f"Fetching JD for: {role} at {company}")
            jd_raw = self.fetch_jd(job)
            jd_len = len(jd_raw)
            if jd_len < 200:
                status = "jd_missing"
                logger.warning(
                    f"JD too short for {role} at {company} "
                    f"({jd_len} chars) - marking as jd_missing"
                )
            else:
                status = "new"
            self.sheets.append_row({
                "job_id": str(uuid.uuid4()),
                "company": company,
                "role": role,
                "url": job.get("url", ""),
                "jd_raw": jd_raw,
                "posted_date": job.get("posted_date", ""),
                "location": job.get("location", ""),
                "status": status,
            })

        skipped = len(jobs) - len(new_jobs)
        logger.info(f"save_jobs: {len(new_jobs)} new saved, {skipped} duplicates skipped")
        return len(new_jobs)

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #

    def run(self, backfill: bool = False) -> int:
        logger.info(f"ScrapingAgent.run() started (backfill={backfill})")
        jobs = self.scrape_all()
        jobs = self.filter_by_date(jobs, backfill=backfill)
        logger.info(f"{len(jobs)} jobs remain after date filter")

        if backfill:
            # Backfill ignores duplicate checking — saves every scraped listing.
            for job in jobs:
                role = job.get("role", "")
                company = job.get("company", "")
                logger.info(f"Fetching JD for: {role} at {company}")
                jd_raw = self.fetch_jd(job)
                jd_len = len(jd_raw)
                if jd_len < 200:
                    status = "jd_missing"
                    logger.warning(
                        f"JD too short for {role} at {company} "
                        f"({jd_len} chars) - marking as jd_missing"
                    )
                else:
                    status = "new"
                self.sheets.append_row({
                    "job_id": str(uuid.uuid4()),
                    "company": company,
                    "role": role,
                    "url": job.get("url", ""),
                    "jd_raw": jd_raw,
                    "posted_date": job.get("posted_date", ""),
                    "location": job.get("location", ""),
                    "status": status,
                })
            saved = len(jobs)
        else:
            saved = self.save_jobs(jobs)

        logger.info(f"ScrapingAgent.run() done — {saved} new jobs saved to sheet")
        return saved
