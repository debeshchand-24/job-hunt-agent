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
    def __init__(self):
        self.companies = load_companies()
        self._company_map = {c["name"]: c for c in self.companies}
        self.sheets = SheetsClient()
        logger.info(f"ScrapingAgent initialised with {len(self.companies)} enabled companies")

    # ------------------------------------------------------------------ #
    # Scrapers
    # ------------------------------------------------------------------ #

    def scrape_static(self, company: dict) -> list[dict]:
        resp = requests.get(company["careers_url"], headers=_BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
        return _parse_html(company, resp.text)

    def scrape_javascript(self, company: dict) -> list[dict]:
        return asyncio.run(self._scrape_js_async(company))

    async def _scrape_js_async(self, company: dict) -> list[dict]:
        from playwright.async_api import async_playwright
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        name = company["name"]
        url = company["careers_url"]
        wait_sel = _WAIT_SELECTORS.get(name, "body")

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

                html = await page.content()
                return _parse_html(company, html)
            finally:
                await browser.close()

    # ------------------------------------------------------------------ #
    # Filtering
    # ------------------------------------------------------------------ #

    _LOCATION_TERMS = (
        "bangalore", "bengaluru", "blr", "karnataka",
        "remote", "work from home", "wfh", "hybrid", "india",
    )

    def is_relevant_job(self, job_title: str, company: dict) -> bool:
        keywords = company.get("filter_keywords", [])
        title_lower = job_title.lower()
        return any(kw.lower() in title_lower for kw in keywords)

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

            if ctype == "linkedin":
                logger.info(f"Skipping {name} (type=linkedin)")
                continue

            if url_filtered and name not in url_filtered_log:
                logger.info(f"Skipping location filter for {name} - URL already filtered")
                url_filtered_log.add(name)

            logger.info(f"Scraping {name} ({ctype}) ...")
            try:
                raw_jobs = (
                    self.scrape_javascript(company)
                    if ctype == "javascript"
                    else self.scrape_static(company)
                )
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

        location_kept = len(all_jobs)
        logger.info(
            f"Scraping complete: {total_scraped} scraped, "
            f"{total_scraped - role_skipped} PM roles, "
            f"{location_kept} in Bangalore/Remote, "
            f"{role_skipped} skipped (role), "
            f"{location_skipped} skipped (location)"
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
